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
    build_table,
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


def _mk_doc(records):
    return {
        "format": "depressionplex.stopwatch-audit.v1", "partial": False,
        "done_count": len(records), "total_trials": len(records),
        "scorer_id": "测试员", "seed": 1, "delivered_order": [r["trial_id"] for r in records],
        "tool_version": "test", "exported_at": "", "records": records,
    }


def _write_doc(tmp: Path, records, name="timer_audit_测试员_x.json") -> Path:
    p = tmp / name
    p.write_text(json.dumps(_mk_doc(records), ensure_ascii=False), encoding="utf-8")
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
    res = build_table(RAW)
    assert res.n_trials == 43
    assert res.n_rejected == 1 and res.n_accepted == 42
    rejected = [r for r in res.rows if r.status == STATUS_REJECTED]
    assert rejected[0].scorer_id == "徐乐彤" and rejected[0].trial_id == "20mg_3周-ch4"
    assert rejected[0].mobile_union_s is None  # 不许变成 0 入库 ⇒ 360

    assert res.unsorted_total == 12, "审计 §5.1：王3 徐5 陈3 张1"
    by = res.by_scorer()
    assert {s: by[s]["unsorted"] for s in by} == {
        "王娟": 3, "陈璇": 3, "徐乐彤": 5, "张": 1}
    assert {s: by[s]["zero_length"] for s in by} == {
        "王娟": 0, "陈璇": 0, "徐乐彤": 1, "张": 3}
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
    }
    for s, expect in {"王娟": 195.7, "陈璇": 167.1, "徐乐彤": 115.0, "张": 117.8}.items():
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
    assert res.seed_groups == {973678866: ["王娟", "陈璇"], 210593506: ["张", "徐乐彤"]}
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
