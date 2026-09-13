"""DP-104：写 CSI 的 FST 参数文件（`.SET`）。

**只改，不造。** 这是本模块最重要的一条设计决定，理由不是懒：

一份 `.SET` 里我们真正认识的只有表头那几十字节 —— 逆向报告 §7.2 明写文件里还有
「版本化全局阈值/标志、一个 **0x558 字节**分析规则对象、启停状态、每槽分类器/背景对象、
显示状态」，只读检查器对这些一律保持 opaque。从零合成就等于**编造** 1500 字节我们
读不懂的结构：CSI 可能拒收，也可能收下并按我们编的规则算分——后者是灾难，
因为那时候错的是**判定规则**，而输出看着完全正常。

所以对外只有一个能力：**拿客户自己的 `.SET` 当模板，逐位保留，只改我们认得的那几个字段。**

## 为什么 Motion 参数按**下标**改，不按字段名改

`fst_import.MOTION_PAIR_UNRESOLVED_IDX` 记着 4 对同类型字段（min length / noise /
merge / bin）的「挣扎侧 vs 放弃侧」归属仍是**约定推断**，不是字节级证明。
读的时候猜错只是标签难看；**写的时候猜错，CSI 会把两侧的阈值对调着用**，
算出来的秒数照样是一串正常数字。所以本模块**不提供**任何按字段名改这四对的入口，
只接受 `{下标: 新值}`——下标是读出来的位置，位置是证实过的。

顺带说：本模块正好是**收口那个 bit 的仪器**。把 idx5 写成 20、idx6 写成 40
（其余不动），拿去 CSI 面板上看哪一侧显示 20，一次就定死
（`fst_import` 里那条「收口只需一份两侧取值不同的 .SET」）。

## 验收边界（不许含糊）

沙箱能证的：模板逐位保留、改动只落在预期偏移、改完 `parse_set` 读回来就是写进去的值。
沙箱**证不了**的：**CSI 自己能不能读回这份文件**（要 Windows + 那台装了 DepressionSuite
的机器）。架构 §6.1 的 B9 验收行点名要这一条，所以它是**未完成项**，不是通过项。
"""

from __future__ import annotations

import struct
from pathlib import Path

from .fst_import import (
    MOTION_PAIR_UNRESOLVED_IDX,
    SET_MOTION_REL,
    CsiParseError,
    parse_set,
)

#: 允许改的标量字段 → (相对 base 的偏移, struct 码)。
#: 键名与 `parse_set` 返回的键**逐字相同**——同一个数字只许有一个名字。
#: 故意不含：`bool_block`（15 字节的布尔块，逐位含义未确认）、`tank_triples`
#: 与 `n_tanks`（改它会让 base 位移，后面每个字段都得重排，还牵连每槽的分类器对象）、
#: 一切 `unknown_rel_*`（改一个说不出名字的字段，等于改一个说不出后果的东西）。
SET_SCALAR_FIELDS: dict[str, tuple[int, str]] = {
    "frame_padding": (4, "<i"),
    "bkgd_gen_thresh": (8, "<i"),
    "only_change_bg_above_water": (12, "<i"),
    "high_cutoff": (16, "<f"),
    "low_cutoff": (20, "<f"),
    "learning_memory": (24, "<f"),
    "struggle_esc_thresh": (43, "<f"),
    "float_immobile_thresh": (47, "<f"),
}

#: 13 个 Motion 数里唯一的 float32（idx3 = MaxMoveThresh）。其余 12 个是 int32。
SET_MOTION_FLOAT_IDX = 3
SET_MOTION_COUNT = 13


def _motion_offset(base: int, idx: int) -> tuple[int, str]:
    if not 0 <= idx < SET_MOTION_COUNT:
        raise CsiParseError(
            f"Motion 下标 {idx} 不在 0..{SET_MOTION_COUNT - 1}"
        )
    code = "<f" if idx == SET_MOTION_FLOAT_IDX else "<i"
    return base + SET_MOTION_REL + 4 * idx, code


def _check_value(code: str, value: object, where: str) -> object:
    """int 槽只收 int，float 槽只收 int/float。**不许悄悄取整或转型。**"""
    if code == "<i":
        if isinstance(value, bool) or not isinstance(value, int):
            raise CsiParseError(
                f"{where} 是 int32 槽，只收 int，收到 {type(value).__name__}={value!r}。"
                f"悄悄取整会让写进去的数和你以为写进去的数不是一个数。"
            )
        if not -(2 ** 31) <= value < 2 ** 31:
            raise CsiParseError(f"{where}: {value} 超出 int32 范围")
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CsiParseError(
            f"{where} 是 float32 槽，只收 int/float，收到 {type(value).__name__}={value!r}"
        )
    return float(value)


def patch_set_bytes(
    template: bytes,
    *,
    scalars: dict[str, object] | None = None,
    motion_by_index: dict[int, object] | None = None,
) -> tuple[bytes, set[int]]:
    """在模板字节上改字段，返回 (新字节, 被改动的字节偏移集合)。

    - `scalars`：键必须在 `SET_SCALAR_FIELDS` 里，不认的键**当场报错**（不许静默忽略，
      静默忽略意味着客户以为自己改了参数、其实没改）。
    - `motion_by_index`：`{0..12: 新值}`，**按下标**，理由见模块 docstring。
    - 返回的偏移集合让调用方能断言「只动了该动的字节」。

    模板本身必须能被 `parse_set` 通过（含 4 个哨兵对的结构校验）。
    读不动的文件不许往里写——那时候 base 都可能是错的。
    """
    parsed = _parse_bytes(template)
    base = parsed["base"]
    out = bytearray(template)
    touched: set[int] = set()

    for key, value in (scalars or {}).items():
        if key not in SET_SCALAR_FIELDS:
            raise CsiParseError(
                f"不认识的标量字段 {key!r}；可改的只有 "
                f"{sorted(SET_SCALAR_FIELDS)}。"
                f"（`bool_block` / `tank_triples` / `n_tanks` / `unknown_rel_*` 故意不许改，"
                f"见 SET_SCALAR_FIELDS 的注释）"
            )
        rel, code = SET_SCALAR_FIELDS[key]
        checked = _check_value(code, value, f"字段 {key}")
        struct.pack_into(code, out, base + rel, checked)
        touched.update(range(base + rel, base + rel + 4))

    for idx, value in (motion_by_index or {}).items():
        if isinstance(idx, bool) or not isinstance(idx, int):
            raise CsiParseError(f"Motion 下标必须是 int，收到 {idx!r}")
        off, code = _motion_offset(base, idx)
        checked = _check_value(code, value, f"Motion idx{idx}")
        struct.pack_into(code, out, off, checked)
        touched.update(range(off, off + 4))

    return bytes(out), touched


def write_set(
    template_path,
    out_path,
    *,
    scalars: dict[str, object] | None = None,
    motion_by_index: dict[int, object] | None = None,
) -> dict:
    """按模板写一份改过参数的 `.SET`，写完**自己验**一遍，返回改动摘要。

    三条硬规矩：

    1. `out_path` 已存在时**报错不覆盖**（客户的参数文件被静默盖掉，等于抹掉一批实验的口径）。
    2. 写盘后重新读出来，逐项核对写进去的值；对不上就删掉半成品并抛错。
    3. 逐字节比对模板与产物，**改动集合必须恰好等于**预期偏移集合；多改一个字节就抛错。

    模板在**动手之前**先单独验一遍，报错里点名模板文件。这一步看着和后面的自检重复，
    其实不是：读不动的模板意味着 `base` 都可能是错的，"改某个字段"改到的是随机位置；
    等写完再由自检发现，报错会指向产物，把人往错的方向带。
    """
    template_path = Path(template_path)
    out_path = Path(out_path)
    if out_path.exists():
        raise CsiParseError(f"{out_path} 已存在，不覆盖。换个文件名或先自己移走。")

    try:
        parse_set(template_path)
    except CsiParseError as exc:
        raise CsiParseError(f"模板 {template_path.name} 读不动，不许往里写：{exc}") from exc

    template = template_path.read_bytes()
    new, touched = patch_set_bytes(
        template, scalars=scalars, motion_by_index=motion_by_index
    )

    actual = {i for i in range(len(template)) if template[i] != new[i]}
    if not actual <= touched:
        raise CsiParseError(
            f"写 {out_path.name} 时改到了预期之外的字节：{sorted(actual - touched)[:16]}"
        )

    out_path.write_bytes(new)
    try:
        back = parse_set(out_path)
        for key, value in (scalars or {}).items():
            got = back[key]
            if not _same(got, value, SET_SCALAR_FIELDS[key][1]):
                raise CsiParseError(f"{key} 写进去 {value!r}，读回来 {got!r}")
        for idx, value in (motion_by_index or {}).items():
            got = back["motion_ints"][idx]
            code = "<f" if idx == SET_MOTION_FLOAT_IDX else "<i"
            if not _same(got, value, code):
                raise CsiParseError(f"Motion idx{idx} 写进去 {value!r}，读回来 {got!r}")
    except BaseException:
        out_path.unlink(missing_ok=True)  # 不留一份没验过的参数文件在客户目录里
        raise

    return {
        "template": str(template_path),
        "out": str(out_path),
        "n_bytes_changed": len(actual),
        "changed_offsets": sorted(actual),
        "csi_read_back_verified": False,  # 只有那台装了 CSI 的机器能把它变成 True
    }


def _same(got: object, want: object, code: str) -> bool:
    if code == "<f":
        return abs(float(got) - float(want)) <= 1e-6 * max(1.0, abs(float(want)))
    return got == want


def _parse_bytes(template: bytes) -> dict:
    """把字节喂给 `parse_set`（它只吃路径），避免在两处各写一份表头解析。"""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "template.SET"
        p.write_bytes(template)
        return parse_set(p)


#: 给调用方（B6 导出 / 排障脚本）看的：这四对下标的高低归属还没定死，
#: 所以**不许**按字段名写它们。这里只是把 fst_import 的清单转出来，不复制内容。
UNRESOLVED_MOTION_PAIRS = MOTION_PAIR_UNRESOLVED_IDX
