"""DP-104：CSI 槽位标定文件（`.CLB`）的**结构**解析。

只解结构，**不解释字段含义**。逆向报告 §7.3 恢复的是布局，不是语义：

    两种二进制头：
      - 当前格式：首字节 `0x43 + arena_count`，随后 `0A 0A`（3 字节头）
        ⇒ `D\\n\\n` = 1 槽，`G\\n\\n` = 4 槽
      - 旧格式：直接 little-endian `u32 arena_count`（4 字节头）
    两者后面均为**每槽 19 个 little-endian int32**（76 字节）

四份随包样例与公式闭合，两种头各两份，这是本模块唯一的地面真值：

    | 文件 | 字节 | 头 | 槽 | 闭合 |
    |---|---:|---|---:|---|
    | `FSDEMO.CLB` | 79 | 当前 | 1 | 3 + 1×76 |
    | `FourTanksSample.CLB` | 307 | 当前 | 4 | 3 + 4×76 |
    | `sample2.CLB`（旧） | 80 | 旧 | 1 | 4 + 1×76 |
    | `正常1-4.CLB`（本仓夹具） | 308 | 旧 | 4 | 4 + 4×76 |

**19 个 int32 分别是什么，未确认。** 逆向报告只给了「每槽 19 个 int32」，没有给字段名；
`2026-09-10_新增材料全量验证` 报告也只写到「308-byte、4 arenas × 19 dwords，完整解析」。
所以本模块把它们原样端出来（`words[0..18]`），**不许起名、不许拆成矩形**——
一旦起名，未经证实的语义就会长成下游可以直接乘除的数字（DP-052 的老毛病）。

夹具 `正常1-4.CLB` 的 4 槽 x 区间为 42–72 / 156–185 / 279–306 / 387–416，y 不超过 240。
按 `2026-09-10_实测验证` 报告第 91 行「配套 CLB 和背景图使用转码后的坐标系」，
这些数落在 CSI **转码后**的画面（464×272 或 480×272）里，不是源视频（494×262 等）。
要把它当隔间定位用，必须先在**转码后**的同一段视频上跑我们的 DP-032 定位再比——
那需要真素材与能用的 cv2，见 `docs/ISSUES.md` 的 DP-104。**在比出来之前，不许当隔间用。**
"""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

#: 每槽固定 19 个 little-endian int32。逆向报告 §7.3。
CLB_WORDS_PER_ARENA = 19
CLB_BYTES_PER_ARENA = 4 * CLB_WORDS_PER_ARENA  # 76

#: 当前格式的首字节 = 0x43 + arena_count，后跟 b"\n\n"。
CLB_CURRENT_FIRST_BASE = 0x43
CLB_CURRENT_MARKER = b"\n\n"
CLB_HEADER_CURRENT = 3
CLB_HEADER_LEGACY = 4

#: 槽位数的合法范围。软件面板最多 4 槽（`SET_MAX_TANKS` 同）。
CLB_MAX_ARENAS = 4


class ClbParseError(Exception):
    """`.CLB` 结构假设被打破。**宁可大声失败，也不许猜着往下解析。**"""


def _read_header(data: bytes, name: str) -> tuple[str, int, int]:
    """认头，返回 (格式名, arena_count, 头长度)。两种头都认不出就抛。"""
    if len(data) >= CLB_HEADER_CURRENT and data[1:3] == CLB_CURRENT_MARKER:
        count = data[0] - CLB_CURRENT_FIRST_BASE
        if 1 <= count <= CLB_MAX_ARENAS:
            return "current", count, CLB_HEADER_CURRENT

    if len(data) >= CLB_HEADER_LEGACY:
        count = struct.unpack_from("<I", data, 0)[0]
        if 1 <= count <= CLB_MAX_ARENAS:
            return "legacy", count, CLB_HEADER_LEGACY

    raise ClbParseError(
        f"{name}: 两种 .CLB 头都不认（首 4 字节 {data[:4]!r}）。"
        f"当前格式要求 data[0]-0x43 落在 1..{CLB_MAX_ARENAS} 且 data[1:3]==b'\\n\\n'；"
        f"旧格式要求 u32 槽位数落在 1..{CLB_MAX_ARENAS}。"
        f"159 字节的 `Standard.CLB` 是另一种**文本**身体/笼位标定（逆向报告 §7.3），"
        f"不适用本结构，不许套进来。"
    )


def parse_clb_struct(path) -> dict:
    """按逆向报告 §7.3 结构解析 `.CLB`。

    返回：

        {
          "size": 文件字节数,
          "sha256": 文件哈希,
          "header": "current" | "legacy",
          "header_bytes": 3 | 4,
          "arena_count": 1..4,
          "arenas": [{"index": 1..n, "words": [19 个 int32]}, ...],
        }

    长度必须与 `头 + arena_count × 76` **精确**相等：多一字节少一字节都抛。
    多出来的字节意味着这份文件里还有我们没认出来的东西，那时候「解析成功」是假的。

    每槽只有 `index` 与 `words` 两个键 —— 19 个数的含义未确认（见模块 docstring），
    所以不许出现任何语义键名。这条有测试守（`test_arena_keys_are_frozen`）。
    """
    path = Path(path)
    data = path.read_bytes()
    if len(data) < CLB_HEADER_CURRENT + CLB_BYTES_PER_ARENA:
        raise ClbParseError(
            f"{path.name}: 只有 {len(data)} 字节，连一槽都装不下"
            f"（最小 {CLB_HEADER_CURRENT + CLB_BYTES_PER_ARENA}）"
        )

    header, count, head = _read_header(data, path.name)
    want = head + count * CLB_BYTES_PER_ARENA
    if len(data) != want:
        raise ClbParseError(
            f"{path.name}: 头说 {count} 槽（{header} 格式，头 {head} 字节），"
            f"应为 {head} + {count}×{CLB_BYTES_PER_ARENA} = {want} 字节，"
            f"实际 {len(data)} 字节。长度不闭合就说明还有没认出来的内容，"
            f"不许当解析成功。"
        )

    arenas = []
    for k in range(count):
        off = head + k * CLB_BYTES_PER_ARENA
        words = list(struct.unpack_from(f"<{CLB_WORDS_PER_ARENA}i", data, off))
        arenas.append({"index": k + 1, "words": words})

    return {
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "header": header,
        "header_bytes": head,
        "arena_count": count,
        "arenas": arenas,
    }


def serialize_clb_struct(parsed: dict) -> bytes:
    """把 `parse_clb_struct` 的结果打回字节。**只用来证明解析无损**，不对外当写盘用。

    `.CLB` 写盘不在 DP-104 范围内（架构 §6.1 的 B9 行只要求 `.CLB` **读**）。
    这个函数存在的唯一理由是让「解析没丢东西」成为一条可执行的断言。
    """
    header = parsed["header"]
    count = parsed["arena_count"]
    if header == "current":
        out = bytes([CLB_CURRENT_FIRST_BASE + count]) + CLB_CURRENT_MARKER
    elif header == "legacy":
        out = struct.pack("<I", count)
    else:
        raise ClbParseError(f"未知头格式 {header!r}")
    for arena in parsed["arenas"]:
        out += struct.pack(f"<{CLB_WORDS_PER_ARENA}i", *arena["words"])
    return out
