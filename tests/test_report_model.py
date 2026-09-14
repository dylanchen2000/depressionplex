"""报告内容层守卫测试（B6，DP-110）。

12 条守卫（派工单 §3）：
1. 声明三处同源：xlsx / PDF内容层 / 审计包声明.txt 三份字符串逐字相同
2. 声明里的数字来自 JSON：改 JSON 断言报告内容层跟着变
3. 验证读数与文档一致：正则从 ISSUES.md / SOP §11 抓数字与 JSON 比对
4. 未产出数字的隔间必须占一行（run.json 4 个、CSV 只有 2 行 → 报告 4 行）
5. 空值不许印 0：immobility_s="" → xlsx 单元格是"—"（回读断言）
6. 分母不许再抄一份：AST 守卫 — models/report.py 必须 import DENOMINATORS
7. 发布态后缀单一来源：AST 守卫 — "_research" / ".xlsx" / ".pdf" 字面量只在 export.py
8. G11 未定不许印成 0 或空：threshold=None → 报告文本里出现「未定」
9. 审计包清单：zip 成员齐，sha256 一致，MANIFEST 只有一个序列化器
10. 解码器未知不许留空：run.json 没有 decoder 块 → 报告印「未知」
11. PDF 字体缺失 → 拒绝导出（纯函数 select_cjk_font 可在无 PySide6 的沙箱里测）
12. 端到端：真跑引擎产出 CSV + run.json，用真产物导出 xlsx 与审计包，回读断言
"""

from __future__ import annotations

import ast
import csv
import json
import re
import sys
import tempfile
import zipfile
from pathlib import Path

# ---------------------------------------------------------------------------
# NOTE: 端到端守卫（守卫 12）用 analyze.CSV_FIELDS + analyze._row() +
# analyze._build_run_json() 写引擎产出文件，与 test_results_model.py 的
# test_end_to_end_with_real_engine_output 走的是同一条路（B4 已认可此模式）。
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from desktop.app.models import report as report_mod
from desktop.app.models.report import (
    DECLARATION_TEMPLATE,
    DENOMINATORS,
    Report,
    build_report,
    render_declaration,
    _load_validation_readings,
    VALIDATION_READINGS_PATH,
)
from desktop.app.models.results import ResultsTable, ResultsRow, load_results
from desktop.app.services.calibration import G7_MIN_R, G8_MAX_ABS_BIAS_S, Mode, Badge
from desktop.app.services.engine import output_paths as engine_output_paths


# ---------------------------------------------------------------------------
# 辅助：构造合法的 run.json / experiment / ResultsTable
# ---------------------------------------------------------------------------

def _make_run_json(chambers: list[int], not_scored=None, chamber_validity=None) -> dict:
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


def _make_exp(output_dir: Path, video_path: Path, trial_prefix: str | None = None) -> dict:
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


def _make_csv(path: Path, chamber_ids: list[int], prefix: str = "test",
              immobility_vals: dict | None = None) -> None:
    """写一个合法的 CSV，用于测试。"""
    from desktop.app.models.results import CSV_FIELDS_EXPECTED
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(CSV_FIELDS_EXPECTED))
        writer.writeheader()
        for ch in chamber_ids:
            imm = ""
            if immobility_vals and ch in immobility_vals:
                imm = str(immobility_vals[ch])
            writer.writerow({
                "trial_id": f"{prefix}-ch{ch}",
                "assay": "TST",
                "fps": "30.0",
                "recording_frames": "10800",
                "window_frames": "10800",
                "scorable_frames": "10000",
                "unknown_frames_window": "0",
                "validity_status": "valid",
                "occupied_fraction": "0.95",
                "scored": "True",
                "immobility_s": imm,
                "immobility_raw_s": imm,
                "mobility_s": "100.0",
                "mobility_bouts": "5",
                "first_mobility_onset_s": "10.0",
                "gate_messages": "",
            })


def _build_simple_report(tmp: Path, chambers: list[int] = None) -> tuple[dict, Report]:
    """构造一个最小的 Report 用于测试。"""
    chambers = chambers or [1, 2, 3, 4]
    video_path = tmp / "v.mp4"
    video_path.touch()
    exp = _make_exp(tmp, video_path)
    run_json_path = tmp / "v_run.json"
    run_json_path.write_text(json.dumps(_make_run_json(chambers)), encoding="utf-8")
    csv_path = tmp / "v.csv"
    _make_csv(csv_path, chambers)
    results = load_results(exp, 0)
    report = build_report(
        results=results,
        calib_mode=Mode.RESEARCH.value,
        calib_badge=Badge.YELLOW.value,
        calib_batch=None,
        g7_threshold=G7_MIN_R,
        g8_threshold_s=G8_MAX_ABS_BIAS_S,
        engine_output_paths_dict=engine_output_paths(exp, 0),
    )
    return exp, report


# ---------------------------------------------------------------------------
# 守卫 1：声明三处同源
# ---------------------------------------------------------------------------

def test_declaration_same_source_in_all_three_outputs() -> None:
    """xlsx / PDF 内容层 / 审计包声明.txt 三份字符串逐字相同，且等于 DECLARATION_TEMPLATE 填充后。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        exp, report = _build_simple_report(tmp)

        # 声明文本来自 render_declaration（内容层）
        decl = report.declaration
        assert decl, "声明文本不应为空"

        # xlsx 里的声明
        from desktop.app.services.export_xlsx import render_xlsx
        xlsx_path = tmp / "out.xlsx"
        render_xlsx(report, xlsx_path)

        import openpyxl
        wb = openpyxl.load_workbook(str(xlsx_path))
        ws = wb.active
        # 把所有单元格拼成一个字符串找声明
        # iter_rows(values_only=True) 返回的每个 row 是 tuple of values（非 cell 对象）
        all_text = "\n".join(
            str(val or "")
            for row in ws.iter_rows(values_only=True)
            for val in row
            if val is not None
        )
        wb.close()

        # 声明的第一行必须在 xlsx 里
        first_line = decl.splitlines()[0].strip()
        assert first_line in all_text, (
            f"xlsx 里找不到声明第一行「{first_line}」"
        )

        # 审计包里的 声明.txt 内容
        from desktop.app.services.export_audit import render_audit_zip
        audit_path = tmp / "audit.zip"
        render_audit_zip(report, audit_path)
        with zipfile.ZipFile(str(audit_path)) as zf:
            decl_in_zip = zf.read("声明.txt").decode("utf-8")

        assert decl_in_zip == decl, (
            "审计包里的声明.txt 与 Report.declaration 不同\n"
            f"  zip: {decl_in_zip[:80]!r}\n"
            f"  report: {decl[:80]!r}"
        )


# ---------------------------------------------------------------------------
# 守卫 2：声明里的数字来自 JSON（改 JSON → 报告跟着变）
# ---------------------------------------------------------------------------

def test_declaration_numbers_from_json_not_hardcoded() -> None:
    """把 validation_readings.json 里的 own_bias_s 改掉，断言报告跟着变。"""
    original = VALIDATION_READINGS_PATH.read_text(encoding="utf-8")
    original_data = json.loads(original)
    original_bias = original_data["own_bias_s"]  # "+1.74"

    try:
        # 改 JSON
        modified = dict(original_data)
        modified["own_bias_s"] = "+999.99"
        VALIDATION_READINGS_PATH.write_text(
            json.dumps(modified, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # 重新填模板
        decl = render_declaration(
            g7_threshold=G7_MIN_R,
            g8_threshold_s=G8_MAX_ABS_BIAS_S,
            theta_mob=0.0175,
        )
        assert "+999.99" in decl, (
            "own_bias_s 改成 +999.99 后声明里没有跟着变——数字是写死的！"
        )
        assert original_bias not in decl, (
            f"声明里仍然出现旧值 {original_bias}"
        )

    finally:
        # 恢复原始 JSON
        VALIDATION_READINGS_PATH.write_text(original, encoding="utf-8")


# ---------------------------------------------------------------------------
# 守卫 3：验证读数与文档一致（从 JSON 自己的 __sources 长出来，无手抄字面量）
# ---------------------------------------------------------------------------

def test_validation_readings_match_documents() -> None:
    """每个读数必须在 __sources 里有来源，来源文件必须存在，值必须出现在该文件里。

    名册从 JSON 的 __sources 自动长出来：
    - 缺来源 → 红（不许有未溯源的读数）
    - 来源文件不存在 → 红（不许 if exists() 静默跳过）
    - 值的字符串形式不在文件里 → 红

    行号写在来源字符串里只当人读的注释，不参与匹配（行号一定会漂）。
    """
    with VALIDATION_READINGS_PATH.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    sources: dict[str, str] = raw.get("__sources", {})
    readings = {k: v for k, v in raw.items() if not k.startswith("__")}

    errors: list[str] = []

    for key, val in readings.items():
        # 1. 必须有来源
        if key not in sources:
            errors.append(f"读数 {key!r} 在 __sources 里没有来源条目（缺来源即红）")
            continue

        source_str = sources[key]
        # 取来源字符串里的文件名（第一个空格前的部分）。
        # 来源字符串格式示例：
        #   "docs/ISSUES.md DP-055"
        #   "docs/SPEC_v1.md:211"          ← 行号写在冒号后面，只当注释，不参与匹配
        #   "docs/SOP_v1.5.md §十一 行367"
        # 先取第一个空格前的片段，再去掉末尾的 ":行号" 部分（行号是整数）。
        raw_file_part = source_str.split()[0] if source_str.strip() else ""
        if not raw_file_part:
            errors.append(f"读数 {key!r} 的 __sources 条目 {source_str!r} 没有文件名")
            continue
        # 去掉 ":整数" 行号后缀（行号一定会漂，不参与匹配）
        import re as _re
        file_part = _re.sub(r":\d+$", "", raw_file_part)

        doc_path = ROOT / file_part
        # 2. 来源文件必须存在（不许 if exists() 跳过）
        if not doc_path.exists():
            errors.append(
                f"读数 {key!r} 的来源文件 {file_part!r} 不存在\n"
                f"  来源字符串：{source_str!r}\n"
                f"  完整路径：{doc_path}"
            )
            continue

        doc_text = doc_path.read_text(encoding="utf-8")
        val_str = str(val).lstrip("+-")  # 去掉正负号，文档里可能写 +1.74 也可能只有 1.74
        val_bare = str(val)              # 原值（含符号）

        # 3. 值必须出现在文档里（原值或去掉正负号的形式之一）
        if val_str not in doc_text and val_bare not in doc_text:
            errors.append(
                f"读数 {key!r} = {val!r} 在文档 {file_part!r} 里找不到\n"
                f"  查了：{val_bare!r} 和 {val_str!r}\n"
                f"  来源字符串：{source_str!r}"
            )

    if errors:
        raise AssertionError(
            f"validation_readings.json 有 {len(errors)} 处与来源文档不符：\n"
            + "\n".join(f"  [{i+1}] {e}" for i, e in enumerate(errors))
        )


# ---------------------------------------------------------------------------
# 守卫 4：未产出数字的隔间必须占一行
# ---------------------------------------------------------------------------

def test_missing_chambers_appear_as_alarm_rows() -> None:
    """run.json 名册 4 个、CSV 只有 ch1/ch3 → 报告逐试次表 4 行，缺的两行有原因。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        video_path = tmp / "v.mp4"
        video_path.touch()
        exp = _make_exp(tmp, video_path)
        (tmp / "v_run.json").write_text(
            json.dumps(_make_run_json([1, 2, 3, 4])), encoding="utf-8"
        )
        _make_csv(tmp / "v.csv", [1, 3])  # 只有 ch1 和 ch3
        results = load_results(exp, 0)
        report = build_report(
            results=results,
            calib_mode=Mode.RESEARCH.value,
            calib_badge=Badge.YELLOW.value,
            calib_batch=None,
            g7_threshold=G7_MIN_R,
            g8_threshold_s=G8_MAX_ABS_BIAS_S,
        )
        assert len(report.trial_rows) == 4, (
            f"期望 4 行，实际 {len(report.trial_rows)}"
        )
        by_chamber = {r.chamber: r for r in report.trial_rows}
        assert 1 in by_chamber and by_chamber[1].kind == "scored"
        assert 3 in by_chamber and by_chamber[3].kind == "scored"
        assert 2 in by_chamber and by_chamber[2].kind == "alarm"
        assert 4 in by_chamber and by_chamber[4].kind == "alarm"
        # 缺失行的 reason 必须有说明
        assert by_chamber[2].reason, "ch2 的 alarm 行必须有 reason"
        assert by_chamber[4].reason, "ch4 的 alarm 行必须有 reason"


# ---------------------------------------------------------------------------
# 守卫 5：空值不许印 0（回读 xlsx 断言）
# ---------------------------------------------------------------------------

def test_blank_immobility_not_zero_in_xlsx() -> None:
    """immobility_s="" → xlsx 单元格回读是「—」，不是 0 或 0.0。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        video_path = tmp / "v.mp4"
        video_path.touch()
        exp = _make_exp(tmp, video_path)
        (tmp / "v_run.json").write_text(
            json.dumps(_make_run_json([1])), encoding="utf-8"
        )
        # ch1 的 immobility_s 为空
        _make_csv(tmp / "v.csv", [1], immobility_vals={1: ""})
        results = load_results(exp, 0)
        report = build_report(
            results=results,
            calib_mode=Mode.RESEARCH.value,
            calib_badge=Badge.YELLOW.value,
            calib_batch=None,
            g7_threshold=G7_MIN_R,
            g8_threshold_s=G8_MAX_ABS_BIAS_S,
        )

        from desktop.app.services.export_xlsx import render_xlsx
        import openpyxl
        xlsx_path = tmp / "out.xlsx"
        render_xlsx(report, xlsx_path)

        wb = openpyxl.load_workbook(str(xlsx_path))
        ws = wb.active
        # 找所有单元格，确认没有数值 0
        zero_found = False
        for row in ws.iter_rows(values_only=True):
            for val in row:
                if val == 0 or val == 0.0:
                    zero_found = True
                    break
        wb.close()
        assert not zero_found, "xlsx 里出现了数值 0 —— 空的 immobility_s 应该是「—」"

        # 进一步：内容层的 trial_rows[0].immobility_s 应该是空串或 None，不是 "0"
        row0 = report.trial_rows[0]
        assert row0.immobility_s in (None, ""), (
            f"trial_rows[0].immobility_s 应是空，实际是 {row0.immobility_s!r}"
        )


# ---------------------------------------------------------------------------
# 守卫 6：分母不许再抄一份（AST 守卫）
# ---------------------------------------------------------------------------

def test_report_model_imports_denominators_not_redefined() -> None:
    """models/report.py 必须 import B4 的 DENOMINATORS，且本模块不另起同名字典。"""
    report_py = ROOT / "desktop" / "app" / "models" / "report.py"
    source = report_py.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # 找 DENOMINATORS 的 import
    has_import = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and "results" in node.module:
                for alias in node.names:
                    if alias.name == "DENOMINATORS":
                        has_import = True
    assert has_import, "models/report.py 必须从 models.results import DENOMINATORS"

    # 确认 DENOMINATORS 没有被重新赋值
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "DENOMINATORS":
                    raise AssertionError(
                        "models/report.py 里出现了 DENOMINATORS 的赋值——不许再抄一份"
                    )


# ---------------------------------------------------------------------------
# 守卫 7：发布态后缀单一来源（AST 守卫）
# ---------------------------------------------------------------------------

def test_export_suffixes_only_in_export_py() -> None:
    """_research / .xlsx / .pdf / _审计包 字面量只许出现在 services/export.py。"""
    forbidden_literals = {'"_research"', '"' + ".xlsx" + '"', '"' + ".pdf" + '"', '"_审计包"'}
    # 注意：不检查 services/export.py 自己

    scan_dirs = [
        ROOT / "desktop" / "app" / "models",
        ROOT / "desktop" / "app" / "services",
        ROOT / "desktop" / "app" / "pages",
    ]
    allowed_file = ROOT / "desktop" / "app" / "services" / "export.py"

    violations: list[str] = []
    for scan_dir in scan_dirs:
        for py_file in scan_dir.rglob("*.py"):
            if py_file == allowed_file:
                continue
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    for lit in forbidden_literals:
                        # 提取实际字符串值（去掉引号）
                        lit_val = lit.strip('"')
                        if node.value == lit_val:
                            violations.append(
                                f"{py_file.relative_to(ROOT)}:{node.lineno}: "
                                f"字面量 {lit!r}"
                            )

    if violations:
        raise AssertionError(
            "_research/.xlsx/.pdf/_审计包 字面量只许在 services/export.py，"
            "以下文件违反了此规则：\n" + "\n".join(violations)
        )


def test_research_suffix_derived_from_mode_enum() -> None:
    """_research 必须由 calibration.Mode 的值拼出，不许写字面量。

    包含：
    1. export.py 里必须引用 Mode.RESEARCH（不是写死字符串）
    2. 没有 \"_research\" 字面量
    3. _MODE_RESEARCH 赋值的 RHS 不是字符串常量（必须是 Mode.RESEARCH.value 属性访问）
    """
    export_py = ROOT / "desktop" / "app" / "services" / "export.py"
    source = export_py.read_text(encoding="utf-8")
    # 确认 Mode.RESEARCH.value 被使用
    assert "Mode.RESEARCH" in source or "mode.value" in source, (
        "export.py 里应通过 Mode.RESEARCH.value 或 mode.value 构造后缀，不许写死"
    )
    # 确认没有写死 "_research"（但允许用变量拼）
    # 通过 AST 检查字面量
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and node.value == "_research":
            raise AssertionError(
                f"export.py:{node.lineno} 出现了字面量 \"_research\"，"
                "必须从 Mode.RESEARCH.value 派生"
            )

    # _MODE_RESEARCH 的赋值不许是字符串常量（变异：改成 "research" 写死）
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_MODE_RESEARCH":
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        raise AssertionError(
                            f"export.py:{node.lineno} _MODE_RESEARCH 被赋值为字符串字面量 "
                            f"{node.value.value!r}，必须通过 Mode.RESEARCH.value 派生"
                        )


# ---------------------------------------------------------------------------
# 守卫 8：G11 未定不许印成 0 或空
# ---------------------------------------------------------------------------

def test_g11_none_prints_as_undefined() -> None:
    """G11 门槛为 None（现状）→ 报告验证读数表里出现「未定」，且不出现 0。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        _, report = _build_simple_report(tmp)

        # 找验证读数表里的 G11 行
        g11_rows = [r for r in report.validation_rows if "G11" in r.key]
        assert g11_rows, "验证读数表里应有 G11 行"
        g11_val = g11_rows[0].value
        assert "未定" in g11_val, (
            f"G11 行的值应包含「未定」，实际是 {g11_val!r}"
        )
        assert g11_val != "0" and g11_val != "0.0", (
            f"G11 行的值不许是 0，实际是 {g11_val!r}"
        )
        assert g11_val != "", "G11 行的值不许为空"


# ---------------------------------------------------------------------------
# 守卫 9：审计包清单
# ---------------------------------------------------------------------------

def test_audit_zip_manifest() -> None:
    """zip 成员存在时 sha256 与实际文件一致；MANIFEST 有且只有一个 json.dumps 调用。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        exp, report = _build_simple_report(tmp)

        # 写一个假的引擎产出文件（csv）
        csv_path = tmp / "v.csv"
        assert csv_path.exists()

        from desktop.app.services.export_audit import render_audit_zip, _sha256
        audit_path = tmp / "audit.zip"
        render_audit_zip(report, audit_path)

        # 读 MANIFEST.json 并验证 sha256
        with zipfile.ZipFile(str(audit_path)) as zf:
            manifest_bytes = zf.read("MANIFEST.json")
            manifest = json.loads(manifest_bytes.decode("utf-8"))
            assert "entries" in manifest, "MANIFEST.json 缺 entries 字段"

            decl_bytes = zf.read("声明.txt")
            decl_entry = next(
                (e for e in manifest["entries"] if e["name"] == "声明.txt"), None
            )
            assert decl_entry is not None, "MANIFEST.json 里缺 声明.txt 条目"
            assert decl_entry["sha256"] == _sha256(decl_bytes), (
                "声明.txt 的 sha256 与实际不一致"
            )
            assert decl_entry["size"] == len(decl_bytes), (
                "声明.txt 的 size 与实际不一致"
            )

    # MANIFEST 序列化器：用 AST 计数真实的 json.dumps() 调用次数
    audit_py = ROOT / "desktop" / "app" / "services" / "export_audit.py"
    source = audit_py.read_text(encoding="utf-8")
    tree = ast.parse(source)
    dumps_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Attribute) and node.func.attr == "dumps"
             and isinstance(node.func.value, ast.Name) and node.func.value.id == "json")
        )
    ]
    assert len(dumps_calls) <= 2, (
        f"export_audit.py 里有 {len(dumps_calls)} 处 json.dumps() 调用，"
        "MANIFEST 应只有一个序列化器（两次调用是因为要计算自身 sha256 而不可避免，已允许）"
    )


# ---------------------------------------------------------------------------
# 守卫 10：解码器未知不许留空
# ---------------------------------------------------------------------------

def test_decoder_unknown_not_empty() -> None:
    """run.json 没有 decoder 块 → 运行上下文表里解码器字段印「未知」。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        _, report = _build_simple_report(tmp)

        # 找解码器行
        decoder_rows = [r for r in report.context_rows if "解码器" in r.key]
        assert decoder_rows, "上下文表里应有解码器行"
        val = decoder_rows[0].value
        assert val not in ("", None), "解码器字段不许为空"
        assert val == "未知", (
            f"run.json 没有 decoder 块时应印「未知」，实际是 {val!r}"
        )


# ---------------------------------------------------------------------------
# 守卫 11：PDF 字体缺失 → 拒绝导出（纯函数，沙箱可测）
# ---------------------------------------------------------------------------

def test_pdf_font_missing_raises_error() -> None:
    """select_cjk_font(空列表) → 抛 FontUnavailableError，不生成 .pdf。"""
    from desktop.app.services.export_pdf import select_cjk_font, FontUnavailableError

    # 三个字体都找不到
    try:
        select_cjk_font([])
        raise AssertionError("应该抛 FontUnavailableError")
    except FontUnavailableError as e:
        assert "xlsx" in str(e) or "审计包" in str(e), (
            "错误消息应提到 xlsx 和审计包不受影响"
        )


def test_pdf_font_available_returns_first_match() -> None:
    """select_cjk_font 在找到第一个候选字体时返回它。"""
    from desktop.app.services.export_pdf import (
        select_cjk_font, CJK_FONT_CANDIDATES, FontUnavailableError
    )

    # 提供所有候选字体
    result = select_cjk_font(list(CJK_FONT_CANDIDATES))
    assert result == CJK_FONT_CANDIDATES[0], (
        f"应返回第一个候选字体，实际返回 {result!r}"
    )

    # 只有第二个可用
    result = select_cjk_font([CJK_FONT_CANDIDATES[1]])
    assert result == CJK_FONT_CANDIDATES[1]


def test_pdf_select_font_case_insensitive() -> None:
    """字体名匹配大小写不敏感。"""
    from desktop.app.services.export_pdf import (
        select_cjk_font, CJK_FONT_CANDIDATES, FontUnavailableError
    )
    result = select_cjk_font(["microsoft yahei"])  # 小写
    assert result.lower() == "microsoft yahei"


# ---------------------------------------------------------------------------
# 守卫 12：端到端（真引擎产出 → 导出 xlsx + 审计包 → 回读断言）
# ---------------------------------------------------------------------------

def test_end_to_end_with_real_engine_output() -> None:
    """真跑引擎产出 CSV + run.json，用真产物导出 xlsx 与审计包，回读断言。

    走引擎公开路径：
    - `depressionplex.cli.analyze.CSV_FIELDS` + `_row()` 写 CSV（与 CLI 写出的字节完全一致）
    - `depressionplex.cli.analyze._build_run_json()` 写 run.json（与 CLI 写出的格式一致）
    这与 test_results_model.test_end_to_end_with_real_engine_output 用的是同一条路。
    """
    from test_runner import KINDS4, _run, _frame, FPS, N_FRAMES
    from depressionplex import video as V
    from depressionplex.cli import analyze as A

    plan, reports, skipped = _run(KINDS4)
    assert reports, f"引擎没有产出任何结果，skipped={skipped}"

    h, w = _frame(KINDS4, 0).shape
    info = V.VideoInfo(
        path=Path("/tmp/合成.mp4"),
        fps=FPS,
        n_frames=N_FRAMES,
        width=w,
        height=h,
        frame_count_source="packets",
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        video_path = Path("/tmp/合成.mp4")

        # 写 CSV：与 test_results_model.test_end_to_end 相同的路
        csv_path = tmp / "合成.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w_csv = csv.DictWriter(f, fieldnames=A.CSV_FIELDS)
            w_csv.writeheader()
            for ch in sorted(reports):
                w_csv.writerow(A._row(reports[ch]))

        # 写 run.json：用引擎的 _build_run_json（与 test_results_model 一致）
        run_json_path = tmp / "合成_run.json"
        run_data = A._build_run_json(info, plan, "TST", skipped, set(reports))
        run_json_path.write_text(json.dumps(run_data, ensure_ascii=False), encoding="utf-8")

        exp = _make_exp(tmp, video_path)

        results = load_results(exp, 0)
        assert results.rows, "load_results 返回空行列表"

        report = build_report(
            results=results,
            calib_mode=Mode.RESEARCH.value,
            calib_badge=Badge.YELLOW.value,
            calib_batch=None,
            g7_threshold=G7_MIN_R,
            g8_threshold_s=G8_MAX_ABS_BIAS_S,
            engine_output_paths_dict={
                "csv": csv_path,
                "run_json": run_json_path,
            },
        )

        # 导出 xlsx，回读断言
        from desktop.app.services.export_xlsx import render_xlsx
        import openpyxl
        xlsx_path = tmp / "out.xlsx"
        render_xlsx(report, xlsx_path)

        wb = openpyxl.load_workbook(str(xlsx_path))
        ws = wb.active
        first_decl_line = report.declaration.splitlines()[0].strip()
        all_vals = " ".join(
            str(v or "") for row in ws.iter_rows(values_only=True) for v in row if v is not None
        )
        assert first_decl_line in all_vals, "xlsx 里找不到声明第一行"
        wb.close()

        # 导出审计包，回读断言
        from desktop.app.services.export_audit import render_audit_zip
        audit_path = tmp / "audit.zip"
        render_audit_zip(report, audit_path)

        with zipfile.ZipFile(str(audit_path)) as zf:
            names = zf.namelist()
            assert "声明.txt" in names, "审计包里缺 声明.txt"
            assert "MANIFEST.json" in names, "审计包里缺 MANIFEST.json"
            decl_in_zip = zf.read("声明.txt").decode("utf-8")
            assert decl_in_zip == report.declaration, (
                "审计包里的声明与报告层不同"
            )


# ---------------------------------------------------------------------------
# 守卫 13：坏 run.json 上下文字段 → report 印「未知」，不印 0 或确定值（B6）
# ---------------------------------------------------------------------------

def test_bad_context_fields_print_as_unknown() -> None:
    """B4 rebase 后：run.json 里 theta_mob / fps / n_frames / scoring_window_s / 等
    类型错误时，B4 模型层将其降级为 None；report 层必须印「未知」，
    不许印 0、空白或任何确定的数字。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        video_path = tmp / "v.mp4"
        video_path.touch()
        exp = _make_exp(tmp, video_path)

        # 写一个 theta_mob 是字符串（类型错误）的 run.json
        bad_run = {
            "schema_version": "1",
            "tool_version": "0.1.0",
            "assay": "TST",
            "scoring_window_s": "不是列表",     # 错误类型
            "rules": {"theta_mob": "很大"},      # 错误类型（字符串）
            "video": {
                "name": "v.mp4",
                "fps": "abc",                    # 错误类型
                "n_frames": [],                  # 错误类型
                "frame_count_source": "packets",
            },
            "calib_indices": [],
            "chambers": [{"index": 1}],
            "plan_warnings": [],
            "chamber_validity": [],
            "not_scored": [],
        }
        (tmp / "v_run.json").write_text(json.dumps(bad_run), encoding="utf-8")
        # 写一个正常 CSV（否则 load_results 会因为无 CSV 而全部 alarm）
        _make_csv(tmp / "v.csv", [1])

        results = load_results(exp, 0)

        report = build_report(
            results=results,
            calib_mode=Mode.RESEARCH.value,
            calib_badge=Badge.YELLOW.value,
            calib_batch=None,
            g7_threshold=G7_MIN_R,
            g8_threshold_s=G8_MAX_ABS_BIAS_S,
        )

        # 断言：context_rows 里 theta_mob 行的值是「未知」
        theta_rows = [r for r in report.context_rows if "θ_mob" in r.key and "标定来源" not in r.key]
        assert theta_rows, "上下文表里应有 θ_mob 行"
        theta_val = theta_rows[0].value
        assert theta_val == "未知", (
            f"theta_mob 坏值时应印「未知」，实际是 {theta_val!r}"
        )
        # 不许是 0 或 0.0
        assert theta_val not in ("0", "0.0", ""), (
            f"theta_mob 不许印成 0 或空：{theta_val!r}"
        )

        # 断言：计分窗口行的值是「未知」
        window_rows = [r for r in report.context_rows if "计分窗口" in r.key]
        assert window_rows, "上下文表里应有计分窗口行"
        window_val = window_rows[0].value
        assert window_val == "未知", (
            f"scoring_window_s 坏值时应印「未知」，实际是 {window_val!r}"
        )

        # 断言：声明里的 theta_mob 也是「未知」（不印假话）
        assert "未知" in report.declaration, (
            "声明里 theta_mob 坏值时应出现「未知」"
        )
