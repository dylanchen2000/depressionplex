"""标准流编码归一化。

Windows 上 stdout/stderr 默认编码是 cp1252（英文 locale）或 cp936（中文 locale），
中文报告文本会 `UnicodeEncodeError`。本模块提供防御式的 UTF-8 切换。
"""

import sys


def force_utf8() -> None:
    """把本进程的 stdout/stderr 切成 UTF-8。取不到 reconfigure 就什么都不做。

    取不到 `reconfigure` 的情况包括：
    - 测试里 mock 出来的 `io.StringIO`（没有这个方法）
    - 极个别受限环境（流被重定向成不可 reconfigure 的对象）

    防御式设计：真取不到就让系统自己的编码决定，不许因为「想改编码」而崩。
    """
    for stream in (sys.stdout, sys.stderr):
        fn = getattr(stream, "reconfigure", None)
        if fn is not None:
            fn(encoding="utf-8")
