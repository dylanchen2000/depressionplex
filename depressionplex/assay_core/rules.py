"""TST 事件判定：轮廓/RAD 特征 → 逐帧事件（L1 + L2）。

规划文档 §3.2 的事件体系落地。结构（也是 Shared Assay Core 明确缺的
AND/OR/N-of-M 组合语义，§4.10.1）：

    原子谓词（对特征序列的逐帧布尔）
      → 组合器（AND / OR / N-of-M / NOT）
      → 时间过滤（最短持续 + 允许短暂丢失）
      → 事件（逐帧布尔序列）
      → bouts.py 的 CSI 兼容流水线出 bout 统计

纪律（沿用全项目）：
- 特征为 NaN（lag 越界、剪影缺失、方向判不出）⇒ 谓词为 False 且计入
  `unknown`，事件不猜、不插值。
- 阈值均有物理单位或标定来源；暂标定值明确注释为"起点值"。
- Mobility 与 Immobility 互斥；Forelimb-only 是"在动但不计入 mobility"
  （金标准：仅前肢小动作不计），不并入 L1 任何一类。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import silhouette as sil
from . import rad
from . import bouts

# ---- 组合器 ------------------------------------------------------------------


def AND(*ps: np.ndarray) -> np.ndarray:
    out = np.ones_like(np.asarray(ps[0], dtype=bool))
    for p in ps:
        out &= np.asarray(p, dtype=bool)
    return out


def OR(*ps: np.ndarray) -> np.ndarray:
    out = np.zeros_like(np.asarray(ps[0], dtype=bool))
    for p in ps:
        out |= np.asarray(p, dtype=bool)
    return out


def NOT(p: np.ndarray) -> np.ndarray:
    return ~np.asarray(p, dtype=bool)


def N_OF_M(n: int, *ps: np.ndarray) -> np.ndarray:
    """至少 n 个谓词同时成立。"""
    acc = np.zeros(len(np.asarray(ps[0])), dtype=int)
    for p in ps:
        acc += np.asarray(p, dtype=bool).astype(int)
    return acc >= n


def fill_gaps(p: np.ndarray, max_gap: int) -> np.ndarray:
    """长度 ≤ max_gap 的 False 空洞填为 True（允许短暂丢失）。"""
    p = np.asarray(p, dtype=bool).copy()
    if max_gap <= 0 or not p.any():
        return p
    idx = np.flatnonzero(p)
    for a, b in zip(idx[:-1], idx[1:]):
        if 0 < b - a - 1 <= max_gap:
            p[a + 1 : b] = True
    return p


def min_run(p: np.ndarray, min_len: int) -> np.ndarray:
    """短于 min_len 的 True 段整段清除（最短持续）。"""
    p = np.asarray(p, dtype=bool)
    if min_len <= 1:
        return p.copy()
    # 直接扫描，避免边界技巧出错
    out = np.zeros_like(p)
    i = 0
    n = len(p)
    while i < n:
        if p[i]:
            j = i
            while j + 1 < n and p[j + 1]:
                j += 1
            if j - i + 1 >= min_len:
                out[i : j + 1] = True
            i = j + 1
        else:
            i += 1
    return out


def temporal(p: np.ndarray, *, min_len: int, max_gap: int = 0) -> np.ndarray:
    """先填空洞、再过滤短段。"""
    return min_run(fill_gaps(p, max_gap), min_len)


# ---- 特征 --------------------------------------------------------------------


@dataclass
class TstFeatures:
    """逐帧特征序列。长度 = 帧数；不可算的帧为 NaN。"""

    residual: np.ndarray      # lag1 XOR 关节残差（BL² 归一化）
    omega: np.ndarray         # lag1 刚体旋转角速度（弧度/帧）
    rho_hind: np.ndarray      # 尾侧（近悬挂点）段残差占比 ∈[0,1]
    hole_count: np.ndarray    # 剪影孔洞数（尾攀爬拓扑信号）
    centroid_dy: np.ndarray   # 质心 y − 悬挂点 y（像素；向下为正）
    fps: float

    @property
    def unknown(self) -> np.ndarray:
        return (
            np.isnan(self.residual)
            | np.isnan(self.omega)
            | np.isnan(self.rho_hind)
        )


def hind_index_for_frame(
    mask: np.ndarray, suspension_y: float
) -> int | None:
    """TST 尾侧 = 离悬挂点更近的主轴分段。判不出返回 None。"""
    segs = sil.axis_segments(mask, 2)
    c0 = sil.metrics(segs[0], with_holes=False)
    c1 = sil.metrics(segs[1], with_holes=False)
    if c0 is None or c1 is None or c0.area <= 0 or c1.area <= 0:
        return None
    d0 = abs(c0.centroid[1] - suspension_y)
    d1 = abs(c1.centroid[1] - suspension_y)
    return 0 if d0 <= d1 else 1


def build_tst_features(
    masks: list[np.ndarray],
    *,
    suspension: tuple[float, float],
    fps: float,
) -> TstFeatures:
    """从剪影序列构建逐帧特征。悬挂点用几何语义图（geometry.suspension_point）。"""
    rows = rad.decompose_series(masks)
    n = len(masks)
    residual = np.full(n, np.nan)
    omega = np.full(n, np.nan)
    rho_hind = np.full(n, np.nan)
    hole_count = np.zeros(n, dtype=int)
    centroid_dy = np.full(n, np.nan)

    for i, row in enumerate(rows):
        if row.get("residual_lag1") is not None:
            residual[i] = row["residual_lag1"]
            omega[i] = row["omega_lag1"]
        m = masks[i]
        met = sil.metrics(m, with_holes=True)
        if met is not None:
            hole_count[i] = met.hole_count
            centroid_dy[i] = met.centroid[1] - suspension[1]
        s0 = row.get("seg0_lag1")
        s1 = row.get("seg1_lag1")
        if s0 is not None and s1 is not None and (s0 + s1) > 0:
            hi = hind_index_for_frame(m, suspension[1])
            if hi is not None:
                rho_hind[i] = (s0 if hi == 0 else s1) / (s0 + s1)

    return TstFeatures(
        residual=residual,
        omega=omega,
        rho_hind=rho_hind,
        hole_count=hole_count,
        centroid_dy=centroid_dy,
        fps=fps,
    )


# ---- 参数与事件 ----------------------------------------------------------------


@dataclass
class TstRulesParams:
    """阈值起点值。

    **θ_mob 冻结（评审护栏，2026-08-24）**：provisional 值，只许用双人 ethogram
    重标定；**禁止用本系统自己的输出调它**——拿模型输出拟合模型阈值是循环
    论证。表面效度（挣扎期 Mobility 主导等）不是精度，分不出阈值偏敏感与否。
    """

    theta_mob: float = 0.0175          # FROZEN provisional：0.015–0.02 区间中点
                                       # （合成标定 + 真实静止/活动交叉验证）
    theta_hind: float = 0.30           # 尾侧段残差占比下限（金标准"仅前肢不计"）
    omega_min: float = 1.25            # 被动摆角速度下限（弧度/**秒**）。
                                       # 每帧角速度随帧率缩小，阈值必须用秒单位
                                       # （= 旧 0.05 弧度/帧 @25fps），否则换帧率失灵
    swing_band_hz: tuple[float, float] = (0.4, 2.5)  # 单摆频带（自适应中心在其内）
    swing_max_gap_frames: int = 12     # 估不出钟摆周期时的兜底 gap（帧）
    min_event_frames: int = 25         # 事件最短持续 1 s @25fps
    max_gap_frames: int = 5            # 允许短暂丢失
    climb_rise_frac: float = 0.3       # 攀爬：事件内质心相对悬挂点上移 ≥ 0.3×BL 趋势


def swing_gap_frames(fps: float, f_star: float | None, fallback: int) -> int:
    """Passive Swing 事件的时间过滤 gap = **半个估计钟摆周期**（帧数）。

    钟摆速度过零是固有物理（每周期两次），事件必须跨过这些零点；过零间隙
    随帧率与体重（摆长）变化，写帧数常数会换条件就失灵。空间用 BL 归一化，
    时间用钟摆周期归一化——同一道理换到时间轴（评审推论，2026-08-24）。
    估不出主频时用兜底值。
    """
    if f_star is not None and f_star > 0:
        return max(1, int(round(fps / (2.0 * f_star))))
    return fallback


def dominant_frequency(x: np.ndarray, fps: float) -> float | None:
    """序列主频（Hz）。常值/空序列返回 None。"""
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    if x.size < 8:
        return None
    x = x - x.mean()
    if np.abs(x).max() < 1e-9:
        return None
    spec = np.abs(np.fft.rfft(x)) ** 2
    freqs = np.fft.rfftfreq(x.size, d=1.0 / fps)
    keep = freqs > 0
    if not keep.any():
        return None
    return float(freqs[keep][int(np.argmax(spec[keep]))])


@dataclass
class TstEventLabels:
    """逐帧事件布尔序列 + unknown。事件间不互斥的允许共存（L2 细化 L1）。"""

    mobility: np.ndarray
    immobility: np.ndarray
    passive_swing: np.ndarray
    tail_climbing: np.ndarray
    forelimb_only: np.ndarray
    unknown: np.ndarray

    def as_dict(self) -> dict[str, np.ndarray]:
        return {
            "Mobility": self.mobility,
            "Immobility": self.immobility,
            "PassiveSwing": self.passive_swing,
            "TailClimbing": self.tail_climbing,
            "ForelimbOnly": self.forelimb_only,
        }


def label_tst_events(
    f: TstFeatures,
    params: TstRulesParams | None = None,
    *,
    bl: float | None = None,
) -> TstEventLabels:
    """逐帧判定 TST 事件。bl 用于攀爬上升量的归一化（缺省用质心位移自身尺度）。"""
    p = params or TstRulesParams()
    n = len(f.residual)
    zero = np.zeros(n, dtype=bool)

    residual_ok = ~np.isnan(f.residual)
    moving = AND(residual_ok, f.residual >= p.theta_mob)
    still = AND(residual_ok, f.residual < p.theta_mob)
    hind_ok = ~np.isnan(f.rho_hind)

    # L1 Mobility：在动 且 尾侧（后肢）参与（金标准"仅前肢不计"）。
    mobility_raw = AND(moving, hind_ok, f.rho_hind >= p.theta_hind)
    # Forelimb-only：在动 但 尾侧参与不足——显式单列，**不**并入 mobility。
    forelimb_raw = AND(moving, hind_ok, f.rho_hind < p.theta_hind)

    # L2 Passive Swing：刚体摆得快、关节残差低，且主频落在单摆频带。
    swing_raw = AND(
        still,
        ~np.isnan(f.omega),
        np.abs(f.omega) * f.fps >= p.omega_min,   # 每帧角速度 → 秒单位
    )
    f_star: float | None = None
    if swing_raw.any():
        # 主频用全序列算：幅值门控会只采到速度峰、把频谱搬到二倍频。
        f_star = dominant_frequency(f.omega, f.fps)
        if f_star is None or not (
            p.swing_band_hz[0] <= f_star <= p.swing_band_hz[1]
        ):
            swing_raw = zero.copy()  # 频带不符 ⇒ 不是钟摆，拒绝整段
            f_star = None

    # L2 Tail Climbing：拓扑闭环 且 质心相对悬挂点持续上移。
    holes = f.hole_count >= 1
    rising = zero.copy()
    dy = f.centroid_dy
    if bl is not None and bl > 0:
        win = p.min_event_frames
        for i in range(n):
            j = min(i + win, n)
            a = max(i - win, 0)
            past = dy[max(a, 0) : i + 1]
            future = dy[i:j]
            past = past[~np.isnan(past)]
            future = future[~np.isnan(future)]
            if past.size and future.size:
                # 未来窗质心比过去窗更靠上（dy 更小）≥ climb_rise_frac × BL
                rising[i] = (np.median(past) - np.median(future)) >= (
                    p.climb_rise_frac * bl
                )
    climb_raw = AND(holes, rising)

    def ev(raw: np.ndarray) -> np.ndarray:
        return temporal(
            raw, min_len=p.min_event_frames, max_gap=p.max_gap_frames
        )

    # 各事件只依赖自己所需的特征：raw 谓词里 NaN 比较天然为 False，
    # 故"特征缺失 ⇒ 该事件不成立"已内含；不能用全局 unknown 一刀切
    # （钟摆序列 rho_hind 全 NaN，Immobility 仍必须成立）。
    mobility = ev(mobility_raw)
    forelimb = ev(forelimb_raw)
    passive = temporal(
        swing_raw,
        min_len=p.min_event_frames,
        max_gap=swing_gap_frames(f.fps, f_star, p.swing_max_gap_frames),
    )
    climb = ev(climb_raw)
    # L1 Immobility：残差低即计入（含被动摆动，与 CSI UseMotionComp 口径一致）。
    immobility = AND(ev(still), ~mobility)

    return TstEventLabels(
        mobility=mobility,
        immobility=immobility,
        passive_swing=passive,
        tail_climbing=climb,
        forelimb_only=forelimb,
        unknown=f.unknown,
    )


def summarize_tst(
    labels: TstEventLabels,
    *,
    fps: float,
    params: bouts.BoutParams | None = None,
) -> dict[str, bouts.BoutSummary]:
    """逐帧事件 → CSI 兼容 bout 统计（Bouts / Duration / Duration% + 潜伏期）。"""
    bp = params or bouts.CSI_TST_STARTING_POINT
    out: dict[str, bouts.BoutSummary] = {}
    for name, series in labels.as_dict().items():
        lab = np.where(series, name, None)
        out[name] = bouts.score(list(lab), name, params=bp, fps=fps)
    return out
