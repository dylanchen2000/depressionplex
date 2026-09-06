"""驱动层：把一段录像跑成 trial 级数字。

在此之前每一块都在（分割、走廊标定、RAD、规则、bout、分母、有效性闸门），
**但没有任何非测试代码把它们串起来**——`cli/probe_frames.py` 只吃 PNG 序列
且停在体检，`trial_report.build_trial_report` 从逐帧标签才开始。缺的就是这一层。

链条（一条直线，没有分支魔法）：

    帧源 → find_chambers（隔间列区间）
         → calibrate_tape_corridor（每隔间：走廊 + 带底 + BL 估计）
         → segment_animal（逐帧逐隔间 → PackedMasks，失败存 None 不补齐）
         → assess_trial_validity（用面积剖面，分母只数分割成功的帧）
         → rules.build_tst_features → rules.label_tst_events（FROZEN 参数）
         → trial_report.build_trial_report（窗口 + 分母 + score_gate）

**分层纪律**：本模块可以调 ffmpeg（经 `video.py`），`assay_core` 不行。
所有"能纯 numpy 测"的逻辑都放在 `analyze_frames` 及其以下，`analyze_video`
只负责把视频变成帧——测试因此不需要视频素材。

**标定是提案不是真值**：`find_chambers` 与 `calibrate_tape_corridor` 的
docstring 都写明了权威来源是人工确认过的 `GeometryEnvelope`。本层原样传递
这个身份：`ChamberPlan.source` 记清每个几何量从哪来，报告里照印。
悬挂点同理——目前由走廊顶端推出，是**提案**，不是标定过的悬挂杆。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np

from . import video
from .assay_core import rad, rules, segment, trial_report, validity
from .maskseq import PackedMasks

#: 标定抽帧数。走廊标定的暗频率法要"动物在动、胶带不动"，帧太少动物扫不开、
#: 太多没必要（`calibrate_tape_corridor` 建议 20–40）。取 24 在建议区间内。
N_CALIB_FRAMES = 24


@dataclass(frozen=True)
class ChamberPlan:
    """一个隔间的几何计划。每个量都带来源，**不许出现来源不明的几何数字**。"""

    index: int                              # 1-based，左起第几个（与切片 chN 同序）
    col_range: tuple[int, int]              # 整帧列区间 [c0, c1]，闭区间
    corridor: segment.TapeCorridor | None
    suspension: tuple[float, float] | None  # (x, y)，隔间 ROI 局部坐标
    source: str                             # 几何量来源，人读

    @property
    def width(self) -> int:
        return self.col_range[1] - self.col_range[0] + 1


@dataclass(frozen=True)
class TrialPlan:
    """整段录像的标定结论 + 有效性判定。"""

    chambers: tuple[ChamberPlan, ...]
    trial_validity: validity.TrialValidity
    calib_indices: tuple[int, ...]
    warnings: tuple[str, ...]


def calibration_indices(n_frames: int, *, n: int = N_CALIB_FRAMES) -> list[int]:
    """全片均匀抽 n 帧的帧号。**均匀抽而不是取开头连续 n 帧**：走廊标定靠
    "动物在多数帧里不在同一列"，开头连续帧里动物几乎不动，标定会把动物当胶带。"""
    if n_frames <= 0:
        raise ValueError(f"帧数必须为正：{n_frames}")
    k = min(n, n_frames)
    return sorted({int(round(i * (n_frames - 1) / max(k - 1, 1))) for i in range(k)})


def _suspension_from_corridor(
    corr: segment.TapeCorridor | None,
) -> tuple[tuple[float, float] | None, str]:
    """悬挂点提案 = 走廊列中心 × 走廊行上界（胶带顶端 ≈ 悬挂杆处）。

    为什么用胶带**顶端**而不是底端：`geometry.suspension_point` 的语义是悬挂杆，
    底端是尾根。两者对 `hind_index`（哪一段离悬挂点近）给出同样的排序，但对
    `centroid_dy` 的绝对值不同；攀爬判据看的是趋势（≥0.3×BL 上移），不吃绝对值。
    取顶端与几何语义图一致，将来接上人工确认的 envelope 时不用换语义。

    **标定不出走廊 ⇒ 返回 None，不猜一个画面中心。**猜出来的悬挂点会让
    "尾侧段"随机取到头侧，`rho_hind` 判据（金标准"仅前肢不计"）直接失效。
    """
    if corr is None:
        return None, "走廊标定失败 ⇒ 悬挂点不可估（不猜）"
    c0, c1 = corr.col_range
    r0, _ = corr.row_range
    return ((c0 + c1) / 2.0, float(r0)), "走廊提案（胶带顶端×列中心；非人工确认几何）"


def build_plan(calib_grays: list[np.ndarray], *, n_chambers: int = 4,
               calib_indices: tuple[int, ...] = (),
               body_area_prior: float | None = None) -> TrialPlan:
    """从标定帧建计划：隔间列区间 + 每隔间走廊 + 悬挂点 + 有效性判定。

    纯 numpy，不碰视频——所以能不带素材单测。
    """
    if len(calib_grays) < 2:
        raise ValueError(
            f"标定帧只有 {len(calib_grays)} 帧：走廊标定的暗频率法至少要 2 帧"
            "（要靠'动物在动、胶带不动'区分两者）")
    cols = segment.find_chambers(calib_grays[0])
    warnings: list[str] = []
    if len(cols) != n_chambers:
        warnings.append(
            f"chamber_count_mismatch：结构立柱只分出 {len(cols)} 个隔间，"
            f"期望 {n_chambers} ——**按实测的 {len(cols)} 个继续**，不凑数不等分切。"
            "等分四列与真实立柱位置不是一回事（DP-042 实测）")

    plans: list[ChamberPlan] = []
    calib_areas: dict[int, list[float | None]] = {}
    for k, (c0, c1) in enumerate(cols, start=1):
        rois = [np.asarray(g)[:, c0:c1 + 1] for g in calib_grays]
        corr = segment.calibrate_tape_corridor(rois)
        susp, susp_src = _suspension_from_corridor(corr)
        src = (f"隔间列=结构立柱提案（find_chambers）"
               f"；走廊={'标定成功' if corr else '标定失败'}"
               f"{'（未收口 band_unsealed）' if corr and not corr.sealed else ''}"
               f"；悬挂点={susp_src}")
        plans.append(ChamberPlan(index=k, col_range=(c0, c1), corridor=corr,
                                 suspension=susp, source=src))
        # 标定期面积剖面：分割失败记 None（不是 0）——DP-032 的分母口径。
        prof: list[float | None] = []
        for roi in rois:
            res = segment.segment_animal(roi, corridor=corr,
                                         bl=(corr.bl_est if corr else None))
            prof.append(float(res.mask.sum()) if res.ok and res.mask is not None
                        else None)
        calib_areas[k] = prof
        if corr is not None and not corr.sealed:
            warnings.append(
                f"chamber{k}_band_unsealed：走廊带底未按动物活动块收口"
                "——带底是扩展值，可能已进收集盒区；此隔间分割大概率整段失败"
                "（DP-032 的实测路径）")

    tv = validity.assess_trial_validity(calib_areas, body_area_prior=body_area_prior)
    return TrialPlan(chambers=tuple(plans), trial_validity=tv,
                     calib_indices=tuple(calib_indices), warnings=tuple(warnings))


def segment_series(frames: Iterable[np.ndarray],
                   plan: TrialPlan) -> dict[int, PackedMasks]:
    """一次顺序扫帧，同时给所有隔间出掩膜序列（位打包，见 maskseq）。

    只扫一遍：每个隔间各扫一遍要解码 N 次，一段 6 min 素材就是 N 倍解码时间。
    """
    seqs: dict[int, PackedMasks] = {}
    for f in frames:
        g = np.asarray(f)
        for ch in plan.chambers:
            c0, c1 = ch.col_range
            roi = g[:, c0:c1 + 1]
            if ch.index not in seqs:
                seqs[ch.index] = PackedMasks(roi.shape)
            res = segment.segment_animal(
                roi, corridor=ch.corridor,
                bl=(ch.corridor.bl_est if ch.corridor else None))
            seqs[ch.index].append(res.mask if res.ok else None)
    return seqs


def analyze_chamber(masks: PackedMasks, ch: ChamberPlan,
                    cv: validity.ChamberValidity | None, *,
                    fps: float, assay: str, trial_id: str,
                    ) -> trial_report.TrialReport:
    """单隔间：掩膜序列 → 特征 → 事件 → trial 报告。

    悬挂点估不出 ⇒ **不产数字**：把它填成画面中心会让 `rho_hind`（尾侧残差
    占比，金标准"仅前肢活动不计"的判据）随机取到头侧，报告看起来正常但判据
    已经失效。这种情况下走与排除态同一条路：不产 immobility，报警说明原因。
    """
    if ch.suspension is None:
        raise ValueError(
            f"{trial_id}：悬挂点不可估（{ch.source}）⇒ 拒绝产出数字。"
            "补救顺序：① 修走廊标定（DP-052）；② 给人工确认的 GeometryEnvelope")
    bl = rad.trial_body_length(masks)
    if bl <= 0:
        bl = float(ch.corridor.bl_est) if (ch.corridor and ch.corridor.bl_est) else 0.0
    feats = rules.build_tst_features(masks, suspension=ch.suspension, fps=fps)
    labels = rules.label_tst_events(feats, bl=(bl if bl > 0 else None))
    return trial_report.build_trial_report(
        labels, fps=fps, assay=assay, trial_id=trial_id, chamber_validity=cv)


def _reports(plan: TrialPlan, seqs: dict[int, PackedMasks], *,
             fps: float, assay: str, prefix: str,
             ) -> tuple[dict[int, trial_report.TrialReport], dict[int, str]]:
    """逐隔间出报告。**一个隔间跑不出来不该让另外三个也没结果**，
    但跳过的原因必须显式带回，不许静默少几行。"""
    by = {cv.chamber: cv for cv in plan.trial_validity.chambers}
    reports: dict[int, trial_report.TrialReport] = {}
    skipped: dict[int, str] = {}
    for ch in plan.chambers:
        masks = seqs.get(ch.index)
        if masks is None or len(masks) == 0:
            skipped[ch.index] = "没有帧（帧源为空）"
            continue
        try:
            reports[ch.index] = analyze_chamber(
                masks, ch, by.get(ch.index), fps=fps, assay=assay,
                trial_id=f"{prefix}-ch{ch.index}")
        except ValueError as e:
            skipped[ch.index] = str(e)
    return reports, skipped


def analyze_frames(calib_grays: list[np.ndarray], frames: Iterable[np.ndarray], *,
                   fps: float, assay: str, trial_prefix: str,
                   n_chambers: int = 4, body_area_prior: float | None = None,
                   ) -> tuple[TrialPlan, dict[int, trial_report.TrialReport],
                              dict[int, str]]:
    """全链（纯 numpy 版）：标定帧 + 全片帧 → (计划, 每隔间报告, 跳过原因)。

    `analyze_video` 只是给它接上 ffmpeg 帧源——所以整条判定链能不带素材单测。
    """
    plan = build_plan(calib_grays, n_chambers=n_chambers,
                      body_area_prior=body_area_prior)
    seqs = segment_series(frames, plan)
    reports, skipped = _reports(plan, seqs, fps=fps, assay=assay,
                                prefix=trial_prefix)
    return plan, reports, skipped


def analyze_video(path: str | Path, *, assay: str, n_chambers: int = 4,
                  trial_prefix: str | None = None,
                  n_calib: int = N_CALIB_FRAMES,
                  body_area_prior: float | None = None,
                  ) -> tuple[video.VideoInfo, TrialPlan,
                             dict[int, trial_report.TrialReport], dict[int, str]]:
    """入口：一段录像 → 每隔间一份 trial 报告。

    fps 与帧数**从文件读**（`video.probe`），不接受调用方传入——传进来的 fps
    与素材不符时，秒↔帧换算会安静地错掉所有时长指标。
    """
    info = video.probe(path)
    idx = calibration_indices(info.n_frames, n=n_calib)
    plan = build_plan(video.frames_at(info, idx), n_chambers=n_chambers,
                      calib_indices=tuple(idx), body_area_prior=body_area_prior)
    seqs = segment_series(video.iter_gray(info), plan)
    reports, skipped = _reports(
        plan, seqs, fps=info.fps, assay=assay,
        prefix=trial_prefix or Path(path).stem)
    return info, plan, reports, skipped
