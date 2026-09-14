"""元守卫：tests/test_*.py 必须全部登记进 run_tests.TEST_MODULES。

教训（2026-09-04 自查发现）：`test_g10_gates.py` 自 DP-034 起就躺在 tests/ 里，
从未进模块表——runner 报"全绿"，但那 7 个测试从未被执行。人工维护的表必须有
机器守卫：漏登记 = 本测试变红，孤儿测试与引用不存在的模块都拦。
"""

from __future__ import annotations

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
    """元守卫：tests/*.py 模块顶层不许给 depressionplex.* / desktop.* 赋属性（H1）。

    永不还原的 lambda（`video._resolve_ffmpeg_tool = lambda …`）会污染后续测试，
    导致守卫被邻居的导入副作用解除武装。本仓没有 pytest，不能用 fixture。
    必须在每个测试函数内部用 try/finally 或 mock 上下文管理器，不许留在模块顶层。
    """
    import ast
    import sys

    here = Path(__file__).resolve().parent
    violations = []

    for test_file in here.glob("test_*.py"):
        src = test_file.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(test_file))

        # 检查模块顶层（depth=0）的赋值语句
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                # 检查赋值目标是否是 depressionplex.* 或 desktop.* 的属性
                targets = []
                if isinstance(node, ast.Assign):
                    targets = node.targets
                elif isinstance(node, ast.AnnAssign) and node.value is not None:
                    targets = [node.target]
                elif isinstance(node, ast.AugAssign):
                    targets = [node.target]

                for target in targets:
                    if isinstance(target, ast.Attribute):
                        # 递归提取属性链的根
                        root = target
                        while isinstance(root.value, ast.Attribute):
                            root = root.value
                        if isinstance(root.value, ast.Name):
                            root_name = root.value.id
                            if root_name in ("depressionplex", "desktop"):
                                violations.append(
                                    f"{test_file.name}:{node.lineno}: "
                                    f"模块顶层给 {root_name}.* 赋值，会污染后续测试"
                                )

    assert not violations, (
        "这些测试模块在顶层给 depressionplex.* / desktop.* 赋属性，"
        "永不还原的 patch 会污染后续测试。必须用 try/finally 或 mock 上下文管理器：\n"
        + "\n".join(violations)
    )
