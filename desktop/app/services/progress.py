"""stderr 进度解析（纯逻辑，不依赖 PySide6）。

引擎往 stderr 写两种东西：
- 进度行：`{"ev": "progress", "frame": 1234, "n": 45000}`，`n` 可能是 null
- 其它行：`[警告] ...` 这类人话

本模块提供行缓冲（`StderrPump`）与百分比计算（`percent`），不做 GUI 相关的事。
"""

import json
from typing import NamedTuple


class ProgressEvent(NamedTuple):
    """一条进度事件。"""

    frame: int
    n: int | None  # 总帧数，未知时为 None


class LogLine(NamedTuple):
    """一条日志行（不是进度，是人话）。"""

    text: str


class StderrPump:
    """有状态的行缓冲：QProcess 给的是字节块，一次可能给半行，也可能给三行。

    用法：
        pump = StderrPump()
        # 每次 QProcess readyReadStandardError 时：
        for item in pump.feed(process.readAllStandardError().data()):
            if isinstance(item, ProgressEvent):
                # 更新进度条
            elif isinstance(item, LogLine):
                # 写入日志
    """

    def __init__(self):
        self._buf = b""

    def feed(self, chunk: bytes) -> list[ProgressEvent | LogLine]:
        """喂入一块字节，返回零到多条事件/日志行。

        半行会缓存，等下一块拼完整。解析不出的行归为 LogLine，不抛异常也不丢弃。
        """
        self._buf += chunk
        result: list[ProgressEvent | LogLine] = []

        while b"\n" in self._buf:
            line_bytes, self._buf = self._buf.split(b"\n", 1)
            try:
                line_text = line_bytes.decode("utf-8")
            except UnicodeDecodeError:
                # 解码失败也不丢，保留原始字节的 repr
                result.append(LogLine(repr(line_bytes)))
                continue

            line_text = line_text.strip()
            if not line_text:
                continue

            # 尝试解析为 JSON 进度行
            if line_text.startswith("{") and '"ev"' in line_text:
                try:
                    obj = json.loads(line_text)
                    if obj.get("ev") == "progress":
                        frame = obj["frame"]
                        n = obj["n"]  # 可能是 null，保持为 None
                        result.append(ProgressEvent(frame=frame, n=n))
                        continue
                except (json.JSONDecodeError, KeyError, TypeError):
                    pass

            # 不是进度行，当日志行处理
            result.append(LogLine(line_text))

        return result


def percent(frame: int, n: int | None) -> float | None:
    """计算百分比。n 为 None 时返回 None（不确定进度），不许算成 0% 或 100%。"""
    if n is None or n <= 0:
        return None
    return 100.0 * frame / n
