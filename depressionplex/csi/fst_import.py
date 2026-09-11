"""DP-094：CSI DepressionScan（FST）结果导入。

把 CSI 导出的每孔位事件时间线（.xlsx）、参数文件（.SET）、标定文件（.CLB）、
汇总 Bin 表读进来。**只做如实解析 + 自检**，不设任何门槛、不判定 CSI 对错、
不把 CSI 当调参目标（R4）。

口径铁律（R1/R2，证据见规格 §2，不在此重新论证）：
  - CSI 侧唯一可用的不动时长是 **Ranges 口径** = 事件时间线里 Immobile 的 Length 之和。
  - **禁止**用 `Average of Frames and Ranges`，**禁止**用 `Frames/Time`。
    CSI 自己的 Score Method 选的是 `Range Scores (Smoothed) (BEST)`，
    是它自己认定 Ranges 才是正式结论；那两个是别的可选口径的附带值，
    本模块只把它们当"体检列"原样存进 statistics，任何聚合都不许用。

如实转载（R3）：`.SET` / `.CLB` 里未经面板/手册确认的偏移一律叫
`unknown_rel_<相对 base 的十进制偏移>`，不许起名。
"""
from __future__ import annotations

import hashlib
import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .xlsx import read_sheet

__all__ = [
    "CsiParseError",
    "CSI_EVENTS",
    "CSI_TANK_TO_CHAMBER",
    "RECORDING_ALIASES",
    "SET_MAGIC",
    "CsiEvent",
    "CsiTank",
    "parse_tank_filename",
    "parse_time_label",
    "parse_tank_xlsx",
    "immobile_ranges_s",
    "parse_bin_xlsx",
    "match_bin_to_tanks",
    "parse_set",
    "parse_clb",
    "load_csi_dir",
    "TANK_FILE_RE",
]


class CsiParseError(Exception):
    """CSI 文件解析/自检失败。不静默兜底——宁可抛错也不猜。"""


#: 28 个孔位里实际出现过的全部事件名。`Climb` 在 2 个孔位触发（7.76 / 12.68 s），
#: 不许省掉；出现未列入的名字要抛 CsiParseError，不许静默丢弃。
#: CSI 认识的全部事件类。前 5 类在 32 个孔位的时间线里实际出现过；
#: `Dive` 至今未在任何时间线里出现，但它**是 CSI 的正式类别**——
#: 每份导出的 Statistics 块表头都列着它（Escape|Immobile|Climb|Dive|PassDive|Swim）。
#: 所以它属于合法数据而不是脏数据，必须收进来，否则将来遇到就会误报解析失败。
CSI_EVENTS = ("Immobile", "Swim", "Escape", "Climb", "Dive", "PassDive")

#: CSI 孔位号 → 我们的 chamber 号。证据：正常1-4 的 4 个孔位 Ranges-Immobile
#: 依次为 21.76 / 104.36 / 208.72 / 23.24，人工（陈璇 09-10）依次为
#: 43.62 / 109.29 / 215.75 / 37.90，大小顺序与量级都对齐，故取恒等映射。
#: 这是**只有一段录像支持的假设**，别处不许再假定，需要更多录像验证。
CSI_TANK_TO_CHAMBER = {1: 1, 2: 2, 3: 3, 4: 4}

#: CSI 文件名 → 清单里的录像名。CSI 那边 "20mg 1周" 中间有空格，清单里没有。
RECORDING_ALIASES = {"20mg 1周": "20mg1周"}

SET_MAGIC = b"FSS3"

#: `.SET` 表头是变长的：偏移 4 是孔位数，紧跟 n_tanks 个 12 字节三元组，
#: 之后所有字段都相对 `base = 8 + 12 * n_tanks` 定位。见 parse_set 的 docstring。
SET_MAX_TANKS = 4
SET_MOTION_REL = 51        # 13 个 Motion 数相对 base 的偏移
SET_SENTINEL_REL = 123     # 第 1 个哨兵对相对 base 的偏移
SET_SENTINEL_STRIDE = 276  # 哨兵对之间的间距
SET_N_SENTINELS = 4        # 哨兵块恒为 4 个，**与 n_tanks 无关**

#: 13 个 Motion 数在文件里的位置 → 面板字段，**已定到的程度**（2026-09-11）。
#:
#: 定法：拿两份只改 Motion 的 `.SET` 做受控差分。用户把
#: Merge Bouts Limit 20→25、Min Length Thresh 15→18、Noise Thresh 10→15、
#: Bin Size 5→8（挣扎侧和放弃侧都改），其余 5 个字段不动。
#: 于是"哪个位置是哪个**字段类型**"被新值一一点亮，5 个没变的位置就是
#: 挣扎侧独有的那 5 个字段。
#:
#: **仍未定的两件事**（都需要一份"13 个值互不相同"的 `.SET`）：
#:   1. 同类型的两份里，哪个是 Struggle 哪个是 Float（4 对，各 2 种可能）
#:   2. idx 0 和 idx 2 哪个是 WaterSurProxThresh、哪个是 ClimbMagnThresh
#:      （两者都是 15 且都没变，这次差分点不亮）
#: 所以还剩 2**5 = 32 种可能的完整排列，比原来的天文数字已经小了极多。
#:
#: **这个常量只作记录，不许拿它给 parse_set 的返回键起名**（R3）。
MOTION_FIELD_HINTS = (
    "WaterSurProxThresh 或 ClimbMagnThresh（与 idx2 互换，未定）",
    "ClimbHeightThresh",
    "WaterSurProxThresh 或 ClimbMagnThresh（与 idx0 互换，未定）",
    "MaxMoveThresh（唯一的 float32）",
    "EarlyMergeLimit",
    "MinLengthThresh（与 idx6 成对，Struggle/Float 未定）",
    "MinLengthThresh（与 idx5 成对，Struggle/Float 未定）",
    "NoiseThreshFrames（与 idx10 成对，Struggle/Float 未定）",
    "MergeBoutsLimit（与 idx11 成对，Struggle/Float 未定）",
    "BinSizeSeconds（与 idx12 成对，Struggle/Float 未定）",
    "NoiseThreshFrames（与 idx7 成对，Struggle/Float 未定）",
    "MergeBoutsLimit（与 idx8 成对，Struggle/Float 未定）",
    "BinSizeSeconds（与 idx9 成对，Struggle/Float 未定）",
)

#: 孔位文件名主干正则。**（N）后面可能还有人写的后缀**（实测存在 `抑郁8-10（4）空鼠`），
#: 所以 suffix 用 `.*` 兜住，**不许把 `$` 顶在 `）` 后面**——那样会静默漏掉空鼠那一份。
TANK_FILE_RE = re.compile(r"^(?P<rec>.+?)（(?P<tank>\d)）(?P<suffix>.*)$")

_TIME_LABEL_RE = re.compile(r"^(?:(?P<min>\d+)')?(?P<sec>\d+)\"$")

# Statistics 块里只校验这三条度量行（规格 §4.2）。块里实际还有
# `Bins * Frames or Time per Bin:` / `Total Analyze Frames/Time:` 两行，
# 如实存进 statistics，但不参与自检、不参与任何聚合。
_STAT_FRAMES = "Frames/Time"
_STAT_RANGES = "Ranges"
_STAT_AVERAGE = "Average of Frames and Ranges"

# 自检容差
_LEN_TICK = 0.04          # Length 列的最小刻度（25 fps）
_LEN_TOL = 1e-6
_RANGE_TOL = 0.02
# Average = (Ranges + Frames/Time)/2 在金标准文件里就有恰好 0.02 的差
# （如 正常1-4（1） Escape 343.36 vs (363.4+323.36)/2=343.38），
# 再叠上 float 表示误差（~1e-14），所以容差取 0.02 + 1e-9，避免被浮点尾数卡掉。
_AVG_TOL = 0.02 + 1e-9


@dataclass(frozen=True)
class CsiEvent:
    from_s: float      # CSI 导出的 From/To 已被四舍五入到整秒，只能当参考
    to_s: float
    length_s: float    # 这一列是 0.04 s 的整数倍（25 fps），是唯一精确的量
    event: str


@dataclass(frozen=True)
class CsiTank:
    source_file: str
    recording: str     # 已过 RECORDING_ALIASES
    tank: int
    chamber: int
    events: tuple[CsiEvent, ...]
    ranges_s: dict[str, float]        # 事件名 → Length 之和（= Ranges 口径），只含出现过的事件
    statistics: dict[str, dict[str, float]]  # 度量名 → {事件名: 值}；块缺失时为空 dict
    window_s: float                   # = sum(ranges_s.values())


def parse_tank_filename(stem: str) -> tuple[str, int]:
    """从文件名主干取 (录像名, 孔位号)。录像名已过 RECORDING_ALIASES。

    suffix 不参与匹配（由调用方从 source_file 追溯）。stem 不带扩展名。
    匹配不上抛 CsiParseError——不许静默跳过。
    """
    m = TANK_FILE_RE.match(stem)
    if not m:
        raise CsiParseError(f"文件名不是孔位表格式: {stem!r}")
    rec = m.group("rec")
    rec = RECORDING_ALIASES.get(rec, rec)
    return rec, int(m.group("tank"))


def parse_time_label(s: str) -> float:
    """CSI 的时间标签 → 秒。见过的格式：`0"`、`30"`、`1'39"`、`10'05"`。

    解析不出来抛 CsiParseError，**不许返回 0 兜底**。
    """
    if not isinstance(s, str):
        raise CsiParseError(f"时间标签不是字符串: {s!r}")
    m = _TIME_LABEL_RE.match(s.strip())
    if not m:
        raise CsiParseError(f"时间标签解析不出来: {s!r}")
    minutes = int(m.group("min") or 0)
    seconds = int(m.group("sec"))
    return float(minutes * 60 + seconds)


def _num(v: object, ctx: str) -> float:
    """把单元格值转成 float。数值单元格是 float，文本存的数字是 str；都不是就抛错。"""
    if isinstance(v, bool):
        raise CsiParseError(f"{ctx}: 期望数值，拿到布尔 {v!r}")
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            raise CsiParseError(f"{ctx}: 期望数值，拿到文本 {v!r}")
    raise CsiParseError(f"{ctx}: 期望数值，拿到 {v!r}")


def _text(v: object, ctx: str) -> str:
    if not isinstance(v, str):
        raise CsiParseError(f"{ctx}: 期望文本，拿到 {v!r}")
    return v.strip()


def _find_statistics(rows: list[list[object]]) -> tuple[int, int] | None:
    """找 Statistics 块的 (行号, 列号)。

    规格 §4.2 的散文说块在"第 0 列"，但**实测金标准文件里 `Statistics` 在 D 列**
    （事件时间线占 A–D，块从 D 列起、事件名表头在 E–J）。所以这里按"哪个单元格
    等于 Statistics"来定位，不写死列号——见 PR 正文 discrepancy A。
    找不到返回 None（块可以整个不存在）。
    """
    for i, row in enumerate(rows):
        for j, v in enumerate(row):
            if isinstance(v, str) and v.strip() == "Statistics":
                return i, j
    return None


def _parse_statistics_block(
    rows: list[list[object]], si: int, sj: int, path: str
) -> dict[str, dict[str, float]]:
    """解析 Statistics 块。

    布局（实测）：`Statistics` 单元格右边同一行是事件名表头（E–J，可能少于全部）；
    往下每行第 sj 列是度量名（带尾随冒号），右边各列是该事件的值。
    度量名去掉尾随冒号后原样做 key（如实转载，包括规格没点名的
    `Bins * Frames or Time per Bin` / `Total Analyze Frames/Time`）。
    """
    header = rows[si]
    ev_col: dict[int, str] = {}
    for j in range(sj + 1, len(header)):
        name = header[j]
        if isinstance(name, str) and name.strip():
            ev_col[j] = name.strip()

    stats: dict[str, dict[str, float]] = {}
    for i in range(si + 1, len(rows)):
        row = rows[i]
        label_cell = row[sj] if sj < len(row) else None
        if not (isinstance(label_cell, str) and label_cell.strip()):
            break  # 块结束
        measure = label_cell.strip().rstrip(":").strip()
        vals: dict[str, float] = {}
        for j, ev in ev_col.items():
            cell = row[j] if j < len(row) else None
            if cell is None:
                continue
            vals[ev] = _num(cell, f"{path} Statistics[{measure}][{ev}]")
        stats[measure] = vals
    return stats


def parse_tank_xlsx(path) -> CsiTank:
    """解析一个孔位的事件表。详见模块 docstring 与规格 §4.2。

    行结构：第 1 行表头 `From Time | To Time | Length | Event`；之后是事件行；
    遇到 `Statistics` 单元格就停止收事件，其右为事件名表头、往下为度量行。
    **Statistics 块可能整个不存在**（规格说 8/28 个孔位如此；实测见 PR discrepancy C），
    没有时 statistics 返回空 dict，**不是错误**，带 ★ 的自检整体跳过。

    自检（不通过抛 CsiParseError）：
      - 每个 length_s 是 0.04 的整数倍（容差 1e-6）
      - 出现的事件名都在 CSI_EVENTS 里
      - ★ ranges_s[e] 与 statistics['Ranges'][e] 相等（容差 0.02），对块里出现的 e
      - ★ statistics['Average...'][e] == (Ranges[e]+Frames/Time[e])/2（容差 0.02），
        对同时出现在这三行里的 e
    `Swim` / `PassDive` 不在块的列里时，★ 不因 ranges_s 有它们而报错。
    """
    path = Path(path)
    rows = read_sheet(path)
    if not rows:
        raise CsiParseError(f"{path.name}: 空表")

    # —— 表头 ——
    header = [(c.strip() if isinstance(c, str) else c) for c in rows[0]]
    expected = ["From Time", "To Time", "Length", "Event"]
    if header[:4] != expected:
        raise CsiParseError(f"{path.name}: 表头不是 {expected}，实际 {header[:4]}")

    stat_pos = _find_statistics(rows)
    stat_row = stat_pos[0] if stat_pos else len(rows)

    # —— 事件行 ——
    events: list[CsiEvent] = []
    for i in range(1, stat_row):
        row = rows[i]
        if all(c is None for c in row):
            continue
        if len(row) < 4:
            raise CsiParseError(f"{path.name} 第{i+1}行: 不足 4 列 {row!r}")
        from_s = _time_or_num(row[0], f"{path.name} 第{i+1}行 From Time")
        to_s = _time_or_num(row[1], f"{path.name} 第{i+1}行 To Time")
        length_s = _num(row[2], f"{path.name} 第{i+1}行 Length")
        event = _text(row[3], f"{path.name} 第{i+1}行 Event")
        if event not in CSI_EVENTS:
            raise CsiParseError(f"{path.name} 第{i+1}行: 事件名 {event!r} 不在 CSI_EVENTS")
        events.append(CsiEvent(from_s=from_s, to_s=to_s, length_s=length_s, event=event))

    # —— 自检：Length 是 0.04 的整数倍 ——
    for k, e in enumerate(events):
        q = e.length_s / _LEN_TICK
        if abs(q - round(q)) * _LEN_TICK > _LEN_TOL:
            raise CsiParseError(
                f"{path.name} 第{k+1}条事件: Length={e.length_s} 不是 0.04 的整数倍")

    # —— ranges_s（只含出现过的事件；四舍五入到 0.01 抹掉累加尾数）——
    sums: dict[str, float] = {}
    for e in events:
        sums[e.event] = sums.get(e.event, 0.0) + e.length_s
    ranges_s = {k: round(v, 2) for k, v in sums.items()}
    window_s = round(sum(ranges_s.values()), 2)

    # —— Statistics 块 ——
    statistics: dict[str, dict[str, float]] = {}
    if stat_pos is not None:
        statistics = _parse_statistics_block(rows, stat_pos[0], stat_pos[1], path.name)
        # ★ ranges 与 statistics['Ranges'] 互相校验（只对块里出现的事件）
        ranges_row = statistics.get(_STAT_RANGES, {})
        for ev, sval in ranges_row.items():
            tval = ranges_s.get(ev, 0.0)
            if abs(tval - sval) > _RANGE_TOL:
                raise CsiParseError(
                    f"{path.name}: Ranges[{ev}] 自检失败 事件表={tval} 统计块={sval}")
        # ★ Average == (Ranges + Frames/Time)/2（只对三行都出现的事件）
        frames_row = statistics.get(_STAT_FRAMES, {})
        avg_row = statistics.get(_STAT_AVERAGE, {})
        for ev, aval in avg_row.items():
            if ev in ranges_row and ev in frames_row:
                expect = (ranges_row[ev] + frames_row[ev]) / 2.0
                if abs(aval - expect) > _AVG_TOL:
                    raise CsiParseError(
                        f"{path.name}: Average[{ev}] 自检失败 {aval} != ({ranges_row[ev]}"
                        f"+{frames_row[ev]})/2={expect}")

    rec, tank = parse_tank_filename(path.stem)
    if tank not in CSI_TANK_TO_CHAMBER:
        raise CsiParseError(f"{path.name}: 孔位号 {tank} 不在 CSI_TANK_TO_CHAMBER")
    return CsiTank(
        source_file=path.name,
        recording=rec,
        tank=tank,
        chamber=CSI_TANK_TO_CHAMBER[tank],
        events=tuple(events),
        ranges_s=ranges_s,
        statistics=statistics,
        window_s=window_s,
    )


def _time_or_num(v: object, ctx: str) -> float:
    """From/To 列：可能是时间标签 `1'39"`（str），也可能直接是数值秒。两种都收。"""
    if isinstance(v, str):
        return parse_time_label(v)
    return _num(v, ctx)


def immobile_ranges_s(tank: CsiTank) -> float:
    """CSI 侧唯一可用的不动时长（R1/R2）= Immobile 事件的 Length 之和。"""
    return tank.ranges_s.get("Immobile", 0.0)


def parse_bin_xlsx(path) -> list[dict]:
    """解析 Bin 汇总表。表头：
    `Trial ID | Tank ID | Events | Bouts1 | Total Bouts | Duration1(s) | Total Duration`。
    返回每 (trial_id, tank_id, event) 一条 dict。

    有两份：`Bin导出数据.xlsx`（20 trial）和 `28只鼠bin导出数据.xlsx`（28 trial）。
    **trial 数不写死**，按文件里实际的算（表头行靠 'Trial ID' 定位）。
    Bin 里的数字有的以文本存，_num 两种都收。
    """
    path = Path(path)
    rows = read_sheet(path)
    hdr_i = None
    for i, row in enumerate(rows):
        if row and isinstance(row[0], str) and row[0].strip() == "Trial ID":
            hdr_i = i
            break
    if hdr_i is None:
        raise CsiParseError(f"{path.name}: 找不到 'Trial ID' 表头行")
    hdr = [(c.strip() if isinstance(c, str) else c) for c in rows[hdr_i]]

    def col(name: str) -> int:
        for j, h in enumerate(hdr):
            if h == name:
                return j
        raise CsiParseError(f"{path.name}: 表头缺列 {name!r}，实际 {hdr}")

    c_tid, c_tank, c_ev = col("Trial ID"), col("Tank ID"), col("Events")
    c_b1, c_tb = col("Bouts1"), col("Total Bouts")
    c_d1, c_td = col("Duration1(s)"), col("Total Duration")

    out: list[dict] = []
    for row in rows[hdr_i + 1:]:
        if not row or row[c_tid] is None:
            continue
        out.append({
            "trial_id": int(_num(row[c_tid], f"{path.name} Trial ID")),
            "tank_id": int(_num(row[c_tank], f"{path.name} Tank ID")),
            "event": _text(row[c_ev], f"{path.name} Events"),
            "bouts1": _num(row[c_b1], f"{path.name} Bouts1"),
            "total_bouts": _num(row[c_tb], f"{path.name} Total Bouts"),
            "duration1_s": _num(row[c_d1], f"{path.name} Duration1(s)"),
            "total_duration": _num(row[c_td], f"{path.name} Total Duration"),
        })
    return out


def match_bin_to_tanks(bin_rows: Sequence[dict], tanks: Sequence[CsiTank]) -> dict[int, CsiTank]:
    """把 Bin 的 Trial ID 对上孔位——**靠时长匹配，不靠顺序猜**。

    对每个 Bin trial，取它各事件的 Total Duration 向量，和每个 CsiTank 的 ranges_s
    向量比；**全部事件（包括零值项）都在 0.02 s 内相等**才算命中。
    要求命中恰好一对一；不是一对一就抛 CsiParseError 并把冲突的 trial 号打出来。

    空杯位 `抑郁8-10（4）` 只有 Immobile+Swim 两个非零事件，向量特殊本来好匹配；
    但若只比 Immobile 一项会和别人撞——所以必须比全部事件、含零值项。
    """
    # 每个 trial 的时长向量（含所有出现过的 bin 事件名，零值项也要）
    bin_events = sorted({r["event"] for r in bin_rows})
    trial_vec: dict[int, dict[str, float]] = {}
    for r in bin_rows:
        trial_vec.setdefault(r["trial_id"], {})[r["event"]] = float(r["total_duration"])

    def vec_eq(a: dict[str, float], b: dict[str, float]) -> bool:
        for ev in bin_events:
            if abs(a.get(ev, 0.0) - b.get(ev, 0.0)) > _RANGE_TOL:
                return False
        return True

    # CsiTank 含 dict 字段、不可哈希，所以按下标存向量，不拿对象当 key
    tank_vec = [{ev: t.ranges_s.get(ev, 0.0) for ev in bin_events} for t in tanks]

    matched: dict[int, CsiTank] = {}
    used: set[int] = set()
    conflicts: list[str] = []
    for tid in sorted(trial_vec):
        hits = [k for k in range(len(tanks)) if vec_eq(trial_vec[tid], tank_vec[k])]
        if len(hits) != 1:
            conflicts.append(f"trial {tid} 命中 {len(hits)} 个孔位")
            continue
        k = hits[0]
        if k in used:
            conflicts.append(f"trial {tid} 与已匹配的 trial 撞同一个孔位")
            continue
        used.add(k)
        matched[tid] = tanks[k]

    if conflicts or len(matched) != len(trial_vec):
        raise CsiParseError(
            f"Bin↔孔位不是一对一（匹配 {len(matched)}/{len(trial_vec)}）: "
            + "; ".join(conflicts or ["有 trial 没匹配上"]))
    return matched


def parse_set(path) -> dict:
    """解析 CSI 的 FST 参数文件（.SET）。

    **表头是变长的。** 偏移 4 是孔位数 `n_tanks`，紧跟着 `n_tanks` 个 12 字节三元组，
    所以**后面每一个字段的位置都取决于 n_tanks**：

        base = 8 + 12 * n_tanks

    两份实测文件证实：`10mg 2周.SET` n_tanks=4 → base=56（文件 1611 字节）；
    `正常1-4对照更改.SET` n_tanks=1 → base=20（文件 1527 字节）。
    两份文件里**所有字段相对 base 的偏移完全一致**（差值恒为 36 = 3 个三元组）。

    **不许写死绝对偏移。** 这正是 2026-09-11 修掉的 bug：旧版按 n_tanks=4 的绝对偏移读，
    喂一份 n_tanks=1 的文件会**静默返回垃圾数字而不抛任何异常**
    （frame_padding 读成 -1711276032、high_cutoff 读成 6.16e-33）。
    最危险的一类失败：数字看着是数字，全是错的。

    结构校验：4 个哨兵对 (−1.0f, −1i) 必须落在 `base + 123 + 276*k`（k=0..3）。
    两份文件都成立。对不上就抛 CsiParseError —— **宁可大声失败，也不许猜着往下解析。**
    注意这 4 个哨兵块**恒为 4 个，与 n_tanks 无关**（n_tanks=1 时仍是 4 个），
    所以它们**不是**"每孔位一块"，具体是什么未知（R3，不起名）。

    键名见规格 §4.3 / §7.2。未确认的偏移一律 `unknown_rel_<相对 base 的偏移>`（R3）。
    **2026-09-11 起从 `unknown_off_*`（绝对）改名为 `unknown_rel_*`（相对）**——
    绝对偏移随 n_tanks 变化，拿它当键名本身就是错的。
    """
    path = Path(path)
    data = path.read_bytes()
    if len(data) < 8:
        raise CsiParseError(f"{path.name}: .SET 只有 {len(data)} 字节，连表头都不够")
    if data[:4] != SET_MAGIC:
        raise CsiParseError(f"{path.name}: magic {data[:4]!r} != {SET_MAGIC!r}")

    def i32(off: int) -> int:
        return struct.unpack_from("<i", data, off)[0]

    def f32(off: int) -> float:
        return struct.unpack_from("<f", data, off)[0]

    n_tanks = i32(4)
    if not 1 <= n_tanks <= SET_MAX_TANKS:
        raise CsiParseError(
            f"{path.name}: 孔位数 {n_tanks} 不在 1..{SET_MAX_TANKS}，拒绝按它算 base"
        )
    base = 8 + 12 * n_tanks

    need = base + SET_SENTINEL_REL + SET_SENTINEL_STRIDE * (SET_N_SENTINELS - 1) + 8
    if len(data) < need:
        raise CsiParseError(
            f"{path.name}: n_tanks={n_tanks} 需要至少 {need} 字节，实际只有 {len(data)}"
        )

    # 结构校验：4 个哨兵对必须都在预期位置。这是唯一跨两份文件都验证过的强不变量，
    # 比"文件总长等于某个公式"可靠（后者只有 2 个数据点，拟合出来的直线未经证实）。
    for k in range(SET_N_SENTINELS):
        off = base + SET_SENTINEL_REL + SET_SENTINEL_STRIDE * k
        if not (abs(f32(off) + 1.0) < 1e-6 and i32(off + 4) == -1):
            raise CsiParseError(
                f"{path.name}: 第 {k + 1} 个哨兵对应在偏移 {off}"
                f"（base={base}+{SET_SENTINEL_REL}+{SET_SENTINEL_STRIDE}*{k}），"
                f"实读 {f32(off)!r}/{i32(off + 4)!r} —— .SET 布局假设被打破，"
                f"不许猜着解析。拿这份文件去核对 docs 里的字节表。"
            )

    tank_triples = [tuple(i32(8 + 12 * k + d) for d in (0, 4, 8)) for k in range(n_tanks)]

    # base+51 起：13 个数 = 12 个 int32 + 1 个 float32。
    # 唯一那个 float32 是第 4 项（idx 3 = MaxMoveThresh = 2.0，0x40000000）。
    # 逐项对应见 MOTION_FIELD_HINTS —— 已定到"字段类型"，Struggle/Float 归属未定（R3）。
    motion_ints: list[object] = []
    for k in range(13):
        off = base + SET_MOTION_REL + 4 * k
        motion_ints.append(f32(off) if k == 3 else i32(off))

    return {
        "n_tanks": n_tanks,
        "base": base,
        "tank_triples": tank_triples,
        "unknown_rel_0": i32(base + 0),
        "frame_padding": i32(base + 4),
        "bkgd_gen_thresh": i32(base + 8),
        "only_change_bg_above_water": i32(base + 12),
        "high_cutoff": f32(base + 16),
        "low_cutoff": f32(base + 20),
        "learning_memory": f32(base + 24),
        "bool_block": data[base + 28 : base + 43],
        "struggle_esc_thresh": f32(base + 43),
        "float_immobile_thresh": f32(base + 47),
        "motion_ints": motion_ints,
        "unknown_rel_103": i32(base + 103),
        "unknown_rel_107": i32(base + 107),
        "unknown_rel_111": i32(base + 111),
        "unknown_rel_115": i32(base + 115),
        "unknown_rel_119": i32(base + 119),
        # base+123 / base+127 是第 1 个哨兵对（已在上面校验过），按 R3 仍用偏移名
        "unknown_rel_123": f32(base + 123),
        "unknown_rel_127": i32(base + 127),
    }


def parse_clb(path) -> dict:
    """`.CLB` 固定 308 字节。本阶段**只做无损转储**，不解释、不起名（R3）。

    返回 {'size', 'sha256', 'int32': [77 个], 'float32': [77 个]}。
    长度不是 308 抛 CsiParseError。
    """
    path = Path(path)
    data = path.read_bytes()
    if len(data) != 308:
        raise CsiParseError(f"{path.name}: .CLB 是 {len(data)} 字节，应为 308")
    n = len(data) // 4  # 77
    return {
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "int32": list(struct.unpack(f"<{n}i", data)),
        "float32": list(struct.unpack(f"<{n}f", data)),
    }


def load_csi_dir(csi_dir) -> tuple[list[CsiTank], dict, list[dict]]:
    """扫一个 CSI 导出目录，返回 (28 个孔位 CsiTank, .SET 参数 dict, .CLB 转储 list)。

    只挑文件名匹配 TANK_FILE_RE 的 .xlsx 当孔位表——Bin 汇总表（`Bin导出数据.xlsx` /
    `28只鼠bin导出数据.xlsx`）不含 `（N）`，自然被排除；.BMP/.SET/.CLB 后缀不符也排除。
    多个 .SET 时取唯一那个（本批 28 只鼠共用一份）；多于一个抛错，不猜。
    """
    csi_dir = Path(csi_dir)
    tanks: list[CsiTank] = []
    for f in sorted(csi_dir.iterdir()):
        if f.suffix.lower() == ".xlsx" and TANK_FILE_RE.match(f.stem):
            tanks.append(parse_tank_xlsx(f))

    sets = sorted(csi_dir.glob("*.SET"))
    if not sets:
        raise CsiParseError(f"{csi_dir}: 没有 .SET 文件")
    if len(sets) > 1:
        raise CsiParseError(f"{csi_dir}: 有 {len(sets)} 个 .SET，本批应只有一份，不猜: {[s.name for s in sets]}")
    set_params = parse_set(sets[0])

    clbs = [parse_clb(f) for f in sorted(csi_dir.glob("*.CLB"))]
    return tanks, set_params, clbs
