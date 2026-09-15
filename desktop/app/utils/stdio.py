"""Windows 控制台 UTF-8 强制（外壳进程专用）。

与 depressionplex/cli/_stdio.py 是**架构层面的重复实现**（不是技术债）：
外壳与引擎之间是进程边界，外壳不许 import depressionplex（架构 §3.4）。
"""
import sys


def force_utf8() -> None:
    """把本进程的 stdout/stderr 切成 UTF-8。取不到 reconfigure 就什么都不做。"""
    for stream in (sys.stdout, sys.stderr):
        fn = getattr(stream, "reconfigure", None)
        if fn is not None:
            fn(encoding="utf-8")
