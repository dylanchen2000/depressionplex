#!/usr/bin/env python3
"""变异测试：验证 C2 的 4 条守卫（手动脚本，不依赖 pytest 导入）。

每条守卫必须能检测到对应的违规变异。
"""
import ast
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.resolve()


def run_single_test(test_name: str) -> bool:
    """运行单个测试，返回是否通过（True = GREEN，False = RED）。"""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", f"tests/test_packaging_contract.py::{test_name}", "-xvs"],
        cwd=ROOT,
        capture_output=True,
        timeout=10,
    )
    return result.returncode == 0


def mutate_spec_to_init() -> None:
    """变异 C2.1：把 spec 入口改回 __init__.py。"""
    spec_file = ROOT / "packaging" / "build_analyzer_windows.spec"
    content = spec_file.read_text(encoding="utf-8")
    content = content.replace('["analyzer_entry.py"]', '["../depressionplex/cli/__init__.py"]')
    spec_file.write_text(content, encoding="utf-8")


def mutate_entry_add_print() -> None:
    """变异 C2.2：在入口脚本加一条 print。"""
    entry_file = ROOT / "packaging" / "analyzer_entry.py"
    content = entry_file.read_text(encoding="utf-8")
    # 在 import sys 后面加一条 print
    content = content.replace("import sys\n", "import sys\nprint('hello')\n")
    entry_file.write_text(content, encoding="utf-8")


def mutate_cli_main_no_force_utf8() -> None:
    """变异 C2.3：把 force_utf8() 移到后面。"""
    cli_init = ROOT / "depressionplex" / "cli" / "__init__.py"
    tree = ast.parse(cli_init.read_text(encoding="utf-8"), filename=str(cli_init))

    # 找到 main() 函数
    main_func = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            main_func = node
            break

    if main_func is None:
        raise RuntimeError("找不到 main() 函数")

    # 把第一句（假设是 force_utf8()）移到第二句
    if len(main_func.body) >= 2:
        main_func.body[0], main_func.body[1] = main_func.body[1], main_func.body[0]

    cli_init.write_text(ast.unparse(tree), encoding="utf-8")


def mutate_entry_add_relative_import() -> None:
    """变异 C2.4：在入口脚本加相对导入。"""
    entry_file = ROOT / "packaging" / "analyzer_entry.py"
    content = entry_file.read_text(encoding="utf-8")
    # 在 import sys 后面加一条相对导入
    content = content.replace("import sys\n", "import sys\nfrom . import _stdio\n")
    entry_file.write_text(content, encoding="utf-8")


def restore_file(file_path: Path, backup: str) -> None:
    """恢复文件内容。"""
    file_path.write_text(backup, encoding="utf-8")


def main() -> int:
    """执行 4 条守卫的变异测试。"""
    tests = [
        ("test_analyzer_spec_entry_is_not_package_init", mutate_spec_to_init, ROOT / "packaging" / "build_analyzer_windows.spec"),
        ("test_analyzer_entry_is_thin", mutate_entry_add_print, ROOT / "packaging" / "analyzer_entry.py"),
        ("test_cli_main_first_statement_is_force_utf8", mutate_cli_main_no_force_utf8, ROOT / "depressionplex" / "cli" / "__init__.py"),
        ("test_no_relative_import_in_any_pyinstaller_entry", mutate_entry_add_relative_import, ROOT / "packaging" / "analyzer_entry.py"),
    ]

    results = []
    for test_name, mutate_fn, file_path in tests:
        print(f"\n{'='*60}")
        print(f"测试守卫: {test_name}")
        print(f"{'='*60}")

        # 备份原文件
        backup = file_path.read_text(encoding="utf-8")

        try:
            # 1. 原始状态应该 GREEN
            print(f"[1/3] 原始状态...")
            if not run_single_test(test_name):
                print(f"❌ FAIL: 原始状态就是 RED（守卫本身有问题）")
                results.append((test_name, "FAIL_BASELINE"))
                continue
            print(f"✓ 原始状态 GREEN")

            # 2. 变异后应该 RED
            print(f"[2/3] 应用变异...")
            mutate_fn()
            if run_single_test(test_name):
                print(f"❌ FAIL: 变异后仍然 GREEN（守卫未检测到违规）")
                results.append((test_name, "FAIL_NOT_CAUGHT"))
            else:
                print(f"✓ 变异后 RED（守卫成功检测）")

                # 3. 恢复后应该 GREEN
                print(f"[3/3] 恢复原文件...")
                restore_file(file_path, backup)
                if not run_single_test(test_name):
                    print(f"❌ FAIL: 恢复后仍然 RED（环境污染）")
                    results.append((test_name, "FAIL_RESTORE"))
                else:
                    print(f"✓ 恢复后 GREEN")
                    results.append((test_name, "PASS"))

        except Exception as e:
            print(f"❌ FAIL: 变异测试异常 - {e}")
            results.append((test_name, f"FAIL_EXCEPTION: {e}"))
        finally:
            # 确保恢复
            restore_file(file_path, backup)

    # 汇总结果
    print(f"\n{'='*60}")
    print("变异测试汇总")
    print(f"{'='*60}")
    for test_name, status in results:
        symbol = "✓" if status == "PASS" else "❌"
        print(f"{symbol} {test_name}: {status}")

    passed = sum(1 for _, status in results if status == "PASS")
    total = len(results)
    print(f"\n通过: {passed}/{total}")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
