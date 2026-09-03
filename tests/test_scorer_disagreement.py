"""评分员分歧定位工具测试（DP-036）。

守住三件容易悄悄坏掉的事：
1. holds 乱序自嵌套必须先排序再取并集（43 个试次里 12 个乱序，朴素求和最多虚高 +30%）
2. 逐秒 Jaccard 必须与总时长脱钩——总量一致而时间轴不一致的情形必须能被检出，
   这正是新增 G11 门的理由（实测 |差|=0.9 s 而 Jaccard=0.51）
3. 边界配对必须互为最佳，一对多会把同一个长段的偏移重复计入（实测虚高到 834%）
"""

from __future__ import annotations

from depressionplex.cli import scorer_disagreement as S


def test_normalize_sorts_before_union():
    """乱序自嵌套：朴素求和 = 10+10+30 = 50，正确并集 = 30。"""
    holds = [(20.0, 30.0), (0.0, 10.0), (0.0, 30.0)]
    assert S.normalize(holds) == [(0.0, 30.0)]
    assert S.total(S.normalize(holds)) == 30.0


def test_normalize_accepts_dict_and_reversed_pairs():
    assert S.normalize([{"start": 5.0, "end": 1.0}]) == [(1.0, 5.0)]


def test_jaccard_is_independent_of_total_agreement():
    """两条轨迹总时长完全相同（各 20 s）却毫无重叠 ⇒ Jaccard 必须为 0，不是 1。"""
    a = S.normalize([(0.0, 20.0)])
    b = S.normalize([(20.0, 40.0)])
    assert S.total(a) == S.total(b) == 20.0
    inter = S.intersect_total(a, b)
    assert inter == 0.0
    assert inter / (S.total(a) + S.total(b) - inter) == 0.0


def test_subtract_reports_fragments_not_one_block():
    """单向分歧的形状要能看出来：碎片数与总量都要对。"""
    a = S.normalize([(0.0, 10.0)])
    b = S.normalize([(2.0, 3.0), (5.0, 6.0)])
    frags = S.subtract(a, b)
    assert len(frags) == 3
    assert abs(sum(e - s for s, e in frags) - 8.0) < 1e-9


def test_merge_gap_only_bridges_gaps_within_threshold():
    segs = S.normalize([(0.0, 1.0), (1.4, 2.0), (5.0, 6.0)])
    assert S.merge_gap(segs, 0.5) == [(0.0, 2.0), (5.0, 6.0)]
    assert S.merge_gap(segs, 0.1) == segs


def test_mutual_best_match_does_not_double_count_one_long_segment():
    """一个长段对三个短段：一对多会算 3 次偏移，互为最佳只保留 1 对。"""
    w = S.normalize([(0.0, 30.0)])
    c = S.normalize([(1.0, 5.0), (10.0, 14.0), (20.0, 29.0)])
    edges = S.mutual_best_edges(w, c)
    assert len(edges) == 1
    onset, offset = edges[0]
    assert abs(onset - 20.0) < 1e-9   # 与重叠最大的那段(20,29)配对
    assert abs(offset - 1.0) < 1e-9


def test_no_common_trials_returns_error_not_silent_zero():
    """禁止静默兜底：没有共同试次要返回非零退出码。"""
    import json
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for name, tid in (("a.json", "T1"), ("b.json", "T2")):
            (tmp / name).write_text(json.dumps(
                {"records": [{"trial_id": tid, "holds": [[0, 1]]}]}))
        rc = S.main(["prog", str(tmp / "a.json"), str(tmp / "b.json")])
    assert rc == 1
