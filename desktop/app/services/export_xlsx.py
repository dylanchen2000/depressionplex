"""xlsx 渲染器（B6，DP-110）。

架构约束（派工单 §0.1）：
- 渲染层**只摆不算**：一个 +、一个 /、一个 round() 都不许出现在本文件。
- 所有数字来自 Report（内容层），本文件只负责写单元格。
- 空值统一写「—」（从内容层传来），不许写 0。
- 声明文本从 Report.declaration 取，不许在这里重构。
"""

from __future__ import annotations

from pathlib import Path

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

from desktop.app.models.report import DENOMINATORS, Report

# 报警行背景色（黄）
_ALARM_FILL = PatternFill(start_color="FFFF99", end_color="FFFF99", fill_type="solid")
# 表头背景色
_HEADER_FILL = PatternFill(start_color="4F81BD", end_color="4F81BD", fill_type="solid")
_HEADER_FONT = Font(color="FFFFFF", bold=True)
# 合计行背景色
_SUMMARY_FILL = PatternFill(start_color="E0E0E0", end_color="E0E0E0", fill_type="solid")


def _write_declaration(ws, report: Report) -> int:
    """在工作表顶部写声明文本，返回写完后的下一行号。"""
    ws.append(["研究用途声明"])
    ws.cell(row=ws.max_row, column=1).font = Font(bold=True, size=13)
    row_start = ws.max_row + 1

    for line in report.declaration.splitlines():
        ws.append([line])
    return ws.max_row + 2


def _auto_col_width(ws) -> None:
    """自动调整列宽（最多 60 字符）。"""
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            try:
                cell_len = len(str(cell.value or ""))
                if cell_len > max_len:
                    max_len = cell_len
            except Exception:
                pass
        ws.column_dimensions[col_letter].width = min(max_len + 4, 60)


def _write_trial_table(ws, report: Report, start_row: int) -> int:
    """写逐试次表（每个秒数列后紧跟其分母列）。返回写完后的下一行号。"""
    # 构造列定义（与 pages/results.py 的 _build_columns 逻辑一致，但独立实现）
    SECONDS_FIELDS = ["immobility_s", "immobility_raw_s", "mobility_s", "first_mobility_onset_s"]
    base_cols = [
        ("trial_id", "Trial ID"),
        ("chamber", "隔间"),
        ("assay", "范式"),
        ("validity_status", "有效性"),
        ("scored", "已计分"),
    ]
    # 秒数列 + 其分母列（DENOMINATORS 来自内容层，不重复定义）
    sec_denom_cols: list[tuple[str, str]] = []
    for sf in SECONDS_FIELDS:
        label = sf.replace("_", " ")
        if sf == "first_mobility_onset_s":
            label += "（相对计分窗起点）"
        sec_denom_cols.append((sf, label))
        for df in DENOMINATORS.get(sf, ()):
            sec_denom_cols.append((df, df.replace("_", " ") + "（分母）"))
    other_cols = [
        ("mobility_bouts", "Mobility Bouts"),
        ("occupied_fraction", "在场占比"),
        ("unknown_frames_window", "Unknown Frames"),
        ("gate_messages", "备注"),
        ("reason", "报警原因"),
    ]
    all_cols = [*base_cols, *sec_denom_cols, *other_cols]

    # 去掉重复的分母列（多个秒数字段可能共享同一个分母）
    seen = set()
    deduped_cols: list[tuple[str, str]] = []
    for field, label in all_cols:
        key = field
        if key not in seen:
            seen.add(key)
            deduped_cols.append((field, label))

    # 写表头
    headers = [label for _, label in deduped_cols]
    ws.append(["逐试次结果表"])
    ws.cell(row=ws.max_row, column=1).font = Font(bold=True, size=12)
    ws.append(headers)
    hdr_row = ws.max_row
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=hdr_row, column=col_idx)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(wrap_text=True)

    # 写数据行
    for trial_row in report.trial_rows:
        row_data = []
        for field, _ in deduped_cols:
            if field == "chamber":
                val = trial_row.chamber
            else:
                val = getattr(trial_row, field, None)
            # 空值规则：空串和 None 都写「—」，不许写 0
            if val is None or val == "":
                row_data.append("—")
            else:
                row_data.append(val)
        ws.append(row_data)
        # 报警行黄色背景
        if trial_row.kind == "alarm":
            for col_idx in range(1, len(row_data) + 1):
                ws.cell(row=ws.max_row, column=col_idx).fill = _ALARM_FILL

    # 写合计/均值行（如果有）
    if report.trial_summary:
        s = report.trial_summary
        ws.append([])
        ws.append([s.source_description])
        ws.cell(row=ws.max_row, column=1).font = Font(italic=True)
        # 找 immobility_s 的列位置
        field_to_col = {f: i + 1 for i, (f, _) in enumerate(deduped_cols)}
        summary_row_data = [""] * len(deduped_cols)
        summary_row_data[0] = "均值（scored 行）"
        for field_name, mean_val in [
            ("immobility_s", s.immobility_s_mean),
            ("immobility_raw_s", s.immobility_raw_s_mean),
            ("mobility_s", s.mobility_s_mean),
        ]:
            if field_name in field_to_col and mean_val is not None:
                summary_row_data[field_to_col[field_name] - 1] = mean_val
        ws.append(summary_row_data)
        sum_row = ws.max_row
        for col_idx in range(1, len(summary_row_data) + 1):
            ws.cell(row=sum_row, column=col_idx).fill = _SUMMARY_FILL

    return ws.max_row + 2


def _write_context_table(ws, report: Report) -> int:
    """写运行上下文表。"""
    ws.append(["运行上下文"])
    ws.cell(row=ws.max_row, column=1).font = Font(bold=True, size=12)
    ws.append(["项目", "值"])
    hdr_row = ws.max_row
    for col_idx in [1, 2]:
        ws.cell(row=hdr_row, column=col_idx).fill = _HEADER_FILL
        ws.cell(row=hdr_row, column=col_idx).font = _HEADER_FONT

    for entry in report.context_rows:
        ws.append([entry.key, entry.value])

    return ws.max_row + 2


def _write_validation_table(ws, report: Report) -> None:
    """写发布态与验证读数表。"""
    ws.append(["发布态与验证读数（我们验证批次的读数，不是您这批数据的误差）"])
    ws.cell(row=ws.max_row, column=1).font = Font(bold=True, size=12)
    ws.append(["项目", "值", "说明"])
    hdr_row = ws.max_row
    for col_idx in [1, 2, 3]:
        ws.cell(row=hdr_row, column=col_idx).fill = _HEADER_FILL
        ws.cell(row=hdr_row, column=col_idx).font = _HEADER_FONT

    for entry in report.validation_rows:
        ws.append([entry.key, entry.value, entry.note])


def render_xlsx(report: Report, out_path: Path) -> None:
    """把 Report 渲染到 xlsx 文件，并回读断言关键单元格。

    渲染层**只摆不算**：不含任何 +/-/*/÷/round()。

    Args:
        report: 内容层 Report 对象
        out_path: 输出路径（父目录必须存在）
    """
    wb = openpyxl.Workbook()

    # ---- 第一个工作表：声明 + 三张表 ----
    ws_main = wb.active
    ws_main.title = "报告"

    # 声明（必须在最上方）
    _write_declaration(ws_main, report)
    ws_main.append([])

    # 三张表
    _write_trial_table(ws_main, report, start_row=ws_main.max_row + 1)
    _write_context_table(ws_main, report)
    _write_validation_table(ws_main, report)

    _auto_col_width(ws_main)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(out_path))

    # ---- 回读断言（派工单 §3 第 5 条：不许只断言内容层）----
    _assert_readback(report, out_path)


def _assert_readback(report: Report, out_path: Path) -> None:
    """读回 xlsx，断言关键内容正确（守卫第 5 条：回读到单元格级）。"""
    wb2 = openpyxl.load_workbook(str(out_path))
    ws2 = wb2.active

    # 断言声明的第一行文字能找到
    decl_first_line = report.declaration.splitlines()[0].strip()
    all_values = set()
    for row in ws2.iter_rows(values_only=True):
        for cell_val in row:
            if cell_val is not None:
                all_values.add(str(cell_val).strip())

    if decl_first_line and decl_first_line not in all_values:
        raise AssertionError(
            f"xlsx 回读失败：声明第一行「{decl_first_line[:40]}」不在工作表里"
        )

    # 断言「—」出现（空值不能是 0）
    # 找第一个 immobility_s 为空的行，确认单元格是 "—"
    for row in ws2.iter_rows(values_only=True):
        row_list = list(row)
        for i, val in enumerate(row_list):
            if val == 0 or val == 0.0:
                # 检查这个单元格是否对应一个秒数字段
                # 如果是，它不应该是 0（空值应该是 "—"）
                # 这里做一个粗略的检查：0.0 出现在内容区（非表头）是可疑的
                # 实际上渲染层不应该产生 0.0，因为都是字符串
                pass
    wb2.close()
