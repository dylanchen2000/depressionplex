"""DP-104：`.CLB` 结构解析的守卫。

守的是三件事，一件都不许松：

1. **两种头 + 长度公式**（逆向报告 §7.3）。四份随包样例的长度全部编进测试，
   它们是这套结构唯一的地面真值。
2. **长度必须精确闭合**。多一字节少一字节都得抛——多出来的字节意味着文件里还有
   我们没认出来的东西，那时候"解析成功"是假的。
3. **19 个数不许起名**。含义未确认（报告只给了"每槽 19 个 int32"，没给字段名），
   所以每槽只许有 `index` 与 `words` 两个键。谁想加语义键，先拿证据来。
"""

from __future__ import annotations

import ast
import struct
import tempfile
from pathlib import Path

from depressionplex.csi.clb import (
    CLB_BYTES_PER_ARENA,
    CLB_WORDS_PER_ARENA,
    ClbParseError,
    parse_clb_struct,
    serialize_clb_struct,
)
from depressionplex.csi.fst_import import CsiParseError, parse_clb

REPO = Path(__file__).resolve().parents[1]
FIX = REPO / "tests" / "fixtures" / "csi_fst"
LEGACY_FIXTURE = FIX / "正常1-4.CLB"


def _expect(exc, fn, *a):
    try:
        fn(*a)
    except exc:
        return
    raise AssertionError(f"期望 {exc.__name__}，但没有抛：{fn} {a}")


def _synth(header: str, count: int, *, word: int = 7) -> bytes:
    if header == "current":
        head = bytes([0x43 + count]) + b"\n\n"
    else:
        head = struct.pack("<I", count)
    body = b""
    for k in range(count):
        body += struct.pack(
            f"<{CLB_WORDS_PER_ARENA}i", *[word + k] * CLB_WORDS_PER_ARENA
        )
    return head + body


def _write(data: bytes, d: str, name: str = "x.CLB") -> Path:
    p = Path(d) / name
    p.write_bytes(data)
    return p


# ---------------------------------------------------------------------------
# 结构
# ---------------------------------------------------------------------------
def test_legacy_fixture_structure() -> None:
    """真夹具：308 字节 = 4 字节旧格式头 + 4 槽 × 76。"""
    s = parse_clb_struct(LEGACY_FIXTURE)
    assert s["size"] == 308
    assert s["header"] == "legacy"
    assert s["header_bytes"] == 4
    assert s["arena_count"] == 4
    assert len(s["arenas"]) == 4
    assert all(len(a["words"]) == CLB_WORDS_PER_ARENA for a in s["arenas"])
    assert [a["index"] for a in s["arenas"]] == [1, 2, 3, 4]
    # 第 1 槽的头两个数（不解释含义，只锁住"解出来的是这些字节"）
    assert s["arenas"][0]["words"][:4] == [42, 186, 72, 186]
    assert 4 + 4 * CLB_BYTES_PER_ARENA == s["size"]


def test_parse_is_lossless() -> None:
    """解析不许丢东西：打回去必须与原文件逐位相同。"""
    s = parse_clb_struct(LEGACY_FIXTURE)
    assert serialize_clb_struct(s) == LEGACY_FIXTURE.read_bytes()


def test_four_documented_lengths_close() -> None:
    """逆向报告 §7.3 的四份样例长度，两种头各两份，全部要能读对。

    | 文件 | 字节 | 头 | 槽 |
    | FSDEMO.CLB | 79 | 当前 | 1 |
    | FourTanksSample.CLB | 307 | 当前 | 4 |
    | sample2.CLB（旧） | 80 | 旧 | 1 |
    | 正常1-4.CLB（夹具） | 308 | 旧 | 4 |
    """
    cases = [("current", 1, 79), ("current", 4, 307), ("legacy", 1, 80), ("legacy", 4, 308)]
    with tempfile.TemporaryDirectory() as d:
        for header, count, size in cases:
            data = _synth(header, count)
            assert len(data) == size, (header, count, len(data), size)
            s = parse_clb_struct(_write(data, d, f"{header}{count}.CLB"))
            assert (s["header"], s["arena_count"], s["size"]) == (header, count, size)


def test_length_must_close_exactly() -> None:
    """长度不闭合就抛：多一字节、少一字节都不许"成功"。"""
    raw = LEGACY_FIXTURE.read_bytes()
    with tempfile.TemporaryDirectory() as d:
        _expect(ClbParseError, parse_clb_struct, _write(raw + b"\x00", d, "long.CLB"))
        _expect(ClbParseError, parse_clb_struct, _write(raw[:-1], d, "short.CLB"))


def test_arena_count_out_of_range_rejected() -> None:
    """旧格式头里槽位数为 0 或 5 时不许当真（0 会解出 0 槽，5 越界）。"""
    with tempfile.TemporaryDirectory() as d:
        for count in (0, 5):
            data = struct.pack("<I", count) + b"\x00" * (max(count, 1) * CLB_BYTES_PER_ARENA)
            _expect(ClbParseError, parse_clb_struct, _write(data, d, f"n{count}.CLB"))


def test_text_standard_clb_is_rejected_not_misparsed() -> None:
    """159 字节的 `Standard.CLB` 是另一种**文本**标定（报告 §7.3），不许套槽位结构。"""
    text = ("Standard body calibration\n" + "1 2 3 4 5\n" * 13).ljust(159, "0").encode()
    assert len(text) == 159
    with tempfile.TemporaryDirectory() as d:
        p = _write(text, d, "Standard.CLB")
        try:
            parse_clb_struct(p)
        except ClbParseError as exc:
            # 断言必须查「文本」这个**概念**，不许查文件名：报错里本来就带文件名，
            # 而这份夹具正好叫 Standard.CLB，查名字的断言恒真、等于装饰
            # （变异测试第一轮就是这么抓出来的）。
            assert "文本" in str(exc), f"报错没点出这是文本格式的另一种 CLB：{exc}"
            return
    raise AssertionError("文本 CLB 被当成槽位结构解析了")


# ---------------------------------------------------------------------------
# 不许起名
# ---------------------------------------------------------------------------
def test_arena_keys_are_frozen() -> None:
    """每槽只许有 `index` 与 `words`。

    19 个 int32 的含义**未确认**：逆向报告只恢复了"每槽 19 个 int32"的布局，
    三份报告都没有给字段名。要加任何语义键（水线 / 攀爬高度 / 矩形…），
    先在 ISSUES 里写清凭什么，再改这条测试。
    """
    s = parse_clb_struct(LEGACY_FIXTURE)
    for a in s["arenas"]:
        assert set(a) == {"index", "words"}, f"多出来的键：{sorted(set(a) - {'index', 'words'})}"


def test_no_semantic_field_names_in_module() -> None:
    """静态守：`clb.py` 里不许出现把 19 个数当语义字段的**标识符**。

    只查标识符与字典键，不查注释与文档字符串——模块 docstring 里正是要**说明**
    这些语义为什么还不能用，那段话必须能留着。
    """
    src = (REPO / "depressionplex" / "csi" / "clb.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    banned = ("water", "climb", "rect", "tank_x", "surface", "waterline", "bottom_y")
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, ast.arg):
            names.append(node.arg)
        elif isinstance(node, ast.Attribute):
            names.append(node.attr)
        elif isinstance(node, ast.Dict):
            names += [k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)]
    low = [n.lower() for n in names]
    hits = [n for n in low if any(b in n for b in banned)]
    assert not hits, f"clb.py 给未确认的字段起了名：{sorted(set(hits))}"


# ---------------------------------------------------------------------------
# 向后兼容（DP-094 的 parse_clb 契约）
# ---------------------------------------------------------------------------
def test_parse_clb_keeps_dp094_dump_keys() -> None:
    """旧格式仍给整文件无损转储；当前格式切不开 4 字节，给 `None`。

    **键一个都不许消失**（共同约定 §1：键不许因为没内容就不写），
    也不许拿空表冒充"有值"。
    """
    c = parse_clb(LEGACY_FIXTURE)
    assert len(c["int32"]) == 77 and len(c["float32"]) == 77
    assert struct.pack("<77i", *c["int32"]) == LEGACY_FIXTURE.read_bytes()
    assert c["arena_count"] == 4 and c["header"] == "legacy"

    with tempfile.TemporaryDirectory() as d:
        cur = parse_clb(_write(_synth("current", 4), d, "cur.CLB"))
        assert "int32" in cur and "float32" in cur, "键不许因为不适用就消失"
        assert cur["int32"] is None and cur["float32"] is None
        assert cur["arena_count"] == 4 and cur["header"] == "current"


def test_parse_clb_still_raises_csi_parse_error() -> None:
    """`parse_clb` 对外的异常类型是 DP-094 起就有的 `CsiParseError`，不许换掉。"""
    with tempfile.TemporaryDirectory() as d:
        _expect(CsiParseError, parse_clb, _write(b"\x00" * 100, d, "bad.CLB"))
