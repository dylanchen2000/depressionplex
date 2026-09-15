"""冻结后端 exe 的入口脚本（PyInstaller 专用，源码模式不走这里）。

不许把 depressionplex/cli/__init__.py 当入口脚本：
- PyInstaller 会把入口脚本当 __main__ 执行，__package__ 为空，
  里面任何 `from . import X` 在冻结后必然 ImportError
  （DP-108 第 12 步实测，run 34826444142）；
- 同一份代码会以 __main__ 和 depressionplex.cli 两个名字各载一次，
  模块级状态出现两份。

本文件只许有 import 和一次 sys.exit(main())，不许有任何逻辑、任何 print。
"""
import sys

from depressionplex.cli import main

if __name__ == "__main__":
    sys.exit(main())
