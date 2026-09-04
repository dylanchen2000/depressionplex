"""DP-013：LOVO-CV（留一视频交叉验证）框架。合成真值跑通，暂不接人工数据。

流程（SPEC_人工比对与验收_v2 §5，派工单 v2 §2）：

    for 每个视频 v:
        在其余视频的试次上拟合 θ_mob（唯一自由参数，
            目标 = 最小化 mean |软件 immobility − 人工(或合成真值) immobility|)
        在 v 的试次上做**样本外**预测

    汇总全部样本外预测 → Pearson r / Bland-Altman
    每折拟合的 θ_mob 本身 ⇒ 跨折变异系数 = **G2**

纪律：

- **只拟合 θ_mob 一个标量**。bout 参数保持 FROZEN（bouts.CSI_TST_STARTING_POINT），
  框架没有任何路径可以改它。
- **禁止用自己的输出当真值**（θ_mob 冻结护栏）。`TrialSample.truth_immobility_s`
  只许来自人工评分或合成真值生成器；拿软件输出填 truth 是循环论证，本框架
  在字段命名上把这条路堵死（truth_* 与 software_* 分家）。
- **DP-037 目标函数变体**（`objective="total_immobility" | "onset_match"`，默认前者，
  不许偷改——已跑出的数字口径不许断裂）。依据 `docs/分析_评分员分歧定位_2026-09-03.md`
  （DP-036）：人工对**运动起始**时刻可靠（中位差 0.06 s），对**结束**时刻系统性分歧
  （0.47 s，73% 单向）⇒ "immobility 总量差"目标里混进了人的松手预测方差。
  onset_match 用互为最佳重叠配对（`mutual_best_edges`，一对多会把长段偏移重复计入、
  实测虚高 834%，配对逻辑不许自己重写）只量起始误差。
  **由此产生的纪律：`bouts.py` 的进入判据可用人工数据校验，退出判据不可以**——
  退出侧参数只许用合成数据与物理量（RAD 残差分布）定，拿人工 immobility 总量
  去拟合退出侧就是把松手习惯写进机器。
- 拟合结果**不回写** rules.TstRulesParams 的 FROZEN 值——每折 θ 是估计量，
  出货默认值换不换由道俊拍板。
- 软件 immobility 的口径与人工镜像对齐：人工 mobile = union(在按键段)，
  immobility = window − mobile；软件侧同样 mobile = bout 流水线后的 Mobility
  时长（raw 变体同报，DP-014 要求两侧同时施加 bout 时不迷路）。
- 分母随行携带（G9）：total_frames / scoreable_frames / unknown_fraction。
- **DP-035（G11 逐秒 Jaccard 门）**：总时长一致 ≠ 判断一致（实测有试次总差 0.9 s
  而逐秒 Jaccard 仅 0.51）。软件-人工的 Jaccard 与人工-人工基线（0.738，13 试次
  实测，DP-035）**同式同口径**（复用 `scorer_disagreement` 的交集实现，不许两套账），
  与 G7/G8 捆在同一份报告里出，**任一不过即不过**；逐试次值全部列出（升序 =
  拖累项排前）。算不出 ⇒ 判不过并写明原因，不许"不报=过"。
- 阈值单位：θ 是 BL² 归一化残差（无量纲物理量），搜索网格边界只是搜索范围
  声明，不是判定阈值。时间一律秒。
- assay_core 纪律沿用：只依赖 numpy。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Sequence

import numpy as np

from .assay_core import bouts, rules
from .cli.scorer_disagreement import intersect_total, mutual_best_edges, total as segs_total

#: 唯一允许被拟合的参数名。写死在这里，报告里也好对账。
FITTED_PARAM = "theta_mob"
#: 目标函数（DP-037）。默认值有历史包袱——已跑出的数字全按 total_immobility 口径，
#: 换默认 = 口径断裂，所以只许显式传入，不许改默认。
OBJECTIVE_TOTAL = "total_immobility"
OBJECTIVE_ONSET = "onset_match"
OBJECTIVES = (OBJECTIVE_TOTAL, OBJECTIVE_ONSET)
#: 搜索网格 = θ 的物理合理范围声明（合成标定区间 0.015–0.02 的左右各放大一倍）。
#: 它不是判定阈值，FROZEN 出货值 0.0175 在网格内。
THETA_SEARCH_MIN = 0.005
THETA_SEARCH_MAX = 0.05

# ---------------------------------------------------------- 验收门阈值（G7/G8/G11）
# 三个门槛的来源全部是**人工侧**实测或 SPEC，与软件输出无关——
# "禁止用自己的输出调阈值"在这里是构造性成立，不是口头承诺。
G7_MIN_R = 0.818          # G7：样本外 Pearson r 下限（SPEC §9，人工-人工一致性推导）
G8_MAX_BIAS_S = 28.6      # G8：|Bland-Altman 偏差| 上限 = 两位人工评分员的实测最大偏差（DP-036）
G11_MIN_JACCARD = 0.738   # G11：软件-人工逐秒 Jaccard 下限 = 人工-人工 A 组 n=13 平均
                          # （DP-035 唯一**预先登记**值；B 组 0.816/n=3 只作参考记账，
                          # 拿 n=3 抬门槛属小样本过拟合——门槛维持 0.738 不动）


# ---------------------------------------------------------------- 样本与结果模型


@dataclass(frozen=True)
class TrialSample:
    """一个试次：逐帧特征 + **外部真值**（人工或合成，禁止软件自填）。"""

    trial_id: str
    video: str
    features: rules.TstFeatures
    truth_mobile_s: float     # 真值口径的"在动"并集时长（人工 = union(holds)）
    window_s: float = 360.0
    truth_source: str = "synthetic"   # 'synthetic' | 'human'（G4 版本指纹用）
    #: onset_match 目标需要的真值段（秒，已 union+normalize，零长段须在上游剔除）。
    #: total_immobility 目标不要求；onset 目标缺它 ⇒ 报错，不静默退化成总量差。
    truth_mobile_segs: tuple[tuple[float, float], ...] | None = None

    @property
    def truth_immobility_s(self) -> float:
        return self.window_s - self.truth_mobile_s

    def __post_init__(self) -> None:
        if self.truth_source not in ("synthetic", "human"):
            raise ValueError(f"truth_source 只能是 synthetic/human，得到 {self.truth_source!r}")
        if not (0.0 <= self.truth_mobile_s <= self.window_s):
            raise ValueError(
                f"{self.trial_id}: truth_mobile_s={self.truth_mobile_s} 越出 [0,{self.window_s}]"
                "——拒收，不 clamp（禁止静默兜底）")
        if self.truth_mobile_segs is not None:
            prev_end = None
            for s, e in self.truth_mobile_segs:
                if e <= s:
                    raise ValueError(f"{self.trial_id}: 真值段 [{s},{e}] 零/负长——"
                                     "上游（normalize）就该剔掉，不留二义")
                if prev_end is not None and s <= prev_end:
                    raise ValueError(f"{self.trial_id}: 真值段未排序或有重叠（{s} ≤ {prev_end}）"
                                     "——须先 normalize（排序取并集），onset 匹配不接受乱序输入")
                if not (0.0 <= s and e <= self.window_s + 1e-9):
                    raise ValueError(f"{self.trial_id}: 真值段 [{s},{e}] 越出窗口，拒收不截断")
                prev_end = e
            # G11 与 onset 目标都直接吃段——段合计与 mobile 总量必须一本账。
            seg_sum = sum(e - s for s, e in self.truth_mobile_segs)
            if abs(seg_sum - self.truth_mobile_s) > 0.05:
                raise ValueError(
                    f"{self.trial_id}: 真值段合计 {seg_sum:.3f} s 与 truth_mobile_s "
                    f"{self.truth_mobile_s:.3f} s 差 >0.05 s——两套账不许同时入账"
                    "（上游 normalize/union 与总量必须同源）")


@dataclass(frozen=True)
class TrialEval:
    trial_id: str
    video: str
    held_out: bool
    theta: float
    software_mobility_raw_s: float
    software_mobility_pipeline_s: float
    software_immobility_raw_s: float        # window − mobility（与人工口径镜像）
    software_immobility_pipeline_s: float
    truth_immobility_s: float
    abs_error_pipeline_s: float
    total_frames: int
    scoreable_frames: int
    unknown_fraction: float
    #: 软件 Mobility 段（bout 流水线后，秒；闭区间帧号换算 [start/fps, (end+1)/fps]）。
    #: onset_match 目标的输入；Interval 闭区间语义在这里收口，别处不再猜。
    software_mobility_pipeline_segs: tuple[tuple[float, float], ...] = ()
    #: DP-035（G11）：软件-人工逐秒 Jaccard；真值段缺失时为 None（算不出≠通过）。
    jaccard_vs_truth: float | None = None
    #: DP-045：双方皆空（人工与软件都没有任何运动段）。这类试次**不进 G11 均值的
    #: 分母**——按 DP-043，声明为空的隔间根本不该进分析，记 1.0 是白送分（会把
    #: 门抬虚），记 0 是冤枉；一律剔除并单独报条数。
    jaccard_both_empty: bool = False


@dataclass(frozen=True)
class ThetaFit:
    theta: float
    mean_abs_err_pipeline_s: float    # 最优点处的**目标函数值**（语义见 objective）
    grid: tuple[float, ...]
    curve: tuple[float, ...]
    #: DP-037：本拟合用的目标函数。默认 total_immobility——历史数字全按它，不许偷改。
    objective: str = OBJECTIVE_TOTAL


@dataclass(frozen=True)
class FoldResult:
    held_out_video: str
    fit: ThetaFit
    n_fit_trials: int
    predictions: tuple[TrialEval, ...]


@dataclass(frozen=True)
class LovoResult:
    folds: tuple[FoldResult, ...]
    bout_params: bouts.BoutParams           # 记录用的 FROZEN 参数，从不被拟合
    theta_values: tuple[float, ...]         # 每折一个——这就是 G2 的原料
    theta_cv_pct: float                     # G2：跨折变异系数 ×100
    n_predictions: int
    pooled_pearson_r: float
    ba_bias_s: float
    ba_loa_low_s: float
    ba_loa_high_s: float
    truth_sources: tuple[str, ...]
    #: DP-037：本结果用的目标函数（默认 = 历史口径，不许偷改）。
    objective: str = OBJECTIVE_TOTAL
    #: DP-035（G11）：全部样本外预测的逐秒 Jaccard 均值；任何一试次缺真值段 ⇒ None
    #: （算不出必须显式呈现，不许部分平均冒充全体）。
    g11_mean_jaccard: float | None = None

    def summary(self) -> str:
        lines = [
            "LOVO-CV（拟合对象唯一：θ_mob；bout 参数 FROZEN 未参与拟合）",
            f"  折数 = {len(self.folds)}  样本外预测数 = {self.n_predictions}"
            f"  真值来源 = {sorted(set(self.truth_sources))}"
            f"  目标函数 = {self.objective}",
            "  每折 θ_mob：",
        ]
        for f in self.folds:
            label = ("MAE" if f.fit.objective == OBJECTIVE_TOTAL
                     else "起始误差")
            lines.append(
                f"    held-out {f.held_out_video:<28s} θ={f.fit.theta:.4f}"
                f"（拟合 {label} {f.fit.mean_abs_err_pipeline_s:.2f} s，"
                f"训练 {f.n_fit_trials} 试次，预测 {len(f.predictions)} 试次）")
        thetas = np.asarray(self.theta_values)
        lines += [
            f"  θ_mob 跨折: mean={thetas.mean():.4f} sd={thetas.std(ddof=1):.4f} "
            f"min={thetas.min():.4f} max={thetas.max():.4f}",
            f"  G2 = θ_mob 跨折变异系数 = {self.theta_cv_pct:.1f}%（门槛 ≤15%）",
            f"  样本外 Pearson r = {self.pooled_pearson_r:.3f}"
            f"（G7 门槛 ≥0.818，合成口径仅验管道）",
            f"  Bland-Altman: 偏差 {self.ba_bias_s:+.2f} s, "
            f"LoA [{self.ba_loa_low_s:.2f}, {self.ba_loa_high_s:.2f}] s",
        ]
        # ---- DP-035：G11 逐秒 Jaccard + G7/G8/G11 捆绑判定 ----
        all_p = [p for f in self.folds for p in f.predictions]
        empty = [p.trial_id for p in all_p if p.jaccard_both_empty]
        per_trial = [(p.trial_id, p.jaccard_vs_truth)
                     for p in all_p if not p.jaccard_both_empty]
        missing = [t for t, j in per_trial if j is None]
        # DP-045：分母永远显式——剔除了几个双方皆空试次必须写出来
        excl = (f"，**剔除 {len(empty)} 个双方皆空试次**（DP-045，不记 1.0 不记 0）"
                f"：{empty[:8]}{'…' if len(empty) > 8 else ''}" if empty else "")
        if self.g11_mean_jaccard is not None:
            lines.append(
                f"  G11 逐秒 Jaccard（软件-人工，与人工-人工基线同式）"
                f" mean = {self.g11_mean_jaccard:.3f}（分母 n={len(per_trial)}/"
                f"{len(all_p)}{excl}，门槛 ≥{G11_MIN_JACCARD} = 人工-人工实测，"
                f"DP-035）")
            lines.append("    逐试次升序（拖累项排前）：")
            for t, j in sorted(per_trial, key=lambda kv: kv[1]):
                lines.append(f"      {j:.3f}  {t}")
        else:
            why = (f"{len(missing)}/{len(per_trial)} 试次缺真值段："
                   f"{missing[:8]}{'…' if len(missing) > 8 else ''}"
                   if per_trial else
                   f"剔除 {len(empty)} 个双方皆空试次后**一个可评试次都不剩**")
            lines.append(
                f"  G11 逐秒 Jaccard：**算不出**（{why}）"
                "——算不出按不过处理，不报≠过")
        g7_ok = self.pooled_pearson_r >= G7_MIN_R
        g8_ok = abs(self.ba_bias_s) <= G8_MAX_BIAS_S
        g11_ok = (self.g11_mean_jaccard is not None
                  and self.g11_mean_jaccard >= G11_MIN_JACCARD)
        marks = (("G7 r≥0.818", g7_ok), ("G8 |bias|≤28.6s", g8_ok),
                 ("G11 Jaccard≥0.738", g11_ok))
        lines.append(
            "  验收门捆绑（G7/G8/G11 同时报告，任一不过即不过）: "
            + "; ".join(f"{n} {'过' if ok else '**不过**'}" for n, ok in marks)
            + f" ⇒ 总判定：{'三项全过' if all(ok for _, ok in marks) else '不过'}")
        if set(self.truth_sources) != {"human"}:
            lines.append(
                "  [口径声明] 本表真值含合成——上面的捆绑判定只是**管道演示**，"
                "不得对外作为验收证据；正式 G1–G11 以 DP-014（人工真值）为准。")
        return "\n".join(lines)


# ---------------------------------------------------------------- 单试次评估


def evaluate_trial(sample: TrialSample, theta: float, *,
                   bout_params: bouts.BoutParams | None = None,
                   held_out: bool = False) -> TrialEval:
    """θ 给定的软件侧评估。规则判定全部走 rules.label_tst_events——
    本函数不复制任何判定逻辑，防止口径分叉。"""
    bp = bout_params or bouts.CSI_TST_STARTING_POINT
    params = replace(rules.TstRulesParams(), theta_mob=theta)
    f = sample.features
    labels = rules.label_tst_events(f, params)

    total = int(f.residual.size)
    unknown = labels.unknown
    scoreable = total - int(np.count_nonzero(unknown))
    unknown_fraction = (int(np.count_nonzero(unknown)) / total) if total else 0.0

    mobility_series = np.where(labels.mobility, "Mobility", None)
    summary = bouts.score(list(mobility_series), "Mobility", params=bp, fps=f.fps)

    mob_raw = float(labels.mobility.sum()) / f.fps
    mob_pipe = summary.seconds
    # Interval.start/end 为闭区间帧号 → 半开区间秒 [s, e)
    segs = tuple((iv.start / f.fps, (iv.end + 1) / f.fps) for iv in summary.intervals)
    return TrialEval(
        trial_id=sample.trial_id, video=sample.video, held_out=held_out, theta=theta,
        software_mobility_raw_s=mob_raw,
        software_mobility_pipeline_s=mob_pipe,
        software_immobility_raw_s=sample.window_s - mob_raw,
        software_immobility_pipeline_s=sample.window_s - mob_pipe,
        truth_immobility_s=sample.truth_immobility_s,
        abs_error_pipeline_s=abs((sample.window_s - mob_pipe) - sample.truth_immobility_s),
        total_frames=total, scoreable_frames=scoreable,
        unknown_fraction=unknown_fraction,
        software_mobility_pipeline_segs=segs,
        jaccard_vs_truth=(None if sample.truth_mobile_segs is None
                          else jaccard_segs(segs, sample.truth_mobile_segs)),
        jaccard_both_empty=(sample.truth_mobile_segs is not None
                            and not segs and not sample.truth_mobile_segs),
    )


# ---------------------------------------------------------------- 目标函数


def jaccard_segs(a_segs: Sequence[tuple[float, float]],
                 b_segs: Sequence[tuple[float, float]]) -> float:
    """两组时间段的时长加权 Jaccard = 交集 / (A总 + B总 − 交集)。

    与 `cli/scorer_disagreement` 人工-人工基线 0.738 **完全同式**——交集/总长
    都复用那边的实现，G11 与基线之间不许有第二套账（两套账 = 不可比 = 门失效）。
    唯一的特例是双方皆空（denominator=0）：约定记 1.0——"两边都说整段没有任何
    已判运动段"是零分歧，判对不罚（与 onset_match 的零段语义同构）。一侧空另一侧
    不空由公式自然给 0.0（完全不重叠），不特判。
    """
    a, b = list(a_segs), list(b_segs)
    inter = intersect_total(a, b)
    denom = segs_total(a) + segs_total(b) - inter
    if denom <= 1e-12:
        return 1.0 if not a and not b else 0.0
    return inter / denom


def g11_mean(preds: Sequence["TrialEval"]) -> float | None:
    """G11 均值：all-or-nothing + DP-045 双方皆空剔除。

    两条规则，缺一门就失效：
    - 有任何一个试次**缺真值段** ⇒ 整门 None（算不出≠通过，不许部分平均冒充全体）；
    - **双方皆空**的试次剔出分母。`jaccard_segs` 对这种情况返回 1.0（成对函数
      的语义是对的：零分歧判对不罚），但把它算进均值就是**白送分**——按 DP-043，
      声明为空的隔间根本不该进分析，靠"隔间是空的"把 G11 抬上门槛是造假通过。
      剔完一个不剩 ⇒ None，同样按不过处理。
    """
    if not preds or any(p.jaccard_vs_truth is None for p in preds):
        return None
    kept = [p.jaccard_vs_truth for p in preds if not p.jaccard_both_empty]
    return float(np.mean(kept)) if kept else None


def onset_match_error(sw_segs: Sequence[tuple[float, float]],
                      truth_segs: Sequence[tuple[float, float]]) -> float:
    """互为最佳重叠配对后的**起始时刻**平均绝对误差（秒）。

    配对逻辑复用 `cli/scorer_disagreement.mutual_best_edges`——一对多匹配会把同
    一个长段的偏移重复计入每个短段（实测虚高 834%，那边有测试锁住），这里不许重写。
    无配对（软件一段没判出来，或与真值完全不相交）⇒ **+inf 不是 NaN**：
    "判不出任何运动"是最坏行为，必须被目标函数看见，不许以"未定义"名义静默获胜。
    两个例外都是语义要求而非兜底：真值无运动且软件也无 ⇒ 0.0（判对的行为不许罚）；
    真值无运动而软件凭空判出 ⇒ inf（幻影比漏判更糟）。
    已知局限（如实记）：只量**已配对**段的起始差，软件漏掉部分真值段不会直接抬分——
    与 DP-036 分析口径一致，配对覆盖率由调用方另看，不掺进目标函数。
    """
    if not truth_segs:
        return 0.0 if not sw_segs else math.inf
    if not sw_segs:
        return math.inf
    pairs = mutual_best_edges(list(sw_segs), list(truth_segs))
    if not pairs:
        return math.inf
    return float(np.mean([abs(onset_diff) for onset_diff, _ in pairs]))


def _objective_value(sample: TrialSample, ev: TrialEval, objective: str) -> float:
    if objective == OBJECTIVE_TOTAL:
        return ev.abs_error_pipeline_s
    if objective == OBJECTIVE_ONSET:
        if sample.truth_mobile_segs is None:
            raise ValueError(
                f"{sample.trial_id}: onset_match 目标要求 truth_mobile_segs（normalize 后的"
                "真值段）——缺了它目标函数没定义，不许静默退回总量差")
        return onset_match_error(ev.software_mobility_pipeline_segs,
                                 sample.truth_mobile_segs)
    raise ValueError(f"未知目标函数 {objective!r}，只许 {OBJECTIVES}")


# ---------------------------------------------------------------- θ 拟合


def _default_grid() -> np.ndarray:
    """两阶段网格：31 点粗扫 + 最优邻域细化到 0.0005 步长。确定性，无 scipy。"""
    return np.linspace(THETA_SEARCH_MIN, THETA_SEARCH_MAX, 31)


def fit_theta_mob(fit_samples: Sequence[TrialSample], *,
                  bout_params: bouts.BoutParams | None = None,
                  grid: Sequence[float] | None = None,
                  objective: str = OBJECTIVE_TOTAL) -> ThetaFit:
    """网格搜 θ_mob 使目标函数最小。

    - `total_immobility`（默认）：mean|软件 immobility − 真值 immobility|（秒）。
      **默认值不许改**——已跑出的数字全按它，偷改 = 口径断裂（DP-037 派工单原话）。
    - `onset_match`：mean 起始时刻配对误差（秒），只信人可靠的边界。
    - 目标函数对 θ 是阶梯状分段常数：argmin 是平台时取平台中点（确定性 tie-break，
      不依赖遍历顺序）。`mean_abs_err_pipeline_s` 存的是**最优点处的目标函数值**
      （两种 objective 单位都是秒，语义由 objective 字段标注）。
    """
    if not fit_samples:
        raise ValueError("fit_theta_mob 收到空训练集——拒跑，不猜")
    if objective not in OBJECTIVES:
        raise ValueError(f"未知目标函数 {objective!r}，只许 {OBJECTIVES}")
    bp = bout_params or bouts.CSI_TST_STARTING_POINT
    if grid is not None:
        # 显式网格 = 唯一网格（测试用小网格提速时，不许被默认细网格悄悄加回去）
        fine = np.unique(np.round(np.asarray(grid, dtype=float), 6))
    else:
        fine = np.unique(np.round(np.concatenate(
            [_default_grid(),
             np.arange(THETA_SEARCH_MIN, THETA_SEARCH_MAX + 1e-12, 0.0005)]), 6))
    return _grid_search(fit_samples, fine, bp, objective)


def _grid_search(samples: Sequence[TrialSample], grid: np.ndarray,
                 bp: bouts.BoutParams, objective: str) -> ThetaFit:
    n = len(samples)
    curve = np.empty(len(grid))
    for k, theta in enumerate(grid):
        tot = 0.0
        for s in samples:
            e = evaluate_trial(s, float(theta), bout_params=bp)
            tot += _objective_value(s, e, objective)
        curve[k] = tot / n
    best = curve.min()
    if not np.isfinite(best):
        # onset 目标下全网格都有试次配不上对——这个数据集不支持该目标，如实报错
        raise ValueError(
            f"{objective}: 全网格上都有试次无配对（软件在一切 θ 下都判不出/配不上真值段）"
            "——目标未定义，不硬凑一个数")
    at_min = np.flatnonzero(curve <= best + 1e-12)
    # 最宽的最小值平台取中点（阶梯函数常见）
    runs = np.split(at_min, np.flatnonzero(np.diff(at_min) > 1) + 1)
    widest = max(runs, key=len)
    theta_hat = float(grid[widest].mean())
    return ThetaFit(theta=round(theta_hat, 6),
                    mean_abs_err_pipeline_s=round(float(best), 4),
                    grid=tuple(round(float(g), 6) for g in grid),
                    curve=tuple(round(float(c), 4) for c in curve),
                    objective=objective)


# ---------------------------------------------------------------- LOVO 主循环


def lovo_cv(samples: Sequence[TrialSample], *,
            bout_params: bouts.BoutParams | None = None,
            theta_grid: Sequence[float] | None = None,
            objective: str = OBJECTIVE_TOTAL) -> LovoResult:
    """留一视频交叉验证。每折输出：θ、θ 曲线、全部样本外预测行。

    objective（DP-037）：默认 `total_immobility` = 历史口径，不许改默认；
    `onset_match` 要求每个训练试次带 truth_mobile_segs（缺了立刻报错，不静默退化）。
    """
    if objective not in OBJECTIVES:
        raise ValueError(f"未知目标函数 {objective!r}，只许 {OBJECTIVES}")
    bp = bout_params or bouts.CSI_TST_STARTING_POINT
    videos = sorted({s.video for s in samples})
    if len(videos) < 2:
        raise ValueError(
            f"LOVO-CV 需要 ≥2 个视频（留'一视频'出），当前只有 {videos}——"
            "单视频上的 θ 拟合是 in-sample，G2 无从谈起。报警不硬算。")

    folds: list[FoldResult] = []
    all_pred: list[TrialEval] = []
    for v in videos:
        fit_set = [s for s in samples if s.video != v]
        hold_set = [s for s in samples if s.video == v]
        fit = fit_theta_mob(fit_set, bout_params=bp, grid=theta_grid,
                            objective=objective)
        preds = tuple(evaluate_trial(s, fit.theta, bout_params=bp, held_out=True)
                      for s in hold_set)
        folds.append(FoldResult(held_out_video=v, fit=fit,
                                n_fit_trials=len(fit_set), predictions=preds))
        all_pred.extend(preds)

    if len(all_pred) != len(samples):
        raise AssertionError("样本外预测数 ≠ 试次数——有试次被重复留出或漏掉，拒收此结果")

    truth = np.array([p.truth_immobility_s for p in all_pred])
    soft = np.array([p.software_immobility_pipeline_s for p in all_pred])
    thetas = tuple(f.fit.theta for f in folds)
    th = np.asarray(thetas)
    diff = soft - truth
    cv = float(100.0 * th.std(ddof=1) / th.mean()) if th.size > 1 else 0.0
    # G11：all-or-nothing——有一个试次缺真值段就整门"算不出"，不许部分平均冒充全体。
    # DP-045：双方皆空的试次先剔除（不记 1.0 也不记 0），剔完没剩 ⇒ 算不出，不是过。
    g11 = g11_mean(all_pred)
    return LovoResult(
        folds=tuple(folds),
        bout_params=bp,
        theta_values=thetas,
        theta_cv_pct=round(cv, 2),
        n_predictions=len(all_pred),
        pooled_pearson_r=_pearson(truth, soft),
        ba_bias_s=float(diff.mean()),
        ba_loa_low_s=float(diff.mean() - 1.96 * diff.std(ddof=1)) if len(diff) > 1 else 0.0,
        ba_loa_high_s=float(diff.mean() + 1.96 * diff.std(ddof=1)) if len(diff) > 1 else 0.0,
        truth_sources=tuple(s.truth_source for s in samples),
        objective=objective,
        g11_mean_jaccard=g11,
    )


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    if a.size < 2:
        raise ValueError("r 需要 ≥2 个样本外预测")
    aa = a - a.mean()
    bb = b - b.mean()
    denom = float(np.sqrt((aa ** 2).sum() * (bb ** 2).sum()))
    if denom == 0.0:
        raise ValueError("r 无定义（一侧零方差）——如实报错，不返回 0")
    return float((aa * bb).sum() / denom)


# ---------------------------------------------------------------- 目标函数对照（DP-037）


def lovo_cv_objective_comparison(samples: Sequence[TrialSample], *,
                                 bout_params: bouts.BoutParams | None = None,
                                 theta_grid: Sequence[float] | None = None,
                                 ) -> dict[str, LovoResult]:
    """两个目标函数各跑一遍 LOVO-CV：同样本、同网格、同 FROZEN bout 参数。

    返回 {objective: LovoResult}。对照的全部意义在**两组 θ_mob 是否接近**——
    接近 ⇒ θ 稳（不是在追人的松手习惯）；差很多 ⇒ 总量目标主要在被松手方差牵引，
    目标函数要改成 onset 那个。**判"接近/差很多"的是道俊/Capy，不是本函数**——
    这里只并排列数字，不自定阈值（禁用自己的输出调阈值）。
    """
    return {
        obj: lovo_cv(samples, bout_params=bout_params, theta_grid=theta_grid,
                     objective=obj)
        for obj in OBJECTIVES
    }


def format_objective_comparison(comp: dict[str, LovoResult]) -> str:
    """两组 θ_mob 并列打印，各带自己的 G2（派工单要求"并列打印各带自己的 G2"）。"""
    if set(comp) != set(OBJECTIVES):
        raise ValueError(f"对照表须含且仅含 {OBJECTIVES}，得到 {sorted(comp)}")
    lines = ["═══ DP-037 目标函数对照（同 7 折样本、同网格、bout 参数 FROZEN）═══"]
    for obj in OBJECTIVES:
        res = comp[obj]
        thetas = np.asarray(res.theta_values)
        lines.append(f"── objective = {obj} ──")
        for f in res.folds:
            lines.append(f"    held-out {f.held_out_video:<28s} θ={f.fit.theta:.4f}")
        lines.append(
            f"    θ: mean={thetas.mean():.4f} sd={thetas.std(ddof=1):.4f} "
            f"min={thetas.min():.4f} max={thetas.max():.4f}   G2 = {res.theta_cv_pct:.1f}%")
    a = np.asarray(comp[OBJECTIVE_TOTAL].theta_values)
    b = np.asarray(comp[OBJECTIVE_ONSET].theta_values)
    lines += [
        "── 差异（不判好坏，只列数）──",
        f"    Δmean(θ) = {abs(b.mean() - a.mean()):.4f}   "
        f"ΔG2 = {comp[OBJECTIVE_ONSET].theta_cv_pct - comp[OBJECTIVE_TOTAL].theta_cv_pct:+.1f} 个百分点",
        f"    逐折 |Δθ| 最大 = {np.abs(b - a).max():.4f}（held-out "
        f"{comp[OBJECTIVE_TOTAL].folds[int(np.abs(b - a).argmax())].held_out_video}）",
        "判读（道俊/Capy 拍板）：两组接近 ⇒ θ 稳；差很多 ⇒ 拟合目标主要在追松手习惯，目标函数应改用 onset_match。",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- 预测表导出


def prediction_rows(res: LovoResult) -> list[dict]:
    """每试次一行（带分母，G9 口径）。供 DP-014 报告与人工核对。"""
    out = []
    for f in res.folds:
        for p in f.predictions:
            out.append({
                "trial_id": p.trial_id, "video": p.video,
                "held_out_video": f.held_out_video,
                "theta_fold": f.fit.theta,
                "truth_immobility_s": round(p.truth_immobility_s, 2),
                "software_immobility_pipeline_s": round(p.software_immobility_pipeline_s, 2),
                "software_immobility_raw_s": round(p.software_immobility_raw_s, 2),
                "software_mobility_pipeline_s": round(p.software_mobility_pipeline_s, 2),
                "abs_error_pipeline_s": round(p.abs_error_pipeline_s, 2),
                "total_frames": p.total_frames,
                "scoreable_frames": p.scoreable_frames,
                "unknown_fraction": round(p.unknown_fraction, 4),
                "g11_jaccard": (None if p.jaccard_vs_truth is None
                                else round(p.jaccard_vs_truth, 4)),
                "bout_params_frozen": res.bout_params == bouts.CSI_TST_STARTING_POINT,
                "objective": res.objective,
            })
    return out


# ---------------------------------------------------------------- 合成真值数据


#: 与交付集同构的 7 视频（27 试次 = 7×4 − 1，缺 30mg_2周-ch4，见 DP-005）。
SYNTHETIC_VIDEOS = (
    "10mg_2周", "20mg_2周", "20mg_3周",
    "20mg_1周_1-3+20_2周1", "30mg_2周",
    "30mg_2周_1-3+20_1周1", "30mg_2周_2+20_2周2",
)


def synthetic_lovo_trials(*, n_videos: int = 7, chambers: tuple[int, ...] = (1, 2, 3, 4),
                          drop_last: bool = True, window_s: float = 60.0,
                          fps: float = 10.0, seed: int = 20260903) -> list[TrialSample]:
    """软件对软件的合成真值集：解析式特征，结构对齐交付集（7 视频 × 4 隔间 − 1）。

    生成原理（每个视频一套物理量级，模拟不同批次/动物）：
      - 静止帧残差 ~ N(L0, σ)，L0 落在钟摆/静止带（≈0.006–0.011，见 STATUS 基线）；
      - 活动帧残差 ~ N(L1, σ)，L1 落在主动挣扎带（≈0.025–0.045）；两带之间有间隙
        ⇒ 真 θ* 平台存在，框架应当找回它；
      - 活动帧尾侧残差比 rho_hind ∈ U(0.35, 0.70)（> θ_hind=0.30 ⇒ 计 Mobility，
        不是 ForelimbOnly）；
      - 真值 mobile 时长 = 激活段并集（与人工 union(holds) 同口径）。
    """
    rng = np.random.default_rng(seed)
    videos = list(SYNTHETIC_VIDEOS[:n_videos])
    n_total = len(videos) * len(chambers)
    # 视频级物理量级：跨视频有漂移（这正是 G2 要考的），带内间隙保持 ≥0.010
    l0 = rng.uniform(0.006, 0.0105, size=len(videos))
    l1 = l0 + rng.uniform(0.016, 0.028, size=len(videos))
    sigma = rng.uniform(0.0008, 0.0018, size=len(videos))
    samples: list[TrialSample] = []
    for vi, vid in enumerate(videos):
        for ci, ch in enumerate(chambers):
            if drop_last and vi == len(videos) - 1 and ci == len(chambers) - 1:
                continue  # 30mg_2周-ch4：交付集本就缺失（DP-005）
            n_frames = int(round(window_s * fps))
            residual = rng.normal(l0[vi], sigma[vi], n_frames)
            # 真值激活段：随机 3–6 段，合计 ≤ window−20%（保证 immobility 有下限）
            segs: list[tuple[int, int]] = []
            budget = int((window_s * rng.uniform(0.20, 0.62)) * fps)
            while budget > int(2 * fps):
                length = min(budget, int(rng.uniform(2, 12) * fps))
                start = int(rng.uniform(0, n_frames - length))
                segs.append((start, start + length))
                budget -= length
            truth_mobile = 0.0
            merged: list[tuple[int, int]] = []
            if segs:
                merged = _merge(sorted(segs))
                truth_mobile = sum(b - a for a, b in merged) / fps
                for a, b in merged:
                    residual[a:b] = rng.normal(l1[vi], sigma[vi], b - a)
            rho_hind = rng.uniform(0.0, 1.0, n_frames)
            for a, b in merged:
                rho_hind[a:b] = rng.uniform(0.35, 0.70, b - a)
            residual = np.clip(residual, 0.0, None)
            omega = np.zeros(n_frames)
            # 一个试次掺一点 unknown 帧（NaN），验证分母与 unknown 记账
            trial_id = f"{vid}-ch{ch}"
            if rng.random() < 0.15:
                k = int(rng.integers(5, 20))
                pos = int(rng.integers(0, n_frames - k))
                residual[pos:pos + k] = np.nan
            f = rules.TstFeatures(
                residual=residual, omega=omega, rho_hind=rho_hind,
                hole_count=np.zeros(n_frames, dtype=int),
                centroid_dy=np.full(n_frames, np.nan), fps=fps,
            )
            samples.append(TrialSample(
                trial_id=trial_id, video=vid, features=f,
                truth_mobile_s=float(truth_mobile), window_s=window_s,
                truth_source="synthetic",
                # 帧半开区间 [a,b) → 秒半开区间；与软件侧 segs 同口径（DP-037）
                truth_mobile_segs=tuple((a / fps, b / fps) for a, b in merged),
            ))
    return samples


def _merge(segs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for a, b in segs:
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out
