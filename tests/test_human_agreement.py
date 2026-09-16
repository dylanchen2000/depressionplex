"""DP-012 校验器测试：四条硬规则 + 真值数据回归。

真值基线取自 `docs/审计_人工评分_四人配对_2026-09-03.md`（已发表数字，改任一
断言前必须回到审计核对——这些数字是 DP-014 报告引用的出处）：
43 试次 / 12 乱序 / 零长段 王0 陈0 徐1 张3 / 朴素虚高最大 +54.8 s /
每按键墙钟 0.121 s（逐试次口径更抖）/ mobile 均值 195.7/167.1/115.0/117.8。
"""

from __future__ import annotations

import collections
import dataclasses
import importlib
import json
import random
import shutil
import tempfile
from pathlib import Path

from depressionplex.human_agreement import (
    DECL_COLUMNS,
    KEY_FIELDS,
    NON_CSV_FIELDS,
    READING_FIELDS,
    REJECT_UNSCORED_TRIPLE,
    RESCORE_DECL_NAME,
    SCORER_ALIASES,
    SESSION_TRACE_FIELDS,
    STATUS_ACCEPTED,
    STATUS_REJECTED,
    TST_WINDOW_S,
    ConflictingReadings,
    TrialRejected,
    TrialRow,
    CSV_COLUMNS,
    build_table,
    crosscheck_summary_csv,
    load_audit_json,
    load_rescore_declarations,
    load_salvaged_csv,
    table_csv_text,
    union_holds,
)

REPO = Path(__file__).resolve().parents[1]
RAW = REPO / "data" / "human_scores" / "raw"
INCOMING = REPO / "data" / "human_scores" / "incoming"
COMMITTED_TABLE = REPO / "data" / "human_scores" / "recomputed" / "human_scores_recomputed_DP-012.csv"
COMMITTED_TABLE_DP129 = (
    REPO / "data" / "human_scores" / "recomputed"
    / "human_scores_recomputed_DP-129_54trials.csv"
)


def _load_salvage_ref():
    """按包导入抢救工具，让相对 import 的 __package__ 成立。

    不许用 spec_from_file_location 按路径加载：那样 __package__ 为空，
    模块里 `from . import _stdio` 必然 ImportError，而桌面端 / 冻结 exe
    都走 `python -m depressionplex.cli`，从不按文件路径跑这个模块。
    """
    return importlib.import_module("depressionplex.cli.salvage_truncated_audit")


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
    # DP-129：抢救件「张」并入「张咸明」后，raw/ 真值 54 条（57−3）+ 3 条已声明复评。
    # 两条 `20mg_3周-ch4` 都被拒收（两人独立都给空，DP-012：空 holds 与"没评"
    # 不可区分，一律拒收不许变 0）——这正是**真空隔间**在入库口径上的表现。
    res = build_table(RAW)
    assert res.n_trials == 54
    assert res.n_repeats == 3
    assert res.n_rejected == 2 and res.n_accepted == 52
    rejected = sorted((r.scorer_id, r.trial_id) for r in res.rows
                      if r.status == STATUS_REJECTED)
    assert rejected == [("张咸明", "20mg_3周-ch4"), ("徐乐彤", "20mg_3周-ch4")]
    for r in res.rows:
        if r.status == STATUS_REJECTED:
            assert r.mobile_union_s is None  # 不许变成 0 入库 ⇒ 360

    # 幽灵「张」的 1 条乱序随复评出表；真值表剩 王3 徐5 陈3 张咸明3 = 14
    assert res.unsorted_total == 14, "DP-129：王3 徐5 陈3 张咸明3（张的 1 条已出真值表）"
    by = res.by_scorer()
    assert set(by) == {"王娟", "陈璇", "徐乐彤", "张咸明"}
    assert {s: by[s]["unsorted"] for s in by} == {
        "王娟": 3, "陈璇": 3, "徐乐彤": 5, "张咸明": 3}
    assert {s: by[s]["zero_length"] for s in by} == {
        "王娟": 0, "陈璇": 0, "徐乐彤": 1, "张咸明": 3}
    assert 54.6 <= res.max_naive_inflation_s <= 54.9, "审计：最多虚高 +54.8 s"

    # DP-004 证据：holds 无一越 360 硬收口
    assert res.window_violations == []
    assert res.order_mismatches == []
    assert res.crosscheck_mismatches == []

    # 审计 §3：mobile 均值（并集口径）—— 王娟 195.7 / 陈璇 167.1 / 徐乐彤 115.0
    # 「张」117.8 是身份未合并时的幽灵均值，DP-129 后不再出现在 by_scorer
    means = {
        "王娟": by["王娟"]["union_sum_s"] / 13,
        "陈璇": by["陈璇"]["union_sum_s"] / 13,
        "徐乐彤": by["徐乐彤"]["union_sum_s"] / 13,   # 拒绝的不计入合计，13 有效
        # DP-044：张咸明 13 个有效（同一批、同 seed、全程 0.5x）；3 条抢救复评不入均值
        "张咸明": by["张咸明"]["union_sum_s"] / 13,
    }
    for s, expect in {"王娟": 195.7, "陈璇": 167.1, "徐乐彤": 115.0,
                      "张咸明": 132.7}.items():
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
    # DP-081：元素带「/范式」后缀（raw 目录现全是悬尾 ⇒ /TST），分组关系不变
    # DP-129：张并入张咸明，seed 210593506 侧只剩张咸明/徐乐彤
    assert res.seed_groups == {973678866: ["王娟/TST", "陈璇/TST"],
                               210593506: ["张咸明/TST", "徐乐彤/TST"]}
    w = {r.trial_id: r.presentation_order for r in res.rows if r.scorer_id == "王娟"}
    c = {r.trial_id: r.presentation_order for r in res.rows if r.scorer_id == "陈璇"}
    assert set(w) == set(c) and all(w[t] == c[t] for t in w), \
        "A 组共享 seed ⇒ presentation_order 必须逐条相同（审计 §4 顺序效应不抵消的依据）"


def test_committed_table_reproducible() -> None:
    """入库的重算表 = 校验器现算输出。真值可复现是 GLP 底线，也是 ignore 失效
    （文件在磁盘、commit 里没有/对不上）的探测器。

    57 条那一版（human_scores_recomputed_DP-012.csv）是身份未合并时的历史产物，
    文件保留在仓里一个字节不许改；DP-129 之后 raw/ 的真值是 54 条，比这一份。
    """
    assert COMMITTED_TABLE_DP129.exists(), (
        "DP-129 54 条重算表未入库——检查 .gitignore（WORKFLOW §4）")
    fresh = table_csv_text(build_table(RAW).rows)
    assert fresh == COMMITTED_TABLE_DP129.read_text(encoding="utf-8")


def test_dp012_published_table_bytes_frozen() -> None:
    """已发表 57 条表原地冻结：不许被 DP-129 覆盖或改写。"""
    assert COMMITTED_TABLE.exists()
    text = COMMITTED_TABLE.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert len(lines) - 1 == 57, f"DP-012 表应为 57 行，实际 {len(lines) - 1}"
    assert "张,30mg_2周_2+20_2周2-ch1," in text
    assert "张,30mg_2周_2+20_2周2-ch2," in text
    assert "张,30mg_2周_1-3+20_1周1-ch3," in text
    import hashlib
    assert hashlib.sha256(COMMITTED_TABLE.read_bytes()).hexdigest() == (
        "08d6567f7d383acb8417970317891913e71840c1b37aff3919a9331b303a72c2"
    )


def test_salvaged_loader_passthrough() -> None:
    rows = load_salvaged_csv(
        RAW / "human_scores_张_2026-09-03_SALVAGED_3of14.csv")
    assert len(rows) == 3
    # DP-129：文件名仍是「张」，别名表映射为张咸明（断言等于，不许只断言非空）
    assert all(r.scorer_id == "张咸明" for r in rows), (
        "评分员从文件名解析后再过别名表，必须是张咸明")
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


# ---------------------------------------------------------------- DP-127：逐份导出对账


def test_crosscheck_pairs_csv_to_one_export_not_scorer_dict() -> None:
    """两份导出、两条不同读数、CSV 只对应其中一份 ⇒ 不报不一致。

    这是 incoming/ 上那 38 条假警报的最小复现：全评分员字典让后者覆盖前者，
    CSV 对到了错误那一份的读数。
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        a = _mk_record(trial_id="v-ch1", mobile=10.0)
        a["presentation_order"] = 1
        b = _mk_record(trial_id="v-ch1", mobile=99.0)
        b["presentation_order"] = 27  # 不同会话的 order，配对必须把它们分开
        _, rows_a = load_audit_json(
            _write_doc(tmp, [a], name="timer_audit_测试员_2026-09-07.json"))
        _, rows_b = load_audit_json(
            _write_doc(tmp, [b], name="timer_audit_测试员_2026-09-08.json"))
        csv_p = _write_summary_csv(
            tmp, "human_scores_测试员_2026-09-07_partial1of1.csv",
            [("v-ch1", "10.0", 1)])
        out = crosscheck_summary_csv(rows_a + rows_b, [csv_p])
        assert out == [], out


def test_crosscheck_none_none_is_consistent_one_side_names_the_gap() -> None:
    """两边 mobile 都空 ⇒ 一致；只有一边空 ⇒ 报，且报文说得出缺哪边。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        both = _mk_record(trial_id="v-ch1", mobile=None, holds=[])
        both["mobile_seconds"] = None
        both["unscoreable"] = True  # 避免 rule 3 三联；mobile 仍是 None
        _, rows = load_audit_json(_write_doc(tmp, [both]))
        assert rows[0].mobile_seconds_DISCARDED is None

        def _csv(name: str, mob: str) -> Path:
            # unscoreable 必须与 JSON 一致，否则干扰本条要测的 mobile 口径
            text = ("scorer_id,trial_id,mobile_seconds,tail_climbing,unscoreable,"
                    "note,scored_at,presentation_order\n"
                    f"测试员,v-ch1,{mob},false,true,,2026-09-07,1\n")
            p = tmp / name
            p.write_text(text, encoding="utf-8")
            return p

        assert crosscheck_summary_csv(rows, [_csv("ok.csv", "")]) == [], \
            "None/None 不许报不一致"

        out_csv = crosscheck_summary_csv(rows, [_csv("csv_has.csv", "10.0")])
        assert len(out_csv) == 1, out_csv
        assert "JSON 缺" in out_csv[0] and "CSV=" in out_csv[0], out_csv[0]

        only_json_rec = _mk_record(trial_id="v-ch2", mobile=12.0)
        only_json_rec["presentation_order"] = 2
        _, rows2 = load_audit_json(
            _write_doc(tmp, [only_json_rec], name="timer_audit_测试员_y.json"))
        text2 = ("scorer_id,trial_id,mobile_seconds,tail_climbing,unscoreable,"
                 "note,scored_at,presentation_order\n"
                 "测试员,v-ch2,,false,false,,2026-09-07,2\n")
        empty_csv = tmp / "json_has.csv"
        empty_csv.write_text(text2, encoding="utf-8")
        out_json = crosscheck_summary_csv(rows2, [empty_csv])
        assert len(out_json) == 1, out_json
        assert "CSV 缺" in out_json[0] and "JSON=" in out_json[0], out_json[0]


def test_crosscheck_order_is_pairing_key_not_a_mismatch() -> None:
    """同一 trial_id 在两份导出里 order 不同 ⇒ 配对分开，不产生 order 不一致。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        a = _mk_record(trial_id="v-ch1", mobile=10.0)
        a["presentation_order"] = 27
        b = _mk_record(trial_id="v-ch1", mobile=10.0)
        b["presentation_order"] = 4
        _, rows_a = load_audit_json(
            _write_doc(tmp, [a], name="timer_audit_测试员_2026-09-07.json"))
        _, rows_b = load_audit_json(
            _write_doc(tmp, [b], name="timer_audit_测试员_2026-09-08.json"))
        csv_p = _write_summary_csv(
            tmp, "human_scores_测试员_2026-09-08_partial1of1.csv",
            [("v-ch1", "10.0", 4)])
        out = crosscheck_summary_csv(rows_a + rows_b, [csv_p])
        assert out == [], out
        assert all("presentation_order" not in m for m in out)


def test_crosscheck_reports_unpaired_csv_with_closest_diffs() -> None:
    """一份 CSV 配不上任何导出 ⇒ 报「配不上」+ 最接近那份的差异。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        a = _mk_record(trial_id="v-ch1", mobile=10.0)
        a["presentation_order"] = 1
        _, rows = load_audit_json(
            _write_doc(tmp, [a], name="timer_audit_测试员_2026-09-07.json"))
        csv_p = _write_summary_csv(
            tmp, "human_scores_测试员_2026-09-07_partial1of1.csv",
            [("v-ch9", "10.0", 1)])  # 完全不同的 trial
        out = crosscheck_summary_csv(rows, [csv_p])
        assert any("配不上任何一份导出" in m for m in out), out
        assert any("最接近 timer_audit_测试员_2026-09-07.json" in m for m in out), out
        assert any("CSV 有 JSON 无 → v-ch9" in m for m in out), out


def test_crosscheck_refuses_to_pick_when_pairing_is_ambiguous() -> None:
    """一份 CSV 同时配上两份内容一致的导出 ⇒ 报「配对不唯一」，不许挑一份。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        rec = _mk_record(trial_id="v-ch1", mobile=10.0)
        rec["presentation_order"] = 1
        _, rows_a = load_audit_json(
            _write_doc(tmp, [rec], name="timer_audit_测试员_2026-09-07_a.json"))
        _, rows_b = load_audit_json(
            _write_doc(tmp, [rec], name="timer_audit_测试员_2026-09-07_b.json"))
        csv_p = _write_summary_csv(
            tmp, "human_scores_测试员_2026-09-07_partial1of1.csv",
            [("v-ch1", "10.0", 1)])
        out = crosscheck_summary_csv(rows_a + rows_b, [csv_p])
        assert len(out) == 1, out
        assert "配对不唯一" in out[0], out[0]
        assert "timer_audit_测试员_2026-09-07_a.json" in out[0]
        assert "timer_audit_测试员_2026-09-07_b.json" in out[0]


#: DP-127 修完后 incoming/ 交叉核对的钉死清单。
#: 架构师预期是 5 条（徐乐彤 09-10 CSV 未交）；本机跑出 7 条——多出来的
#: 「陈璇 09-08 CSV 配不上」是同源副本归并后第二份导出的行从内存里消失造成的，
#: **不许为凑成 5 而改守卫**，清单原样钉住，口径由架构师定。
INCOMING_CROSSCHECK_AFTER_DP127 = [
    "human_scores_FST_徐乐彤_2026-09-07_partial12of28.csv 等 8 份: "
    "JSON 有 CSV 无 → 徐乐彤/FST-抑郁4-7-ch1",
    "human_scores_FST_徐乐彤_2026-09-07_partial12of28.csv 等 8 份: "
    "JSON 有 CSV 无 → 徐乐彤/FST-抑郁4-7-ch2",
    "human_scores_FST_徐乐彤_2026-09-07_partial12of28.csv 等 8 份: "
    "JSON 有 CSV 无 → 徐乐彤/FST-正常1-4-ch1",
    "human_scores_FST_徐乐彤_2026-09-07_partial12of28.csv 等 8 份: "
    "JSON 有 CSV 无 → 徐乐彤/FST-正常1-4-ch3",
    "human_scores_FST_徐乐彤_2026-09-07_partial12of28.csv 等 8 份: "
    "JSON 有 CSV 无 → 徐乐彤/FST-正常5+抑郁1-3-ch1",
    "human_scores_FST_陈璇_2026-09-08_partial8of28_030748Z.csv: "
    "配不上任何一份导出（最接近 "
    "timer_audit_FST_陈璇_2026-09-08_partial8of28_030748Z.json）",
    "  CSV 有 JSON 无 → FST-抑郁8-10-ch4",
]


def test_incoming_crosscheck_mismatches_are_pinned() -> None:
    """真数据守卫：把修完后的清单钉死，不是断言「少于 48 条」。"""
    res = build_table(INCOMING)
    assert res.crosscheck_mismatches == INCOMING_CROSSCHECK_AFTER_DP127, (
        "清单变了——原样贴出来给架构师，不许改守卫凑数：\n"
        + "\n".join(res.crosscheck_mismatches))


def test_raw_crosscheck_still_empty_after_dp127() -> None:
    """raw/ 没有重复键，这一单不许动它的核对结果。"""
    res = build_table(RAW)
    assert res.crosscheck_mismatches == []


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


# ---------------------------------------------------------------- DP-081：种子台账按（评分员, 范式）


def test_same_scorer_two_assays_do_not_overwrite_each_other() -> None:
    """DP-081 回归锁：同一评分员的 TST 与 FST 是两把独立随机顺序，
    按 scorer_id 做 key 时后读到的会静默覆盖前一份——正是漏报事故的形态。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        rec_t = _mk_record(trial_id="20mg_2周-ch1")
        rec_t["assay"] = "TST"
        rec_f = _mk_record(trial_id="FST-抑郁8-10-ch1")
        rec_f["assay"] = "FST"
        _write_doc(tmp, [rec_t], "timer_audit_测试员_tst.json",
                   seed=111000111, assay="TST")
        _write_doc(tmp, [rec_f], "timer_audit_测试员_fst.json",
                   seed=222000222, assay="FST")
        res = build_table(tmp)
        assert len(res.seeds) == 2
        assert res.seeds[("测试员", "TST")] == 111000111
        assert res.seeds[("测试员", "FST")] == 222000222
        assert res.seed_rebatches == []   # 跨范式不是换批次，不许记账


def test_shared_seed_across_scorers_is_detected_per_assay() -> None:
    """两人、同范式、同 seed ⇒ 必须检出为一组，元素是「评分员/范式」（排序后）。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        rec_a = _mk_record(trial_id="FST-抑郁8-10-ch1")
        rec_a["assay"] = "FST"
        rec_b = _mk_record(trial_id="FST-抑郁8-10-ch2")
        rec_b["assay"] = "FST"
        _write_doc(tmp, [rec_a], "timer_audit_甲_fst.json",
                   scorer_id="甲", seed=987654321, assay="FST")
        _write_doc(tmp, [rec_b], "timer_audit_乙_fst.json",
                   scorer_id="乙", seed=987654321, assay="FST")
        res = build_table(tmp)
        assert res.seed_groups[987654321] == sorted(["甲/FST", "乙/FST"])
        assert len(res.seed_groups[987654321]) == 2


def test_same_scorer_same_assay_new_seed_is_logged_as_rebatch() -> None:
    """同人同范式两份导出、seed 不同 ⇒ 台账保留后读到的，旧值记进 rebatches。
    重评批次换种子是正常的，只记账不报错。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        rec1 = _mk_record(trial_id="FST-抑郁8-10-ch1")
        rec1["assay"] = "FST"
        rec2 = _mk_record(trial_id="FST-抑郁8-10-ch2")
        rec2["assay"] = "FST"
        _write_doc(tmp, [rec1], "timer_audit_测试员_fst_b1.json",
                   seed=111222333, assay="FST")
        _write_doc(tmp, [rec2], "timer_audit_测试员_fst_b2.json",
                   seed=444555666, assay="FST")
        res = build_table(tmp)
        assert list(res.seeds) == [("测试员", "FST")]
        assert res.seeds[("测试员", "FST")] == 444555666  # 文件名按字典序扫，b2 后读到
        assert len(res.seed_rebatches) == 1
        msg = res.seed_rebatches[0]
        assert "111222333" in msg and "444555666" in msg
        assert msg == ("测试员/FST: 111222333 → 444555666 "
                       "(timer_audit_测试员_fst_b2.json)")


# ------------------------------------------- DP-082：入库归并（DP-124 定的口径）


def _rec(trial_id="v-ch1", *, union_end=10.0, order=1, at="2026-09-07",
         rate=0.5, unscoreable=False):
    """一条读数。union 由 holds 决定（mobile_seconds 一律丢弃，规则 1）。"""
    r = _mk_record(trial_id=trial_id, mobile=union_end + 0.3,
                   holds=[] if unscoreable else [[0.0, union_end]],
                   unscoreable=unscoreable)
    r["presentation_order"] = order
    r["scored_at"] = at
    r["playback_rate"] = rate
    return r


def _two_exports(tmp: Path, rec_a, rec_b, *, seed_b=2):
    """把两条读数写成两份**不同会话**的导出（同一评分员）。"""
    a = _write_doc(tmp, [rec_a], "timer_audit_测试员_a.json")
    b = _write_doc(tmp, [rec_b], "timer_audit_测试员_b.json", seed=seed_b)
    return a, b


def _write_decl(tmp: Path, rows, *, header=DECL_COLUMNS,
                comment="# 测试用声明\n") -> Path:
    p = tmp / RESCORE_DECL_NAME
    body = [comment.rstrip("\n"), ",".join(header)]
    body += [",".join(r) for r in rows]
    p.write_text("\n".join(body) + "\n", encoding="utf-8")
    return p


def test_conflicting_readings_halt_and_name_both_files() -> None:
    """DP-124：同一 (scorer, trial) 两条读数不同 ⇒ **报错停机**。

    不许静默取后者、不许取平均。报错必须把两份导出和两个读数都摆出来，并说出
    出路——一条只说"冲突"的报错，等于把排查成本转给下一个人。
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _two_exports(tmp, _rec(union_end=10.0), _rec(union_end=12.5, at="2026-09-08"))
        try:
            build_table(tmp)
        except ConflictingReadings as e:
            msg = str(e)
        else:
            raise AssertionError("同一键两条不同读数必须停机，不许静默入库两条")
        assert "v-ch1" in msg
        assert "timer_audit_测试员_a.json" in msg and "timer_audit_测试员_b.json" in msg, msg
        assert "10.0" in msg and "12.5" in msg, f"两个读数都要摆出来：{msg}"
        assert RESCORE_DECL_NAME in msg, f"报错必须说出路在哪：{msg}"


def test_identical_duplicate_collapses_and_ignores_session_traces() -> None:
    """读数完全相同 ⇒ 取一条入库 + 记「同源副本」，**不停机**。

    两条的 presentation_order 与 warnings 都不同（27 与 delivered_order 不符会
    带 presentation_order_mismatch），照样判为同一份读数：会话痕迹不参与判身份
    （DP-124：order 是会话内序号，跨会话必撞）。
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _two_exports(tmp, _rec(order=1, at="2026-09-07"),
                     _rec(order=27, at="2026-09-08"))
        res = build_table(tmp)
        assert res.n_trials == 1, f"同源副本必须归并成 1 条，实际 {res.n_trials}"
        assert res.n_repeats == 0
        assert len(res.duplicate_notes) == 1 and "同源副本" in res.duplicate_notes[0]
        keep = res.rows[0]
        assert keep.scored_at == "2026-09-07", "同源副本取最早落盘那条"
        assert keep.source_file == "timer_audit_测试员_a.json"


def test_same_presentation_order_different_trials_stay_two_rows() -> None:
    """两份导出里 order 都是 27、但是两场不同的试次 ⇒ 老老实实两条。

    这是真数据里的形状（09-07 与 09-08 两批都有 order=27）。把 order 放进键会
    把这两场合成一场——那是丢真值，比多算更坏。
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _two_exports(tmp, _rec("v-ch1", order=27, union_end=10.0),
                     _rec("v-ch2", order=27, union_end=12.5, at="2026-09-08"))
        res = build_table(tmp)
        assert res.n_trials == 2, f"两场不同试次不许合并，实际 {res.n_trials}"
        assert res.duplicate_notes == []
        assert {r.trial_id for r in res.rows} == {"v-ch1", "v-ch2"}


def test_playback_rate_change_is_not_a_same_source_copy() -> None:
    """读数秒数一样但倍速不同 ⇒ 仍然是冲突，不许当同源副本收下。

    倍速是受控实验参数（DP-046：一处错配把 ICC 由 0.864 打到 0.344）。同一段
    视频在 0.5x 与 1.0x 下按出同样的秒数，是两次不同条件的测量凑巧相等，不是
    同一份读数。

    这里是**双保险**：倍速本身算读数字段，而 per_key_excess_wallclock_s（每按键
    墙钟超额）本身又按倍速缩放。所以只把 playback_rate 挪进会话痕迹**不会**让这
    条测试变红——做变异时发现的，写在这里，不假装它是单点守卫。
    """
    assert "playback_rate" in READING_FIELDS
    assert "playback_rate" not in SESSION_TRACE_FIELDS
    assert "per_key_excess_wallclock_s" in READING_FIELDS
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _two_exports(tmp, _rec(rate=0.5), _rec(rate=1.0, at="2026-09-08"))
        try:
            build_table(tmp)
        except ConflictingReadings as e:
            assert "倍速=0.5" in str(e) and "倍速=1.0" in str(e), str(e)
        else:
            raise AssertionError("换了倍速必须停机")


def test_declared_rescore_splits_primary_and_repeat() -> None:
    """有声明 ⇒ 首评进真值表，复评另存（不进真值表、不参与评分员间一致性）。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _two_exports(tmp, _rec(union_end=10.0), _rec(union_end=12.5, at="2026-09-08"))
        _write_decl(tmp, [("测试员", "v-ch1", "timer_audit_测试员_a.json",
                           "timer_audit_测试员_b.json", "工具跨会话重派")])
        res = build_table(tmp)
        assert res.n_trials == 1 and res.n_repeats == 1
        assert res.rows[0].mobile_union_s == 10.0, "真值必须是声明里指名的首评"
        assert res.rows[0].source_file == "timer_audit_测试员_a.json"
        assert res.repeat_rows[0].mobile_union_s == 12.5
        assert any("已声明重评" in n and "工具跨会话重派" in n
                   for n in res.duplicate_notes), res.duplicate_notes
        # 复评绝不能悄悄丢：它是免费的组内重测样本
        assert res.repeat_rows[0].trial_id == "v-ch1"


def test_declaration_must_match_the_data() -> None:
    """声明对不上数据 ⇒ 停机。一份没人核对过的声明比没有声明更坏。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _two_exports(tmp, _rec(union_end=10.0), _rec(union_end=12.5, at="2026-09-08"))
        # a) 声明了数据里不存在的键
        _write_decl(tmp, [("测试员", "根本没有这场-ch1", "timer_audit_测试员_a.json",
                           "timer_audit_测试员_b.json", "x")])
        try:
            build_table(tmp)
        except ValueError as e:
            assert "根本没有这一键" in str(e), str(e)
        else:
            raise AssertionError("声明了不存在的键必须停机")
        # b) 声明的导出名不是这一键的那两份
        _write_decl(tmp, [("测试员", "v-ch1", "timer_audit_测试员_a.json",
                           "timer_audit_测试员_不存在.json", "x")])
        try:
            build_table(tmp)
        except ValueError as e:
            assert "对不上" in str(e), str(e)
        else:
            raise AssertionError("声明的导出名对不上必须停机")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # c) 这一键只有一条读数，却给它声明了重评
        _write_doc(tmp, [_rec(union_end=10.0)], "timer_audit_测试员_a.json")
        _write_decl(tmp, [("测试员", "v-ch1", "timer_audit_测试员_a.json",
                           "timer_audit_测试员_b.json", "x")])
        try:
            build_table(tmp)
        except ValueError as e:
            assert "只有 1 条读数" in str(e), str(e)
        else:
            raise AssertionError("声明与数据不符必须停机")


def test_declaration_file_format_problems_all_halt() -> None:
    """声明文件任何格式问题都停机，不降级成警告。"""
    good = ("测试员", "v-ch1", "a.json", "b.json", "工具跨会话重派")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # 文件不存在 ⇒ 空字典（于是有冲突照样停机，这条不算"格式问题"）
        assert load_rescore_declarations(tmp / RESCORE_DECL_NAME) == {}
        p = _write_decl(tmp, [good])
        assert list(load_rescore_declarations(p)) == [("测试员", "v-ch1")]

        for rows, header, why in [
            ([good], ("scorer_id", "trial_id", "primary", "repeat", "attribution"), "表头"),
            ([("测试员", "v-ch1", "a.json", "b.json", "")], DECL_COLUMNS, "归因为空"),
            ([("测试员", "v-ch1", "a.json", "a.json", "x")], DECL_COLUMNS, "首评复评同一份"),
            ([good, good], DECL_COLUMNS, "同一键声明两次"),
        ]:
            p = _write_decl(tmp, rows, header=header)
            try:
                load_rescore_declarations(p)
            except ValueError:
                pass
            else:
                raise AssertionError(f"{why} 必须停机")
        # 只有注释、连表头都没有 ⇒ 同样停机
        (tmp / RESCORE_DECL_NAME).write_text("# 什么都没写\n", encoding="utf-8")
        try:
            load_rescore_declarations(tmp / RESCORE_DECL_NAME)
        except ValueError:
            pass
        else:
            raise AssertionError("只有注释的声明文件必须停机")


def test_three_readings_on_one_key_always_halt() -> None:
    """一个键三条读数 ⇒ 停机，**即便有声明**。没定过口径的形状不许放过。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _write_doc(tmp, [_rec(union_end=10.0, at="2026-09-07")], "timer_audit_测试员_a.json")
        _write_doc(tmp, [_rec(union_end=12.5, at="2026-09-08")], "timer_audit_测试员_b.json", seed=2)
        _write_doc(tmp, [_rec(union_end=14.0, at="2026-09-09")], "timer_audit_测试员_c.json", seed=3)
        _write_decl(tmp, [("测试员", "v-ch1", "timer_audit_测试员_a.json",
                           "timer_audit_测试员_b.json", "工具跨会话重派")])
        try:
            build_table(tmp)
        except ConflictingReadings as e:
            assert "三条以上" in str(e), str(e)
        else:
            raise AssertionError("三条读数必须停机")


def test_trialrow_fields_are_all_classified() -> None:
    """TrialRow 每个字段都必须表过态：进不进 CSV、算键/会话痕迹/读数。

    新加字段时「忘了分类」和「决定不分类」在代码里长得一模一样。没有这条守卫，
    一个新字段会默默地既不进重算表、又不参与判身份——于是两条不同的读数被当成
    同一份收下，静默丢真值。
    """
    names = {f.name for f in dataclasses.fields(TrialRow)}
    assert names == set(CSV_COLUMNS) | set(NON_CSV_FIELDS), (
        f"字段与 CSV_COLUMNS/NON_CSV_FIELDS 不闭合：{names ^ (set(CSV_COLUMNS) | set(NON_CSV_FIELDS))}")
    buckets = [set(KEY_FIELDS), set(SESSION_TRACE_FIELDS), set(READING_FIELDS)]
    assert set().union(*buckets) == names, (
        f"未分类字段：{names - set().union(*buckets)}")
    assert sum(len(b) for b in buckets) == len(names), "同一字段不许落进两类"


def test_source_file_never_reaches_the_recomputed_table() -> None:
    """来源文件名进得了内存、进不了重算表。

    重算表是已发表数字的出处（DP-012），加一列就让逐字节回归失去意义。
    """
    res = build_table(INCOMING)
    assert all(r.source_file for r in res.rows), "每条真值都要知道自己来自哪份导出"
    head = table_csv_text(res.rows).splitlines()[0]
    assert head == ",".join(CSV_COLUMNS)
    assert "source_file" not in head


def test_real_incoming_ingests_88_truth_rows_with_declarations() -> None:
    """真数据（`data/human_scores/incoming/`）：116 条读数 → 88 条真值 + 27 条复评。

    归并之前 `build_table` 把 116 条读数当 116 场试次数（28 个键各有两条），
    每个下游 FST 数字都跟着虚高。数字全部由本机跑出来，不是估的。
    """
    res = build_table(INCOMING)
    assert res.n_trials == 88, f"真值应为 88 条，实际 {res.n_trials}"
    assert res.n_repeats == 27, f"已声明复评应为 27 条，实际 {res.n_repeats}"
    assert len(res.duplicate_notes) == 28, "27 条声明重评 + 1 个同源副本"
    assert sum("同源副本" in n for n in res.duplicate_notes) == 1
    keys = collections.Counter((r.scorer_id, r.trial_id) for r in res.rows)
    assert not [k for k, n in keys.items() if n > 1], "真值表里不许再有重复键"
    per = collections.Counter((r.scorer_id, r.assay) for r in res.rows)
    assert dict(per) == {
        ("张咸明", "FST"): 16, ("张咸明", "TST"): 12,
        ("徐乐彤", "FST"): 28, ("徐乐彤", "TST"): 12,
        ("陈璇", "FST"): 20,
    }, dict(per)
    tst = [r for r in res.rows if r.assay == "TST"]
    assert len({(r.scorer_id, r.trial_id) for r in tst}) == len(tst) == 24, (
        "TST 本来就没有重复键（DP-124 记的 24 条），归并不许动它")


def test_incoming_halts_when_the_declarations_are_taken_away() -> None:
    """把声明文件拿掉，真数据必须停机——守卫在真数据上是活的，不只在夹具上。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for p in INCOMING.iterdir():
            if p.is_file() and p.name != RESCORE_DECL_NAME:
                shutil.copy2(p, tmp / p.name)
        try:
            build_table(tmp)
        except ConflictingReadings as e:
            assert "读数不同" in str(e)
        else:
            raise AssertionError("撤掉声明后必须停机")


def test_cli_writes_truth_and_repeats_to_separate_tables() -> None:
    """CLI 真跑一次 incoming/：真值表 88 行、复评表 27 行，互不混。

    走 `python -m`（桌面端与冻结 exe 唯一的调用方式），不 import main 后改 argv。
    """
    import subprocess
    import sys as _sys
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        out, rep = tmp / "truth.csv", tmp / "repeats.csv"
        r = subprocess.run(
            [_sys.executable, "-m", "depressionplex.cli.recompute_human_scores",
             "-d", str(INCOMING), "-o", str(out), "--repeats-output", str(rep)],
            cwd=REPO, capture_output=True, text=True, encoding="utf-8")
        assert out.exists() and rep.exists(), r.stdout + r.stderr
        truth = out.read_text(encoding="utf-8").splitlines()
        repeats = rep.read_text(encoding="utf-8").splitlines()
        assert len(truth) - 1 == 88, f"真值表应 88 行，实际 {len(truth) - 1}"
        assert len(repeats) - 1 == 27, f"复评表应 27 行，实际 {len(repeats) - 1}"
        assert truth[0] == repeats[0] == ",".join(CSV_COLUMNS)
        # 复评不许出现在真值表里：同一键在两张表里各一条，但读数不同
        assert set(truth[1:]).isdisjoint(set(repeats[1:]))
        assert "重复键归并（DP-082" in r.stdout


# ---------------------------------------------------------------- DP-129：评分员身份「张」→「张咸明」


#: 09-04 真值（0.5x）三键的 mobile_union_s；0.25x 抢救件是 112.36/127.37/113.52
_DP129_TRUTH_MOBILES = {
    "30mg_2周_2+20_2周2-ch1": 114.7,
    "30mg_2周_2+20_2周2-ch2": 140.52,
    "30mg_2周_1-3+20_1周1-ch3": 111.16,
}
_DP129_SALVAGE_MOBILES = {
    "30mg_2周_2+20_2周2-ch1": 112.36,
    "30mg_2周_2+20_2周2-ch2": 127.37,
    "30mg_2周_1-3+20_1周1-ch3": 113.52,
}


def test_dp129_raw_54_truth_with_09_04_mobiles() -> None:
    """raw/：54 真值 + 3 已声明复评；三键真值取 09-04，排除 0.25x 三个数。"""
    res = build_table(RAW)
    assert res.n_trials == 54, f"真值应为 54 条，实际 {res.n_trials}"
    assert res.n_repeats == 3, f"已声明复评应为 3 条，实际 {res.n_repeats}"
    truth = {
        r.trial_id: r.mobile_union_s
        for r in res.rows
        if r.scorer_id == "张咸明" and r.trial_id in _DP129_TRUTH_MOBILES
    }
    assert truth == _DP129_TRUTH_MOBILES, truth
    for tid, bad in _DP129_SALVAGE_MOBILES.items():
        assert truth[tid] != bad, f"{tid} 真值不许是 0.25x 的 {bad}"
    repeats = {
        r.trial_id: r.mobile_union_s
        for r in res.repeat_rows
        if r.trial_id in _DP129_SALVAGE_MOBILES
    }
    assert repeats == _DP129_SALVAGE_MOBILES, repeats


def test_dp129_raw_halts_without_declarations() -> None:
    """把 raw/ 的声明拿掉 ⇒ ConflictingReadings 停机（tempfile，不动 git 跟踪的 data/）。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for p in RAW.iterdir():
            if p.is_file() and p.name != RESCORE_DECL_NAME:
                shutil.copy2(p, tmp / p.name)
        try:
            build_table(tmp)
        except ConflictingReadings as e:
            assert "读数不同" in str(e)
        else:
            raise AssertionError("撤掉 raw 声明后必须停机")


def test_dp129_single_char_scorer_halts() -> None:
    """单字评分员名且不在别名表 ⇒ 停机；报错含文件名与「别名表」。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        name = "human_scores_李_2026-09-03_SALVAGED_1of1.csv"
        (tmp / name).write_text(
            "trial_id,mobile_union_s,immobility_s,n_hold_segments,"
            "holds_unsorted,zero_length_segments,playback_rate,"
            "tail_climbing,unscoreable,presentation_order,scored_at,note,"
            "mobile_seconds_DISCARDED\n"
            "v-ch1,10.0,350.0,1,False,0,0.5,False,False,1,2026-09-03,,10.0\n",
            encoding="utf-8",
        )
        try:
            load_salvaged_csv(tmp / name)
        except ValueError as e:
            msg = str(e)
            assert name in msg, msg
            assert "别名表" in msg, msg
            assert "李" in msg, msg
        else:
            raise AssertionError("单字评分员名必须停机")


def test_dp129_scorer_aliases_only_zhang() -> None:
    """别名表只许有架构师授权的一条；新加别名必须连着这条守卫一起改。"""
    assert set(SCORER_ALIASES) == {"张"}
    assert SCORER_ALIASES["张"] == "张咸明"
