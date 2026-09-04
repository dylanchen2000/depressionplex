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


def test_both_empty_trial_excluded_from_all_means():
    """DP-045：双方皆空的试次剔出所有均值的分母。

    实来源 `20mg_3周-ch4`：真的空隔间，徐乐彤与张咸明独立都给 0 段。
    留在分母里的两个后果都发生过：Jaccard 记 nan ⇒ **平均 Jaccard 整个变 nan，
    G11 基线读不出来**；偏差被一个"0 差"稀释（14 分母下 −16.45 s，13 分母下
    −17.72 s）。剔除必须显式报出，不许悄悄改分母。
    """
    import contextlib
    import io
    import json
    import tempfile
    from pathlib import Path

    assert S._both_empty([], []) is True
    assert S._both_empty([(0.0, 1.0)], []) is False
    assert S._both_empty([], [(0.0, 1.0)]) is False

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # T1/T2 有段，T3 双方皆空
        a = [{"trial_id": "T1", "holds": [[0, 10]]},
             {"trial_id": "T2", "holds": [[0, 20]]},
             {"trial_id": "T3", "holds": []}]
        b = [{"trial_id": "T1", "holds": [[0, 20]]},
             {"trial_id": "T2", "holds": [[0, 40]]},
             {"trial_id": "T3", "holds": []}]
        (tmp / "a.json").write_text(json.dumps({"records": a}))
        (tmp / "b.json").write_text(json.dumps({"records": b}))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = S.main(["prog", str(tmp / "a.json"), str(tmp / "b.json")])
    out = buf.getvalue()
    assert rc == 0
    # 偏差按 n=2：(−10 + −20)/2 = −15.0；把 T3 算进去会被稀释成 −10.0
    assert "偏差(A−B) -15.00 s" in out, out
    # Jaccard 按 n=2：0.5 与 0.5 ⇒ 0.500；把 T3 算进去会是 nan
    assert "**平均 Jaccard 0.500**（n=2）" in out, out
    # 剔除必须显式，且分母写出来
    assert "剔除 1 个" in out and "'T3'" in out and "分母 = 2/3" in out, out


def test_all_empty_returns_error_not_fake_perfect_score():
    """全是双方皆空 ⇒ 拒绝输出均值，退出码非 0。绝不能报"Jaccard 1.000 完美"。"""
    import json
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for name in ("a.json", "b.json"):
            (tmp / name).write_text(json.dumps(
                {"records": [{"trial_id": "T1", "holds": []}]}))
        rc = S.main(["prog", str(tmp / "a.json"), str(tmp / "b.json")])
    assert rc == 1
