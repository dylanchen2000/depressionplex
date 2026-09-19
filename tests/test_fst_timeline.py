"""三时钟与 protocol_alignment 的纪律测试（Spec A §6.1 + R2-115 T1/T3）。

钉死的口径，每条都对应一个真会犯的错：
- t0 未知 ⇒ 协议时间不存在（None），不是"等于媒体时间"；
- t0 已知 ⇒ 负协议时间**保留**，clamp 到 0 会把适应期折进计分窗；
- t0 已知必须带依据字符串（t0_evidence），随记录落盘；
- analysis_media 钟 + known_t0 但偏移未查到 ⇒ **构造即拒**，
  不静默跳过减法（跳过 = 把未知偏移当 0）；
- 时间量 NaN/Inf ⇒ 拒；
- 名义窗 (120,360) 是常数，但落点由 t0 决定，截断要如实报；
- 覆盖率三分（T1）：解码完整性 / 抽样密度各报各的，缺口逐段列出不补帧；
- 素材角色只认清单登记（T3）：查不到就 unverified，歧义就拒绝猜。
"""

from __future__ import annotations

from depressionplex.fst_research import timeline as tl


def _tb(**kw) -> tl.TimeBase:
    base = dict(fps=25.0, n_frames=10000, clock=tl.CLOCK_SOURCE_MEDIA,
                protocol_alignment=tl.PROTOCOL_ALIGNMENT_UNKNOWN,
                t0_source_s=None, analysis_offset_s=None)
    base.update(kw)
    if (base["protocol_alignment"] == tl.PROTOCOL_ALIGNMENT_KNOWN
            and "t0_evidence" not in base):
        base["t0_evidence"] = "测试依据字符串"      # known_t0 必须带依据
    return tl.TimeBase(**base)


def test_timebase_rejects_bad_construction() -> None:
    for bad in (dict(clock="wall_clock"),
                dict(protocol_alignment="probably_t0"),
                dict(protocol_alignment=tl.PROTOCOL_ALIGNMENT_KNOWN,
                     t0_source_s=None),          # 半承认 known_t0
                dict(fps=0.0),
                dict(fps=float("nan")),          # T3：NaN 不进时间基准
                dict(fps=float("inf")),
                dict(n_frames=-1),
                dict(media_role="guessed"),
                # t0 已知却没依据：t0 是科学事实不是参数
                dict(protocol_alignment=tl.PROTOCOL_ALIGNMENT_KNOWN,
                     t0_source_s=10.0, t0_evidence=""),
                dict(protocol_alignment=tl.PROTOCOL_ALIGNMENT_KNOWN,
                     t0_source_s=10.0, t0_evidence="   "),
                # 时间量 NaN/Inf
                dict(protocol_alignment=tl.PROTOCOL_ALIGNMENT_KNOWN,
                     t0_source_s=float("nan")),
                dict(analysis_offset_s=float("inf"))):
        try:
            _tb(**bad)
        except ValueError:
            continue
        raise AssertionError(f"TimeBase 没拒绝非法构造: {bad}")


def test_analysis_clock_known_t0_without_offset_refused() -> None:
    """R2-115 T3 核心拒绝：偏移未查到 ⇒ 不能换算 t0，构造即炸。

    旧行为是 to_protocol_s/plan_window 里 `offset is not None` 静默跳过减法
    ——把"未知偏移"当 0 用。现在这条组合根本构造不出来。
    """
    try:
        _tb(clock=tl.CLOCK_ANALYSIS_MEDIA, analysis_offset_s=None,
            protocol_alignment=tl.PROTOCOL_ALIGNMENT_KNOWN, t0_source_s=10.0)
    except ValueError as e:
        assert "偏移" in str(e) and ("拒绝" in str(e) or "不静默" in str(e))
    else:
        raise AssertionError("analysis_media + known_t0 + 偏移 None 居然构造成功")
    # unknown 对齐时允许（诊断照跑，账本如实记偏移未查到）
    tb = _tb(clock=tl.CLOCK_ANALYSIS_MEDIA, analysis_offset_s=None)
    assert tb.to_protocol_s(5.0) is None
    assert "不许填 0" in tl.build_ledger(tb).missing[tl.CLOCK_ANALYSIS_MEDIA]


def test_unknown_t0_protocol_time_is_none() -> None:
    tb = _tb()
    assert tb.to_protocol_s(123.4) is None
    assert tb.to_protocol_s(0.0) is None


def test_known_t0_negative_protocol_time_not_clamped() -> None:
    tb = _tb(protocol_alignment=tl.PROTOCOL_ALIGNMENT_KNOWN, t0_source_s=100.0)
    assert abs(tb.to_protocol_s(50.0) - (-50.0)) < 1e-9   # 入水前是负协议时间
    assert tb.to_protocol_s(100.0) == 0.0
    assert abs(tb.to_protocol_s(220.0) - 120.0) < 1e-9


def test_t0_evidence_carried_for_record() -> None:
    """t0 依据字符串随 TimeBase 走（CLI 落进记录），不是只在终端打一行。"""
    tb = _tb(protocol_alignment=tl.PROTOCOL_ALIGNMENT_KNOWN, t0_source_s=100.0,
             t0_evidence="实验记录本第 3 页：入水时刻 10:02:10")
    assert tb.t0_evidence == "实验记录本第 3 页：入水时刻 10:02:10"


def test_analysis_clock_offset_applied_before_t0() -> None:
    # 偏移是**已核实**的换算量才允许进 TimeBase（raw PTS 起点差要另行换算核实）
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


def test_plan_window_noninteger_boundary_frames_floor() -> None:
    """R2-115 T3：窗口边界落不到整帧号时向下取整（边界帧属于窗口）。"""
    tb = _tb(n_frames=20000, protocol_alignment=tl.PROTOCOL_ALIGNMENT_KNOWN,
             t0_source_s=0.1)
    plan = tl.plan_window(tb)
    # (0.1+120)*25 = 3002.5 → 3002；(0.1+360)*25 = 9002.5 → 9002
    assert plan.media_frames == (3002, 9002)
    assert plan.truncated is False


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


# ---------------------------------------------------------------------------
# 覆盖率三分（R2-115 T1）：解码完整性 / 抽样密度分开报，从实际消费帧号数出
# ---------------------------------------------------------------------------

def test_coverage_sampling_density_not_decode_loss() -> None:
    """step=5 抽 1/5 帧且全部解出来 ⇒ 解码完整，抽样密度 0.2——两件事分开。"""
    plan = tl.plan_window(_tb(n_frames=1000))
    consumed = list(range(0, 1000, 5))
    cov = tl.coverage_of(plan, n_frames=1000, consumed=consumed,
                         sample_step=5, fps=25.0)
    assert cov.requested_frames == 1000
    assert cov.consumed_frames == 200
    assert cov.expected_samples == 200
    assert cov.decode_complete is True            # 计划的样点全部真消费
    assert abs(cov.sample_fraction - 0.2) < 1e-12  # 密度是选择，不是损失
    assert abs(cov.sample_spacing_s - 0.2) < 1e-12
    d = cov.to_dict()
    assert d["decode"]["complete"] is True
    assert d["sampling"]["step_frames"] == 5


def test_coverage_step_not_divisible_and_last_frame() -> None:
    """n_frames 不整除 step：样点数按 range 算；末帧恰为样点时被消费。"""
    plan = tl.plan_window(_tb(n_frames=1003))
    consumed = list(range(0, 1003, 5))            # 最后一个是 1000
    cov = tl.coverage_of(plan, n_frames=1003, consumed=consumed, sample_step=5)
    assert cov.expected_samples == len(consumed) == 201
    assert cov.decode_complete is True
    # 1001 帧、step=5 时最后一个样点恰是末帧 1000
    plan2 = tl.plan_window(_tb(n_frames=1001))
    consumed2 = list(range(0, 1001, 5))
    cov2 = tl.coverage_of(plan2, n_frames=1001, consumed=consumed2, sample_step=5)
    assert consumed2[-1] == 1000 and cov2.consumed_frames == 201
    assert cov2.decode_complete is True


def test_coverage_partial_consumption_is_decode_incompleteness() -> None:
    """计划 200 个样点只消费了 150 ⇒ 解码不完整，如实报，不当抽样密度糊弄。"""
    plan = tl.plan_window(_tb(n_frames=1000))
    consumed = list(range(0, 750, 5))             # 后 250 帧没解出来
    cov = tl.coverage_of(plan, n_frames=1000, consumed=consumed, sample_step=5)
    assert cov.expected_samples == 200
    assert cov.consumed_frames == 150
    assert cov.decode_complete is False
    assert abs(cov.sample_fraction - 0.15) < 1e-12


def test_coverage_step1_full_consumption() -> None:
    plan = tl.plan_window(_tb(n_frames=100))
    cov = tl.coverage_of(plan, n_frames=100, consumed=list(range(100)),
                         sample_step=1, fps=25.0)
    assert cov.expected_samples == 100 and cov.consumed_frames == 100
    assert cov.decode_complete is True
    assert abs(cov.sample_fraction - 1.0) < 1e-12
    assert abs(cov.sample_spacing_s - 0.04) < 1e-12


def test_coverage_empty_consumed_list() -> None:
    """空消费序列：0 帧、不完整（有计划样点时）、不炸。"""
    plan = tl.plan_window(_tb(n_frames=1000))
    cov = tl.coverage_of(plan, n_frames=1000, consumed=[], sample_step=5)
    assert cov.consumed_frames == 0
    assert cov.expected_samples == 200
    assert cov.decode_complete is False
    assert cov.sample_fraction == 0.0


def test_coverage_truncation_counted() -> None:
    tb = _tb(n_frames=10000, protocol_alignment=tl.PROTOCOL_ALIGNMENT_KNOWN,
             t0_source_s=100.0)
    plan = tl.plan_window(tb)                  # (5500, 11500)
    consumed = list(range(5500, 10000, 5))     # 素材里只有 5500..9999
    cov = tl.coverage_of(plan, n_frames=10000, consumed=consumed, sample_step=5)
    assert cov.requested_frames == 6000
    assert cov.truncated_frames == 1500        # 请求了但没有的，如实报，不补
    assert cov.expected_samples == len(range(5500, 10000, 5)) == 900
    assert cov.consumed_frames == 900
    assert cov.decode_complete is True         # 素材范围内的样点全消费了


def test_coverage_window_truncation_with_sampling() -> None:
    """窗被素材截断 + 抽帧：截断帧数与样点数各算各的，不互相冒充。"""
    tb = _tb(n_frames=20, protocol_alignment=tl.PROTOCOL_ALIGNMENT_KNOWN,
             t0_source_s=-118.0)
    plan = tl.plan_window(tb)                  # (50, 6050)：整窗在素材之外
    assert plan.truncated is True
    cov = tl.coverage_of(plan, n_frames=20, consumed=list(range(0, 20, 2)),
                         sample_step=2)
    assert cov.requested_frames == 6000
    assert cov.truncated_frames == 6000        # 素材一帧都不在窗内
    assert cov.consumed_frames == 0            # 窗内消费 0
    assert cov.expected_samples == 0           # 窗内应消费样点也是 0
    assert cov.sample_fraction == 0.0


def test_coverage_gaps_listed_not_padded() -> None:
    # fps=1 让帧号与秒数值一致，窗口 (120,360) 直接可读
    tb = tl.TimeBase(fps=1.0, n_frames=1000, clock=tl.CLOCK_SOURCE_MEDIA,
                     protocol_alignment=tl.PROTOCOL_ALIGNMENT_KNOWN,
                     t0_source_s=0.0, analysis_offset_s=None,
                     t0_evidence="测试依据")
    plan = tl.plan_window(tb)                  # (120, 360) 帧
    consumed = [f for f in range(120, 360)
                if not (150 <= f < 160)]       # 缺 10 帧真没解出来
    cov = tl.coverage_of(plan, n_frames=1000, consumed=consumed,
                         gaps=((150, 160),), sample_step=1)
    assert cov.requested_frames == 240
    assert cov.consumed_frames == 230          # 缺 10 帧照扣，不静默补
    assert cov.decode_complete is False        # 有缺口就不完整
    assert cov.gaps == ((150, 160),)
    d = cov.to_dict()
    assert d["decode"]["gaps"] == [[150, 160]]
    assert d["decode"]["complete"] is False


def test_coverage_consumed_outside_window_ignored() -> None:
    tb = tl.TimeBase(fps=1.0, n_frames=1000, clock=tl.CLOCK_SOURCE_MEDIA,
                     protocol_alignment=tl.PROTOCOL_ALIGNMENT_KNOWN,
                     t0_source_s=0.0, analysis_offset_s=None,
                     t0_evidence="测试依据")
    plan = tl.plan_window(tb)                  # (120, 360)
    cov = tl.coverage_of(plan, n_frames=1000, consumed=[50, 120, 200, 999])
    assert cov.consumed_frames == 2            # 只有 120、200 在窗内


def test_ledger_missing_entries() -> None:
    led = tl.build_ledger(_tb())
    assert led.in_use == tl.CLOCK_SOURCE_MEDIA
    assert "t0" in led.missing[tl.CLOCK_PROTOCOL]
    # 角色未核实（默认）：不许声称"这是原视频"
    assert "转码件" in led.missing[tl.CLOCK_ANALYSIS_MEDIA]
    assert "未经清单核实" in led.missing[tl.CLOCK_ANALYSIS_MEDIA]


def test_ledger_source_role_says_verified() -> None:
    tb = _tb(media_role=tl.MEDIA_ROLE_SOURCE)
    led = tl.build_ledger(tb)
    assert "source_video" in led.missing[tl.CLOCK_ANALYSIS_MEDIA]
    assert "未经清单核实" not in led.missing[tl.CLOCK_ANALYSIS_MEDIA]


def test_ledger_complete_case_has_no_missing() -> None:
    tb = _tb(clock=tl.CLOCK_ANALYSIS_MEDIA, analysis_offset_s=0.54,
             offset_evidence="DP-133 清单实测 raw PTS 起点差（换算已核实）",
             protocol_alignment=tl.PROTOCOL_ALIGNMENT_KNOWN, t0_source_s=10.0)
    led = tl.build_ledger(tb)
    assert led.missing == {}
    assert led.to_dict() == {"in_use": tl.CLOCK_ANALYSIS_MEDIA, "missing": {}}


# ---------------------------------------------------------------------------
# 素材角色映射（R2-115 T3）：只认清单登记行，歧义拒绝猜
# ---------------------------------------------------------------------------

def _lookup(*roles: str, found: bool = True) -> dict:
    return {"found": found,
            "aliases": [{"material_id": f"M{i}", "role": r, "path": "x",
                         "t0_status": "", "t0_source_s": ""}
                        for i, r in enumerate(roles)]}


def test_resolve_media_role_source() -> None:
    role, ev, problems = tl.resolve_media_role(_lookup("source_video"))
    assert role == tl.MEDIA_ROLE_SOURCE and problems == []
    assert "source_video" in ev


def test_resolve_media_role_transcode_variants() -> None:
    for r in ("csi_transcode", "transcode_intermediate", "transcode"):
        role, ev, problems = tl.resolve_media_role(_lookup(r))
        assert role == tl.MEDIA_ROLE_TRANSCODE, r
        assert problems == []
        assert "转码件" in ev and "不许填 0" in ev


def test_resolve_media_role_unverified_cases() -> None:
    role, ev, problems = tl.resolve_media_role(None)          # 没给清单
    assert role == tl.MEDIA_ROLE_UNVERIFIED and problems == []
    assert "未经清单核实" in ev
    role, ev, problems = tl.resolve_media_role(_lookup("x", found=False))
    assert role == tl.MEDIA_ROLE_UNVERIFIED                   # sha 未登记
    role, ev, problems = tl.resolve_media_role(_lookup("csi_clb"))
    assert role == tl.MEDIA_ROLE_UNVERIFIED                   # 非视频角色


def test_resolve_media_role_ambiguous_refuses() -> None:
    """同一 sha 既是 source_video 又是转码件 ⇒ 歧义，problems 非空（调用方拒）。"""
    role, ev, problems = tl.resolve_media_role(
        _lookup("source_video", "csi_transcode"))
    assert problems and "歧义" in problems[0] and "拒绝猜" in problems[0]
