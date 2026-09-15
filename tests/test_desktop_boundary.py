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
import re
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


def _desktop_deps() -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """外壳第三方依赖的**唯一权威清单**：`pyproject.toml`（DP-114）。

    返回 `({分发名: 需求串}, {分发名: {module, scope}})`。

    为什么不在这里手抄一份白名单：这条守卫原来写死 `module != "PySide6"`，
    而架构 §3.2 的依赖表里 `openpyxl==3.1.5` 早就写着了。于是 B6 按架构文件把
    xlsx 导出做出来，撞在一条比架构文件更严的守卫上——**这时候最省事的一步永远是
    把守卫改松**，而那一步会把「外壳只许 PySide6 + 一个受限的 openpyxl」这条边界
    换成「外壳想 import 什么都行」。清单只留一份，谁要加依赖就去改那一份。
    """
    import tomllib

    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    reqs = data["project"]["optional-dependencies"]["desktop"]
    declared = {}
    for r in reqs:
        name, _, ver = r.partition("==")
        assert ver, f"外壳依赖必须钉死版本，`{r}` 不是 `名==版本`（>= 等于客户拿到一个我们没测过的库）"
        declared[name] = r
    scopes = data["tool"]["depressionplex"]["desktop-imports"]
    missing = sorted(set(declared) - set(scopes))
    extra = sorted(set(scopes) - set(declared))
    assert not missing, f"这些外壳依赖没有 import 名/作用域声明：{missing}（pyproject 的 [tool.depressionplex.desktop-imports]）"
    assert not extra, f"这些 import 声明没有对应的已声明依赖：{extra}（声明了作用域却没人装它）"
    return declared, scopes


def _build_pins() -> dict[str, str]:
    """构建期/引擎侧钉子的**唯一权威位置**：`pyproject.toml` 的
    `[tool.depressionplex.build-pins]`（DP-114 的延伸，DP-108 A9c）。

    返回 `{分发名: "名==版本"}`，形状与 `_desktop_deps()` 的第一个返回值一致，
    这样两边可以并起来一起对账。

    为什么要单独开一处，而不是把它们塞进 `desktop` extra：那份清单回答的是
    「外壳允许 import 什么」，而 numpy 与 pyinstaller 都不是外壳的依赖
    （外壳不许 import 引擎，见规则 1）。为什么不塞进 `[project].dependencies`：
    那里的 `numpy>=1.24` 是「引擎能跑起来的下限」，这里的 `1.26.4` 是
    「G7/G8 那批读数是在哪个版本上得到的」——两个不同的事实。

    这一处存在的直接原因：在它之前，`numpy==1.26.4` 只活在架构 §3.2 那张表里，
    而这条守卫把 numpy 从对账里 `pop` 掉了（理由是它不是外壳依赖）。于是那个数字
    **待在一张有守卫的表里，自己正好在守卫的豁免名单上**——看着被守着，其实没有。
    2026-09-14 第一次真构建就装进了 numpy 2.4.6（DP-115）。
    """
    import tomllib

    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pins = data["tool"]["depressionplex"]["build-pins"]
    assert pins, "[tool.depressionplex.build-pins] 是空的——钉子被搬走了，这不是通过"
    out = {}
    for name, ver in pins.items():
        assert isinstance(ver, str) and ver and not any(
            c in ver for c in "<>=!~*"), (
            f"build-pins 里 `{name} = {ver!r}` 必须是裸版本号（如 \"1.26.4\"）："
            "范围/通配等于「重建会得到另一个二进制」")
        out[name] = f"{name}=={ver}"
    return out


def test_third_party_whitelist():
    """规则 2：desktop/**/*.py 的第三方 import 必须在 pyproject 的 `desktop` 清单里，
    且必须出现在允许它出现的那个文件里。

    标准库随意用。第三方**只有两个**：PySide6（全仓可用）与 openpyxl（只许导出那一层）。
    onnxruntime / opencv / torch 一律不许——它们不在清单里，加进来要先改 pyproject。
    """
    files = collect_desktop_python_files()
    assert files, "desktop/ 下一个 .py 都没扫到——目录被删或 glob 写错，这不是通过"

    _, scopes = _desktop_deps()
    allowed = {v["module"]: v["scope"] for v in scopes.values()}

    violations = []

    for file_path in files:
        top_level, _ = extract_imports_from_ast(file_path)
        rel = file_path.relative_to(ROOT).as_posix()

        for module in top_level:
            # 跳过标准库
            if module in STDLIB_MODULES:
                continue

            # 跳过本项目包（`depressionplex` / `assay_core` 由规则 1 单独报，
            # 免得同一处违规在两条守卫里各报一遍、掩掉真正的措辞）
            if module in ("desktop", "depressionplex", "assay_core"):
                continue

            if module not in allowed:
                violations.append(
                    f"{rel}: import {module}"
                    f"（不在 pyproject 的 desktop 清单里：{sorted(allowed)}）")
                continue

            scope = allowed[module]
            if scope != "*" and rel != scope:
                violations.append(
                    f"{rel}: import {module}（这个包只许出现在 {scope}）")

    assert not violations, (
        "外壳第三方依赖只认 pyproject 的 `desktop` 清单（架构 §3.2 是它的镜像）：\n" +
        "\n".join(violations)
    )


def test_dependency_table_in_spec_matches_pyproject():
    """架构 §3.2 那张依赖表必须与 pyproject 的 `desktop` 清单**逐字相等**。

    §3.2 是给人读的那一份：道俊、外派 agent、将来接手的人都从那里知道「外壳能用什么」。
    它和 pyproject 分叉一次，就会有人照着过期的那份写代码——DP-110 的 B6 就是这么撞上
    白名单守卫的（§3.2 早写着 openpyxl，守卫却只认 PySide6）。

    比对范围是 **`desktop` extra ∪ `build-pins`**（DP-108 A9c）。numpy 曾经被
    `pop` 出对账（理由：它是引擎的依赖不是外壳的），结果那一行成了全仓唯一
    「待在有守卫的表里、却在守卫豁免名单上」的数字，2026-09-14 的真构建因此装进了
    numpy 2.4.6。豁免一个数字，等于把它从守卫里删掉——**要么进对账，要么别写在这张表里**。
    """
    declared, _ = _desktop_deps()
    pins = _build_pins()
    spec = (ROOT / "docs/SPEC_产品化总体架构_v1.md").read_text(encoding="utf-8")
    # 先切出 §3.2 这一节（到下一个同级标题为止），再取节内**第一个**代码块。
    # 不写成「标题紧跟代码块」：那样在标题与代码块之间加一段说明文字就会让守卫红，
    # 而假违规的下一步永远是有人把守卫删掉。
    section = re.search(r"### 3\.2 [^\n]*\n(.*?)(?=\n### )", spec, re.S)
    assert section, "架构文件里找不到 §3.2 这一节——它被改名或删了，这不是通过"
    block = re.search(r"```\n(.*?)```", section.group(1), re.S)
    assert block, "§3.2 里找不到依赖清单代码块——清单被删了，这不是通过"
    pinned = dict(re.findall(r"^([A-Za-z0-9_.\-]+)==([^\s#]+)", block.group(1), re.M))
    in_spec = {f"{k}=={v}" for k, v in pinned.items()}
    authoritative = set(declared.values()) | set(pins.values())
    assert in_spec == authoritative, (
        "架构 §3.2 的依赖表与 pyproject 不一致（清单只许有一份）：\n"
        f"  §3.2：      {sorted(in_spec)}\n"
        f"  desktop：   {sorted(declared.values())}\n"
        f"  build-pins：{sorted(pins.values())}")


def test_ci_installs_the_pinned_desktop_deps():
    """`tests.yml` 装的版本必须与 pyproject 的 pin **一致**（PySide6 例外）。

    两个方向都要防：
    - 装了别的版本 ⇒ CI 验的不是产品用的那一份；
    - **根本不装** ⇒ 依赖那个包的守卫在 CI 上必然红，而下一步永远是有人把守卫删掉。
      DP-110 第一轮就是这样：三条 xlsx 守卫在 CI 上以 `ModuleNotFoundError` 收场。

    PySide6 是**唯一的例外，而且是刻意的**：测试套件必须能在没有 PySide6 的环境里跑完
    （`test_no_pyside_import_in_tests` 守着这条），外壳的运行期验证走
    `desktop-selftest.yml`，那边单独装 `PySide6==6.7.3`。
    """
    declared, _ = _desktop_deps()
    wf = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")
    missing = [r for name, r in declared.items() if name != "PySide6" and r not in wf]
    assert not missing, (
        f"`tests.yml` 没有装这些钉死的依赖：{missing}\n"
        "（不装的后果不是「少测一点」，而是相关守卫在 CI 上永远红，然后被删掉）")
    sf = (ROOT / ".github/workflows/desktop-selftest.yml").read_text(encoding="utf-8")
    assert declared["PySide6"] in sf, (
        f"`desktop-selftest.yml` 必须装 {declared['PySide6']}（版本也要与 pyproject 一致）")
    # export render 步骤要装 openpyxl（版本与 pyproject 一致）
    assert declared["openpyxl"] in sf, (
        f"`desktop-selftest.yml` 必须装 {declared['openpyxl']}（export render 步骤需要，"
        "版本必须与 pyproject 的 pin 一致）")


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
    """规则 7：页面清单只有 `PAGE_ORDER` 一份，七个，且每个类真的存在且不重名。

    自检打印的 `pages=7` 只有在清单本身被锁住时才有意义——否则删掉一页，
    自检照样打印 `pages=6` 并退 0，CI 全绿。

    B2 改动：扫 desktop/app/pages/*.py 全部文件收类名，并断言 PAGE_ORDER 里的
    每个类名只定义一次（重名即红）。守卫只许变严，不许变松。
    """
    mw = ROOT / "desktop/app/main_window.py"
    assert mw.exists(), "main_window.py 不存在"

    pages_dir = ROOT / "desktop/app/pages"
    assert pages_dir.exists() and pages_dir.is_dir(), "desktop/app/pages/ 不存在"

    # 收集 PAGE_ORDER
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

    # 原有的两条断言（不许动）
    assert len(entries) == 7, f"页面应为 7 个，实为 {len(entries)}：{entries}"
    assert len({n for n, _ in entries}) == 7, f"显示名有重复：{entries}"

    # 扫 desktop/app/pages/*.py 全部文件收类名
    page_files = sorted(pages_dir.glob("*.py"))
    assert page_files, "desktop/app/pages/ 下没有 .py 文件"

    class_definitions = {}  # {类名: [文件路径列表]}
    for file_path in page_files:
        if file_path.name == "__init__.py":
            continue
        for node in ast.walk(_parse(file_path)):
            if isinstance(node, ast.ClassDef):
                if node.name not in class_definitions:
                    class_definitions[node.name] = []
                class_definitions[node.name].append(file_path)

    # 检查 PAGE_ORDER 里的类是否都存在
    required_classes = {c for _, c in entries}
    missing = required_classes - set(class_definitions.keys())
    assert not missing, f"PAGE_ORDER 指到不存在的类：{missing}"

    # 检查 PAGE_ORDER 里的类是否有重名（只定义一次）
    duplicates = {cls: files for cls, files in class_definitions.items()
                  if cls in required_classes and len(files) > 1}
    assert not duplicates, \
        f"PAGE_ORDER 里的类名有重复定义：{duplicates}"


def test_page_classes_resolved_in_one_place():
    """规则 9：「哪个类是哪一页」只许有一个解析器 —— `main_window.PAGE_CLASSES`。

    这条是补 DP-101 那次 CI 全红的：`main.py` 的自检原来自己
    `getattr(placeholders, cls_name)`，与主窗口的类表并列成了第二个解析器。
    B2 把 `NewExperimentPage` 从 `placeholders.py` 搬进自己的模块（每一页最终都要走这条路），
    主窗口用新类、自检还在老模块里找，于是自检以 `AttributeError` **崩掉退 1**，
    而不是「不通过退 2」——ubuntu 与 windows 两个平台同时红，归因还容易先怀疑新页面本身。

    三条断言，静态就能判，所以它会在 Tests 工作流里先红，不用等 Desktop Self-Test。

    前两条的写法是被变异测试逼出来的（第一版两条都是装饰）：
    - 查 import 不能用 `extract_imports_from_ast` 的**顶层**集合——`from desktop.app.pages
      import placeholders` 在那里只留下 `desktop`，把 bug 原样种回去照样通过；
    - 查「用了类表」不能用子串 `"PAGE_CLASSES" in src`——import 行和这段注释里本来就有这个词，
      改成 `globals().get(cls_name)` 也恒真。
    """
    main_py = ROOT / "desktop/main.py"
    mw = ROOT / "desktop/app/main_window.py"
    assert main_py.exists() and mw.exists()

    main_tree = _parse(main_py)

    # 1. main.py 不许碰任何页面模块（查**完整点分名**，两种 import 形式都要盖住）
    touched = set()
    for node in ast.walk(main_tree):
        if isinstance(node, ast.Import):
            touched |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            touched.add(node.module)
            touched |= {f"{node.module}.{a.name}" for a in node.names}
    bad = {m for m in touched if m.startswith("desktop.app.pages")}
    assert not bad, f"main.py 不许 import 页面模块（第二个解析器就是这么长出来的）：{sorted(bad)}"

    # 2. self_test() 必须**直接读** PAGE_CLASSES，且不许用任何别的方式按名字找类
    fn = next((n for n in ast.walk(main_tree)
               if isinstance(n, ast.FunctionDef) and n.name == "self_test"), None)
    assert fn is not None, "desktop/main.py 里找不到 self_test()"
    assert any(isinstance(n, ast.Name) and n.id == "PAGE_CLASSES" for n in ast.walk(fn)), \
        "self_test() 必须直接从 main_window.PAGE_CLASSES 取类"
    sneaky = sorted({n.func.id for n in ast.walk(fn)
                     if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                     and n.func.id in ("getattr", "globals", "vars", "eval", "import_module")})
    assert not sneaky, f"self_test() 里出现了按名字找类的旁路：{sneaky}（类表是唯一解析器）"

    # 3. PAGE_CLASSES 的键集合必须与 PAGE_ORDER 的类名集合逐个相等
    tree = _parse(mw)
    def _assign(name):
        return next((n for n in ast.walk(tree)
                     if isinstance(n, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)), None)

    order = _assign("PAGE_ORDER")
    table = _assign("PAGE_CLASSES")
    assert order is not None and table is not None, "PAGE_ORDER / PAGE_CLASSES 必须都是模块级赋值"
    assert isinstance(table.value, ast.Dict), "PAGE_CLASSES 必须是字典字面量（好静态核）"

    want = {item.elts[1].value for item in order.value.elts}
    got = {k.value for k in table.value.keys if isinstance(k, ast.Constant)}
    assert got == want, (
        f"PAGE_CLASSES 与 PAGE_ORDER 脱节："
        f"表里多 {sorted(got - want)}、少 {sorted(want - got)}"
    )


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
