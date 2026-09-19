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
    dtheta_rad: float                 # 主轴转动**幅度**（mod π 最小角，非负——报告口径；
                                      # 配准另用带符号的 _signed_axial，R2-115 R组）
    residual_after_rigid: float       # 带方向刚性对齐后 1−IoU；0=纯刚体
    d_elongation: float
    d_bend: float
    d_area_frac: float                # (area_cur − area_prev) / area_prev
    above_water_frac_cur: float | None
    wall_dist_px_cur: float | None
    #: R2-115 T2：frame_prev 与 frame_cur 之间非 observed 的抽样记录条数。
    #: 0 = 相邻 observed；>0 = 这对帧**跨观测缺口**（lost_short/unclear），
    #: 速度/位移不是连续行为量，必须单列，不进连续统计。
    gap_records_between: int = 0


def _wrap_angle(d: float) -> float:
    """主轴无方向性（θ 与 θ+π 等价），差值折到 [0, π/2] 的**幅度**。

    只用于报告 dtheta_rad（转动幅度非负是评审允许的）；**不许**拿它去
    旋转掩膜配准——abs 丢掉了方向，反向旋转会被当正向补（R2-115 R组：
    同一椭圆刚体 −20° 旋转，旧代码残差 0.697 vs +20° 的 0.107）。
    """
    d = abs(d) % np.pi
    return float(min(d, np.pi - d))


def _signed_axial(delta: float) -> float:
    """保留符号的轴向最短角（R2-115 R组）：折到 [−π/2, π/2)。

    主轴 mod π 折叠后，把 prev 转到 cur 的最短旋转是有方向的：
    `((delta + π/2) mod π) − π/2`。幅度与 _wrap_angle 一致
    （|_signed_axial(d)| == _wrap_angle(d)），符号供 _rotate_about 配准用。
    方向约定（在测试中用图像坐标核对，不是只改公式）：_rotate_about(mask,
    d) 使实测主轴角 theta 增加 d（mod π）——即配准应传
    _signed_axial(theta_cur − theta_prev)。
    """
    return float(((delta + np.pi / 2) % np.pi) - np.pi / 2)


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

    Δθ 用**保留符号**的轴向最短角（_signed_axial，R2-115 R组）：
    旋转方向丢了的话，反向转动会被朝错误方向补——同一椭圆刚体旋转
    （无形变）本该只剩最近邻离散化的小残差，旧代码 −20°/−30° 却给出
    0.697/0.778 的系统性大残差，把"方向性缺陷"混进"非刚性信号"。

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
                            _signed_axial(theta_cur - theta_prev))
    inter = int((aligned & b).sum())
    union = int((aligned | b).sum())
    return 1.0 - (inter / union if union else 0.0)


def pair_features(*, frame_prev: int, frame_cur: int, fps: float,
                  mask_prev: np.ndarray, mask_cur: np.ndarray,
                  cent_prev: tuple[float, float], cent_cur: tuple[float, float],
                  theta_prev: float, theta_cur: float,
                  spatial_scale_px: float,
                  above_water_frac_cur: float | None,
                  wall_dist_px_cur: float | None,
                  gap_records_between: int = 0) -> PairFeatures:
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
    if gap_records_between < 0:
        raise ValueError(f"gap_records_between 不能为负: {gap_records_between}")
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
        gap_records_between=gap_records_between,
    )


#: 连续统计覆盖的键（R2-115 T2：只吃 gap_records_between==0 的对）
STAT_KEYS = ("disp_norm", "speed_norm_per_s", "dtheta_rad",
             "residual_after_rigid", "d_area_frac")

#: 跨缺口对在记录里逐条列出的上限（超出只留计数，记录是给人读的）
MAX_CROSS_GAP_ROWS = 50


def summarize(pairs: list[PairFeatures]) -> dict:
    """逐对特征的汇总。连续统计**只吃相邻 observed 对**（R2-115 T2）。

    跨观测缺口的对（lost_short/unclear 记录把两帧隔开）不是连续行为量：
    2 秒缺口两端的"速度"混进了动物在缺口里干什么的未知数。这类对单列
    （缺口条数/帧号/时长逐对记录），不进下面的连续统计——旧版把它们混进
    同一个中位数，缺口越多"速度"越假。

    返回的是研究诊断数：中位数 + p90 + 对数。均值对这种重尾分布没意义，
    但中位数也得带 n——n 太小（<10）时这些数字什么都说明不了。
    """
    cont = [p for p in pairs if p.gap_records_between == 0]
    cross = [p for p in pairs if p.gap_records_between > 0]
    out: dict = {
        "n_pairs": len(pairs),
        "n_pairs_continuous": len(cont),
        "n_pairs_cross_gap": len(cross),
        "local_motion_inside_vs_outside": LOCAL_MOTION_NOT_IMPLEMENTED,
        "local_motion_reason": LOCAL_MOTION_REASON,
    }
    if cross:
        out["cross_gap_pairs"] = {
            "note": "对之间有观测缺口（lost_short/unclear 记录）：不进连续统计，"
                    "缺口逐对列出（条数上限 %d）" % MAX_CROSS_GAP_ROWS,
            "gaps": [{"frame_prev": p.frame_prev, "frame_cur": p.frame_cur,
                      "gap_records": p.gap_records_between, "dt_s": p.dt_s}
                     for p in cross[:MAX_CROSS_GAP_ROWS]],
            "rows_omitted": max(0, len(cross) - MAX_CROSS_GAP_ROWS),
        }
    if not cont:
        out["statistics_apply_to"] = None
        out["stats_note"] = "没有相邻 observed 对：连续统计不输出（不填 0）"
        for key in STAT_KEYS:
            out[key] = None
        return out

    def stat(vals: list[float]) -> dict:
        a = np.asarray(vals, dtype=float)
        return {"median": float(np.median(a)),
                "p90": float(np.percentile(a, 90)),
                "max": float(a.max())}

    out["statistics_apply_to"] = "continuous_pairs_only"
    for key in STAT_KEYS:
        out[key] = stat([getattr(p, key) for p in cont])
    return out
