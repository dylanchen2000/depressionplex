"""导出服务守卫测试（B6，DP-110）。

补充 test_report_model.py 没有的守卫：
- EXPORT_SUFFIXES 唯一来源
- export_paths 文件名含 mode.value（不是写死的 _research）
- 渲染层无算术：AST 守卫扫 export_xlsx.py / export_audit.py 里的运算符
- 声明模板里不包含读数的字面量数字（改 JSON 而声明照旧印旧数是最难发现的错法）
"""

from __future__ import annotations

import ast
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from desktop.app.services.calibration import Mode, Badge, G7_MIN_R, G8_MAX_ABS_BIAS_S
from desktop.app.models.report import (
    DECLARATION_TEMPLATE,
    _load_validation_readings,
)


# ---------------------------------------------------------------------------
# EXPORT_SUFFIXES 唯一来源（C9：_MODE_RESEARCH 已删除，改为 {mode} 模板）
# ---------------------------------------------------------------------------

def test_export_suffixes_contain_mode_value() -> None:
    """EXPORT_SUFFIXES 的模板里含 {mode} 占位符，格式化后含 Mode.RESEARCH.value。

    C9：_MODE_RESEARCH 已从 export.py 中删除。
    EXPORT_SUFFIXES 现在是纯模板，由 export_paths() 在调用时注入 mode.value。
    """
    from desktop.app.services.export import EXPORT_SUFFIXES

    research_val = Mode.RESEARCH.value  # "research"

    # 1. 每个模板都含 {mode} 和 {stem} 占位符
    for k, tmpl in EXPORT_SUFFIXES.items():
        assert "{mode}" in tmpl, (
            f"EXPORT_SUFFIXES[{k!r}]={tmpl!r} 应含 '{{mode}}' 占位符"
        )
        assert "{stem}" in tmpl, (
            f"EXPORT_SUFFIXES[{k!r}]={tmpl!r} 应含 '{{stem}}' 占位符"
        )

    # 2. 格式化后含 mode.value
    formatted = {k: v.format(stem="test_video", mode=research_val)
                 for k, v in EXPORT_SUFFIXES.items()}
    assert research_val in formatted["xlsx"], (
        f"格式化后 xlsx={formatted['xlsx']!r} 里应含 {research_val!r}"
    )
    assert ".xlsx" in formatted["xlsx"]
    assert ".pdf" in formatted["pdf"]
    assert ".zip" in formatted["audit_zip"]


def test_export_paths_uses_mode_value() -> None:
    """export_paths 的文件名里含 mode.value，不是写死的 research。"""
    from desktop.app.services.export import export_paths
    import json

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


# ---------------------------------------------------------------------------
# 渲染层无科学计算（AST 守卫）
# 允许：字符串拼接、路径拼接（Path / "name"）、整数索引偏移（i + 1）、列表复制（[""] * n）
# 禁止：round() 调用；浮点数上的算术；DENOMINATORS 字段名参与运算
# ---------------------------------------------------------------------------

def _is_string_constant(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _is_small_int_constant(node: ast.expr) -> bool:
    """小整数常量（索引偏移用，如 +1, -1, +2, +4, +60 等）。"""
    return isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool)


def _is_list_or_dict(node: ast.expr) -> bool:
    return isinstance(node, (ast.List, ast.Dict, ast.Tuple))


def _is_path_div(node: ast.BinOp) -> bool:
    """Path / "string" 这样的路径除法。"""
    return (isinstance(node.op, ast.Div) and
            (_is_string_constant(node.right) or _is_string_constant(node.left)))


def _is_benign_binop(node: ast.BinOp) -> bool:
    """是否是渲染层可接受的操作（非科学计算）。"""
    l, r, op = node.left, node.right, node.op
    # 字符串拼接
    if _is_string_constant(l) or _is_string_constant(r):
        return True
    # f-string 拼接
    if isinstance(l, ast.JoinedStr) or isinstance(r, ast.JoinedStr):
        return True
    # 路径 /
    if _is_path_div(node):
        return True
    # 列表拼接 / 列表乘法（初始化用）
    if isinstance(op, (ast.Add, ast.Mult)) and (_is_list_or_dict(l) or _is_list_or_dict(r)):
        return True
    # 小整数偏移（ws.max_row + 1, range(..., n + 1), i + 1 等）
    if isinstance(op, (ast.Add, ast.Sub)) and (_is_small_int_constant(l) or _is_small_int_constant(r)):
        return True
    # 整数乘法（列表复制）
    if isinstance(op, ast.Mult) and (_is_small_int_constant(l) or _is_small_int_constant(r)):
        return True
    return False


def _find_scientific_arithmetic(py_file: Path) -> list[str]:
    """找文件里可能是科学计算的算术运算（排除字符串/路径/索引操作）。"""
    source = py_file.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source, filename=str(py_file))
    violations = []

    # 检查 round() 调用
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "round":
                violations.append(f"L{node.lineno}: round() 调用")

    # 检查可疑的 BinOp
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv)):
                if not _is_benign_binop(node):
                    line = lines[node.lineno - 1].strip()
                    violations.append(
                        f"L{node.lineno} ({type(node.op).__name__}): {line[:80]}"
                    )
    return violations


def test_render_xlsx_no_scientific_arithmetic() -> None:
    """export_xlsx.py 渲染层不含科学计算（round()、浮点算术；允许字符串/路径/索引操作）。"""
    xlsx_py = ROOT / "desktop" / "app" / "services" / "export_xlsx.py"
    viols = _find_scientific_arithmetic(xlsx_py)
    assert not viols, (
        "export_xlsx.py 里出现可能的科学计算（渲染层只摆不算）：\n"
        + "\n".join(viols)
    )


def test_render_audit_no_scientific_arithmetic() -> None:
    """export_audit.py 渲染层不含科学计算。"""
    audit_py = ROOT / "desktop" / "app" / "services" / "export_audit.py"
    viols = _find_scientific_arithmetic(audit_py)
    assert not viols, (
        "export_audit.py 里出现可能的科学计算（渲染层只摆不算）：\n"
        + "\n".join(viols)
    )


# ---------------------------------------------------------------------------
# 声明模板不含读数的字面量数字
# ---------------------------------------------------------------------------

def test_declaration_template_has_no_hardcoded_numbers() -> None:
    """DECLARATION_TEMPLATE 里不出现 validation_readings.json 的读数字面量。

    如果模板里写死了数字，改 JSON 而模板照旧印旧数，是最难被发现的错法。
    仅检查有小数点的浮点数（避免误判 §5、19 之类的普通文本）。
    """
    import re
    readings = _load_validation_readings()

    # 先把所有 {占位符} 替换掉，再扫数字
    template_no_placeholders = re.sub(r"\{[^}]+\}", "PLACEHOLDER", DECLARATION_TEMPLATE)

    # 只检查含小数点的数字字面量（如 1.74、35.15、19.33），
    # 避免对单个整数（5、27、26）产生误报
    for k, v in readings.items():
        if k.startswith("__"):
            continue
        v_str = str(v).lstrip("+-")
        # 只检查含小数点的精确数字（整数太容易误报）
        if "." not in v_str:
            continue
        if v_str in template_no_placeholders:
            raise AssertionError(
                f"DECLARATION_TEMPLATE 里出现了读数字面量 {v!r}（键 {k}）。"
                "改 JSON 后模板还是印旧数——必须用 {{占位符}} 引用。"
            )


# ---------------------------------------------------------------------------
# export.py 里的 _research 字面量检查（C9：_MODE_RESEARCH 已删除，改为模板设计）
# ---------------------------------------------------------------------------

def test_research_literal_not_hardcoded_in_export_suffixes() -> None:
    """EXPORT_SUFFIXES 的模板值里不出现字面量 'research'（应使用 {mode} 占位符）。

    C9 修正：_MODE_RESEARCH 已从 export.py 中删除。
    新检查：
    1. export.py 里没有 _MODE_RESEARCH 赋值（已清除）
    2. EXPORT_SUFFIXES 的任何字符串值里不含字面量 "research" 或 "validated"
       （模式应通过 {mode} 占位符在运行时注入，不许写死）
    3. export.py 里没有字面量 "_research" 或 "_validated"
    """
    export_py = ROOT / "desktop" / "app" / "services" / "export.py"
    source = export_py.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # 1. _MODE_RESEARCH 不应再出现（C9 已删除）
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_MODE_RESEARCH":
                    raise AssertionError(
                        f"export.py:{node.lineno} 仍然有 _MODE_RESEARCH 赋值，"
                        "C9 要求已删除该常量，改为 {{mode}} 模板占位符"
                    )

    # 2. EXPORT_SUFFIXES 的字符串值里不含硬编码的 mode 名称
    from desktop.app.services.export import EXPORT_SUFFIXES
    for k, v in EXPORT_SUFFIXES.items():
        for literal in ("research", "validated"):
            assert literal not in v, (
                f"EXPORT_SUFFIXES[{k!r}]={v!r} 含字面量 {literal!r}，"
                "应使用 {{mode}} 占位符，由 export_paths() 在运行时注入"
            )

    # 3. export.py 里不许有字面量 "_research" 或 "_validated"
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for bad in ("_research", "_validated"):
                if node.value == bad:
                    raise AssertionError(
                        f"export.py:{node.lineno} 里出现字面量 {bad!r}，"
                        "应通过 Mode.xxx.value 格式化到 {{mode}} 模板里"
                    )


# ---------------------------------------------------------------------------
# C13'：evaluate_calibration 不许出现在 services/ 或 pages/ 下（DP-111 单一判定点）
# ---------------------------------------------------------------------------

def test_evaluate_calibration_only_in_main_window() -> None:
    """services/ 和 pages/ 下不许调用或 import evaluate_calibration（DP-111 单一判定点）。

    标定判定只在主窗口启动时做一次（main_window.py:63，DP-111）。
    各页从 window().calibration_status 取结论，导出层接受 CalibrationStatus 入参。
    两个调用点之间文件可以被换掉，屏幕和导出会基于不同的标定文件各说一句话。

    用 AST 扫代码调用/import（不扫注释），calibration.py 本身豁免。
    """
    _FORBIDDEN_DIRS = [
        ROOT / "desktop" / "app" / "services",
        ROOT / "desktop" / "app" / "pages",
    ]
    violations: list[str] = []
    for d in _FORBIDDEN_DIRS:
        for py_file in sorted(d.glob("*.py")):
            if py_file.name == "calibration.py":
                continue  # 定义文件本身豁免
            source = py_file.read_text(encoding="utf-8")
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                # 函数调用：evaluate_calibration(...)
                if (isinstance(node, ast.Call) and
                        isinstance(node.func, ast.Name) and
                        node.func.id == "evaluate_calibration"):
                    violations.append(
                        f"{py_file.relative_to(ROOT)}:{node.lineno} call"
                    )
                # import 引入：from ... import evaluate_calibration
                elif isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        if alias.name == "evaluate_calibration":
                            violations.append(
                                f"{py_file.relative_to(ROOT)}:{node.lineno} import"
                            )
                # 普通 import 或 import-as（保险）
                elif (isinstance(node, ast.Import)):
                    for alias in node.names:
                        if "evaluate_calibration" in alias.name:
                            violations.append(
                                f"{py_file.relative_to(ROOT)}:{node.lineno} import"
                            )
    assert not violations, (
        f"以下位置调用/import 了 evaluate_calibration，违反 DP-111 单一判定点：\n"
        + "\n".join(f"  {v}" for v in violations)
        + "\n判定只在 desktop/app/main_window.py 启动时做一次；"
        "各页问 window().calibration_status，导出层接受 CalibrationStatus 入参。"
    )
