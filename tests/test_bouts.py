"""bout 流水线测试。关键用例直接来自 CSI 手册给出的数值例子。"""

from __future__ import annotations

from depressionplex.assay_core import bouts as B


def test_manual_merge_example() -> None:
    """ForcedSwimScan / TailSuspScan 手册原文例子。

    "if this threshold was set at 60 and if the previous escape bout begins and
    ends at frames 1050 and 1145 respectively and the next escape bout begins
    and ends at frames 1186 and 1360 respectively, these two records will be
    combined into a single larger struggle record that starts and ends at
    frames 1050 and 1360 ... because the gap between the two records
    (1186-1145) is smaller than 60."
    """
    a = B.Interval(1050, 1145)
    b = B.Interval(1186, 1360)
    assert B._gap(a, b) == 41, "手册明确把间隔定义为 1186-1145=41"

    merged = B.merge_by_gap([a, b], 60)
    assert merged == [B.Interval(1050, 1360)], merged

    # 间隔 41，若阈值降到 41 则不应合并（严格小于）
    assert B.merge_by_gap([a, b], 41) == [a, b]
    assert B.merge_by_gap([a, b], 42) == [B.Interval(1050, 1360)]


def test_pipeline_order_matters() -> None:
    """First Combine 必须在 Noise Thresh 之前——这是手册明确强调的顺序。

    构造两段各 1 帧、间隔 2 的记录。若先去噪（阈值 5），两段都会被删光；
    只有先预合并成 1 段（长度 4）再去噪，才可能留下来。
    """
    raw = [B.Interval(100, 100), B.Interval(103, 103)]
    params = B.BoutParams(
        first_combine_limit=5,
        noise_thresh_frames=4,
        combination_limit=0,
        length_thresh_frames=0,
    )
    got = B.run_pipeline(raw, params)
    assert got == [B.Interval(100, 103)], got

    # 关掉预合并，同样的输入就应该被去噪清空——反证顺序确实起作用
    no_pre = B.run_pipeline(raw, params.__class__(**{**params.__dict__, "first_combine_limit": 0}))
    assert no_pre == [], no_pre


def test_length_thresh_applied_last() -> None:
    """Length Thresh 是"所有合并完成后"的最终过滤。"""
    raw = [B.Interval(0, 9), B.Interval(12, 20)]
    params = B.BoutParams(
        first_combine_limit=0,
        noise_thresh_frames=0,
        combination_limit=5,  # gap = 12-9 = 3 < 5 → 合并成 [0,20]，长度 21
        length_thresh_frames=21,
    )
    assert B.run_pipeline(raw, params) == [B.Interval(0, 20)]

    # 若最终阈值再高一帧，则连合并后的段也应被删掉
    strict = B.BoutParams(**{**params.__dict__, "length_thresh_frames": 22})
    assert B.run_pipeline(raw, strict) == []


def test_bin_vote_manual_example() -> None:
    """手册例子：bin=5 秒，bin 内行为 >2.5 秒才整箱计入，否则整箱不计。"""
    fps = 10.0
    total = 100  # 10 秒 → 2 个 bin
    # 第 1 个 bin（帧 0-49）内占 30 帧 = 3 秒 > 2.5 秒 → 整箱计入（50 帧）
    # 第 2 个 bin（帧 50-99）内占 20 帧 = 2 秒 < 2.5 秒 → 整箱不计
    ivs = [B.Interval(0, 29), B.Interval(50, 69)]
    votes, counted = B.bin_vote(
        ivs, total_frames=total, fps=fps, section_size_seconds=5.0
    )
    assert votes == [True, False], votes
    assert counted == 50, counted


def test_bin_vote_is_strictly_greater_than_half() -> None:
    """恰好半个 bin 不应计入（手册说 "more than"）。"""
    fps = 10.0
    ivs = [B.Interval(0, 24)]  # 25 帧，正好半个 50 帧的 bin
    votes, counted = B.bin_vote(
        ivs, total_frames=50, fps=fps, section_size_seconds=5.0
    )
    assert votes == [False], votes
    assert counted == 0


def test_intervals_from_labels() -> None:
    labels = ["I", "I", "M", "M", "M", "I", "M"]
    assert B.intervals_from_labels(labels, "M") == [
        B.Interval(2, 4),
        B.Interval(6, 6),
    ]
    assert B.intervals_from_labels(labels, "I") == [
        B.Interval(0, 1),
        B.Interval(5, 5),
    ]
    assert B.intervals_from_labels([], "M") == []


def test_score_methods_ordering() -> None:
    """frame（不平滑）应 >= range（平滑后），average 介于两者之间。"""
    labels = ["I"] * 300
    for f in (10, 11, 12, 40, 41, 100, 101, 102, 103, 104, 105):
        labels[f] = "M"
    p_frame = B.BoutParams(score_method=B.SCORE_FRAME)
    p_range = B.BoutParams(score_method=B.SCORE_RANGE)
    p_avg = B.BoutParams(score_method=B.SCORE_AVERAGE)

    s_frame = B.score(labels, "M", params=p_frame, fps=30.0)
    s_range = B.score(labels, "M", params=p_range, fps=30.0)
    s_avg = B.score(labels, "M", params=p_avg, fps=30.0)

    assert s_frame.frames == 11, s_frame.frames
    # 默认 length_thresh=30，这些碎片合并后仍不足 30 帧 → 全被清掉
    assert s_range.frames == 0, s_range.frames
    assert s_range.frames <= s_avg.frames <= s_frame.frames


def test_csi_defaults_match_manual_screenshot() -> None:
    """默认值须与 TailSuspScan 手册 Fig 4.1 的 Forced Swim Settings 面板一致。"""
    d = B.CSI_FST_DEFAULTS
    assert d.first_combine_limit == 5
    assert d.noise_thresh_frames == 5
    assert d.combination_limit == 30
    assert d.length_thresh_frames == 30
    assert d.section_size_seconds == 5.0


def test_summary_fields() -> None:
    ivs = [B.Interval(0, 29), B.Interval(60, 89)]
    s = B.summarize(ivs, total_frames=120, fps=30.0)
    assert s.bouts == 2
    assert s.frames == 60
    assert abs(s.seconds - 2.0) < 1e-9
    assert abs(s.duration_pct - 50.0) < 1e-9
    assert abs(s.mean_bout_frames - 30.0) < 1e-9
    assert s.first_onset_frame == 0
