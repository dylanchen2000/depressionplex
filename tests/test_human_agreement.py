"""SPEC_人工比对与验收_v1 的测试：§2.1 校验、§3 三坑、§4 排除、§5 报告、§6 判据。"""

from __future__ import annotations

import json
import math
import random
import tempfile
from pathlib import Path

import numpy as np

from depressionplex import human_agreement as ha


# ---------------------------------------------------------------- fixtures

def _mk_rows(scanner_a: dict[str, float | None], scorer: str,
             tail: set[str] = frozenset(), unscorable: set[str] = frozenset()) -> str:
    lines = [",".join(ha.HUMAN_CSV_COLUMNS)]
    for i, (tid, mob) in enumerate(scanner_a.items(), start=1):
        m = "" if (tid in unscorable or mob is None) else f"{mob:.1f}"
        lines.append(",".join([
            scorer, tid, m,
            "true" if tid in tail else "false",
            "true" if tid in unscorable else "false",
            "", "2026-08-30", str(i),
        ]))
    return "\n".join(lines) + "\n"


def _write_files(tmp: Path, csv_a: str, csv_b: str) -> list[Path]:
    pa, pb = tmp / "a.csv", tmp / "b.csv"
    pa.write_text(csv_a, encoding="utf-8")
    pb.write_text(csv_b, encoding="utf-8")
    return [pa, pb]


def _pkg(tmp: Path, trials: dict[str, dict]) -> Path:
    d = tmp / "result_dir"
    d.mkdir(exist_ok=True)
    for tid, over in trials.items():
        doc = {"trial_id": tid, "duration_sec": 360.0, "immobility_seconds": 200.0,
               "scoreable_frames": 8800, "total_frames": 9000, "unknown_fraction": 0.02,
               "validity": "valid", "tail_climbing": False, "source": "software_pipeline",
               "parameters": {"theta_mob": 0.0175}, "versions": {"code_git_sha": "abc123",
               "tool_version": "2.2.0", "model_version": "tst-rules-v1"}}
        doc.update(over)
        (d / f"{tid}.json").write_text(json.dumps(doc), encoding="utf-8")
    return d


# ---------------------------------------------------------------- §2.1 校验

def test_human_csv_happy_path():
    text = _mk_rows({"v1-ch1": 118.4, "v1-ch2": 90.0}, "X")
    rows, errs = ha.parse_human_csv(text)
    assert errs == [], errs
    assert len(rows) == 2 and rows[0].mobile_seconds == 118.4


def test_header_mismatch_rejected():
    rows, errs = ha.parse_human_csv("scorer_id,trial_id\nX,v1-ch1\n")
    assert errs and "header mismatch" in errs[0] and rows == []


def test_unscoreable_requires_empty_mobile():
    text = _mk_rows({"v1-ch1": 100.0}, "X").replace(",false,\n", ",true,\n")
    # 直接把 unscoreable 翻成 true 但 mobile 仍在 → 必须拒
    bad = 'scorer_id,trial_id,mobile_seconds,tail_climbing,unscoreable,note,scored_at,presentation_order\nX,v1-ch1,100.0,false,true,,2026-08-30,1\n'
    rows, errs = ha.parse_human_csv(bad)
    assert any("unscoreable=True but mobile_seconds" in e for e in errs), errs
    good = 'scorer_id,trial_id,mobile_seconds,tail_climbing,unscoreable,note,scored_at,presentation_order\nX,v1-ch1,,false,true,胶带脱落,2026-08-30,1\n'
    rows, errs = ha.parse_human_csv(good)
    assert errs == [] and rows[0].mobile_seconds is None and rows[0].unscoreable


def _body(text: str) -> str:
    """去掉 _mk_rows 的表头，用于拼接第二评分员。"""
    return "".join(text.splitlines(True)[1:])


def test_range_rejected_not_clipped():
    text = _mk_rows({"v1-ch1": 999.0}, "A") + _body(_mk_rows({"v1-ch1": 50.0}, "B"))
    rows, errs = ha.parse_human_csv(text)
    assert errs == [], errs
    probs = ha.human_validation_errors(rows, {"v1-ch1": 360.0})
    assert any("999" in e and "not clipped" in e for e in probs), probs


def test_two_scorers_identical_trials():
    rows, _ = ha.parse_human_csv(
        _mk_rows({"v1-ch1": 50.0, "v1-ch2": 60.0}, "A")
        + "".join(_mk_rows({"v1-ch1": 50.0, "v1-ch3": 60.0}, "B").splitlines(True)[1:]))
    probs = ha.human_validation_errors(rows, {"v1-ch1": 360.0, "v1-ch2": 360.0, "v1-ch3": 360.0})
    assert any("trial_id sets differ" in e for e in probs), probs
    rows3, _ = ha.parse_human_csv(
        _mk_rows({"v1-ch1": 50.0}, "A")
        + "Z,v1-ch1,50,false,false,,2026-08-30,1\n" + "Q,v1-ch1,50,false,false,,2026-08-30,1\n")
    probs = ha.human_validation_errors(rows3, {"v1-ch1": 360.0})
    assert any("expected exactly 2" in e for e in probs), probs


def test_duplicate_and_order_checks():
    dup = (_mk_rows({"v1-ch1": 50.0}, "A") + "A,v1-ch1,40,false,false,,2026-08-30,2\n"
           + "B,v1-ch1,45,false,false,,2026-08-30,1\nB,v1-ch1b,45,false,false,,2026-08-30,1\n")
    # 注意 v1-ch1b 不是合法 trial_id 格式，先造合法的：见下行替换
    dup = dup.replace("v1-ch1b", "v2-ch1")
    rows, errs = ha.parse_human_csv(dup)
    assert errs == [], errs
    probs = ha.human_validation_errors(rows, {"v1-ch1": 360.0, "v2-ch1": 360.0})
    assert any("duplicate" in e for e in probs), probs
    assert any("presentation_order" in e for e in probs), probs


def test_prefill_trace_rejected():
    # 评分员 B 的 mobile 与软件 mobility 在 3 个试次内逐位相同（≤0.01s）⇒ 疑似预填
    rows, _ = ha.parse_human_csv(_mk_rows(
        {"v1-ch1": 160.00, "v1-ch2": 160.00, "v1-ch3": 160.01}, "B"))
    errs = ha.prefill_trace_errors(rows, {"v1-ch1": 160.0, "v1-ch2": 160.005, "v1-ch3": 160.0})
    assert errs and "prefill" in errs[0], errs
    # 巧合只 2 个 ⇒ 放行（阈值只严不松）
    assert ha.prefill_trace_errors(rows[:2], {"v1-ch1": 160.0, "v1-ch2": 160.0}) == []
    # 正常独立评分（差 ~1s）⇒ 放行
    assert ha.prefill_trace_errors(rows, {k: v + 1.0 for k, v in
                                          {"v1-ch1": 160.0, "v1-ch2": 160.0, "v1-ch3": 160.0}.items()}) == []


def test_timer_csv_format_accepted():
    """镜像计时器输出格式的 fixture 必须过校验（PR3 契约对齐）。"""
    text = ("scorer_id,trial_id,mobile_seconds,tail_climbing,unscoreable,note,scored_at,presentation_order\r\n"
            "张三,TST-007-ch2,118.4,false,false,,2026-09-01,1\r\n"
            "张三,TST-011-ch4,,false,true,第90秒胶带脱落,2026-09-01,2\r\n")
    rows, errs = ha.parse_human_csv(text)
    assert errs == [], errs
    assert rows[1].mobile_seconds is None


# ---------------------------------------------------------------- §3 三坑

def test_sign_conversion_extreme_assertion():
    # spec §3.1 明令的极端断言
    assert ha.human_immobility_seconds(0.0, 360.0) == 360.0
    assert ha.human_immobility_seconds(360.0, 360.0) == 0.0
    try:
        ha.human_immobility_seconds(361.0, 360.0)
        assert False, "must reject, not clip"
    except ha.AgreementError as e:
        assert "not clipped" in e.errors[0]


def test_window_mismatch_hard_error():
    doc = {"trial_id": "v1-ch1", "duration_sec": 300.0, "immobility_seconds": 100.0,
           "scoreable_frames": 7400, "total_frames": 7500, "unknown_fraction": 0.01,
           "validity": "valid", "tail_climbing": False, "source": "software_pipeline",
           "parameters": {"theta_mob": 0.0175}, "versions": {}}
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "p"
        d.mkdir()
        (d / "v1-ch1.json").write_text(json.dumps(doc))
        res = ha.load_results(d)
        try:
            ha.assert_window_match(res["v1-ch1"])
            assert False, "300s window must hard-error for TST"
        except ha.WindowMismatchError:
            pass


def test_no_frame_time_fields():
    doc = {"trial_id": "v1-ch1", "duration_sec": 360.0, "immobility_frames": 5000,
           "scoreable_frames": 8800, "total_frames": 9000, "unknown_fraction": 0.02,
           "validity": "valid", "tail_climbing": False, "source": "software_pipeline",
           "parameters": {"theta_mob": 0.0175}, "versions": {}}
    errs = ha._trial_errors("v1-ch1", doc)
    assert any("missing field 'immobility_seconds'" in e for e in errs), errs


# ---------------------------------------------------------------- 统计参考值

def test_student_t_reference():
    assert abs(ha.student_t_two_sided_p(0.0, 8) - 1.0) < 1e-9
    assert abs(ha.student_t_two_sided_p(2.306004, 8) - 0.05) < 1e-3
    assert abs(ha.student_t_two_sided_p(1.959964, 100000) - 0.05) < 1e-3


def test_pearson_and_ci():
    x = [1.0, 2, 3, 4, 5, 6, 7, 8]
    assert abs(ha.pearson_r(x, x) - 1.0) < 1e-12
    assert abs(ha.pearson_r(x, [-v for v in x]) + 1.0) < 1e-12
    lo, hi = ha.pearson_ci_fisher_z(1.0, 30)
    assert lo > 0.99
    # 与 numpy 交叉验证
    rng = random.Random(7)
    xs = [rng.uniform(60, 300) for _ in range(40)]
    ys = [v + rng.gauss(0, 2) for v in xs]
    assert abs(ha.pearson_r(xs, ys) - float(np.corrcoef(xs, ys)[0, 1])) < 1e-9


def test_bland_altman_catches_constant_and_proportional():
    rng = random.Random(3)
    means = [rng.uniform(60, 300) for _ in range(30)]
    diffs_const = [20.0 + rng.gauss(0, 3) for _ in means]
    ba = ha.bland_altman(diffs_const, means)
    assert 17 < ba.bias < 23 and not ba.proportional_bias
    assert abs(ba.loa_high - (ba.bias + 1.96 * ba.sd)) < 1e-9
    diffs_prop = [0.10 * m + rng.gauss(0, 1) for m in means]
    bp = ha.bland_altman(diffs_prop, means)
    assert bp.proportional_bias and bp.slope_p < 0.05


def test_icc_2_1_sanity():
    a = [100.0, 120, 90, 200, 150, 80, 210, 130]
    assert abs(ha.icc_2_1(a, list(a)) - 1.0) < 1e-12
    rng = random.Random(11)
    b = [v + rng.gauss(0, 2) for v in a]
    icc = ha.icc_2_1(a, b)
    assert icc is not None and icc > 0.98
    assert ha.icc_2_1([5.0], [6.0]) is None


def test_confusion_empty_class():
    c = ha.confusion_from_bools([False, False], [False, True])
    assert c.sensitivity is None and c.specificity == 0.5


# ---------------------------------------------------------------- 引擎与判据

def _synthetic(tmp: Path, n=30, *, software=None, tail_double: list[int] | None = None,
               unsc: list[int] | None = None, repair: list[int] | None = None,
               detached_human: list[int] | None = None):
    """n 试次合成数据：真值均匀分布，软件默认=真值+1s。"""
    software = software or (lambda truth: truth + 1.0)
    rng = random.Random(42)
    truths = [rng.uniform(60, 300) for _ in range(n)]
    trials, mob_a, mob_b = {}, {}, {}
    tail_double = tail_double or []
    unsc = unsc or []
    repair = repair or []
    detached_human = detached_human or []
    for i, truth in enumerate(truths):
        tid = f"v{i // 4 + 1}-ch{i % 4 + 1}"
        immob = software(truth)
        trials[tid] = {"immobility_seconds": round(immob, 2),
                       "tail_climbing": i in tail_double,
                       "validity": "truncated_suspect" if i in repair
                                   else ("detached" if i in detached_human else "valid")}
        # 零噪声的"理想双标"fixture：判据测试必须与 RNG 运气无关
        # （BA 斜率显著性在 σ 噪声下偶发误显著，是真实数据会有的现象，不该进 pass 断言）
        mob = round(360.0 - truth, 1)
        mob_a[tid] = max(0.0, mob)
        mob_b[tid] = max(0.0, mob)
    a_txt = _mk_rows(mob_a, "A", tail={f"v{i//4+1}-ch{i%4+1}" for i in tail_double},
                     unscorable={f"v{i//4+1}-ch{i%4+1}" for i in unsc})
    b_txt = _mk_rows(mob_b, "B", tail={f"v{i//4+1}-ch{i%4+1}" for i in tail_double})
    pkg = _pkg(tmp, trials)
    paths = _write_files(tmp, a_txt, b_txt)
    return pkg, paths


def test_synthetic_perfect_passes_gates():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        pkg, paths = _synthetic(tmp)
        rep = ha.build_report(pkg, paths)
        assert rep["format"] == ha.REPORT_FORMAT
        assert rep["software_vs_truth"]["r"] > 0.995, rep["software_vs_truth"]
        st = {k: v["status"] for k, v in rep["gates"].items()}
        assert st["V1"] == "pass" and st["V7"] == "pass" and st["V6"] == "pass"
        assert st["V2"] == "pass"  # bias=1s < 18s
        md = ha.render_markdown(rep)
        assert "§5.1" in md and "V1" in md
        json.loads(ha.dump_report_json(rep))  # allow_nan=False 必须成功


def test_biased_but_correlated_trap_caught_by_V2():
    """spec 点名场景：软件每试次系统性偏大，r 仍高，BA 必须抓住。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        pkg, paths = _synthetic(tmp, software=lambda t: t * 1.15 + 10)
        rep = ha.build_report(pkg, paths)
        assert rep["software_vs_truth"]["r"] > 0.95, rep["software_vs_truth"]["r"]
        assert rep["gates"]["V2"]["status"] == "fail", rep["gates"]["V2"]


def test_exclusions_rules():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        pkg, paths = _synthetic(tmp, n=30, tail_double=[3, 7], unsc=[5], repair=[9],
                                detached_human=[11])
        rep = ha.build_report(pkg, paths)
        ex = rep["exclusions"]
        assert ex["n_before"] == 30
        reasons = dict(ex["dropped"])
        assert reasons.get("v1-ch4") == "human_tail_climbing_both"      # i=3
        assert reasons.get("v2-ch2") == "human_unscoreable"              # i=5（A 标无法评分）
        assert reasons.get("v2-ch4") == "human_tail_climbing_both"      # i=7
        assert reasons.get("v3-ch4") == "software_validity_exclude"      # i=11 软件判脱落
        assert ex["needs_repair"] == ["v3-ch2"]                          # i=9：保留并标出
        assert "v3-ch2" not in reasons                                   # 绝未被"排除"绕过
        assert ex["n_after"] == 26
        # V6 因 needs_repair fail（bug 不许绕过）
        assert rep["gates"]["V6"]["status"] == "fail"
        # 排除前后两组数字都在（§7）
        assert rep["software_vs_truth_pre_exclusion"]["n"] > rep["software_vs_truth"]["n"]


def test_single_tail_climbing_review_list():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        pkg, paths = _synthetic(tmp, n=10)
        # 手工把 A 的某试次 tail_climbing 改 true，B 保持 false
        pa = paths[0]
        txt = pa.read_text().replace(
            ",true,", ",false,")  # 先归零
        lines = txt.strip().split("\n")
        lines[4] = lines[4].replace(",false,false,", ",true,false,")
        pa.write_text("\n".join(lines) + "\n")
        rep = ha.build_report(pkg, paths)
        assert rep["exclusions"]["review_list"], rep["exclusions"]
        # 且未被排除
        assert rep["exclusions"]["n_after"] == 10


def test_pending_data_lock():
    """source 非 software_pipeline ⇒ 一切判据 pending_data，绝不 leak pass。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        pkg, paths = _synthetic(tmp, n=10)
        f = next(pkg.glob("*.json"))
        doc = json.loads(f.read_text())
        doc["source"] = "derived_from_annotation_rubric"
        f.write_text(json.dumps(doc))
        rep = ha.build_report(pkg, paths)
        statuses = {v["status"] for v in rep["gates"].values()}
        assert statuses == {"pending_data"}, statuses


def test_v5_zero_false_positive():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # i=4 → v2-ch1：人工 A 标无法评分（真值阳性），软件也判 detached ⇒ TP，敏感性=1.0
        pkg, paths = _synthetic(tmp, n=10, unsc=[4])
        (pkg / "v2-ch1.json").write_text(json.dumps(
            {**json.loads((pkg / "v2-ch1.json").read_text()), "validity": "detached"}))
        # 再把一个人工完全正常的试次判成 detached ⇒ FP=1：哪怕敏感性 100% 也必须 fail
        (pkg / "v1-ch2.json").write_text(json.dumps(
            {**json.loads((pkg / "v1-ch2.json").read_text()), "validity": "detached"}))
        rep = ha.build_report(pkg, paths)
        det = rep["events_2x2"]["detached"]
        assert det["tp"] == 1 and det["fp"] == 1 and det["sensitivity"] == 1.0, det
        assert rep["gates"]["V5"]["status"] == "fail", rep["gates"]["V5"]


def test_top_warning_when_raters_inconsistent():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        pkg, paths = _synthetic(tmp, n=20)
        # 把 B 全部打散成与 A 无关的数
        pb = paths[1]
        rows, _ = ha.parse_human_csv(pb.read_text())
        ids = [r.trial_id for r in rows]
        pb.write_text(_mk_rows({tid: (60.0 + 10 * i) % 300 for i, tid in enumerate(ids)}, "B"))
        rep = ha.build_report(pkg, paths)
        assert rep["top_warning"] and "TOP WARNING" in rep["top_warning"]
        assert rep["gates"]["V3"]["status"] == "fail"


def test_order_effect_detection():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        pkg, paths = _synthetic(tmp, n=20)
        # 给 B 注入随 presentation_order 线性漂移的 mobile
        pb = paths[1]
        rows, _ = ha.parse_human_csv(pb.read_text())
        new = {}
        for r in rows:
            base = 360.0 - (r.mobile_seconds or 100.0)
            new[r.trial_id] = max(0.0, (r.mobile_seconds or 0.0) + 1.5 * r.presentation_order)
        pb.write_text(_mk_rows(new, "B"))
        rep = ha.build_report(pkg, paths)
        blk = rep["order_effects"]["B"]
        assert blk.get("significant_drift") is True, blk


def test_run_manifest_records_theta():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        pkg, paths = _synthetic(tmp, n=5)
        rep = ha.build_report(pkg, paths)
        rm = rep["run_manifest"]
        assert rm["parameters_snapshot"]["theta_mob"] == 0.0175
        assert "code_git_sha" in rm and rm["inputs"]
        assert "FROZEN" in rm["parameters_note"]
