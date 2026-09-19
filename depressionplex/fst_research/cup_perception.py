"""每杯逐帧分割 + 可见性诊断：把「看得见 / 看不清 / 短暂丢失 / 申报空杯」分成四件。

Spec A §6.2 的那张表在这里落成四个互斥状态，**每帧恰好一个**，
所以一帧不漏地加起来必须等于分析帧数（分区闭合，见 record.check_partition）：

| 状态 | 含义 | 出不出特征 |
|---|---|---|
| `observed` | 动物存在且观测有效 | 出 |
| `unclear` | 看不清/有冲突 | 不出，记原因 |
| `lost_short` | 短暂分割失败（前后都看得见） | 不出，记原因 |
| `declared_absent` | **人工申报**的空杯 | 不出；结果为空，**不输出 0** |

三条不许（都真犯过或差点犯）：

- **短暂丢失 ≠ 空杯**。空杯只认人工申报（`declared_absent=True`），
  连续多少帧找不到动物都**不**自动变成空杯——长缺失记 `unclear`，
  原因写 `long_absence_unexplained`，让读报告的人自己决定要不要去现场看。
- **静态动物不许被背景吸收**。背景模型是标定样本的逐像素 max；
  若动物在整段标定样本里一动不动，它的像素在 max 图里也是暗的，
  会被当成"杯壁一类的静态结构"而整段消失。所以每帧额外检查：
  静态暗掩膜在杯内区里有没有动物尺寸的紧连通域——有就报
  `possible_static_animal_absorbed`（unclear），**绝不**报空杯。
- **已确认无动物 ⇒ 结果为空**，不是 0 秒、也不是整窗不动（Spec A §5.1 S16）。
  这个语义在 record 层落：declared 杯的诊断记录里没有行为秒数这一项。

**流式与整段两种喂法共用一套状态机**：CLI 逐帧流式喂（整段视频的掩膜/灰度
进不了内存），测试整段喂（`diagnose_cup` 是它的便利包装）。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..assay_core import segment as seg
from ..assay_core import silhouette as sil
from .cup_geometry import CupProposal

QUALITY_OBSERVED = "observed"
QUALITY_UNCLEAR = "unclear"
QUALITY_LOST_SHORT = "lost_short"
QUALITY_DECLARED_ABSENT = "declared_absent"

QUALITIES: tuple[str, ...] = (QUALITY_OBSERVED, QUALITY_UNCLEAR,
                              QUALITY_LOST_SHORT, QUALITY_DECLARED_ABSENT)

#: 内部过渡态：本帧没找到候选，等前后文再定是 lost_short 还是 unclear。
_PENDING = "_no_candidate_pending"


@dataclass(frozen=True)
class FrameDiag:
    """一帧一杯的诊断。特征是**研究候选**，不是类别标签。"""

    frame: int
    quality: str
    reasons: tuple[str, ...]
    area_px: int | None = None
    centroid: tuple[float, float] | None = None     # (x, y) 全帧坐标
    bbox: tuple[int, int, int, int] | None = None    # (r0, c0, r1, c1)
    above_water_frac: float | None = None            # 动物面积在水线上方的比例
    wall_dist_px: float | None = None                # 到杯内区四壁的最小距离


class CupDiagnoser:
    """一个杯子的逐帧状态机。先 `see_background` 喂标定帧，再逐帧 `diagnose`。

    背景模型 = 标定帧的逐像素 max（动物到过的地方是亮的）。
    `finish()` 做缺失归并（lost_short / unclear），返回最终诊断序列。
    """

    def __init__(self, prop: CupProposal, *,
                 dark: float = 96.0,
                 static_dark: float = 64.0,
                 min_area: int = 40,
                 max_area_frac: float = 0.55,
                 rival_frac: float = 0.4,
                 lost_short_max_frames: int = 10,
                 declared_absent: bool = False,
                 on_observed=None) -> None:
        self.prop = prop
        self.dark = dark
        self.static_dark = static_dark
        self.min_area = min_area
        self.max_area_frac = max_area_frac
        self.rival_frac = rival_frac
        self.lost_short_max_frames = lost_short_max_frames
        self.declared_absent = declared_absent
        self.on_observed = on_observed
        self._mx: np.ndarray | None = None
        self._static: np.ndarray | None = None
        self._diags: list[FrameDiag] = []

    def see_background(self, gray: np.ndarray) -> None:
        g = np.asarray(gray, dtype=np.float64)
        self._mx = g if self._mx is None else np.maximum(self._mx, g)

    def _ready(self) -> None:
        if self._mx is None:
            raise RuntimeError("CupDiagnoser 还没喂过标定帧就诊断——背景模型不存在")
        if self._static is None:
            self._static = self._mx < self.static_dark

    def diagnose(self, idx: int, gray: np.ndarray) -> FrameDiag:
        """诊断一帧并记入序列。declared 杯不跑分割（见模块文档）。"""
        if self.declared_absent:
            d = FrameDiag(idx, QUALITY_DECLARED_ABSENT,
                          ("人工申报空杯：结果为空，不输出 0 秒",))
            self._diags.append(d)
            return d
        self._ready()
        assert self._static is not None
        r0, c0, r1, c1 = self.prop.interior
        g = np.asarray(gray, dtype=np.float64)
        d = self._diagnose_one(idx, g, self._mx, self._static,
                               (r0, c0, r1, c1))
        self._diags.append(d)
        return d

    def _diagnose_one(self, idx, g, mx, static, box) -> FrameDiag:
        r0, c0, r1, c1 = box
        roi = g[r0:r1 + 1, c0:c1 + 1]
        if roi.size == 0:
            return FrameDiag(idx, QUALITY_UNCLEAR, ("杯内区为空",))
        bright_med = float(np.median(mx[r0:r1 + 1, c0:c1 + 1]))
        if bright_med < self.dark + 20:
            return FrameDiag(
                idx, QUALITY_UNCLEAR,
                (f"背景亮度中值 {bright_med:.0f} 与暗阈 {self.dark:.0f} 对比不足"
                 "（采集对比度问题，不是行为问题）",))
        fg = np.zeros_like(g, dtype=bool)
        fg[r0:r1 + 1, c0:c1 + 1] = (roi < self.dark) & ~static[r0:r1 + 1, c0:c1 + 1]
        comps = seg.label_components(fg, min_area=self.min_area)
        if not comps:
            if _static_animal_blob(static, self.prop, self.min_area):
                return FrameDiag(
                    idx, _PENDING,
                    ("possible_static_animal_absorbed：静态暗掩膜里有动物尺寸的连通域，"
                     "动物可能整段没动而被背景模型吸收——不作空杯处理",))
            return FrameDiag(idx, _PENDING, ("本帧无动物候选",))
        big = comps[0]
        reasons: list[str] = []
        interior_area = max(1, (r1 - r0 + 1) * (c1 - c0 + 1))
        if big.area > self.max_area_frac * interior_area:
            reasons.append(f"候选面积 {big.area} 超过杯内区的 {self.max_area_frac:.0%}"
                           "（更可能是水面反光/波纹连片，不是动物）")
        rivals = [c for c in comps[1:] if c.area >= self.rival_frac * big.area]
        if rivals:
            reasons.append(f"存在 {len(rivals)} 个面积≥{self.rival_frac:.0%}×最大的竞争连通域"
                           "（分不清哪个是动物）")
        br0, bc0, br1, bc1 = big.bbox
        if br0 <= r0 or br1 >= r1 or bc0 <= c0 or bc1 >= c1:
            reasons.append("候选剪影贴着杯内区边界（被 ROI 截断，几何量不可信）")
        if reasons:
            return FrameDiag(idx, QUALITY_UNCLEAR, tuple(reasons),
                             area_px=big.area, centroid=_centroid(big),
                             bbox=big.bbox)
        above_frac = None
        if self.prop.water_surface_y is not None:
            above, below = sil.above_below(big.mask, self.prop.water_surface_y)
            tot = int(above.sum()) + int(below.sum())
            above_frac = float(above.sum()) / tot if tot else None
        wall = min(br0 - r0, r1 - br1, bc0 - c0, c1 - bc1)
        diag = FrameDiag(idx, QUALITY_OBSERVED, (),
                         area_px=big.area, centroid=_centroid(big),
                         bbox=big.bbox, above_water_frac=above_frac,
                         wall_dist_px=float(wall))
        if self.on_observed is not None:
            # observed 的定性不会被后面的缺失归并改写，回调在这里发是稳的；
            # 掩膜只交给回调、本类不留——整段视频的掩膜序列内存上不可接受。
            self.on_observed(idx, big.mask, diag)
        return diag

    def last(self) -> FrameDiag | None:
        """最近一帧的诊断（渲染叠加图用）。还没诊断过 ⇒ None。"""
        return self._diags[-1] if self._diags else None

    def finish(self) -> list[FrameDiag]:
        if self.declared_absent:
            return list(self._diags)
        return _resolve_pending(self._diags, self.lost_short_max_frames)


def diagnose_cup(frames, indices, prop: CupProposal, **kw) -> list[FrameDiag]:
    """整段喂法的便利包装：先全部帧建背景，再逐帧诊断。测试与离线小素材用。"""
    if len(frames) != len(indices):
        raise ValueError("frames 与 indices 数目不符")
    diag = CupDiagnoser(prop, **kw)
    for f in frames:
        diag.see_background(f)
    for f, i in zip(frames, indices):
        diag.diagnose(i, f)
    return diag.finish()


def _centroid(comp) -> tuple[float, float]:
    m = sil.metrics(comp.mask, with_holes=False)
    return m.centroid if m is not None else (float("nan"), float("nan"))


def _static_animal_blob(static_mask: np.ndarray, prop: CupProposal,
                        min_area: int) -> bool:
    """杯内区里的恒暗结构中有没有动物尺寸的紧连通域（静态动物吸收陷阱）。"""
    r0, c0, r1, c1 = prop.interior
    sub = static_mask[r0:r1 + 1, c0:c1 + 1]
    if not sub.any():
        return False
    comps = seg.label_components(sub, min_area=min_area)
    return any(min_area <= c.area <= 0.55 * sub.size
               and c.height <= 0.8 * sub.shape[0] and c.width <= 0.8 * sub.shape[1]
               for c in comps)


def _resolve_pending(diags: list[FrameDiag], max_run: int) -> list[FrameDiag]:
    """把过渡态定成 lost_short / unclear。

    前后都是 observed 的短缺失（≤ max_run 帧）⇒ lost_short：分割抖了一下，
    动物显然还在。其余（长缺失、贴着序列两端的缺失）⇒ unclear，
    原因写明"未申报空杯，不作空杯处理"。
    """
    out = list(diags)
    n = len(out)
    i = 0
    while i < n:
        if out[i].quality != _PENDING:
            i += 1
            continue
        j = i
        while j < n and out[j].quality == _PENDING:
            j += 1
        run = j - i
        before_ok = i > 0 and out[i - 1].quality == QUALITY_OBSERVED
        after_ok = j < n and out[j].quality == QUALITY_OBSERVED
        if before_ok and after_ok and run <= max_run:
            state, why = QUALITY_LOST_SHORT, (f"连续 {run} 帧无候选，前后均可见"
                                              "（短暂分割失败，不等于空杯）",)
        else:
            state = QUALITY_UNCLEAR
            why = (f"连续 {run} 帧无候选且无法归为短暂丢失"
                   "（long_absence_unexplained：未申报空杯，不作空杯处理）",)
        for k in range(i, j):
            old = out[k]
            static = bool(old.reasons) and old.reasons[0].startswith(
                "possible_static_animal_absorbed")
            if static:
                # 静态动物吸收是**整杯背景模型**的问题，不是某一帧抖了一下：
                # 哪怕前后都看得见，这些帧也记 unclear，等人工看叠加图定。
                state_k = QUALITY_UNCLEAR
                why_k = old.reasons + why
            else:
                state_k = state
                why_k = tuple(r for r in old.reasons if r != "本帧无动物候选") + why
            out[k] = FrameDiag(old.frame, state_k, why_k,
                               area_px=old.area_px, centroid=old.centroid,
                               bbox=old.bbox, above_water_frac=old.above_water_frac,
                               wall_dist_px=old.wall_dist_px)
        i = j
    return out


def touch_wall(diag: FrameDiag, *, touch_px: float = 2.0) -> bool | None:
    """触壁候选。观测无效 ⇒ None（不填 False：False 是"看清了没触壁"）。"""
    if diag.quality != QUALITY_OBSERVED or diag.wall_dist_px is None:
        return None
    return diag.wall_dist_px <= touch_px
