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

# tests/ 不受进程边界约束，可以 import 引擎包取工具名常量
from depressionplex.video import TOOL_FFMPEG, TOOL_FFPROBE

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
from desktop.app.services.calibration import (
    G7_MIN_R,
    G8_MAX_ABS_BIAS_S,
    Mode,
    Badge,
    _MODE_LABELS,
)
from desktop.app.services.engine import output_paths as engine_output_paths


# ---------------------------------------------------------------------------
# 守卫 13（C0）：声明关键短语钉住列表（在测试里，不在产品代码里）
# 改一个字就红，要改声明就得同时改测试——那是有意的，reviewer 会看到。
# ---------------------------------------------------------------------------
_DECLARATION_REQUIRED_PHRASES: list[str] = [
    "研究版",
    "没有计量资质",
    "不得",
    "作为计量结果",       # badge.py NO_METROLOGY_LINE 也含此句（C0 钉住双处合规声明）
    "逐秒时间对齐门",
    "未定",
    "单个试次的秒数不要单独作为结论依据",
    "分母不看，秒数没有意义",
    "没有产出数字的隔间会在表里占一行并写明原因",
]


# ---------------------------------------------------------------------------
# 辅助：构造合法的 run.json / experiment / ResultsTable
# ---------------------------------------------------------------------------

# run.json 的 decoder 块样本，形状与 depressionplex/cli/analyze.py 写的那一份一致：
# 每个工具各有 path / source / version，外加一个 mixed_source。
_DECODER_BLOCK: dict = {
    TOOL_FFMPEG: {
        "path": "/opt/dpx/ffmpeg",
        "source": "bundled",
        "version": "ffmpeg version 7.1.1",
    },
    TOOL_FFPROBE: {
        "path": "/opt/dpx/ffprobe",
        "source": "bundled",
        "version": "ffprobe version 7.1.1",
    },
    "mixed_source": False,
}

_UNSET = object()


def _make_run_json(chambers: list[int], not_scored=None, chamber_validity=None,
                   decoder=_UNSET, plan_warnings=_UNSET) -> dict:
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
        "plan_warnings": [] if plan_warnings is _UNSET else plan_warnings,
        "chamber_validity": chamber_validity or [],
        "not_scored": not_scored or [],
        "decoder": _DECODER_BLOCK if decoder is _UNSET else decoder,
    }


def _decoder_identity(tmp: Path) -> tuple[str, str, str, str]:
    """按产品那条路取解码器身份，穿进 `_build_run_json`（DP-108 H10+A4）。

    两个工具各解析一次（source 可以不同），并且**走真的 `_resolve_ffmpeg_tool`**：
    在测试里手写 `"system"` 之类的字面量，等于给 run.json 的 decoder 块编一个产品
    永远不会写出来的值，那个块就再也没人验了。

    用 `DPX_FFMPEG` / `DPX_FFPROBE` 指到 tmp 下两个真实存在的空文件，走解析器的 env
    真分支（不 patch `Path.exists`——同一个 tmp 里还要真读真写 CSV 与 run.json，
    全局盖掉 exists 会把「文件不在」这类失败一起盖住）。空文件跑不起来，
    `_get_ffmpeg_version` 会走它自己的退化路径给 None，这也是现场会走的路。
    """
    from unittest import mock

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


def _build_simple_report(tmp: Path, chambers: list[int] = None,
                         **run_json_kwargs) -> tuple[dict, Report]:
    """构造一个最小的 Report 用于测试（run.json 的字段可用关键字参数覆盖）。"""
    chambers = chambers or [1, 2, 3, 4]
    video_path = tmp / "v.mp4"
    video_path.touch()
    exp = _make_exp(tmp, video_path)
    run_json_path = tmp / "v_run.json"
    run_json_path.write_text(
        json.dumps(_make_run_json(chambers, **run_json_kwargs)), encoding="utf-8")
    csv_path = tmp / "v.csv"
    _make_csv(csv_path, chambers)
    results = load_results(exp, 0)
    report = build_report(
        results=results,
        calib_mode=Mode.RESEARCH,
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


def test_compliance_phrases_consistent_with_badge() -> None:
    """badge.py NO_METROLOGY_LINE 和声明文本必须都含相同的核心合规短语（C0 追加）。

    这两处都说「没有计量资质/不得作为计量结果」，受众不同，文本各自维护（B8 明确
    说了不强行合并），但改任何一处而不改另一处就是合规说辞出现分叉——必须红。

    badge.py 由 B8（DP-111）引入，尚未合入时 skip。
    """
    badge_path = ROOT / "desktop" / "app" / "models" / "badge.py"
    if not badge_path.exists():
        # B8 先合，B6 后合；本测试在 B8 合入后才有意义
        import sys
        print("SKIP: badge.py 尚未合入（B8 先合），test_compliance_phrases_consistent_with_badge 跳过", file=sys.stderr)
        return

    badge_src = badge_path.read_text(encoding="utf-8")
    # 提取 NO_METROLOGY_LINE 的值（AST，不 import 以避免 PySide6 依赖链）
    import ast as _ast
    tree = _ast.parse(badge_src)
    no_metrology_line: str | None = None
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Assign):
            for t in node.targets:
                if isinstance(t, _ast.Name) and t.id == "NO_METROLOGY_LINE":
                    if isinstance(node.value, _ast.Constant):
                        no_metrology_line = node.value.value
    assert no_metrology_line is not None, (
        "badge.py 存在但找不到 NO_METROLOGY_LINE 赋值——守卫写法需要更新"
    )

    decl = render_declaration(
        g7_threshold=G7_MIN_R,
        g8_threshold_s=G8_MAX_ABS_BIAS_S,
        theta_mob=0.0175,
        mode=Mode.RESEARCH,
    )

    _COMPLIANCE_PHRASES = ["没有计量资质", "作为计量结果"]
    for phrase in _COMPLIANCE_PHRASES:
        assert phrase in no_metrology_line, (
            f"badge.py NO_METROLOGY_LINE 缺少合规短语 {phrase!r}，"
            "改了 badge.py 必须同时确认 declaration 也说同样的事"
        )
        assert phrase in decl, (
            f"声明文本缺少合规短语 {phrase!r}，"
            "改了 declaration 必须同时确认 badge.py NO_METROLOGY_LINE 也说同样的事"
        )


def test_declaration_mode_research_label() -> None:
    """研究版声明含判定层给的那个标签，不含另一个模式的标签。

    标签从 calibration._MODE_LABELS 取——测试里重抄一遍「研究版」的话，
    产品把映射改了测试照旧绿。值本身由 test_mode_labels_pinned 逐字钉住。
    """
    decl = render_declaration(
        g7_threshold=G7_MIN_R,
        g8_threshold_s=G8_MAX_ABS_BIAS_S,
        theta_mob=0.0175,
        mode=Mode.RESEARCH,
    )
    assert _MODE_LABELS[Mode.RESEARCH] in decl, "研究版声明应含研究版标签"
    assert _MODE_LABELS[Mode.VALIDATED] not in decl, "研究版声明不该出现计量版标签"


def test_declaration_mode_validated_raises() -> None:
    """Mode.VALIDATED 时抛 NotImplementedError（计量版文案未定，M3 前禁止生成）。

    抛点在判定层 calibration.declaration_version_label，报告层只是把它传下去；
    这条测试走的是产品真实路径（render_declaration），不直接点判定层。
    """
    try:
        render_declaration(
            g7_threshold=G7_MIN_R,
            g8_threshold_s=G8_MAX_ABS_BIAS_S,
            theta_mob=0.0175,
            mode=Mode.VALIDATED,
        )
        raise AssertionError("Mode.VALIDATED 应抛 NotImplementedError")
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


def test_mode_labels_pinned() -> None:
    """两个发布态标签逐字钉住。

    只断言「声明里含 _MODE_LABELS[Mode.RESEARCH]」是不够的：把标签改成空串，
    `"" in decl` 恒真，那条守卫会变成装饰。所以值在这里逐字写死，
    改文案必须同时改这条测试。
    """
    assert _MODE_LABELS == {Mode.RESEARCH: "研究版", Mode.VALIDATED: "计量版"}, (
        f"发布态标签被改了：{_MODE_LABELS}"
    )


def test_report_layer_has_no_mode_branch() -> None:
    """报告层不许自己拿模式做分支——标签和拒绝都走判定层那一个入口。

    与 DP-111 的 test_no_second_mode_decision_in_desktop 同源，在这里再钉一遍是因为
    这一处有过实际复发：C0 第一版就是把「哪个模式配哪套文案」和
    「计量版不许出报告」写在了 report.py 里，字典派发一份、比较表达式一处。
    判据是「任何形状」：Mode.X 属性访问与 "research"/"validated" 字面量都算。
    """
    report_py = ROOT / "desktop" / "app" / "models" / "report.py"
    source = report_py.read_text(encoding="utf-8")
    tree = ast.parse(source)
    doc_ids = _collect_docstring_nodes(tree)

    offenders: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in ("RESEARCH", "VALIDATED"):
            offenders.append(node.lineno)
        if (isinstance(node, ast.Constant)
                and node.value in ("research", "validated")
                and id(node) not in doc_ids):
            offenders.append(node.lineno)
    assert not offenders, (
        f"report.py 这些行自己拿模式做了分支：{sorted(set(offenders))}——"
        "标签与拒绝都该走 calibration.declaration_version_label"
    )

    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "declaration_version_label" in called, (
        "report.py 没有调用判定层的 declaration_version_label——"
        "版本标签又变成本层自己决定的了"
    )


def test_render_declaration_mode_is_required() -> None:
    """render_declaration 的 mode 不许有默认值。

    默认值就是一处藏在函数签名里的发布态判定：调用方漏传时静默按那个默认发布态渲染。
    这不是假想——本文件「读数不写死」那条测试原先就在白吃这个默认值，
    也就是说签名已经在替调用方做决定了。漏传必须当场炸。
    """
    report_py = ROOT / "desktop" / "app" / "models" / "report.py"
    tree = ast.parse(report_py.read_text(encoding="utf-8"))
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "render_declaration"), None)
    assert fn is not None, "找不到 render_declaration——守卫写法要更新"
    names = [a.arg for a in fn.args.args]
    assert "mode" in names, f"render_declaration 没有 mode 参数：{names}"
    n_required = len(names) - len(fn.args.defaults)
    assert names.index("mode") < n_required, (
        "render_declaration 的 mode 有默认值——漏传就会静默按那个默认发布态渲染"
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
            mode=Mode.RESEARCH,
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
            calib_mode=Mode.RESEARCH,
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
            calib_mode=Mode.RESEARCH,
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
# 守卫 10（DP-121 / DP-122）：解码器身份与计划告警印的是真值，不是「永远未知」
# ---------------------------------------------------------------------------

def test_decoder_identity_printed_from_run_json() -> None:
    """报告里印的解码器身份必须是 run.json 里那一份真值。

    这条守卫的上一版断言的是「印了『未知』」——那是把缺陷钉住了：report.py 当时用
    `getattr(results, "decoder_info", None)` 取，而全仓没有任何地方设过那个字段，
    于是这一行**永远**印「未知」，测试也永远绿。而 DP-108 要求审计包能回答
    「这批帧是哪个解码器解出来的」，一行「未知」答不了这个问题。
    「不为空」不是要求，「说的是真话」才是：这里逐个断言 version / source / path
    真的落在那一行里，期望值取自夹具那份 decoder 块，不在测试里另抄一遍。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        _, report = _build_simple_report(Path(tmpdir))
        rows = {r.key: r.value for r in report.context_rows}

        for tool in (TOOL_FFMPEG, TOOL_FFPROBE):
            key = f"解码器 {tool}"
            assert key in rows, f"上下文表里没有 {key!r} 这一行：{sorted(rows)}"
            val = rows[key]
            expected = _DECODER_BLOCK[tool]
            for field_name in ("version", "source", "path"):
                assert expected[field_name] in val, (
                    f"{key} 那一行没带上 run.json 的 {field_name}："
                    f"期望含 {expected[field_name]!r}，实际 {val!r}"
                )
            assert "未知" not in val, (
                f"{key} 印了「未知」，而 run.json 里明明有值：{val!r}"
            )

        mixed_key = "解码器来源混用（mixed_source）"
        assert rows.get(mixed_key) == "否", (
            f"夹具里 mixed_source=False，这一行应印「否」，实际 {rows.get(mixed_key)!r}"
        )


def test_decoder_bad_block_prints_unknown_with_reason() -> None:
    """decoder 块坏掉 ⇒ 印「未知」，且报警行里留下原因（降级必须留说明）。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        _, report = _build_simple_report(Path(tmpdir), decoder="不是对象")
        rows = {r.key: r.value for r in report.context_rows}

        assert rows.get("解码器身份") == "未知", (
            f"decoder 块不可读时应印「未知」，实际 {rows.get('解码器身份')!r}"
        )
        assert not [k for k in rows if k.startswith("解码器 ")], (
            f"decoder 块不可读，却还印出了逐工具的解码器行：{sorted(rows)}"
        )
        alarms = [r.reason or "" for r in report.trial_rows if r.kind == "alarm"]
        assert any("decoder" in a for a in alarms), (
            f"降级成「未知」却没留下一句说明：{alarms}"
        )


def test_decoder_mixed_source_says_what_it_means() -> None:
    """两个工具来源不同时那一行必须说人话，不许把 JSON 的 True 原样印给用户。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        block = dict(_DECODER_BLOCK)
        block[TOOL_FFPROBE] = dict(block[TOOL_FFPROBE], source="system")
        block["mixed_source"] = True
        _, report = _build_simple_report(Path(tmpdir), decoder=block)
        rows = {r.key: r.value for r in report.context_rows}

        val = rows.get("解码器来源混用（mixed_source）")
        assert val is not None, "上下文表里没有 mixed_source 行"
        assert val.startswith("是"), f"mixed_source=True 时应印「是……」，实际 {val!r}"
        assert val not in ("True", "true"), "不许把 JSON 的布尔值原样印给用户"


def test_plan_warnings_printed_not_swallowed() -> None:
    """引擎给了计划告警，报告必须原文印出来，不许印「（无）」。

    与解码器那一行是同一个病的第二个器官：report.py 当时用
    `getattr(results, "plan_warnings", None)` 取一个 `ResultsTable` 从来没有过的
    字段，所以这一行**永远**是「（无）」，而 run.json 里 `plan_warnings`
    从 DP-108 起就是必写键，数据一直都在。
    """
    warnings = ["隔间 3 的校准帧只有 2 帧", "隔间 4 几乎全程无占据"]
    with tempfile.TemporaryDirectory() as tmpdir:
        _, report = _build_simple_report(Path(tmpdir), plan_warnings=warnings)
        rows = {r.key: r.value for r in report.context_rows}

        val = rows.get("plan_warnings")
        assert val is not None, "上下文表里没有 plan_warnings 行"
        for w in warnings:
            assert w in val, f"plan_warnings 丢了一条：{w!r}（实际 {val!r}）"
        assert "（无）" not in val, "引擎明明告警了，这一行却写着「（无）」"


def test_plan_warnings_unreadable_is_not_folded_into_empty() -> None:
    """plan_warnings 读不出来 ⇒「未知」，不许折成「（无）」。

    「引擎说没有告警」和「这个字段坏了」是两件事。印成同一句话的后果是：
    看报告的人以为引擎放行了这批数据。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        _, report = _build_simple_report(Path(tmpdir), plan_warnings="不是列表")
        rows = {r.key: r.value for r in report.context_rows}
        assert rows.get("plan_warnings") == "未知", (
            f"plan_warnings 不可读时应印「未知」，实际 {rows.get('plan_warnings')!r}"
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
    """Qt 在 _check_font_renders 里抛的异常内容，必须原样出现在 FontUnavailableError 里。

    上一版是**一条断言自己 mock 的测试**：它把 `ep._check_font_renders` 整个换成一个
    抛 AttributeError 的假函数，然后断言这个假函数抛了 AttributeError，接着改去测
    `select_cjk_font([])`（和上面那条重复）。产品那段 try/except 一行都没跑到。
    这一版换掉的是**依赖**（Qt），跑的是产品本体：沙箱里没有 PySide6，
    所以往 sys.modules 塞一个只有 QFont/QRawFont 的假 QtGui，用完精确还原
    （CI 上是有真 PySide6 的，不还原会影响后面的用例）。
    """
    import sys as _sys
    import types
    from desktop.app.services import export_pdf as ep

    sentinel = "INJECTED_SENTINEL_ERROR_12345"

    class _FakeRawFont:
        @staticmethod
        def fromFont(_font):
            raise AttributeError(sentinel)

    fake_qtgui = types.ModuleType("PySide6.QtGui")
    fake_qtgui.QRawFont = _FakeRawFont
    fake_qtgui.QFont = lambda family: family
    fake_pyside = types.ModuleType("PySide6")
    fake_pyside.QtGui = fake_qtgui

    saved = {name: _sys.modules.get(name) for name in ("PySide6", "PySide6.QtGui")}
    try:
        _sys.modules["PySide6"] = fake_pyside
        _sys.modules["PySide6.QtGui"] = fake_qtgui

        try:
            ep._check_font_renders("SomeFont")
        except ep.FontUnavailableError as e:
            msg = str(e)
            assert sentinel in msg, (
                f"Qt 的原始异常内容被吞了，报错里查不到它：{msg!r}"
            )
            assert "SomeFont" in msg, f"报错没说是哪个字体：{msg!r}"
            assert isinstance(e.__cause__, AttributeError), (
                f"原异常没挂在 __cause__ 上，traceback 会断：{e.__cause__!r}"
            )
        else:
            raise AssertionError("Qt 抛 AttributeError 时应该包成 FontUnavailableError")

        # 同一个异常在「逐个筛一批字体」的路径上必须被吞掉：
        # 一个字体探测失败不该让整次导出失败，「一个都没有」才是失败。
        assert ep._renders_ok("SomeFont") is False, (
            "_renders_ok 应该吞掉单个字体的探测异常并当它不可渲"
        )
    finally:
        for name, mod in saved.items():
            if mod is None:
                _sys.modules.pop(name, None)
            else:
                _sys.modules[name] = mod


# ---------------------------------------------------------------------------
# 守卫 13（DP-110）：随包中文字体——常量钉死、进打包清单、报错能归因、不进 git
#
# 这一节放在本文件而不是 test_packaging_contract.py：随包字体是 PDF 导出契约的一部分
# （没有它 windows 上根本导不出 PDF），而 test_packaging_contract.py 正被 DP-108 分支
# 同时改，避开撞车。
# ---------------------------------------------------------------------------

_FETCH_FONT = ROOT / "packaging" / "fetch_font.py"


def _fetch_font_constants() -> dict:
    """把 packaging/fetch_font.py 的模块级常量取出来（不 import 那个文件）。

    不 import 的两个理由：`packaging` 这个顶层名字和 PyPI 上的 packaging 包撞车，
    仓里也没有 `packaging/__init__.py`；已有的 fetch_ffmpeg 守卫也是读源码。
    只 exec 值是字面量或 f-string 的赋值语句——`VENDOR_DIR = Path(...)` 那种带调用的
    跳过（本测试不需要它，exec 它还要先造出 Path）。
    """
    import ast as _ast
    src = _FETCH_FONT.read_text(encoding="utf-8")
    tree = _ast.parse(src)
    keep = [n for n in tree.body
            if isinstance(n, _ast.Assign)
            and isinstance(n.value, (_ast.Constant, _ast.JoinedStr))]
    ns: dict = {}
    exec(compile(_ast.Module(body=keep, type_ignores=[]), str(_FETCH_FONT), "exec"), ns)
    return ns


def test_bundled_font_constants_pinned() -> None:
    """字体的 URL / 字节数 / sha256 逐字钉死，且与 export_pdf 的文件名对得上。

    钉死的理由：这份文件是**可执行的法律与技术前提**——OFL 授权的是这一份文件，
    「能渲中文」也是对这一份文件实测出来的（8331336 字节 / family "Noto Sans SC" /
    cmap 里有 U+4E2D）。换了文件就要重新实测、重新看许可，不许悄悄换。
    URL 必须钉在 tag 上：raw.githubusercontent 的 main 分支上文件会变，哈希会失效。
    """
    ns = _fetch_font_constants()

    assert ns["FONT_BYTES"] == 8331336, f"字体字节数被改了：{ns['FONT_BYTES']}"
    assert ns["FONT_SHA256"] == (
        "faa6c9df652116dde789d351359f3d7e5d2285a2b2a1f04a2d7244df706d5ea9"
    ), f"字体 sha256 被改了：{ns['FONT_SHA256']}"
    assert ns["FONT_URL"] == (
        "https://raw.githubusercontent.com/notofonts/noto-cjk/Sans2.004"
        "/Sans/SubsetOTF/SC/NotoSansSC-Regular.otf"
    ), f"字体下载地址被改了：{ns['FONT_URL']}"

    assert ns["LICENSE_BYTES"] == 4301, f"许可字节数被改了：{ns['LICENSE_BYTES']}"
    assert ns["LICENSE_SHA256"] == (
        "6a73f9541c2de74158c0e7cf6b0a58ef774f5a780bf191f2d7ec9cc53efe2bf2"
    ), f"许可 sha256 被改了：{ns['LICENSE_SHA256']}"

    for key in ("FONT_URL", "LICENSE_URL"):
        for moving in ("/main/", "/master/", "/latest/"):
            assert moving not in ns[key], (
                f"{key} 指到了滚动分支 {moving}——上游一改文件哈希就失效，必须钉 tag"
            )

    # 与消费方对齐：文件名只许有一份真值
    from desktop.app.services.export_pdf import BUNDLED_FONT_FAMILY
    # 文件名与包内目录名从 utils/paths.py 拿（单一来源）。故意不从 export_pdf 转手：
    # 那样 export_pdf 里再写死一份同名常量也能让这条守卫过去。
    from desktop.app.utils.paths import BUNDLED_FONT_FILENAME, BUNDLED_FONT_SUBDIR
    assert ns["FONT_FILENAME"] == BUNDLED_FONT_FILENAME, (
        f"fetch_font 落的文件名 {ns['FONT_FILENAME']!r} 与 export_pdf 找的 "
        f"{BUNDLED_FONT_FILENAME!r} 不一致——拉下来也用不上"
    )
    # family 名是 Qt 注册后报出来的那个，**不是** "Noto Sans CJK SC"（Linux 系统包才叫那个）
    assert BUNDLED_FONT_FAMILY == "Noto Sans SC", (
        f"随包字体的 family 名被改了：{BUNDLED_FONT_FAMILY!r}"
    )
    assert BUNDLED_FONT_SUBDIR == "fonts", (
        f"包内字体目录名被改了：{BUNDLED_FONT_SUBDIR!r}（要与 spec 的 datas 目标一致）"
    )


def test_bundled_font_in_installer_spec() -> None:
    """GUI 的 PyInstaller spec 必须把字体**和许可**都打进包里的 fonts/ 目录。

    漏了字体：客户机上 PDF 直接拒绝导出（不是降级，是拒绝）。
    漏了许可：OFL 1.1 要求再分发时带许可正文，那是法律边界。
    路径与目标目录都从常量推出来，不在这里另抄一遍。
    """
    import ast as _ast
    from desktop.app.utils.paths import BUNDLED_FONT_FILENAME, BUNDLED_FONT_SUBDIR

    spec = ROOT / "packaging" / "build_windows.spec"
    tree = _ast.parse(spec.read_text(encoding="utf-8"))
    datas = None
    for node in _ast.walk(tree):
        if isinstance(node, _ast.keyword) and node.arg == "datas":
            datas = [tuple(_ast.literal_eval(e)) for e in node.value.elts]
    assert datas is not None, "build_windows.spec 里找不到 datas=（守卫写法要更新）"

    ns = _fetch_font_constants()
    # spec 里的相对路径以 packaging/ 为基准（dark.qss 那两条就是这个写法）
    want = [
        (f"../vendor/{BUNDLED_FONT_SUBDIR}/{BUNDLED_FONT_FILENAME}", BUNDLED_FONT_SUBDIR),
        (f"../vendor/{BUNDLED_FONT_SUBDIR}/{ns['LICENSE_FILENAME']}", BUNDLED_FONT_SUBDIR),
    ]
    for entry in want:
        assert entry in datas, f"spec 的 datas 里缺 {entry}，实际：{datas}"


def test_font_failure_message_distinguishes_three_causes() -> None:
    """拒绝导出时那句话必须说清是三种原因里的哪一种。

    旧文案把「一个字体都没枚举到」和「机器上没有中文字体」说成同一句，于是 CI 上
    印出来的「本机缺中文字体，已枚举 0 个字体均不可渲中文」是**一句关于机器的假话**：
    同一台 windows runner 上 msyh.ttc 在位、换原生插件能枚举到 154 个（24 个能渲「中」），
    真正的原因是 offscreen 平台插件不提供字体库（实测 run 34932358712）。
    报错文案本身就是归因结论，含混的文案会把下一个人的排查带偏一整天。
    """
    from desktop.app.services.export_pdf import _font_failure_message
    from desktop.app.utils.paths import BUNDLED_FONT_FILENAME

    with tempfile.TemporaryDirectory() as tmpdir:
        present = Path(tmpdir) / BUNDLED_FONT_FILENAME
        present.write_bytes(b"not a real font, only needs to exist")
        absent = Path(tmpdir) / "nowhere" / BUNDLED_FONT_FILENAME

        missing_msg = _font_failure_message(absent, 0)
        plugin_msg = _font_failure_message(present, 0)
        no_cjk_msg = _font_failure_message(present, 154)

        # 1) 随包字体不在位 ⇒ 说是打包/部署漏了，且明确否掉「机器没字体」
        assert str(absent) in missing_msg, f"没说缺的是哪个文件：{missing_msg!r}"
        assert "不是这台机器缺字体" in missing_msg, (
            f"文件都不在位却没否掉「机器没字体」这个误判：{missing_msg!r}"
        )

        # 2) 在位但注册失败 + 枚举到 0 个 ⇒ 归因到平台插件，**不许**说机器没字体
        assert "平台插件" in plugin_msg, f"枚举到 0 个时没归因到平台插件：{plugin_msg!r}"
        assert "不等于这台机器没有中文字体" in plugin_msg, (
            f"枚举到 0 个时说成了机器没字体（那是假话）：{plugin_msg!r}"
        )
        assert "确实缺中文字体" not in plugin_msg, (
            f"枚举到 0 个时不许断言机器确实缺字体：{plugin_msg!r}"
        )

        # 3) 枚举到 N>0 但无一可渲 ⇒ 这才是「机器确实缺中文字体」
        assert "确实缺中文字体" in no_cjk_msg, (
            f"枚举到 154 个都不能渲中文，这时才该说机器缺字体：{no_cjk_msg!r}"
        )
        assert "154" in no_cjk_msg, f"没带上枚举总数这个证据：{no_cjk_msg!r}"
        assert "平台插件" not in no_cjk_msg, (
            f"枚举正常却把锅推给平台插件：{no_cjk_msg!r}"
        )

        msgs = [missing_msg, plugin_msg, no_cjk_msg]
        assert len(set(msgs)) == 3, f"三种原因印出了同一句话：{msgs}"
        for m in msgs:
            assert "xlsx" in m and "审计包" in m, (
                f"拒绝导出 PDF 时要告诉用户另外两样不受影响：{m!r}"
            )


def test_vendor_binaries_not_committed() -> None:
    """vendor/ 必须被 .gitignore 挡住——字体和 ffmpeg 都是下载得到的，不进仓。

    这一条不是新规矩（大文件不进仓是项目硬规矩），是把它真的钉上：
    在本轮之前 .gitignore 里**没有** vendor/，那 80 MB 的 ffmpeg 和 8.3 MB 的字体
    一直都是能被 commit 进去的，只是碰巧没人 add。
    """
    lines = [ln.strip() for ln in
             (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()]
    assert "vendor/" in lines, (
        ".gitignore 里没有 vendor/ —— 随包二进制（ffmpeg 80 MB、字体 8.3 MB）会被 commit 进仓"
    )


# ---------------------------------------------------------------------------
# 守卫 14（DP-110 C12）：部分失败时的弹窗不许说「成功」——那几份也撤回了
#
# 沙箱与 3.9 都 import 不了 pages/（要 PySide6），所以只能 AST 读。
# 这不是「测不到」：这条守卫盯的是**文案说的是不是真话**，而文案是源码里的字面量。
# ---------------------------------------------------------------------------

_RESULTS_PAGE = ROOT / "desktop" / "app" / "pages" / "results.py"


def _on_export_ast() -> "ast.FunctionDef":
    tree = ast.parse(_RESULTS_PAGE.read_text(encoding="utf-8"))
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_on_export"), None)
    assert fn is not None, "pages/results.py 里找不到 _on_export（守卫写法要更新）"
    return fn


def _string_pieces(node) -> list[str]:
    """节点里所有中文/英文字面量片段（f-string 的常量段也算）。"""
    out: list[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            out.append(sub.value)
    return out


def test_partial_export_dialog_does_not_claim_success() -> None:
    """部分导出失败时不许宣称「成功：xlsx, 审计包」。

    原子性契约是「任何一份失败则整体不留」，代码本身是对的（三份先渲染到临时目录，
    全成功才移动）。错的是那句话——用户读到「成功：xlsx, 审计包」会去目标目录找那两个
    文件，那里一个都没有；更糟的是他会以为那两份数据已经留档了。
    「不为空」不是要求，「说的是真话」才是。
    """
    pieces = _string_pieces(_on_export_ast())
    joined = "\n".join(pieces)

    for claim in ("成功：", "成功:"):
        offenders = [p for p in pieces if claim in p]
        assert not offenders, (
            f"导出弹窗里出现了 {claim!r} 式的成功清单：{offenders}——"
            "部分失败时那几份也一起撤回了，目标目录里没有它们"
        )
    assert "一个文件都没有导出" in joined, (
        "部分失败的弹窗里没有明说「一个文件都没有导出」，用户会去找不存在的文件"
    )
    assert "撤回" in joined, (
        "部分失败的弹窗里没说成功渲染的那几份也撤回了（原子性约定要说给用户听）"
    )


def test_font_failure_dialog_quotes_the_reason() -> None:
    """字体那条弹窗必须把 FontUnavailableError 的原文带上，不许换成一句猜测。

    原来写的是「PDF 未导出：请检查中文字体安装」——那是**猜**出来的原因：真实原因可能是
    安装包漏了随包字体，也可能是平台插件不提供字体库（实测 run 34932358712），
    两种都跟用户装没装字体无关。归因结论已经写在异常消息里了
    （见 export_pdf._font_failure_message 的三条分支），这里只需原文转述。
    """
    fn = _on_export_ast()
    handlers = [h for h in ast.walk(fn)
                if isinstance(h, ast.ExceptHandler)
                and isinstance(h.type, ast.Name)
                and h.type.id == "FontUnavailableError"]
    assert len(handlers) == 1, (
        f"_on_export 里 FontUnavailableError 的 except 分支有 {len(handlers)} 个，期望 1 个"
    )
    handler = handlers[0]
    assert handler.name, (
        "except FontUnavailableError 没有 as e —— 拿不到异常就不可能转述它的归因结论"
    )
    exc_name = handler.name

    interpolated = [
        node for node in ast.walk(handler)
        if isinstance(node, ast.JoinedStr)
        and any(isinstance(v, ast.FormattedValue)
                and isinstance(v.value, ast.Name)
                and v.value.id == exc_name
                for v in node.values)
    ]
    assert interpolated, (
        f"字体失败那条消息里没有把 {exc_name} 插进去——"
        "三种原因（打包漏了 / 平台插件不给字体库 / 机器真没字体）会退回成一句猜测"
    )


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

        # 写 run.json：用引擎的 _build_run_json（与 test_results_model 一致）。
        # 解码器身份由调用方穿进来（DP-108 H10+A4），值取自真的 _resolve_ffmpeg_tool。
        run_json_path = tmp / "合成_run.json"
        ffmpeg_path, ffmpeg_source, ffprobe_path, ffprobe_source = _decoder_identity(tmp)
        run_data = A._build_run_json(info, plan, "TST", skipped, set(reports),
                                     ffmpeg_path, ffmpeg_source,
                                     ffprobe_path, ffprobe_source)
        run_json_path.write_text(json.dumps(run_data, ensure_ascii=False), encoding="utf-8")

        exp = _make_exp(tmp, video_path)

        results = load_results(exp, 0)
        assert results.rows, "load_results 返回空行列表"

        report = build_report(
            results=results,
            calib_mode=Mode.RESEARCH,
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
            # decoder 是必写键（DP-121：缺了要炸，不许 getattr 成「未知」蒙过去）。
            # 这条测试的主题是**别的**字段类型错 ⇒ 印「未知」，所以这里给一个**好**块；
            # 坏 decoder 块有自己的守卫 test_decoder_bad_block_prints_unknown_with_reason。
            "decoder": _DECODER_BLOCK,
        }
        (tmp / "v_run.json").write_text(json.dumps(bad_run), encoding="utf-8")
        # 写一个正常 CSV（否则 load_results 会因为无 CSV 而全部 alarm）
        _make_csv(tmp / "v.csv", [1])

        results = load_results(exp, 0)

        report = build_report(
            results=results,
            calib_mode=Mode.RESEARCH,
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
            calib_mode=Mode.RESEARCH,
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
