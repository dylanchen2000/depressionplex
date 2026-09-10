"""DP-094：CSI（FST）结果导入测试。

金标准数字全部取自规格 §7（`docs/spec_DP-094_CSI_FST结果导入.md`），
是架构师实测跑出来的，**改任一断言前必须回规格核对**。

夹具在 `tests/fixtures/csi_fst/`（规格 §6 的 5 个文件）。
全 28 孔位的不变量（§7.3）需要外部 CSI 目录，缺了就 skip（打印一行后 return）。

三处与规格对不上的地方（A/B/C）在对应测试里用注释标了，详见 PR 正文 discrepancy 段：
  A. Statistics 块实测在 D 列、有 6 个事件列（规格散文说第 0 列、3 列）——解析器按通用定位。
  B. .SET 保留区 187–1610 实测有非零簇（规格说全零）——不解析/不校验/不起名。
  C. 实测 28/28 孔位都有 Statistics 块（规格说 8 个缺块）——块缺失分支用合成文件测。
"""
from __future__ import annotations

import os
import re
import struct
import tempfile
import zipfile
from pathlib import Path

from depressionplex.csi.fst_import import (
    CSI_EVENTS,
    CSI_TANK_TO_CHAMBER,
    RECORDING_ALIASES,
    SET_MAGIC,
    CsiEvent,
    CsiParseError,
    CsiTank,
    immobile_ranges_s,
    load_csi_dir,
    match_bin_to_tanks,
    parse_bin_xlsx,
    parse_clb,
    parse_set,
    parse_tank_filename,
    parse_tank_xlsx,
    parse_time_label,
)
from depressionplex.csi.xlsx import read_sheet
from depressionplex.human_agreement import load_audit_json

REPO = Path(__file__).resolve().parents[1]
FIX = REPO / "tests" / "fixtures" / "csi_fst"
HUMAN_INCOMING = REPO / "data" / "human_scores" / "incoming"

# 外部 CSI 目录（不在仓库里）。缺了相关测试 skip。
_CSI_DIR_CANDIDATES = [
    os.environ.get("DP094_CSI_DIR"),
    "/Users/dylanchen2000/Work/depression抑郁绝望/9月10日集中标注/CSI分析强迫游泳数据",
]


def _csi_dir() -> Path | None:
    for c in _CSI_DIR_CANDIDATES:
        if c and Path(c).is_dir():
            return Path(c)
    return None


def _expect_raise(fn, *a, **k) -> None:
    try:
        fn(*a, **k)
    except CsiParseError:
        return
    raise AssertionError(f"期望 CsiParseError，但没有抛: {fn} {a}")


# ---------------------------------------------------------------------------
# 合成 xlsx 写入（测"缺 Statistics 块"和 Bin 表——这两类没有现成夹具可进 git）
# 只用标准库 zipfile，不引新依赖。
# ---------------------------------------------------------------------------
def _col_idx(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _write_xlsx(path: Path, rows: list[dict[str, object]]) -> None:
    """rows: 每行是 {列字母: 值}，值 None/str/int/float。字符串走 inlineStr。"""
    sheet_rows = []
    for ri, row in enumerate(rows, 1):
        cells = []
        for col in sorted(row, key=_col_idx):
            v = row[col]
            if v is None:
                continue
            ref = f"{col}{ri}"
            if isinstance(v, str):
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{_esc(v)}</t></is></c>')
            else:
                cells.append(f'<c r="{ref}"><v>{v}</v></c>')
        sheet_rows.append(f'<row r="{ri}">{"".join(cells)}</row>')
    sheet = ('<?xml version="1.0" encoding="UTF-8"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             f'<sheetData>{"".join(sheet_rows)}</sheetData></worksheet>')
    ct = ('<?xml version="1.0" encoding="UTF-8"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
          '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
          '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>')
    wb = ('<?xml version="1.0" encoding="UTF-8"?>'
          '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
          '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>')
    wbrels = ('<?xml version="1.0" encoding="UTF-8"?>'
              '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
              '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
              '</Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", wbrels)
        z.writestr("xl/worksheets/sheet1.xml", sheet)


def _tank_rows(events: list[tuple[str, str, float, str]],
               stats: list[dict[str, object]] | None) -> list[dict[str, object]]:
    """搭一个孔位表的行。events: (from_label, to_label, length, name)。
    stats: None ⇒ 不写块；否则是块行（每行 {列字母: 值}，'Statistics' 放 D 列）。"""
    rows: list[dict[str, object]] = [
        {"A": "From Time", "B": "To Time", "C": "Length", "D": "Event"},
    ]
    for fr, to, ln, ev in events:
        rows.append({"A": fr, "B": to, "C": ln, "D": ev})
    if stats:
        rows.extend(stats)
    return rows


# ---------------------------------------------------------------------------
# parse_tank_filename / parse_time_label
# ---------------------------------------------------------------------------
def test_parse_tank_filename_basic_and_suffix() -> None:
    assert parse_tank_filename("正常1-4（1）") == ("正常1-4", 1)
    # 后缀必须被吃掉——`）$` 收尾会静默漏掉空鼠那一份（派工单 §7.2 的坑）
    assert parse_tank_filename("抑郁8-10（4）空鼠") == ("抑郁8-10", 4)
    assert parse_tank_filename("正常5+抑郁1-3（2）") == ("正常5+抑郁1-3", 2)


def test_parse_tank_filename_alias() -> None:
    # CSI 那边 "20mg 1周" 有空格，清单里没有 → 过 RECORDING_ALIASES
    assert parse_tank_filename("20mg 1周（2）") == ("20mg1周", 2)
    assert RECORDING_ALIASES["20mg 1周"] == "20mg1周"


def test_parse_tank_filename_bad() -> None:
    _expect_raise(parse_tank_filename, "正常1-4")          # 没有（N）
    _expect_raise(parse_tank_filename, "Bin导出数据")        # 汇总表
    _expect_raise(parse_tank_filename, "正常1-4（12）")      # 多位数孔位（正则只收 \d）


def test_parse_time_label() -> None:
    assert parse_time_label('0"') == 0.0
    assert parse_time_label('30"') == 30.0
    assert parse_time_label('1\'39"') == 99.0     # 规格 §7.1 金标准
    assert parse_time_label('10\'05"') == 605.0


def test_parse_time_label_bad() -> None:
    for bad in ("", "abc", "30", '1\'39', "1'39\"x", None, 12.0):
        _expect_raise(parse_time_label, bad)


# ---------------------------------------------------------------------------
# read_sheet
# ---------------------------------------------------------------------------
def test_read_sheet_golden() -> None:
    rows = read_sheet(FIX / "正常1-4（1）.xlsx")
    hdr = [(c.strip() if isinstance(c, str) else c) for c in rows[0][:4]]
    assert hdr == ["From Time", "To Time", "Length", "Event"]
    # 第 2 行（首条事件）：Length 是数值单元格 0.28
    assert rows[1][2] == 0.28
    assert rows[1][3] == "Swim"


# ---------------------------------------------------------------------------
# parse_tank_xlsx —— §7.1 金标准（正常1-4（1））
# ---------------------------------------------------------------------------
def test_tank_golden_7_1() -> None:
    t = parse_tank_xlsx(FIX / "正常1-4（1）.xlsx")
    assert t.recording == "正常1-4" and t.tank == 1 and t.chamber == 1
    assert len(t.events) == 31
    assert t.ranges_s["Immobile"] == 21.76
    assert t.ranges_s["Swim"] == 9.52
    assert t.ranges_s["Escape"] == 363.40
    assert t.window_s == 394.68
    assert t.statistics["Frames/Time"]["Immobile"] == 207.36
    assert t.statistics["Ranges"]["Immobile"] == 21.76
    assert t.statistics["Average of Frames and Ranges"]["Immobile"] == 114.56
    assert t.statistics["Ranges"]["Climb"] == 0.0
    assert t.events[0] == CsiEvent(from_s=0.0, to_s=0.0, length_s=0.28, event="Swim")
    assert t.events[1] == CsiEvent(from_s=0.0, to_s=30.0, length_s=30.2, event="Escape")
    assert immobile_ranges_s(t) == 21.76


def test_tank_climb_nonzero_7_3() -> None:
    # 正常5+抑郁1-3（2）：Climb=7.76（规格 §7.3 修正条）。
    # 注：规格 §6 说这份"无 Statistics 块"，**实测它有完整的块**（discrepancy C）。
    t = parse_tank_xlsx(FIX / "正常5+抑郁1-3（2）.xlsx")
    assert len(t.events) == 91
    assert t.window_s == 365.52
    assert t.ranges_s == {"Swim": 21.16, "Escape": 289.52, "Immobile": 47.08, "Climb": 7.76}
    assert t.statistics, "实测这份有 Statistics 块（与规格 §6 的'无块'说法不符，见 PR）"
    assert t.statistics["Ranges"]["Climb"] == 7.76


def test_tank_empty_cup_7_3() -> None:
    # 抑郁8-10（4）空鼠：CSI 把空杯报成几乎全程不动。
    # 这条是"CSI 没有空孔位检测"的回归留证（规格 §7.3 要求 > 0.99）。
    t = parse_tank_xlsx(FIX / "抑郁8-10（4）空鼠.xlsx")
    assert t.recording == "抑郁8-10" and t.tank == 4 and t.chamber == 4
    assert len(t.events) == 2
    assert t.window_s == 467.24
    assert t.ranges_s == {"Immobile": 466.88, "Swim": 0.36}
    assert immobile_ranges_s(t) / t.window_s > 0.99


# ---------------------------------------------------------------------------
# Statistics 块缺失 / 自检（合成文件——没有现成夹具，见 discrepancy C）
# ---------------------------------------------------------------------------
def test_missing_statistics_block_is_ok() -> None:
    # 块整个不存在 ⇒ statistics == {}，不是错误，★ 自检整体跳过。
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "正常1-4（1）.xlsx"
        _write_xlsx(p, _tank_rows(
            [('0"', '30"', 30.2, "Escape"), ('30"', '60"', 29.8, "Immobile")],
            stats=None))
        t = parse_tank_xlsx(p)
        assert t.statistics == {}
        assert t.ranges_s == {"Escape": 30.2, "Immobile": 29.8}
        assert t.window_s == 60.0


def test_statistics_ranges_mismatch_raises() -> None:
    # ★ Ranges 行与事件表 Length 之和不一致 ⇒ CsiParseError
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "正常1-4（1）.xlsx"
        _write_xlsx(p, _tank_rows(
            [('0"', '30"', 30.2, "Escape"), ('30"', '60"', 29.8, "Immobile")],
            stats=[
                {"D": "Statistics", "E": "Escape", "F": "Immobile"},
                {"D": "Frames/Time:", "E": 30.2, "F": 29.8},
                {"D": "Ranges:", "E": 999.0, "F": 29.8},   # Escape 对不上
                {"D": "Average of Frames and Ranges:", "E": 30.2, "F": 29.8},
            ]))
        _expect_raise(parse_tank_xlsx, p)


def test_statistics_average_mismatch_raises() -> None:
    # ★ Average != (Ranges+Frames/Time)/2 ⇒ CsiParseError
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "正常1-4（1）.xlsx"
        _write_xlsx(p, _tank_rows(
            [('0"', '30"', 30.2, "Escape"), ('30"', '60"', 29.8, "Immobile")],
            stats=[
                {"D": "Statistics", "E": "Escape", "F": "Immobile"},
                {"D": "Frames/Time:", "E": 30.2, "F": 29.8},
                {"D": "Ranges:", "E": 30.2, "F": 29.8},
                {"D": "Average of Frames and Ranges:", "E": 99.0, "F": 99.0},  # 错
            ]))
        _expect_raise(parse_tank_xlsx, p)


def test_length_not_multiple_of_tick_raises() -> None:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "正常1-4（1）.xlsx"
        _write_xlsx(p, _tank_rows([('0"', '30"', 30.21, "Escape")], stats=None))
        _expect_raise(parse_tank_xlsx, p)


def test_unknown_event_name_raises() -> None:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "正常1-4（1）.xlsx"
        _write_xlsx(p, _tank_rows([('0"', '30"', 30.2, "Fly")], stats=None))
        _expect_raise(parse_tank_xlsx, p)


# ---------------------------------------------------------------------------
# parse_set —— §7.2 金标准
# ---------------------------------------------------------------------------
def test_set_golden_7_2() -> None:
    s = parse_set(FIX / "10mg 2周.SET")
    assert s["n_tanks"] == 4
    assert s["tank_triples"] == [(200, 80, 70), (200, 70, 60), (200, 80, 80), (200, 60, 60)]
    assert s["unknown_off_56"] == 18
    assert s["frame_padding"] == 10
    assert s["bkgd_gen_thresh"] == 5000
    assert s["only_change_bg_above_water"] == 1
    assert abs(s["high_cutoff"] - 0.2) < 1e-6
    assert abs(s["low_cutoff"] - 0.01) < 1e-6
    assert abs(s["learning_memory"] - 0.95) < 1e-6
    assert abs(s["struggle_esc_thresh"] - 0.11) < 1e-6
    assert abs(s["float_immobile_thresh"] - 0.09) < 1e-6
    assert s["motion_ints"] == [15, 10, 15, 2.0, 5, 15, 15, 10, 20, 5, 10, 20, 5]
    assert s["bool_block"] == bytes([0, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0])
    assert s["unknown_off_159"] == 1
    assert s["unknown_off_163"] == 1
    assert s["unknown_off_167"] == 1
    assert s["unknown_off_171"] == 0
    assert s["unknown_off_175"] == 0


def test_set_motion_multiset_matches_panel() -> None:
    # 规格 §7.2：多重集与面板一致，但**不许**断言逐项对应关系。
    # 面板 13 个数值字段：Struggle 组 20/15/10/5/5/15/10/15/2 + Float 组 20/15/10/5。
    s = parse_set(FIX / "10mg 2周.SET")
    panel = [20, 15, 10, 5, 5, 15, 10, 15, 2, 20, 15, 10, 5]
    assert sorted(s["motion_ints"]) == sorted(panel)


def test_set_bad_magic_raises() -> None:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "bad.SET"
        p.write_bytes(b"XXXX" + b"\x00" * 200)
        _expect_raise(parse_set, p)


def test_set_too_short_raises() -> None:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "short.SET"
        p.write_bytes(SET_MAGIC + b"\x00" * 10)
        _expect_raise(parse_set, p)


# ---------------------------------------------------------------------------
# parse_clb
# ---------------------------------------------------------------------------
def test_clb_golden() -> None:
    c = parse_clb(FIX / "正常1-4.CLB")
    assert c["size"] == 308
    assert c["sha256"] == "428b930e980d838fcaa11f473644def0c010a6c3b4d29b0e5f22481afbe67fec"
    assert len(c["int32"]) == 77
    assert len(c["float32"]) == 77
    # 无损转储：int32 重打包应还原原字节
    raw = (FIX / "正常1-4.CLB").read_bytes()
    assert struct.pack("<77i", *c["int32"]) == raw


def test_clb_wrong_size_raises() -> None:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "bad.CLB"
        p.write_bytes(b"\x00" * 100)
        _expect_raise(parse_clb, p)


# ---------------------------------------------------------------------------
# parse_bin_xlsx + match_bin_to_tanks（合成 Bin 表 + 真夹具孔位）
# ---------------------------------------------------------------------------
def _bin_rows_for(tanks: list[CsiTank]) -> list[dict]:
    """按真孔位的 ranges_s 造 Bin 行（每孔位 6 个事件，含零值项）。"""
    bin_events = ["Escape", "Immobile", "Climb", "Dive", "PassDive", "Swim"]
    rows = []
    for tid, t in enumerate(tanks, 1):
        for ev in bin_events:
            rows.append({
                "trial_id": tid, "tank_id": t.tank, "event": ev,
                "bouts1": 0.0, "total_bouts": 0.0,
                "duration1_s": t.ranges_s.get(ev, 0.0),
                "total_duration": t.ranges_s.get(ev, 0.0),
            })
    return rows


def test_parse_bin_xlsx_synthetic() -> None:
    t1 = parse_tank_xlsx(FIX / "正常1-4（1）.xlsx")
    t3 = parse_tank_xlsx(FIX / "抑郁8-10（4）空鼠.xlsx")
    bin_rows = _bin_rows_for([t1, t3])
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "bin.xlsx"
        xrows = [
            {"A": "ForcedSwimming Bin Result ROOT:test"},
            {"A": "Number of Trials", "B": 2},
            {"A": "Trial ID", "B": "Tank ID", "C": "Events",
             "D": " Bouts1 ", "E": "Total Bouts", "F": "Duration1(s) ", "G": "Total Duration"},
        ]
        for r in bin_rows:
            xrows.append({"A": r["trial_id"], "B": r["tank_id"], "C": r["event"],
                          "D": r["bouts1"], "E": r["total_bouts"],
                          "F": r["duration1_s"], "G": r["total_duration"]})
        _write_xlsx(p, xrows)
        parsed = parse_bin_xlsx(p)
    assert len(parsed) == 12  # 2 trial × 6 事件
    assert {r["trial_id"] for r in parsed} == {1, 2}
    by_trial = {}
    for r in parsed:
        by_trial.setdefault(r["trial_id"], {})[r["event"]] = r["total_duration"]
    assert by_trial[1]["Immobile"] == 21.76   # t1
    assert by_trial[2]["Immobile"] == 466.88  # t3 空杯


def test_match_bin_to_tanks_by_duration_not_order() -> None:
    t1 = parse_tank_xlsx(FIX / "正常1-4（1）.xlsx")
    t3 = parse_tank_xlsx(FIX / "抑郁8-10（4）空鼠.xlsx")
    # 故意把 trial 顺序倒过来：trial 1 = 空杯向量，trial 2 = 正常1-4 向量
    bin_rows = _bin_rows_for([t3, t1])
    matched = match_bin_to_tanks(bin_rows, [t1, t3])
    assert matched[1] is t3   # 靠时长匹配，不靠顺序
    assert matched[2] is t1


def test_match_bin_ambiguous_raises() -> None:
    t1 = parse_tank_xlsx(FIX / "正常1-4（1）.xlsx")
    bin_rows = _bin_rows_for([t1])
    # 同一个孔位放两次 ⇒ 一个 trial 命中 2 个 ⇒ 不是一对一
    _expect_raise(match_bin_to_tanks, bin_rows, [t1, t1])


# ---------------------------------------------------------------------------
# §7.4 人工侧金标准（仓库内数据，确定性； tied to incoming commit 4209f24 / PR #78）
# ---------------------------------------------------------------------------
def test_human_side_goldens_7_4() -> None:
    files = sorted(HUMAN_INCOMING.glob("timer_audit_FST_*.json"))
    assert files, f"{HUMAN_INCOMING} 里没有 FST 审计文件"
    total = acc = uns = ni = hu = 0
    named = {}
    for p in files:
        m = re.search(r"_(\d{4}-\d{2}-\d{2})_", p.name)
        batch = m.group(1) if m else ""
        header, rows = load_audit_json(p, on_reject="mark")
        sid = header["scorer_id"]
        for r in rows:
            total += 1
            if r.status == "accepted":
                acc += 1
            if r.status == "unscoreable":
                uns += 1
            if (r.naive_inflation_s or 0) > 0.5:
                ni += 1
            if r.holds_unsorted:
                hu += 1
            if r.trial_id == "FST-正常1-4-ch3" and batch == "2026-09-10":
                named[sid] = r.immobility_s
    assert total == 92, total
    assert acc == 88, acc
    assert uns == 4, uns
    assert ni == 41, ni
    assert hu == 42, hu
    assert named.get("徐乐彤") == 200.72, named
    assert named.get("陈璇") == 215.75, named


# ---------------------------------------------------------------------------
# §7.3 全 28 孔位不变量（需外部 CSI 目录，缺了 skip）
# ---------------------------------------------------------------------------
def test_all_28_tanks_invariants() -> None:
    csi = _csi_dir()
    if csi is None:
        print("  SKIP  test_all_28_tanks_invariants（无外部 CSI 目录）")
        return
    tanks, set_params, clbs = load_csi_dir(csi)
    assert len(tanks) == 28, len(tanks)
    recs = {}
    for t in tanks:
        recs.setdefault(t.recording, []).append(t)
    assert len(recs) == 7, sorted(recs)
    for rec, ts in recs.items():
        assert len(ts) == 4, (rec, len(ts))
        ws = [t.window_s for t in ts]
        assert max(ws) - min(ws) <= 0.8, (rec, ws)

    # PassDive 只在 抑郁4-7（1）出现，合计 6.08
    pd = {(t.recording, t.tank): t.ranges_s.get("PassDive", 0.0) for t in tanks}
    pd_nz = {k: v for k, v in pd.items() if v}
    assert pd_nz == {("抑郁4-7", 1): 6.08}, pd_nz

    # Climb 只在 2 个孔位非零
    cl = {(t.recording, t.tank): t.ranges_s.get("Climb", 0.0) for t in tanks}
    cl_nz = {k: v for k, v in cl.items() if v}
    assert cl_nz == {("正常5+抑郁1-3", 2): 7.76, ("正常5+抑郁1-3", 4): 12.68}, cl_nz

    # .SET / .CLB 顺带核对
    assert set_params["n_tanks"] == 4
    assert len(clbs) == 7 and all(c["size"] == 308 for c in clbs)

    # 8 个新孔位金标准（规格 §7.3 表）
    golden8 = {
        ("抑郁8-10", 1): (25, 467.52, 83.24, 0.80, 383.48, 0.0),
        ("抑郁8-10", 2): (57, 467.52, 153.08, 6.44, 308.00, 0.0),
        ("抑郁8-10", 3): (47, 467.24, 238.72, 0.36, 228.16, 0.0),
        ("抑郁8-10", 4): (2, 467.24, 466.88, 0.36, 0.00, 0.0),
        ("正常5+抑郁1-3", 1): (28, 365.52, 48.60, 8.56, 308.36, 0.0),
        ("正常5+抑郁1-3", 2): (91, 365.52, 47.08, 21.16, 289.52, 7.76),
        ("正常5+抑郁1-3", 3): (48, 365.52, 37.72, 2.24, 325.56, 0.0),
        ("正常5+抑郁1-3", 4): (92, 365.52, 13.36, 52.88, 286.60, 12.68),
    }
    by_key = {(t.recording, t.tank): t for t in tanks}
    for key, (en, w, imm, sw, esc, climb) in golden8.items():
        t = by_key[key]
        assert len(t.events) == en, (key, len(t.events))
        assert t.window_s == w, (key, t.window_s)
        assert t.ranges_s.get("Immobile", 0.0) == imm, (key, t.ranges_s)
        assert t.ranges_s.get("Swim", 0.0) == sw, (key, t.ranges_s)
        assert t.ranges_s.get("Escape", 0.0) == esc, (key, t.ranges_s)
        assert t.ranges_s.get("Climb", 0.0) == climb, (key, t.ranges_s)

    # ---- discrepancy C：规格 §7.3 说"有块=20、缺块的 8 个"，实测 28/28 都有块 ----
    # 这 8 份文件（抑郁8-10 ×4、正常5+抑郁1-3 ×4）的 mtime 是 09-10 17:07，
    # 比另外 20 份（14:35–14:36）晚——规格考据后被重导过，旧结论已过时。
    # 按"照实报，别凑"：断言实测的 28，不迁就规格里过时的 20。块缺失分支由
    # test_missing_statistics_block_is_ok 用合成文件覆盖（解析器仍然支持缺块）。
    with_block = sum(1 for t in tanks if t.statistics)
    assert with_block == 28, f"实测有 Statistics 块的孔位数={with_block}（规格旧结论说 20，见 PR discrepancy C）"


def test_match_bin_real_28() -> None:
    csi = _csi_dir()
    if csi is None:
        print("  SKIP  test_match_bin_real_28（无外部 CSI 目录）")
        return
    bin_path = csi / "28只鼠bin导出数据.xlsx"
    if not bin_path.is_file():
        print("  SKIP  test_match_bin_real_28（无 28只鼠bin导出数据.xlsx）")
        return
    tanks, _s, _c = load_csi_dir(csi)
    bin_rows = parse_bin_xlsx(bin_path)
    matched = match_bin_to_tanks(bin_rows, tanks)
    assert len(matched) == 28, len(matched)
    assert len(set(id(t) for t in matched.values())) == 28  # 一对一


# ---------------------------------------------------------------------------
# 常量自检
# ---------------------------------------------------------------------------
def test_constants() -> None:
    assert CSI_EVENTS == ("Immobile", "Swim", "Escape", "Climb", "PassDive")
    assert CSI_TANK_TO_CHAMBER == {1: 1, 2: 2, 3: 3, 4: 4}
    assert SET_MAGIC == b"FSS3"
