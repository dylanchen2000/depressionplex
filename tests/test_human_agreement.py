"""DP-012 校验器测试：四条硬规则 + 真值数据回归。

真值基线取自 `docs/审计_人工评分_四人配对_2026-09-03.md`（已发表数字，改任一
断言前必须回到审计核对——这些数字是 DP-014 报告引用的出处）：
43 试次 / 12 乱序 / 零长段 王0 陈0 徐1 张3 / 朴素虚高最大 +54.8 s /
每按键墙钟 0.121 s（逐试次口径更抖）/ mobile 均值 195.7/167.1/115.0/117.8。
"""

from __future__ import annotations

import importlib.util
import json
import random
import tempfile
from pathlib import Path

from depressionplex.human_agreement import (
    REJECT_UNSCORED_TRIPLE,
    STATUS_ACCEPTED,
    STATUS_REJECTED,
    TST_WINDOW_S,
    TrialRejected,
    CSV_COLUMNS,
    build_table,
    crosscheck_summary_csv,
    load_audit_json,
    load_salvaged_csv,
    table_csv_text,
    union_holds,
)

REPO = Path(__file__).resolve().parents[1]
RAW = REPO / "data" / "human_scores" / "raw"
COMMITTED_TABLE = REPO / "data" / "human_scores" / "recomputed" / "human_scores_recomputed_DP-012.csv"


def _load_salvage_ref():
    """按文件路径 import 抢救工具（DP-030 后在 depressionplex/cli/，旧路径兜底）。"""
    for rel in ("depressionplex/cli/salvage_truncated_audit.py",
                "cli/salvage_truncated_audit.py"):
        p = REPO / rel
        if p.exists():
            break
    else:
        raise AssertionError("找不到 salvage_truncated_audit.py——位置变了？")
    spec = importlib.util.spec_from_file_location("salvage_ref", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- 规则 2：排序取并集


def test_union_requires_sorting_self_nested() -> None:
    """乱序自嵌套（真实事故形态：回看重按追加到数组尾部）。"""
    holds = [[10.0, 20.0], [0.0, 30.0]]  # 后段完全罩住前段
    u = union_holds(holds)
    assert u.unsorted  # 起点 10 > 0 ⇒ 乱序
    assert u.total_s == 30.0, "并集 = [0,30]，不是段内和 10+20"
    assert u.naive_sum_s == 40.0  # 朴素求和虚高 +10：嵌套段被重算
    holds2 = [[50.0, 60.0], [0.0, 40.0], [10.0, 25.0]]
    u2 = union_holds(holds2)
    assert u2.unsorted
    assert u2.total_s == 50.0  # [0,40] ∪ [50,60]，[10,25] 被 [0,40] 罩住
    assert u2.naive_sum_s == 65.0  # 10+40+15，虚高 +15


def test_union_touching_overlap_and_empty() -> None:
    assert union_holds([]).total_s == 0.0
    assert union_holds([[0.0, 5.0], [5.0, 10.0]]).total_s == 10.0
    assert union_holds([[0.0, 5.0], [4.0, 6.0]]).total_s == 6.0
    # 完全包含且不排序：必须仍是并集不是和
    assert union_holds([[2.0, 3.0], [0.0, 10.0]]).total_s == 10.0


def test_union_zero_length_bookkeeping() -> None:
    """规则 4：零长段并集口径下为 0，但要记账。"""
    u = union_holds([[1.0, 1.0], [0.0, 0.0], [2.0, 5.0], [7.0, 6.0]])
    assert u.zero_length == 3  # 两个零长 + 一个负长（b<a 同样非法，计入）
    assert u.total_s == 3.0
    assert u.n_segments == 4


def test_union_equivalence_with_salvage_tool() -> None:
    """与 `depressionplex/cli/salvage_truncated_audit.union_seconds` 逐案等价（张抢救件就是
    它算的），钉住两份实现不分叉。"""
    ref = _load_salvage_ref()
    rng = random.Random(20260903)
    for _ in range(300):
        n = rng.randint(0, 12)
        holds = []
        for _ in range(n):
            a = round(rng.uniform(0, 360), 2)
            span = rng.choice([0.0, round(rng.uniform(0.01, 40), 2)])
            holds.append([a, round(a + span, 2)])
        if rng.random() < 0.4:  # 制造乱序
            rng.shuffle(holds)
        total, nseg, unsorted, zero = ref.union_seconds(holds)
        u = union_holds(holds)
        assert u.total_s == round(total, 2), holds
        assert u.n_segments == nseg
        assert u.unsorted == unsorted
        assert u.zero_length == zero


# ---------------------------------------------------------------- 规则 3：拒绝入库


def _mk_record(trial_id="v-ch1", mobile=10.0, holds=None, unscoreable=False):
    return {
        "trial_id": trial_id, "mobile_seconds": mobile, "tail_climbing": False,
        "unscoreable": unscoreable, "note": "", "playback_rate": 0.5,
        "holds": holds if holds is not None else [[0.0, 20.0]],
        "presentation_order": 1, "scored_at": "2026-09-03",
    }


def _mk_doc(records, **extra):
    doc = {
        "format": "depressionplex.stopwatch-audit.v1", "partial": False,
        "done_count": len(records), "total_trials": len(records),
        "scorer_id": "测试员", "seed": 1, "delivered_order": [r["trial_id"] for r in records],
        "tool_version": "test", "exported_at": "", "records": records,
    }
    doc.update(extra)
    return doc


def _write_doc(tmp: Path, records, name="timer_audit_测试员_x.json", **extra) -> Path:
    p = tmp / name
    p.write_text(json.dumps(_mk_doc(records, **extra), ensure_ascii=False),
                 encoding="utf-8")
    return p


def test_reject_rule_exactly_the_triple() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # 命中：mobile==0 && holds==[] && unscoreable==false
        p = _write_doc(tmp, [_mk_record(mobile=0, holds=[], unscoreable=False)])
        _, rows = load_audit_json(p)
        assert rows[0].status == STATUS_REJECTED
        assert rows[0].mobile_union_s is None  # 不得按 0 入库
        assert rows[0].immobility_s is None    # 更不得变 360
        assert rows[0].reject_reason == REJECT_UNSCORED_TRIPLE

        # 不命中：unscoreable==true（"评不了"是合法判定）
        p2 = _write_doc(tmp, [_mk_record(mobile=0, holds=[], unscoreable=True)],
                        "timer_audit_测试员_y.json")
        _, rows2 = load_audit_json(p2)
        assert rows2[0].status != STATUS_REJECTED

        # 不命中：mobile>0 但 holds 空（数据矛盾 ⇒ 并集口径仍重算为 0，但不属 rule 3 三联）
        p3 = _write_doc(tmp, [_mk_record(mobile=5.0, holds=[])], "timer_audit_测试员_z.json")
        _, rows3 = load_audit_json(p3)
        assert rows3[0].status == STATUS_ACCEPTED
        assert rows3[0].mobile_union_s == 0.0
        assert rows3[0].mobile_seconds_DISCARDED == 5.0


def test_strict_mode_raises_for_pipeline_consumers() -> None:
    with tempfile.TemporaryDirectory() as td:
        p = _write_doc(Path(td), [_mk_record(mobile=0, holds=[])])
        try:
            load_audit_json(p, on_reject="raise")
            assert False, "strict 消费方必须收到 TrialRejected"
        except TrialRejected as e:
            assert e.trial_id == "v-ch1" and e.scorer_id == "测试员"


def test_window_exceeded_alarms_not_clamped() -> None:
    """静默兜底禁令：越窗 holds 必须报警，且不得 clamp——clamp 就是把问题吃掉。"""
    with tempfile.TemporaryDirectory() as td:
        p = _write_doc(Path(td), [_mk_record(mobile=50.0, holds=[[0.0, 400.0]])])
        _, rows = load_audit_json(p)
        assert "hold_beyond_window" in rows[0].warnings
        assert rows[0].mobile_union_s == 400.0        # 如实，不截到 360
        assert rows[0].immobility_s == TST_WINDOW_S - 400.0  # 负数可见，让下游炸


def test_window_bound_follows_recording_length_not_tst_360() -> None:
    """FST 录像比 360 s 长，分母必须是该场 window_s——否则 immobility 算成负数。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        rec = _mk_record(trial_id="FST-抑郁8-10-ch3", mobile=400.0,
                         holds=[[0.0, 400.0]])
        rec["assay"] = "FST"
        rec["window_s"] = 467.56
        p = _write_doc(tmp, [rec], "timer_audit_FST_测试员_a.json")
        _, rows = load_audit_json(p)
        r = rows[0]
        assert r.mobile_union_s == 400.0
        assert r.immobility_s == round(467.56 - 400.0, 2)      # 67.56，不是 −40
        assert "hold_beyond_window" not in r.warnings          # 400 < 467.56，没越窗
        assert "fst_window_s_missing:fallback_360s" not in r.warnings


def test_fst_without_window_s_is_flagged_not_silently_360() -> None:
    """FST 记录缺 window_s ⇒ 分母不明，必须打标；不许静默按 360 s 算。"""
    with tempfile.TemporaryDirectory() as td:
        rec = _mk_record(trial_id="FST-抑郁8-10-ch3", mobile=300.0,
                         holds=[[0.0, 300.0]])
        p = _write_doc(Path(td), [rec], "timer_audit_FST_测试员_b.json")
        _, rows = load_audit_json(p)
        assert "fst_window_s_missing:fallback_360s" in rows[0].warnings
        assert rows[0].immobility_s == TST_WINDOW_S - 300.0    # 兜底如实，不隐瞒


def test_beyond_window_still_alarms_against_own_window_s() -> None:
    """越窗判据跟着本场 window_s 走：超过自己的录像实长照样报警、照样不 clamp。"""
    with tempfile.TemporaryDirectory() as td:
        rec = _mk_record(trial_id="FST-抑郁8-10-ch3", mobile=380.0,
                         holds=[[0.0, 380.0]])
        rec["assay"] = "FST"
        rec["window_s"] = 362.20
        p = _write_doc(Path(td), [rec], "timer_audit_FST_测试员_c.json")
        _, rows = load_audit_json(p)
        assert "hold_beyond_window" in rows[0].warnings
        assert rows[0].mobile_union_s == 380.0                 # 如实，不截到 362.20
        assert rows[0].immobility_s == round(362.20 - 380.0, 2)  # 负数可见


def test_tst_legacy_files_unchanged_by_window_bound_change() -> None:
    """旧 TST 件没有 window_s，行为必须与改动前逐字一致（历史真值不许被搅动）。"""
    with tempfile.TemporaryDirectory() as td:
        p = _write_doc(Path(td), [_mk_record(mobile=50.0, holds=[[0.0, 100.0]])])
        _, rows = load_audit_json(p)
        assert rows[0].immobility_s == TST_WINDOW_S - 100.0
        assert not [w for w in rows[0].warnings if "window_s_missing" in w]


def test_fst_passthrough_columns_are_transcribed_verbatim() -> None:
    """DP-080：工具落的四个字段必须**原样进表**，一个都不许在入库时丢。

    `wall_support_still` 是「靠着杯壁不动」的**唯一人工标签来源**——FST 边界口径
    能不能定，全靠这一列，丢了就没有第二处可查。
    """
    with tempfile.TemporaryDirectory() as td:
        rec = _mk_record(trial_id="FST-抑郁8-10-ch3", mobile=300.0, holds=[[0.0, 300.0]])
        rec.update(assay="FST", window_s=467.56, wall_support_still=True,
                   declared_empty=False)
        p = _write_doc(Path(td), [rec], "timer_audit_FST_测试员_p.json",
                       assay="FST")
        _, rows = load_audit_json(p)
        r = rows[0]
        assert (r.assay, r.assay_source) == ("FST", "record")
        assert (r.window_s, r.window_source) == (467.56, "record")
        assert r.wall_support_still is True
        assert r.declared_empty is False
        assert r.tool_version == "test"
        text = table_csv_text(rows)
        assert "wall_support_still" in text.splitlines()[0]
        assert "window_s" in text.splitlines()[0]


def test_legacy_tst_assay_is_inferred_and_labelled_as_inferred() -> None:
    """v1.x 旧件两处都没有 assay ⇒ 判 TST，但来源必须标成 trial_id_prefix。

    推断值和声明值混在一列里而不留痕，等于把猜测冒充成数据。
    """
    with tempfile.TemporaryDirectory() as td:
        p = _write_doc(Path(td), [_mk_record(trial_id="20mg_2周-ch1")])
        _, rows = load_audit_json(p)
        r = rows[0]
        assert (r.assay, r.assay_source) == ("TST", "trial_id_prefix")
        assert r.window_s is None and r.window_source == "tst_default"
        assert r.wall_support_still is None   # 悬尾不问这一问，不许填 False 冒充答过
        assert r.declared_empty is None


def test_document_level_assay_used_when_record_lacks_it() -> None:
    """只有文件头有 assay（一份导出只可能一个范式）⇒ 用它，来源标 document。"""
    with tempfile.TemporaryDirectory() as td:
        p = _write_doc(Path(td), [_mk_record(trial_id="FST-正常1-4-ch1")],
                       "timer_audit_FST_测试员_d.json", assay="FST")
        _, rows = load_audit_json(p)
        assert (rows[0].assay, rows[0].assay_source) == ("FST", "document")


def test_dp080_is_additive_only_no_existing_column_renamed() -> None:
    """加宽只许**加列**。既有 23 列的名字与相对次序都不许动——重算表是所有已发表
    一致性数字的出处，改名等于让历史引用失效。"""
    old = ["scorer_id", "trial_id", "source", "video", "chamber", "seed",
           "presentation_order", "playback_rate", "tail_climbing", "unscoreable",
           "status", "n_hold_segments", "holds_unsorted", "zero_length_segments",
           "mobile_union_s", "immobility_s", "mobile_seconds_DISCARDED",
           "naive_inflation_s", "per_key_excess_wallclock_s",
           "reject_reason", "note", "scored_at", "warnings"]
    assert set(old) <= set(CSV_COLUMNS), "既有列被改名或删除"
    kept = [c for c in CSV_COLUMNS if c in old]
    assert kept == old, "既有列的相对次序变了"
    assert len(CSV_COLUMNS) == len(old) + 7


def test_unknown_format_rejected() -> None:
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "timer_audit_w.json"
        p.write_text(json.dumps({"format": "something.else", "records": []}),
                     encoding="utf-8")
        try:
            load_audit_json(p)
            assert False
        except ValueError as e:
            assert "depressionplex.stopwatch-audit.v1" in str(e)


# ---------------------------------------------------------------- 真值数据回归


def test_real_data_matches_audit_baseline() -> None:
    # 2026-09-04（DP-044）：并入张咸明重评的 14 条 ⇒ 43+14=57 试次。
    # 两条 `20mg_3周-ch4` 都被拒收（两人独立都给空，DP-012：空 holds 与"没评"
    # 不可区分，一律拒收不许变 0）——这正是**真空隔间**在入库口径上的表现。
    res = build_table(RAW)
    assert res.n_trials == 57
    assert res.n_rejected == 2 and res.n_accepted == 55
    rejected = sorted((r.scorer_id, r.trial_id) for r in res.rows
                      if r.status == STATUS_REJECTED)
    assert rejected == [("张咸明", "20mg_3周-ch4"), ("徐乐彤", "20mg_3周-ch4")]
    for r in res.rows:
        if r.status == STATUS_REJECTED:
            assert r.mobile_union_s is None  # 不许变成 0 入库 ⇒ 360

    assert res.unsorted_total == 15, "审计 §5.1：王3 徐5 陈3 张1 + 张咸明3"
    by = res.by_scorer()
    assert {s: by[s]["unsorted"] for s in by} == {
        "王娟": 3, "陈璇": 3, "徐乐彤": 5, "张": 1, "张咸明": 3}
    assert {s: by[s]["zero_length"] for s in by} == {
        "王娟": 0, "陈璇": 0, "徐乐彤": 1, "张": 3, "张咸明": 3}
    assert 54.6 <= res.max_naive_inflation_s <= 54.9, "审计：最多虚高 +54.8 s"

    # DP-004 证据：holds 无一越 360 硬收口
    assert res.window_violations == []
    assert res.order_mismatches == []
    assert res.crosscheck_mismatches == []

    # 审计 §3：mobile 均值（并集口径）—— 王娟 195.7 / 陈璇 167.1 / 徐乐彤 115.0 / 张 117.8
    means = {
        "王娟": by["王娟"]["union_sum_s"] / 13,
        "陈璇": by["陈璇"]["union_sum_s"] / 13,
        "徐乐彤": by["徐乐彤"]["union_sum_s"] / 13,   # 拒绝的不计入合计，13 有效
        "张": by["张"]["union_sum_s"] / 3,
        # DP-044：张咸明 13 个有效（同一批、同 seed、全程 0.5x）
        "张咸明": by["张咸明"]["union_sum_s"] / 13,
    }
    for s, expect in {"王娟": 195.7, "陈璇": 167.1, "徐乐彤": 115.0,
                      "张": 117.8, "张咸明": 132.7}.items():
        assert abs(means[s] - expect) < 0.15, f"{s}: {means[s]:.2f} vs 审计 {expect}"

    # 审计 §5：每按键墙钟超额（逐试次口径，合并估计 0.121 s；逐试次更抖）
    assert res.per_key_estimates, "rule 1 的证据链必须可复算"
    m = sum(res.per_key_estimates) / len(res.per_key_estimates)
    assert 0.10 <= m <= 0.14, f"逐试次均值 {m:.4f} 偏离审计 0.121 s 过远"

    # 口径恒等式：每个 accepted 行 immobility = 360 − union
    for r in res.rows:
        if r.status == STATUS_ACCEPTED:
            assert abs(r.immobility_s - (TST_WINDOW_S - r.mobile_union_s)) < 1e-9

    # seed 分组（PROVENANCE 铁律 3 的数据前提）：同 seed 组共享播放顺序
    assert res.seed_groups == {973678866: ["王娟", "陈璇"],
                               210593506: ["张", "张咸明", "徐乐彤"]}
    w = {r.trial_id: r.presentation_order for r in res.rows if r.scorer_id == "王娟"}
    c = {r.trial_id: r.presentation_order for r in res.rows if r.scorer_id == "陈璇"}
    assert set(w) == set(c) and all(w[t] == c[t] for t in w), \
        "A 组共享 seed ⇒ presentation_order 必须逐条相同（审计 §4 顺序效应不抵消的依据）"


def test_committed_table_reproducible() -> None:
    """入库的重算表 = 校验器现算输出。真值可复现是 GLP 底线，也是 ignore 失效
    （文件在磁盘、commit 里没有/对不上）的探测器。"""
    assert COMMITTED_TABLE.exists(), "重算表未入库——检查 .gitignore（WORKFLOW §4）"
    fresh = table_csv_text(build_table(RAW).rows)
    assert fresh == COMMITTED_TABLE.read_text(encoding="utf-8")


def test_salvaged_loader_passthrough() -> None:
    rows = load_salvaged_csv(
        RAW / "human_scores_张_2026-09-03_SALVAGED_3of14.csv")
    assert len(rows) == 3
    assert all(r.scorer_id == "张" for r in rows), "评分员必须从文件名解析，不是整路径"
    assert all(r.seed == 210593506 for r in rows), "seed 从配套 TRUNCATED txt 头部回填"
    assert rows[0].holds_unsorted and rows[0].zero_length_segments == 3
    assert rows[0].mobile_union_s == 112.36 and rows[0].immobility_s == 247.64
    assert rows[0].naive_inflation_s is None  # 抢救件无 holds 明细，如实留空不猜
    assert all("union_precomputed_by_salvage_tool" in r.warnings for r in rows)
# ---------------------------------------------------------------- DP-076：配套 CSV 交叉核对


def _write_summary_csv(tmp: Path, name: str, recs) -> Path:
    """写一份 `human_scores_*.csv`。recs 里每项是 (trial_id, mobile_seconds, 呈现序)。"""
    lines = ["scorer_id,trial_id,mobile_seconds,tail_climbing,unscoreable,"
             "note,scored_at,presentation_order"]
    for tid, mob, order in recs:
        lines.append("测试员,%s,%s,false,false,,2026-09-07,%d" % (tid, mob, order))
    p = tmp / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _two_trial_rows(tmp: Path):
    a = _mk_record(trial_id="v-ch1", mobile=10.0)
    b = _mk_record(trial_id="v-ch2", mobile=12.0)
    b["presentation_order"] = 2
    _, rows = load_audit_json(_write_doc(tmp, [a, b]))
    return rows


def test_crosscheck_json_only_trial_is_reported_not_crashed() -> None:
    """DP-076 回归：JSON 有、CSV 无 这条分支原本 NameError——**报错的路径自己炸了**。

    干净数据永远走不到它，所以这个缺陷能一直躺着。测试必须直接踩这条分支。
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        rows = _two_trial_rows(tmp)
        p = _write_summary_csv(tmp, "human_scores_测试员_2026-09-07_partial1of2.csv",
                               [("v-ch1", "10.0", 1)])
        out = crosscheck_summary_csv(rows, p)
        assert out == ["human_scores_测试员_2026-09-07_partial1of2.csv: "
                       "JSON 有 CSV 无 → 测试员/v-ch2"], out


def test_crosscheck_judges_missing_on_the_union_of_partial_exports() -> None:
    """分次导出的两份 CSV 各覆盖一半 ⇒ 合起来不缺，**不许逐份判**。

    逐份判的话每份都会把另一份的试次报成缺失——纯误报，而误报会让人学会忽略这张表。
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        rows = _two_trial_rows(tmp)
        p1 = _write_summary_csv(tmp, "human_scores_测试员_2026-09-06_partial1of2.csv",
                                [("v-ch1", "10.0", 1)])
        p2 = _write_summary_csv(tmp, "human_scores_测试员_2026-09-07_partial1of2.csv",
                                [("v-ch2", "12.0", 2)])
        assert crosscheck_summary_csv(rows, [p1, p2]) == []
        # 顺序不影响结论
        assert crosscheck_summary_csv(rows, [p2, p1]) == []


def test_crosscheck_value_mismatch_stays_per_file() -> None:
    """并集只用于判缺失；逐条数值核对仍是逐文件的，报错必须点出**哪一份**。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        rows = _two_trial_rows(tmp)
        p1 = _write_summary_csv(tmp, "human_scores_测试员_2026-09-06_partial1of2.csv",
                                [("v-ch1", "10.0", 1)])
        p2 = _write_summary_csv(tmp, "human_scores_测试员_2026-09-07_partial1of2.csv",
                                [("v-ch2", "99.9", 2)])
        out = crosscheck_summary_csv(rows, [p1, p2])
        assert len(out) == 1, out
        assert "2026-09-07" in out[0] and "mobile_seconds" in out[0]
        assert "2026-09-06" not in out[0]


def test_crosscheck_refuses_empty_path_list() -> None:
    """一份 CSV 都没给却返回空清单 = 静默假装「全部一致」⇒ 必须炸。"""
    with tempfile.TemporaryDirectory() as td:
        rows = _two_trial_rows(Path(td))
        try:
            crosscheck_summary_csv(rows, [])
            assert False, "空路径列表必须拒绝"
        except ValueError as e:
            assert "假装" in str(e)


def test_build_table_handles_multiple_partial_exports_per_scorer() -> None:
    """端到端：一个评分员两份分次导出（现在的常态）必须核对干净。

    这正是 2026-09-07 那批新标注的形状——两份都叫 `partial4of27`、各覆盖不同的 4 个
    试次。改之前 `build_table` 在这里直接 NameError，新数据连校验都进不去。
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        a = _mk_record(trial_id="v-ch1", mobile=10.0)
        b = _mk_record(trial_id="v-ch2", mobile=12.0)
        b["presentation_order"] = 2
        _write_doc(tmp, [a, b])
        _write_summary_csv(tmp, "human_scores_测试员_2026-09-06_partial1of2.csv",
                           [("v-ch1", "10.0", 1)])
        _write_summary_csv(tmp, "human_scores_测试员_2026-09-07_partial1of2.csv",
                           [("v-ch2", "12.0", 2)])
        res = build_table(tmp)
        assert res.n_trials == 2
        assert res.crosscheck_mismatches == [], res.crosscheck_mismatches
