"""候选特征（Spec A §5.2）：只出**可解释**的量，没实现的明说没实现。

五条候选，逐一对照 Spec：

1. **质心位移/速度**，用可解释的空间尺度归一 —— 尺度取**杯内区宽度（px）**，
   它是从真帧量出来的、说得清出处的量。没有标尺条 ⇒ `mm_per_px=None`，
   **绝不**拿 CLB 里的杯径换算成毫米（那是 CSI 的参数，不是测量）。
2. **非刚性轮廓变化** —— 逐帧 silhouette 的 elongation / bend / 面积的帧间差。
3. **刚体平移/旋转 与 补偿后残差，两个都留** —— 只报位移会把"整只动物游过去"
   和"原地划水但身体没挪"混成一个数；只报残差会把"游过去"读成"没动"。
   残差 = 把上一帧掩膜按 (Δx, Δy, Δθ) 刚性对齐后与当前掩膜的 1−IoU。
4. **触壁距离 / 身体与水线的关系** —— 来自 perception 的 wall_dist_px 与
   `silhouette.above_below` 的水上面积比例。
5. **动物区域内局部运动 vs 区域外水扰** —— **没实现**。这里没有光流，
   也没有局部运动估计；`LOCAL_MOTION_STATUS` 恒为 `not_implemented`。
   Spec A §5.2 的原话是"尚无可靠实现时不得假装光流/局部运动功能已存在"，
   所以这个键的值是字符串标记，**任何下游把它当数字用都是 bug**。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..assay_core import silhouette as sil

#: 第 5 条候选feature 的状态标记。是字符串，不是数。
LOCAL_MOTION_NOT_IMPLEMENTED = "not_implemented"

LOCAL_MOTION_REASON = (
    "本包没有光流/局部运动估计实现。动物区域内运动与区域外水扰的区分"
    "尚无可靠实现，按 Spec A §5.2 不许假装它存在。")


@dataclass(frozen=True)
class PairFeatures:
    """相邻两个 observed 帧之间的候选特征。单位写在字段名里。"""

    frame_prev: int
    frame_cur: int
    dt_s: float
    disp_px: float                    # 质心平移量（像素，未归一）
    disp_norm: float                  # disp_px / 杯内区宽度（可解释尺度）
    speed_norm_per_s: float           # disp_norm / dt
    dtheta_rad: float                 # 主轴方向变化（无方向性，取 mod π 后的最小角）
    residual_after_rigid: float       # 刚性对齐后 1−IoU；0=纯刚体
    d_elongation: float
    d_bend: float
    d_area_frac: float                # (area_cur − area_prev) / area_prev
    above_water_frac_cur: float | None
    wall_dist_px_cur: float | None


def _wrap_angle(d: float) -> float:
    """主轴无方向性（θ 与 θ+π 等价），差值折到 [0, π/2]。"""
    d = abs(d) % np.pi
    return float(min(d, np.pi - d))


def _translate(mask: np.ndarray, dy: int, dx: int) -> np.ndarray:
    out = np.zeros_like(mask, dtype=bool)
    h, w = mask.shape
    r0, r1 = max(0, dy), min(h, h + dy)
    c0, c1 = max(0, dx), min(w, w + dx)
    if r0 >= r1 or c0 >= c1:
        return out
    out[r0:r1, c0:c1] = mask[r0 - dy:r1 - dy, c0 - dx:c1 - dx]
    return out


def _rotate_about(mask: np.ndarray, cy: float, cx: float, dtheta: float) -> np.ndarray:
    """绕 (cy, cx) 旋转 dtheta（最近邻）。小角度补偿用，不追求插值质量。"""
    if abs(dtheta) < 1e-9:
        return mask
    h, w = mask.shape
    ys, xs = np.nonzero(mask)
    if ys.size == 0:
        return mask
    c, s = np.cos(-dtheta), np.sin(-dtheta)
    dy, dx = ys - cy, xs - cx
    ny = np.rint(cy + dy * c - dx * s).astype(int)
    nx = np.rint(cx + dy * s + dx * c).astype(int)
    ok = (ny >= 0) & (ny < h) & (nx >= 0) & (nx < w)
    out = np.zeros_like(mask, dtype=bool)
    out[ny[ok], nx[ok]] = True
    return out


def rigid_residual(mask_prev: np.ndarray, mask_cur: np.ndarray,
                   cent_prev: tuple[float, float], cent_cur: tuple[float, float],
                   theta_prev: float, theta_cur: float) -> float:
    """把 prev 按 (Δx, Δy, Δθ) 对齐到 cur 后的 1−IoU。

    两个掩膜都空 ⇒ 0.0（没有东西可残差）；只有一个空 ⇒ 1.0（完全没对上）。
    """
    a = np.asarray(mask_prev, dtype=bool)
    b = np.asarray(mask_cur, dtype=bool)
    if not a.any() and not b.any():
        return 0.0
    if not a.any() or not b.any():
        return 1.0
    dx = int(round(cent_cur[0] - cent_prev[0]))
    dy = int(round(cent_cur[1] - cent_prev[1]))
    aligned = _translate(a, dy, dx)
    aligned = _rotate_about(aligned, cent_cur[1], cent_cur[0],
                            _wrap_angle(theta_cur - theta_prev))
    inter = int((aligned & b).sum())
    union = int((aligned | b).sum())
    return 1.0 - (inter / union if union else 0.0)


def pair_features(*, frame_prev: int, frame_cur: int, fps: float,
                  mask_prev: np.ndarray, mask_cur: np.ndarray,
                  cent_prev: tuple[float, float], cent_cur: tuple[float, float],
                  theta_prev: float, theta_cur: float,
                  spatial_scale_px: float,
                  above_water_frac_cur: float | None,
                  wall_dist_px_cur: float | None) -> PairFeatures:
    m_prev = sil.metrics(mask_prev, with_holes=False)
    m_cur = sil.metrics(mask_cur, with_holes=False)
    if m_prev is None or m_cur is None:
        raise ValueError("pair_features 只吃非空掩膜（空掩膜帧不该是 observed）")
    dt = (frame_cur - frame_prev) / fps
    if dt <= 0:
        raise ValueError(f"帧序错乱或重复：{frame_prev} → {frame_cur}")
    disp = float(np.hypot(cent_cur[0] - cent_prev[0], cent_cur[1] - cent_prev[1]))
    if spatial_scale_px <= 0:
        raise ValueError("空间尺度必须为正（杯内区宽度量不出来就别归一）")
    d_area = ((m_cur.area - m_prev.area) / m_prev.area) if m_prev.area else 0.0
    return PairFeatures(
        frame_prev=frame_prev, frame_cur=frame_cur, dt_s=dt,
        disp_px=disp, disp_norm=disp / spatial_scale_px,
        speed_norm_per_s=(disp / spatial_scale_px) / dt,
        dtheta_rad=_wrap_angle(theta_cur - theta_prev),
        residual_after_rigid=rigid_residual(mask_prev, mask_cur,
                                            cent_prev, cent_cur,
                                            theta_prev, theta_cur),
        d_elongation=m_cur.elongation - m_prev.elongation,
        d_bend=m_cur.bend - m_prev.bend,
        d_area_frac=float(d_area),
        above_water_frac_cur=above_water_frac_cur,
        wall_dist_px_cur=wall_dist_px_cur,
    )


def summarize(pairs: list[PairFeatures]) -> dict:
    """逐对特征的汇总。**只汇总 observed 帧之间的对**，分母写清楚。

    返回的是研究诊断数：中位数 + p90 + 对数。均值对这种重尾分布没意义，
    但中位数也得带 n——n 太小（<10）时这些数字什么都说明不了。
    """
    if not pairs:
        return {"n_pairs": 0,
                "local_motion_inside_vs_outside": LOCAL_MOTION_NOT_IMPLEMENTED,
                "local_motion_reason": LOCAL_MOTION_REASON}
    def stat(vals: list[float]) -> dict:
        a = np.asarray(vals, dtype=float)
        return {"median": float(np.median(a)),
                "p90": float(np.percentile(a, 90)),
                "max": float(a.max())}
    return {
        "n_pairs": len(pairs),
        "disp_norm": stat([p.disp_norm for p in pairs]),
        "speed_norm_per_s": stat([p.speed_norm_per_s for p in pairs]),
        "dtheta_rad": stat([p.dtheta_rad for p in pairs]),
        "residual_after_rigid": stat([p.residual_after_rigid for p in pairs]),
        "d_area_frac": stat([p.d_area_frac for p in pairs]),
        "local_motion_inside_vs_outside": LOCAL_MOTION_NOT_IMPLEMENTED,
        "local_motion_reason": LOCAL_MOTION_REASON,
    }
