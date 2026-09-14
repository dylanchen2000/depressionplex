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
