"""剪影（silhouette）几何量。纯 numpy 实现，不依赖 OpenCV。

核心量都以体长 BL 归一化，使阈值跨分辨率、跨体型可迁移——这是对 CSI
"pure relative measure，需逐视频试错" 的正面修正。

坐标约定：mask 为 2D 布尔/0-1 数组，索引 [row, col] = [y, x]。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

# 计算 BL / 宽度时用的鲁棒分位，避免单像素噪声拉长体长。
_EXTENT_LO = 1.0
_EXTENT_HI = 99.0


@dataclass(frozen=True)
class SilhouetteMetrics:
    """单帧剪影的几何量。长度单位为像素，面积为像素²。"""

    area: float
    centroid: tuple[float, float]  # (x, y)
    theta: float  # 主轴方向，弧度，无方向性（theta 与 theta+pi 等价）
    body_length: float  # 沿主轴的鲁棒跨度 = BL
    body_width: float  # 沿次轴的鲁棒跨度
    elongation: float  # BL / width
    bend: float  # 中轴线偏离直线的最大幅度 / BL
    hole_count: int  # 剪影内部孔洞数（尾巴攀爬的拓扑信号）

    @property
    def bl(self) -> float:
        return self.body_length


def _principal_axis(xs: np.ndarray, ys: np.ndarray) -> tuple[float, float, float]:
    """返回 (cx, cy, theta)。theta 为主轴与 x 轴夹角，无方向性。"""
    cx = float(xs.mean())
    cy = float(ys.mean())
    dx = xs - cx
    dy = ys - cy
    mu20 = float((dx * dx).mean())
    mu02 = float((dy * dy).mean())
    mu11 = float((dx * dy).mean())
    theta = 0.5 * float(np.arctan2(2.0 * mu11, mu20 - mu02))
    return cx, cy, theta


def _project(
    xs: np.ndarray, ys: np.ndarray, cx: float, cy: float, theta: float
) -> tuple[np.ndarray, np.ndarray]:
    """把像素投影到主轴坐标系。u 沿主轴，v 沿次轴。"""
    c, s = np.cos(theta), np.sin(theta)
    dx = xs - cx
    dy = ys - cy
    u = dx * c + dy * s
    v = -dx * s + dy * c
    return u, v


def _robust_extent(vals: np.ndarray) -> float:
    if vals.size == 0:
        return 0.0
    lo, hi = np.percentile(vals, [_EXTENT_LO, _EXTENT_HI])
    return float(hi - lo)


def _bend(u: np.ndarray, v: np.ndarray, bl: float, bins: int = 9) -> float:
    """中轴线弯曲度：把 u 分箱，取每箱 v 的均值，量其偏离直线的最大幅度 / BL。"""
    if bl <= 0 or u.size < bins * 2:
        return 0.0
    edges = np.linspace(u.min(), u.max(), bins + 1)
    idx = np.clip(np.digitize(u, edges[1:-1]), 0, bins - 1)
    centers = np.full(bins, np.nan)
    for b in range(bins):
        sel = idx == b
        if sel.any():
            centers[b] = v[sel].mean()
    ok = ~np.isnan(centers)
    if ok.sum() < 3:
        return 0.0
    xs = np.arange(bins, dtype=float)[ok]
    ys = centers[ok]
    # 减去线性趋势后的最大绝对偏离，即"非直"的程度
    slope, intercept = np.polyfit(xs, ys, 1)
    resid = ys - (slope * xs + intercept)
    return float(np.abs(resid).max() / bl)


def count_holes(mask: np.ndarray) -> int:
    """数剪影内部孔洞（不与外框连通的背景连通域）。

    TST 中前爪抓住尾巴形成闭环时，剪影会出现孔洞——这是尾巴攀爬的拓扑信号，
    比"前爪-尾距 < 阈值"更不依赖关键点。
    """
    m = np.asarray(mask, dtype=bool)
    if m.size == 0 or not m.any():
        return 0
    # padding 一圈背景，保证外部背景连通且可从 (0,0) 出发
    bg = ~np.pad(m, 1, constant_values=False)
    h, w = bg.shape
    seen = np.zeros_like(bg)
    dq = deque([(0, 0)])
    seen[0, 0] = True
    while dq:
        r, c = dq.popleft()
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nr, nc = r + dr, c + dc
            if 0 <= nr < h and 0 <= nc < w and bg[nr, nc] and not seen[nr, nc]:
                seen[nr, nc] = True
                dq.append((nr, nc))

    holes = 0
    todo = bg & ~seen
    while todo.any():
        r, c = map(int, np.argwhere(todo)[0])
        holes += 1
        dq = deque([(r, c)])
        todo[r, c] = False
        while dq:
            rr, cc = dq.popleft()
            for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nr, nc = rr + dr, cc + dc
                if 0 <= nr < h and 0 <= nc < w and todo[nr, nc]:
                    todo[nr, nc] = False
                    dq.append((nr, nc))
    return holes


def metrics(mask: np.ndarray, *, with_holes: bool = True) -> SilhouetteMetrics | None:
    """计算单帧剪影几何量。空掩膜返回 None（上层应记为 unknown，不要补 0）。"""
    m = np.asarray(mask, dtype=bool)
    ys, xs = np.nonzero(m)
    if xs.size == 0:
        return None
    xs = xs.astype(float)
    ys = ys.astype(float)
    cx, cy, theta = _principal_axis(xs, ys)
    u, v = _project(xs, ys, cx, cy, theta)
    bl = _robust_extent(u)
    bw = _robust_extent(v)
    return SilhouetteMetrics(
        area=float(xs.size),
        centroid=(cx, cy),
        theta=theta,
        body_length=bl,
        body_width=bw,
        elongation=float(bl / bw) if bw > 0 else float("inf"),
        bend=_bend(u, v, bl),
        hole_count=count_holes(m) if with_holes else 0,
    )


def axis_segments(mask: np.ndarray, n: int = 2) -> list[np.ndarray]:
    """沿身体长轴把剪影切成 n 段，返回 n 个布尔掩膜。

    这是对 CSI 固定水平 Split Level 的替代：分段随姿态自适应，动物直立、
    攀爬、倒挂都不会错分。段序沿主轴正方向（无方向性，故"前/后"归属需由
    Pose 或颜色头尾辅助确定，见 L2'）。
    """
    m = np.asarray(mask, dtype=bool)
    ys, xs = np.nonzero(m)
    if xs.size == 0:
        return [np.zeros_like(m) for _ in range(n)]
    cx, cy, theta = _principal_axis(xs.astype(float), ys.astype(float))
    u, _ = _project(xs.astype(float), ys.astype(float), cx, cy, theta)
    lo, hi = np.percentile(u, [_EXTENT_LO, _EXTENT_HI])
    if hi <= lo:
        edges = np.linspace(u.min(), u.max() + 1e-9, n + 1)
    else:
        edges = np.linspace(lo, hi, n + 1)
    edges[0] = -np.inf
    edges[-1] = np.inf

    out = []
    for k in range(n):
        sel = (u >= edges[k]) & (u < edges[k + 1])
        seg = np.zeros_like(m)
        seg[ys[sel], xs[sel]] = True
        out.append(seg)
    return out


def above_below(mask: np.ndarray, y_split: float) -> tuple[np.ndarray, np.ndarray]:
    """按一条水平线把剪影切成上/下两部分。

    用于 FST 的水面线分区（水上/水下成像特性完全不同，CSI 用 Above/Under
    Water Contrast 两个参数处理，这个设计是对的，予以保留）。
    """
    m = np.asarray(mask, dtype=bool)
    rows = np.arange(m.shape[0])[:, None]
    above = m & (rows < y_split)
    below = m & (rows >= y_split)
    return above, below
