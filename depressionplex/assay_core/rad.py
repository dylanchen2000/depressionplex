"""RAD：剪影域的刚体-关节运动分解（Rigid-Articulated Decomposition）。

目的：把「整体刚体位移/旋转」与「身体各部位相对自身的形变」在数学上分开。

  ① 由轮廓矩估计帧间刚体+尺度变换 T
  ② 把上一帧剪影按 T warp 到当前帧 → Ŝ
  ③ 刚体分量 = T（平移、旋转角速度 ω、尺度）      → 钟摆摆动 / 水流带动
  ④ 关节残差 = area(Ŝ XOR S_cur) / BL²            → 真正的主动动作

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
    """
    m = np.asarray(mask, dtype=np.float64)
    h, w = m.shape if out_shape is None else out_shape

    cpx, cpy = transform.c_prev
    ccx, ccy = transform.c_cur
    s = transform.scale if transform.scale > 1e-6 else 1.0
    c, sn = np.cos(transform.dtheta), np.sin(transform.dtheta)

    yy, xx = np.mgrid[0:h, 0:w]
    # 输出像素 q 反变换回输入坐标 p： p = R^{-1} (q - c_cur) / s + c_prev
    qx = xx - ccx
    qy = yy - ccy
    rx = (qx * c + qy * sn) / s
    ry = (-qx * sn + qy * c) / s
    px = rx + cpx
    py = ry + cpy

    return _sample_bilinear(m, px, py) >= 0.5


def _sample_bilinear(img: np.ndarray, px: np.ndarray, py: np.ndarray) -> np.ndarray:
    h, w = img.shape
    x0 = np.floor(px).astype(np.int64)
    y0 = np.floor(py).astype(np.int64)
    x1 = x0 + 1
    y1 = y0 + 1
    fx = px - x0
    fy = py - y0

    def at(yi: np.ndarray, xi: np.ndarray) -> np.ndarray:
        ok = (yi >= 0) & (yi < h) & (xi >= 0) & (xi < w)
        out = np.zeros(yi.shape, dtype=np.float64)
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
) -> RadResult | None:
    """分解一对帧。任一帧剪影为空则返回 None（上层记 unknown，不要补 0）。

    bl 应传入试次级体长（见 trial_body_length）。不传则退化为用当前帧 BL，
    仅适合单帧调试，不要用于正式分析。
    """
    transform = estimate_rigid(prev_mask, cur_mask, refine=refine)
    if transform is None:
        return None
    mc = sil.metrics(cur_mask, with_holes=False)
    if mc is None or mc.bl <= 0:
        return None
    scale_len = bl if (bl is not None and bl > 0) else mc.bl

    warped = warp_mask(prev_mask, transform, cur_mask.shape)
    diff = np.asarray(warped, bool) ^ np.asarray(cur_mask, bool)
    bl2 = scale_len * scale_len

    seg_res: list[float] = []
    for seg in sil.axis_segments(cur_mask, n_segments):
        # 该段的残差 = 差异图中落在（当前帧该段 ∪ 其膨胀邻域）内的部分。
        # 这里用当前帧分段直接掩蔽，简单且不引入额外参数。
        seg_res.append(float(np.count_nonzero(diff & seg)) / bl2)

    return RadResult(
        rigid=transform,
        residual=float(np.count_nonzero(diff)) / bl2,
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
) -> list[dict[str, float | None]]:
    """对一段剪影序列做多时标分解。

    返回逐帧字典，键形如 residual_lag1 / omega_lag1 / trans_lag1 / seg0_lag1。
    值为 None 表示该帧不可算（剪影缺失或 lag 越界）——保留该帧但置空，
    与 DrugEffect 的质量门做法一致：不删 unit，只置空特征。
    """
    n = len(masks)
    scale_len = bl if (bl is not None and bl > 0) else trial_body_length(masks)
    out: list[dict[str, float | None]] = []
    for i in range(n):
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
