"""CLI 子命令分发器。

冻结构建的后端 exe 入口。源码模式通过 `python -m depressionplex.cli` 调用，
冻结模式通过 `depression-analyzer.exe` 调用。两种模式的 argv 形状一致：
    [<engine_path>] + [子命令] + [参数...]

例如：
    python -m depressionplex.cli analyze video.mp4 --assay TST
    depression-analyzer.exe analyze video.mp4 --assay TST
"""

import sys

from . import _stdio

# 子命令名册（显式字典，不许隐式发现）
# 每个键必须对应 depressionplex/cli/ 下一个有 main() 的模块
SUBCOMMANDS = {
    "analyze": "depressionplex.cli.analyze",
    "acq-check": "depressionplex.cli.acq_check",
}


def main() -> int:
    """子命令分发器。

    退出码契约（smoke test 依赖这个）：
    - 0: 正常（--help / -h / 无参数 / 子命令成功）
    - 1: 内部错误（导入失败、模块缺 main 等）
    - 2: 用法错误（未知子命令）
    - 其他：由子命令的 main() 决定
    """
    # A1: 必须是第一句（在任何 print 之前），两条路径都打中文（stdout/stderr）
    _stdio.force_utf8()

    # --help / -h / 无参数：打印用法到 stdout，rc 0
    # （客户双击 exe 不该看到错误，加了子命令忘了更新用法也能从 --help 发现）
    if len(sys.argv) < 2 or sys.argv[1] in ("--help", "-h"):
        print("用法: depression-analyzer <子命令> [参数...]")
        print()
        print("可用子命令:")
        for subcmd in sorted(SUBCOMMANDS.keys()):
            print(f"  {subcmd}")
        print()
        print("详细用法: depression-analyzer <子命令> --help")
        return 0

    subcommand = sys.argv[1]

    # 未知子命令：打印用法到 stderr，rc 2（用法错误）
    if subcommand not in SUBCOMMANDS:
        print(f"未知子命令: {subcommand}", file=sys.stderr)
        print(f"可用子命令: {', '.join(sorted(SUBCOMMANDS.keys()))}", file=sys.stderr)
        print("运行 depression-analyzer --help 查看用法", file=sys.stderr)
        return 2

    # 导入并调用子命令的 main()
    module_name = SUBCOMMANDS[subcommand]
    try:
        module = __import__(module_name, fromlist=["main"])
    except ImportError as e:
        print(f"无法导入子命令模块 {module_name}: {e}", file=sys.stderr)
        return 1

    if not hasattr(module, "main"):
        print(f"子命令模块 {module_name} 缺少 main() 函数", file=sys.stderr)
        return 1

    # 传递剩余参数给子命令（跳过程序名和子命令名）
    return module.main(sys.argv[2:])


if __name__ == "__main__":
    sys.exit(main())
