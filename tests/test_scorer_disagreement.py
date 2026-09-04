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

def test_playback_rate_stratification_labels_mixed_numbers():
    """DP-046：倍速错配的配对，合并数必须当场贴"混杂、不得单独引用"，并分层给数。

    为什么这条必须锁死：实测**一处倍速错配就把 ICC 由 0.864 打到 0.344**，与"一个人
    评得粗一个人评得细"同量级。合并数被当成"人工-人工一致性"去定 G8/G11 的门，
    等于用一个混杂量当验收基准。倍速缺记录同样算分不了层，一律进"未记录"层。
    """
    import contextlib
    import io
    import json
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # T1/T2 同 0.5x；T3 错配（0.25x vs 0.5x）；T4 一侧没记倍速
        a = [{"trial_id": "T1", "holds": [[0, 10]], "playback_rate": 0.5},
             {"trial_id": "T2", "holds": [[0, 10]], "playback_rate": 0.5},
             {"trial_id": "T3", "holds": [[0, 10]], "playback_rate": 0.25},
             {"trial_id": "T4", "holds": [[0, 10]]}]
        b = [{"trial_id": "T1", "holds": [[0, 20]], "playback_rate": 0.5},
             {"trial_id": "T2", "holds": [[0, 20]], "playback_rate": 0.5},
             {"trial_id": "T3", "holds": [[0, 20]], "playback_rate": 0.5},
             {"trial_id": "T4", "holds": [[0, 20]], "playback_rate": 0.5}]
        (tmp / "a.json").write_text(json.dumps({"records": a}))
        (tmp / "b.json").write_text(json.dumps({"records": b}))
        assert S.load_rates(tmp / "a.json") == {
            "T1": 0.5, "T2": 0.5, "T3": 0.25, "T4": None}, "缺字段必须是 None 不是默认值"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = S.main(["prog", str(tmp / "a.json"), str(tmp / "b.json")])
    out = buf.getvalue()
    assert rc == 0
    assert "混杂：1 个倍速错配 + 1 个倍速未记录" in out, out
    assert "不得单独引用" in out
    assert "⚠ **批内换过倍速**" in out, "A 批内 0.5x/0.25x/未记录混用，必须点出来"
    assert "倍速匹配: n=2" in out and "倍速错配: n=1" in out and "倍速未记录: n=1" in out, out
    assert "只许用**倍速匹配**那一层" in out


def test_matched_rates_produce_no_mixed_warning():
    """对照：两人全程同倍速 ⇒ 不贴混杂标签，Jaccard 行回到"G11 门槛取此值"。"""
    import contextlib
    import io
    import json
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        recs = lambda end: [{"trial_id": f"T{i}", "holds": [[0, end]],
                             "playback_rate": 0.5} for i in (1, 2)]
        (tmp / "a.json").write_text(json.dumps({"records": recs(10)}))
        (tmp / "b.json").write_text(json.dumps({"records": recs(20)}))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = S.main(["prog", str(tmp / "a.json"), str(tmp / "b.json")])
    out = buf.getvalue()
    assert rc == 0
    assert "混杂" not in out and "批内换过倍速" not in out, out
    assert "G11 门槛取此值" in out and "倍速匹配: n=2" in out
