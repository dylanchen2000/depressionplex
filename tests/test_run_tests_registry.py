"""元守卫：让「这条测试到底跑了没有」这件事有机器管。

两次同型教训，同一个形状——**一条不会被执行的测试，长得和一条通过的测试一模一样**：

1. 2026-09-04：`test_g10_gates.py` 自 DP-034 起就躺在 tests/ 里，从未进模块表，
   runner 报"全绿"，那 7 个测试从未被执行 ⇒ `test_all_test_files_registered`。
2. 2026-09-14（DP-108 第三轮）：有人给 10 条测试加了 `monkeypatch` 参数。
   **本仓没有 pytest**（见 `run_tests.py` 头三行：沙箱里 pytest 所在的
   site-packages 有 I/O 故障，所以自带 runner），runner 是零参调用 `fn()`，
   于是那 10 条全部以 `TypeError: missing 1 required positional argument`
   收场——一条都没执行 ⇒ `test_no_fixture_style_signatures`。

第 2 条这次是**响的**失败（TypeError 会让 runner 变红），但它只是运气：
若 runner 当年写成「带参数就跳过」，那 10 条测试会安静地消失。
把它钉在这里，是不打算再依赖这种运气。
"""

from __future__ import annotations

import ast
from pathlib import Path

import run_tests


def test_all_test_files_registered() -> None:
    here = Path(__file__).resolve().parent
    on_disk = {p.stem for p in here.glob("test_*.py")}
    registered = set(run_tests.TEST_MODULES)
    missing = sorted(on_disk - registered)
    assert not missing, (
        f"这些测试模块没有登记进 run_tests.TEST_MODULES，runner 不会执行它们: {missing}"
    )


def test_no_phantom_entries() -> None:
    here = Path(__file__).resolve().parent
    on_disk = {p.stem for p in here.glob("test_*.py")}
    phantom = sorted(set(run_tests.TEST_MODULES) - on_disk)
    assert not phantom, f"TEST_MODULES 引用了不存在的测试文件: {phantom}"


def test_no_module_level_patches() -> None:
    """元守卫：tests/*.py 模块顶层一律不许有属性赋值（A7）。

    永不还原的 lambda（`video._resolve_ffmpeg_tool = lambda …`）会污染后续测试，
    导致守卫被邻居的导入副作用解除武装。本仓没有 pytest，不能用 fixture。
    必须在每个测试函数内部用 try/finally 或 mock 上下文管理器，不许留在模块顶层。

    A7 修复：不按根名字匹配（`video` 是 `from depressionplex import video as V` 的别名，
    旧版守卫认不出）。改成：tests/*.py 顶层一律不许属性赋值（X.y = …），不管 X 是什么。
    这个套件里没有任何一个正当理由需要它；真出现正当需求，那时再加白名单。
    """
    import ast
    import sys

    here = Path(__file__).resolve().parent
    violations = []

    for test_file in here.glob("test_*.py"):
        src = test_file.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(test_file))

        # 检查模块顶层（depth=0）的赋值语句，任何属性赋值（X.y = ...）都不许
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                # 提取赋值目标
                targets = []
                if isinstance(node, ast.Assign):
                    targets = node.targets
                elif isinstance(node, ast.AnnAssign) and node.value is not None:
                    targets = [node.target]
                elif isinstance(node, ast.AugAssign):
                    targets = [node.target]

                for target in targets:
                    # 任何属性赋值（X.y = ...）都不许，不管 X 是什么
                    if isinstance(target, ast.Attribute):
                        # 提取被赋值的属性链（用于错误消息）
                        attr_chain = []
                        node_walk = target
                        while isinstance(node_walk, ast.Attribute):
                            attr_chain.insert(0, node_walk.attr)
                            node_walk = node_walk.value
                        if isinstance(node_walk, ast.Name):
                            attr_chain.insert(0, node_walk.id)

                        violations.append(
                            f"{test_file.name}:{node.lineno}: "
                            f"模块顶层给 {'.'.join(attr_chain)} 赋值，会污染后续测试"
                        )

    assert not violations, (
        "这些测试模块在顶层给对象属性赋值（X.y = ...），"
        "永不还原的 patch 会污染后续测试。必须用 try/finally 或 mock 上下文管理器：\n"
        + "\n".join(violations)
    )


def test_no_fixture_style_signatures() -> None:
    """`def test_*` 一律**零参数**——本仓没有 pytest，fixture 不存在。

    runner 是 `fn()`：带参数的测试函数在这里不是「用了个 fixture」，
    而是「这条测试不会被执行」。`monkeypatch` / `tmp_path` / `capsys` 都不存在。

    替代办法（本仓在用的）：
    - 改环境变量：`unittest.mock.patch.dict("os.environ", {...})` 上下文管理器；
    - 临时文件：`tempfile.TemporaryDirectory()` / `NamedTemporaryFile` + `try/finally`；
    - 替换模块属性：`unittest.mock.patch("包.模块.名字", ...)` 上下文管理器。
      **不许在模块顶层直接赋值**——runner 单进程按序导入所有测试模块，
      顶层的替换永不还原，会把后面模块里的守卫一起架空（DP-108 H1）。
    """
    here = Path(__file__).resolve().parent
    offenders: list[str] = []
    for path in sorted(here.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        # 只看模块顶层的函数：runner 也只调这一层（`dir(mod)` 里的 test_*）
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            a = node.args
            names = [x.arg for x in (*a.posonlyargs, *a.args, *a.kwonlyargs)]
            if a.vararg:
                names.append("*" + a.vararg.arg)
            if a.kwarg:
                names.append("**" + a.kwarg.arg)
            if names:
                offenders.append(f"{path.name}:{node.lineno} {node.name}({', '.join(names)})")
    assert not offenders, (
        "这些测试函数带参数，本仓的 runner 是零参调用 `fn()`，它们**根本不会被执行**"
        "（本仓没有 pytest，fixture 不存在，见本文件 docstring 给的替代办法）：\n  "
        + "\n  ".join(offenders))
