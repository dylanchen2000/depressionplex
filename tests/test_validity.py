"""试次级有效性判据测试：脱落 / 截断 / 有效。

判据（评审定稿）：全片标定帧是否出现身体级面积（≥0.5×其他隔间中位数）。
从未出现 = 脱落（产品特性，建议排除）；出现过但当前只有尾级 = 截断 bug。
"""

from __future__ import annotations

import numpy as np

from depressionplex.assay_core import validity as V


def _profile(base: float, n: int = 8, jitter: float = 5.0) -> list[float]:
    return [base + jitter * np.sin(i) for i in range(n)]


def test_detached_chamber_detected() -> None:
    calib = {
        1: _profile(200), 2: _profile(190), 3: _profile(210),
        4: _profile(40),          # 只有尾级面积，全片如此
    }
    tv = V.assess_trial_validity(calib)
    st = {c.chamber: c.status for c in tv.chambers}
    assert st[4] == V.STATUS_DETACHED
    assert st[1] == st[2] == st[3] == V.STATUS_VALID
    assert tv.exclude == (4,)
    assert tv.needs_repair == ()


def test_reference_excludes_self() -> None:
    """脱落隔间自身不参与定标：参考量是其他隔间中位数。"""
    calib = {1: _profile(200), 2: _profile(220), 3: _profile(40), 4: _profile(40)}
    tv = V.assess_trial_validity(calib)
    by = {c.chamber: c for c in tv.chambers}
    # 两遍法：尾级隔间不污染参考。ch1 的参考只来自其他有身体隔间
    # （ch2 中位数 ≈220，而非含尾级的全局中位数 ≈120）
    assert 210.0 < by[1].ref_body_area < 230.0
    assert by[3].status == V.STATUS_DETACHED
    assert by[4].status == V.STATUS_DETACHED
    assert by[1].status == V.STATUS_VALID


def test_truncated_suspect_when_body_existed() -> None:
    """标定帧有过身体、当前分析只有尾级 ⇒ 截断 bug，不是脱落。"""
    calib = {1: _profile(200), 2: _profile(200), 3: _profile(200),
             4: [45.0, 200.0, 180.0, 40.0, 190.0, 210.0, 42.0, 185.0]}
    current = {1: _profile(200), 2: _profile(200), 3: _profile(200),
               4: _profile(42)}
    tv = V.assess_trial_validity(calib, current)
    by = {c.chamber: c for c in tv.chambers}
    assert by[4].status == V.STATUS_TRUNCATED
    assert by[4].ever_had_body is True
    assert tv.needs_repair == (4,)
    assert tv.exclude == ()   # 截断不得按脱落排除


def test_all_valid() -> None:
    calib = {k: _profile(180 + 10 * k) for k in (1, 2, 3, 4)}
    tv = V.assess_trial_validity(calib, {k: _profile(180 + 10 * k) for k in (1, 2, 3, 4)})
    assert all(c.status == V.STATUS_VALID for c in tv.chambers)
    assert tv.exclude == () and tv.needs_repair == ()


def test_no_body_anywhere_needs_prior_to_decide() -> None:
    """全试次尾级：相对判据不可决（边界）；常开下界 prior ⇒ 整批判脱落。"""
    calib = {k: _profile(30) for k in (1, 2, 3, 4)}
    # 无先验：相对判据自洽地判 valid——这是已记录的不可决边界
    tv_rel = V.assess_trial_validity(calib)
    assert all(c.status == V.STATUS_VALID for c in tv_rel.chambers)
    # 有先验：thr = max(0.5×median_others≈15, 100) = 100 > 30 ⇒ 整批脱落
    tv = V.assess_trial_validity(calib, body_area_prior=100.0)
    assert all(c.status == V.STATUS_DETACHED for c in tv.chambers)
    assert tv.exclude == (1, 2, 3, 4)


def test_three_of_four_detached_stable_with_prior() -> None:
    """评审用例：3/4 脱落时相对基准退化，常开下界使判据仍成立。"""
    calib = {1: _profile(200), 2: _profile(5), 3: [None] * 8, 4: _profile(5)}
    tv = V.assess_trial_validity(calib, body_area_prior=100.0)
    by = {c.chamber: c for c in tv.chambers}
    assert by[1].status == V.STATUS_VALID
    assert by[2].status == V.STATUS_DETACHED
    assert by[3].status == V.STATUS_DETACHED
    assert by[4].status == V.STATUS_DETACHED
    assert tv.exclude == (2, 3, 4)


def test_none_and_tiny_entries_ignored() -> None:
    calib = {
        1: [None, 200.0, None, 195.0, 205.0, 3.0, 200.0, None],
        2: _profile(200), 3: _profile(200), 4: [None] * 8,
    }
    tv = V.assess_trial_validity(calib)
    by = {c.chamber: c for c in tv.chambers}
    assert by[1].status == V.STATUS_VALID
    assert by[4].status == V.STATUS_DETACHED


def test_verdicts_invariant_to_area_scale() -> None:
    """单位不变量配套：全部面积 ×4（更高分辨率同一试次）判定不变。"""
    calib = {1: _profile(200), 2: _profile(190), 3: _profile(210), 4: _profile(40)}
    v1 = V.assess_trial_validity(calib)
    v2 = V.assess_trial_validity({k: [4 * x for x in p] for k, p in calib.items()})
    assert [c.status for c in v1.chambers] == [c.status for c in v2.chambers]
