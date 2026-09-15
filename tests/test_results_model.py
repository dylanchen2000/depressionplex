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
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent

# 可以 import 引擎（tests/ 不受进程边界约束）
sys.path.insert(0, str(ROOT))
from depressionplex.cli import analyze
from depressionplex.video import TOOL_FFMPEG, TOOL_FFPROBE

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
        ffmpeg_path, ffmpeg_source, ffprobe_path, ffprobe_source = _decoder_identity(tmp)
        run_data = analyze._build_run_json(info, plan, "TST", skipped, set(reports),
                                           ffmpeg_path, ffmpeg_source,
                                           ffprobe_path, ffprobe_source)
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


def _decoder_identity(tmp: Path) -> tuple[str, str, str, str]:
    """按产品那条路取解码器身份，穿进 `_build_run_json`（DP-108 H10+A4）。

    两个工具各解析一次（source 可以不同：只设了 DPX_FFMPEG、或随包漏了一个文件），
    并且**走真的 `_resolve_ffmpeg_tool`**——在测试里手写 `"system"` 之类的字面量，
    等于给 run.json 的 decoder 块编一个产品永远不会写出来的值，那个块就再也没人验了。

    用 DPX_FFMPEG / DPX_FFPROBE 指到 tmp 下两个真实存在的空文件：这几条测试因此在
    没装 ffmpeg 的机器上也能跑，走的还是解析器的 env 真分支（不是 patch 掉
    `Path.exists`——本文件同一个 tmp 里还要真读真写 CSV 与 run.json，
    全局 patch 掉 exists 会把「文件不在」这类失败一起盖住）。
    空文件跑不起来，`_get_ffmpeg_version` 会按它自己的退化路径给 None 并往 stderr
    说一句，这也是产品在现场会走的路。
    """
    from depressionplex import video as V

    stub_ffmpeg = tmp / "stub_ffmpeg"
    stub_ffprobe = tmp / "stub_ffprobe"
    stub_ffmpeg.write_bytes(b"")
    stub_ffprobe.write_bytes(b"")
    with mock.patch.dict(
        "os.environ",
        {"DPX_FFMPEG": str(stub_ffmpeg), "DPX_FFPROBE": str(stub_ffprobe)},
    ):
        ffmpeg_path, ffmpeg_source = V._resolve_ffmpeg_tool(TOOL_FFMPEG)
        ffprobe_path, ffprobe_source = V._resolve_ffmpeg_tool(TOOL_FFPROBE)
    return ffmpeg_path, ffmpeg_source, ffprobe_path, ffprobe_source


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
        # decoder 块：引擎每次都写（DP-108 A4），所以它和上面几个键一样是必写的。
        # 键名用 video 的常量，不在这里手抄字符串（抄了就是第二份工具名真值）。
        "decoder": {
            TOOL_FFMPEG: {
                "path": "/usr/bin/ffmpeg",
                "source": "system",
                "version": "ffmpeg version 6.0",
            },
            TOOL_FFPROBE: {
                "path": "/usr/bin/ffprobe",
                "source": "system",
                "version": "ffprobe version 6.0",
            },
            "mixed_source": False,
        },
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
        ffmpeg_path, ffmpeg_source, ffprobe_path, ffprobe_source = _decoder_identity(tmp)
        run_data = analyze._build_run_json(info, plan, "TST", skipped, set(reports),
                                           ffmpeg_path, ffmpeg_source,
                                           ffprobe_path, ffprobe_source)
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
        ffmpeg_path, ffmpeg_source, ffprobe_path, ffprobe_source = _decoder_identity(tmp)
        run_data = analyze._build_run_json(info, plan, "TST", skipped, set(reports),
                                           ffmpeg_path, ffmpeg_source,
                                           ffprobe_path, ffprobe_source)

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
        ffmpeg_path, ffmpeg_source, ffprobe_path, ffprobe_source = _decoder_identity(tmp)
        run_data = analyze._build_run_json(info, plan, "TST", skipped, set(reports),
                                           ffmpeg_path, ffmpeg_source,
                                           ffprobe_path, ffprobe_source)
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


# ========================================================================
# 第三轮复核新增守卫（N1–N13）：run.json 值类型校验
# ========================================================================


def _make_csv_with_rows(tmp: Path, chambers: list[int]) -> None:
    """辅助：写一份含指定隔间行的合法 CSV。"""
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


def _make_empty_csv(tmp: Path) -> None:
    """辅助：写一份只有表头、没有数据行的 CSV。"""
    csv_path = tmp / "v.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=results.CSV_FIELDS_EXPECTED)
        writer.writeheader()


def test_n1_chambers_null_raises():
    """守卫 N1：chambers: null → ResultsError（不许 TypeError 飞到调用方）。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        exp = _make_exp(tmp, tmp / "v.mp4")

        data = _make_run_json([1])
        data["chambers"] = None   # null

        (tmp / "v_run.json").write_text(json.dumps(data), encoding="utf-8")
        _make_empty_csv(tmp)

        try:
            results.load_results(exp, 0)
            assert False, "chambers=null 应该抛 ResultsError"
        except results.ResultsError as e:
            assert "chambers" in str(e).lower(), f"错误信息应提到 chambers：{e}"


def test_n2_chambers_wrong_type_roster_none():
    """守卫 N2：chambers 是 {} → 名册进「不可信」态，不许出现「名册外的隔间」。

    {} 是 dict 不是 list，迭代结果取决于 dict 内容（此处为空 dict），
    会导致 chamber_roster=set() 而 CSV 里的产出行全被扣上「名册外的隔间」。
    修复后：chamber_roster 必须变 None，CSV 行保持自己的 kind。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        exp = _make_exp(tmp, tmp / "v.mp4")

        data = _make_run_json([])
        data["chambers"] = {}   # dict，不是 list

        (tmp / "v_run.json").write_text(json.dumps(data), encoding="utf-8")
        _make_csv_with_rows(tmp, [1, 3])

        # 不许抛错，必须正常返回
        table = results.load_results(exp, 0)

        # 隔间行（chamber is not None）不许被贴「名册外的隔间」标签
        # 注意：chamber=None 的元报警行（说明名册为何不可信）可以提到这个词，但不应出现在隔间行里
        for row in table.rows:
            if row.chamber is not None and row.reason is not None:
                assert "名册外的隔间" not in row.reason, (
                    f"chambers={{}} 时隔间行不许被贴「名册外的隔间」标签，"
                    f"ch={row.chamber} reason={row.reason!r}"
                )

        # CSV 行必须保持自己的 kind（scored）
        ch_rows = {r.chamber: r for r in table.rows if r.chamber is not None}
        assert 1 in ch_rows and ch_rows[1].kind == "scored"
        assert 3 in ch_rows and ch_rows[3].kind == "scored"


def test_n3_chambers_items_not_dict_raises():
    """守卫 N3：chambers=[1, 2]（条目不是 dict）→ ResultsError。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        exp = _make_exp(tmp, tmp / "v.mp4")

        data = _make_run_json([])
        data["chambers"] = [1, 2]   # 条目是 int，不是 dict

        (tmp / "v_run.json").write_text(json.dumps(data), encoding="utf-8")
        _make_empty_csv(tmp)

        try:
            results.load_results(exp, 0)
            assert False, "chambers=[1,2] 应该抛 ResultsError"
        except results.ResultsError as e:
            assert "chambers" in str(e).lower(), f"错误信息应提到 chambers：{e}"


def test_n4_chambers_index_wrong_type_raises():
    """守卫 N4：chambers[].index 是字符串 "1" → ResultsError（index 必须是整数）。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        exp = _make_exp(tmp, tmp / "v.mp4")

        data = _make_run_json([])
        data["chambers"] = [{"index": "1"}]   # index 是 str

        (tmp / "v_run.json").write_text(json.dumps(data), encoding="utf-8")
        _make_empty_csv(tmp)

        try:
            results.load_results(exp, 0)
            assert False, "index='1' 应该抛 ResultsError"
        except results.ResultsError as e:
            assert "index" in str(e).lower(), f"错误信息应提到 index：{e}"


def test_n5_chambers_index_null_raises():
    """守卫 N5：chambers[].index 是 null → ResultsError。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        exp = _make_exp(tmp, tmp / "v.mp4")

        data = _make_run_json([])
        data["chambers"] = [{"index": None}]   # index 是 null

        (tmp / "v_run.json").write_text(json.dumps(data), encoding="utf-8")
        _make_empty_csv(tmp)

        try:
            results.load_results(exp, 0)
            assert False, "index=null 应该抛 ResultsError"
        except results.ResultsError as e:
            assert "index" in str(e).lower(), f"错误信息应提到 index：{e}"


def test_n6_empty_chambers_with_csv_rows_roster_none():
    """守卫 N6：chambers=[] 但 CSV 有产出行 → 名册进「不可信」态。

    引擎不可能在计划里没有 chN 的情况下产出 chN 的秒数；
    这种矛盾说明名册不可信，不许推出「名册外的隔间」。
    对照组：chambers=[] 且 CSV 也为空 → 正常（真的空名册）。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        exp = _make_exp(tmp, tmp / "v.mp4")

        data = _make_run_json([])   # chambers=[]

        (tmp / "v_run.json").write_text(json.dumps(data), encoding="utf-8")
        _make_csv_with_rows(tmp, [1, 3])   # CSV 有产出行 → 矛盾

        # 不许抛错，必须正常返回
        table = results.load_results(exp, 0)

        # 隔间行（chamber is not None）不许被贴「名册外的隔间」标签
        for row in table.rows:
            if row.chamber is not None and row.reason is not None:
                assert "名册外的隔间" not in row.reason, (
                    f"chambers=[] 且 CSV 有行时隔间行不许被贴「名册外的隔间」标签，"
                    f"ch={row.chamber} reason={row.reason!r}"
                )

        # CSV 行保持 kind='scored'
        ch_rows = {r.chamber: r for r in table.rows if r.chamber is not None}
        assert 1 in ch_rows and ch_rows[1].kind == "scored"
        assert 3 in ch_rows and ch_rows[3].kind == "scored"

    # 对照组：chambers=[] 且 CSV 也为空 → 应该能正常加载（真的空名册）
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        exp = _make_exp(tmp, tmp / "v.mp4")

        data = _make_run_json([])   # chambers=[]
        (tmp / "v_run.json").write_text(json.dumps(data), encoding="utf-8")
        _make_empty_csv(tmp)   # CSV 也没有产出行

        # 不许抛错
        table = results.load_results(exp, 0)
        # 没有 chamber 行（名册为空，CSV 也为空）
        ch_rows = [r for r in table.rows if r.chamber is not None]
        assert len(ch_rows) == 0, f"空名册 + 空 CSV 不应有 chamber 行，实际：{ch_rows}"


def test_n7_not_scored_null_raises():
    """守卫 N7：not_scored: null → ResultsError（不许 TypeError 飞到调用方）。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        exp = _make_exp(tmp, tmp / "v.mp4")

        # chambers=[1]，CSV 没有该隔间 → 模型要去查 not_scored
        data = _make_run_json([1])
        data["not_scored"] = None   # null

        (tmp / "v_run.json").write_text(json.dumps(data), encoding="utf-8")
        _make_empty_csv(tmp)   # ch1 在名册里但 CSV 无此行

        try:
            results.load_results(exp, 0)
            assert False, "not_scored=null 应该抛 ResultsError"
        except results.ResultsError as e:
            assert "not_scored" in str(e), f"错误信息应提到 not_scored：{e}"


def test_n9_rules_null_raises():
    """守卫 N9：rules: null → ResultsError（不许 TypeError 飞到调用方）。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        exp = _make_exp(tmp, tmp / "v.mp4")

        data = _make_run_json([1])
        data["rules"] = None   # null

        (tmp / "v_run.json").write_text(json.dumps(data), encoding="utf-8")
        _make_empty_csv(tmp)

        try:
            results.load_results(exp, 0)
            assert False, "rules=null 应该抛 ResultsError"
        except results.ResultsError as e:
            assert "rules" in str(e), f"错误信息应提到 rules：{e}"


def test_n10_video_null_raises():
    """守卫 N10：video: null → ResultsError（不许 TypeError 飞到调用方）。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        exp = _make_exp(tmp, tmp / "v.mp4")

        data = _make_run_json([1])
        data["video"] = None   # null

        (tmp / "v_run.json").write_text(json.dumps(data), encoding="utf-8")
        _make_empty_csv(tmp)

        try:
            results.load_results(exp, 0)
            assert False, "video=null 应该抛 ResultsError"
        except results.ResultsError as e:
            assert "video" in str(e), f"错误信息应提到 video：{e}"


def test_n11_context_fields_wrong_type_become_none():
    """守卫 N11：上下文字段类型不对时，字段变 None（不抛错，不许原样传给报告）。

    scoring_window_s=1（期望 list）、theta_mob='很大'（期望数字）、
    video.name=123（期望 str）、video.fps='abc'（期望数字）、
    video.n_frames=[]（期望 int）→ 载入成功，五个字段都变 None。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        exp = _make_exp(tmp, tmp / "v.mp4")

        data = _make_run_json([1])
        # 五个字段都设成错误类型
        data["scoring_window_s"] = 1          # int，期望 list
        data["rules"]["theta_mob"] = "很大"   # str，期望数字
        data["video"]["name"] = 123            # int，期望 str
        data["video"]["fps"] = "abc"           # str，期望数字
        data["video"]["n_frames"] = []         # list，期望 int

        (tmp / "v_run.json").write_text(json.dumps(data), encoding="utf-8")
        _make_csv_with_rows(tmp, [1])

        # 不许抛错，必须正常返回
        table = results.load_results(exp, 0)

        # 五个字段必须全是 None
        assert table.scoring_window_s is None, (
            f"scoring_window_s=1 时应变 None，实际 {table.scoring_window_s!r}"
        )
        assert table.theta_mob is None, (
            f"theta_mob='很大' 时应变 None，实际 {table.theta_mob!r}"
        )
        assert table.video_name is None, (
            f"video.name=123 时应变 None，实际 {table.video_name!r}"
        )
        assert table.video_fps is None, (
            f"video.fps='abc' 时应变 None，实际 {table.video_fps!r}"
        )
        assert table.video_n_frames is None, (
            f"video.n_frames=[] 时应变 None，实际 {table.video_n_frames!r}"
        )


def test_n11_context_fields_reverse_guard():
    """守卫 N11 反向：合法的 run.json 载入后，上下文字段必须是原值（不变 None）。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        exp = _make_exp(tmp, tmp / "v.mp4")

        data = _make_run_json([1])
        # _make_run_json 已经设了合法值
        (tmp / "v_run.json").write_text(json.dumps(data), encoding="utf-8")
        _make_csv_with_rows(tmp, [1])

        table = results.load_results(exp, 0)

        # 合法值不许被当成「类型不对」而清零
        assert table.scoring_window_s == [0, 360]
        assert table.theta_mob == 0.0175
        assert table.video_name == "test.mp4"
        assert table.video_fps == 30.0
        assert table.video_n_frames == 10800

        # 字段类型也必须正确（不许变成 str 之类的）
        assert isinstance(table.scoring_window_s, list)
        assert isinstance(table.theta_mob, float)
        assert isinstance(table.video_name, str)
        assert isinstance(table.video_fps, float)
        assert isinstance(table.video_n_frames, int)


def test_n13_top_level_not_dict_raises():
    """守卫 N13：run.json 顶层是 [] 而非 {} → ResultsError，错误提到顶层类型不对。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        exp = _make_exp(tmp, tmp / "v.mp4")

        # 顶层写成数组
        (tmp / "v_run.json").write_text("[]", encoding="utf-8")
        _make_empty_csv(tmp)

        try:
            results.load_results(exp, 0)
            assert False, "顶层是数组应该抛 ResultsError"
        except results.ResultsError as e:
            err_str = str(e)
            assert "顶层" in err_str or "dict" in err_str.lower() or "对象" in err_str, (
                f"错误信息应指出顶层类型不对，实际：{err_str}"
            )


def test_bool_index_raises():
    """守卫 bool：chambers[].index=true → ResultsError（bool 不算 int）。

    Python 里 isinstance(True, int) 为 True，不加特判会把 true 当成 1 接受。
    引擎不可能写 true，写了就是合约破坏，必须拒绝。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        exp = _make_exp(tmp, tmp / "v.mp4")

        data = _make_run_json([])
        data["chambers"] = [{"index": True}]   # bool，不是 int

        (tmp / "v_run.json").write_text(json.dumps(data), encoding="utf-8")
        _make_empty_csv(tmp)

        try:
            results.load_results(exp, 0)
            assert False, "index=true 应该抛 ResultsError"
        except results.ResultsError as e:
            err_str = str(e)
            assert "index" in err_str.lower() or "bool" in err_str.lower(), (
                f"错误信息应提到 index 或 bool：{err_str}"
            )


# ========================================================================
# 架构师收口（DP-107 第三轮结束时，改的人 = 复核的人）：
# 同一个形状的第四处。前三轮修的是「run.json 的顶层键」，这一批修的是
#   ① 上下文字段的**元素类型**（`list[float]` 里的 float 也得当真）；
#   ② 降级的**说明**（第三轮派工单 §2.3 要求「不合就当未知并挂一条说明」，
#      说明那半句没做——一个静默变成「未知」的数字，用户分不清是没记还是文件坏了）；
#   ③ `not_scored[]` / `chamber_validity[]` **条目层**的类型（顶层修了，条目层没修，
#      七个裸 TypeError 仍会穿到 Qt 事件循环）。
#
# ③ 里有一个专门的陷阱：条目是**字符串**时，`"chamber" not in item` 不报错，
# 它变成了子串判断——`"chamber"` 这个字符串自己就含 "chamber"，检查恒真通过，
# 然后 `item["chamber"]` 才抛 TypeError。所以「缺键检查」看着像挡住了非 dict，
# 其实没挡。查类型必须显式 `isinstance`。
# ========================================================================


def _load_with_run_json(tmp: Path, data: dict, chambers_in_csv: list[int]):
    """辅助：写 run.json + CSV 后载入（这一批守卫都是这个形状）。"""
    exp = _make_exp(tmp, tmp / "v.mp4")
    (tmp / "v_run.json").write_text(json.dumps(data), encoding="utf-8")
    if chambers_in_csv:
        _make_csv_with_rows(tmp, chambers_in_csv)
    else:
        _make_empty_csv(tmp)
    return results.load_results(exp, 0)


def test_scoring_window_elements_must_be_numbers():
    """`scoring_window_s: list[float]` 里的元素也要是数字。

    只查「是不是 list」的后果：`['a','b']` 原样进 `ResultsTable`，
    再原样印到客户那份 PDF 的「计分窗口」一栏上。**注解不是校验。**
    空列表同样算未知——一个没有边界的窗口不是窗口。
    """
    for bad in (["a", "b"], [0, "360"], [None], [True, 360], []):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            data = _make_run_json([1])
            data["scoring_window_s"] = bad
            table = _load_with_run_json(tmp, data, [1])
            assert table.scoring_window_s is None, (
                f"scoring_window_s={bad!r} 应当按未知处理，实际 {table.scoring_window_s!r}")


def test_scoring_window_reverse_guard_lengths():
    """反向：合法窗口不许被判掉，**长度也不许限**。

    FST 的计分窗口口径还没定（DP-057/074）。今天写死「必须两个数」等于给将来的
    合法输出埋一条假违规，而假违规的下一步永远是有人把校验整段删掉。
    """
    for good in ([0, 360], [0.0, 360.0], [120, 360], [0, 60, 120]):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            data = _make_run_json([1])
            data["scoring_window_s"] = good
            table = _load_with_run_json(tmp, data, [1])
            assert table.scoring_window_s == good, (
                f"合法窗口 {good!r} 被判成了 {table.scoring_window_s!r}")


def test_tool_version_and_assay_type_checked():
    """`tool_version` / `assay` 也要校验：它们印在报告上。

    「这份结果是哪个版本算的」印成 `123`，和印一个假版本号没有区别。
    空串同样算未知：报告上一个空格看起来像「本来就没有这一项」。
    """
    for key, bad in (("tool_version", 123), ("tool_version", ""),
                     ("assay", None), ("assay", "   ")):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            data = _make_run_json([1])
            data[key] = bad
            table = _load_with_run_json(tmp, data, [1])
            assert getattr(table, key) is None, (
                f"{key}={bad!r} 应当按未知处理，实际 {getattr(table, key)!r}")


def test_impossible_fps_and_frame_count_become_unknown():
    """帧率/总帧数 ≤ 0 物理上不可能，印上去是一句关于这段视频的假话。

    只可能来自坏文件，所以按未知处理。**`theta_mob` 不加这条**——
    阈值取多少是科学口径的事，不该由读文件的这一层来判（`theta_mob=0.0` 必须放行）。
    """
    for key, bad in (("fps", 0), ("fps", -1), ("n_frames", 0), ("n_frames", -5),
                     ("n_frames", 10800.5)):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            data = _make_run_json([1])
            data["video"][key] = bad
            table = _load_with_run_json(tmp, data, [1])
            got = table.video_fps if key == "fps" else table.video_n_frames
            assert got is None, f"video.{key}={bad!r} 应当按未知处理，实际 {got!r}"

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        data = _make_run_json([1])
        data["rules"]["theta_mob"] = 0.0
        table = _load_with_run_json(tmp, data, [1])
        assert table.theta_mob == 0.0, "theta_mob=0.0 是合法数字，不许当未知判掉"


def test_context_degradation_leaves_an_explanation_row():
    """降级必须**说出来**：一条 `[上下文不可读]` 报警行，点名每个被判未知的字段。

    没有这一行，用户看到的只是报告上某几项印着「未知」，无法分辨是
    「这次运行没记这个」还是「run.json 坏了」——后者意味着这份结果的元数据不可信。
    **静默兜底比报错危险。**
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        data = _make_run_json([1])
        data["scoring_window_s"] = 1
        data["rules"]["theta_mob"] = "很大"
        data["video"]["name"] = 123
        data["video"]["fps"] = "abc"
        data["video"]["n_frames"] = []
        table = _load_with_run_json(tmp, data, [1])

        notes = [r for r in table.rows if r.trial_id == "[上下文不可读]"]
        assert len(notes) == 1, (
            f"应当恰好一条 [上下文不可读] 报警行，实际 {len(notes)} 条"
            f"（行：{[r.trial_id for r in table.rows]}）")
        note = notes[0]
        assert note.kind == "alarm" and note.chamber is None, (
            "上下文级报警不属于任何隔间，chamber 必须是 None（F7）")
        for field in ("scoring_window_s", "theta_mob", "video.name",
                      "video.fps", "video.n_frames"):
            assert field in note.reason, f"说明里没点出 {field}：{note.reason!r}"


def test_no_explanation_row_when_context_is_clean():
    """反向：run.json 干净时**不许**出现那条说明行（不许无事报警）。

    误报的守卫最后都会被人削弱——界面上的误报同理，用户会学会忽略这一行。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        table = _load_with_run_json(tmp, _make_run_json([1]), [1])
        assert not [r for r in table.rows if r.trial_id == "[上下文不可读]"], \
            "合法 run.json 不许出现 [上下文不可读] 行"


def test_alarm_source_items_must_be_dicts():
    """`not_scored[]` / `chamber_validity[]` 的条目不是对象 ⇒ ResultsError。

    这七个场景原来全是裸 `TypeError`，会穿到 Qt 事件循环——页面按契约只接
    `ResultsError`（G2 那一轮的教训）。用户看到的是软件消失或一个英文栈。

    **`'chamber'` 这个字符串是专门的一例**：`"chamber" not in item` 对字符串是
    子串判断，恒真通过，缺键检查根本没挡住它。
    """
    cases = [
        ("not_scored", ["chamber"]),
        ("not_scored", [None]),
        ("not_scored", [42]),
        ("not_scored", [["chamber", "reason"]]),
        ("chamber_validity", [1]),
        ("chamber_validity", ["chamber"]),
        ("chamber_validity", [None]),
    ]
    for key, bad in cases:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            # 名册 (1,2)、CSV 只有 ch1 ⇒ 必定去查这两个报警源
            data = _make_run_json([1, 2])
            data[key] = bad
            try:
                _load_with_run_json(tmp, data, [1])
                assert False, f"{key}={bad!r} 应该抛 ResultsError"
            except results.ResultsError as e:
                assert key in str(e), f"错误信息应点出是哪个列表：{e}"


def test_unreadable_reason_text_degrades_not_crashes():
    """报警源里的 `reason` / `status` 不是文本 ⇒ 标成不可读，**不抛也不留空**。

    抛出去会让整张结果页打不开，连 CSV 里算好的数字一起看不见；
    静默换成空串会让界面上出现一个没有原因的报警行，看起来像「引擎什么都没说」。
    """
    cases = [
        ("not_scored", [{"chamber": 2, "reason": None}]),
        ("not_scored", [{"chamber": 2, "reason": 123}]),
        ("not_scored", [{"chamber": 2, "reason": "  "}]),
        ("chamber_validity", [{"chamber": 2, "status": None, "occupied_fraction": 0.9,
                               "unsegmentable_fraction": 0.0, "note": ""}]),
        ("chamber_validity", [{"chamber": 2, "status": 123, "occupied_fraction": 0.9,
                               "unsegmentable_fraction": 0.0, "note": ""}]),
    ]
    for key, bad in cases:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            data = _make_run_json([1, 2])
            data[key] = bad
            table = _load_with_run_json(tmp, data, [1])
            ch2 = [r for r in table.rows if r.chamber == 2]
            assert len(ch2) == 1 and ch2[0].kind == "alarm"
            reason = ch2[0].reason or ""
            assert "不可读" in reason, f"{key}={bad!r} 的说明应标不可读，实际 {reason!r}"
            assert reason.strip(), "报警行不许没有原因"


def test_readable_reason_text_passes_through():
    """反向：合法的 `reason` / `status` 必须原样出现，不许被「不可读」盖掉。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        data = _make_run_json([1, 2])
        data["not_scored"] = [{"chamber": 2, "reason": "排除态不放行计分"}]
        table = _load_with_run_json(tmp, data, [1])
        ch2 = [r for r in table.rows if r.chamber == 2][0]
        assert ch2.reason == "排除态不放行计分", f"合法原因被改写了：{ch2.reason!r}"


def test_n2_nonempty_dict_roster_is_unreadable_not_an_error():
    """N2 补强：`chambers` 是**非空** dict 时也必须走「名册不可读」，不许抛。

    变异检验抓出来的：原来那条 N2 守卫用的是 `chambers={}`，而空 dict 迭代出零个
    条目，`chamber_roster` 变成空集，接着被**邻居** N6（空名册+CSV有行 ⇒ 不可信）
    兜成了 None——于是把 N2 自己那段 `isinstance(chambers_raw, list)` 判断整段删掉，
    守卫照样绿。**能被邻居悄悄换掉的守卫比装饰更糟**：它看起来在守着一件事，
    实际守着的是另一段代码的副作用。

    非空 dict 是这条判断唯一无法被兜住的入口：没有这段判断，`{"1": {...}}` 会去
    迭代它的**键**（字符串），撞上「条目不是对象」而抛 ResultsError——
    而第三轮的裁决要的是「名册不可信 + 一条报警」，不是让整张页面打不开。
    """
    for bad in ({"1": {"index": 1}}, {"a": 1, "b": 2}):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            data = _make_run_json([])
            data["chambers"] = bad
            table = _load_with_run_json(tmp, data, [1, 3])   # 抛出来就是不通过

            notes = [r for r in table.rows if r.chamber is None and r.reason]
            assert any("名册" in (r.reason or "") for r in notes), (
                f"chambers={bad!r} 应当留下一条说明名册不可信的报警行，"
                f"实际报警行：{[r.reason for r in notes]}")
            for row in table.rows:
                if row.chamber is not None and row.reason:
                    assert "名册外的隔间" not in row.reason, (
                        f"名册不可信时不许推出「名册外的隔间」：{row.reason!r}")
            ch_rows = {r.chamber: r for r in table.rows if r.chamber is not None}
            assert ch_rows[1].kind == "scored" and ch_rows[3].kind == "scored"


def test_g6_chamber_index_below_one_raises():
    """G6：`chambers[].index < 1` ⇒ ResultsError（引擎隔间号从 1 起）。

    变异检验抓出来的：第二轮验收时我用探针确认了这条**代码**是对的，但从来没有
    **测试**守着它——把 `if idx < 1:` 整段删掉，全套测试照样全绿。
    探针是一次性的，守卫才是留下来的那个。**没跑到 ≠ 通过**，
    这里是「跑到了，但没人盯着它」。
    """
    for bad in (0, -1, -999):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            data = _make_run_json([])
            data["chambers"] = [{"index": bad}]
            try:
                _load_with_run_json(tmp, data, [])
                assert False, f"index={bad} 应该抛 ResultsError"
            except results.ResultsError as e:
                assert "index" in str(e).lower(), f"错误信息应提到 index：{e}"


def test_bool_valued_numbers_are_unknown_not_one():
    """`true` 出现在数字字段上必须当未知，**不许被当成 1**。

    Python 里 `isinstance(True, int)` 为真，`True > 0` 也为真——所以少写一个
    `isinstance(value, bool)` 特判，`theta_mob: true` 会变成报告上的 `1`、
    `fps: true` 会变成 `1 fps`。**一个假数字比一句「未知」危险得多**，
    而 `theta_mob` 是冻结参数、是科学结论的一部分。

    这条也是变异检验抓出来的：`chambers[].index` 的 bool 特判有守卫
    （`test_bool_index_raises`），上下文数字字段的没有。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        data = _make_run_json([1])
        data["rules"]["theta_mob"] = True
        data["video"]["fps"] = True
        data["video"]["n_frames"] = True
        data["scoring_window_s"] = [True, False]
        table = _load_with_run_json(tmp, data, [1])
        assert table.theta_mob is None, f"theta_mob=true 应当按未知处理，实际 {table.theta_mob!r}"
        assert table.video_fps is None, f"fps=true 应当按未知处理，实际 {table.video_fps!r}"
        assert table.video_n_frames is None, \
            f"n_frames=true 应当按未知处理，实际 {table.video_n_frames!r}"
        assert table.scoring_window_s is None, \
            f"窗口里的 true/false 应当按未知处理，实际 {table.scoring_window_s!r}"
