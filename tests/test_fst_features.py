"""候选特征测试（Spec A §5.2）：可解释的量算得对，没实现的明说没实现。

第 3 条候选（刚体平移/旋转 与 补偿后残差**两个都留**）是重点：
只报位移会把"原地转身"读成"没动"，这里用一对同心但转了 90° 的矩形
钉住——位移=0 而残差路径必须把旋转对掉。
"""

from __future__ import annotations

import numpy as np

from depressionplex.fst_research import cup_features as feat


def _mask(rows, cols, shape=(60, 60)) -> np.ndarray:
    m = np.zeros(shape, dtype=bool)
    m[rows[0]:rows[1] + 1, cols[0]:cols[1] + 1] = True
    return m


def _cent(m: np.ndarray) -> tuple[float, float]:
    ys, xs = np.nonzero(m)
    if xs.size == 0:
        return (0.0, 0.0)      # 空掩膜的质心无所谓：pair_features 必须先拒收
    return (float(xs.mean()), float(ys.mean()))


def _pf(mask_prev, mask_cur, *, frame_prev=0, frame_cur=5, fps=25.0,
        theta_prev=0.0, theta_cur=0.0, scale=50.0,
        gap_records_between=0, **kw) -> feat.PairFeatures:
    return feat.pair_features(
        frame_prev=frame_prev, frame_cur=frame_cur, fps=fps,
        mask_prev=mask_prev, mask_cur=mask_cur,
        cent_prev=_cent(mask_prev), cent_cur=_cent(mask_cur),
        theta_prev=theta_prev, theta_cur=theta_cur,
        spatial_scale_px=scale,
        above_water_frac_cur=kw.get("above_water_frac_cur", None),
        wall_dist_px_cur=kw.get("wall_dist_px_cur", None),
        gap_records_between=gap_records_between)


def test_wrap_angle_no_directionality() -> None:
    assert feat._wrap_angle(0.0) == 0.0
    assert abs(feat._wrap_angle(np.pi)) < 1e-12          # θ 与 θ+π 等价
    assert abs(feat._wrap_angle(np.pi / 2) - np.pi / 2) < 1e-12
    assert abs(feat._wrap_angle(3 * np.pi / 4) - np.pi / 4) < 1e-12
    assert abs(feat._wrap_angle(-np.pi / 2) - np.pi / 2) < 1e-12
    assert abs(feat._wrap_angle(5 * np.pi / 4) - np.pi / 4) < 1e-12


def test_translate_moves_and_drops_out_of_range() -> None:
    m = _mask((10, 12), (10, 12))
    t = feat._translate(m, 0, 5)
    assert t[10:13, 15:18].all() and t.sum() == 9
    assert feat._translate(m, 0, 200).sum() == 0         # 完全移出 ⇒ 空


def test_rigid_residual_empty_mask_cases() -> None:
    empty = np.zeros((20, 20), dtype=bool)
    m = _mask((5, 8), (5, 8), shape=(20, 20))
    assert feat.rigid_residual(empty, empty, (0, 0), (0, 0), 0, 0) == 0.0
    assert feat.rigid_residual(empty, m, (0, 0), _cent(m), 0, 0) == 1.0
    assert feat.rigid_residual(m, empty, _cent(m), (0, 0), 0, 0) == 1.0


def test_pure_translation_disp_and_residual_exact() -> None:
    prev = _mask((5, 14), (5, 14))
    cur = _mask((5, 14), (10, 19))           # 右移 5 px
    p = _pf(prev, cur)
    assert p.dt_s == 0.2
    assert p.disp_px == 5.0
    assert abs(p.disp_norm - 0.1) < 1e-12    # 5 / 50
    assert abs(p.speed_norm_per_s - 0.5) < 1e-12
    assert p.dtheta_rad == 0.0
    assert p.residual_after_rigid < 1e-9     # 纯刚体平移 ⇒ 残差 0
    assert p.d_area_frac == 0.0
    assert abs(p.d_elongation) < 1e-9 and abs(p.d_bend) < 1e-9


def test_rotation_kept_alongside_zero_displacement() -> None:
    """位移 0 的转身：位移与残差两个都留，谁也不吞掉谁（Spec §5.2 第 3 条）。"""
    prev = _mask((28, 31), (10, 49))         # 横条 40×4
    cur = _mask((10, 49), (28, 31))          # 竖条，同中心 ⇒ 质心位移 0
    p = _pf(prev, cur, theta_prev=0.0, theta_cur=np.pi / 2)
    assert p.disp_px < 1e-9                  # 只看位移会读成"没动"
    assert abs(p.dtheta_rad - np.pi / 2) < 1e-12
    assert p.residual_after_rigid < 0.35     # 刚性对齐把旋转对掉 ⇒ 残差小


def test_non_rigid_deformation_leaves_residual() -> None:
    prev = _mask((20, 30), (20, 30))
    cur = np.zeros((60, 60), dtype=bool)
    cur[20:31, 20:26] = True                 # 同面积量级但形状大变（拉长）
    cur[20:25, 26:37] = True
    p = _pf(prev, cur)
    assert p.residual_after_rigid > 0.1      # 刚性补偿后仍对不上 ⇒ 非刚性信号


def test_pair_features_rejects_bad_input() -> None:
    m = _mask((5, 9), (5, 9))
    empty = np.zeros((60, 60), dtype=bool)
    cases = [
        dict(frame_prev=5, frame_cur=5),     # dt=0
        dict(frame_prev=9, frame_cur=4),     # 帧序倒流
    ]
    for kw in cases:
        base = dict(frame_prev=kw["frame_prev"], frame_cur=kw["frame_cur"],
                    fps=25.0, mask_prev=m, mask_cur=m,
                    cent_prev=_cent(m), cent_cur=_cent(m),
                    theta_prev=0.0, theta_cur=0.0, spatial_scale_px=50.0,
                    above_water_frac_cur=None, wall_dist_px_cur=None)
        try:
            feat.pair_features(**base)
        except ValueError:
            pass
        else:
            raise AssertionError(f"没拒绝坏帧序: {kw}")
    for masks in ((empty, m), (m, empty), (empty, empty)):
        try:
            _pf(masks[0], masks[1])
        except ValueError:
            pass
        else:
            raise AssertionError("空掩膜不该进 pair_features（空掩膜帧不是 observed）")
    try:
        _pf(m, m, scale=0.0)
    except ValueError:
        pass
    else:
        raise AssertionError("空间尺度 0 没被拒绝")


def test_summarize_empty_and_not_implemented_marker() -> None:
    s = feat.summarize([])
    assert s["n_pairs"] == 0
    assert s["n_pairs_continuous"] == 0 and s["n_pairs_cross_gap"] == 0
    # R2-115 T2：没有连续对 ⇒ 统计键全 None，不填 0
    assert s["statistics_apply_to"] is None
    assert "不填 0" in s["stats_note"]
    for key in feat.STAT_KEYS:
        assert s[key] is None
    assert s["local_motion_inside_vs_outside"] == feat.LOCAL_MOTION_NOT_IMPLEMENTED
    assert isinstance(s["local_motion_inside_vs_outside"], str)   # 绝不是数字
    assert "光流" in s["local_motion_reason"]


def test_summarize_stats_and_marker_persist() -> None:
    pairs = []
    for k in range(12):
        prev = _mask((10, 14), (10 + k, 14 + k))
        cur = _mask((10, 14), (11 + k, 15 + k))
        pairs.append(_pf(prev, cur, frame_prev=k, frame_cur=k + 1))
    s = feat.summarize(pairs)
    assert s["n_pairs"] == 12
    assert s["n_pairs_continuous"] == 12 and s["n_pairs_cross_gap"] == 0
    assert s["statistics_apply_to"] == "continuous_pairs_only"
    for key in ("disp_norm", "speed_norm_per_s", "dtheta_rad",
                "residual_after_rigid", "d_area_frac"):
        st = s[key]
        assert set(st) == {"median", "p90", "max"}
        assert st["median"] <= st["p90"] <= st["max"]
    assert s["local_motion_inside_vs_outside"] == feat.LOCAL_MOTION_NOT_IMPLEMENTED


# ---------------------------------------------------------------------------
# R2-115 T2：跨观测缺口的配对单列，不进连续统计
# ---------------------------------------------------------------------------

def test_pair_features_gap_records_between_passthrough() -> None:
    m = _mask((10, 14), (10, 14))
    p = _pf(m, m, frame_prev=0, frame_cur=25, gap_records_between=4)
    assert p.gap_records_between == 4
    assert _pf(m, m).gap_records_between == 0            # 默认相邻
    try:
        _pf(m, m, gap_records_between=-1)
    except ValueError:
        pass
    else:
        raise AssertionError("gap_records_between 负数没被拒绝")


def test_summarize_cross_gap_pairs_excluded_from_stats() -> None:
    """缺口两端的"速度"混进了动物在缺口里干什么的未知数 ⇒ 单列不进统计。"""
    pairs = []
    for k in range(4):                                   # 4 对连续（dt=0.2 s）
        pairs.append(_pf(_mask((10, 14), (10 + k, 14 + k)),
                         _mask((10, 14), (11 + k, 15 + k)),
                         frame_prev=k * 5, frame_cur=k * 5 + 5))
    # 一对跨缺口：frame 10 → 60，中间 9 条非 observed 记录
    pairs.append(_pf(_mask((10, 14), (10, 14)), _mask((10, 14), (40, 44)),
                     frame_prev=10, frame_cur=60, fps=25.0,
                     gap_records_between=9))
    s = feat.summarize(pairs)
    assert s["n_pairs"] == 5
    assert s["n_pairs_continuous"] == 4 and s["n_pairs_cross_gap"] == 1
    assert s["statistics_apply_to"] == "continuous_pairs_only"
    # 连续统计只由 4 对小位移构成：跨缺口那对的巨大 disp_norm/speed 没混进来
    # （连续对 disp_norm=0.02、speed=0.1；跨缺口对是 0.6 / 0.3）
    assert s["disp_norm"]["max"] < 0.05
    assert s["speed_norm_per_s"]["max"] < 0.15
    g = s["cross_gap_pairs"]
    assert g["rows_omitted"] == 0
    assert g["gaps"] == [{"frame_prev": 10, "frame_cur": 60,
                          "gap_records": 9, "dt_s": 2.0}]


def test_summarize_cross_gap_rows_capped() -> None:
    m = _mask((10, 14), (10, 14))
    pairs = [_pf(m, m, frame_prev=k * 10, frame_cur=k * 10 + 5,
                 gap_records_between=2)
             for k in range(feat.MAX_CROSS_GAP_ROWS + 7)]
    s = feat.summarize(pairs)
    assert s["n_pairs_cross_gap"] == feat.MAX_CROSS_GAP_ROWS + 7
    assert len(s["cross_gap_pairs"]["gaps"]) == feat.MAX_CROSS_GAP_ROWS
    assert s["cross_gap_pairs"]["rows_omitted"] == 7
    assert s["statistics_apply_to"] is None              # 全是跨缺口对


def test_summarize_only_cross_gap_no_zero_filled_stats() -> None:
    m = _mask((10, 14), (10, 14))
    s = feat.summarize([_pf(m, m, frame_prev=0, frame_cur=50,
                            gap_records_between=9)])
    assert s["n_pairs_continuous"] == 0
    for key in feat.STAT_KEYS:
        assert s[key] is None, f"没有连续对时 {key} 必须是 None 不是 0"
