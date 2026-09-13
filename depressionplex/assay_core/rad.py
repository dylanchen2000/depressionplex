"""RAD：剪影域的刚体-关节运动分解（Rigid-Articulated Decomposition）。

目的：把「整体刚体位移/旋转」与「身体各部位相对自身的形变」在数学上分开。

  ① 由轮廓矩估计帧间刚体+尺度变换 T
  ② 把上一帧剪影按 T warp 到当前帧 → Ŝ
  ③ 刚体分量 = T（平移、旋转角速度 ω、尺度）      → 钟摆摆动 / 水流带动
  ④ 关节残差 = area(Ŝ △ S_cur) / BL²              → 真正的主动动作

第 ② 步的 warp 有一个亚像素下限，这是 DP-071 的核心发现：对**二值**掩膜，
「双线性采样 + 0.5 阈值」等价于把位移四舍五入到最近整数像素 ⇒ 量化下限恰好
0.5 px，而不动帧的估计位移中位只有 0.106 px ⇒ 刚体补偿在最需要它的那批帧上
逐位空转。DP-071-R 把没有死区的口径实现出来（距离场 + 覆盖率，见
`RESIDUAL_MODES`），在 26 个试次上同分割同帧正面对比，读数是**反的**：
帧级 AUC 中位 −0.017、25/26 为负 ⇒ **默认口径仍然是二值掩膜 + 硬 XOR**。
这支持一个解释：硬 XOR 是"量化自洽"的——它按整像素测变化、也按整像素补偿，
两边的量化互相抵消；覆盖率则对二值掩膜根本观测不到的亚像素几何敏感，
把光栅化噪声一起算进了残差。**死区是真的，但它与分子的整像素分辨率是配套的。**
等高分辨素材到位再评距离场口径（这批数据原生 120x272、BL 26–33 px 是硬约束）。

为什么必须这么做：TST 金标准（Can et al. 2012）明确要求把「因先前挣扎的惯性
产生的钟摆式摆动」排除在 mobility 之外。CSI 用的是标量 blob 帧间运动量，原理上
分不开这两者；剪影域的刚体分解可以。

已知盲区：XOR 只看轮廓变化。动物在剪影包络内部动肢体（水下尤甚）不改变外轮廓时
XOR 察觉不到，必须配掩膜内稠密光流互补（见 flow.py，待实现）。

不是本项目原创：DrugEffect `pose_arena_core.py` 已实现身体平移/旋转/头部刚性残差
（含 lag 1 与 lag 4 双时标）。本模块把同一思路移到剪影域，并沿用其双时标做法。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import silhouette as sil

# 沿用 DrugEffect 的双时标：短时标抓快速动作，长时标抓缓慢姿态改变。
DEFAULT_LAGS: tuple[int, ...] = (1, 4)

# 精修旋转时的搜索范围与步长（弧度）。主轴 theta 在近圆形剪影上不稳，需精修。
_REFINE_SPAN = np.deg2rad(12.0)
_REFINE_STEPS = 13

# 距离场的截断半径（像素）。coverage 把 |phi| >= 0.5 一律夹成 0/1 ⇒ 更远处的距离
# 准不准对残差没有任何影响；取 3 px 是给 warp 的双线性采样留 2 px 余量
# （旋转 + 尺度合起来不会把边界搬出 2 px）。截断让距离变换用纯 numpy 就精确，
# 不必引入 scipy——assay_core 只依赖 numpy 是本项目刻意的约束。
_SDF_RADIUS = 3.0

# 残差分子的两种口径。
#   "binary_xor"   —— 冻结口径：warp 二值掩膜后硬 XOR 数像素。DP-071 实测它有一个
#                     精确的 0.5 px 死区：对二值输入，「双线性采样 + 0.5 阈值」等价于
#                     把位移四舍五入到最近整数像素 ⇒ 任何 |u| < 0.5 px 的刚体补偿
#                     逐位空转。而不动帧的估计位移中位只有 0.106 px（比死区小一个
#                     数量级），空转率中位 78% ⇒ 最需要补偿的那批帧上刚体分解退化成
#                     未补偿的原始帧差。
#   "sdf_coverage" —— 没有死区的口径：warp 的是**有符号距离场**（连续量，插值它
#                     不需要再二值化），残差是两侧**覆盖率**之差的 L1。整数位移下
#                     与 "binary_xor" 逐位相等（有测试钉住），只在亚像素处不同；
#                     同帧残差恰好 0（区别于已否掉的"软掩膜对硬掩膜"修法）。
#
# **默认为什么还是 "binary_xor"。** DP-071-R 在 26 个试次上做过正面 A/B（同一次
# 分割、同一批稳态一致帧，只差本口径）：帧级 AUC 中位 **−0.017**、范围
# −0.044…+0.002、**25/26 为负**。跑之前写死的判定是「ΔAUC 中位 ≤ 0 ⇒ 不翻默认」，
# 读数落在这一档 ⇒ 距离场口径**留而不用**。留着的理由有两条：① 死区是代码级
# 恒等式，将来换高分辨相机后分子的整像素分辨率不再是瓶颈，这条口径可能反转；
# ② 它是"不动 ⇒ 恰好 0 残差"的唯一实现，是别的分析（如光栅化噪声估计）的工具。
#
# 一个副产物值得记住：距离场口径把**门槛离散度**改善了（极差比 19.4x → 9.3x、
# CV 51.4% → 37.2%），AUC 却整体变差。⇒ **离散度单独不构成判据**：给所有试次
# 加一个共同的底噪就能压低极差比，却不会让任何一帧更好分。评"换分母"（DP-073）
# 时必须同时报 ΔAUC，不许只报离散度。
RESIDUAL_MODES: tuple[str, ...] = ("binary_xor", "sdf_coverage")
DEFAULT_RESIDUAL_MODE = "binary_xor"


@dataclass(frozen=True)
class RigidTransform:
    """把 prev 剪影映射到 cur 剪影的刚体+尺度变换。"""

    dx: float  # 质心平移，像素
    dy: float
    dtheta: float  # 主轴旋转，弧度，已折叠到 (-pi/2, pi/2]
    scale: float  # 尺度比，由面积比开方得到
    c_prev: tuple[float, float]
    c_cur: tuple[float, float]

    def translation_norm(self, bl: float) -> float:
        """以 BL 归一化的平移量。"""
        if bl <= 0:
            return 0.0
        return float(np.hypot(self.dx, self.dy) / bl)


@dataclass(frozen=True)
class RadResult:
    """一对帧的分解结果。所有残差均以 BL² 归一化（无量纲）。"""

    rigid: RigidTransform
    residual: float  # 全身关节残差
    segment_residuals: tuple[float, ...]  # 沿身体长轴分段的残差
    bl: float
    lag: int

    def segment_ratio(self, index: int) -> float:
        """某段残差占各段残差之和的比例。

        index=0/1（二分时）对应主轴负/正方向的两段。哪一段是后肢需由 Pose 或
        颜色头尾辅助判定（轮廓主轴无方向性）——判不出时应输出 unknown，不要猜。
        """
        total = sum(self.segment_residuals)
        if total <= 0:
            return 0.0
        return float(self.segment_residuals[index] / total)


def _wrap_half_pi(angle: float) -> float:
    """把角度折叠到 (-pi/2, pi/2]。主轴方向无方向性，theta 与 theta+pi 等价。"""
    a = (angle + np.pi / 2) % np.pi - np.pi / 2
    return float(a if a != -np.pi / 2 else np.pi / 2)


def warp_mask(
    mask: np.ndarray,
    transform: RigidTransform,
    out_shape: tuple[int, int] | None = None,
) -> np.ndarray:
    """按 transform 把 mask warp 过去。双线性采样后按 0.5 阈值二值化。

    用双线性而非最近邻：最近邻的重采样误差会给残差抬出一个可观的噪声底，
    而残差噪声底正是本方案的头号风险。

    **不要拿它算残差**：见 `RESIDUAL_MODES` 的说明——对二值输入，"双线性采样 +
    0.5 阈值" 等价于把位移四舍五入到整数像素，所以本函数在亚像素位移下必然是空转。
    这不是实现缺陷，是"输出必须是像素掩膜"这件事本身的量化下限：边界正好落在两个
    像素中心之间，走不到半个像素就没有任何像素该翻面。残差要亚像素就不能经过
    二值掩膜这一道——那是 `warp_field` + `coverage` 的分工。

    留着它是因为 `estimate_rigid(refine=True)` 的搜索代价函数与 DP-071 的复现脚本
    都还在用它，且它在**整数**位移下是精确的（有测试钉住）。
    """
    m = np.asarray(mask, dtype=np.float64)
    shape = m.shape if out_shape is None else out_shape
    px, py, _ = _backward_coords(transform, shape)
    return _sample_bilinear(m, px, py) >= 0.5


def _backward_coords(
    transform: RigidTransform, out_shape: tuple[int, int]
) -> tuple[np.ndarray, np.ndarray, float]:
    """输出像素 q 反变换回输入坐标 p： p = R^{-1} (q - c_cur) / s + c_prev。

    同时返回实际用到的 scale——`warp_field` 需要它来缩放场的**取值**（距离随长度
    一起缩放），`warp_mask` 用不到。
    """
    h, w = out_shape
    cpx, cpy = transform.c_prev
    ccx, ccy = transform.c_cur
    s = transform.scale if transform.scale > 1e-6 else 1.0
    c, sn = np.cos(transform.dtheta), np.sin(transform.dtheta)

    yy, xx = np.mgrid[0:h, 0:w]
    qx = xx - ccx
    qy = yy - ccy
    rx = (qx * c + qy * sn) / s
    ry = (-qx * sn + qy * c) / s
    return rx + cpx, ry + cpy, s


def warp_field(
    field: np.ndarray,
    transform: RigidTransform,
    out_shape: tuple[int, int] | None = None,
    *,
    fill: float = _SDF_RADIUS,
) -> np.ndarray:
    """按 transform 把**有符号距离场**warp 过去。DP-071 修法的本体。

    与 `warp_mask` 的差别只有一处，但那一处就是死区的来源：距离场是连续量，
    插值完**不必再二值化** ⇒ 亚像素精度来自场本身，而不是网格密度。
    （升采样这条路已被算术排掉：要让实测中位 0.106 px 的漂移抬过 0.5 px 门需 ≥5x，
    覆盖最小的非零漂移 0.045 px 需 ≥12x ⇒ 每帧像素数 x25 到 x144。）

    `fill`：出界处填"远在外面"。填 0 会被当成"正好压在边界上"，那是错的。
    """
    f = np.asarray(field, dtype=np.float64)
    shape = f.shape if out_shape is None else out_shape
    px, py, s = _backward_coords(transform, shape)
    return _sample_bilinear(f, px, py, fill=float(fill)) * s


def _shift_bool(m: np.ndarray, dy: int, dx: int) -> np.ndarray:
    """整数平移：返回 out，满足 out[y, x] == m[y + dy, x + dx]，越界填 False。

    越界填 False 在这里是**对的**：数组之外没有前景，所以"那边没有更近的前景"。
    """
    out = np.zeros_like(m)
    h, w = m.shape
    ys0, ys1 = max(0, dy), min(h, h + dy)
    xs0, xs1 = max(0, dx), min(w, w + dx)
    if ys0 >= ys1 or xs0 >= xs1:
        return out
    out[ys0 - dy:ys1 - dy, xs0 - dx:xs1 - dx] = m[ys0:ys1, xs0:xs1]
    return out


def _edt_truncated(mask: np.ndarray, radius: float) -> np.ndarray:
    """欧氏距离变换，在 radius 以内**精确**，更远一律记 radius。

    做法是枚举半径内的所有整数偏移取最小值：半径 3 只有 24 个偏移 ⇒ 纯 numpy、
    全向量化、且没有近似（不是 chamfer/城区距离那种走样的近似）。
    完整 EDT（逐行抛物线法）在这里既没必要也更慢——只有边界那一圈进得了 coverage。
    """
    m = np.asarray(mask, bool)
    r = int(np.ceil(radius))
    d = np.full(m.shape, float(radius), dtype=np.float64)
    d[m] = 0.0
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            n2 = dy * dy + dx * dx
            if n2 == 0:
                continue
            dist = float(np.sqrt(n2))
            if dist >= radius:
                continue
            np.minimum(d, np.where(_shift_bool(m, dy, dx), dist, radius), out=d)
    return d


def signed_distance(mask: np.ndarray, *, radius: float = _SDF_RADIUS) -> np.ndarray:
    """二值掩膜的有符号距离场：内部为负、外部为正，零等值面落在边界像素之间。

    约定：紧贴背景的**内部**像素取 −0.5，紧贴前景的**外部**像素取 +0.5
    ⇒ 场沿法向的梯度是每像素 **1**（不是 2）⇒ "把场平移 u 像素"就等于"把边界
    搬动 u 像素"。这条约定是本口径成立的关键：若梯度是 2，覆盖率对位移的响应
    就会差整整一倍，残差的物理量纲跟着错。

    只在 |d| < radius 内精确，更远处饱和到 ±radius（见 `_SDF_RADIUS`）。
    空掩膜/满掩膜没有边界 ⇒ 整场取 ±radius。
    """
    m = np.asarray(mask, bool)
    far = float(radius)
    if not m.any():
        return np.full(m.shape, far)
    if m.all():
        return np.full(m.shape, -far)
    d_in = _edt_truncated(~m, radius)   # 内部像素到最近**背景**的距离
    d_out = _edt_truncated(m, radius)   # 外部像素到最近**前景**的距离
    return np.where(m, -(d_in - 0.5), d_out - 0.5)


def coverage(field: np.ndarray) -> np.ndarray:
    """把有符号距离场换成每像素"被剪影覆盖的面积比"，取值 [0, 1]。

    直线边界下 `clip(0.5 − phi, 0, 1)` 就是该像素落在形状内的**精确**面积比。
    对未经 warp 的掩膜它恰好还原成 0/1（内部像素 phi <= −0.5、外部 >= +0.5）
    ⇒ **不动就是 0 残差**，没有"软对硬"的底噪。

    这一点是本修法与 DP-071 已否掉的 A3（软掩膜对硬掩膜）的分界：A3 拿连续值的
    warp 结果去比硬二值的 `cur`，沿**整条轮廓**都在累账；这里两侧都过同一个
    coverage，静止时逐位相等。
    """
    return np.clip(0.5 - np.asarray(field, dtype=np.float64), 0.0, 1.0)


def _checked_field(field: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """校验外部传进来的距离场确实是这张掩膜的场。

    这个参数**只为省重复计算**，不是第二份真相；传错了会静默改变残差，
    所以这里宁可报错。校验本身只有两次数组运算，比重算距离场便宜得多。
    """
    f = np.asarray(field, dtype=np.float64)
    m = np.asarray(mask, bool)
    if f.shape != m.shape or not np.array_equal(f < 0.0, m):
        raise ValueError("传入的距离场与掩膜不一致——它只是缓存，不是另一份真相")
    return f


def _sample_bilinear(
    img: np.ndarray, px: np.ndarray, py: np.ndarray, *, fill: float = 0.0
) -> np.ndarray:
    h, w = img.shape
    x0 = np.floor(px).astype(np.int64)
    y0 = np.floor(py).astype(np.int64)
    x1 = x0 + 1
    y1 = y0 + 1
    fx = px - x0
    fy = py - y0

    def at(yi: np.ndarray, xi: np.ndarray) -> np.ndarray:
        ok = (yi >= 0) & (yi < h) & (xi >= 0) & (xi < w)
        out = np.full(yi.shape, float(fill), dtype=np.float64)
        out[ok] = img[yi[ok], xi[ok]]
        return out

    top = at(y0, x0) * (1 - fx) + at(y0, x1) * fx
    bot = at(y1, x0) * (1 - fx) + at(y1, x1) * fx
    return top * (1 - fy) + bot * fy


def _xor_area(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.count_nonzero(np.asarray(a, bool) ^ np.asarray(b, bool)))


def _erode_once(mask: np.ndarray) -> np.ndarray:
    """4-连通二值腐蚀（纯 numpy）。"""
    p = np.pad(np.asarray(mask, bool), 1, constant_values=False)
    return (
        p[1:-1, 1:-1] & p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:]
    )


def body_core(mask: np.ndarray, *, width_frac: float = 0.25) -> np.ndarray:
    """腐蚀掉细附肢，留下躯干核心。

    为什么需要它：刚体变换必须在**不随关节活动改变的部位**上估计。若拿整个剪影
    去估，肢体一动质心与主轴就偏，刚体拟合会把一部分关节运动"解释掉"，
    导致残差被系统性低估——灵敏度自损。躯干厚、附肢细，腐蚀正是分离两者的手段。

    腐蚀次数按体宽自适应（width_frac × body_width），不写死像素数，
    这样跨分辨率、跨体型都成立。腐蚀过头会掏空，此时回退到原掩膜。
    """
    m = np.asarray(mask, bool)
    met = sil.metrics(m, with_holes=False)
    if met is None or met.body_width <= 0:
        return m
    iters = int(round(met.body_width * width_frac))
    iters = max(1, min(iters, 12))
    core = m
    for _ in range(iters):
        nxt = _erode_once(core)
        if not nxt.any():
            break  # 再腐蚀就空了，保留上一步
        core = nxt
    return core if core.any() else m


def estimate_rigid(
    prev_mask: np.ndarray,
    cur_mask: np.ndarray,
    *,
    refine: bool = False,
    use_core: bool = True,
) -> RigidTransform | None:
    """估计刚体+尺度变换。

    默认在腐蚀后的躯干核心上估计（见 body_core 的说明），残差则在完整剪影上量。
    refine 默认 **False**：以最小 XOR 精修旋转角本意是为近圆剪影的主轴不稳兜底，
    但实测它会主动转动去匹配移位的肢体，把关节运动当成刚体旋转吸收掉，
    区分度从 5.2 掉到 4.3。只在确认主轴估计不稳时才开，且需复核灵敏度影响。
    """
    prev_fit = body_core(prev_mask) if use_core else np.asarray(prev_mask, bool)
    cur_fit = body_core(cur_mask) if use_core else np.asarray(cur_mask, bool)

    mp = sil.metrics(prev_fit, with_holes=False)
    mc = sil.metrics(cur_fit, with_holes=False)
    if mp is None or mc is None:
        return None

    # 尺度用完整剪影的面积比更稳（核心受腐蚀次数影响，不宜用于尺度）
    fp = sil.metrics(prev_mask, with_holes=False)
    fc = sil.metrics(cur_mask, with_holes=False)
    if fp is None or fc is None or fp.area <= 0:
        return None
    scale = float(np.sqrt(fc.area / fp.area))

    base = RigidTransform(
        dx=mc.centroid[0] - mp.centroid[0],
        dy=mc.centroid[1] - mp.centroid[1],
        dtheta=_wrap_half_pi(mc.theta - mp.theta),
        scale=scale,
        c_prev=mp.centroid,
        c_cur=mc.centroid,
    )
    if not refine:
        return base

    best = base
    best_cost = _xor_area(warp_mask(prev_fit, base, cur_fit.shape), cur_fit)
    for delta in np.linspace(-_REFINE_SPAN, _REFINE_SPAN, _REFINE_STEPS):
        if delta == 0.0:
            continue
        cand = RigidTransform(
            dx=base.dx,
            dy=base.dy,
            dtheta=_wrap_half_pi(base.dtheta + float(delta)),
            scale=base.scale,
            c_prev=base.c_prev,
            c_cur=base.c_cur,
        )
        cost = _xor_area(warp_mask(prev_fit, cand, cur_fit.shape), cur_fit)
        if cost < best_cost:
            best, best_cost = cand, cost
    return best


def trial_body_length(masks: list[np.ndarray]) -> float:
    """试次级体长 BL = 逐帧剪影主轴长度的时间中位数。

    **必须是试次级标量，不能逐帧算。** 逐帧 BL 会被肢体伸展污染（实测躯干 60 px
    的剪影，伸腿时整体主轴量到 76 px），而 BL² 是残差的归一化分母——分母抖动会
    直接给残差抬噪声底，正是本方案的头号风险。取时间中位数即可免疫瞬时伸展。
    """
    vals = []
    for m in masks:
        met = sil.metrics(m, with_holes=False)
        if met is not None and met.body_length > 0:
            vals.append(met.body_length)
    return float(np.median(vals)) if vals else 0.0


def decompose(
    prev_mask: np.ndarray,
    cur_mask: np.ndarray,
    *,
    n_segments: int = 2,
    refine: bool = False,
    lag: int = 1,
    bl: float | None = None,
    residual_mode: str | None = None,
    prev_field: np.ndarray | None = None,
    cur_field: np.ndarray | None = None,
) -> RadResult | None:
    """分解一对帧。任一帧剪影为空则返回 None（上层记 unknown，不要补 0）。

    bl 应传入试次级体长（见 trial_body_length）。不传则退化为用当前帧 BL，
    仅适合单帧调试，不要用于正式分析。

    residual_mode 不传 = `DEFAULT_RESIDUAL_MODE`（见 `RESIDUAL_MODES`）。
    prev_field / cur_field 是距离场缓存，只为省重复计算（`decompose_series` 会传），
    传错会被 `_checked_field` 挡下。
    """
    mode = DEFAULT_RESIDUAL_MODE if residual_mode is None else residual_mode
    if mode not in RESIDUAL_MODES:
        raise ValueError("residual_mode 只能是 %r，得到 %r" % (RESIDUAL_MODES, mode))
    transform = estimate_rigid(prev_mask, cur_mask, refine=refine)
    if transform is None:
        return None
    mc = sil.metrics(cur_mask, with_holes=False)
    if mc is None or mc.bl <= 0:
        return None
    scale_len = bl if (bl is not None and bl > 0) else mc.bl

    if mode == "binary_xor":
        warped = warp_mask(prev_mask, transform, cur_mask.shape)
        diff = (np.asarray(warped, bool) ^ np.asarray(cur_mask, bool)).astype(np.float64)
    else:
        pf = (signed_distance(prev_mask) if prev_field is None
              else _checked_field(prev_field, prev_mask))
        cf = (signed_distance(cur_mask) if cur_field is None
              else _checked_field(cur_field, cur_mask))
        warped_cov = coverage(warp_field(pf, transform, np.asarray(cur_mask).shape))
        diff = np.abs(warped_cov - coverage(cf))
    bl2 = scale_len * scale_len

    seg_res: list[float] = []
    for seg in sil.axis_segments(cur_mask, n_segments):
        # 该段的残差 = 差异图中落在（当前帧该段 ∪ 其膨胀邻域）内的部分。
        # 这里用当前帧分段直接掩蔽，简单且不引入额外参数。
        seg_res.append(float(diff[seg].sum()) / bl2)

    return RadResult(
        rigid=transform,
        residual=float(diff.sum()) / bl2,
        segment_residuals=tuple(seg_res),
        bl=scale_len,
        lag=lag,
    )


def decompose_series(
    masks: list[np.ndarray],
    *,
    lags: tuple[int, ...] = DEFAULT_LAGS,
    n_segments: int = 2,
    refine: bool = False,
    bl: float | None = None,
    residual_mode: str | None = None,
) -> list[dict[str, float | None]]:
    """对一段剪影序列做多时标分解。

    返回逐帧字典，键形如 residual_lag1 / omega_lag1 / trans_lag1 / seg0_lag1。
    值为 None 表示该帧不可算（剪影缺失或 lag 越界）——保留该帧但置空，
    与 DrugEffect 的质量门做法一致：不删 unit，只置空特征。
    """
    n = len(masks)
    scale_len = bl if (bl is not None and bl > 0) else trial_body_length(masks)
    mode = DEFAULT_RESIDUAL_MODE if residual_mode is None else residual_mode

    # 距离场按帧缓存：同一帧会被当 cur 用一次、当 prev 用 len(lags) 次。
    # **只留最近 max(lags)+1 帧**——9000 帧 x 120x272 的 float64 场是 2.3 GB，
    # 全量预算不起；滚动缓存把它压到 5 帧。
    cache: dict[int, np.ndarray] = {}
    keep = max(lags) + 1 if lags else 1

    def field(i: int) -> np.ndarray | None:
        if mode != "sdf_coverage":
            return None
        f = cache.get(i)
        if f is None:
            f = signed_distance(masks[i])
            cache[i] = f
        return f

    out: list[dict[str, float | None]] = []
    for i in range(n):
        for stale in [k for k in cache if k < i - keep]:
            del cache[stale]
        row: dict[str, float | None] = {"frame": float(i)}
        for lag in lags:
            keys = (
                f"residual_lag{lag}",
                f"omega_lag{lag}",
                f"trans_lag{lag}",
                f"scale_lag{lag}",
            )
            seg_keys = [f"seg{k}_lag{lag}" for k in range(n_segments)]
            res = None
            if i - lag >= 0:
                res = decompose(
                    masks[i - lag],
                    masks[i],
                    n_segments=n_segments,
                    refine=refine,
                    lag=lag,
                    bl=scale_len,
                    residual_mode=mode,
                    prev_field=field(i - lag),
                    cur_field=field(i),
                )
            if res is None:
                for k in (*keys, *seg_keys):
                    row[k] = None
                continue
            row[keys[0]] = res.residual
            row[keys[1]] = res.rigid.dtheta / lag
            row[keys[2]] = res.rigid.translation_norm(res.bl) / lag
            row[keys[3]] = res.rigid.scale
            for k, name in enumerate(seg_keys):
                row[name] = res.segment_residuals[k]
        out.append(row)
    return out
