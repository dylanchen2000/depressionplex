"""trial 级输出串联：rules 事件标签 + bouts 流水线 + 分母 + TrialValidity。

工单口径（2026-09-03 二轮 #5）：**每个 immobility 数字旁必须同时给分母**——
可评分帧/总帧、unknown 占比、`TrialValidity`。无分母的指标不可审计（CSI
不给，这是我方差异化 + GLP 硬要求）。

计分窗口两范式不同，**不许抹平**：

    FST = 6 min 里只计后 4 min（前 2 min 适应不计）
    TST = 全程 6 min 都计

`assay` 无默认值、未知值直接 raise；窗口表在测试里被钉死（两支 end 相同、
start 必须不同——有人把两行改成一样就变红）。窗口切帧只做秒→帧换算，
本层**不新增任何像素/帧常数**：bout 参数沿用已冻结的 CSI 兼容起点值
（帧单位为 CSI 对齐口径，已另行登记，不是这里引入的）。

**分母先于数值**：窗口内 unknown 帧不进可评分分母；所有占比同时给
"占窗口"与"占可评分"两个口径，不静默挑一个。录像短于窗口 ⇒ 截断但**必须**
带 warning（不算静默收缩）；录像完全落在窗口之前 ⇒ raise 要人工。

**唯一评分入口 = `validity.score_gate`**（DP-028 N1/N3）：排除态隔间
（never_occupied / detached / truncated_suspect / unknown）不放行——
流水线照常算出候选值递给闸门，闸门拦截并把幻影数值写进报警行；报告里
**不产出任何 immobility 数字**。`chamber_validity=None`（没做有效性判定）
时数字照常出，但警告必须显式标注"不是放行凭据"——静默缺判同罪。

镜像口径（主口径）：`immobility = 窗口 − Mobility(流水线)`，与人工侧
（秒表只记活动段）及 `lovo_cv.software_immobility_*` 完全同式——只此一本
账。事件口径的 Immobility（rules 的 L1 标签）在分类表里单列，两数语义
不同（差 = unknown + 短事件清除 + ForelimbOnly），渲染器分别标注，禁止混用。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import bouts, rules, validity

#: 计分窗口（秒，自试次录像起点）。两范式**不许抹平**——改任何一行前先读
#: docs/SPEC_人工比对与验收_v1.md §"计分窗口"（FST 后 4 min / TST 全程 6 min）。
#: 秒是单位不变量；帧号一律按本试次 fps 换算，不写帧数常数。
ASSAY_WINDOWS: dict[str, tuple[float, float]] = {
    "TST": (0.0, 360.0),    # 全程 6 min 都计
    "FST": (120.0, 360.0),  # 6 min 里只计后 4 min（前 2 min 适应不计）
}


@dataclass(frozen=True)
class CategoryStat:
    """一个事件类别在**计分窗口内**的统计（分母随行可查）。"""

    name: str
    bouts: int
    frames: int                       # 流水线后帧数（窗口内）
    seconds_pipeline: float
    seconds_raw: float                # 逐帧未平滑
    pct_of_window: float              # 100 × frames / 窗口帧数
    pct_of_scorable: float | None     # 100 × frames / 可评分帧数；分母为 0 ⇒ None
    first_onset_s: float | None       # 首个 bout 起点，相对**窗口起点**（秒）


@dataclass(frozen=True)
class TrialReport:
    """单试次（单隔间）的 trial 级输出。数字块（categories/镜像值）仅在
    `scored=True` 时存在；分母块永远存在——拦截时更要看得见分母。"""

    trial_id: str
    assay: str
    fps: float
    recording_frames: int
    window_start_s: float             # 名义窗口（来自 ASSAY_WINDOWS）
    window_end_s: float
    window_frames: int                # 实际可用窗口帧数（短录像时 < 名义）
    unknown_frames_window: int
    scorable_frames_window: int
    unknown_frames_recording: int
    validity_status: str              # ChamberValidity.status 或 "not_assessed"
    occupied_fraction: float | None
    scored: bool
    immobility_mirror_pipeline_s: float | None   # 主口径：窗口 − Mobility(流水线)
    immobility_mirror_raw_s: float | None
    categories: dict[str, CategoryStat]
    gate_messages: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def window_seconds(self) -> float:
        return self.window_frames / self.fps

    @property
    def unknown_fraction_window(self) -> float:
        return self.unknown_frames_window / self.window_frames

    @property
    def unknown_fraction_recording(self) -> float:
        return self.unknown_frames_recording / self.recording_frames


def _default_bout_params(assay: str) -> bouts.BoutParams:
    # 今天两组数值同源（TST 沿用 FST 起点），但**不共用同一个默认参数引用**：
    # 将来任一侧重新标定，另一侧不得被顺带改掉。
    return (bouts.CSI_FST_DEFAULTS if assay == "FST"
            else bouts.CSI_TST_STARTING_POINT)


def build_trial_report(
    labels: rules.TstEventLabels,
    *,
    fps: float,
    assay: str,
    trial_id: str,
    chamber_validity: validity.ChamberValidity | None = None,
    bout_params: bouts.BoutParams | None = None,
) -> TrialReport:
    """把 rules 逐帧标签接进 bouts 流水线，产出带分母与有效性闸门的 trial 级输出。

    `chamber_validity`：该隔间的 `assess_trial_validity` 结论。给 None 表示
    "没做判定"——数字会产出，但报告带显式警告；**不是**"当作有效"。
    事件标签由调用方用冻结参数 `label_tst_events` 产出；本函数不调阈值、
    不回写任何参数（θ_mob 与 bout 参数保持 FROZEN）。
    """
    if fps <= 0:
        raise ValueError(f"fps 必须为正，收到 {fps}")
    if assay not in ASSAY_WINDOWS:
        raise ValueError(
            f"未知 assay {assay!r}：计分窗口只认 {sorted(ASSAY_WINDOWS)}。"
            "不许静默套用另一范式的窗口——FST 后 4 min / TST 全程，抹平即口径事故")
    w0, w1 = ASSAY_WINDOWS[assay]

    n = len(labels.unknown)
    for name, arr in labels.as_dict().items():
        if len(arr) != n:
            raise ValueError(
                f"{name} 序列长度 {len(arr)} ≠ unknown 长度 {n}"
                "——不同源数据不得串成一个试次（两套账护栏）")

    start_f = int(round(w0 * fps))
    end_f = int(round(w1 * fps))
    warnings: list[str] = []
    if n <= start_f:
        raise ValueError(
            f"试次 {trial_id}：录像仅 {n} 帧（{n / fps:.2f} s），完全落在 {assay} "
            f"计分窗 [{w0:g}, {w1:g}) s 之前——无帧可评，须人工确认素材与窗口起点")
    if n < end_f:
        warnings.append(
            f"recording_shorter_than_window：{assay} 窗口名义止于 {w1:g} s"
            f"（第 {end_f} 帧），录像止于 {n / fps:.2f} s（{n} 帧）"
            f"——窗口尾按实际截断为 {n - start_f} 帧，所有分母按截断后计，"
            "不做静默补齐")
        end_f = n

    window_frames = end_f - start_f
    unknown_slice = labels.unknown[start_f:end_f]
    unknown_frames_window = int(np.count_nonzero(unknown_slice))
    scorable = window_frames - unknown_frames_window
    unknown_frames_recording = int(np.count_nonzero(labels.unknown))

    bp = bout_params or _default_bout_params(assay)
    categories: dict[str, CategoryStat] = {}
    for name, series_full in labels.as_dict().items():
        series = np.asarray(series_full, dtype=bool)[start_f:end_f]
        raw_frames = int(np.count_nonzero(series))
        lab = np.where(series, name, None)
        summary = bouts.score(list(lab), name, params=bp, fps=fps)
        categories[name] = CategoryStat(
            name=name,
            bouts=summary.bouts,
            frames=summary.frames,
            seconds_pipeline=summary.seconds,
            seconds_raw=raw_frames / fps,
            pct_of_window=100.0 * summary.frames / window_frames,
            pct_of_scorable=(None if scorable <= 0
                             else 100.0 * summary.frames / scorable),
            first_onset_s=(None if summary.first_onset_frame is None
                           else summary.first_onset_frame / fps),
        )

    window_s = window_frames / fps
    mob = categories["Mobility"]
    mirror_pipeline = window_s - mob.seconds_pipeline
    mirror_raw = window_s - mob.seconds_raw

    if chamber_validity is None:
        status, occupied = "not_assessed", None
        gate_ok, gate_msgs = True, ()
        warnings.append(
            "validity_not_assessed：本试次未做试次级有效性判定"
            "（assess_trial_validity）——数字照常产出但**不是放行凭据**")
    else:
        status = chamber_validity.status
        occupied = chamber_validity.occupied_fraction
        # 候选值照算照递——幻影必须让闸门看得见才报得响（N3）
        gate_ok, gate_msgs = validity.score_gate(
            chamber_validity, candidate_immobility_s=mirror_pipeline)

    if not gate_ok:
        # 排除态：不放行 ⇒ 数字不进报告（N1），报警文本里带被拦截的候选值。
        categories = {}
        mirror_pipeline = mirror_raw = None

    return TrialReport(
        trial_id=trial_id, assay=assay, fps=fps,
        recording_frames=n, window_start_s=w0, window_end_s=w1,
        window_frames=window_frames,
        unknown_frames_window=unknown_frames_window,
        scorable_frames_window=scorable,
        unknown_frames_recording=unknown_frames_recording,
        validity_status=status, occupied_fraction=occupied,
        scored=gate_ok,
        immobility_mirror_pipeline_s=mirror_pipeline,
        immobility_mirror_raw_s=mirror_raw,
        categories=categories,
        gate_messages=tuple(gate_msgs),
        warnings=tuple(warnings),
    )


def trial_report_text(r: TrialReport) -> str:
    """人读渲染：分母行与有效性行**每个数字都带在旁**，不拆开给。"""
    L: list[str] = []
    L.append(f"==== 试次 {r.trial_id} ｜ {r.assay} ｜ fps={r.fps:g} ｜ "
             f"录像 {r.recording_frames} 帧（{r.recording_frames / r.fps:.2f} s）====")
    rule = ("FST：6 min 里只计后 4 min（前 2 min 适应不计）" if r.assay == "FST"
            else "TST：全程 6 min 都计")
    trunc = ("" if abs(r.window_seconds - (r.window_end_s - r.window_start_s)) < 1e-9
             else f"（短于名义窗，截断后）")
    L.append(f"计分窗：{rule} ⇒ [{r.window_start_s:g}, {r.window_end_s:g}) s；"
             f"实际 {r.window_frames} 帧 = {r.window_seconds:.2f} s{trunc}")
    L.append(f"分母：可评分帧 {r.scorable_frames_window}/{r.window_frames}（窗口内）"
             f"｜窗口内 unknown {r.unknown_frames_window} 帧"
             f"（占比 {100 * r.unknown_fraction_window:.2f}%）"
             f"｜全录像 unknown {r.unknown_frames_recording} 帧"
             f"（占比 {100 * r.unknown_fraction_recording:.2f}%）")
    occ = ("" if r.occupied_fraction is None
           else f"，在场占比 {r.occupied_fraction:.2f}")
    L.append(f"有效性：{r.validity_status}{occ}")
    if not r.scored:
        L.append("Immobility：**不产出**（被有效性闸门拦截，数值本身是幻影）")
        for m in r.gate_messages:
            L.append(f"  {m}")
    else:
        scorable_s = r.scorable_frames_window / r.fps
        pct_sc = (float("nan") if scorable_s <= 0
                  else 100.0 * r.immobility_mirror_pipeline_s / scorable_s)
        pct_w = 100.0 * r.immobility_mirror_pipeline_s / r.window_seconds
        L.append(f"Immobility（主口径 = 窗口 − Mobility 流水线，与人工/LOVO 镜像同式）"
                 f"= {r.immobility_mirror_pipeline_s:.2f} s ｜ raw "
                 f"{r.immobility_mirror_raw_s:.2f} s ｜ 占窗口 {pct_w:.2f}% ｜ "
                 f"占可评分 {pct_sc:.2f}%")
        L.append("逐类别（窗口内，bout 流水线 = 冻结 CSI 兼容参数；raw = 未平滑逐帧）：")
        for cs in r.categories.values():
            onset = "—" if cs.first_onset_s is None else f"{cs.first_onset_s:.2f} s"
            psc = "—" if cs.pct_of_scorable is None else f"{cs.pct_of_scorable:.2f}%"
            L.append(f"  {cs.name:<13} bouts={cs.bouts:<4} "
                     f"{cs.seconds_pipeline:7.2f} s（raw {cs.seconds_raw:.2f} s）"
                     f" 占窗口 {cs.pct_of_window:6.2f}% 占可评分 {psc:>8} 首动 {onset}")
        L.append("注：主口径与事件口径 Immobility 的差 = unknown + 短事件清除 + "
                 "ForelimbOnly（在动但不计 mobility），两数各答各的问题，禁止混用。")
    for w in r.warnings:
        L.append(f"[!] {w}")
    return "\n".join(L)
