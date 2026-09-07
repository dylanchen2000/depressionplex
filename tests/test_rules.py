"""TST 事件判定测试：组合器、时间过滤、五个事件在合成序列上的行为。

合成序列复用 synth.py：pendulum = 纯刚体钟摆（被动摆），articulated =
躯干不动、肢体摆动（主动）。TST 悬挂 = 尾在上：hind 侧 = 离悬挂点近的主轴段。
"""

from __future__ import annotations

import numpy as np

from depressionplex.assay_core import rad, rules as R
from synth import draw_body, pendulum_series


def test_combinators() -> None:
    a = np.array([True, True, False, False])
    b = np.array([True, False, True, False])
    assert R.AND(a, b).tolist() == [True, False, False, False]
    assert R.OR(a, b).tolist() == [True, True, True, False]
    assert R.NOT(a).tolist() == [False, False, True, True]
    assert R.N_OF_M(2, a, b, a).tolist() == [True, True, False, False]
    assert R.N_OF_M(1, a, b).tolist() == [True, True, True, False]


def test_temporal_fill_and_min_run() -> None:
    p = np.array([1, 1, 0, 1, 1, 0, 0, 0, 1, 1, 1, 1], dtype=bool)
    # 空洞 1 行被填，3 行不填；然后短段（<4）清除
    filled = R.fill_gaps(p, max_gap=1)
    assert filled.tolist() == [1, 1, 1, 1, 1, 0, 0, 0, 1, 1, 1, 1]
    out = R.temporal(p, min_len=4, max_gap=1)
    # 填洞后两段长度 5 与 4，均 ≥ min_len
    assert out.tolist() == [1, 1, 1, 1, 1, 0, 0, 0, 1, 1, 1, 1]


def test_pendulum_is_passive_swing_and_immobility() -> None:
    """纯刚体钟摆：关节残差≈0、角速度周期 ⇒ PassiveSwing + Immobility，
    且**不是** Mobility（金标准：钟摆不计入 mobility）。

    75 帧 3 个周期 = 1.0 Hz @25fps（真实单摆 ≈1.1–1.25 Hz，同量级）。
    pendulum_series 固定单周期（75 帧=0.33Hz），故这里直接用 draw_body 造。
    """
    pivot = (100.0, 20.0)
    arm = 90.0
    masks = []
    for i in range(75):
        swing = np.deg2rad(20.0) * np.sin(2 * np.pi * 3 * i / 75)
        masks.append(
            draw_body(
                (200, 200),
                (pivot[0] + arm * np.sin(swing), pivot[1] + arm * np.cos(swing)),
                np.pi / 2 + swing,
                60.0,
                22.0,
            )
        )
    f = R.build_tst_features(masks, suspension=pivot, fps=25.0)
    lab = R.label_tst_events(f)
    assert lab.passive_swing[10:45].any(), "钟摆应被判为 PassiveSwing"
    assert lab.immobility[10:45].any()
    assert not lab.mobility.any(), "钟摆不得计入 Mobility"
    assert not lab.tail_climbing.any()


def test_articulated_hind_limb_is_mobility() -> None:
    """肢体在尾侧（近悬挂点）摆动 ⇒ Mobility。"""
    masks = [
        draw_body(
            (200, 200),
            (100.0, 120.0),
            -np.pi / 2,          # 主轴朝上：肢体端 = 尾侧
            60.0,
            22.0,
            limb_angle=np.deg2rad(55) * np.sin(2 * np.pi * 2 * i / 50),
            limb_len=34.0,
            limb_width=12.0,
        )
        for i in range(50)
    ]
    f = R.build_tst_features(masks, suspension=(100.0, 20.0), fps=25.0)
    lab = R.label_tst_events(f)
    assert lab.mobility[10:45].any(), "尾侧肢体主动摆动应计入 Mobility"
    assert not lab.forelimb_only.any()
    assert not lab.immobility[10:45].any()


def test_articulated_fore_limb_is_forelimb_only() -> None:
    """肢体在头侧（远悬挂点）摆动 ⇒ Forelimb-only，**不**计入 Mobility。"""
    masks = [
        draw_body(
            (200, 200),
            (100.0, 120.0),
            np.pi / 2,           # 主轴朝下：肢体端 = 头侧（前肢）
            60.0,
            22.0,
            limb_angle=np.deg2rad(55) * np.sin(2 * np.pi * 2 * i / 50),
            limb_len=34.0,
            limb_width=12.0,
        )
        for i in range(50)
    ]
    f = R.build_tst_features(masks, suspension=(100.0, 20.0), fps=25.0)
    lab = R.label_tst_events(f)
    assert lab.forelimb_only[10:45].any(), "仅前肢动作应显式标为 ForelimbOnly"
    assert not lab.mobility.any(), "仅前肢动作不得计入 Mobility（金标准）"


def test_tail_climbing_on_rising_ring() -> None:
    """带孔剪影（前爪抓尾）+ 质心持续上移 ⇒ TailClimbing。"""
    shape = (300, 80)
    masks = []
    for i in range(60):
        m = np.zeros(shape, dtype=bool)
        cy = 220 - 2 * i          # 匀速上移
        yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
        d = np.hypot(xx - 40, yy - cy)
        m |= (d <= 25) & (d >= 10)   # 带孔环 = 攀爬闭环
        masks.append(m)
    f = R.build_tst_features(masks, suspension=(40.0, 20.0), fps=25.0)
    lab = R.label_tst_events(f, bl=40.0)
    assert lab.tail_climbing[15:50].any(), "孔洞+持续上移应判 TailClimbing"

    # 同样的环但不上移 ⇒ 不判攀爬
    still_masks = []
    for i in range(60):
        m = np.zeros(shape, dtype=bool)
        yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
        d = np.hypot(xx - 40, yy - 200)
        m |= (d <= 25) & (d >= 10)
        still_masks.append(m)
    f2 = R.build_tst_features(still_masks, suspension=(40.0, 20.0), fps=25.0)
    lab2 = R.label_tst_events(f2, bl=40.0)
    assert not lab2.tail_climbing.any(), "不上移不得判攀爬"


def test_unknown_frames_not_labeled() -> None:
    masks = pendulum_series(n_frames=50)
    f = R.build_tst_features(masks, suspension=(100.0, 20.0), fps=25.0)
    assert f.unknown[0], "lag1 首帧应记 unknown"
    lab = R.label_tst_events(f)
    for series in lab.as_dict().values():
        assert not series[0], "unknown 帧不得判入任何事件"


def test_summarize_produces_bout_stats() -> None:
    masks = pendulum_series(n_frames=50)
    f = R.build_tst_features(masks, suspension=(100.0, 20.0), fps=25.0)
    lab = R.label_tst_events(f)
    s = R.summarize_tst(lab, fps=25.0)
    assert "Immobility" in s and "Mobility" in s
    assert s["Immobility"].frames > 0


def test_swing_gap_scales_with_fps() -> None:
    """gap = 半个钟摆周期：随帧率缩放；估不出主频用兜底。"""
    assert R.swing_gap_frames(25.0, 1.2, 99) == 10   # 25/(2×1.2)≈10.4
    assert R.swing_gap_frames(50.0, 1.2, 99) == 21   # 同摆,帧率翻倍 gap 翻倍
    assert R.swing_gap_frames(25.0, None, 12) == 12  # 无主频 → 兜底


def _pendulum_masks(n: int, cycles: int, amp_deg: float = 20.0):
    pivot = (100.0, 20.0)
    arm = 90.0
    out = []
    for i in range(n):
        swing = np.deg2rad(amp_deg) * np.sin(2 * np.pi * cycles * i / n)
        out.append(
            draw_body(
                (200, 200),
                (pivot[0] + arm * np.sin(swing), pivot[1] + arm * np.cos(swing)),
                np.pi / 2 + swing,
                60.0,
                22.0,
            )
        )
    return out, pivot


def test_passive_swing_is_fps_invariant() -> None:
    """同一 1 Hz 物理钟摆：25 fps 与 50 fps 采样都应判 PassiveSwing。

    过零间隙随帧率翻倍；固定帧数 gap 会在高帧率下把事件切碎，
    周期归一化后不变（时间轴归一化 = 空间 BL 归一化的对应物）。
    """
    m25, piv = _pendulum_masks(75, 3)     # 3 周期 / 75 帧 @25fps = 1 Hz
    lab25 = R.label_tst_events(
        R.build_tst_features(m25, suspension=piv, fps=25.0))
    assert lab25.passive_swing.any()

    m50, piv = _pendulum_masks(150, 3)    # 同摆 @50fps
    lab50 = R.label_tst_events(
        R.build_tst_features(m50, suspension=piv, fps=50.0))
    assert lab50.passive_swing.any(), "高帧率下周期归一化 gap 应保住事件"


def test_features_bl_injection_matches_internal_estimate() -> None:
    """注入 `bl` 必须与不注入**逐位相同**（DP-058 的兼容性锚）。

    `build_tst_features(bl=...)` 加进来的唯一目的是省掉重复计算并让分母可审计，
    **不是改判据**。所以缺省路径与显式传 `trial_body_length` 必须一字不差；
    这条一旦红，说明有人顺手改了归一化，而不是接了个参数。
    """
    masks, piv = _pendulum_masks(75, 3)
    bl = rad.trial_body_length(masks)
    assert bl > 0
    a = R.build_tst_features(masks, suspension=piv, fps=25.0)
    b = R.build_tst_features(masks, suspension=piv, fps=25.0, bl=bl)
    np.testing.assert_array_equal(np.isnan(a.residual), np.isnan(b.residual))
    m = ~np.isnan(a.residual)
    np.testing.assert_array_equal(a.residual[m], b.residual[m])
    np.testing.assert_array_equal(a.rho_hind[~np.isnan(a.rho_hind)],
                                  b.rho_hind[~np.isnan(b.rho_hind)])


def test_features_bl_scales_residual_quadratically() -> None:
    """分母是 BL²：BL 折半 ⇒ 残差 ×4。这条证明这个旋钮**是活的**。

    为什么要单独钉住：DP-058 第一版探针把 BL 在 ×0.70…×1.30 之间扰了一遍，
    immobility 全幅摆动 **0.0 s**——因为它扰的是 `label_tst_events(bl=)`
    （只管爬尾巴，本批 0 段），而真正的分母在 `decompose_series` 里自己算。
    有了这条测试，那种"扰了个不存在的旋钮"的探针一开跑就能被识破。
    """
    masks, piv = _pendulum_masks(75, 3)
    bl = rad.trial_body_length(masks)
    base = R.build_tst_features(masks, suspension=piv, fps=25.0, bl=bl)
    half = R.build_tst_features(masks, suspension=piv, fps=25.0, bl=bl / 2.0)
    m = ~np.isnan(base.residual) & ~np.isnan(half.residual)
    assert m.sum() > 0
    np.testing.assert_allclose(half.residual[m], base.residual[m] * 4.0,
                               rtol=1e-9, atol=0.0)
