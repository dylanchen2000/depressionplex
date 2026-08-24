"""RAD 测试。核心通过门：纯刚体钟摆的关节残差必须接近零。

这条测试就是整个方案成立与否的判据。CSI 的标量 blob 运动量在钟摆序列上会给出
很大的运动值（因而把被动摆动误计为 mobility）；我们必须做到接近零。
"""

from __future__ import annotations

import numpy as np

from depressionplex.assay_core import rad as R
from depressionplex.assay_core import silhouette as sil

import synth


def _residuals(masks: list[np.ndarray], lag: int = 1) -> np.ndarray:
    """用试次级 BL 归一化——与正式分析口径一致。"""
    bl = R.trial_body_length(masks)
    out = []
    for i in range(lag, len(masks)):
        res = R.decompose(masks[i - lag], masks[i], lag=lag, bl=bl)
        assert res is not None
        out.append(res.residual)
    return np.asarray(out)


def test_pendulum_residual_near_zero() -> None:
    """纯刚体摆动 → 关节残差接近零，但刚体旋转量显著非零。

    2026-08-24 实测基线：mean=0.0070 max=0.0103（BL² 归一化）。
    这个量级就是离散化噪声底，也是设 θ_mob 的参考下限。
    阈值刻意贴近实测值，任何抬高噪声底的改动都应让本测试失败。
    """
    masks = synth.pendulum_series()
    res = _residuals(masks)
    mean_res = float(np.mean(res))
    max_res = float(np.max(res))

    assert mean_res < 0.012, f"钟摆平均残差过大: {mean_res:.4f}（基线 0.0070）"
    assert max_res < 0.015, f"钟摆最大残差过大: {max_res:.4f}（基线 0.0103）"

    # 同时必须确认刚体分量真的抓到了摆动——否则可能是"什么都没动"的假通过
    omegas = []
    for i in range(1, len(masks)):
        t = R.estimate_rigid(masks[i - 1], masks[i])
        assert t is not None
        omegas.append(abs(t.dtheta))
    assert max(omegas) > np.deg2rad(2.0), "未检测到摆动，测试无意义"


def test_articulated_residual_much_larger() -> None:
    """纯关节运动 → 残差显著抬升。

    2026-08-24 实测基线：min=0.0202 p50=0.0332 max=0.0691。
    注意本合成用例是**刻意保守**的：躯干完全不动、只有一条 34px 细肢在摆。
    真实 TST 的主动挣扎是全身扭动 + 四肢，残差应远高于此。
    """
    masks = synth.articulated_series()
    res = _residuals(masks)
    assert float(np.median(res)) > 0.025, f"关节残差中位数过低: {np.median(res):.4f}"
    assert float(res.min()) > 0.015, f"关节残差最小值过低: {res.min():.4f}"


def test_pendulum_vs_articulated_separation() -> None:
    """两类运动的残差分布必须**完全不重叠**——这是 D1 差异化的量化证据。

    比"均值比值 > N"强得多：完全不重叠意味着存在一个单一阈值能无误地把被动摆动
    与主动挣扎分开，而这正是 TST 金标准要求做到的事（CSI 的标量 blob 运动量
    在原理上做不到）。

    2026-08-24 实测：钟摆 max=0.01025 < 关节 min=0.02023，留有约 2× 间隙。
    """
    pend = _residuals(synth.pendulum_series())
    arti = _residuals(synth.articulated_series())

    assert float(pend.max()) < float(arti.min()), (
        f"分布重叠：钟摆 max={pend.max():.5f} 未低于关节 min={arti.min():.5f}"
    )
    ratio = float(np.mean(arti) / max(np.mean(pend), 1e-9))
    assert ratio > 4.0, f"区分度不足，均值比 {ratio:.1f}"


def test_pendulum_rotation_dominates_translation_signature() -> None:
    """钟摆的特征是"刚体量大、残差小"，这正是判定 Passive Swing 的依据。"""
    masks = synth.pendulum_series()
    bl = R.trial_body_length(masks)
    for i in range(1, len(masks)):
        res = R.decompose(masks[i - 1], masks[i], bl=bl)
        assert res is not None
        rigid_rot_deg = abs(np.rad2deg(res.rigid.dtheta))
        if rigid_rot_deg > 1.0:
            assert res.residual < 0.015, (
                f"帧 {i}: 刚体旋转 {rigid_rot_deg:.2f}° 却有残差 {res.residual:.4f}"
            )


def test_segment_residual_localizes_limb() -> None:
    """关节运动应集中在含肢体的那一段，而非均摊到两段。"""
    masks = synth.articulated_series()
    bl = R.trial_body_length(masks)
    ratios = []
    for i in range(1, len(masks)):
        res = R.decompose(masks[i - 1], masks[i], n_segments=2, bl=bl)
        assert res is not None
        if res.residual <= 0:
            continue
        ratios.append(max(res.segment_ratio(0), res.segment_ratio(1)))
    assert ratios, "没有可用帧"
    assert float(np.mean(ratios)) > 0.7, (
        f"残差未集中在单段，平均最大占比仅 {np.mean(ratios):.2f}"
    )


def test_decompose_series_shape_and_nulls() -> None:
    """越界的 lag 必须置 None（保留该帧但置空），不能补 0。"""
    masks = synth.pendulum_series(n_frames=8)
    rows = R.decompose_series(masks, lags=(1, 4), n_segments=2)
    assert len(rows) == 8

    assert rows[0]["residual_lag1"] is None
    assert rows[0]["residual_lag4"] is None
    assert rows[3]["residual_lag4"] is None
    assert rows[4]["residual_lag4"] is not None
    assert rows[1]["residual_lag1"] is not None
    for key in ("seg0_lag1", "seg1_lag1", "omega_lag1", "trans_lag1", "scale_lag1"):
        assert key in rows[1]


def test_empty_mask_returns_none() -> None:
    """空剪影不可算 → None，由上层记 unknown。"""
    empty = np.zeros((50, 50), dtype=bool)
    body = synth.draw_body((50, 50), (25, 25), 0.0, 20.0, 10.0)
    assert R.decompose(empty, body) is None
    assert R.decompose(body, empty) is None
    assert sil.metrics(empty) is None


def test_silhouette_basic_metrics() -> None:
    m = synth.draw_body((200, 200), (100.0, 100.0), 0.0, 80.0, 20.0)
    met = sil.metrics(m)
    assert met is not None
    assert abs(met.centroid[0] - 100.0) < 1.0
    assert abs(met.centroid[1] - 100.0) < 1.0
    # theta≈0 时主轴沿 x，BL 应接近 80
    assert abs(met.body_length - 80.0) < 6.0, met.body_length
    assert met.elongation > 2.5
    assert met.hole_count == 0


def test_body_length_is_rotation_invariant() -> None:
    """BL 必须与朝向无关——否则用它做归一化会引入朝向偏差。"""
    lens = []
    for deg in (0, 20, 45, 70, 90, 135):
        m = synth.draw_body(
            (240, 240), (120.0, 120.0), np.deg2rad(deg), 80.0, 24.0
        )
        met = sil.metrics(m, with_holes=False)
        assert met is not None
        lens.append(met.body_length)
    spread = max(lens) - min(lens)
    assert spread < 5.0, f"BL 随朝向漂移 {spread:.2f} px: {lens}"


def test_hole_count_detects_ring() -> None:
    """尾巴攀爬的拓扑信号：剪影出现闭环孔洞。"""
    assert sil.count_holes(synth.ring()) == 1
    solid = synth.draw_body((80, 80), (40.0, 40.0), 0.0, 40.0, 20.0)
    assert sil.count_holes(solid) == 0


def test_axis_segments_partition_is_complete() -> None:
    """分段必须不重不漏地覆盖原剪影。"""
    m = synth.draw_body((200, 200), (100.0, 100.0), np.deg2rad(35), 80.0, 24.0)
    segs = sil.axis_segments(m, 3)
    union = np.zeros_like(m)
    total = 0
    for s in segs:
        assert not (union & s).any(), "分段之间有重叠"
        union |= s
        total += int(s.sum())
    assert (union == m).all(), "分段未覆盖全部像素"
    assert total == int(m.sum())


def test_above_below_split() -> None:
    """FST 水面线分区。"""
    m = synth.draw_body((200, 200), (100.0, 100.0), np.pi / 2, 80.0, 24.0)
    above, below = sil.above_below(m, 100.0)
    assert not (above & below).any()
    assert ((above | below) == m).all()
    assert above.sum() > 0 and below.sum() > 0


def test_trial_bl_is_stable_against_limb_extension() -> None:
    """试次级 BL 必须免疫瞬时肢体伸展。

    实测：躯干设定 60px 的关节序列，逐帧全剪影 BL 会飘到 76px；
    若拿逐帧 BL 做归一化分母，残差会被系统性压低约 1.6 倍。
    """
    masks = synth.articulated_series()
    per_frame = []
    for m in masks:
        met = sil.metrics(m, with_holes=False)
        assert met is not None
        per_frame.append(met.body_length)
    trial = R.trial_body_length(masks)
    spread = max(per_frame) - min(per_frame)
    assert spread > 3.0, "合成序列未产生肢体伸展，测试无意义"
    assert min(per_frame) <= trial <= max(per_frame)
    # 试次级值应比逐帧极值稳定：落在分布中部
    assert abs(trial - float(np.median(per_frame))) < 1e-6


def test_body_core_removes_limb() -> None:
    """腐蚀应削掉细肢体、保留躯干——这是刚体拟合不被关节污染的前提。"""
    with_limb = synth.draw_body(
        (200, 200), (100.0, 100.0), 0.0, 60.0, 22.0,
        limb_angle=1.2, limb_len=34.0, limb_width=10.0,
    )
    core = R.body_core(with_limb)
    assert core.sum() > 0
    assert core.sum() < with_limb.sum()

    met_full = sil.metrics(with_limb, with_holes=False)
    met_core = sil.metrics(core, with_holes=False)
    assert met_full is not None and met_core is not None
    # 核心的主轴长度应明显短于被肢体拉长的全剪影
    assert met_core.body_length < met_full.body_length


def test_refine_default_is_off() -> None:
    """refine 默认必须关闭：实测它会把关节运动当刚体旋转吸收，区分度 5.2→4.3。"""
    import inspect

    sig = inspect.signature(R.estimate_rigid)
    assert sig.parameters["refine"].default is False
    sig2 = inspect.signature(R.decompose)
    assert sig2.parameters["refine"].default is False
