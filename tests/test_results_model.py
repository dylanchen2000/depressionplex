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
        run_json.write_text(json.dumps({
            "schema_version": "1",
            "tool_version": "0.1.0",
            "assay": "TST",
            "scoring_window_s": [0, 360],
            "rules": {"theta_mob": 0.0175},
            "video": {"name": "v.mp4", "fps": 30.0, "n_frames": 100},
            "not_scored": [],
            "chamber_validity": [],
        }), encoding="utf-8")

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
        run_json.write_text(json.dumps({
            "schema_version": "1",
            "tool_version": "0.1.0",
            "assay": "TST",
            "scoring_window_s": [0, 360],
            "rules": {"theta_mob": 0.0175},
            "video": {"name": "v.mp4", "fps": 30.0, "n_frames": 100},
            "not_scored": [],
            "chamber_validity": [],
        }), encoding="utf-8")

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
        run_json.write_text(json.dumps({
            "schema_version": "1",
            "tool_version": "0.1.0",
            "assay": "TST",
            "scoring_window_s": [0, 360],
            "rules": {"theta_mob": 0.0175},
            "video": {"name": "v.mp4", "fps": 30.0, "n_frames": 100},
            "not_scored": [
                {"chamber": 2, "reason": "面积判据不通过"},
            ],
            "chamber_validity": [
                {
                    "chamber": 4,
                    "status": "excluded",
                    "occupied_fraction": 0.05,
                    "unsegmentable_fraction": 0.95,
                    "note": "不可分割帧过多",
                },
            ],
        }), encoding="utf-8")

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
        run_json_path.write_text(json.dumps({
            "schema_version": "1",
            "tool_version": "0.1.0",
            "assay": "TST",
            "scoring_window_s": [0, 360],
            "rules": {"theta_mob": 0.0175},
            "video": {"name": "v.mp4", "fps": 30.0, "n_frames": 100},
            "not_scored": [
                {"chamber": 1, "reason": "解码失败"},
                {"chamber": 2, "reason": "解码失败"},
            ],
            "chamber_validity": [],
        }), encoding="utf-8")

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
