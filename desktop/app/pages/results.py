"""结果页：trial 表 + 分母 + 报警行（B4 交付，DP-107）。

架构纪律：
- 数字只读 CSV，上下文只读 run.json，不许在这里算任何科学量
- 文件名走 services/engine.output_paths()，不许再拼一次
- 不 import 引擎包（测试会守）
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QPushButton,
    QFileDialog,
    QHeaderView,
    QMessageBox,
)

from desktop.app.models.results import load_results, ResultsTable, ResultsRow, DENOMINATORS

# M2 研究版不随包标定文件；M3 计量版在此接入 paths.calibration_json()。
# 守卫会检查 export_* 调用的第三个实参不得出现字面量 None（DP-118）。
_CALIB_PATH_FOR_M2: Path | None = None


class ResultsPage(QWidget):
    """结果页：显示一个视频段的 trial 级数字 + 分母 + 报警行。"""

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)

        # 页面标题
        title = QLabel("结果")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        # 徽章容器（B8 往里挂发布态徽章，本单只留位置）
        badge_row = QHBoxLayout()
        badge_row.addStretch()
        self.badge_slot = QWidget()
        self.badge_slot.setObjectName("badgeSlot")
        badge_row.addWidget(self.badge_slot)
        layout.addLayout(badge_row)

        # 按钮行：打开目录 + 导出
        btn_row = QHBoxLayout()
        open_btn = QPushButton("打开输出目录…")
        open_btn.clicked.connect(self._on_open_directory)
        btn_row.addWidget(open_btn)

        # B6 导出按钮（只加按钮与调用，不动表格逻辑）
        self.export_btn = QPushButton("导出…")
        self.export_btn.setEnabled(False)  # 没有加载数据时禁用
        self.export_btn.clicked.connect(self._on_export)
        btn_row.addWidget(self.export_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # 元信息区域
        self.meta_label = QLabel("")
        self.meta_label.setWordWrap(True)
        layout.addWidget(self.meta_label)

        # 结果表格
        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table)

        # 存储当前加载的数据
        self._current_exp: dict | None = None
        self._current_video_index: int = 0

    def _on_open_directory(self):
        """打开输出目录，读取 experiment.json，加载结果。"""
        import json

        dir_path = QFileDialog.getExistingDirectory(
            self, "选择包含 experiment.json 的输出目录"
        )
        if not dir_path:
            return

        exp_json = Path(dir_path) / "experiment.json"
        if not exp_json.exists():
            self.meta_label.setText(f"错误：找不到 {exp_json}")
            return

        try:
            with exp_json.open("r", encoding="utf-8") as f:
                exp = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            self.meta_label.setText(f"读取 experiment.json 失败：{e}")
            return

        # 暂时只显示第一个视频的结果
        # TODO: 支持逐 video_index 列出，用户选择后显示
        self._current_exp = exp
        self._current_video_index = 0
        self._load_results()

    def _load_results(self):
        """加载并显示结果。"""
        if self._current_exp is None:
            return

        try:
            table_data = load_results(self._current_exp, self._current_video_index)
        except Exception as e:
            self.meta_label.setText(f"加载结果失败：{e}")
            return

        self._display_meta(table_data)
        self._display_table(table_data)
        self.export_btn.setEnabled(True)  # 有数据后启用导出按钮

    def _display_meta(self, table_data: ResultsTable):
        """显示页面顶部的元信息。"""
        parts = []

        if table_data.tool_version:
            parts.append(f"工具版本：{table_data.tool_version}")

        if table_data.assay:
            parts.append(f"范式：{table_data.assay}")

        if table_data.scoring_window_s:
            window_str = f"{table_data.scoring_window_s[0]}–{table_data.scoring_window_s[1]} s"
            parts.append(f"计分窗口：{window_str}")

        if table_data.theta_mob is not None:
            parts.append(f"θ_mob：{table_data.theta_mob}")

        if table_data.video_name:
            fps_str = f"{table_data.video_fps:.3g}" if table_data.video_fps else "?"
            n_frames_str = str(table_data.video_n_frames) if table_data.video_n_frames else "?"
            source_str = table_data.frame_count_source or "?"
            parts.append(
                f"视频：{table_data.video_name}  |  {fps_str} fps  |  "
                f"{n_frames_str} 帧（来源：{source_str}）"
            )

        if table_data.run_json_missing:
            parts.append("⚠️  run.json 缺失，上下文信息不完整")

        if table_data.csv_missing:
            parts.append("⚠️  CSV 缺失，所有隔间都是报警行")

        self.meta_label.setText("  |  ".join(parts) if parts else "无元信息")

    def _display_table(self, table_data: ResultsTable):
        """显示结果表格。"""
        rows = table_data.rows
        if not rows:
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            return

        # 构造列定义：每个秒数列后紧跟其分母列
        columns = self._build_columns()

        self.table.setRowCount(len(rows))
        self.table.setColumnCount(len(columns))
        self.table.setHorizontalHeaderLabels([col["header"] for col in columns])

        # 填充数据
        for row_idx, row in enumerate(rows):
            for col_idx, col_def in enumerate(columns):
                item = self._make_item(row, col_def)
                self.table.setItem(row_idx, col_idx, item)

            # 报警行用特殊背景色
            if row.kind == "alarm":
                for col_idx in range(len(columns)):
                    item = self.table.item(row_idx, col_idx)
                    if item:
                        item.setBackground(Qt.GlobalColor.yellow)

        # 调整列宽
        header = self.table.horizontalHeader()
        if header:
            header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)

    def _build_columns(self) -> list[dict]:
        """构造列定义：每个秒数列后紧跟其分母列。

        Returns:
            列定义列表，每项是 {"field": str, "header": str, "is_denominator": bool}
        """
        columns = []

        # 基础列
        columns.append({"field": "trial_id", "header": "Trial ID", "is_denominator": False})
        columns.append({"field": "chamber", "header": "隔间", "is_denominator": False})
        columns.append({"field": "assay", "header": "范式", "is_denominator": False})
        columns.append({"field": "validity_status", "header": "有效性", "is_denominator": False})
        columns.append({"field": "scored", "header": "已计分", "is_denominator": False})

        # 秒数列 + 分母列（按 DENOMINATORS 的顺序）
        # 注意：DENOMINATORS 的键就是秒数字段名
        for seconds_field in ["immobility_s", "immobility_raw_s", "mobility_s", "first_mobility_onset_s"]:
            if seconds_field in DENOMINATORS:
                # 秒数列
                header = seconds_field.replace("_", " ").title()
                # first_mobility_onset_s 需要特殊标注
                if seconds_field == "first_mobility_onset_s":
                    header = "First Mobility Onset S (相对计分窗起点)"
                columns.append({"field": seconds_field, "header": header, "is_denominator": False})

                # 分母列（可能有多个）
                for denom_field in DENOMINATORS[seconds_field]:
                    denom_header = denom_field.replace("_", " ").title() + " (分母)"
                    columns.append({"field": denom_field, "header": denom_header, "is_denominator": True})

        # 其他列
        columns.append({"field": "mobility_bouts", "header": "Mobility Bouts", "is_denominator": False})
        columns.append({"field": "occupied_fraction", "header": "在场占比", "is_denominator": False})
        columns.append({"field": "unknown_frames_window", "header": "Unknown Frames", "is_denominator": False})
        columns.append({"field": "gate_messages", "header": "备注", "is_denominator": False})
        columns.append({"field": "reason", "header": "报警原因", "is_denominator": False})

        return columns

    def _make_item(self, row: ResultsRow, col_def: dict) -> QTableWidgetItem:
        """根据列定义创建单元格。"""
        field = col_def["field"]
        value = getattr(row, field, None)

        # 空值显示为"—"
        if value is None or value == "":
            display = "—"
        else:
            display = str(value)

        item = QTableWidgetItem(display)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)  # 只读

        return item

    def _on_export(self) -> None:
        """导出按钮点击处理（B6 新增，只加按钮与调用，不动表格逻辑）。

        原子性契约：三份产物先渲染到临时子目录，三份都成功才移动到目标目录；
        任何一份失败则整体不留（DP-110 C7/C12）。
        """
        if self._current_exp is None:
            return

        import tempfile
        import shutil
        from desktop.app.services.calibration import evaluate_calibration
        from desktop.app.services.export import export_paths, export_xlsx, export_audit
        from desktop.app.services.export import export_pdf as _export_pdf
        from desktop.app.services.export_pdf import FontUnavailableError

        calib_status = evaluate_calibration(_CALIB_PATH_FOR_M2)
        e_paths = export_paths(
            self._current_exp, self._current_video_index, calib_status.mode
        )

        # 让用户选择导出到哪个目录
        dir_path = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if not dir_path:
            return

        out_dir = Path(dir_path)

        # 先渲染到临时子目录，全部成功才移到目标目录
        successes: list[str] = []
        failures: list[str] = []

        with tempfile.TemporaryDirectory() as _tmpdir:
            tmp = Path(_tmpdir)
            tmp_xlsx = tmp / e_paths["xlsx"].name
            tmp_audit = tmp / e_paths["audit_zip"].name
            tmp_pdf = tmp / e_paths["pdf"].name

            # xlsx
            try:
                export_xlsx(self._current_exp, self._current_video_index,
                            _CALIB_PATH_FOR_M2, tmp_xlsx)
                successes.append("xlsx")
            except Exception as e:
                failures.append(f"xlsx 导出失败：这是软件内部错误，请把这段话发给我们: {e}")

            # 审计包
            try:
                export_audit(self._current_exp, self._current_video_index,
                             _CALIB_PATH_FOR_M2, tmp_audit)
                successes.append("审计包")
            except Exception as e:
                failures.append(f"审计包导出失败：这是软件内部错误，请把这段话发给我们: {e}")

            # PDF
            try:
                _export_pdf(self._current_exp, self._current_video_index,
                            _CALIB_PATH_FOR_M2, tmp_pdf)
                successes.append("PDF")
            except FontUnavailableError:
                failures.append("PDF 未导出：请检查中文字体安装")
            except Exception as e:
                failures.append(f"PDF 导出失败：这是软件内部错误，请把这段话发给我们: {e}")

            # 原子移动：全部成功才移到目标目录
            if not failures:
                for src in [tmp_xlsx, tmp_audit, tmp_pdf]:
                    shutil.move(str(src), str(out_dir / src.name))

        # 弹窗反馈
        if not failures:
            QMessageBox.information(self, "导出成功", f"已导出到：{out_dir}")
        elif not successes:
            QMessageBox.critical(
                self, "导出失败",
                "三份产物全部失败，目标目录里没有任何文件。\n" + "\n".join(failures),
            )
        else:
            QMessageBox.warning(
                self, "部分导出失败",
                f"成功：{', '.join(successes)}\n失败（目标目录里没有这些文件）：\n"
                + "\n".join(failures),
            )
