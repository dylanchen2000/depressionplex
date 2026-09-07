"""逐段时间线导出测试（DP-061）。

钉死的口径：
- 段长之和 **= 已发布的总时长**（软件侧 = `seconds_pipeline`，人工侧 =
  `mobile_union_s`）——导出层不许另算一遍；
- 时基统一到**录像起点**：软件段平移 `window_start_s`（FST 差 120 s），
  人工段**不平移**（秒表本来就是录像时基）；
- **没产出 ≠ 零段**：未放行的试次出说明行，`start_s` 留空，**不写 0**（DP-032）；
- 人工按键乱序 ⇒ 先排序再并集，段必互不重叠且升序（DP-012 规则 2）。
"""

from __future__ import annotations

import csv
import tempfile
from pathlib import Path

import numpy as np

from depressionplex import human_agreement
from depressionplex.assay_core import timeline as TL
from depressionplex.assay_core import trial_report as TR
from depressionplex.assay_core import validity
from depressionplex.assay_core.rules import TstEventLabels

FPS = 10.0
N = 3600          # 360 s 整


def _labels(mob=(), unknown=(), n=N):
    """按 (起秒, 止秒) 闭区间列表铺 True（与 test_trial_report 同形）。"""
    def fill(spans):
        a = np.zeros(n, dtype=bool)
        for s0, s1 in spans:
            a[int(s0 * FPS):int(s1 * FPS) + 1] = True
        return a
    return TstEventLabels(
        mobility=fill(mob), immobility=np.zeros(n, dtype=bool),
        passive_swing=np.zeros(n, dtype=bool),
        tail_climbing=np.zeros(n, dtype=bool),
        forelimb_only=np.zeros(n, dtype=bool),
        unknown=fill(unknown),
    )


def _cv(status):
    return validity.ChamberValidity(
        chamber=1, status=status,
        max_area=0.0 if status == "never_occupied" else 50.0,
        ref_body_area=188.0, body_threshold=94.0,
        ever_had_body=(status == validity.STATUS_VALID),
        note="测试口径", occupied_fraction=None)


# ---- 段长之和必须等于已发布的总时长 ---------------------------------------------

def test_software_segment_durations_sum_to_pipeline_seconds():
    """导出层不许另算一遍：段长之和逐位等于 `seconds_pipeline`。"""
    lab = _labels(mob=[(10.0, 20.0), (100.0, 130.0), (300.0, 301.0)])
    r = TR.build_trial_report(lab, fps=FPS, assay="TST", trial_id="t")
    mob = r.categories["Mobility"]
    assert mob.segments_s, "有 3 段活动却导出 0 段"
    total = sum(b - a for a, b in mob.segments_s)
    assert abs(total - mob.seconds_pipeline) < 1e-9, (
        "段长之和 %.6f ≠ 已发布 %.6f" % (total, mob.seconds_pipeline))


def test_software_segments_sorted_disjoint_and_within_window():
    lab = _labels(mob=[(10.0, 20.0), (100.0, 130.0)])
    r = TR.build_trial_report(lab, fps=FPS, assay="TST", trial_id="t")
    segs = r.categories["Mobility"].segments_s
    for (a, b) in segs:
        assert 0.0 <= a < b <= r.window_seconds
    for i in range(len(segs) - 1):
        assert segs[i][1] <= segs[i + 1][0], "段重叠或未排序：%s" % (segs,)


# ---- 时基：FST 必须平移 120 s，抹平即口径事故 --------------------------------------

def test_fst_software_segments_shifted_to_recording_time():
    lab = _labels(mob=[(150.0, 160.0)])
    r = TR.build_trial_report(lab, fps=FPS, assay="FST", trial_id="t")
    win_rel = r.categories["Mobility"].segments_s[0][0]
    rows = TL.rows_from_report(r)
    rec_abs = rows[0]["start_s"]
    # 窗口相对是 30 s 附近，录像绝对是 150 s 附近，两者差正好 window_start_s
    assert abs(win_rel - 30.0) < 0.2, win_rel
    assert abs(rec_abs - 150.0) < 0.2, rec_abs
    assert abs(rec_abs - win_rel - 120.0) < 1e-9
    assert rows[0]["window_start_s"] == 120.0


def test_tst_software_segments_need_no_shift():
    lab = _labels(mob=[(10.0, 20.0)])
    r = TR.build_trial_report(lab, fps=FPS, assay="TST", trial_id="t")
    rows = TL.rows_from_report(r)
    assert abs(rows[0]["start_s"] - 10.0) < 0.2
    assert rows[0]["window_start_s"] == 0.0


def test_human_segments_are_not_shifted_even_for_fst():
    """人工秒表记的就是录像时基。`window_start_s` 只作记录，不参与平移。"""
    rows = TL.rows_from_human_holds(((5.0, 7.0),), trial_id="t",
                                    scorer_id="某评分员", assay="FST")
    assert rows[0]["start_s"] == 5.0
    assert rows[0]["window_start_s"] == 120.0


# ---- 没产出 ≠ 零段（DP-032） -----------------------------------------------------

def test_excluded_chamber_emits_note_row_not_zero_segment():
    lab = _labels(mob=[(10.0, 20.0)])
    r = TR.build_trial_report(lab, fps=FPS, assay="TST", trial_id="t",
                              chamber_validity=_cv("never_occupied"))
    assert r.scored is False, "前置假设变了：这里需要一个未放行的试次"
    rows = TL.rows_from_report(r)
    assert len(rows) == 1
    row = rows[0]
    assert row["start_s"] == "" and row["end_s"] == "" \
        and row["duration_s"] == "" and row["seg_index"] == "", \
        "未放行的试次被写成了 0 长段：0 会被下游读成'一秒都没动过'"
    assert row["note"], "说明行没给原因"


def test_zero_segments_vs_not_produced_are_distinguishable():
    """窗口内确实 0 段活动，与"未放行没产出"是两件事，note 必须能分开。"""
    r0 = TR.build_trial_report(_labels(mob=[]), fps=FPS, assay="TST",
                               trial_id="t0")
    zero = TL.rows_from_report(r0)[0]
    assert zero["start_s"] == "" and "scored=True" in zero["note"]
    r1 = TR.build_trial_report(_labels(mob=[(10.0, 20.0)]), fps=FPS,
                               assay="TST", trial_id="t1",
                               chamber_validity=_cv("never_occupied"))
    blocked = TL.rows_from_report(r1)[0]
    assert "scored=True" not in blocked["note"]


def test_note_row_refuses_empty_reason():
    try:
        TL.note_row(trial_id="t", assay="TST", source="software", note="")
    except ValueError:
        return
    raise AssertionError("没有原因的说明行被放过了——那等于把'没有'伪装成'零'")


# ---- 人工并集段（DP-012 规则 2：先排序再并集） ---------------------------------------

def test_union_holds_segments_sum_equals_published_total():
    u = human_agreement.union_holds([[1.0, 3.0], [2.5, 4.0], [10.0, 11.0]])
    assert u.segments == ((1.0, 4.0), (10.0, 11.0))
    assert abs(sum(b - a for a, b in u.segments) - u.total_s) < 0.01


def test_union_holds_segments_sorted_when_input_unsorted():
    u = human_agreement.union_holds([[10.0, 11.0], [1.0, 3.0]])
    assert u.unsorted is True
    assert u.segments == ((1.0, 3.0), (10.0, 11.0))


def test_zero_length_hold_produces_no_segment():
    u = human_agreement.union_holds([[5.0, 5.0], [7.0, 6.0]])
    assert u.segments == ()
    assert u.total_s == 0.0


def test_human_empty_segments_emit_note_row():
    rows = TL.rows_from_human_holds((), trial_id="t", scorer_id="某评分员")
    assert len(rows) == 1 and rows[0]["start_s"] == "" and rows[0]["note"]


# ---- 表结构 -----------------------------------------------------------------

def test_write_csv_columns_pinned_and_roundtrip():
    lab = _labels(mob=[(10.0, 20.0)])
    r = TR.build_trial_report(lab, fps=FPS, assay="TST", trial_id="试次-ch1")
    rows = TL.rows_from_report(r) + TL.rows_from_human_holds(
        ((11.0, 19.0),), trial_id="试次-ch1", scorer_id="某评分员")
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "tl.csv"
        n = TL.write_csv(rows, p)
        assert n == len(rows)
        with p.open(encoding="utf-8") as fh:
            got = list(csv.DictReader(fh))
        assert tuple(got[0]) == TL.TIMELINE_FIELDS
        assert {g["source"] for g in got} == {"software", "human"}
        assert got[-1]["scorer_id"] == "某评分员"
