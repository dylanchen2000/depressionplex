#!/usr/bin/env python3
"""极简测试运行器。

沙箱里 pytest 所在的 site-packages 有 I/O 故障，故自带 runner。
纯 numpy 依赖，跑法：  python3 run_tests.py
"""

from __future__ import annotations

import importlib
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

TEST_MODULES = ("test_bouts", "test_rad", "test_geometry", "test_segment",
                "test_rules", "test_validity", "test_g10_gates",
                "test_primitives", "test_annotate",
                "test_scorer_disagreement", "test_human_agreement",
                "test_lovo_cv", "test_trial_report",
                "test_maskseq", "test_video", "test_runner",
                "test_analyze_cli", "test_timeline", "test_timer_tool",
                "test_csi_fst_import", "test_ci_workflows",
                "test_desktop_boundary", "test_progress_runjson",
                # 元守卫：新增 tests/test_*.py 必须同时进这张表，否则此测试变红
                "test_run_tests_registry")


def main() -> int:
    passed: list[str] = []
    failed: list[tuple[str, str]] = []

    for mod_name in TEST_MODULES:
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            failed.append((f"{mod_name} (import)", traceback.format_exc()))
            continue

        for name in sorted(dir(mod)):
            if not name.startswith("test_"):
                continue
            fn = getattr(mod, name)
            if not callable(fn):
                continue
            label = f"{mod_name}.{name}"
            try:
                fn()
                passed.append(label)
                print(f"  PASS  {label}")
            except Exception:
                failed.append((label, traceback.format_exc()))
                print(f"  FAIL  {label}")

    print()
    print(f"通过 {len(passed)}  失败 {len(failed)}")
    if failed:
        print()
        for label, tb in failed:
            print("=" * 70)
            print(label)
            print("-" * 70)
            print(tb)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
