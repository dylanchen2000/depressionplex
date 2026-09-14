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
# EXPORT_SUFFIXES 唯一来源
# ---------------------------------------------------------------------------

def test_export_suffixes_contain_mode_value() -> None:
    """EXPORT_SUFFIXES 的 xlsx 后缀里应含 research（来自 Mode.RESEARCH.value）。"""
    from desktop.app.services.export import EXPORT_SUFFIXES, _MODE_RESEARCH
    assert _MODE_RESEARCH == Mode.RESEARCH.value, (
        "_MODE_RESEARCH 应等于 Mode.RESEARCH.value"
    )
    # xlsx 后缀应包含 research
    assert _MODE_RESEARCH in EXPORT_SUFFIXES["xlsx"], (
        f"EXPORT_SUFFIXES['xlsx']={EXPORT_SUFFIXES['xlsx']!r} 里应含 research"
    )
    assert ".xlsx" in EXPORT_SUFFIXES["xlsx"]
    assert ".pdf" in EXPORT_SUFFIXES["pdf"]
    assert ".zip" in EXPORT_SUFFIXES["audit_zip"]


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
# export.py 里的 _research 字面量检查（变异：写死 → 红）
# ---------------------------------------------------------------------------

def test_research_literal_not_hardcoded_in_export_suffixes() -> None:
    """EXPORT_SUFFIXES 的值里不出现字面量 'research'（应该是通过 Mode.RESEARCH.value 构造）。

    包含两项检查：
    1. EXPORT_SUFFIXES 里没有 \"_research\" 字面量
    2. _MODE_RESEARCH 的赋值不是字符串常量（必须是 Mode.RESEARCH.value 这样的属性访问）
    """
    export_py = ROOT / "desktop" / "app" / "services" / "export.py"
    source = export_py.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # 检查 _MODE_RESEARCH 的赋值方式：RHS 不许是字符串常量
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_MODE_RESEARCH":
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        raise AssertionError(
                            f"export.py:{node.lineno} _MODE_RESEARCH 被赋值为字符串字面量 "
                            f"{node.value.value!r}，必须通过 Mode.RESEARCH.value 派生，"
                            "不许写死任何字符串"
                        )

    # 找 EXPORT_SUFFIXES 的赋值
    # 检查：没有任何字符串常量直接是 "_research"（允许通过变量拼）
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value == "_research":
                raise AssertionError(
                    f"export.py:{node.lineno} 里出现字面量 \"_research\"，"
                    "应该通过 Mode.RESEARCH.value 构造"
                )
