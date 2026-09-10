"""DP-094：CSI 导出 .xlsx 的最小读取，只用标准库 zipfile + xml.etree。

R7 / 派工单铁律：**不许引入 openpyxl / pandas / numpy 或任何新依赖**。
CSI DepressionScan 导出的 xlsx 很简单：单 sheet、字符串全在 sharedStrings、
数字直接是数值单元格、行列稀疏（用 r="B7" 这种引用定位，不能按出现顺序数）。

数字单元格返回 float，文本返回 str，空单元格返回 None——见 read_sheet。
注意 CSI 的 Bin 汇总表里**有的数字是以文本存的**（共享字符串），那种情况
read_sheet 返回 str，调用方自己转数；本模块不猜、不强转。
"""
from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"

_SHEET_PART_RE = re.compile(r"^xl/worksheets/sheet\d+\.xml$")
_CELL_REF_RE = re.compile(r"^([A-Z]+)(\d+)$")


class XlsxReadError(Exception):
    """xlsx 结构读不出来（缺部件、引用越界、单元格类型不认识）。"""


def _col_to_index(letters: str) -> int:
    """'A'→0, 'B'→1, ..., 'Z'→25, 'AA'→26。"""
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    out: list[str] = []
    for si in root.iter(NS + "si"):
        # 一个 <si> 可能由多个 <r><t> 富文本片段拼成，全部接起来
        out.append("".join(t.text or "" for t in si.iter(NS + "t")))
    return out


def _sheet_part(zf: zipfile.ZipFile, sheet_index: int) -> str:
    """按 workbook 的 sheet 顺序找第 sheet_index 个 sheet 的部件路径。

    解析失败时退回按文件名排序（CSI 的文件只有一个 sheet，两种走法等价）。
    """
    names = zf.namelist()
    try:
        wb = ET.fromstring(zf.read("xl/workbook.xml"))
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        rid_to_target = {r.get("Id"): r.get("Target") for r in rels.iter(REL_NS + "Relationship")}
        parts: list[str] = []
        for sh in wb.iter(NS + "sheet"):
            rid = sh.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
            target = rid_to_target.get(rid)
            if target is None:
                continue
            if not target.startswith("/"):
                target = "xl/" + target.lstrip("./")
            else:
                target = target.lstrip("/")
            parts.append(target)
        if sheet_index < len(parts):
            return parts[sheet_index]
    except (KeyError, ET.ParseError):
        pass
    candidates = sorted(n for n in names if _SHEET_PART_RE.match(n))
    if sheet_index >= len(candidates):
        raise XlsxReadError(f"sheet_index={sheet_index} 越界，只有 {len(candidates)} 个 sheet 部件")
    return candidates[sheet_index]


def read_sheet(path, sheet_index: int = 0) -> list[list[object]]:
    """返回按行的单元格值。数字→float，文本→str，空→None。

    - 处理 sharedStrings.xml（CSI 的字符串都在共享表里）。
    - 处理稀疏行/列：用 r="B7" 引用定位，**不按出现顺序数**。
    - 行间长度对齐到该行最大列号；整张表再对齐到全表最大列数。
    """
    path = str(path)
    with zipfile.ZipFile(path) as zf:
        shared = _shared_strings(zf)
        part = _sheet_part(zf, sheet_index)
        if part not in zf.namelist():
            raise XlsxReadError(f"{path}: 找不到 sheet 部件 {part}")
        root = ET.fromstring(zf.read(part))

        table: list[list[object]] = []
        for row_el in root.iter(NS + "row"):
            cells: dict[int, object] = {}
            for c in row_el.iter(NS + "c"):
                ref = c.get("r") or ""
                m = _CELL_REF_RE.match(ref)
                if not m:
                    raise XlsxReadError(f"{path}: 单元格引用 {ref!r} 不认识")
                ci = _col_to_index(m.group(1))
                ctype = c.get("t")
                v_el = c.find(NS + "v")
                is_el = c.find(NS + "is")
                val: object
                if ctype == "s":
                    if v_el is None or v_el.text is None:
                        val = None
                    else:
                        idx = int(v_el.text)
                        if idx >= len(shared):
                            raise XlsxReadError(f"{path}: 共享字符串索引 {idx} 越界")
                        val = shared[idx]
                elif ctype == "inlineStr":
                    val = "".join(t.text or "" for t in is_el.iter(NS + "t")) if is_el is not None else None
                elif ctype == "b":
                    val = (v_el is not None and v_el.text == "1")
                elif ctype in (None, "n"):
                    if v_el is None or v_el.text is None:
                        val = None
                    else:
                        try:
                            val = float(v_el.text)
                        except ValueError:
                            raise XlsxReadError(f"{path}: 数值单元格 {ref} 的值 {v_el.text!r} 不是数")
                elif ctype == "str":
                    val = v_el.text if v_el is not None else None
                else:
                    raise XlsxReadError(f"{path}: 单元格 {ref} 类型 {ctype!r} 不认识")
                cells[ci] = val
            width = (max(cells) + 1) if cells else 0
            table.append([cells.get(i) for i in range(width)])

        maxw = max((len(r) for r in table), default=0)
        for r in table:
            r.extend([None] * (maxw - len(r)))
        return table
