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

from . import silhouette as sil


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
    corridor: "TapeCorridor | None" = None,
    tape_width_frac: float = 0.10,
    min_area: int = 20,
    max_area_frac: float = 0.35,
    body_run_min_px: int = 3,
    climb_frac_thresh: float = 0.10,
) -> SegResult:
    """从单个隔间的灰度 ROI 里分出动物剪影。

    **无走廊路径**（`corridor=None`，默认；行为与历史版本逐位一致）：

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

    **走廊路径**（给了 `corridor`）：

    相机固定 ⇒ 胶带列区间是常量。用标定好的走廊把"胶带在哪"从每帧推断变成
    已知量：在胶带底端（`row_range[1] + 1`）处按行截断，胶带被整体切掉，
    截断面以下取面积最大的内部连通域 = 动物。动物剧烈挣扎、与胶带频繁接触时
    不再依赖形状启发式，这是本路径存在的理由（见 SPEC 胶带走廊标定 v1）。

    - 走廊结构非法（列/行区间越界等）→ 退回无走廊路径并打 `corridor_unused`。
    - 截断面以下找不到动物 → 回搜胶带区是否有攀爬（见下），也没有则显式失败。
    - 动物**攀爬**（抓尾/抓胶带上移）是真实行为，不是噪声：回搜命中时返回
      该动物并打 `animal_in_corridor`，供事件层使用；正常悬挂但剪影与走廊
      重叠占比高（≥ `climb_frac_thresh`）同样打此标记。
    """
    if corridor is not None:
        result = _segment_with_corridor(
            gray,
            corridor,
            thresh=thresh,
            tape_width_frac=tape_width_frac,
            min_area=min_area,
            max_area_frac=max_area_frac,
            body_run_min_px=body_run_min_px,
            climb_frac_thresh=climb_frac_thresh,
        )
        if result is not None:
            return result
        # 走廊结构非法（退化条件）：退回无走廊路径，但必须显式打标。
        fallback = segment_animal(
            gray,
            thresh=thresh,
            band=band,
            tape_width_frac=tape_width_frac,
            min_area=min_area,
            max_area_frac=max_area_frac,
        )
        return SegResult(fallback.mask, fallback.reason,
                         fallback.flags + ("corridor_unused",))

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
        # 阈值取 0.10 而非 0.20：实测真实胶带宽 6–7 px / 隔间宽 95 px ≈ 7%，
        # 而动物宽 12–16 px ≈ 13–17%。用 0.20（19 px）会让这个标记在每个隔间
        # 都触发，等于没有信息。恒亮的标记比没有标记更糟。
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


# ---- 胶带走廊标定 -------------------------------------------------------------

# 扩展带收口常量（全部有实测依据，见 calibrate_tape_corridor 注释与
# 2026-08-24 素材测量）：
_PRESENCE_FREQ_LO = 0.10  # 暗频率 ≥0.10 才算"出现过"（31 帧样本 ≈ ≥3 帧）
_PRESENCE_MIN_COLS = 3    # 一行至少 3 列暗才算痕迹（单列尾巴/噪点不算）
_BLOCK_GAP = 3            # 行块内允许的最大空隙行数（尾巴亮区实测 ≤3 行）
_HANG_REACH = 60          # 动物块必须起始于胶带底端 60 行内（实测尾隙 31–36）
# 收口余量 = _SEAL_BL_K × BL（BL 在标定期对全部标定帧估一次，取中位数）。
# **不用硬编码像素**：本项目主张阈值体长归一化（D2），且硬件规格 ≥1280×720
# 比当前素材线性约 2.7×，届时 BL 46–98 px，固定像素余量相对缩到 ~0.4×BL、
# 会把动物切掉。k 由当前数据反解：30 px 余量 / BL 20.9–35.6 = 0.84–1.44，取 1.0。
_SEAL_BL_K = 1.0
_BOX_MARGIN = 15         # 收口硬上界与盒区结构顶端的最小间隔行数（评审判据）
_BOTTOM_GUARD = 20       # 离帧底这么近的痕迹块一律视为盒/框（动物悬挂够不到帧底）


@dataclass(frozen=True)
class TapeCorridor:
    """胶带走廊标定结果。**只是提案**，权威来源是人工确认过的 GeometryEnvelope
    （geometry.py 的 ROLE_TAPE_CORRIDOR，confirmed=True 后方可作正式标定）。

    坐标均为隔间 ROI 局部坐标（ROI = 整帧按隔间列切出的窄条，行号与整帧一致）。

    - `col_range`: 走廊列区间 [c0, c1]（闭区间）。胶带所在的竖直列。
    - `row_range`: 胶带最大竖直延伸 [r0, r1]（闭区间）。r1 = 胶带底端
      （暗频率首次跌破阈值的行的上一行），≈ 尾根附着处。
    - `confidence`: 上部区域走廊列的平均暗频率 ∈ (0, 1]，越接近 1 越可信。
    - `band_range`: 稳定化的面板行带 [r0, r1]。标定期逐帧扩展取并（避免
      逐帧 panel_band 底缘在阈值附近抖动、把动物截断），再收口到动物活动块
      之下（避免把底部收集盒区纳入搜索——盒内碎屑会成为假候选）。
    - `sealed`: 带底是否已按动物活动块收口。**False 是显式的危险态**：
      未收口 = 带底仍是扩展值、可能已进盒区，此时分割会带 `band_unsealed`
      标记。找不到悬挂运动块或估不出 BL 时才会出现（见 §6.2 纪律：
      宁可带警告跑，不静默回到坏状态）。手工给定的走廊默认 True。
    """

    col_range: tuple[int, int]
    row_range: tuple[int, int]
    confidence: float
    band_range: tuple[int, int]
    sealed: bool = True


def _extended_band(
    gray: np.ndarray, base_band: tuple[int, int], ext_frac: float
) -> tuple[int, int]:
    """把 panel_band 的底缘向下扩展：亮像素占比仍 > ext_frac 的行并回来。

    为什么需要：面板底部常有轻度阴影带（实测亮占比 0.84–0.93，恰在 0.85 门槛
    附近），逐帧 Otsu 稍变，panel_band 底缘就在两个位置间跳变，动物会被随机
    截断。面板几何是常量，应在标定期一次性取稳（见 calibrate_tape_corridor）。
    """
    thr = otsu_threshold(gray)
    frac = (np.asarray(gray, dtype=np.float64) > thr).mean(axis=1)
    r0, r1 = base_band
    h = gray.shape[0]
    while r1 + 1 < h and frac[r1 + 1] > ext_frac:
        r1 += 1
    return (r0, r1)


def _max_outside_run(dark_row: np.ndarray, cols: np.ndarray) -> int:
    """一行中、走廊**外**暗像素的最长连续段长度。

    用连续段宽度而不是像素个数判"动物身体行"：胶带边缘抗锯齿是两侧各 1 px 的
    孤立暗点（个数会达 2–3），动物身体则是 ≥6 px 的连续段。个数判据会在
    面板带顶部被抗锯齿误触发（实测踩坑），宽度判据天然免疫。
    """
    out = np.flatnonzero(dark_row & ~cols)
    if out.size == 0:
        return 0
    gaps = np.diff(out)
    best = cur = 1
    for g in gaps:
        cur = cur + 1 if g == 1 else 1
        if cur > best:
            best = cur
    return best


def _block_max_run(pmask: np.ndarray, block: tuple[int, int]) -> int:
    """块内所有行中、出现像素的最长连续段宽度。

    判"这个痕迹块里有没有身体级的宽行"：动物身体必有（蜷曲/悬挂时宽 ≥ 体长/4），
    2 px 的静态挂线/划痕/边缘渗漏没有。收口生长时据此区分动物碎片与盒区结构。
    """
    best = 0
    for r in range(block[0], block[1] + 1):
        idx = np.flatnonzero(pmask[r])
        if idx.size == 0:
            continue
        runs = np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1)
        w = max(len(s) for s in runs)
        if w > best:
            best = w
    return best


def calibrate_tape_corridor(
    grays: list[np.ndarray],
    *,
    dark_freq_thresh: float = 0.90,
    top_depth: int = 30,
    band_ext_frac: float = 0.70,
    max_width_frac: float = 0.25,
    thresh: float | None = None,
) -> TapeCorridor | None:
    """从同一隔间 ROI 的多帧灰度图标定胶带走廊。**失败返回 None，不猜。**

    建议输入为全片均匀抽样的 20–40 帧。原理（暗频率法）：

    - 胶带在几乎所有帧都占据同一批列；动物在动。**逐像素统计"暗频率"
      = 该像素在多少比例的帧里低于阈值**，胶带列 ≈ 1.0，动物扫过的列远低于。
    - 候选列只看**面板带顶缘起 `top_depth` 行**：胶带从面板带顶缘垂下，这段
      必在胶带内、且动物悬挂在胶带底端之下、到不了这里。**锚定在带顶而不是
      取全带的固定比例**——带深随素材变化，比例窗口会伸到胶带底端以下，把
      走廊列的分数稀释到阈值之下（高个素材上实测踩到）。
    - 走廊行下界 = 走廊列的暗频率自上面下首次跌破阈值处（≈ 胶带底端/尾根）。
      取**首次**跌破而非"最低达标行"：再往下动物频繁出现会形成第二段高频区，
      不能当作胶带的延伸。

    判别依据（实测）：结构立柱暗占比 1.00、胶带 0.32–0.67（全带），两者宽度
    都约 6–7 px，靠宽度分不开。本函数在顶部区域找候选列段，若出现多段则依次
    用两条结构性规则消歧（不是猜测，失败即返回 None）：

    1. 贴 ROI 左/右边缘的段是立柱渗漏，优先丢弃（胶带悬在隔间内部）；
    2. 全带剖面从不跌破阈值的段是立柱（胶带必有底端），丢弃。
    """
    if len(grays) < 2:
        return None
    g0 = np.asarray(grays[0], dtype=np.float64)
    h, w = g0.shape
    thr = otsu_threshold(g0) if thresh is None else thresh

    # 逐帧面板带 → 扩展 → 取稳：r0 中位数、r1 最大值。
    r0s: list[int] = []
    r1s: list[int] = []
    for g in grays:
        g = np.asarray(g, dtype=np.float64)
        found = panel_band(g)
        if found is None:
            continue
        e0, e1 = _extended_band(g, found, band_ext_frac)
        r0s.append(e0)
        r1s.append(e1)
    if len(r0s) < 2:
        return None
    r0 = int(np.median(r0s))
    r1 = int(max(r1s))
    if r1 - r0 < 10 or r0 < 0 or r1 >= h:
        return None

    # 暗频率图（公共区域 r0..r1）。
    dark = np.stack(
        [np.asarray(g, dtype=np.float64)[r0 : r1 + 1, :] < thr for g in grays]
    )
    freq = dark.mean(axis=0)
    bh = r1 - r0 + 1

    # 顶部窗口锚定在带顶（胶带从带顶垂下），不随带深按比例放大。
    u_depth = max(1, min(top_depth, bh))
    col_score = freq[:u_depth, :].mean(axis=0)
    cand = np.flatnonzero(col_score >= dark_freq_thresh)
    if cand.size == 0:
        return None
    runs = [
        (int(s[0]), int(s[-1]))
        for s in np.split(cand, np.flatnonzero(np.diff(cand) > 1) + 1)
    ]

    # 宽度护栏：胶带是细条。超过隔间宽 1/4 的"走廊"必是误标定（实测胶带 ≈7%）。
    runs = [(a, b) for a, b in runs if (b - a + 1) <= max_width_frac * w]
    if not runs:
        return None
    if len(runs) > 1:
        # 消歧一：贴边的段是结构立柱渗漏。
        inner = [(a, b) for a, b in runs if a > 0 and b < w - 1]
        if inner:
            runs = inner
    if len(runs) > 1:
        # 消歧二：胶带必有底端（全带剖面会跌破阈值）；从不跌破的是立柱。
        ended = []
        for a, b in runs:
            prof = freq[:, a : b + 1].mean(axis=1)
            if float(prof.min()) < dark_freq_thresh:
                ended.append((a, b))
        if ended:
            runs = ended
    if len(runs) != 1:
        return None

    c0, c1 = runs[0]
    # 胶带底端 = 各走廊列"首次跌破"位置的中位数。不用平均剖面：胶带边缘的
    # 抗锯齿列可能只在上段稳定、中段就开始闪动（实测 ch2 列 47 在行 95 就跌破，
    # 而胶带真底端在 124），平均剖面会被这种离群列拉偏；逐列取中位数免疫。
    col_drops: list[int] = []
    for j in range(c0, c1 + 1):
        d = np.flatnonzero(freq[:, j] < dark_freq_thresh)
        col_drops.append(int(d[0]) if d.size else bh)
    med = int(np.median(col_drops))
    row_end = r0 + med - 1 if med < bh else r1

    # 扩展带收口：不得进入底部收集盒区。扩展用的"亮占比 >0.70"挡不住盒区——
    # 玻璃盒在部分隔间里大部分行仍是亮的（实测 ch3/4 延伸到 254，盒内不触边
    # 的碎屑会成为候选）。收口依据一条几何事实：**动物悬挂在胶带上，它的活动
    # 痕迹必须出现在胶带底端附近**；盒区结构（盒缘/挂线/累积粪粒，静态或渐积）
    # 离动物更远。具体做法见下方"向下生长"注释；带底封到 动物块底 + _SEAL_BL_K×BL
    # （BL 归一化，见常量注释），**两侧都有约束**——再取盒区结构顶 − _BOX_MARGIN
    # 为硬上界，防胶带更长、挂得更低的个体把收口推进盒区。找不到悬挂块或估不出
    # BL ⇒ 不收口，但必须显式标 sealed=False（下游打 band_unsealed）：不收口 =
    # 回到刚被证明会伸进盒区的状态，静默退化违反 §6.2 纪律。
    #
    # 痕迹行块建立在**出现行**（暗频率 ≥ _PRESENCE_FREQ_LO，静物与运动都算）上：
    # 动物静止重叠区（频率=1.0）与盒区静态结构（频率≈1.0）都是高频，只靠频率
    # 分不开，靠"与动物块的间隙 > BL"+"块内最宽行 ≥ 体宽"分开；若把静物排除，
    # 盒缘这种静态结构反而看不见，硬上界会漏。胶带本身也是静态高频，建块前把
    # 胶带底端以上的走廊列从出现掩膜里挖掉——否则胶带会与动物并成一块、块顶
    # 冲到面板带顶、seed 判据失配（实测 v4 隔间3）。
    pmask = freq >= _PRESENCE_FREQ_LO
    row_end_local = row_end - r0
    pmask[: row_end_local + 1, c0 : c1 + 1] = False
    presence = pmask.sum(axis=1)
    presence_rows = np.flatnonzero(presence >= _PRESENCE_MIN_COLS)
    blocks: list[tuple[int, int]] = []
    if presence_rows.size:
        btop = bbot = int(presence_rows[0])
        for i in presence_rows[1:]:
            if int(i) - bbot <= _BLOCK_GAP + 1:
                bbot = int(i)
            else:
                blocks.append((btop, bbot))
                btop = bbot = int(i)
        blocks.append((btop, bbot))
    sealed = False
    # 识别悬挂块用"向下生长"而不是逐块判定：从最高的悬挂块出发（块顶必须在
    # **胶带底端以下**——胶带自身的轻微摆动会在胶带区内制造痕迹块，实测 v6
    # 隔间1 因此把带收塌到胶带底端之上；动物悬挂只出现在底端之下，攀爬向上、
    # 不影响深度收口），且在底端 _HANG_REACH 行内。生长规则：与当前区域的间隙
    # ≤ BL **且** 块内出现过身体级宽行的块仍是动物（静止重叠区与尾巴亮区都
    # ≤ 体长；身体必然有宽行，2 px 的静态挂线/划痕没有——实测 v7 隔间2 盒区
    # 2 px 挂线曾按间隙 ≤ BL 被误并入动物区）。间隙 > BL 或无宽行的块即盒区
    # 结构。距离与宽度判据都相对 BL，与体长同比例缩放，跨分辨率成立。
    seed = next(
        (
            b
            for b in blocks
            if row_end_local - 3 <= b[0] <= row_end_local + _HANG_REACH
        ),
        None,
    )
    if seed is not None:
        conf = float(col_score[c0 : c1 + 1].mean())
        provisional = TapeCorridor(
            col_range=(c0, c1),
            row_range=(r0, row_end),
            confidence=conf,
            band_range=(r0, r1),
            sealed=False,
        )
        bl_est = _estimate_body_length(grays, provisional)
        if bl_est > 0:
            min_body_run = max(5.0, bl_est / 4.0)
            # 动物从顶端胶带悬挂，不会垂到帧底；贴近帧底的块是盒/框，不是动物。
            # 实测各视频动物最深处离帧底 ≥40 行，_BOTTOM_GUARD 取 20 留足余量，
            # 并对大动物按 0.5×BL 放大。
            bottom_guard = max(_BOTTOM_GUARD, int(0.5 * bl_est))
            frame_bottom_local = (h - 1) - r0
            idx = blocks.index(seed)
            animal_bottom = seed[1]
            rest: list[tuple[int, int]] = []
            for b in blocks[idx + 1 :]:
                near_bottom = b[1] >= frame_bottom_local - bottom_guard
                if (
                    not near_bottom
                    and b[0] - animal_bottom <= bl_est
                    and _block_max_run(pmask, b) >= min_body_run
                ):
                    animal_bottom = b[1]
                else:
                    rest.append(b)
            seal = r0 + animal_bottom + int(round(_SEAL_BL_K * bl_est))
            # 硬上界：盒区结构顶（首个未并下来的块）− _BOX_MARGIN。
            if rest:
                box_top = r0 + rest[0][0]
                seal = min(seal, box_top - _BOX_MARGIN)
            # 收口永不裁掉已观测到的动物区域（盒缘贴着动物的极端几何下，
            # 宁留窄余量也不截断观测）。
            seal = max(seal, r0 + animal_bottom)
            r1 = min(r1, seal)
            sealed = True

    return TapeCorridor(
        col_range=(c0, c1),
        row_range=(r0, row_end),
        confidence=float(col_score[c0 : c1 + 1].mean()),
        band_range=(r0, r1),
        sealed=sealed,
    )


def _estimate_body_length(
    grays: list[np.ndarray], corridor: TapeCorridor
) -> float:
    """标定期体长：全部标定帧走一遍走廊路径，取掩膜 BL 的**中位数**。

    只供收口余量归一化用（_SEAL_BL_K × BL）。中位数对个别帧失手免疫：
    试次全程动物悬挂，绝大多数帧的掩膜是动物；即便个别帧挑到盒区碎屑也
    移不动中位数。估不出（全失败）返回 0，调用方放弃收口并显式标记。
    """
    bls: list[float] = []
    for g in grays:
        res = _segment_with_corridor(
            np.asarray(g, dtype=np.float64),
            corridor,
            thresh=None,
            tape_width_frac=0.10,
            min_area=20,
            max_area_frac=0.35,
            body_run_min_px=3,
            climb_frac_thresh=0.10,
        )
        if res is None or not res.ok or res.mask is None:
            continue
        met = sil.metrics(res.mask, with_holes=False)
        if met is not None and met.body_length > 0:
            bls.append(met.body_length)
    return float(np.median(bls)) if bls else 0.0


def _find_animal_below(
    sub: np.ndarray,
    t_local: int,
    *,
    min_area: int,
    area_cap: float,
) -> Component | None:
    """在 `sub[t_local:, :]` 里找动物组件：排除触边者，取面积最大者。

    走廊路径里截断面以下不应再有胶带（胶带在截断面以上被整体切掉），所以
    与无走廊路径的"取最低"不同，这里**取最大**：触边规则已排除箱体/收集盒，
    悬空的内部连通域里最大者即动物。
    """
    if t_local >= sub.shape[0]:
        return None
    cut = sub[t_local:, :]
    comps = label_components(cut, min_area=min_area)
    interior = [c for c in comps if not _touches(c, cut.shape)]
    cands = [c for c in interior if min_area <= c.area <= area_cap]
    if not cands:
        return None
    return max(cands, key=lambda c: c.area)


def _segment_with_corridor(
    gray: np.ndarray,
    corridor: TapeCorridor,
    *,
    thresh: float | None,
    tape_width_frac: float,
    min_area: int,
    max_area_frac: float,
    body_run_min_px: int,
    climb_frac_thresh: float,
) -> SegResult | None:
    """走廊路径实现。返回 None 表示走廊结构非法，调用方退回无走廊路径。"""
    g = np.asarray(gray, dtype=np.float64)
    h, w = g.shape

    # --- 走廊合法性。非法则返回 None（调用方退回无走廊路径 + corridor_unused）。
    c0, c1 = corridor.col_range
    r0, r1 = corridor.band_range
    row_end = corridor.row_range[1]
    if c1 < c0 or c1 < 0 or c0 >= w:
        return None
    if r1 - r0 < 10 or r0 < 0 or r1 >= h:
        return None
    if not (r0 <= row_end <= r1):
        return None

    thr = otsu_threshold(g) if thresh is None else thresh
    sub = g[r0 : r1 + 1, :] < thr
    cols = np.zeros(w, dtype=bool)
    cols[max(c0, 0) : min(c1, w - 1) + 1] = True
    # 未收口是显式危险态（带底可能已进盒区）：每次分割都带标记，供上层 QC。
    unsealed_flag = () if corridor.sealed else ("band_unsealed",)

    # 逐行"走廊外最长连续暗段"。抗锯齿是 1 px 孤立点，动物身体是连续段。
    runs = np.array(
        [_max_outside_run(sub[r], cols) for r in range(sub.shape[0])]
    )

    # 截断面 = 胶带底端 + 1：胶带被整体切掉。
    t_local = row_end - r0 + 1
    pick = _find_animal_below(sub, t_local, min_area=min_area,
                              area_cap=max_area_frac * sub.size)
    climb_path = False
    if pick is None:
        # 截断面以下没有动物。两种可能：动物完全缩在走廊里（与胶带无法区分），
        # 或动物**攀爬**到了胶带区。后者是真实行为（尾攀爬，C57BL/6 常见），
        # 不能设计掉：回搜胶带区是否存在持续够宽的外部暗段（= 身体突出走廊）。
        # 已知边界：攀爬个体的身体若未覆盖其下方残余胶带（身体底端高于胶带
        # 底端），残段会并入掩膜使面积偏大。攀爬帧已被 animal_in_corridor
        # 标记，事件层/试次有效性会处理（金标准本就要求排除攀爬试次）。
        lim = min(t_local, len(runs))
        hits = np.flatnonzero((runs >= body_run_min_px) & (np.arange(len(runs)) < lim))
        if hits.size:
            t_local = int(hits[0])
            climb_path = True
            pick = _find_animal_below(sub, t_local, min_area=min_area,
                                      area_cap=max_area_frac * sub.size)
        if pick is None:
            reason = (
                "动物完全位于走廊内（或缺失），无法与胶带区分"
                if hits.size == 0
                else "走廊区见疑似攀爬信号，但未找到合规动物组件"
            )
            return SegResult(None, reason, flags=("animal_in_corridor",) + unsealed_flag)

    out = np.zeros_like(g, dtype=bool)
    out[r0 + t_local : r1 + 1, :] = pick.mask

    flags: list[str] = []
    # 攀爬度量：落在胶带底端**之上**的剪影像素占比。正常悬挂 ≈0（身体在
    # 尾根以下）；攀爬时身体上移进入走廊行区间，占比显著升高。
    area = int(pick.area)
    climb_frac = float(out[: row_end + 1, :].sum()) / max(1, area)
    if climb_path or climb_frac >= climb_frac_thresh:
        flags.append("animal_in_corridor")

    max_tape_w = max(2, int(round(tape_width_frac * w)))
    if pick.width <= max_tape_w:
        flags.append("narrow_as_tape")

    return SegResult(out, "", tuple(flags) + unsealed_flag)
