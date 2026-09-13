"""桌面外壳边界守卫测试（DP-099 B1）。

这些测试用 AST 静态解析源码，确保外壳与引擎之间的进程边界不被突破。
绝对不许 import 任何 desktop.* 模块或 PySide6（本仓测试套件跑在没有 PySide6 的环境里）。

四条规则（架构文件 SPEC_产品化总体架构_v1.md §3.4）：
1. 进程边界：desktop/**/*.py 不许 import depressionplex / assay_core
2. 依赖白名单：第三方 import 只许 PySide6，其余必须是标准库
3. 禁 sys.path 魔法：不许出现 sys.path 的赋值/insert/append
4. 入口对齐 CI：desktop/main.py 与 .github/workflows/desktop-selftest.yml 必须对齐
"""

import ast
import sys
from pathlib import Path
from typing import Set

ROOT = Path(__file__).resolve().parent.parent

# Python 标准库白名单（3.11 标准库，不完全列举但覆盖常用模块）
STDLIB_MODULES = {
    # 核心内置
    "abc", "argparse", "asyncio", "base64", "collections", "contextlib",
    "copy", "dataclasses", "datetime", "decimal", "difflib", "enum",
    "functools", "hashlib", "heapq", "io", "itertools", "json",
    "logging", "math", "os", "pathlib", "pickle", "platform",
    "pprint", "queue", "random", "re", "shutil", "socket",
    "sqlite3", "string", "struct", "subprocess", "sys", "tempfile",
    "textwrap", "threading", "time", "traceback", "typing", "urllib",
    "uuid", "warnings", "weakref", "zipfile",
    # 特定于平台或 typing 扩展
    "typing_extensions", "__future__",
}


def collect_desktop_python_files() -> list[Path]:
    """收集 desktop/ 下所有 .py 文件。"""
    desktop_dir = ROOT / "desktop"
    if not desktop_dir.exists():
        return []
    return sorted(desktop_dir.rglob("*.py"))


def extract_imports_from_ast(file_path: Path) -> tuple[Set[str], Set[str]]:
    """用 AST 提取文件中的 import 语句。

    Returns:
        (顶层模块集合, 所有 from...import 的模块集合)
    """
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"))
    except SyntaxError:
        # 语法错误会在其他地方被发现，这里跳过
        return set(), set()

    top_level_imports = set()
    from_imports = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                # import foo.bar -> 记录顶层模块 "foo"
                top_module = alias.name.split(".")[0]
                top_level_imports.add(top_module)

        elif isinstance(node, ast.ImportFrom):
            if node.module:
                # from foo.bar import baz -> 记录 "foo.bar"
                from_imports.add(node.module)
                # 也记录顶层模块 "foo"
                top_module = node.module.split(".")[0]
                top_level_imports.add(top_module)

    return top_level_imports, from_imports


def check_sys_path_manipulation(file_path: Path) -> list[str]:
    """检查文件中是否有 sys.path 的赋值/insert/append。

    Returns:
        违规行号列表（字符串形式）
    """
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []

    violations = []

    for node in ast.walk(tree):
        # 检查赋值：sys.path = ...
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute):
                    if (isinstance(target.value, ast.Name) and
                        target.value.id == "sys" and
                        target.attr == "path"):
                        violations.append(f"line {node.lineno}")

        # 检查方法调用：sys.path.insert(...) 或 sys.path.append(...)
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                if (isinstance(node.func.value, ast.Attribute) and
                    isinstance(node.func.value.value, ast.Name) and
                    node.func.value.value.id == "sys" and
                    node.func.value.attr == "path" and
                    node.func.attr in ("insert", "append")):
                    violations.append(f"line {node.lineno}")

    return violations


def test_no_engine_imports():
    """规则 1：desktop/**/*.py 不许 import depressionplex / assay_core。

    外壳与引擎之间是进程边界，GUI 只起子进程 + 读文件。
    """
    files = collect_desktop_python_files()
    if not files:
        # desktop/ 尚不存在，本测试通过（B1 未交付时）
        return

    violations = []
    forbidden_prefixes = ["depressionplex", "assay_core"]

    for file_path in files:
        top_level, from_modules = extract_imports_from_ast(file_path)

        # 检查 import depressionplex / import assay_core
        for module in top_level:
            if module in forbidden_prefixes:
                violations.append(f"{file_path.relative_to(ROOT)}: import {module}")

        # 检查 from depressionplex... / from assay_core...
        for module in from_modules:
            top_module = module.split(".")[0]
            if top_module in forbidden_prefixes:
                violations.append(
                    f"{file_path.relative_to(ROOT)}: from {module} import ...")

    assert not violations, (
        "外壳不许 import 引擎包（进程边界，见架构 §3.4）：\n" +
        "\n".join(violations)
    )


def test_third_party_whitelist():
    """规则 2：desktop/**/*.py 的第三方 import 只许 PySide6。

    标准库随意用，但第三方库只许 PySide6（不许引入 onnxruntime / opencv / torch 等）。
    """
    files = collect_desktop_python_files()
    if not files:
        return

    violations = []

    for file_path in files:
        top_level, _ = extract_imports_from_ast(file_path)

        for module in top_level:
            # 跳过标准库
            if module in STDLIB_MODULES:
                continue

            # 跳过本项目包
            if module in ("desktop", "depressionplex", "assay_core"):
                continue

            # 只允许 PySide6
            if module != "PySide6":
                violations.append(
                    f"{file_path.relative_to(ROOT)} line ?: import {module} "
                    f"(第三方库只许 PySide6)"
                )

    assert not violations, (
        "外壳第三方依赖只许 PySide6（见架构 §3.2）：\n" +
        "\n".join(violations)
    )


def test_no_sys_path_magic():
    """规则 3：desktop/**/*.py 不许出现 sys.path 的赋值/insert/append。

    sys.path 魔法会导致导入不可预测，且 PyInstaller 打包后无效。
    """
    files = collect_desktop_python_files()
    if not files:
        return

    violations = []

    for file_path in files:
        file_violations = check_sys_path_manipulation(file_path)
        if file_violations:
            violations.append(
                f"{file_path.relative_to(ROOT)}: sys.path 魔法 at {', '.join(file_violations)}"
            )

    assert not violations, (
        "外壳不许用 sys.path 魔法（见架构 §3.4）：\n" +
        "\n".join(violations)
    )


def test_entry_point_aligns_with_ci():
    """规则 4：desktop/main.py 与 CI workflow 必须对齐。

    desktop/main.py 里必须出现 '--self-test'
    .github/workflows/desktop-selftest.yml 里必须出现 '-m desktop.main --self-test'

    两边任何一边改了名字，这条测试就该红。
    """
    main_py = ROOT / "desktop" / "main.py"
    workflow = ROOT / ".github" / "workflows" / "desktop-selftest.yml"

    # desktop/main.py 必须存在且包含 '--self-test'
    assert main_py.exists(), "desktop/main.py 不存在（B1 未交付）"
    main_content = main_py.read_text()
    assert "'--self-test'" in main_content or '"--self-test"' in main_content, (
        "desktop/main.py 缺少 '--self-test' 参数（自检入口）"
    )

    # workflow 必须存在且包含 '-m desktop.main --self-test'
    assert workflow.exists(), "desktop-selftest.yml 不存在（CI 未就绪）"
    workflow_content = workflow.read_text()
    assert "desktop.main" in workflow_content, (
        "desktop-selftest.yml 缺少 'desktop.main'（入口模块）"
    )
    assert "--self-test" in workflow_content, (
        "desktop-selftest.yml 缺少 '--self-test' 参数"
    )
