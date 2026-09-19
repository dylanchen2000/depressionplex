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

#: 短暂丢失门（R2-115 T2）：前后两个相邻 observed 抽样点之间的**源帧跨度**
#: 上限，不是"抽样记录条数"。旧版按记录条数（j-i）判，同是 10 条缺失记录，
#: step=1 是 0.4 s 的物理丢失、step=5 是 2 s，却撞同一个门——分类随抽样
#: 步长漂移。现在门长在源帧号差上：同一物理丢失在 step=1/5/10 分类一致。
#: 起点值 11 源帧（≈0.44 s @ 25 fps，与旧 step=1 的"10 条记录"门等价），
#: **未经真实素材标定**；改它属于研究配置变更，必须随诊断记录落盘。
DEFAULT_LOST_SHORT_MAX_GAP_FRAMES = 11


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
    #: R2-115 G1：水线为 None 或不可靠（无旁证/旁证分歧）时 above_water_frac
    #: 置 null，**原因写在这里**——不许填假 0，也不许无声置 null。
    above_water_null_reason: str | None = None
    wall_dist_px: float | None = None                # 到 ROI 左/右/底的最小距离（顶边是水面上方的观察留白，不是杯壁，不计）


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
                 lost_short_max_gap_frames: int = DEFAULT_LOST_SHORT_MAX_GAP_FRAMES,
                 fps: float | None = None,
                 declared_absent: bool = False,
                 on_observed=None) -> None:
        self.prop = prop
        self.dark = dark
        self.static_dark = static_dark
        self.min_area = min_area
        self.max_area_frac = max_area_frac
        self.rival_frac = rival_frac
        # R2-115 T2：门是**源帧跨度**（实际帧号差），参数与取值写进原因字符串
        self.lost_short_max_gap_frames = lost_short_max_gap_frames
        self.fps = fps                    # 只为把跨度折成秒写进原因；None 就不写秒
        self.declared_absent = declared_absent
        self.on_observed = on_observed
        self._mx: np.ndarray | None = None
        self._static: np.ndarray | None = None
        self._diags: list[FrameDiag] = []
        #: 上一条 observed 记录以来的非 observed 记录条数（跨缺口配对时随
        #: 回调带出，pair_features 记成 gap_records_between，R2-115 T2）
        self._records_since_observed = 0

    def see_background(self, gray: np.ndarray) -> None:
        g = np.asarray(gray, dtype=np.float64)
        self._mx = g if self._mx is None else np.maximum(self._mx, g)

    def _ready(self) -> None:
        if self._mx is None:
            raise RuntimeError("CupDiagnoser 还没喂过标定帧就诊断——背景模型不存在")
        if self._static is None:
            self._static = self._mx < self.static_dark

    def diagnose(self, idx: int, gray: np.ndarray) -> FrameDiag:
        """诊断一帧并记入序列。declared 杯不跑分割（见模块文档）。

        `on_observed` 回调在 observed 帧发，签名 (idx, mask, diag, gap_records)：
        gap_records = 上一条 observed 记录以来的非 observed 记录条数
        （0 = 与上一条 observed 相邻；>0 = 这对帧跨了观测缺口，配对特征
        必须单列，不进连续统计，R2-115 T2）。
        """
        if self.declared_absent:
            d = FrameDiag(idx, QUALITY_DECLARED_ABSENT,
                          ("人工申报空杯：结果为空，不输出 0 秒",))
            self._diags.append(d)
            return d
        self._ready()
        assert self._static is not None
        r0, c0, r1, c1 = self.prop.roi
        g = np.asarray(gray, dtype=np.float64)
        d = self._diagnose_one(idx, g, self._mx, self._static,
                               (r0, c0, r1, c1))
        self._diags.append(d)
        # observed 的定性不会被后面的缺失归并改写 ⇒ 计数在流式过程中就是终值
        if d.quality == QUALITY_OBSERVED:
            self._records_since_observed = 0
        else:
            self._records_since_observed += 1
        return d

    def _diagnose_one(self, idx, g, mx, static, box) -> FrameDiag:
        r0, c0, r1, c1 = box
        roi = g[r0:r1 + 1, c0:c1 + 1]
        if roi.size == 0:
            return FrameDiag(idx, QUALITY_UNCLEAR, ("分析 ROI 为空",))
        # 对比度检查看**水体区**（有派生水体时）：ROI 含线上留白，
        # 留白处可能是暗的架子/阴影，中值会被拉低造成假"对比不足"。
        # 人工确认件没有 water_body（None）⇒ 退回整个 ROI。
        b = self.prop.water_body if self.prop.water_body is not None else box
        bright_med = float(np.median(mx[b[0]:b[2] + 1, b[1]:b[3] + 1]))
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
        # 面积占比的分母用**水体区**（有派生水体时），与旧行为一致：
        # 反光/波纹连片发生在水里，用含线上留白的 ROI 当分母会稀释灵敏度。
        b = self.prop.water_body if self.prop.water_body is not None else box
        interior_area = max(1, (b[2] - b[0] + 1) * (b[3] - b[1] + 1))
        if big.area > self.max_area_frac * interior_area:
            reasons.append(f"候选面积 {big.area} 超过水体区的 {self.max_area_frac:.0%}"
                           "（更可能是水面反光/波纹连片，不是动物）")
        rivals = [c for c in comps[1:] if c.area >= self.rival_frac * big.area]
        if rivals:
            reasons.append(f"存在 {len(rivals)} 个面积≥{self.rival_frac:.0%}×最大的竞争连通域"
                           "（分不清哪个是动物）")
        br0, bc0, br1, bc1 = big.bbox
        if br0 <= r0 or br1 >= r1 or bc0 <= c0 or bc1 >= c1:
            reasons.append("候选剪影贴着分析 ROI 边界（被截断，几何量不可信）")
        if reasons:
            return FrameDiag(idx, QUALITY_UNCLEAR, tuple(reasons),
                             area_px=big.area, centroid=_centroid(big),
                             bbox=big.bbox)
        # R2-115 G1：水线特征只有在水线**可靠**时才算。水线缺失或不可靠 ⇒
        # above_water_frac=None + 原因（不填假 0，不无声置 null）。
        above_frac = None
        null_reason = None
        if self.prop.water_surface_y is None:
            null_reason = "水线为 None（提不出/确认件没画）：above_water_frac 不计算，不填 0"
        elif not self.prop.water_surface_reliable:
            null_reason = (
                "水线不可靠（"
                + (self.prop.water_surface_unreliable_reason or "未经旁证")
                + "）：above_water_frac 不计算、不填 0，等人工确认")
        else:
            above, below = sil.above_below(big.mask, self.prop.water_surface_y)
            tot = int(above.sum()) + int(below.sum())
            above_frac = float(above.sum()) / tot if tot else None
            if above_frac is None:
                null_reason = "剪影像素数为 0（不应发生）：above_water_frac 不可算"
        # 触壁距离只量左/右/底：ROI 顶边是水面之上的观察留白（G1），
        # 不是物理杯壁——动物贴着顶边是"离开观察区"，由上面的截断检查记 unclear。
        wall = min(bc0 - c0, c1 - bc1, r1 - br1)
        diag = FrameDiag(idx, QUALITY_OBSERVED, (),
                         area_px=big.area, centroid=_centroid(big),
                         bbox=big.bbox, above_water_frac=above_frac,
                         above_water_null_reason=null_reason,
                         wall_dist_px=float(wall))
        if self.on_observed is not None:
            # observed 的定性不会被后面的缺失归并改写，回调在这里发是稳的；
            # 掩膜只交给回调、本类不留——整段视频的掩膜序列内存上不可接受。
            # 第 4 参 = 距上一条 observed 的非 observed 记录条数（跨缺口配对标位）。
            self.on_observed(idx, big.mask, diag, self._records_since_observed)
        return diag

    def last(self) -> FrameDiag | None:
        """最近一帧的诊断（渲染叠加图用）。还没诊断过 ⇒ None。"""
        return self._diags[-1] if self._diags else None

    def finish(self) -> list[FrameDiag]:
        if self.declared_absent:
            return list(self._diags)
        return _resolve_pending(self._diags, self.lost_short_max_gap_frames,
                                fps=self.fps)


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
    """水体区里的恒暗结构中有没有动物尺寸的紧连通域（静态动物吸收陷阱）。

    查**水体区**而不是含线上留白的整个 ROI：真实素材里留白处有挂钩/架子
    这类细暗静态结构（形状和大小都像"静态动物"），拿它查会把每个无候选帧
    都冤枉成吸收陷阱。整段不动的动物只会在水里。确认件没有 water_body
    ⇒ 退回 ROI（人工确认时留白本来就画得干净）。
    """
    r0, c0, r1, c1 = prop.water_body if prop.water_body is not None else prop.roi
    sub = static_mask[r0:r1 + 1, c0:c1 + 1]
    if not sub.any():
        return False
    comps = seg.label_components(sub, min_area=min_area)
    return any(min_area <= c.area <= 0.55 * sub.size
               and c.height <= 0.8 * sub.shape[0] and c.width <= 0.8 * sub.shape[1]
               for c in comps)


def _resolve_pending(diags: list[FrameDiag], max_gap_frames: int,
                     *, fps: float | None = None) -> list[FrameDiag]:
    """把过渡态定成 lost_short / unclear。门长在**源帧跨度**上（R2-115 T2）。

    跨度 = 缺失段前后两个相邻 observed 抽样点的实际帧号差；跨度
    ≤ max_gap_frames 源帧 ⇒ lost_short（分割抖了一下，动物显然还在），
    其余（跨度超限、贴着序列两端算不出跨度）⇒ unclear，原因写明
    "未申报空杯，不作空杯处理"。旧版按记录条数（j-i）判：同是 10 条
    缺失，step=1 是 0.4 s、step=5 是 2 s 却撞同一个门——分类随抽样步长
    漂移。参数名与取值写进原因字符串（参数出处可追溯）。
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
        if before_ok and after_ok:
            span = out[j].frame - out[i - 1].frame      # 源帧跨度（实际帧号差）
            span_txt = f"源帧跨度 {span}" + (f" ≈{span / fps:.2f} s" if fps else "")
            short = span <= max_gap_frames
        else:
            span_txt = "源帧跨度不可算（缺失贴序列端点）"
            short = False
        gate_txt = f"门 lost_short_max_gap_frames={max_gap_frames} 源帧"
        if short:
            state = QUALITY_LOST_SHORT
            why = (f"连续 {run} 条抽样记录无候选，前后观测点之间{span_txt}，{gate_txt}"
                   "（短暂分割失败，不等于空杯）",)
        else:
            state = QUALITY_UNCLEAR
            why = (f"连续 {run} 条抽样记录无候选且无法归为短暂丢失"
                   f"（{span_txt}，{gate_txt}）"
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
                               above_water_null_reason=old.above_water_null_reason,
                               wall_dist_px=old.wall_dist_px)
        i = j
    return out


def touch_wall(diag: FrameDiag, *, touch_px: float = 2.0) -> bool | None:
    """触壁候选。观测无效 ⇒ None（不填 False：False 是"看清了没触壁"）。"""
    if diag.quality != QUALITY_OBSERVED or diag.wall_dist_px is None:
        return None
    return diag.wall_dist_px <= touch_px
