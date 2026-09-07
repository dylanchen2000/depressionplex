"""RAD 测试。核心通过门：纯刚体钟摆的关节残差必须接近零。

这条测试就是整个方案成立与否的判据。CSI 的标量 blob 运动量在钟摆序列上会给出
很大的运动值（因而把被动摆动误计为 mobility）；我们必须做到接近零。
"""

from __future__ import annotations

import numpy as np

from depressionplex.assay_core import rad as R
from depressionplex.assay_core import silhouette as sil

import synth


def _residuals(masks: list[np.ndarray], lag: int = 1,
               mode: str = "binary_xor") -> np.ndarray:
    """用试次级 BL 归一化——与正式分析口径一致。

    `mode` **必须显式**，缺省钉在 `"binary_xor"`：下面三条基线数字（0.0070/0.0103/
    0.0202…）是 2026-08-24 在**旧口径**上实测的，它们记的是「旧口径当时是什么样」，
    换口径后那些数字就不再描述同一个量 ⇒ 与其跟着默认值漂，不如把它们焊死在
    产生它们的那个口径上。新口径的基线另立（见 `_SDF_BASELINE`）。
    """
    bl = R.trial_body_length(masks)
    out = []
    for i in range(lag, len(masks)):
        res = R.decompose(masks[i - lag], masks[i], lag=lag, bl=bl,
                          residual_mode=mode)
        assert res is not None
        out.append(res.residual)
    return np.asarray(out)


def _shift(du: float, dv: float, c: tuple[float, float]) -> R.RigidTransform:
    """纯平移的 RigidTransform：把中心从 c−(du,dv) 搬到 c。"""
    return R.RigidTransform(dx=du, dy=dv, dtheta=0.0, scale=1.0,
                            c_prev=(c[0] - du, c[1] - dv), c_cur=c)


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


# ------------------------------------------------------------- DP-071 距离场口径

#: 2026-09-07 在合成序列上实测的**新口径**基线（`sdf_coverage`）。
#: 钟摆 mean 0.01277 / max 0.01456；关节 mean 0.04111 / min 0.02305。
#: 新口径的噪声底比旧口径高（旧：0.00760 / 0.01025），这**不是回退**：
#: 旧口径的钟摆残差之所以低，是因为 0.5 px 死区把亚像素失配四舍五入掉了
#: （DP-071：不动帧估计位移中位 0.106 px、逐位空转率中位 78%）。
#: 新口径把那部分失配照实算进来 ⇒ 底抬高、区分度收窄，**但仍不重叠**。
#: 这两组数字都写在这里，是为了让「换口径的代价」始终看得见。
_SDF_BASELINE = {"pend_mean": 0.01277, "pend_max": 0.01456,
                 "arti_mean": 0.04111, "arti_min": 0.02305}


def test_coverage_reconstructs_mask_exactly() -> None:
    """`coverage(signed_distance(m))` 必须**逐位**还原 m，且没有任何小数像素。

    这条钉住半像素约定：内部像素 phi <= −0.5、外部 >= +0.5 ⇒ clip 后只有 0 和 1。
    它同时是"不动 ⇒ 0 残差"的前提——若这里出现小数，静止帧就会凭空长出残差，
    那正是 DP-071 已否掉的"软掩膜"修法的病（软对硬，沿整条轮廓累账）。
    """
    for deg in (0.0, 0.3, 1.1):
        m = synth.draw_body((200, 200), (100.0, 100.0), deg, 60.0, 22.0)
        cov = R.coverage(R.signed_distance(m))
        assert np.array_equal(cov > 0.5, m), "还原不出原掩膜"
        frac = int(np.count_nonzero((cov > 0.0) & (cov < 1.0)))
        assert frac == 0, "出现了 %d 个小数覆盖率像素" % frac


def test_warp_mask_is_dead_below_half_pixel() -> None:
    """把 DP-071 的病灶本身钉成测试：warp **二值掩膜**在 |u| < 0.5 px 下逐位空转。

    这不是在测一个 bug 等着修——`warp_mask` 的输出是像素掩膜，量化下限就是半像素，
    这条测试存在的意义是**防止有人以后拿它去算残差**并以为补偿生效了。
    """
    m = synth.draw_body((200, 200), (100.0, 100.0), 0.3, 60.0, 22.0)
    for u in (0.05, 0.1, 0.2, 0.3, 0.45, 0.49):
        w = np.asarray(R.warp_mask(m, _shift(u, 0.0, (100.0, 100.0)), m.shape), bool)
        assert np.array_equal(w, m), "u=%.2f 竟然改变了掩膜" % u
    w = np.asarray(R.warp_mask(m, _shift(1.0, 0.0, (100.0, 100.0)), m.shape), bool)
    assert not np.array_equal(w, m), "整数位移也没动 ⇒ warp 本身坏了"


def test_coverage_response_is_linear_in_subpixel_shift() -> None:
    """距离场口径对亚像素平移**线性**响应，且比例常数就是轮廓周长。

    实测（60x22 的体，1 px 环带周长 124）：每 1 px 位移 54.0 px 覆盖率差，
    u=0.05 读 2.700 = 0.05 x 54.0 ⇒ 死区没了，且量纲是"位移 x 周长"。
    """
    m = synth.draw_body((200, 200), (100.0, 100.0), 0.3, 60.0, 22.0)
    phi = R.signed_distance(m)
    cov = R.coverage(phi)
    unit = float(np.abs(R.coverage(
        R.warp_field(phi, _shift(1.0, 0.0, (100.0, 100.0)), m.shape)) - cov).sum())
    assert unit > 20.0, "1 px 位移只读到 %.1f px，太小" % unit
    for u in (0.05, 0.1, 0.2, 0.3, 0.45):
        got = float(np.abs(R.coverage(
            R.warp_field(phi, _shift(u, 0.0, (100.0, 100.0)), m.shape)) - cov).sum())
        assert abs(got - u * unit) < 0.02 * unit, (
            "u=%.2f 非线性：读 %.3f，线性预期 %.3f" % (u, got, u * unit))


def test_integer_shift_two_modes_are_identical() -> None:
    """整数位移下新旧口径必须**逐位相等**——向后兼容的锚。

    在 `warp_mask` 精确的那些位移上（整数）两个口径给同一张图，
    说明新口径不是"另一个测量"，而是同一个测量在亚像素处的延拓。
    """
    m = synth.draw_body((200, 200), (100.0, 100.0), 0.3, 60.0, 22.0)
    phi = R.signed_distance(m)
    for du, dv in ((1, 0), (0, 1), (2, -3), (-4, 5)):
        t = _shift(float(du), float(dv), (100.0, 100.0))
        hard = np.asarray(R.warp_mask(m, t, m.shape), bool).astype(np.float64)
        soft = R.coverage(R.warp_field(phi, t, m.shape))
        assert np.array_equal(hard, soft), "(%d,%d) 两个口径不一致" % (du, dv)


def test_identical_frames_give_exactly_zero_residual() -> None:
    """两帧完全相同 ⇒ 两个口径的残差都**恰好** 0，不是"很小"。

    这条把新口径与 DP-071 已否掉的软掩膜修法分开：软掩膜在静止时也有底噪
    （实测 u=0.02 就读 8.0 px），本口径静止时逐位相等。
    """
    m = synth.draw_body((200, 200), (100.0, 100.0), 0.3, 60.0, 22.0)
    for mode in R.RESIDUAL_MODES:
        res = R.decompose(m, m, bl=60.0, residual_mode=mode)
        assert res is not None
        assert res.residual == 0.0, "%s 的同帧残差是 %r" % (mode, res.residual)
        assert all(v == 0.0 for v in res.segment_residuals)


def test_residual_mode_is_declared_and_validated() -> None:
    """口径必须是白名单里的一个，且默认值必须在白名单里。"""
    assert R.DEFAULT_RESIDUAL_MODE in R.RESIDUAL_MODES
    m = synth.draw_body((80, 80), (40.0, 40.0), 0.0, 30.0, 12.0)
    try:
        R.decompose(m, m, residual_mode="tolerant_xor")
    except ValueError as exc:
        assert "residual_mode" in str(exc)
    else:
        raise AssertionError("非法口径没有报错")


def test_field_cache_must_match_its_mask() -> None:
    """距离场缓存传错必须**报错**，不许静默改变残差。

    缓存是性能手段，不是第二份真相：`decompose_series` 传的场若和帧对不上，
    残差会悄悄变，而残差是整条链的核心量。
    """
    a = synth.draw_body((120, 120), (60.0, 60.0), 0.0, 40.0, 16.0)
    b = synth.draw_body((120, 120), (66.0, 60.0), 0.0, 40.0, 16.0)
    try:
        R.decompose(a, b, bl=40.0, residual_mode="sdf_coverage",
                    prev_field=R.signed_distance(b))
    except ValueError as exc:
        assert "距离场" in str(exc)
    else:
        raise AssertionError("传错的距离场没有被挡下")


def test_series_rolling_cache_matches_uncached() -> None:
    """`decompose_series` 的滚动缓存不得改变任何一个残差值。

    缓存只留最近 max(lags)+1 帧（9000 帧的全量距离场是 2.3 GB，存不下），
    这条测试钉住"省内存没有省掉正确性"。
    """
    masks = synth.pendulum_series(n_frames=10)
    bl = R.trial_body_length(masks)
    rows = R.decompose_series(masks, lags=(1, 4), bl=bl,
                              residual_mode="sdf_coverage")
    for i in range(len(masks)):
        for lag in (1, 4):
            got = rows[i]["residual_lag%d" % lag]
            if i - lag < 0:
                assert got is None
                continue
            want = R.decompose(masks[i - lag], masks[i], lag=lag, bl=bl,
                               residual_mode="sdf_coverage")
            assert want is not None
            assert abs(got - want.residual) < 1e-12, (
                "帧 %d lag %d：缓存路径 %r 与直算 %r 不一致"
                % (i, lag, got, want.residual))


def test_both_modes_keep_pendulum_and_articulation_separable() -> None:
    """**两个口径都必须**做到「钟摆最大 < 关节最小」——这条是方案成立的判据本身。

    新口径的代价在这里量得见：区分度（均值比）由 5.16 收窄到 3.22，
    间隙由 1.97x 收窄到 1.58x。**收窄但不重叠** ⇒ 仍存在单一门槛能无误分开两类。
    若哪天这条真的重叠了，说明换来的亚像素灵敏度已经被噪声吃掉，那时该退回旧口径。
    """
    for mode in R.RESIDUAL_MODES:
        pend = _residuals(synth.pendulum_series(), mode=mode)
        arti = _residuals(synth.articulated_series(), mode=mode)
        assert float(pend.max()) < float(arti.min()), (
            "%s 下分布重叠：钟摆 max=%.5f 未低于关节 min=%.5f"
            % (mode, pend.max(), arti.min()))
        ratio = float(np.mean(arti) / max(np.mean(pend), 1e-9))
        assert ratio > 3.0, "%s 下区分度只有 %.1f" % (mode, ratio)


def test_sdf_baselines_are_pinned() -> None:
    """把新口径的合成基线钉住，容差 10%——任何抬高噪声底的改动都应让本条失败。

    与旧口径那条 `test_pendulum_residual_near_zero` 平行：那条守旧口径的
    0.0070/0.0103，这条守新口径的 0.01277/0.01456。**两条都在**，
    所以"换口径的代价"不会随时间被人忘掉。
    """
    pend = _residuals(synth.pendulum_series(), mode="sdf_coverage")
    arti = _residuals(synth.articulated_series(), mode="sdf_coverage")
    for got, key in ((float(pend.mean()), "pend_mean"), (float(pend.max()), "pend_max"),
                     (float(arti.mean()), "arti_mean"), (float(arti.min()), "arti_min")):
        want = _SDF_BASELINE[key]
        assert abs(got - want) < 0.1 * want, (
            "%s 实测 %.5f 偏离基线 %.5f 超过 10%%" % (key, got, want))

