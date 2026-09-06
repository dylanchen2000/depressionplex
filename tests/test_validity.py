"""试次级有效性判据测试：从未有动物 / 脱落 / 截断 / 有效（DP-031 拆分）。

判据（评审定稿）：全片标定帧是否出现身体级面积（≥0.5×其他隔间中位数）；
是否出现**任何**动物级掩膜（≥0.15×身体门槛，相对量⇒标度不变）。
- 从未动物级 = never_occupied（布置/录制问题，建议排除——**不是**实验失败）；
- 有尾级从未身体级、或前半身体后半消失 = detached（悬挂失效/中途脱落，实验失败）；
- 出现过但当前只有尾级 = truncated_suspect（分割 bug，不得按脱落静默丢弃）。
DP-028 N1–N3：排除态隔间的一切 immobility 都是幻影——score_gate 是唯一闸门，
never_occupied 的在场占比必须**恰等于 0**，幻影候选必须报警不得静默。
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
    """评审用例：3/4 无身体级时相对基准退化，常开下界使判据仍成立。

    DP-031 拆分后三种"无身体级"证据各归其位：尾级悬挂 = detached（2 号），
    只有噪声级尾链 = never_occupied（4 号）。

    **DP-032 修正（2026-09-06）**：3 号原先是 `[None]*8` 且断言 never_occupied
    （注释写"从未有掩膜"）——那正是 DP-032 的缺陷本身：`None` 是"这一帧没看成"，
    不是"这一帧没有动物"。现在 3 号判 `unknown`，并另立 3′ 号保留"真的看了、
    只有噪声级尾链"⇒ never_occupied 这条不受影响的路径。
    """
    calib = {1: _profile(200), 2: _profile(40), 3: [None] * 8, 4: _profile(5)}
    tv = V.assess_trial_validity(calib, body_area_prior=100.0)
    by = {c.chamber: c for c in tv.chambers}
    assert by[1].status == V.STATUS_VALID
    assert by[2].status == V.STATUS_DETACHED        # 有尾级证据：悬挂失效
    assert by[3].status == V.STATUS_UNKNOWN         # 一帧都没看成 ⇒ 无证据（DP-032）
    assert by[3].occupied_fraction is None          # 不许拿伪占比冒充可判
    assert by[3].unsegmentable_fraction == 1.0
    assert by[4].status == V.STATUS_NEVER_OCCUPIED  # 噪声级尾链（v4/v7 的实况）
    assert tv.exclude == (2, 4)                     # 处置：排除态不产出统计量
    assert tv.detached == (2,)                      # 两支各自可数——不合并
    assert tv.never_occupied == (4,)                # 3 号不再顶 G10a 的账


def test_none_and_tiny_entries_ignored() -> None:
    calib = {
        1: [None, 200.0, None, 195.0, 205.0, 3.0, 200.0, None],
        2: _profile(200), 3: _profile(200), 4: [None] * 8,
    }
    tv = V.assess_trial_validity(calib)
    by = {c.chamber: c for c in tv.chambers}
    assert by[1].status == V.STATUS_VALID
    # 1 号：8 帧里 3 帧 None、1 帧 3.0 —— 分母只算 5 个成功帧，
    # 其中 4 帧身体级 ⇒ 占比 0.8，不是 4/8 = 0.5（DP-032 分母修正）。
    assert by[1].occupied_fraction == 0.8
    assert by[1].unsegmentable_fraction == 0.375
    # 4 号：全 None ⇒ 一帧都没看成 ⇒ unknown，**不是**空场（DP-032）。
    assert by[4].status == V.STATUS_UNKNOWN
    assert by[4].occupied_fraction is None
    assert by[4].unsegmentable_fraction == 1.0


def test_verdicts_invariant_to_area_scale() -> None:
    """单位不变量配套：全部面积 ×4（更高分辨率同一试次）判定不变。

    两个案例都过：尾级悬挂（detached 支）与 v4/v7 实况的噪声级尾链
    （never_occupied 支）。在场判据是**纯相对量**（≥0.15×门槛，raw 值直接比，
    不过 _AREA_FLOOR 像素常数）——×4 后 profile(5) 的尾链跨过 floor 仍判
    never_occupied，判定不随分辨率翻转。occupied_fraction 也必须逐位一致。
    """
    for base4 in (_profile(40), _profile(5)):
        calib = {1: _profile(200), 2: _profile(190), 3: _profile(210), 4: base4}
        v1 = V.assess_trial_validity(calib)
        v2 = V.assess_trial_validity(
            {k: [4 * x for x in p] for k, p in calib.items()})
        assert [(c.status, c.occupied_fraction) for c in v1.chambers] == \
               [(c.status, c.occupied_fraction) for c in v2.chambers]
    for want, base4 in ((V.STATUS_DETACHED, _profile(40)),
                        (V.STATUS_NEVER_OCCUPIED, _profile(5))):
        calib = {1: _profile(200), 2: _profile(190), 3: _profile(210), 4: base4}
        assert {c.chamber: c.status
                for c in V.assess_trial_validity(calib).chambers}[4] == want


def test_never_occupied_separated_from_detached() -> None:
    """DP-031 拆分：整片无掩膜 ⇒ never_occupied；N2 占比**恰为 0**，不是"很低"。"""
    calib = {1: _profile(200), 2: _profile(190), 3: _profile(210),
             4: [None, None, 2.0, None, 5.0, None, 3.0, 1.0]}  # v7 实况：尾链级
    tv = V.assess_trial_validity(calib)
    by = {c.chamber: c for c in tv.chambers}
    assert by[4].status == V.STATUS_NEVER_OCCUPIED
    assert by[4].occupied_fraction == 0.0
    assert by[4].ever_had_body is False
    assert tv.never_occupied == (4,) and tv.detached == ()
    assert tv.exclude == (4,)                      # 处置同 detached：建议排除
    assert "布置/录制问题" in by[4].note            # 科学含义：非实验失败
    # 对照：真尾级悬挂仍归 detached——两支判据不互换
    calib_d = {**calib, 4: _profile(40)}
    tv_d = V.assess_trial_validity(calib_d)
    assert tv_d.detached == (4,) and tv_d.never_occupied == ()


def test_midtrial_detachment_detected() -> None:
    """G10b 的正样本形态：前半有身体、后半整段无动物级 ⇒ 中途脱落（实验失败）。"""
    calib = {1: _profile(200), 2: _profile(190), 3: _profile(210),
             4: [200.0, 190.0, 210.0, 195.0, None, 3.0, 5.0, None]}
    tv = V.assess_trial_validity(calib)
    by = {c.chamber: c for c in tv.chambers}
    assert by[4].status == V.STATUS_DETACHED
    assert tv.detached == (4,) and tv.never_occupied == ()
    assert "中途脱落" in by[4].note and "G10b" in by[4].note
    # 前半在场——非 0，不落 never 支。**DP-032 后分母是 6 个成功帧不是 8 帧**：
    # 4 帧身体级 / 6 帧看成 = 2/3（旧口径 4/8 = 0.5）。判定不变，只是数值更诚实。
    assert by[4].occupied_fraction == 4 / 6
    assert by[4].unsegmentable_fraction == 0.25
    # 证据不足不判：标定帧 <4 帧时前后半无从谈起，保持 valid 不误伤
    calib3 = {**calib, 4: [200.0, None, 2.0]}
    tv3 = V.assess_trial_validity(calib3)
    assert {c.chamber: c.status for c in tv3.chambers}[4] == V.STATUS_VALID
    # 恰好 4 帧、后半整段消失 ⇒ 可判（门槛在 n≥4 打开）
    calib4 = {**calib, 4: [200.0, None, 2.0, None]}
    tv4 = V.assess_trial_validity(calib4)
    assert {c.chamber: c.status for c in tv4.chambers}[4] == V.STATUS_DETACHED


def _cv_of(chamber: int, calib: dict[int, list[float | None]]):
    tv = V.assess_trial_validity(calib)
    return next(c for c in tv.chambers if c.chamber == chamber)


def test_score_gate_blocks_phantom_immobility_N1_N3() -> None:
    """DP-028 最危险失败模式：空隔间被判 immobility=360 s（伪造药效）。

    N1：排除态不放行——immobility 数字根本出不去；N3：流水线仍递来候选值
    ⇒ **必须报警**（幻影要响），报警不许静默。
    """
    calib = {1: _profile(200), 2: _profile(190), 3: _profile(210), 4: [None] * 8}
    cv_never = _cv_of(4, calib)
    allowed, msgs = V.score_gate(cv_never, candidate_immobility_s=360.0)
    assert allowed is False, "N1：never_occupied 隔间不得产出任何 immobility 数字"
    assert any("PHANTOM-IMMOBILITY" in m for m in msgs), "N3：幻影必须报警"
    assert any("360.0" in m for m in msgs), "报警必须带上被拦截的幻影数值（可审计）"
    # 没有候选值也要拦（闸门不依赖上游犯错才生效），此时不该有幻影报警
    allowed2, msgs2 = V.score_gate(cv_never)
    assert allowed2 is False
    assert not any("PHANTOM" in m for m in msgs2)
    # 各排除/失效态一律不放行
    cv_det = _cv_of(4, {**calib, 4: _profile(40)})
    assert V.score_gate(cv_det, 100.0)[0] is False
    cv_trunc = next(
        c for c in V.assess_trial_validity(
            {**calib, 4: [45.0, 200.0, 180.0, 40.0, 190.0, 210.0, 42.0, 185.0]},
            {1: _profile(200), 2: _profile(190), 3: _profile(210), 4: _profile(42)},
        ).chambers if c.chamber == 4)
    assert cv_trunc.status == V.STATUS_TRUNCATED
    assert V.score_gate(cv_trunc, 200.0)[0] is False, "截断 bug 隔间同样不得产出数字"
    cv_unknown = next(
        c for c in V.assess_trial_validity({k: [None] * 8 for k in (1, 2, 3, 4)})
        .chambers if c.chamber == 1)
    assert cv_unknown.status == V.STATUS_UNKNOWN
    assert V.score_gate(cv_unknown, 50.0)[0] is False
    # valid 放行且无消息
    cv_valid = _cv_of(1, calib)
    assert V.score_gate(cv_valid, 12.3) == (True, ())


# ---------------------------------------------------------------------------
# DP-032：把"一帧都没看成"从"从来没有动物"里分出来
# ---------------------------------------------------------------------------

def test_all_frames_unsegmentable_is_unknown_never_empty() -> None:
    """DP-032 的实况回归：整段隔间分割失败**不得**判成空隔间。

    实测路径（`scripts/dp032_diagnose_chamber.py`）：走廊未收口 +
    `animal_in_corridor` ⇒ 整段一帧掩膜都出不来 ⇒ 面积全 None。
    旧代码把它判 never_occupied，等于静默丢掉一只真动物——`30mg 2周` 隔间 4
    已真实发生，那只鼠至今没被任何人评过分。
    """
    calib = {1: _profile(170), 2: _profile(230), 3: _profile(200),
             4: [None] * 12}
    tv = V.assess_trial_validity(calib)
    by = {c.chamber: c for c in tv.chambers}
    assert by[4].status == V.STATUS_UNKNOWN
    assert by[4].status != V.STATUS_NEVER_OCCUPIED
    assert by[4].occupied_fraction is None
    assert by[4].unsegmentable_fraction == 1.0
    assert by[4].ever_had_body is False
    assert "不得判为空隔间" in by[4].note
    # 不许顶 G10a 的账：假阳性只能由"真的看了、确实没有"产生
    assert tv.never_occupied == ()
    # 但处置仍是不放行——unknown 是排除态，不产出任何 immobility 数字
    allowed, msgs = V.score_gate(by[4], candidate_immobility_s=360.0)
    assert allowed is False and msgs


def test_true_empty_and_unsegmentable_do_not_collapse() -> None:
    """两者签名在实测里逐字相同，但判定必须不同——否则等于把巧合当能力。

    真空隔间（看了 12 帧、全是噪声级尾链）⇒ never_occupied；
    不可分割隔间（12 帧一帧没看成）⇒ unknown。**不许合并成同一个结论。**
    """
    base = {1: _profile(200), 2: _profile(190), 3: _profile(210)}
    empty = V.assess_trial_validity({**base, 4: _profile(4)})
    blind = V.assess_trial_validity({**base, 4: [None] * 12})
    assert empty.never_occupied == (4,) and empty.detached == ()
    assert blind.never_occupied == () and blind.detached == ()
    by_e = {c.chamber: c for c in empty.chambers}
    by_b = {c.chamber: c for c in blind.chambers}
    assert by_e[4].status != by_b[4].status
    assert by_e[4].unsegmentable_fraction == 0.0   # 真的看了
    assert by_b[4].unsegmentable_fraction == 1.0   # 一帧没看成


def test_present_frac_denominator_is_usable_frames_only() -> None:
    """占比分母 = 分割成功的帧，不是全部标定帧。

    DP-032 实测里 `30mg 2周` 隔间 3 有一段 27/50 帧分割失败，
    旧分母会把占比稀释到 0，从而把一个有老鼠的隔间推向 never_occupied。
    """
    half = [200.0, None] * 6            # 6 帧身体级 + 6 帧没看成
    calib = {1: _profile(200), 2: _profile(190), 3: _profile(210), 4: half}
    by = {c.chamber: c for c in V.assess_trial_validity(calib).chambers}
    assert by[4].occupied_fraction == 1.0      # 看成的帧里 6/6 在场，不是 6/12
    assert by[4].unsegmentable_fraction == 0.5
    assert by[4].status == V.STATUS_VALID      # 不再被稀释成排除态


def test_unsegmentable_fraction_recorded_on_every_chamber() -> None:
    """"多少帧根本没看见"必须逐隔间落在记录里，不许静默丢失。"""
    calib = {1: _profile(200), 2: [200.0, None, 200.0, None],
             3: _profile(210), 4: _profile(190)}
    by = {c.chamber: c for c in V.assess_trial_validity(calib).chambers}
    assert by[1].unsegmentable_fraction == 0.0
    assert by[2].unsegmentable_fraction == 0.5
    assert all(c.unsegmentable_fraction is not None
               for c in V.assess_trial_validity(calib).chambers)


def test_unsegmentable_verdict_invariant_to_area_scale() -> None:
    """单位不变量配套：面积 ×4 后"不可分割 ⇒ unknown"不翻转。"""
    calib = {1: _profile(200), 2: _profile(190), 3: _profile(210), 4: [None] * 8}
    a = V.assess_trial_validity(calib)
    b = V.assess_trial_validity(
        {k: [None if x is None else 4 * x for x in p] for k, p in calib.items()})
    assert [(c.status, c.occupied_fraction, c.unsegmentable_fraction)
            for c in a.chambers] == \
           [(c.status, c.occupied_fraction, c.unsegmentable_fraction)
            for c in b.chambers]
