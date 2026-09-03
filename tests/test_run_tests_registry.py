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
