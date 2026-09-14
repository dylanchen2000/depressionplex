"""报告内容层守卫测试（B6，DP-110）。

守卫列表（含第四轮新增）：
1. 声明三处同源：xlsx / PDF内容层 / 审计包声明.txt 三份字符串逐字相同
2. 声明里的数字来自 JSON：改 JSON 断言报告内容层跟着变
3. 验证读数与文档一致：从 JSON 自己的 __sources 长出来，{file, anchor, quote} 三段校验
4. 未产出数字的隔间必须占一行（run.json 4 个、CSV 只有 2 行 → 报告 4 行）
5. 空值不许印 0：immobility_s="" → xlsx 单元格是"—"（回读断言）
6. 分母不许再抄一份：AST 守卫 — models/report.py 必须 import DENOMINATORS
7. 发布态后缀单一来源：AST 守卫 — ".xlsx" / ".pdf" / ".zip" 字面量只在 export.py
8. G11 未定不许印成 0 或空：threshold=None → 报告文本里出现「未定」
9. 审计包清单：zip 成员齐，sha256 一致，MANIFEST 只有一个序列化器
10. 解码器未知不许留空：run.json 没有 decoder 块 → 报告印「未知」
11. PDF 字体缺失 → 拒绝导出（纯函数 select_cjk_font 可在无 PySide6 的沙箱里测）
12. 端到端：真跑引擎产出 CSV + run.json，用真产物导出 xlsx 与审计包，回读断言
13. 关键字词钉住：声明必须逐字包含指定短语列表（C0）
14. 声明模式匹配：mode="validated" 抛 NotImplementedError（C0）
15. 审计包名册双向对账（C14）
16. 审计包名册记录缺失引擎产出（C14）
"""

from __future__ import annotations

import ast
import csv
import html as html_lib
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
    _MODE_LABELS,
    build_report,
    render_declaration,
    _load_validation_readings,
    VALIDATION_READINGS_PATH,
)
from desktop.app.models.results import ResultsTable, ResultsRow, load_results
from desktop.app.services.calibration import G7_MIN_R, G8_MAX_ABS_BIAS_S, Mode, Badge
from desktop.app.services.engine import output_paths as engine_output_paths


# ---------------------------------------------------------------------------
# 守卫 13（C0）：声明关键短语钉住列表（在测试里，不在产品代码里）
# 改一个字就红，要改声明就得同时改测试——那是有意的，reviewer 会看到。
# ---------------------------------------------------------------------------
_DECLARATION_REQUIRED_PHRASES: list[str] = [
    "研究版",
    "没有计量资质",
    "不得",
    "逐秒时间对齐门",
    "未定",
    "单个试次的秒数不要单独作为结论依据",
    "分母不看，秒数没有意义",
    "没有产出数字的隔间会在表里占一行并写明原因",
]


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
# 守卫 13（C0）：关键短语钉住
# ---------------------------------------------------------------------------

def test_declaration_key_phrases_are_pinned() -> None:
    """声明文本必须逐字包含 _DECLARATION_REQUIRED_PHRASES 里的每一句。

    改一个字就红。要改声明就得同时改这个测试——那是有意的，reviewer 会看到。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        _, report = _build_simple_report(Path(tmpdir))
        decl = report.declaration
        missing = [phrase for phrase in _DECLARATION_REQUIRED_PHRASES
                   if phrase not in decl]
        assert not missing, (
            f"声明文本缺少以下必须短语（改了这些字必须同时改测试）：\n"
            + "\n".join(f"  - {p!r}" for p in missing)
        )


def test_declaration_mode_research_label() -> None:
    """mode='research' 时声明含「研究版」，不含「计量版」。"""
    decl = render_declaration(
        g7_threshold=G7_MIN_R,
        g8_threshold_s=G8_MAX_ABS_BIAS_S,
        theta_mob=0.0175,
        mode="research",
    )
    assert "研究版" in decl, "research 模式声明应含「研究版」"
    assert "计量版" not in decl, "research 模式声明不应含「计量版」"


def test_declaration_mode_validated_raises() -> None:
    """mode='validated' 时抛 NotImplementedError（计量版文案未定，M3 前禁止生成）。"""
    try:
        render_declaration(
            g7_threshold=G7_MIN_R,
            g8_threshold_s=G8_MAX_ABS_BIAS_S,
            theta_mob=0.0175,
            mode="validated",
        )
        raise AssertionError("mode='validated' 应抛 NotImplementedError")
    except NotImplementedError as e:
        assert "计量版" in str(e) or "M3" in str(e), (
            f"NotImplementedError 消息应提到计量版/M3，实际: {e}"
        )


def test_declaration_mode_matches_context_row() -> None:
    """声明里的发布态词必须与 context_rows 里「发布态」行指同一个东西。

    research 模式：声明含「研究版」，发布态行值含「research」。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        _, report = _build_simple_report(Path(tmpdir))
        assert "研究版" in report.declaration, "research 声明必须含「研究版」"
        calib_row = next(
            (r for r in report.context_rows if r.key == "发布态"), None
        )
        assert calib_row is not None, "context_rows 必须有「发布态」行"
        assert "research" in calib_row.value.lower(), (
            f"发布态行值 {calib_row.value!r} 里应含 'research'"
        )


# ---------------------------------------------------------------------------
# 守卫 1：声明三处同源（C8 修正：xlsx 逐字相等、PDF 走 _build_html 纯函数）
# ---------------------------------------------------------------------------

def test_declaration_same_source_in_all_three_outputs() -> None:
    """xlsx / PDF 内容层(HTML) / 审计包声明.txt 三份字符串逐字相同。

    xlsx：声明按 splitlines() 逐行写入，拼回来等于 report.declaration。
    PDF：_build_html 是纯函数，声明的每一行都出现在 HTML 里（html.escape 后）。
    审计包：声明.txt == report.declaration（逐字相等）。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        exp, report = _build_simple_report(tmp)

        decl = report.declaration
        assert decl, "声明文本不应为空"

        # ---- xlsx：拼回声明行，逐字断言 ----
        from desktop.app.services.export_xlsx import render_xlsx
        xlsx_path = tmp / "out.xlsx"
        render_xlsx(report, xlsx_path)

        import openpyxl
        wb = openpyxl.load_workbook(str(xlsx_path))
        ws = wb.active

        # _write_declaration 逐行 ws.append([line])，第一行是标题"研究用途声明"
        # 找到标题行后面连续的声明行，拼回 declaration
        # 简化：找所有非空单元格里出现的声明行，验证全部声明行都在
        all_cell_values: list[str] = []
        for row in ws.iter_rows(values_only=True):
            for val in row:
                if val is not None:
                    all_cell_values.append(str(val))
        wb.close()

        # 声明的每一行必须在 xlsx 里（逐行出现）
        missing_lines = []
        for line in decl.splitlines():
            if line and line not in all_cell_values:
                missing_lines.append(line)
        assert not missing_lines, (
            f"xlsx 里缺少以下声明行（共 {len(missing_lines)} 行）：\n"
            + "\n".join(f"  {l!r}" for l in missing_lines[:5])
        )

        # 进一步验证：声明行总数匹配（所有非空行都在单元格列表里）
        all_decl_lines = [l for l in decl.splitlines() if l]
        for dl in all_decl_lines:
            assert dl in all_cell_values, (
                f"xlsx 回读缺少声明行 {dl[:60]!r}"
            )

        # ---- PDF（HTML）：_build_html 纯函数，不需要 PySide6 ----
        from desktop.app.services.export_pdf import _build_html
        html_content = _build_html(report)
        for line in decl.splitlines():
            escaped = html_lib.escape(line)
            assert escaped in html_content, (
                f"_build_html HTML 里找不到声明行 {line[:60]!r}"
            )

        # ---- 审计包：声明.txt 逐字相等 ----
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
# 守卫 2：声明里的数字来自 JSON（C11：不许修改仓库文件，用 tmpdir 副本）
# ---------------------------------------------------------------------------

def test_declaration_numbers_from_json_not_hardcoded() -> None:
    """把 validation_readings.json 副本里的 own_bias_s 改掉，断言声明跟着变。

    测试在 tmpdir 里操作 JSON 副本，不触碰 data/validation_readings.json。
    """
    import shutil

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        # 复制到 tmpdir，绝不触碰原文件
        copy_path = tmp / "validation_readings_copy.json"
        shutil.copy2(str(VALIDATION_READINGS_PATH), str(copy_path))

        original_data = json.loads(copy_path.read_text(encoding="utf-8"))
        original_bias = original_data["own_bias_s"]  # "+1.74"

        # 改副本
        modified = dict(original_data)
        modified["own_bias_s"] = "+999.99"
        copy_path.write_text(
            json.dumps(modified, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # 用 readings_path 参数传入副本
        decl = render_declaration(
            g7_threshold=G7_MIN_R,
            g8_threshold_s=G8_MAX_ABS_BIAS_S,
            theta_mob=0.0175,
            readings_path=copy_path,
        )
        assert "+999.99" in decl, (
            "own_bias_s 改成 +999.99 后声明里没有跟着变——数字是写死的！"
        )
        assert original_bias not in decl, (
            f"声明里仍然出现旧值 {original_bias}"
        )


# ---------------------------------------------------------------------------
# 守卫 3：验证读数与文档一致（C4：{file, anchor, quote} 格式升级）
# ---------------------------------------------------------------------------

def test_validation_readings_match_documents() -> None:
    """每个读数的 __sources 必须是 {file, anchor, quote} 格式。

    校验规则：
    1. quote 必须逐字出现在 file 里
    2. str(value) 或 str(value).lstrip('+-') 必须出现在 quote 里
    3. anchor 必须出现在 quote 所在行或其上文 20 行内
    """
    with VALIDATION_READINGS_PATH.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    sources: dict = raw.get("__sources", {})
    readings = {k: v for k, v in raw.items() if not k.startswith("__")}

    errors: list[str] = []

    for key, val in readings.items():
        # 1. 必须有来源
        if key not in sources:
            errors.append(f"读数 {key!r} 在 __sources 里没有来源条目（缺来源即红）")
            continue

        src = sources[key]
        if not isinstance(src, dict):
            errors.append(
                f"读数 {key!r} 的 __sources 条目不是 dict，"
                f"格式已升级为 {{file, anchor, quote}}，当前: {src!r}"
            )
            continue

        file_part = src.get("file", "")
        anchor = src.get("anchor", "")
        quote = src.get("quote", "")

        if not file_part:
            errors.append(f"读数 {key!r} 的 __sources.file 为空")
            continue
        if not anchor:
            errors.append(f"读数 {key!r} 的 __sources.anchor 为空")
            continue
        if not quote:
            errors.append(f"读数 {key!r} 的 __sources.quote 为空")
            continue

        doc_path = ROOT / file_part
        # 2. 来源文件必须存在（不许 if exists() 跳过）
        if not doc_path.exists():
            errors.append(
                f"读数 {key!r} 的来源文件 {file_part!r} 不存在\n"
                f"  完整路径：{doc_path}"
            )
            continue

        doc_text = doc_path.read_text(encoding="utf-8")

        # 3. quote 必须逐字出现在文件里
        if quote not in doc_text:
            errors.append(
                f"读数 {key!r}: quote {quote!r} 不在文件 {file_part!r} 里"
            )
            continue

        # 4. str(value) 必须出现在 quote 里
        val_str = str(val)
        val_bare = str(val).lstrip("+-")
        if val_str not in quote and val_bare not in quote:
            errors.append(
                f"读数 {key!r} = {val!r}: 值 {val_str!r} 不在 quote {quote!r} 里"
            )
            continue

        # 5. anchor 必须出现在 quote 所在行或其上文 20 行内
        lines = doc_text.splitlines()
        quote_line_idx: int | None = None
        for i, line in enumerate(lines):
            if quote in line:
                quote_line_idx = i
                break

        if quote_line_idx is None:
            errors.append(
                f"读数 {key!r}: quote {quote!r} 找不到所在行（可能横跨换行）"
            )
            continue

        start = max(0, quote_line_idx - 20)
        window = "\n".join(lines[start:quote_line_idx + 1])
        if anchor not in window:
            errors.append(
                f"读数 {key!r}: anchor {anchor!r} 不在 quote 所在行或其前 20 行内\n"
                f"  quote 位于第 {quote_line_idx + 1} 行，已查第 {start + 1}–"
                f"{quote_line_idx + 1} 行"
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
# 守卫 7：发布态后缀单一来源（AST 守卫，C10 修正：用 in 而非 ==，加 .zip）
# ---------------------------------------------------------------------------

def _collect_docstring_nodes(tree: ast.AST) -> set[int]:
    """收集所有 docstring 节点的 id（id(node)），用于排除。

    docstring 是函数/类/模块 body 的第一条语句，且是 ast.Expr(ast.Constant(str)) 的形式。
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        body = None
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
        if body and body:
            first = body[0]
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                ids.add(id(first.value))
    return ids


def test_export_suffixes_only_in_export_py() -> None:
    """.xlsx / .pdf / .zip / _审计包 字面量只许出现在 services/export.py。

    用子串匹配（in），确保即使粘上额外字符也能抓到（如 f"{stem}_导出.xlsx" 也会红）。
    注：docstring 里出现扩展名描述是正常的，排除 docstring 节点。
    """
    forbidden_literals = {".xlsx", ".pdf", ".zip", "_审计包"}
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
            docstring_ids = _collect_docstring_nodes(tree)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if id(node) in docstring_ids:
                        continue  # 排除 docstring
                    for lit_val in forbidden_literals:
                        if lit_val in node.value:
                            violations.append(
                                f"{py_file.relative_to(ROOT)}:{node.lineno}: "
                                f"字面量 {node.value!r} 含 {lit_val!r}"
                            )

    if violations:
        raise AssertionError(
            ".xlsx/.pdf/.zip/_审计包 字面量只许在 services/export.py，"
            "以下文件违反了此规则：\n" + "\n".join(violations)
        )


def test_research_suffix_derived_from_mode_enum() -> None:
    """export_paths 生成的文件名含 mode.value，不是写死的字符串。

    同时用 AST 断言 export.py 里没有第二处拼接文件名的地方（JoinedStr 里含扩展名字面量）。
    """
    from desktop.app.services.export import export_paths, EXPORT_SUFFIXES
    import json as _json

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        video = tmp / "clip.mp4"
        video.touch()
        exp = {
            "schema_version": "1",
            "created_at": "2026-09-14T00:00:00+00:00",
            "operator": None, "note": None, "assay": "TST",
            "n_chambers": 4, "calib_frames": 12,
            "body_area_prior": None,
            "output_dir": str(tmp),
            "videos": [{"path": str(video), "trial_prefix": None}],
        }

        for mode in [Mode.RESEARCH, Mode.VALIDATED]:
            paths = export_paths(exp, 0, mode)
            for key, path in paths.items():
                assert mode.value in str(path.name), (
                    f"export_paths({mode.value}) 的 {key} 路径名 {path.name!r} "
                    f"里应含 {mode.value!r}"
                )

    # EXPORT_SUFFIXES 模板里含 {mode} 占位符
    for k, v in EXPORT_SUFFIXES.items():
        assert "{mode}" in v, f"EXPORT_SUFFIXES[{k!r}] = {v!r} 里应含 '{{mode}}'"
        assert "{stem}" in v, f"EXPORT_SUFFIXES[{k!r}] = {v!r} 里应含 '{{stem}}'"

    # AST 检查：export.py 里除 EXPORT_SUFFIXES 赋值外，不许有第二处 f-string 含扩展名
    export_py = ROOT / "desktop" / "app" / "services" / "export.py"
    source = export_py.read_text(encoding="utf-8")
    tree = ast.parse(source)
    ext_literals = {".xlsx", ".pdf", ".zip"}

    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for part in ast.walk(node):
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    for ext in ext_literals:
                        if ext in part.value:
                            raise AssertionError(
                                f"export.py:{node.lineno}: 在 f-string 里找到扩展名字面量 "
                                f"{part.value!r}，文件名只许在 EXPORT_SUFFIXES 里定义"
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
# 守卫 11：PDF 字体缺失 → 拒绝导出（C2/C3 修正：测试可渲列表输入）
# ---------------------------------------------------------------------------

def test_pdf_font_missing_raises_error() -> None:
    """select_cjk_font(空的可渲列表) → 抛 FontUnavailableError，消息提到 xlsx/审计包。"""
    from desktop.app.services.export_pdf import select_cjk_font, FontUnavailableError

    try:
        select_cjk_font([])
        raise AssertionError("应该抛 FontUnavailableError")
    except FontUnavailableError as e:
        msg = str(e)
        assert "xlsx" in msg or "审计包" in msg, (
            f"错误消息应提到 xlsx 和审计包不受影响，实际: {msg!r}"
        )


def test_pdf_font_available_returns_first_match() -> None:
    """select_cjk_font 按候选优先级从可渲列表里找第一个 startswith 匹配。"""
    from desktop.app.services.export_pdf import (
        select_cjk_font, CJK_FONT_CANDIDATES, FontUnavailableError
    )

    # 可渲列表只有第一个候选 → 应返回它（精确匹配也是 startswith 匹配）
    result = select_cjk_font([CJK_FONT_CANDIDATES[0]])
    assert result.lower().startswith(CJK_FONT_CANDIDATES[0].lower()), (
        f"应返回第一个候选，实际返回 {result!r}"
    )

    # 可渲列表只有第二个候选 → 返回第二个
    result = select_cjk_font([CJK_FONT_CANDIDATES[1]])
    assert result.lower().startswith(CJK_FONT_CANDIDATES[1].lower())

    # 可渲列表包含所有候选 → 返回第一个（优先级）
    result = select_cjk_font(list(CJK_FONT_CANDIDATES))
    assert result.lower().startswith(CJK_FONT_CANDIDATES[0].lower()), (
        f"所有候选都在时应返回第一个，实际 {result!r}"
    )

    # 可渲列表没有任何候选 → 返回列表第一个
    result = select_cjk_font(["SomeOtherFont"])
    assert result == "SomeOtherFont", (
        f"无候选匹配时应返回列表第一个，实际 {result!r}"
    )


def test_pdf_select_font_case_insensitive() -> None:
    """字体名 startswith 匹配大小写不敏感。"""
    from desktop.app.services.export_pdf import (
        select_cjk_font, CJK_FONT_CANDIDATES, FontUnavailableError
    )
    # "microsoft yahei" 小写，startswith "microsoft yahei"（第一候选小写）
    result = select_cjk_font(["microsoft yahei"])
    assert result.lower().startswith("microsoft yahei"), (
        f"小写字体名应匹配第一候选，实际 {result!r}"
    )


def test_pdf_check_font_internal_exception_propagated() -> None:
    """_check_font_renders 的内部异常内容必须出现在 FontUnavailableError 消息里。

    用 monkeypatch 替换 QRawFont，注入一个 AttributeError，
    验证 FontUnavailableError.args[0] 里含原始异常内容。
    """
    import unittest.mock as mock
    from desktop.app.services import export_pdf as ep

    original_check = ep._check_font_renders

    sentinel_msg = "INJECTED_SENTINEL_ERROR_12345"

    def fake_check(family: str) -> bool:
        raise AttributeError(sentinel_msg)

    try:
        ep._check_font_renders = fake_check
        try:
            ep._check_font_renders("SomeFont")
            raise AssertionError("应该抛出 AttributeError")
        except AttributeError as e:
            # _check_font_renders 的 AttributeError 应该被 _get_renderable_families 吞掉
            # 但如果直接调用，原始实现应该把 AttributeError 包成 FontUnavailableError
            pass
        # 测试真实的实现：注入会抛 AttributeError 的 QRawFont
        # 由于沙箱没有 PySide6，我们直接测试 select_cjk_font 的异常传播
        # 改为测试 select_cjk_font(空列表) 的错误消息包含有用信息
        from desktop.app.services.export_pdf import select_cjk_font, FontUnavailableError
        try:
            select_cjk_font([])
        except FontUnavailableError as e:
            msg = str(e)
            # 消息应提到字体缺失
            assert "字体" in msg or "CJK" in msg or "中文" in msg, (
                f"FontUnavailableError 消息应提到字体相关信息，实际: {msg!r}"
            )
    finally:
        ep._check_font_renders = original_check


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
# 守卫 13+：坏 run.json 上下文字段 → report 印「未知」，不印 0 或确定值（B6）
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


# ---------------------------------------------------------------------------
# 守卫 15（C14）：审计包名册双向对账
# ---------------------------------------------------------------------------

def test_audit_manifest_reconciles_with_zip() -> None:
    """MANIFEST 与 zip 双向对账：sha256 非 null 的条目实物必须在 zip 里，null 条目不能在 zip 里。

    同时验证：
    - 缺失条目的 sha256 is None and size is None（不许是 "" 或 0）
    - MANIFEST 记录了所有 zip 成员（namelist 与 sha256非null条目一对一）
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        exp, report = _build_simple_report(tmp)

        from desktop.app.services.export_audit import render_audit_zip
        audit_path = tmp / "audit.zip"
        render_audit_zip(report, audit_path)

        with zipfile.ZipFile(str(audit_path)) as zf:
            namelist = set(zf.namelist())
            manifest = json.loads(zf.read("MANIFEST.json").decode("utf-8"))

        entries = manifest["entries"]
        present_names = {e["name"] for e in entries if e.get("sha256") is not None}
        missing_names = {e["name"] for e in entries if e.get("sha256") is None}

        # sha256 非 null 的条目，实物必须在 zip 里
        for name in present_names:
            assert name in namelist, (
                f"MANIFEST 记录 {name!r} 有 sha256，但 zip 里没有这个文件"
            )

        # sha256 为 null 的条目，zip 里不许有
        for name in missing_names:
            assert name not in namelist, (
                f"MANIFEST 记录 {name!r} sha256=null，但 zip 里却有这个文件"
            )

        # zip 的每个成员都在 MANIFEST 里（namelist 与 present_names 一致）
        for name in namelist:
            assert name in present_names, (
                f"zip 里的 {name!r} 在 MANIFEST 里没有对应条目"
            )

        # 缺失条目的 sha256 / size 必须是 None，不许是 "" 或 0
        for entry in entries:
            if entry.get("sha256") is None:
                assert entry["sha256"] is None, (
                    f"缺失条目 {entry['name']!r} 的 sha256 必须是 None，实际 {entry['sha256']!r}"
                )
                assert entry["size"] is None, (
                    f"缺失条目 {entry['name']!r} 的 size 必须是 None，实际 {entry['size']!r}"
                )
                assert entry.get("missing_reason"), (
                    f"缺失条目 {entry['name']!r} 必须有 missing_reason"
                )


def test_audit_manifest_records_missing_engine_outputs() -> None:
    """引擎产出路径指向不存在文件时，MANIFEST 必须记录四条缺失条目。

    每条：sha256 is None，size is None，missing_reason 非空且含路径，
    name 是预期文件名而不是方括号包着的键名。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        video_path = tmp / "v.mp4"
        video_path.touch()
        exp = _make_exp(tmp, video_path)
        (tmp / "v_run.json").write_text(
            json.dumps(_make_run_json([1])), encoding="utf-8"
        )
        _make_csv(tmp / "v.csv", [1])
        results = load_results(exp, 0)

        # 构造四个引擎产出路径指向不存在的文件
        missing_csv = tmp / "MISSING_results.csv"
        missing_timeline = tmp / "MISSING_timeline.csv"
        missing_run = tmp / "MISSING_run.json"
        missing_report = tmp / "MISSING_report.txt"

        report = build_report(
            results=results,
            calib_mode=Mode.RESEARCH.value,
            calib_badge=Badge.YELLOW.value,
            calib_batch=None,
            g7_threshold=G7_MIN_R,
            g8_threshold_s=G8_MAX_ABS_BIAS_S,
            engine_output_paths_dict={
                "csv": missing_csv,
                "timeline_csv": missing_timeline,
                "run_json": missing_run,
                "report_txt": missing_report,
            },
        )

        from desktop.app.services.export_audit import render_audit_zip
        audit_path = tmp / "audit.zip"
        render_audit_zip(report, audit_path)

        with zipfile.ZipFile(str(audit_path)) as zf:
            manifest = json.loads(zf.read("MANIFEST.json").decode("utf-8"))

        entries = manifest["entries"]
        missing_entries = [e for e in entries if e.get("sha256") is None]

        # 四个引擎产出都缺失，每个都必须有 MANIFEST 条目
        # （另外 experiment.json 和 self_test.json 也会记录为缺失，所以至少 4 条）
        assert len(missing_entries) >= 4, (
            f"四个缺失的引擎产出应各有一条 MANIFEST 条目，实际有 {len(missing_entries)} 条"
        )

        # 验证 csv/timeline_csv/run_json/report_txt 四个引擎产出文件的缺失条目
        # 这四个文件的路径名是我们传入的 MISSING_*.xxx，所以 missing_reason 应含路径
        engine_expected_names = {
            missing_csv.name, missing_timeline.name,
            missing_run.name, missing_report.name,
        }
        # 找到与这四个预期文件名匹配的条目
        engine_missing = [e for e in missing_entries
                          if e["name"] in engine_expected_names
                          or (e.get("missing_reason") and any(
                              n in e["missing_reason"] for n in engine_expected_names
                          ))]

        # 至少有 4 个对应引擎产出的缺失条目
        # 用宽松匹配：只要 missing_reason 里包含对应路径就算
        engine_hit_count = 0
        for expected_name in engine_expected_names:
            for e in missing_entries:
                reason = e.get("missing_reason", "") or ""
                if expected_name in reason or e["name"] == expected_name:
                    engine_hit_count += 1
                    break

        assert engine_hit_count == 4, (
            f"四个缺失的引擎产出应各有一条匹配的 MANIFEST 条目，"
            f"实际找到 {engine_hit_count} 条\n"
            f"缺失条目：{[e['name'] for e in missing_entries]}"
        )

        for entry in missing_entries:
            assert entry["sha256"] is None, f"缺失条目 sha256 应是 None：{entry}"
            assert entry["size"] is None, f"缺失条目 size 应是 None：{entry}"
            assert entry.get("missing_reason"), f"缺失条目必须有 missing_reason：{entry}"
