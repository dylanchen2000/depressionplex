"""合成剪影生成器。用于在没有真实视频时验证 RAD 的正确性。

关键设计：剪影用解析方式直接渲染（而非把图像 warp 过去），这样"纯刚体运动"
的两帧之间不含任何重采样误差，残差应当只反映真实的形变。否则测试会把
重采样噪声误当成关节运动，测不出东西。
"""

from __future__ import annotations

import numpy as np


def draw_body(
    shape: tuple[int, int],
    center: tuple[float, float],
    theta: float,
    body_length: float,
    body_width: float,
    *,
    limb_angle: float | None = None,
    limb_len: float = 0.0,
    limb_width: float = 0.0,
) -> np.ndarray:
    """渲染一个鼠形剪影：椭圆躯干 + 可选的一条肢体。

    center/theta/body_* 描述躯干（刚体自由度）。
    limb_angle 是肢体相对躯干长轴的夹角（关节自由度）——只动它就是纯关节运动。
    """
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    cx, cy = center
    c, s = np.cos(theta), np.sin(theta)

    dx = xx - cx
    dy = yy - cy
    u = dx * c + dy * s
    v = -dx * s + dy * c

    a = max(body_length / 2.0, 1e-6)
    b = max(body_width / 2.0, 1e-6)
    mask = (u / a) ** 2 + (v / b) ** 2 <= 1.0

    if limb_angle is not None and limb_len > 0 and limb_width > 0:
        # 肢体根部固定在躯干长轴的正端附近
        root_u = a * 0.6
        root_v = 0.0
        la, lb = limb_len / 2.0, limb_width / 2.0
        lc, ls = np.cos(limb_angle), np.sin(limb_angle)
        # 肢体中心 = 根部 + 沿肢体方向半长
        mid_u = root_u + la * lc
        mid_v = root_v + la * ls
        pu = u - mid_u
        pv = v - mid_v
        qu = pu * lc + pv * ls
        qv = -pu * ls + pv * lc
        mask |= (qu / la) ** 2 + (qv / lb) ** 2 <= 1.0

    return mask


def pendulum_series(
    shape: tuple[int, int] = (200, 200),
    pivot: tuple[float, float] = (100.0, 20.0),
    arm: float = 90.0,
    amplitude_deg: float = 20.0,
    n_frames: int = 24,
    body_length: float = 60.0,
    body_width: float = 22.0,
) -> list[np.ndarray]:
    """纯刚体钟摆：动物绕悬挂点整体摆动，躯干不发生任何形变。

    这模拟 TST 中「因先前挣扎的惯性产生的钟摆式摆动」。金标准要求它**不**计入
    mobility，所以 RAD 的关节残差在这段序列上必须接近零。
    """
    out = []
    for i in range(n_frames):
        phase = 2.0 * np.pi * i / n_frames
        swing = np.deg2rad(amplitude_deg) * np.sin(phase)
        # 悬挂：躯干长轴始终沿摆杆方向，故 theta 随摆角同步变化（纯刚体）
        theta = np.pi / 2 + swing
        cx = pivot[0] + arm * np.sin(swing)
        cy = pivot[1] + arm * np.cos(swing)
        out.append(
            draw_body(shape, (cx, cy), theta, body_length, body_width)
        )
    return out


def articulated_series(
    shape: tuple[int, int] = (200, 200),
    center: tuple[float, float] = (100.0, 110.0),
    theta: float = np.pi / 2,
    amplitude_deg: float = 55.0,
    n_frames: int = 24,
    body_length: float = 60.0,
    body_width: float = 22.0,
    limb_len: float = 34.0,
    limb_width: float = 12.0,
) -> list[np.ndarray]:
    """纯关节运动：躯干完全不动，只有一条肢体在摆动。

    这模拟主动挣扎。RAD 的关节残差在这段序列上必须显著大于钟摆序列。
    """
    out = []
    for i in range(n_frames):
        phase = 2.0 * np.pi * i / n_frames
        limb = np.deg2rad(amplitude_deg) * np.sin(phase)
        out.append(
            draw_body(
                shape,
                center,
                theta,
                body_length,
                body_width,
                limb_angle=limb,
                limb_len=limb_len,
                limb_width=limb_width,
            )
        )
    return out


def ring(shape: tuple[int, int] = (80, 80), r_out: float = 30.0, r_in: float = 14.0):
    """带一个孔的剪影。用于验证 count_holes——尾巴攀爬的拓扑信号。"""
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    d = np.hypot(xx - w / 2.0, yy - h / 2.0)
    return (d <= r_out) & (d >= r_in)
