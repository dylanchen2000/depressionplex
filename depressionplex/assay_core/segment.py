"""S1 前景分割：背光 + 阈值。纯 numpy，不依赖 OpenCV。

这是感知层的**默认主力**，不是兜底。理由见 README：CSI 剪影干净的根因在采集端
（背光板），不在算法。对比度做足时阈值法零标注、可审计、且**不引入模型抖动**——
而模型抖动正是本方案的头号风险。学习式分割（S2）只在 S1 实测不达标处才启用。

TST 场景的几何事实（实测自真实素材）：
- 背光面板是一条明亮的行带，动物是其中的暗连通域
- 悬挂胶带也是暗的，但它**细、竖直、且从面板带顶部延伸下来**，据此可判别
- 底部收集盒边缘也是暗的，靠"不取触底组件"或亮带范围排除
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, eq=False)
class Component:
    """一个连通域。"""

    mask: np.ndarray
    area: int
    bbox: tuple[int, int, int, int]  # (r0, c0, r1, c1) 闭区间

    @property
    def height(self) -> int:
        return self.bbox[2] - self.bbox[0] + 1

    @property
    def width(self) -> int:
        return self.bbox[3] - self.bbox[1] + 1


def otsu_threshold(gray: np.ndarray, bins: int = 256) -> float:
    """Otsu 阈值。背光素材直方图强双峰，Otsu 会落在两峰之间的深谷里，非常稳。"""
    g = np.asarray(gray, dtype=np.float64).ravel()
    hist, edges = np.histogram(g, bins=bins, range=(0.0, 256.0))
    p = hist.astype(np.float64)
    total = p.sum()
    if total <= 0:
        return 128.0
    p /= total
    centers = (edges[:-1] + edges[1:]) / 2.0
    w0 = np.cumsum(p)
    m0 = np.cumsum(p * centers)
    m_tot = m0[-1]
    with np.errstate(invalid="ignore", divide="ignore"):
        between = (m_tot * w0 - m0) ** 2 / (w0 * (1.0 - w0))
    between[~np.isfinite(between)] = -1.0
    return float(centers[int(np.argmax(between))])


def label_components(mask: np.ndarray, *, min_area: int = 1) -> list[Component]:
    """4-连通标记。按面积降序返回。"""
    m = np.asarray(mask, dtype=bool)
    todo = m.copy()
    h, w = m.shape
    out: list[Component] = []
    while todo.any():
        r0, c0 = map(int, np.argwhere(todo)[0])
        comp = np.zeros_like(m)
        dq = deque([(r0, c0)])
        todo[r0, c0] = False
        comp[r0, c0] = True
        rmin = rmax = r0
        cmin = cmax = c0
        while dq:
            r, c = dq.popleft()
            for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nr, nc = r + dr, c + dc
                if 0 <= nr < h and 0 <= nc < w and todo[nr, nc]:
                    todo[nr, nc] = False
                    comp[nr, nc] = True
                    dq.append((nr, nc))
                    rmin = min(rmin, nr)
                    rmax = max(rmax, nr)
                    cmin = min(cmin, nc)
                    cmax = max(cmax, nc)
        area = int(comp.sum())
        if area >= min_area:
            out.append(Component(comp, area, (rmin, cmin, rmax, cmax)))
    out.sort(key=lambda c: c.area, reverse=True)
    return out


def panel_band(
    gray: np.ndarray,
    *,
    bright_frac: float = 0.85,
    fallbacks: tuple[float, ...] = (0.70, 0.60, 0.50),
) -> tuple[int, int] | None:
    """定位背光面板的行带 [r0, r1]。判据：该行亮像素占比 > bright_frac。

    分级回退而非静默兜底：若给定占比无一行达标，依次降到 fallbacks 里的值；
    全部失败则返回 **None**。

    为什么必须显式失败：早先版本在无行达标时直接 `return 0, h-1`（整幅高度），
    结果整图调用时把顶/底黑框都框进来，下游挑到了收集盒而不是动物——
    而收集盒是静态的，会让"面积抖动"这道门假通过。静默兜底比报错危险得多。

    经验取值：单隔间 ROI 用 0.85（能切掉底部收集盒行带）；
    整图用 0.60（整图有隔间分隔带与左右黑边，亮占比封顶约 0.82）。
    """
    g = np.asarray(gray, dtype=np.float64)
    thr = otsu_threshold(g)
    frac = (g > thr).mean(axis=1)
    for f in (bright_frac, *fallbacks):
        run = _longest_true_run(frac > f)
        if run is not None:
            return run
    return None


def _longest_true_run(flags: np.ndarray) -> tuple[int, int] | None:
    """最长连续 True 区段 [i0, i1]。

    必须取连续区段，**不能取达标行的 min..max**：底部收集盒下方往往还有一行
    纯面板达标，min..max 会把中间那段不达标的盒区一起夹进来，
    于是下游又挑到了收集盒。2026-08-24 实测踩过这个坑。
    """
    best: tuple[int, int] | None = None
    best_len = 0
    start: int | None = None
    n = len(flags)
    for i in range(n + 1):
        v = bool(flags[i]) if i < n else False
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start > best_len:
                best_len = i - start
                best = (start, i - 1)
            start = None
    return best


@dataclass(frozen=True)
class SegResult:
    """分割结果 + QC 标记。**刻意不返回裸掩膜**。

    分割失败若静默返回空掩膜，下游会把它当成"动物没动"，等于把失败伪装成
    immobility。所以失败必须显式（mask=None + reason），由上层记 `unknown`。
    """

    mask: np.ndarray | None
    reason: str = ""
    flags: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.mask is not None


def _touches(comp: Component, shape: tuple[int, int], *, top: bool = False) -> bool:
    r0, c0, r1, c1 = comp.bbox
    h, w = shape
    hit = c0 <= 0 or c1 >= w - 1 or r1 >= h - 1
    if top:
        hit = hit or r0 <= 0
    return hit


def _row_widths(comp: Component) -> np.ndarray:
    """组件在每一行的水平跨度（右端 - 左端 + 1），仅统计有像素的行。"""
    m = comp.mask
    rows = np.flatnonzero(m.any(axis=1))
    out = []
    for r in rows:
        cc = np.flatnonzero(m[r])
        out.append(cc[-1] - cc[0] + 1)
    return np.asarray(out, dtype=float)


def segment_animal(
    gray: np.ndarray,
    *,
    thresh: float | None = None,
    band: tuple[int, int] | None = None,
    tape_width_frac: float = 0.20,
    min_area: int = 20,
    max_area_frac: float = 0.35,
) -> SegResult:
    """从单个隔间的灰度 ROI 里分出动物剪影。

    实测得到的三条判别规则（缺一不可，2026-08-24 在真实素材上验证）：

    1. **不取触及 ROI 左/右/下边界的连通域。** 箱体黑框与底部收集盒都触边，
       动物悬在面板内部不触边。少了这条会挑到收集盒——而收集盒是静态的，
       会让"面积抖动"这道门**假通过**（实测残差 0.0001，完全没动）。
    2. **取最低的候选**（bbox 底行最大）：动物悬在胶带下端，胶带在其上方。
       比"排除细的触顶组件再取最大面积"稳得多——后者会把"胶带结头+尾巴+躯干"
       连成的整体误判成胶带而丢弃整个隔间。触顶者打 `tape_attached` 标记。
    3. 面积须落在 [min_area, max_area_frac × ROI 面积] 内，防止挑到整块背景或噪点。

    若最终选中的组件触顶且很宽，说明动物与胶带在掩膜里连成一体，
    打 `tape_attached` 标记——此时 BL 与面积都不可信，需上层处理。
    """
    g = np.asarray(gray, dtype=np.float64)
    thr = otsu_threshold(g) if thresh is None else thresh
    if band is None:
        found = panel_band(g)
        if found is None:
            return SegResult(None, "未检出背光面板行带")
        r0, r1 = found
    else:
        r0, r1 = band
    if r1 - r0 < 10:
        return SegResult(None, f"面板带过窄（{r0}-{r1}）")

    sub = g[r0 : r1 + 1, :]
    dark = sub < thr
    comps = label_components(dark, min_area=min_area)
    if not comps:
        return SegResult(None, "面板带内无暗连通域")

    max_tape_w = max(2, int(round(tape_width_frac * g.shape[1])))
    area_cap = max_area_frac * sub.size

    interior = [c for c in comps if not _touches(c, sub.shape)]
    if not interior:
        return SegResult(None, "所有暗连通域都触及边界（疑似只框到箱体/收集盒）")

    cands = [c for c in interior if min_area <= c.area <= area_cap]
    if not cands:
        return SegResult(None, "无面积合规的内部连通域")

    # 动物**悬在胶带下端**，所以取最低（bbox 底行最大）的候选；同底则取面积大者。
    # 早先按"先排除细的触顶组件、再取最大面积"来做，在真实素材上会把
    # "胶带结头 + 尾巴 + 躯干"连成的整体误判为胶带而整段丢弃（隔间 2 全失败），
    # 因为纯胶带的上端有时是较宽的结头，"宽度沿高度恒定"这条判据并不成立。
    pick = max(cands, key=lambda c: (c.bbox[2], c.area))

    flags: list[str] = []
    if pick.bbox[0] <= 1:
        # 触顶 = 动物与悬挂胶带在掩膜里连成一体。此时 BL 与面积都被胶带拉长，
        # 但**不丢弃**——打标记交人工复核，比静默丢掉一整个隔间好。
        # 曾试过"再判一次是否纯胶带、是则丢弃"，结果把真实动物误杀（隔间2 全失败），
        # 因为纯胶带上端有时是较宽的结头，"宽度沿高度恒定"这条判据在真实素材上不成立。
        flags.append("tape_attached")
    if pick.width <= max_tape_w:
        flags.append("narrow_as_tape")

    out = np.zeros_like(g, dtype=bool)
    out[r0 : r1 + 1, :] = pick.mask
    return SegResult(out, "", tuple(flags))


def find_chambers(
    gray: np.ndarray,
    *,
    min_width: int = 20,
    divider_dark_frac: float = 0.90,
    band_bright_frac: float = 0.60,
) -> list[tuple[int, int]]:
    """按结构立柱定位隔间，返回 [(c0, c1), ...]。

    判据（实测自真实素材，2026-08-24）：在背光面板行带内逐列统计"暗行占比"——
    - **结构立柱 / 箱体黑边：占比 = 1.00**（整条高度都是暗的）
    - **悬挂胶带：占比 0.32–0.67**（只从顶部延伸到动物处，下方是亮的）

    这两者宽度相近（都约 6–7 px），**靠宽度分不开，必须靠暗行占比**。
    早先版本按"亮列段 + 贪心合并到期望数量"来做，结果随 panel_band 参数变化
    就把两个隔间并成一个——那条路不可靠，已废弃。

    注意：这只是**标定提案**。正式分析的权威来源是人工确认过的
    GeometryEnvelope（geometry.py），不是每帧自动检测。
    """
    g = np.asarray(gray, dtype=np.float64)
    found = panel_band(g, bright_frac=band_bright_frac)
    if found is None:
        return []
    r0, r1 = found
    band = g[r0 : r1 + 1, :]
    thr = otsu_threshold(g)
    dark_frac = (band < thr).mean(axis=0)
    is_divider = dark_frac > divider_dark_frac

    chambers: list[tuple[int, int]] = []
    start: int | None = None
    for i, div in enumerate(is_divider):
        if not div and start is None:
            start = i
        elif div and start is not None:
            if i - start >= min_width:
                chambers.append((start, i - 1))
            start = None
    if start is not None and len(is_divider) - start >= min_width:
        chambers.append((start, len(is_divider) - 1))
    return chambers


def contrast_report(gray: np.ndarray) -> dict[str, float]:
    """采集端验收：动物/背景灰度对比度。

    验收门（对标 CSI 实测）：绝对差 ≥ 100 灰阶、比值 ≥ 2×。CSI 参考素材 110 / 2.27×。

    **暗侧只取动物掩膜内的像素**，不是"面板带内所有暗像素"——后者会把箱体黑框
    与抗锯齿过渡带算进来，把对比度显著低估（实测 166/3.27x vs 真值 238/20.9x）。
    """
    g = np.asarray(gray, dtype=np.float64)
    found = panel_band(g)
    if found is None:
        return {"passes_gate": 0.0, "reason_no_band": 1.0}
    r0, r1 = found
    band = g[r0 : r1 + 1, :]
    thr = otsu_threshold(g)
    bright = band[band > thr]
    if bright.size == 0:
        return {"passes_gate": 0.0, "reason_no_bright": 1.0}

    seg = segment_animal(g)
    if seg.ok and seg.mask is not None:
        animal_px = g[seg.mask]
        source = 1.0  # 1 = 动物掩膜
    else:
        animal_px = band[band < thr]
        source = 0.0  # 0 = 回退到全部暗像素，数值会偏保守
    if animal_px.size == 0:
        return {"passes_gate": 0.0, "reason_no_dark": 1.0}

    b = float(bright.mean())
    d = float(animal_px.mean())
    return {
        "threshold": thr,
        "background_mean": b,
        "animal_mean": d,
        "abs_diff": b - d,
        "ratio": b / max(d, 1e-9),
        "dark_from_mask": source,
        "passes_gate": float((b - d) >= 100.0 and (b / max(d, 1e-9)) >= 2.0),
    }


def structural_noise_floor(
    grays: list[np.ndarray], *, thresh: float | None = None
) -> dict[str, float]:
    """用画面里的**静态高对比结构**估计分割噪声底。

    为什么必须单独测这个：「轮廓面积逐帧抖动」这道门本意是量**分割噪声**，
    但在真实素材上直接量动物，测到的是「分割噪声 + 动物真实形变」的混合物——
    动物在动的时候这道门必然"不通过"，而那是信号不是噪声。
    2026-08-24 实测踩过这个坑：t=24 s 的帧里小鼠正在剧烈挣扎（RAD 残差达合成
    噪声底的 4.5–9.7 倍），面积抖动自然超门槛。

    另一个实测结论：**噪声底强烈依赖边缘对比度，不是全局常数**。同一批帧里
    顶部黑框（黑 vs 亮面板，高对比）的面积抖动是 0.00 px，而半透明玻璃收集盒
    （低对比软边）是 6.14 px。动物是黑剪影对亮背光，属于前者，所以应当用
    高对比结构做代理，用收集盒会把噪声底高估近一个数量级。

    取样区域 = 背光面板行带**以上**的全宽区域（箱体顶框），那里没有动物。
    """
    if len(grays) < 2:
        return {"n_frames": float(len(grays))}
    g0 = np.asarray(grays[0], dtype=np.float64)
    thr = otsu_threshold(g0) if thresh is None else thresh
    found = panel_band(g0, bright_frac=0.60)
    if found is None or found[0] < 3:
        return {"reason_no_region": 1.0}
    r_top = found[0]

    areas = np.array(
        [float((np.asarray(g, dtype=np.float64)[0:r_top, :] < thr).sum()) for g in grays]
    )
    d = np.abs(np.diff(areas))
    return {
        "region_rows": float(r_top),
        "area_mean": float(areas.mean()),
        "delta_mean": float(d.mean()),
        "delta_max": float(d.max()),
    }
