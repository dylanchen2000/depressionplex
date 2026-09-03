"""原语体系测试：一次多标签标注 → 三套口径导出；κ 一致性。"""

from __future__ import annotations

import numpy as np

from depressionplex.assay_core import primitives as P

STRUGGLE = {"fore_active": True, "hind_active": True, "trunk_deform": True}
FORE_ONLY = {"fore_active": True}
STILL = {}
SWING = {"rigid_swing": True}
CLIMB = {"tail_grasp": True, "hind_active": True}


def test_tst_struggle_exports_three_rubrics() -> None:
    assert P.export_rubric(STRUGGLE, "academic_tst") == ["Mobility"]
    assert P.export_rubric(STRUGGLE, "csi_tst") == ["Mobility"]
    assert P.export_rubric(STRUGGLE, "ours_tst") == ["Mobility"]


def test_tst_forelimb_only_not_mobility_in_any_rubric_we_control() -> None:
    """金标准：仅前肢不计 mobility。学术口径按互补惯例落 Immobility；
    我方口径显式 ForelimbOnly。"""
    assert P.export_rubric(FORE_ONLY, "academic_tst") == ["Immobility"]
    assert P.export_rubric(FORE_ONLY, "ours_tst") == ["ForelimbOnly"]
    # CSI 兼容口径如实复刻其缺陷（标量运动量不区分）：计为 Mobility
    assert P.export_rubric(FORE_ONLY, "csi_tst") == ["Mobility"]


def test_tst_passive_swing_split() -> None:
    """钟摆：学术/CSI 口径落 immobility/mobility（其缺陷），我方单列 L2。"""
    assert P.export_rubric(SWING, "academic_tst") == ["Immobility"]
    assert P.export_rubric(SWING, "csi_tst") == ["Mobility"]
    assert P.export_rubric(SWING, "ours_tst") == ["PassiveSwing"]


def test_tst_tail_climbing_is_trial_level_exclusion() -> None:
    """攀爬帧在学术口径两类别都不命中（试次级排除区），我方单列。"""
    assert P.export_rubric(CLIMB, "academic_tst") == []
    assert "TailClimbing" in P.export_rubric(CLIMB, "ours_tst")


def test_tst_still_is_immobility() -> None:
    for rub in ("academic_tst", "csi_tst", "ours_tst"):
        assert P.export_rubric(STILL, rub) == ["Immobility"]


def test_fst_rubrics() -> None:
    swim = {"hind_active": True, "head_above": True}
    climb = {"fore_active": True, "touch_wall": True}
    wallfloat = {"touch_wall": True, "head_above": True}
    dive = {"head_above": False}
    assert P.export_rubric(swim, "academic_fst") == ["Swimming"]
    assert P.export_rubric(climb, "academic_fst") == ["Climbing"]
    assert P.export_rubric(swim, "csi_fst") == ["Swim"]
    assert "Diving" in P.export_rubric(dive, "ours_fst")
    assert "WallSupportedFloat" in P.export_rubric(wallfloat, "ours_fst")


def test_expand_bouts_to_frames() -> None:
    bouts = [P.Bout(10, 19, {"hind_active": True}), P.Bout(30, 39, {})]
    f = P.expand_to_frames(bouts, 50, "hind_active")
    assert f[10] and f[19] and not f[9] and not f[20]
    assert f.sum() == 10


def test_cohen_kappa() -> None:
    a = np.array([1, 1, 0, 0, 1, 0], dtype=bool)
    assert P.Cohen_kappa(a, a.copy()) == 1.0
    assert P.Cohen_kappa(a, ~a) < 0.0
    z = np.zeros(6, dtype=bool)
    assert P.Cohen_kappa(z, z) == 1.0  # 双方常假：约定 1.0


def test_agreement_report_rare_primitive_gets_pabak() -> None:
    """基率悖论防护：稀有原语 κ 难看但原始一致率极高 ⇒ 附 PABAK。"""
    n = 1000
    a = np.zeros(n, dtype=bool)
    b = np.zeros(n, dtype=bool)
    a[0:10] = True            # a 标了 10 帧
    b[10:20] = True           # b 标了另 10 帧（无重叠）→ 原始一致率仍 98%
    rep = P.agreement_report(a, b)
    assert rep["rare"] is True
    assert rep["prevalence"] < 0.05
    assert rep["raw_agreement"] >= 0.98
    assert "pabak" in rep and rep["pabak"] > 0.9   # PABAK 揭示真实一致水平
    assert rep["kappa"] < 0.5                       # κ 被基率压低 → 不能单看 κ


def test_agreement_report_common_primitive_no_pabak() -> None:
    a = np.zeros(1000, dtype=bool)
    b = np.zeros(1000, dtype=bool)
    a[0:400] = True
    b[0:400] = True
    rep = P.agreement_report(a, b)
    assert rep["rare"] is False
    assert "pabak" not in rep
    assert rep["kappa"] == 1.0


def test_zero_prevalence_kappa_is_undefined_not_one() -> None:
    """出现率 0 时 κ 数学上退化为 1.0，但那不是一致性证据，必须标 undefined。

    2026-09-03 实证：道俊目检确认没有一只鼠够到自己的尾巴 ⇒ `tail_grasp`
    在本批素材上出现率就是 0。若报 κ=1.000，会被读成「两人在抓尾上完全一致」。
    """
    import numpy as np

    z = np.zeros(100, dtype=bool)
    rep = P.agreement_report(z, z)
    assert rep["prevalence"] == 0.0
    assert rep["undefined_no_positives"] is True
    assert np.isnan(rep["kappa"])
    assert rep["raw_agreement"] == 1.0  # 原始一致率仍是 1，且仍要报
