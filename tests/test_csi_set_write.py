"""DP-104：写 `.SET` 的守卫。

写参数文件比读危险得多：读错只是显示难看，**写错会让 CSI 按另一套阈值算分，
而算出来的秒数看着完全正常**。所以本组守卫盯的全是"看不出来的错"：

- 模板必须逐位保留（只改该改的字节，多改一个就抛）；
- 4 对高低归属未定的 Motion 字段**不许按字段名写**，只许按下标；
- 不许悄悄取整 / 转型 / 覆盖已有文件 / 往读不动的模板里写；
- "CSI 自己读回"这条验收**不许在代码里写死成 True**。
"""

from __future__ import annotations

import ast
import tempfile
from pathlib import Path

from depressionplex.csi.fst_import import (
    MOTION_PAIR_UNRESOLVED_IDX,
    CsiParseError,
    parse_set,
)
from depressionplex.csi.set_write import (
    SET_MOTION_FLOAT_IDX,
    SET_SCALAR_FIELDS,
    patch_set_bytes,
    write_set,
)

REPO = Path(__file__).resolve().parents[1]
FIX = REPO / "tests" / "fixtures" / "csi_fst"
TEMPLATES = ("10mg 2周.SET", "正常1-4对照更改.SET")
SET_WRITE_PY = REPO / "depressionplex" / "csi" / "set_write.py"


def _expect(fn, *a, **k) -> str:
    try:
        fn(*a, **k)
    except CsiParseError as exc:
        return str(exc)
    raise AssertionError(f"期望 CsiParseError，但没有抛：{fn} {a} {k}")


# ---------------------------------------------------------------------------
# 逐位保留
# ---------------------------------------------------------------------------
def test_identity_patch_is_byte_identical() -> None:
    """什么都不改时，产出必须与模板**逐位相同**。

    这是"只改不造"的底线：写盘路径上没有任何会顺手重排字节的逻辑。
    两份 fixture 的 n_tanks 不同（4 与 1，base 分别 56 与 20），两份都要过。
    """
    for name in TEMPLATES:
        raw = (FIX / name).read_bytes()
        out, touched = patch_set_bytes(raw)
        assert out == raw, f"{name}: 恒等 patch 改动了字节"
        assert touched == set(), f"{name}: 恒等 patch 报了改动偏移"


def test_patch_touches_only_that_slot() -> None:
    """改一个 Motion 下标，只许动它那 4 个字节。"""
    raw = (FIX / TEMPLATES[0]).read_bytes()
    base = parse_set(FIX / TEMPLATES[0])["base"]
    out, touched = patch_set_bytes(raw, motion_by_index={5: 20})
    want = set(range(base + 51 + 4 * 5, base + 51 + 4 * 5 + 4))
    assert touched == want
    actual = {i for i in range(len(raw)) if raw[i] != out[i]}
    assert actual <= want, f"改到了预期之外的字节：{sorted(actual - want)}"


def test_scalar_offsets_match_reader() -> None:
    """写用的偏移必须与 `parse_set` 读用的偏移一致——同一个数字只许有一个位置。

    做法：逐个标量字段写一个"哨兵值"，再用 `parse_set` 读回来核对。
    偏移表抄错一个数，这条就红。
    """
    raw = (FIX / TEMPLATES[1]).read_bytes()
    with tempfile.TemporaryDirectory() as d:
        for i, (key, (_rel, code)) in enumerate(sorted(SET_SCALAR_FIELDS.items())):
            probe = 1000 + i if code == "<i" else 0.5 + i
            out, _ = patch_set_bytes(raw, scalars={key: probe})
            p = Path(d) / f"{i}.SET"
            p.write_bytes(out)
            got = parse_set(p)[key]
            assert abs(float(got) - float(probe)) < 1e-6, f"{key}: 写 {probe} 读回 {got}"


# ---------------------------------------------------------------------------
# 高低归属未定 ⇒ 不许按字段名写
# ---------------------------------------------------------------------------
def test_no_field_name_entry_for_unresolved_pairs() -> None:
    """`set_write.py` 里不许出现按字段名写那 4 对的入口。

    `MOTION_PAIR_UNRESOLVED_IDX` 还非空，就说明"挣扎侧 / 放弃侧"这一位没定死；
    此时提供 `min_length_struggle=` 这样的参数，等于把一个未证实的假设做成了 API。
    只查**标识符与字典键**，docstring 里要讲清这件事，那段话必须留得住。
    """
    assert MOTION_PAIR_UNRESOLVED_IDX, "高低归属已定死的话，请连同本测试一起改"
    tree = ast.parse(SET_WRITE_PY.read_text(encoding="utf-8"))
    banned = ("minlength", "noisethresh", "mergebouts", "binsize", "struggle_side", "immobile_side")
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.arg):
            names.append(node.arg)
        elif isinstance(node, (ast.Name, ast.keyword)):
            names.append(node.id if isinstance(node, ast.Name) else (node.arg or ""))
        elif isinstance(node, ast.Dict):
            names += [
                k.value for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            ]
    low = [n.lower().replace("_", "") for n in names]
    hits = [n for n in low if any(b.replace("_", "") in n for b in banned)]
    assert not hits, f"给未定死高低归属的字段开了按名字写的口子：{sorted(set(hits))}"


def test_forbidden_and_unknown_scalar_keys_raise() -> None:
    """不认识的键、以及故意禁改的键，一律当场报错——**不许静默忽略**。

    静默忽略最坏：客户以为自己改了参数，其实文件没变，跑出来的数字还是老口径。
    """
    raw = (FIX / TEMPLATES[0]).read_bytes()
    for key in ("bool_block", "n_tanks", "base", "tank_triples", "unknown_rel_0", "frame_paddin"):
        msg = _expect(patch_set_bytes, raw, scalars={key: 1})
        assert "不认识" in msg


# ---------------------------------------------------------------------------
# 不许悄悄转型
# ---------------------------------------------------------------------------
def test_int_slot_refuses_float_and_bool() -> None:
    """int32 槽给浮点或 bool 一律抛，不许取整。

    取整之后"写进去的数"和"你以为写进去的数"不是一个数，而文件里看不出来。
    """
    raw = (FIX / TEMPLATES[0]).read_bytes()
    _expect(patch_set_bytes, raw, motion_by_index={5: 20.5})
    _expect(patch_set_bytes, raw, motion_by_index={5: True})
    _expect(patch_set_bytes, raw, scalars={"frame_padding": 3.5})
    _expect(patch_set_bytes, raw, motion_by_index={99: 1})


def test_float_slot_keeps_fraction() -> None:
    """idx3（唯一的 float32）写 3.5 必须读回 3.5，不许被当成 int 槽。"""
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "f.SET"
        write_set(FIX / TEMPLATES[0], out, motion_by_index={SET_MOTION_FLOAT_IDX: 3.5})
        assert abs(parse_set(out)["motion_ints"][SET_MOTION_FLOAT_IDX] - 3.5) < 1e-6


# ---------------------------------------------------------------------------
# 写盘纪律
# ---------------------------------------------------------------------------
def test_write_refuses_existing_output() -> None:
    """目标已存在时报错不覆盖：客户的参数文件被静默盖掉 = 抹掉一批实验的口径。"""
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "exists.SET"
        out.write_bytes("占位".encode())
        _expect(write_set, FIX / TEMPLATES[0], out, motion_by_index={5: 20})
        assert out.read_bytes() == "占位".encode(), "报错了却还是把文件写了"


def test_write_refuses_unparsable_template_and_leaves_nothing() -> None:
    """模板读不动就不许往里写，且不许留下半成品。

    模板读不动意味着 base 都可能是错的，此时"改某个字段"改到的是随机位置。
    报错必须点名**模板**：这条断言不是挑字眼，它钉住的是"模板在动手之前就被验过"。
    少了这一步，错误要等写完之后的自检才暴露，报错指向产物，把人往错的方向带
    （而且那时候产物已经被写过一遍了）。
    """
    with tempfile.TemporaryDirectory() as d:
        bad = Path(d) / "bad.SET"
        bad.write_bytes(b"XXXX" + (FIX / TEMPLATES[0]).read_bytes()[4:])  # magic 坏掉
        out = Path(d) / "out.SET"
        msg = _expect(write_set, bad, out, motion_by_index={5: 20})
        assert "模板" in msg and bad.name in msg, f"报错没点名模板：{msg}"
        assert not out.exists(), "模板读不动却留下了产物"


def test_report_says_csi_read_back_not_verified() -> None:
    """"CSI 自己读回"这条验收**沙箱证不了**，所以摘要里必须是 False。

    架构 §6.1 的 B9 验收行点名要这一条（要 Windows + 装了 DepressionSuite 的机器），
    在那台机器上跑过之前，任何地方都不许把它写成 True —— 那就是假报通过。
    """
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "r.SET"
        report = write_set(FIX / TEMPLATES[0], out, motion_by_index={5: 20, 6: 40})
        assert report["csi_read_back_verified"] is False
        assert report["n_bytes_changed"] > 0
    src = SET_WRITE_PY.read_text(encoding="utf-8")
    assert "csi_read_back_verified\": True" not in src
    assert "csi_read_back_verified': True" not in src


def test_disambiguation_probe_writes_two_different_sides() -> None:
    """本模块要能造出那份"两侧取值不同"的 `.SET`——收口高低归属的那把仪器。

    `fst_import` 里写着：只要有一份 min length 挣扎 20 / 放弃 40 的文件，
    拿去 CSI 面板上看哪一侧显示 20，`MOTION_PAIR_UNRESOLVED_IDX` 就能清空。
    """
    lo, hi = MOTION_PAIR_UNRESOLVED_IDX[0]
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "probe.SET"
        write_set(FIX / TEMPLATES[0], out, motion_by_index={lo: 20, hi: 40})
        m = parse_set(out)["motion_ints"]
        assert (m[lo], m[hi]) == (20, 40)
