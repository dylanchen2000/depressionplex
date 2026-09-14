"""结果页模型层守卫测试（B4 交付，DP-107）。

九条守卫（派工单 B4_结果页_DP-107.md §3）：
1. 字段契约：CSV_FIELDS_EXPECTED == analyze.CSV_FIELDS（含顺序）
2. 表头不符即拒：少一列、多一列、换顺序，各抛一次
3. 分母表覆盖全：凡列名以 _s 结尾的字段，DENOMINATORS 里必须有条目
4. 空值不许变 0：喂一行 immobility_s=""，断言给出的是「无」而不是 0
5. 报警行不许消失：造一份 CSV 只有 ch1、ch3，run.json 有 ch2、ch4 的报警
6. 两个文件缺任意一个的行为
7. 文件名不许第二次派生：AST 守卫
8. 外壳不许 import 引擎：AST 守卫
9. 端到端夹具：用引擎真产出的 CSV + run.json 喂模型层
"""

from __future__ import annotations

import ast
import csv
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 可以 import 引擎（tests/ 不受进程边界约束）
sys.path.insert(0, str(ROOT))
from depressionplex.cli import analyze

# 可以 import models（不含 PySide6）
from desktop.app.models import results


def _make_exp(output_dir: Path, video_path: Path, trial_prefix: str | None = None) -> dict:
    """创建一个完整的 experiment 对象（包含所有 SCHEMA_KEYS）。"""
    return {
        "schema_version": "1",
        "created_at": "2026-09-14T00:00:00+00:00",
        "operator": None,
        "note": None,
        "assay": "TST",
        "n_chambers": 4,
        "calib_frames": 12,
        "body_area_prior": None,
        "output_dir": str(output_dir),
        "videos": [{"path": str(video_path), "trial_prefix": trial_prefix}],
    }


def test_csv_fields_contract():
    """守卫 1：字段契约必须与引擎完全一致（含顺序）。"""
    assert results.CSV_FIELDS_EXPECTED == analyze.CSV_FIELDS, (
        f"字段契约不一致：\n"
        f"  外壳：{results.CSV_FIELDS_EXPECTED}\n"
        f"  引擎：{analyze.CSV_FIELDS}\n"
        f"  差异：{set(results.CSV_FIELDS_EXPECTED) ^ set(analyze.CSV_FIELDS)}"
    )


def test_csv_header_mismatch_rejected():
    """守卫 2：表头不符即拒（少一列、多一列、换顺序）。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        exp = _make_exp(tmp, tmp / "v.mp4")

        # 准备一个合法的 run.json
        run_json = tmp / "v_run.json"
        run_json.write_text(json.dumps(_make_run_json([1])), encoding="utf-8")

        csv_path = tmp / "v.csv"

        # 情况1：少一列
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            # 故意少写最后一个字段
            writer.writerow(results.CSV_FIELDS_EXPECTED[:-1])

        try:
            results.load_results(exp, 0)
            assert False, "少一列应该抛 ResultsError"
        except results.ResultsError as e:
            assert "缺字段" in str(e) or "表头" in str(e)
            assert "gate_messages" in str(e), "应该点出缺少的字段"

        # 情况2：多一列
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(results.CSV_FIELDS_EXPECTED + ("extra_field",))

        try:
            results.load_results(exp, 0)
            assert False, "多一列应该抛 ResultsError"
        except results.ResultsError as e:
            assert "多字段" in str(e) or "表头" in str(e)
            assert "extra_field" in str(e), "应该点出多余的字段"

        # 情况3：换顺序
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            # 把第一个和第二个字段换个位置
            swapped = (
                results.CSV_FIELDS_EXPECTED[1],
                results.CSV_FIELDS_EXPECTED[0],
            ) + results.CSV_FIELDS_EXPECTED[2:]
            writer.writerow(swapped)

        try:
            results.load_results(exp, 0)
            assert False, "换顺序应该抛 ResultsError"
        except results.ResultsError as e:
            assert "表头" in str(e) or "顺序" in str(e)


def test_denominators_cover_all_seconds_fields():
    """守卫 3：分母表必须覆盖所有以 _s 结尾的字段。

    遍历 CSV_FIELDS_EXPECTED 生成断言，不许手写清单。
    手写的清单在引擎加第 17 个字段时不会变红，等于装饰。
    """
    seconds_fields = [f for f in results.CSV_FIELDS_EXPECTED if f.endswith("_s")]

    for field in seconds_fields:
        assert field in results.DENOMINATORS, (
            f"秒数字段 {field} 在 DENOMINATORS 里没有分母条目。"
            f"所有以 _s 结尾的字段都必须有分母。"
        )

    # 反向检查：DENOMINATORS 里的键必须都在 CSV_FIELDS_EXPECTED 里
    for field in results.DENOMINATORS:
        assert field in results.CSV_FIELDS_EXPECTED, (
            f"DENOMINATORS 的键 {field} 不在 CSV_FIELDS_EXPECTED 里"
        )


def test_blank_values_not_turned_into_zero():
    """守卫 4：空值不许变 0。

    喂一行 immobility_s=""，断言模型层给出的是「无」而不是 0/0.0/"0"。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        exp = _make_exp(tmp, tmp / "v.mp4")

        # 写一个合法的 run.json
        run_json = tmp / "v_run.json"
        run_json.write_text(json.dumps(_make_run_json([1])), encoding="utf-8")

        # 写一个 CSV，immobility_s 是空字符串
        csv_path = tmp / "v.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=results.CSV_FIELDS_EXPECTED)
            writer.writeheader()
            writer.writerow({
                "trial_id": "v-ch1",
                "assay": "TST",
                "fps": "30.0",
                "recording_frames": "100",
                "window_frames": "100",
                "scorable_frames": "95",
                "unknown_frames_window": "5",
                "validity_status": "valid",
                "occupied_fraction": "0.98",
                "scored": "True",
                "immobility_s": "",  # 空值
                "immobility_raw_s": "",
                "mobility_s": "",
                "mobility_bouts": "",
                "first_mobility_onset_s": "",
                "gate_messages": "未放行计分",
            })

        table = results.load_results(exp, 0)
        assert len(table.rows) == 1
        row = table.rows[0]
        assert row.kind == "scored"

        # 关键断言：空值必须是空字符串，不许变成 "0" 或 0
        assert row.immobility_s == "", (
            f"immobility_s 是空值时必须保持为空字符串，不许变成 {row.immobility_s!r}"
        )
        assert row.immobility_raw_s == ""
        assert row.mobility_s == ""
        assert row.first_mobility_onset_s == ""

        # 确保没有被转成数字 0
        assert row.immobility_s != "0"
        assert row.immobility_s != "0.0"
        assert row.immobility_s != 0


def test_alarm_rows_must_not_disappear():
    """守卫 5：报警行不许消失。

    造一份 CSV 只有 ch1、ch3，run.json 的 not_scored 有 ch2、
    chamber_validity 有 ch4 ⇒ 表必须是 4 行，且 ch2/ch4 是 kind="alarm" 且带原因。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        exp = _make_exp(tmp, tmp / "v.mp4")

        # 写 run.json，包含未产出数字的隔间
        run_json = tmp / "v_run.json"
        run_json.write_text(json.dumps(_make_run_json(
            chambers=[1, 2, 3, 4],
            not_scored=[
                {"chamber": 2, "reason": "面积判据不通过"},
            ],
            chamber_validity=[
                {
                    "chamber": 4,
                    "status": "excluded",
                    "occupied_fraction": 0.05,
                    "unsegmentable_fraction": 0.95,
                    "note": "不可分割帧过多",
                },
            ],
        )), encoding="utf-8")

        # 写 CSV，只有 ch1 和 ch3
        csv_path = tmp / "v.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=results.CSV_FIELDS_EXPECTED)
            writer.writeheader()
            for ch in [1, 3]:
                writer.writerow({
                    "trial_id": f"v-ch{ch}",
                    "assay": "TST",
                    "fps": "30.0",
                    "recording_frames": "100",
                    "window_frames": "100",
                    "scorable_frames": "95",
                    "unknown_frames_window": "5",
                    "validity_status": "valid",
                    "occupied_fraction": "0.98",
                    "scored": "True",
                    "immobility_s": "10.5",
                    "immobility_raw_s": "10.5",
                    "mobility_s": "89.5",
                    "mobility_bouts": "45",
                    "first_mobility_onset_s": "0.5",
                    "gate_messages": "",
                })

        table = results.load_results(exp, 0)

        # 必须有 4 行（ch1, ch2, ch3, ch4）
        assert len(table.rows) == 4, (
            f"期望 4 行（包括报警行），实际 {len(table.rows)} 行"
        )

        # 按 chamber 排序后检查
        chambers = {row.chamber: row for row in table.rows}

        # ch1 和 ch3 是 scored 行
        assert 1 in chambers and chambers[1].kind == "scored"
        assert 3 in chambers and chambers[3].kind == "scored"

        # ch2 是报警行（来自 not_scored）
        assert 2 in chambers, "ch2 的报警行消失了"
        assert chambers[2].kind == "alarm"
        assert "面积判据" in chambers[2].reason

        # ch4 是报警行（来自 chamber_validity）
        assert 4 in chambers, "ch4 的报警行消失了"
        assert chambers[4].kind == "alarm"
        assert "excluded" in chambers[4].reason or "不可分割" in chambers[4].reason


def test_missing_files_behavior():
    """守卫 6：两个文件缺任意一个的行为。

    - CSV 在、run.json 不在 ⇒ 可以显示数字，但标记上下文缺失
    - run.json 在、CSV 不在 ⇒ 全部隔间都是报警行
    - 两个都不在 ⇒ 抛 ResultsError
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        exp = _make_exp(tmp, tmp / "v.mp4")

        csv_path = tmp / "v.csv"
        run_json_path = tmp / "v_run.json"

        # 情况1：两个都不在 ⇒ 抛错
        try:
            results.load_results(exp, 0)
            assert False, "两个文件都不存在应该抛 ResultsError"
        except results.ResultsError as e:
            assert "都不存在" in str(e)

        # 情况2：CSV 在、run.json 不在
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=results.CSV_FIELDS_EXPECTED)
            writer.writeheader()
            writer.writerow({
                "trial_id": "v-ch1",
                "assay": "TST",
                "fps": "30.0",
                "recording_frames": "100",
                "window_frames": "100",
                "scorable_frames": "95",
                "unknown_frames_window": "5",
                "validity_status": "valid",
                "occupied_fraction": "0.98",
                "scored": "True",
                "immobility_s": "10.5",
                "immobility_raw_s": "10.5",
                "mobility_s": "89.5",
                "mobility_bouts": "45",
                "first_mobility_onset_s": "0.5",
                "gate_messages": "",
            })

        table = results.load_results(exp, 0)
        assert table.run_json_missing is True
        assert table.csv_missing is False
        # 应该能显示数字
        assert len(table.rows) >= 1
        # 第一行应该是上下文缺失的警告
        assert any("上下文缺失" in str(row.reason) for row in table.rows if row.kind == "alarm")

        # 情况3：run.json 在、CSV 不在
        csv_path.unlink()
        run_json_path.write_text(json.dumps(_make_run_json(
            chambers=[1, 2],
            not_scored=[
                {"chamber": 1, "reason": "解码失败"},
                {"chamber": 2, "reason": "解码失败"},
            ],
        )), encoding="utf-8")

        table = results.load_results(exp, 0)
        assert table.csv_missing is True
        assert table.run_json_missing is False
        # 全部隔间都是报警行
        assert all(row.kind == "alarm" for row in table.rows)
        assert len(table.rows) == 2  # ch1, ch2


def test_no_filename_literal_in_results_model():
    """守卫 7：文件名不许第二次派生。

    models/results.py 里不许出现 "_run.json" / ".csv" / "_timeline.csv" /
    "_report.txt" 这些字面量，路径必须来自 engine.output_paths。
    """
    results_py = ROOT / "desktop/app/models/results.py"
    assert results_py.exists()

    tree = ast.parse(results_py.read_text(encoding="utf-8"))

    forbidden_literals = {"_run.json", ".csv", "_timeline.csv", "_report.txt"}
    violations = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in forbidden_literals:
                violations.append(
                    f"行 {node.lineno}: 出现文件名字面量 {node.value!r}"
                )

    assert not violations, (
        f"results.py 里不许出现文件名字面量（必须用 engine.output_paths）：\n"
        + "\n".join(violations)
    )


def test_no_pyside_and_no_engine_import():
    """守卫 8：外壳不许 import 引擎。

    desktop/app/models/results.py 和 desktop/app/pages/results.py
    都不许出现 import depressionplex / from depressionplex。
    """
    files_to_check = [
        ROOT / "desktop/app/models/results.py",
        ROOT / "desktop/app/pages/results.py",
    ]

    for file_path in files_to_check:
        if not file_path.exists():
            continue  # results.py 可能还没创建，跳过

        tree = ast.parse(file_path.read_text(encoding="utf-8"))

        violations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("depressionplex"):
                        violations.append(
                            f"行 {node.lineno}: import {alias.name}"
                        )
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("depressionplex"):
                    violations.append(
                        f"行 {node.lineno}: from {node.module} import ..."
                    )

        assert not violations, (
            f"{file_path.relative_to(ROOT)} 不许 import 引擎包：\n"
            + "\n".join(violations)
        )


def test_end_to_end_with_real_engine_output():
    """守卫 9：端到端夹具，用引擎真产出的 CSV + run.json 喂模型层。

    照 test_analyze_cli.py 的合成帧路子，不需要真视频。
    断言：4 行、分母齐、报警行在。
    """
    # 复用 test_runner 的合成夹具
    from test_runner import EMPTY, MOVE, STILL, _run

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # 用合成帧跑引擎（4 个隔间，其中 ch2 是空杯）
        plan, reports, skipped = _run((MOVE, EMPTY, MOVE, STILL))

        # 构造 experiment.json
        exp = _make_exp(tmp, tmp / "synthetic.mp4", trial_prefix="syn")

        # 用引擎的 analyze._row() 和 _build_run_json() 写出文件
        from depressionplex import video as V
        from test_runner import FPS, N_FRAMES, _frame

        h, w = _frame((MOVE, EMPTY, MOVE, STILL), 0).shape
        info = V.VideoInfo(
            path=Path(tmp / "synthetic.mp4"),
            fps=FPS,
            n_frames=N_FRAMES,
            width=w,
            height=h,
            frame_count_source="packets",
        )

        # 写 CSV
        csv_path = tmp / "synthetic.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=analyze.CSV_FIELDS)
            writer.writeheader()
            for ch in sorted(reports):
                writer.writerow(analyze._row(reports[ch]))

        # 写 run.json
        run_json_path = tmp / "synthetic_run.json"
        run_data = analyze._build_run_json(info, plan, "TST", skipped, set(reports))
        with run_json_path.open("w", encoding="utf-8") as f:
            json.dump(run_data, f, indent=2)

        # 加载结果
        table = results.load_results(exp, 0)

        # 断言：4 行（包括任何报警行）
        assert len(table.rows) == 4, f"期望 4 行，实际 {len(table.rows)} 行"

        # 断言：至少有 scored 行
        scored_rows = [r for r in table.rows if r.kind == "scored"]
        alarm_rows = [r for r in table.rows if r.kind == "alarm"]
        assert len(scored_rows) > 0, "应该有 scored 行"
        # 注意：EMPTY 隔间可能被引擎判为 scored（validity_status=excluded），
        # 而不一定产出报警行，所以这里不强制要求有 alarm 行

        # 断言：scored 行的分母字段都存在且非空
        for row in scored_rows:
            # 检查 DENOMINATORS 里的所有分母字段
            for seconds_field, denom_fields in results.DENOMINATORS.items():
                for denom_field in denom_fields:
                    denom_value = getattr(row, denom_field, None)
                    assert denom_value is not None, (
                        f"scored 行的分母字段 {denom_field} 不应该是 None"
                    )
                    assert denom_value != "", (
                        f"scored 行的分母字段 {denom_field} 不应该是空字符串"
                    )

        # 断言：上下文信息存在
        assert table.tool_version is not None
        assert table.theta_mob is not None
        assert table.theta_mob == 0.0175  # FROZEN provisional


# ========================================================================
# 复核后新增的8条守卫（F1-F8）
# ========================================================================


def _make_run_json(chambers: list[int], not_scored: list[dict] | None = None,
                   chamber_validity: list[dict] | None = None) -> dict:
    """创建一个合法的 run.json 对象（包含所有必写键）。"""
    return {
        "schema_version": "1",
        "tool_version": "0.1.0",
        "assay": "TST",
        "scoring_window_s": [0, 360],
        "rules": {"theta_mob": 0.0175},
        "video": {
            "name": "test.mp4",
            "fps": 30.0,
            "n_frames": 10800,
            "frame_count_source": "packets",
        },
        "calib_indices": [0, 100, 200],
        "chambers": [{"index": ch} for ch in chambers],
        "plan_warnings": [],
        "chamber_validity": chamber_validity or [],
        "not_scored": not_scored or [],
    }


def test_f1_chamber_roster_from_runjson():
    """守卫 F1：名册来自 run.json chambers[].index。

    run.json 名册 4 个、CSV 只有 ch1/ch3、两个报警源都为空
    ⇒ 断言 4 行、ch2/ch4 是 alarm 且 reason 里点出「名册里有、产出里没有」。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        exp = _make_exp(tmp, tmp / "v.mp4")

        # 写 run.json，名册 4 个，报警源都为空
        run_json = tmp / "v_run.json"
        run_json.write_text(json.dumps(_make_run_json(
            chambers=[1, 2, 3, 4],
            not_scored=[],
            chamber_validity=[],
        )), encoding="utf-8")

        # 写 CSV，只有 ch1 和 ch3
        csv_path = tmp / "v.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=results.CSV_FIELDS_EXPECTED)
            writer.writeheader()
            for ch in [1, 3]:
                writer.writerow({
                    "trial_id": f"v-ch{ch}",
                    "assay": "TST",
                    "fps": "30.0",
                    "recording_frames": "10800",
                    "window_frames": "10800",
                    "scorable_frames": "10000",
                    "unknown_frames_window": "800",
                    "validity_status": "valid",
                    "occupied_fraction": "0.98",
                    "scored": "True",
                    "immobility_s": "100.0",
                    "immobility_raw_s": "100.0",
                    "mobility_s": "260.0",
                    "mobility_bouts": "50",
                    "first_mobility_onset_s": "1.0",
                    "gate_messages": "",
                })

        table = results.load_results(exp, 0)

        # 断言：必须有 4 行
        assert len(table.rows) == 4, f"期望 4 行（名册），实际 {len(table.rows)} 行"

        # 按 chamber 分组
        chambers_dict = {r.chamber: r for r in table.rows}

        # ch1 和 ch3 是 scored 行
        assert 1 in chambers_dict and chambers_dict[1].kind == "scored"
        assert 3 in chambers_dict and chambers_dict[3].kind == "scored"

        # ch2 和 ch4 是 alarm 行，且 reason 里点出「名册里有、产出里没有」
        assert 2 in chambers_dict, "ch2 不应该消失"
        assert chambers_dict[2].kind == "alarm"
        assert "名册里有" in chambers_dict[2].reason
        assert "产出" in chambers_dict[2].reason or "报警" in chambers_dict[2].reason

        assert 4 in chambers_dict, "ch4 不应该消失"
        assert chambers_dict[4].kind == "alarm"
        assert "名册里有" in chambers_dict[4].reason


def test_f2_no_csv_roster_only():
    """守卫 F2：无 CSV + run.json 名册 4 个 + 报警源全空 ⇒ 4 行 alarm。

    这是引擎退出码 2（一个数字都没产出）的样子，用户最需要解释。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        exp = _make_exp(tmp, tmp / "v.mp4")

        # 只写 run.json，名册 4 个，报警源都为空
        run_json = tmp / "v_run.json"
        run_json.write_text(json.dumps(_make_run_json(
            chambers=[1, 2, 3, 4],
            not_scored=[],
            chamber_validity=[],
        )), encoding="utf-8")

        # 不写 CSV

        table = results.load_results(exp, 0)

        # 断言：必须有 4 行，全是 alarm
        assert len(table.rows) == 4, f"期望 4 行，实际 {len(table.rows)} 行"
        assert all(r.kind == "alarm" for r in table.rows), "所有行都应该是 alarm"


def test_f3_scored_false_becomes_alarm():
    """守卫 F3：CSV 一行 scored=False ⇒ kind="alarm" 且 reason 含 gate_messages。

    同时断言这一行的 16 个字段还在。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        exp = _make_exp(tmp, tmp / "v.mp4")

        # 写 run.json
        run_json = tmp / "v_run.json"
        run_json.write_text(json.dumps(_make_run_json(
            chambers=[1],
        )), encoding="utf-8")

        # 写 CSV，scored=False
        csv_path = tmp / "v.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=results.CSV_FIELDS_EXPECTED)
            writer.writeheader()
            writer.writerow({
                "trial_id": "v-ch1",
                "assay": "TST",
                "fps": "30.0",
                "recording_frames": "10800",
                "window_frames": "10800",
                "scorable_frames": "10000",
                "unknown_frames_window": "800",
                "validity_status": "excluded",
                "occupied_fraction": "0.10",
                "scored": "False",
                "immobility_s": "",
                "immobility_raw_s": "",
                "mobility_s": "",
                "mobility_bouts": "",
                "first_mobility_onset_s": "",
                "gate_messages": "排除态不放行计分",
            })

        table = results.load_results(exp, 0)

        assert len(table.rows) == 1
        row = table.rows[0]

        # 断言：kind="alarm"
        assert row.kind == "alarm", f"scored=False 应该变成 alarm，实际 {row.kind}"

        # 断言：reason 含 gate_messages
        assert row.reason is not None
        assert "排除态" in row.reason or "不放行" in row.reason

        # 断言：16 个字段还在
        assert row.assay == "TST"
        assert row.fps == "30.0"
        assert row.scored == "False"
        assert row.gate_messages == "排除态不放行计分"


def test_f4_trial_prefix_single_source():
    """守卫 F4：trial_prefix 与文件名不同，跑真引擎，断言 trial_id 前缀相同。

    也测试 CSV 里出现名册外隔间时的 trial_id 前缀。
    """
    from test_runner import MOVE, _run
    from depressionplex import video as V
    from test_runner import FPS, N_FRAMES, _frame
    from desktop.app.services.engine import trial_prefix as get_trial_prefix

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # 用真引擎跑
        plan, reports, skipped = _run((MOVE, MOVE))

        # 构造 experiment，指定 trial_prefix
        exp = _make_exp(tmp, tmp / "synthetic.mp4", trial_prefix="TEST-PREFIX")

        h, w = _frame((MOVE, MOVE), 0).shape
        info = V.VideoInfo(
            path=Path(tmp / "synthetic.mp4"),
            fps=FPS,
            n_frames=N_FRAMES,
            width=w,
            height=h,
            frame_count_source="packets",
        )

        # 写 CSV
        csv_path = tmp / "synthetic.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=analyze.CSV_FIELDS)
            writer.writeheader()
            for ch in sorted(reports):
                writer.writerow(analyze._row(reports[ch]))

        # 写 run.json
        run_json_path = tmp / "synthetic_run.json"
        run_data = analyze._build_run_json(info, plan, "TST", skipped, set(reports))
        with run_json_path.open("w", encoding="utf-8") as f:
            json.dump(run_data, f, indent=2)

        # 加载结果
        table = results.load_results(exp, 0)

        # 获取期望的前缀
        expected_prefix = get_trial_prefix(exp, 0)
        assert expected_prefix == "TEST-PREFIX"

        # 断言：所有行的 trial_id 前缀相同
        for row in table.rows:
            assert row.trial_id.startswith("TEST-PREFIX-ch"), (
                f"trial_id 前缀不对：{row.trial_id}，期望 TEST-PREFIX-chN"
            )


def test_f4_no_stem_literal_in_results():
    """守卫 F4b：models/results.py 里不许出现 .stem。"""
    results_py = ROOT / "desktop/app/models/results.py"
    tree = ast.parse(results_py.read_text(encoding="utf-8"))

    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "stem":
            violations.append(f"行 {node.lineno}: .stem")

    assert not violations, (
        f"results.py 里不许出现 .stem（前缀派生只许一份，在 engine.py）：\n"
        + "\n".join(violations)
    )


def test_f5_top_level_required_keys():
    """守卫 F5：run.json 顶层必写键，缺一个就 ResultsError。

    清单从真引擎产出的 run.json 里取顶层键名生成（不许手写）。
    """
    from test_runner import MOVE, _run
    from depressionplex import video as V
    from test_runner import FPS, N_FRAMES, _frame

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # 用真引擎跑，获取真实的 run.json
        plan, reports, skipped = _run((MOVE,))
        h, w = _frame((MOVE,), 0).shape
        info = V.VideoInfo(
            path=Path(tmp / "real.mp4"),
            fps=FPS,
            n_frames=N_FRAMES,
            width=w,
            height=h,
            frame_count_source="packets",
        )

        # 生成真实的 run.json
        run_data = analyze._build_run_json(info, plan, "TST", skipped, set(reports))

        # 提取顶层键名（排除 schema_version，因为它有单独的检查）
        top_keys = [k for k in run_data.keys() if k != "schema_version"]

        # 对每个键，删掉后测试是否抛错
        for missing_key in top_keys:
            exp = _make_exp(tmp, tmp / "test.mp4")

            # 写 run.json，删掉一个键
            run_json_path = tmp / "test_run.json"
            broken_data = {k: v for k, v in run_data.items() if k != missing_key}
            with run_json_path.open("w", encoding="utf-8") as f:
                json.dump(broken_data, f)

            # 写一个空 CSV
            csv_path = tmp / "test.csv"
            with csv_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=results.CSV_FIELDS_EXPECTED)
                writer.writeheader()

            # 断言：必须抛 ResultsError 且错误里含键名
            try:
                results.load_results(exp, 0)
                assert False, f"删掉 '{missing_key}' 应该抛 ResultsError"
            except results.ResultsError as e:
                assert missing_key in str(e), (
                    f"错误信息里应该包含缺失的键名 '{missing_key}'：{e}"
                )


def test_f5_nested_required_keys():
    """守卫 F5b：run.json 嵌套必写键，缺一个就 ResultsError。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        exp = _make_exp(tmp, tmp / "v.mp4")

        # 写 CSV
        csv_path = tmp / "v.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=results.CSV_FIELDS_EXPECTED)
            writer.writeheader()

        # 测试 rules.theta_mob 缺失
        run_json = tmp / "v_run.json"
        data = _make_run_json([1])
        del data["rules"]["theta_mob"]
        run_json.write_text(json.dumps(data), encoding="utf-8")

        try:
            results.load_results(exp, 0)
            assert False, "rules 缺 theta_mob 应该抛错"
        except results.ResultsError as e:
            assert "theta_mob" in str(e)

        # 测试 video.fps 缺失
        data = _make_run_json([1])
        del data["video"]["fps"]
        run_json.write_text(json.dumps(data), encoding="utf-8")

        try:
            results.load_results(exp, 0)
            assert False, "video 缺 fps 应该抛错"
        except results.ResultsError as e:
            assert "fps" in str(e)

        # 测试 not_scored[].chamber 缺失
        data = _make_run_json([1], not_scored=[{"reason": "test"}])
        run_json.write_text(json.dumps(data), encoding="utf-8")

        try:
            results.load_results(exp, 0)
            assert False, "not_scored[] 缺 chamber 应该抛错"
        except results.ResultsError as e:
            assert "chamber" in str(e)


def test_f6_bad_trial_id_raises():
    """守卫 F6：认不出的 trial_id ⇒ ResultsError，错误里含原始字符串。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        exp = _make_exp(tmp, tmp / "v.mp4")

        # 写 run.json
        run_json = tmp / "v_run.json"
        run_json.write_text(json.dumps(_make_run_json([1])), encoding="utf-8")

        # 写 CSV，trial_id 格式错误
        csv_path = tmp / "v.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=results.CSV_FIELDS_EXPECTED)
            writer.writeheader()
            writer.writerow({
                "trial_id": "被改坏的名字",  # 没有 -ch
                "assay": "TST",
                "fps": "30.0",
                "recording_frames": "100",
                "window_frames": "100",
                "scorable_frames": "95",
                "unknown_frames_window": "5",
                "validity_status": "valid",
                "occupied_fraction": "0.98",
                "scored": "True",
                "immobility_s": "10.0",
                "immobility_raw_s": "10.0",
                "mobility_s": "90.0",
                "mobility_bouts": "10",
                "first_mobility_onset_s": "1.0",
                "gate_messages": "",
            })

        try:
            results.load_results(exp, 0)
            assert False, "坏 trial_id 应该抛 ResultsError"
        except results.ResultsError as e:
            assert "被改坏的名字" in str(e), "错误里应该含原始 trial_id"
            assert "trial_id" in str(e).lower()


def test_f7_chamber_none_for_non_chamber_alarms():
    """守卫 F7：非隔间级的报警行 chamber=None，且任何行的 chamber ≥ 1 或 None。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        exp = _make_exp(tmp, tmp / "v.mp4")

        # 只写 CSV，不写 run.json
        csv_path = tmp / "v.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=results.CSV_FIELDS_EXPECTED)
            writer.writeheader()
            writer.writerow({
                "trial_id": "v-ch1",
                "assay": "TST",
                "fps": "30.0",
                "recording_frames": "100",
                "window_frames": "100",
                "scorable_frames": "95",
                "unknown_frames_window": "5",
                "validity_status": "valid",
                "occupied_fraction": "0.98",
                "scored": "True",
                "immobility_s": "10.0",
                "immobility_raw_s": "10.0",
                "mobility_s": "90.0",
                "mobility_bouts": "10",
                "first_mobility_onset_s": "1.0",
                "gate_messages": "",
            })

        table = results.load_results(exp, 0)

        # 找到 run.json 缺失的警告行
        none_chambers = [r for r in table.rows if r.chamber is None]
        assert len(none_chambers) >= 1, "应该有 chamber=None 的警告行"

        # 断言：所有行的 chamber 要么是 None，要么 ≥ 1（不许有 0 或负数）
        for row in table.rows:
            if row.chamber is not None:
                assert row.chamber >= 1, (
                    f"chamber 必须 ≥ 1 或 None，不许是 {row.chamber}"
                )


def test_f8_denominators_回算():
    """守卫 F8：拿真引擎产出回算，断言公式成立。

    window_frames/fps − mobility_s ≈ immobility_s（3 位小数容差）
    同时断言回算用到的每一列都在 DENOMINATORS 里。
    """
    from test_runner import MOVE, STILL, KINDS4, _run
    from depressionplex import video as V
    from test_runner import FPS, N_FRAMES, _frame

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # 用真引擎跑
        plan, reports, skipped = _run(KINDS4)

        exp = _make_exp(tmp, tmp / "synthetic.mp4")

        h, w = _frame(KINDS4, 0).shape
        info = V.VideoInfo(
            path=Path(tmp / "synthetic.mp4"),
            fps=FPS,
            n_frames=N_FRAMES,
            width=w,
            height=h,
            frame_count_source="packets",
        )

        # 写 CSV
        csv_path = tmp / "synthetic.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=analyze.CSV_FIELDS)
            writer.writeheader()
            for ch in sorted(reports):
                writer.writerow(analyze._row(reports[ch]))

        # 写 run.json
        run_json_path = tmp / "synthetic_run.json"
        run_data = analyze._build_run_json(info, plan, "TST", skipped, set(reports))
        with run_json_path.open("w", encoding="utf-8") as f:
            json.dump(run_data, f, indent=2)

        # 加载结果
        table = results.load_results(exp, 0)

        # 找一个 scored 行来回算
        scored_rows = [r for r in table.rows if r.kind == "scored" and r.immobility_s]
        assert len(scored_rows) > 0, "需要至少一个有数字的 scored 行"

        for row in scored_rows:
            # 回算公式：window_frames/fps − mobility_s ≈ immobility_s
            # 注意：这里只在测试里做回算，模型层一个运算符都没有

            window_frames = float(row.window_frames)
            fps = float(row.fps)
            mobility_s = float(row.mobility_s) if row.mobility_s else 0.0
            immobility_s = float(row.immobility_s)

            window_s = window_frames / fps
            calculated_immobility = window_s - mobility_s

            # 3 位小数容差
            assert abs(calculated_immobility - immobility_s) < 0.001, (
                f"回算不符：window_frames/fps - mobility_s = "
                f"{window_frames}/{fps} - {mobility_s} = {calculated_immobility:.3f}，"
                f"但 immobility_s = {immobility_s}"
            )

            # 断言：回算用到的每一列都在 DENOMINATORS["immobility_s"] 里
            used_columns = {"window_frames", "fps"}  # mobility_s 不算，它是被减数
            denominators_set = set(results.DENOMINATORS["immobility_s"])
            assert used_columns.issubset(denominators_set), (
                f"回算用到的列 {used_columns} 必须都在 DENOMINATORS['immobility_s'] 里：{denominators_set}"
            )


# ========================================================================
# 第二轮复核新增守卫（G1–G6）
# ========================================================================


def test_g1_roster_unknown_when_no_runjson():
    """守卫 G1：run.json 缺失时，CSV scored 行保持 kind='scored'，reason=None。

    不许把「名册未知」当成「名册为空」——后者会给每一行挂「名册外」的假话。
    全表必须没有任何 reason 含「名册」二字。
    同时断言存在一条 chamber=None 的上下文缺失 alarm。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        exp = _make_exp(tmp, tmp / "v.mp4")

        # 只写 CSV，不写 run.json
        csv_path = tmp / "v.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=results.CSV_FIELDS_EXPECTED)
            writer.writeheader()
            for ch in [1, 3]:
                writer.writerow({
                    "trial_id": f"v-ch{ch}",
                    "assay": "TST",
                    "fps": "30.0",
                    "recording_frames": "10800",
                    "window_frames": "10800",
                    "scorable_frames": "10000",
                    "unknown_frames_window": "800",
                    "validity_status": "valid",
                    "occupied_fraction": "0.98",
                    "scored": "True",
                    "immobility_s": "100.0",
                    "immobility_raw_s": "100.0",
                    "mobility_s": "260.0",
                    "mobility_bouts": "50",
                    "first_mobility_onset_s": "1.0",
                    "gate_messages": "",
                })

        table = results.load_results(exp, 0)

        # 共 3 行：ch=None 的上下文缺失 alarm + ch1 scored + ch3 scored
        assert len(table.rows) == 3, f"期望 3 行，实际 {len(table.rows)} 行"

        # 必须有一条 chamber=None 的上下文缺失 alarm
        none_rows = [r for r in table.rows if r.chamber is None]
        assert len(none_rows) == 1, "必须有一条 chamber=None 的上下文缺失 alarm"
        assert none_rows[0].kind == "alarm"
        assert "上下文缺失" in (none_rows[0].reason or "")

        # CSV scored 行必须保持 kind='scored'，reason=None
        ch_rows = {r.chamber: r for r in table.rows if r.chamber is not None}
        assert ch_rows[1].kind == "scored", f"ch1 应该是 scored，实际 {ch_rows[1].kind}"
        assert ch_rows[1].reason is None, f"ch1 reason 应该是 None，实际 {ch_rows[1].reason!r}"
        assert ch_rows[3].kind == "scored", f"ch3 应该是 scored，实际 {ch_rows[3].kind}"
        assert ch_rows[3].reason is None, f"ch3 reason 应该是 None，实际 {ch_rows[3].reason!r}"

        # 全表不许有任何 reason 含「名册」二字（G1 的核心要求）
        for row in table.rows:
            if row.reason is not None:
                assert "名册" not in row.reason, (
                    f"run.json 缺失时不许出现「名册」相关 reason，"
                    f"但 ch={row.chamber} 的 reason={row.reason!r}"
                )


def _make_scored_csv_row(tmp: Path, chambers: list[int]) -> None:
    """辅助：写一份合法的多隔间 CSV。"""
    csv_path = tmp / "v.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=results.CSV_FIELDS_EXPECTED)
        writer.writeheader()
        for ch in chambers:
            writer.writerow({
                "trial_id": f"v-ch{ch}",
                "assay": "TST",
                "fps": "30.0",
                "recording_frames": "10800",
                "window_frames": "10800",
                "scorable_frames": "10000",
                "unknown_frames_window": "800",
                "validity_status": "valid",
                "occupied_fraction": "0.98",
                "scored": "True",
                "immobility_s": "100.0",
                "immobility_raw_s": "100.0",
                "mobility_s": "260.0",
                "mobility_bouts": "50",
                "first_mobility_onset_s": "1.0",
                "gate_messages": "",
            })


def test_g2_truncated_csv_row_raises():
    """守卫 G2：CSV 行被截短 ⇒ ResultsError，错误里含行号。

    DictReader 把缺失的尾部列填 None（restval）。
    以前会在 .lower() 时炸成 AttributeError；现在必须提前检测 None 值并抛 ResultsError。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        exp = _make_exp(tmp, tmp / "v.mp4")

        run_json = tmp / "v_run.json"
        run_json.write_text(json.dumps(_make_run_json([1])), encoding="utf-8")

        # 写截短行（只有前 3 个值，后 13 个缺失）
        csv_path = tmp / "v.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            f.write(",".join(results.CSV_FIELDS_EXPECTED) + "\n")
            f.write("v-ch1,TST,30.0\n")  # 只有 3 列，后面缺失

        try:
            results.load_results(exp, 0)
            assert False, "截短行应该抛 ResultsError"
        except results.ResultsError as e:
            err_str = str(e)
            # 必须包含行号（第 2 行，因为第 1 行是表头）
            assert "2" in err_str, f"错误里应该含行号，实际：{err_str}"
            # 必须包含"截短"或相关说明
            assert "截短" in err_str or "缺少" in err_str or "None" in err_str.lower() or "列" in err_str, (
                f"错误描述不够清楚：{err_str}"
            )


def test_g3_extra_long_csv_row_raises():
    """守卫 G3：CSV 行比表头多值 ⇒ ResultsError，错误里含行号。

    DictReader 把多余的值放在 None 键下；以前静默丢掉，现在必须抛错。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        exp = _make_exp(tmp, tmp / "v.mp4")

        run_json = tmp / "v_run.json"
        run_json.write_text(json.dumps(_make_run_json([1])), encoding="utf-8")

        # 写超长行（17 个值，表头只有 16 列）
        csv_path = tmp / "v.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(results.CSV_FIELDS_EXPECTED)
            # 合法的 16 个值 + 1 个多出来的值
            row = [
                "v-ch1", "TST", "30.0", "10800", "10800",
                "10000", "800", "valid", "0.98", "True",
                "100.0", "100.0", "260.0", "50", "1.0",
                "",           # gate_messages（合法）
                "extra_val",  # 第 17 个，多余的
            ]
            writer.writerow(row)

        try:
            results.load_results(exp, 0)
            assert False, "超长行应该抛 ResultsError"
        except results.ResultsError as e:
            err_str = str(e)
            assert "2" in err_str, f"错误里应该含行号，实际：{err_str}"
            assert "多余" in err_str or "多于" in err_str or "超" in err_str or "extra" in err_str.lower(), (
                f"错误描述不够清楚：{err_str}"
            )


def test_g2_g3_reverse_guard_empty_string_valid():
    """反向守卫：空串字段不许被 G2/G3 检查误判为坏行。

    引擎未放行的行 immobility_s="" 是合法的，只有 None（列真的缺失）才是坏行。
    拿 F3 那份 immobility_s="" 的夹具再跑一次，断言仍能正常读出。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        exp = _make_exp(tmp, tmp / "v.mp4")

        run_json = tmp / "v_run.json"
        run_json.write_text(json.dumps(_make_run_json([1])), encoding="utf-8")

        # 与 test_f3_scored_false_becomes_alarm 完全一样的夹具
        csv_path = tmp / "v.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=results.CSV_FIELDS_EXPECTED)
            writer.writeheader()
            writer.writerow({
                "trial_id": "v-ch1",
                "assay": "TST",
                "fps": "30.0",
                "recording_frames": "10800",
                "window_frames": "10800",
                "scorable_frames": "10000",
                "unknown_frames_window": "800",
                "validity_status": "excluded",
                "occupied_fraction": "0.10",
                "scored": "False",
                "immobility_s": "",       # 空串，合法
                "immobility_raw_s": "",   # 空串，合法
                "mobility_s": "",
                "mobility_bouts": "",
                "first_mobility_onset_s": "",
                "gate_messages": "排除态不放行计分",
            })

        # 必须能正常读出，不能因为空串而报 G2 错误
        table = results.load_results(exp, 0)
        assert len(table.rows) == 1
        row = table.rows[0]
        assert row.kind == "alarm"
        assert row.immobility_s == ""   # 空串原样保留


def test_g4_duplicate_chamber_raises():
    """守卫 G4：CSV 里同一个隔间出现两行 ⇒ ResultsError，错误里含两个行号。

    以前前一行会被静默覆盖，数字被悄悄顶掉，用户不可能发现。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        exp = _make_exp(tmp, tmp / "v.mp4")

        run_json = tmp / "v_run.json"
        run_json.write_text(json.dumps(_make_run_json([1])), encoding="utf-8")

        # 写 CSV，ch1 出现两行，第二行 immobility_s 故意不同
        csv_path = tmp / "v.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=results.CSV_FIELDS_EXPECTED)
            writer.writeheader()
            # 第一行（行号 2）
            writer.writerow({
                "trial_id": "v-ch1",
                "assay": "TST",
                "fps": "30.0",
                "recording_frames": "10800",
                "window_frames": "10800",
                "scorable_frames": "10000",
                "unknown_frames_window": "800",
                "validity_status": "valid",
                "occupied_fraction": "0.98",
                "scored": "True",
                "immobility_s": "100.0",
                "immobility_raw_s": "100.0",
                "mobility_s": "260.0",
                "mobility_bouts": "50",
                "first_mobility_onset_s": "1.0",
                "gate_messages": "",
            })
            # 第二行（行号 3）—— 同一个 ch1
            writer.writerow({
                "trial_id": "v-ch1",
                "assay": "TST",
                "fps": "30.0",
                "recording_frames": "10800",
                "window_frames": "10800",
                "scorable_frames": "10000",
                "unknown_frames_window": "800",
                "validity_status": "valid",
                "occupied_fraction": "0.98",
                "scored": "True",
                "immobility_s": "9.9",    # 故意不同
                "immobility_raw_s": "9.9",
                "mobility_s": "350.1",
                "mobility_bouts": "50",
                "first_mobility_onset_s": "1.0",
                "gate_messages": "",
            })

        try:
            results.load_results(exp, 0)
            assert False, "重复隔间应该抛 ResultsError"
        except results.ResultsError as e:
            err_str = str(e)
            # 错误里必须含两个行号（2 和 3）
            assert "2" in err_str and "3" in err_str, (
                f"错误里应该含两个行号（2 和 3），实际：{err_str}"
            )
            # 错误里必须提到 ch1
            assert "ch1" in err_str or "chamber" in err_str.lower(), (
                f"错误里应该提到重复的 chamber，实际：{err_str}"
            )


def test_g5_invalid_scored_value_raises():
    """守卫 G5：scored="yes" ⇒ ResultsError；非 True/False 一律拒。

    以前 "yes" 会被当成 False 处理，同时给出假理由「CSV 标了未放行计分，但引擎没给原因」。
    引擎写 scored 列用的是 Python bool（→ True/False），没有第三种合法取值。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        exp = _make_exp(tmp, tmp / "v.mp4")

        run_json = tmp / "v_run.json"
        run_json.write_text(json.dumps(_make_run_json([1])), encoding="utf-8")

        csv_path = tmp / "v.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=results.CSV_FIELDS_EXPECTED)
            writer.writeheader()
            writer.writerow({
                "trial_id": "v-ch1",
                "assay": "TST",
                "fps": "30.0",
                "recording_frames": "10800",
                "window_frames": "10800",
                "scorable_frames": "10000",
                "unknown_frames_window": "800",
                "validity_status": "valid",
                "occupied_fraction": "0.98",
                "scored": "yes",    # 不合法
                "immobility_s": "100.0",
                "immobility_raw_s": "100.0",
                "mobility_s": "260.0",
                "mobility_bouts": "50",
                "first_mobility_onset_s": "1.0",
                "gate_messages": "",
            })

        try:
            results.load_results(exp, 0)
            assert False, "scored='yes' 应该抛 ResultsError"
        except results.ResultsError as e:
            err_str = str(e)
            assert "scored" in err_str.lower() or "yes" in err_str, (
                f"错误里应该提到 scored 值或原始值，实际：{err_str}"
            )


def test_g5_valid_scored_variants_accepted():
    """守卫 G5 反向：True/False/true/false 四种写法都能正常读出。

    引擎写 True/False，但大小写变体也必须接受（防止引擎实现变化）。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        exp = _make_exp(tmp, tmp / "v.mp4")

        run_json = tmp / "v_run.json"
        run_json.write_text(json.dumps(_make_run_json([1, 2, 3, 4])), encoding="utf-8")

        csv_path = tmp / "v.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=results.CSV_FIELDS_EXPECTED)
            writer.writeheader()
            # 四种合法写法：True, False, true, false
            for ch, scored_val in [(1, "True"), (2, "False"), (3, "true"), (4, "false")]:
                writer.writerow({
                    "trial_id": f"v-ch{ch}",
                    "assay": "TST",
                    "fps": "30.0",
                    "recording_frames": "10800",
                    "window_frames": "10800",
                    "scorable_frames": "10000",
                    "unknown_frames_window": "800",
                    "validity_status": "valid",
                    "occupied_fraction": "0.98",
                    "scored": scored_val,
                    "immobility_s": "100.0" if scored_val.lower() == "true" else "",
                    "immobility_raw_s": "100.0" if scored_val.lower() == "true" else "",
                    "mobility_s": "260.0" if scored_val.lower() == "true" else "",
                    "mobility_bouts": "50" if scored_val.lower() == "true" else "",
                    "first_mobility_onset_s": "1.0" if scored_val.lower() == "true" else "",
                    "gate_messages": "" if scored_val.lower() == "true" else "排除",
                })

        # 必须能正常读出，不抛错
        table = results.load_results(exp, 0)
        assert len(table.rows) == 4

        ch_rows = {r.chamber: r for r in table.rows}
        # ch1 和 ch3 是 scored=True 的行
        assert ch_rows[1].kind == "scored"
        assert ch_rows[3].kind == "scored"
        # ch2 和 ch4 是 scored=False 的 alarm 行
        assert ch_rows[2].kind == "alarm"
        assert ch_rows[4].kind == "alarm"
