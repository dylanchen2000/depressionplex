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
- 拟合结果**不回写** rules.TstRulesParams 的 FROZEN 值——每折 θ 是估计量，
  出货默认值换不换由道俊拍板。
- 软件 immobility 的口径与人工镜像对齐：人工 mobile = union(在按键段)，
  immobility = window − mobile；软件侧同样 mobile = bout 流水线后的 Mobility
  时长（raw 变体同报，DP-014 要求两侧同时施加 bout 时不迷路）。
- 分母随行携带（G9）：total_frames / scoreable_frames / unknown_fraction。
- 阈值单位：θ 是 BL² 归一化残差（无量纲物理量），搜索网格边界只是搜索范围
  声明，不是判定阈值。时间一律秒。
- assay_core 纪律沿用：只依赖 numpy。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Sequence

import numpy as np

from .assay_core import bouts, rules

#: 唯一允许被拟合的参数名。写死在这里，报告里也好对账。
FITTED_PARAM = "theta_mob"
#: 搜索网格 = θ 的物理合理范围声明（合成标定区间 0.015–0.02 的左右各放大一倍）。
#: 它不是判定阈值，FROZEN 出货值 0.0175 在网格内。
THETA_SEARCH_MIN = 0.005
THETA_SEARCH_MAX = 0.05


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


@dataclass(frozen=True)
class ThetaFit:
    theta: float
    mean_abs_err_pipeline_s: float
    grid: tuple[float, ...]
    curve: tuple[float, ...]


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

    def summary(self) -> str:
        lines = [
            "LOVO-CV（拟合对象唯一：θ_mob；bout 参数 FROZEN 未参与拟合）",
            f"  折数 = {len(self.folds)}  样本外预测数 = {self.n_predictions}"
            f"  真值来源 = {sorted(set(self.truth_sources))}",
            "  每折 θ_mob：",
        ]
        for f in self.folds:
            lines.append(
                f"    held-out {f.held_out_video:<28s} θ={f.fit.theta:.4f}"
                f"（拟合 MAE {f.fit.mean_abs_err_pipeline_s:.2f} s，"
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
    )


# ---------------------------------------------------------------- θ 拟合


def _default_grid() -> np.ndarray:
    """两阶段网格：31 点粗扫 + 最优邻域细化到 0.0005 步长。确定性，无 scipy。"""
    return np.linspace(THETA_SEARCH_MIN, THETA_SEARCH_MAX, 31)


def fit_theta_mob(fit_samples: Sequence[TrialSample], *,
                  bout_params: bouts.BoutParams | None = None,
                  grid: Sequence[float] | None = None) -> ThetaFit:
    """网格搜 θ_mob 使 mean|软件 immobility − 真值 immobility| 最小。

    目标函数对 θ 是阶梯状分段常数：argmin 是平台时取平台中点（确定性 tie-break，
    不依赖遍历顺序）。
    """
    if not fit_samples:
        raise ValueError("fit_theta_mob 收到空训练集——拒跑，不猜")
    bp = bout_params or bouts.CSI_TST_STARTING_POINT
    if grid is not None:
        # 显式网格 = 唯一网格（测试用小网格提速时，不许被默认细网格悄悄加回去）
        fine = np.unique(np.round(np.asarray(grid, dtype=float), 6))
    else:
        fine = np.unique(np.round(np.concatenate(
            [_default_grid(),
             np.arange(THETA_SEARCH_MIN, THETA_SEARCH_MAX + 1e-12, 0.0005)]), 6))
    return _grid_search(fit_samples, fine, bp)


def _grid_search(samples: Sequence[TrialSample], grid: np.ndarray,
                 bp: bouts.BoutParams) -> ThetaFit:
    n = len(samples)
    curve = np.empty(len(grid))
    for k, theta in enumerate(grid):
        tot = 0.0
        for s in samples:
            e = evaluate_trial(s, float(theta), bout_params=bp)
            tot += e.abs_error_pipeline_s
        curve[k] = tot / n
    best = curve.min()
    at_min = np.flatnonzero(curve <= best + 1e-12)
    # 最宽的最小值平台取中点（阶梯函数常见）
    runs = np.split(at_min, np.flatnonzero(np.diff(at_min) > 1) + 1)
    widest = max(runs, key=len)
    theta_hat = float(grid[widest].mean())
    return ThetaFit(theta=round(theta_hat, 6),
                    mean_abs_err_pipeline_s=round(float(best), 4),
                    grid=tuple(round(float(g), 6) for g in grid),
                    curve=tuple(round(float(c), 4) for c in curve))


# ---------------------------------------------------------------- LOVO 主循环


def lovo_cv(samples: Sequence[TrialSample], *,
            bout_params: bouts.BoutParams | None = None,
            theta_grid: Sequence[float] | None = None) -> LovoResult:
    """留一视频交叉验证。每折输出：θ、θ 曲线、全部样本外预测行。"""
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
        fit = fit_theta_mob(fit_set, bout_params=bp, grid=theta_grid)
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
                "bout_params_frozen": res.bout_params == bouts.CSI_TST_STARTING_POINT,
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
            if segs:
                merged = _merge(sorted(segs))
                truth_mobile = sum(b - a for a, b in merged) / fps
                for a, b in merged:
                    residual[a:b] = rng.normal(l1[vi], sigma[vi], b - a)
            rho_hind = rng.uniform(0.0, 1.0, n_frames)
            for a, b in merged if segs else []:
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
