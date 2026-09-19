"""三时钟与 protocol_alignment 的纪律测试（Spec A §6.1）。

钉死的口径，每条都对应一个真会犯的错：
- t0 未知 ⇒ 协议时间不存在（None），不是"等于媒体时间"；
- t0 已知 ⇒ 负协议时间**保留**，clamp 到 0 会把适应期折进计分窗；
- 名义窗 (120,360) 是常数，但落点由 t0 决定，截断要如实报；
- 缺口逐段列出，不静默补帧。
"""

from __future__ import annotations

from depressionplex.fst_research import timeline as tl


def _tb(**kw) -> tl.TimeBase:
    base = dict(fps=25.0, n_frames=10000, clock=tl.CLOCK_SOURCE_MEDIA,
                protocol_alignment=tl.PROTOCOL_ALIGNMENT_UNKNOWN,
                t0_source_s=None, analysis_offset_s=None)
    base.update(kw)
    return tl.TimeBase(**base)


def test_timebase_rejects_bad_construction() -> None:
    for bad in (dict(clock="wall_clock"),
                dict(protocol_alignment="probably_t0"),
                dict(protocol_alignment=tl.PROTOCOL_ALIGNMENT_KNOWN,
                     t0_source_s=None),          # 半承认 known_t0
                dict(fps=0.0)):
        try:
            _tb(**bad)
        except ValueError:
            continue
        raise AssertionError(f"TimeBase 没拒绝非法构造: {bad}")


def test_unknown_t0_protocol_time_is_none() -> None:
    tb = _tb()
    assert tb.to_protocol_s(123.4) is None
    assert tb.to_protocol_s(0.0) is None


def test_known_t0_negative_protocol_time_not_clamped() -> None:
    tb = _tb(protocol_alignment=tl.PROTOCOL_ALIGNMENT_KNOWN, t0_source_s=100.0)
    assert abs(tb.to_protocol_s(50.0) - (-50.0)) < 1e-9   # 入水前是负协议时间
    assert tb.to_protocol_s(100.0) == 0.0
    assert abs(tb.to_protocol_s(220.0) - 120.0) < 1e-9


def test_analysis_clock_offset_applied_before_t0() -> None:
    # DP-133 实测：分析素材时间 = 源媒体时间 + 0.54 s（查出来的，不是假设）
    tb = _tb(clock=tl.CLOCK_ANALYSIS_MEDIA, analysis_offset_s=0.54,
             protocol_alignment=tl.PROTOCOL_ALIGNMENT_KNOWN, t0_source_s=100.0)
    assert abs(tb.to_protocol_s(150.54) - 50.0) < 1e-9


def test_frame_time_roundtrip() -> None:
    tb = _tb()
    assert tb.frame_to_s(250) == 10.0
    assert tb.s_to_frame(10.0) == 250
    assert tb.s_to_frame(10.039) == 250        # 向下取整
    assert tb.duration_s == 10000 / 25.0


def test_plan_window_unknown_alignment_no_window() -> None:
    plan = tl.plan_window(_tb())
    assert plan.media_frames is None
    assert plan.applies_standard_window is False
    assert plan.alignment == tl.PROTOCOL_ALIGNMENT_UNKNOWN
    assert "自动截最后 4 分钟" in plan.reason       # 原文是"**不**自动截…"
    assert "t0" in plan.reason
    assert plan.clamped is False and plan.truncated is False


def test_plan_window_known_t0_frames() -> None:
    tb = _tb(n_frames=20000, protocol_alignment=tl.PROTOCOL_ALIGNMENT_KNOWN,
             t0_source_s=10.0)
    plan = tl.plan_window(tb)
    assert plan.media_frames == (3250, 9250)   # (10+120)*25, (10+360)*25
    assert plan.applies_standard_window is True
    assert plan.truncated is False and plan.clamped is False
    assert plan.requested_s == tl.NOMINAL_WINDOW_S


def test_plan_window_truncation_flagged_not_hidden() -> None:
    tb = _tb(n_frames=10000, protocol_alignment=tl.PROTOCOL_ALIGNMENT_KNOWN,
             t0_source_s=100.0)
    plan = tl.plan_window(tb)
    assert plan.media_frames == (5500, 11500)
    assert plan.truncated is True              # b 超出素材末尾，如实标


def test_plan_window_analysis_clock_shifts_t0() -> None:
    tb = _tb(n_frames=20000, clock=tl.CLOCK_ANALYSIS_MEDIA,
             analysis_offset_s=0.54,
             protocol_alignment=tl.PROTOCOL_ALIGNMENT_KNOWN, t0_source_s=100.0)
    plan = tl.plan_window(tb)
    a, b = plan.media_frames
    assert a == int((100.0 + 0.54 + 120.0) * 25)
    assert b == int((100.0 + 0.54 + 360.0) * 25)


def test_coverage_unknown_plan_uses_whole_timeline() -> None:
    plan = tl.plan_window(_tb(n_frames=1000))
    cov = tl.coverage_of(plan, n_frames=1000,
                         observed=list(range(0, 1000, 5)))
    assert cov.requested_frames == 1000
    assert cov.observed_frames == 200
    assert abs(cov.coverage_frac - 0.2) < 1e-12
    assert cov.truncated_frames == 0


def test_coverage_truncation_counted() -> None:
    tb = _tb(n_frames=10000, protocol_alignment=tl.PROTOCOL_ALIGNMENT_KNOWN,
             t0_source_s=100.0)
    plan = tl.plan_window(tb)                  # (5500, 11500)
    cov = tl.coverage_of(plan, n_frames=10000)
    assert cov.requested_frames == 6000
    assert cov.observed_frames == 4500         # 素材里只有 5500..9999
    assert cov.truncated_frames == 1500        # 请求了但没有的，如实报，不补
    assert abs(cov.coverage_frac - 0.75) < 1e-12


def test_coverage_gaps_listed_not_padded() -> None:
    # fps=1 让帧号与秒数值一致，窗口 (120,360) 直接可读
    tb = tl.TimeBase(fps=1.0, n_frames=1000, clock=tl.CLOCK_SOURCE_MEDIA,
                     protocol_alignment=tl.PROTOCOL_ALIGNMENT_KNOWN,
                     t0_source_s=0.0, analysis_offset_s=None)
    plan = tl.plan_window(tb)                  # (120, 360) 帧
    cov = tl.coverage_of(plan, n_frames=1000, gaps=((150, 160),))
    assert cov.requested_frames == 240
    assert cov.observed_frames == 230          # 缺 10 帧照扣，不静默补
    assert cov.gaps == ((150, 160),)
    d = cov.to_dict()
    assert d["gaps"] == [[150, 160]]


def test_coverage_observed_outside_window_ignored() -> None:
    tb = tl.TimeBase(fps=1.0, n_frames=1000, clock=tl.CLOCK_SOURCE_MEDIA,
                     protocol_alignment=tl.PROTOCOL_ALIGNMENT_KNOWN,
                     t0_source_s=0.0, analysis_offset_s=None)
    plan = tl.plan_window(tb)                  # (120, 360)
    cov = tl.coverage_of(plan, n_frames=1000, observed=[50, 120, 200, 999])
    assert cov.observed_frames == 2            # 只有 120、200 在窗内


def test_ledger_missing_entries() -> None:
    led = tl.build_ledger(_tb())
    assert led.in_use == tl.CLOCK_SOURCE_MEDIA
    assert "t0" in led.missing[tl.CLOCK_PROTOCOL]
    assert "转码件" in led.missing[tl.CLOCK_ANALYSIS_MEDIA]


def test_ledger_analysis_clock_without_offset_refuses_zero() -> None:
    tb = _tb(clock=tl.CLOCK_ANALYSIS_MEDIA, analysis_offset_s=None,
             protocol_alignment=tl.PROTOCOL_ALIGNMENT_KNOWN, t0_source_s=10.0)
    led = tl.build_ledger(tb)
    assert "不许填 0" in led.missing[tl.CLOCK_ANALYSIS_MEDIA]
    assert tl.CLOCK_PROTOCOL not in led.missing      # t0 已知，协议钟不缺


def test_ledger_complete_case_has_no_missing() -> None:
    tb = _tb(clock=tl.CLOCK_ANALYSIS_MEDIA, analysis_offset_s=0.54,
             offset_evidence="DP-133 清单实测 pts 差",
             protocol_alignment=tl.PROTOCOL_ALIGNMENT_KNOWN, t0_source_s=10.0)
    led = tl.build_ledger(tb)
    assert led.missing == {}
    assert led.to_dict() == {"in_use": tl.CLOCK_ANALYSIS_MEDIA, "missing": {}}
