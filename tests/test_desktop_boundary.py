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

# 标准库判定**用解释器自己的清单**（3.10+ 的 `sys.stdlib_module_names`），不手抄白名单。
# 手抄的表迟早漏一个（`csv` / `shlex` / `signal` / `webbrowser` …），漏了就变成假违规，
# 而假违规的下一步永远是有人把守卫改松——那才是真损失。
STDLIB_MODULES = frozenset(sys.stdlib_module_names) | {"__future__"}


def collect_desktop_python_files() -> list[Path]:
    """收集 desktop/ 下所有 .py 文件。

    **B1 已交付，所以「一个文件都没收到」本身就是故障**（目录被删、或 rglob 写错），
    不许当成通过：调用方一律先 `assert files`。本仓吃过三次同型教训
    （DP-069 / DP-071 / DP-076，以及 DP-098 里我自己改掉的两条）——
    **能被跳过的守卫等于装饰。**
    """
    return sorted((ROOT / "desktop").rglob("*.py"))


def _parse(file_path: Path) -> ast.Module:
    """解析一个文件。**语法错误直接让守卫红**，不许静默跳过。

    原写法 `except SyntaxError: return set(), set()` 的后果是：一个语法坏掉的文件
    在三条 import 守卫里全部「通过」。它只有被 main 导入时才会在自检里暴露；
    一个还没被导入的模块可以带着违规安静躺在仓里。
    """
    src = file_path.read_text(encoding="utf-8")
    try:
        return ast.parse(src)
    except SyntaxError as e:
        raise AssertionError(f"{file_path.relative_to(ROOT)} 语法错误：{e}") from e


def extract_imports_from_ast(file_path: Path) -> tuple[Set[str], Set[str]]:
    """用 AST 提取文件中的 import 语句。

    Returns:
        (顶层模块集合, 所有 from...import 的模块集合)

    相对导入（`from .paths import ...`）记成 `.模块名`：外壳内部约定用**绝对导入**
    （`from desktop.app.… import`，见架构 §3.4 的入口裁决），
    所以相对导入由 `test_absolute_imports_only` 单独判违规，
    而不是在这里被误当成第三方包 `paths`。
    """
    tree = _parse(file_path)

    top_level_imports = set()
    from_imports = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                # import foo.bar -> 记录顶层模块 "foo"
                top_module = alias.name.split(".")[0]
                top_level_imports.add(top_module)

        elif isinstance(node, ast.ImportFrom):
            if node.level:                      # from . / from .foo
                from_imports.add("." * node.level + (node.module or ""))
                continue
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
    tree = _parse(file_path)

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
    assert files, "desktop/ 下一个 .py 都没扫到——目录被删或 glob 写错，这不是通过"

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
    assert files, "desktop/ 下一个 .py 都没扫到——目录被删或 glob 写错，这不是通过"

    violations = []

    for file_path in files:
        top_level, _ = extract_imports_from_ast(file_path)

        for module in top_level:
            # 跳过标准库
            if module in STDLIB_MODULES:
                continue

            # 跳过本项目包（`depressionplex` / `assay_core` 由规则 1 单独报，
            # 免得同一处违规在两条守卫里各报一遍、掩掉真正的措辞）
            if module in ("desktop", "depressionplex", "assay_core"):
                continue

            # 只允许 PySide6
            if module != "PySide6":
                violations.append(
                    f"{file_path.relative_to(ROOT)}: import {module}"
                    f"（第三方库只许 PySide6）"
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
    assert files, "desktop/ 下一个 .py 都没扫到——目录被删或 glob 写错，这不是通过"

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

    # workflow 必须存在且**整条命令**与架构 §3.4 定的形式一致。
    # 只分别检查 "desktop.main" 与 "--self-test" 是不够的：`python desktop/main.py`
    # 这种写法也能同时命中两个子串，而它和 `-m` 的导入语义不同（前者不把仓根当包根，
    # `from desktop.app.…` 会直接 ImportError）。
    assert workflow.exists(), "desktop-selftest.yml 不存在（CI 未就绪）"
    workflow_content = workflow.read_text()
    assert "-m desktop.main --self-test" in workflow_content, (
        "desktop-selftest.yml 里的自检命令不是 `python -m desktop.main --self-test`"
    )


def test_absolute_imports_only():
    """规则 5：外壳内部一律绝对导入（`from desktop.app.… import`），不许相对导入。

    这是架构 §3.4 入口裁决的另一半：入口写死 `python -m desktop.main`，
    包名固定为 `desktop`，绝对导入在冻结（PyInstaller）与源码运行下行为一致；
    混用相对导入会让「同一个模块被导入两次」这类问题只在打包后出现。
    """
    files = collect_desktop_python_files()
    assert files, "desktop/ 下一个 .py 都没扫到——目录被删或 glob 写错，这不是通过"

    violations = []
    for file_path in files:
        _, from_modules = extract_imports_from_ast(file_path)
        for module in from_modules:
            if module.startswith("."):
                violations.append(f"{file_path.relative_to(ROOT)}: from {module} import ...")

    assert not violations, (
        "外壳内部只许绝对导入 `from desktop.app.… import`（见架构 §3.4）：\n" +
        "\n".join(violations)
    )


def test_self_test_never_enters_event_loop():
    """规则 6：`--self-test` 路径上不许出现 `show()` / `exec()`。

    自检必须构造完页面就返回。一旦误调 `exec()` 就进 Qt 事件循环、任务挂死，
    CI 的 `timeout-minutes` 会替我们兜住，但那是烧掉 10 分钟 runner 才发现
    （windows 还按 2× 计费）。这条在**语法层**直接把它拦掉。
    """
    main_py = ROOT / "desktop" / "main.py"
    assert main_py.exists(), "desktop/main.py 不存在"
    tree = _parse(main_py)

    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "self_test"), None)
    assert fn is not None, "desktop/main.py 里找不到 self_test()"

    bad = [f"line {n.lineno}: .{n.func.attr}()"
           for n in ast.walk(fn)
           if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
           and n.func.attr in ("show", "exec", "exec_")]
    assert not bad, "self_test() 里出现了会起窗/进事件循环的调用：\n" + "\n".join(bad)


def test_seven_pages_declared_once():
    """规则 7：页面清单只有 `PAGE_ORDER` 一份，七个，且每个类真的存在。

    自检打印的 `pages=7` 只有在清单本身被锁住时才有意义——否则删掉一页，
    自检照样打印 `pages=6` 并退 0，CI 全绿。
    """
    mw = ROOT / "desktop/app/main_window.py"
    ph = ROOT / "desktop/app/pages/placeholders.py"
    assert mw.exists() and ph.exists(), "main_window.py / placeholders.py 不存在"

    order = next((n for n in ast.walk(_parse(mw))
                  if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == "PAGE_ORDER"
                          for t in n.targets)), None)
    assert order is not None, "main_window.py 里找不到 PAGE_ORDER"
    assert isinstance(order.value, ast.Tuple), "PAGE_ORDER 必须是元组字面量（好静态核）"

    entries = []
    for item in order.value.elts:
        assert isinstance(item, ast.Tuple) and len(item.elts) == 2, \
            "PAGE_ORDER 每项必须是 (显示名, 类名) 两元组"
        name, cls = item.elts
        assert isinstance(name, ast.Constant) and isinstance(cls, ast.Constant), \
            "PAGE_ORDER 里必须是字符串字面量"
        entries.append((name.value, cls.value))

    assert len(entries) == 7, f"页面应为 7 个，实为 {len(entries)}：{entries}"
    assert len({n for n, _ in entries}) == 7, f"显示名有重复：{entries}"

    defined = {n.name for n in ast.walk(_parse(ph)) if isinstance(n, ast.ClassDef)}
    missing = [c for _, c in entries if c not in defined]
    assert not missing, f"PAGE_ORDER 指到不存在的类：{missing}"


def test_path_getters_have_no_side_effects():
    """规则 8：`paths.py` 里只许 `_ensure` 一处调 `mkdir`。

    取路径的函数一旦顺手建目录，「打印诊断」就变成「往客户机 home 里写东西」，
    而自检是诊断。真要建目录的调用点显式传 `create=True`。
    """
    paths_py = ROOT / "desktop/app/utils/paths.py"
    assert paths_py.exists(), "desktop/app/utils/paths.py 不存在"

    offenders = []
    for fn in ast.walk(_parse(paths_py)):
        if not isinstance(fn, ast.FunctionDef) or fn.name == "_ensure":
            continue
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "mkdir"):
                offenders.append(f"{fn.name}() line {node.lineno}")
    assert not offenders, "取路径的函数里出现了 mkdir：\n" + "\n".join(offenders)
