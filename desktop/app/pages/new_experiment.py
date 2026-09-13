"""新建实验向导（B2 交付）。

四步收集：范式 + 视频 + 参数 + 输出目录。
完成时写出 experiment.json 并发信号 experiment_created。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QLineEdit,
    QFileDialog,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QFormLayout,
    QGroupBox,
    QSpinBox,
    QDoubleSpinBox,
)

from desktop.app.assays import ASSAY_WINDOWS_UI
from desktop.app.models.experiment import (
    ExperimentPlan,
    VideoEntry,
    validate_experiment_plan,
    write_experiment_json,
)


class VideoListItem(QListWidgetItem):
    """视频列表项（存储路径和 trial_prefix）。"""

    def __init__(self, video_path: Path, trial_prefix: str | None = None):
        super().__init__()
        self.video_path = video_path
        self.trial_prefix = trial_prefix
        self._update_display()

    def _update_display(self) -> None:
        """更新显示文本。"""
        prefix_text = f" [{self.trial_prefix}]" if self.trial_prefix else ""
        self.setText(f"{self.video_path.name}{prefix_text}")

    def set_trial_prefix(self, prefix: str | None) -> None:
        """设置 trial_prefix（空字符串转 None）。"""
        self.trial_prefix = prefix if prefix else None
        self._update_display()


class NewExperimentPage(QWidget):
    """新建实验向导页面。"""

    # 信号：实验创建完成，参数为 experiment.json 的绝对路径
    experiment_created = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        # 标题
        title = QLabel("新建实验")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        # === 第一步：范式选择 ===
        assay_group = QGroupBox("1. 选择范式")
        assay_layout = QFormLayout(assay_group)

        self.assay_combo = QComboBox()
        # 选项从 ASSAY_WINDOWS_UI 的键生成（不许硬编码字面量）
        self.assay_combo.addItems(sorted(ASSAY_WINDOWS_UI.keys()))
        self.assay_combo.currentTextChanged.connect(self._on_assay_changed)
        assay_layout.addRow("范式：", self.assay_combo)

        self.window_label = QLabel()
        self.window_label.setStyleSheet("color: #888; font-style: italic;")
        assay_layout.addRow("计分窗口：", self.window_label)

        layout.addWidget(assay_group)

        # === 第二步：视频列表 ===
        video_group = QGroupBox("2. 添加视频")
        video_layout = QVBoxLayout(video_group)

        btn_layout = QHBoxLayout()
        self.btn_add_videos = QPushButton("添加视频")
        self.btn_add_videos.clicked.connect(self._add_videos)
        btn_layout.addWidget(self.btn_add_videos)

        self.btn_remove_video = QPushButton("移除选中")
        self.btn_remove_video.clicked.connect(self._remove_selected_video)
        btn_layout.addWidget(self.btn_remove_video)

        self.btn_move_up = QPushButton("上移")
        self.btn_move_up.clicked.connect(self._move_video_up)
        btn_layout.addWidget(self.btn_move_up)

        self.btn_move_down = QPushButton("下移")
        self.btn_move_down.clicked.connect(self._move_video_down)
        btn_layout.addWidget(self.btn_move_down)

        btn_layout.addStretch()
        video_layout.addLayout(btn_layout)

        self.video_list = QListWidget()
        self.video_list.itemSelectionChanged.connect(self._update_video_buttons)
        video_layout.addWidget(self.video_list)

        # Trial prefix 编辑
        prefix_layout = QHBoxLayout()
        prefix_layout.addWidget(QLabel("选中视频的 trial_prefix（可选）："))
        self.trial_prefix_edit = QLineEdit()
        self.trial_prefix_edit.setPlaceholderText("留空则使用文件名")
        self.trial_prefix_edit.textChanged.connect(self._on_trial_prefix_changed)
        prefix_layout.addWidget(self.trial_prefix_edit)
        video_layout.addLayout(prefix_layout)

        layout.addWidget(video_group)

        # === 第三步：参数 ===
        param_group = QGroupBox("3. 分析参数")
        param_layout = QFormLayout(param_group)

        self.chambers_spin = QSpinBox()
        self.chambers_spin.setMinimum(1)
        self.chambers_spin.setMaximum(100)
        self.chambers_spin.setValue(4)  # 默认 4
        param_layout.addRow("隔间数：", self.chambers_spin)

        self.calib_frames_spin = QSpinBox()
        self.calib_frames_spin.setMinimum(1)
        self.calib_frames_spin.setMaximum(1000)
        self.calib_frames_spin.setValue(12)  # 默认 12
        param_layout.addRow("标定帧数：", self.calib_frames_spin)

        # body_area_prior（可空）
        body_area_layout = QHBoxLayout()
        self.body_area_checkbox = QPushButton("启用")
        self.body_area_checkbox.setCheckable(True)
        self.body_area_checkbox.setMaximumWidth(80)
        self.body_area_checkbox.toggled.connect(self._on_body_area_toggled)
        body_area_layout.addWidget(self.body_area_checkbox)

        self.body_area_spin = QDoubleSpinBox()
        self.body_area_spin.setMinimum(0.0)
        self.body_area_spin.setMaximum(100000.0)
        self.body_area_spin.setDecimals(2)
        self.body_area_spin.setEnabled(False)
        body_area_layout.addWidget(self.body_area_spin)

        body_area_layout.addWidget(
            QLabel("（硬件规格级绝对先验，平时留空）")
        )
        body_area_layout.addStretch()
        param_layout.addRow("Body area prior：", body_area_layout)

        layout.addWidget(param_group)

        # === 第四步：输出目录 + 操作者 + 备注 ===
        output_group = QGroupBox("4. 输出与备注")
        output_layout = QFormLayout(output_group)

        dir_layout = QHBoxLayout()
        self.output_dir_edit = QLineEdit()
        self.output_dir_edit.setPlaceholderText("选择输出目录")
        dir_layout.addWidget(self.output_dir_edit)

        self.btn_browse_dir = QPushButton("浏览...")
        self.btn_browse_dir.clicked.connect(self._browse_output_dir)
        dir_layout.addWidget(self.btn_browse_dir)
        output_layout.addRow("输出目录：", dir_layout)

        self.operator_edit = QLineEdit()
        self.operator_edit.setPlaceholderText("可选")
        output_layout.addRow("操作者：", self.operator_edit)

        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText("可选")
        output_layout.addRow("备注：", self.note_edit)

        layout.addWidget(output_group)

        # 完成按钮
        self.btn_finish = QPushButton("完成并创建实验")
        self.btn_finish.clicked.connect(self._finish)
        layout.addWidget(self.btn_finish)

        layout.addStretch()

        # 初始化
        self._on_assay_changed(self.assay_combo.currentText())
        self._update_finish_button()

    def _on_assay_changed(self, assay: str) -> None:
        """范式改变时更新窗口显示。"""
        if assay in ASSAY_WINDOWS_UI:
            start, end = ASSAY_WINDOWS_UI[assay]
            # 窗口起点非 0 时说明有适应期不计分
            if start > 0:
                self.window_label.setText(
                    f"{start:.0f}–{end:.0f} s（前 {start:.0f} s 适应不计）"
                )
            else:
                self.window_label.setText(f"{start:.0f}–{end:.0f} s")

    def _add_videos(self) -> None:
        """添加视频文件。"""
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择视频文件",
            "",
            "视频文件 (*.mp4 *.avi *.mov *.mkv);;所有文件 (*)",
        )

        if not files:
            return

        # 检查重复
        existing_paths = {
            self.video_list.item(i).video_path
            for i in range(self.video_list.count())
        }

        added = 0
        duplicates = []

        for file_path in files:
            path = Path(file_path)
            if path in existing_paths:
                duplicates.append(path.name)
            else:
                item = VideoListItem(path)
                self.video_list.addItem(item)
                existing_paths.add(path)
                added += 1

        if duplicates:
            QMessageBox.information(
                self,
                "重复文件",
                f"以下文件已在列表中，已跳过：\n" + "\n".join(duplicates),
            )

        self._update_finish_button()

    def _remove_selected_video(self) -> None:
        """移除选中的视频。"""
        current_row = self.video_list.currentRow()
        if current_row >= 0:
            self.video_list.takeItem(current_row)
            self._update_finish_button()

    def _move_video_up(self) -> None:
        """上移视频。"""
        current_row = self.video_list.currentRow()
        if current_row > 0:
            item = self.video_list.takeItem(current_row)
            self.video_list.insertItem(current_row - 1, item)
            self.video_list.setCurrentRow(current_row - 1)

    def _move_video_down(self) -> None:
        """下移视频。"""
        current_row = self.video_list.currentRow()
        if 0 <= current_row < self.video_list.count() - 1:
            item = self.video_list.takeItem(current_row)
            self.video_list.insertItem(current_row + 1, item)
            self.video_list.setCurrentRow(current_row + 1)

    def _update_video_buttons(self) -> None:
        """更新视频按钮状态。"""
        current_row = self.video_list.currentRow()
        has_selection = current_row >= 0

        self.btn_remove_video.setEnabled(has_selection)
        self.btn_move_up.setEnabled(current_row > 0)
        self.btn_move_down.setEnabled(
            0 <= current_row < self.video_list.count() - 1
        )

        # 更新 trial_prefix 编辑框
        if has_selection:
            item = self.video_list.item(current_row)
            self.trial_prefix_edit.blockSignals(True)
            self.trial_prefix_edit.setText(item.trial_prefix or "")
            self.trial_prefix_edit.blockSignals(False)
            self.trial_prefix_edit.setEnabled(True)
        else:
            self.trial_prefix_edit.clear()
            self.trial_prefix_edit.setEnabled(False)

    def _on_trial_prefix_changed(self, text: str) -> None:
        """trial_prefix 编辑框内容改变时更新当前项。"""
        current_row = self.video_list.currentRow()
        if current_row >= 0:
            item = self.video_list.item(current_row)
            item.set_trial_prefix(text if text.strip() else None)

    def _on_body_area_toggled(self, checked: bool) -> None:
        """body_area_prior 启用/禁用切换。"""
        self.body_area_spin.setEnabled(checked)

    def _browse_output_dir(self) -> None:
        """浏览输出目录。"""
        dir_path = QFileDialog.getExistingDirectory(
            self, "选择输出目录", self.output_dir_edit.text()
        )
        if dir_path:
            self.output_dir_edit.setText(dir_path)
            self._update_finish_button()

    def _update_finish_button(self) -> None:
        """更新完成按钮状态（一个视频都没有时不可点）。"""
        has_videos = self.video_list.count() > 0
        has_output_dir = bool(self.output_dir_edit.text().strip())
        self.btn_finish.setEnabled(has_videos and has_output_dir)

    def _finish(self) -> None:
        """完成并创建实验。"""
        # 收集数据
        assay = self.assay_combo.currentText()
        n_chambers = self.chambers_spin.value()
        calib_frames = self.calib_frames_spin.value()

        # body_area_prior
        body_area_prior = None
        if self.body_area_checkbox.isChecked():
            body_area_prior = self.body_area_spin.value()

        # 输出目录
        output_dir_text = self.output_dir_edit.text().strip()
        if not output_dir_text:
            QMessageBox.warning(self, "错误", "请选择输出目录")
            return

        output_dir = Path(output_dir_text)

        # 操作者和备注（空字符串转 None）
        operator = self.operator_edit.text().strip() or None
        note = self.note_edit.text().strip() or None

        # 视频列表
        videos = []
        for i in range(self.video_list.count()):
            item = self.video_list.item(i)
            videos.append(VideoEntry(item.video_path, item.trial_prefix))

        # 创建计划
        plan = ExperimentPlan(
            assay=assay,
            n_chambers=n_chambers,
            calib_frames=calib_frames,
            body_area_prior=body_area_prior,
            output_dir=output_dir,
            videos=videos,
            operator=operator,
            note=note,
        )

        # 验证
        error = validate_experiment_plan(plan)
        if error:
            QMessageBox.warning(self, "验证失败", error)
            return

        # 写出文件
        try:
            json_path = write_experiment_json(plan)
            QMessageBox.information(
                self,
                "成功",
                f"实验计划已创建：\n{json_path}",
            )
            # 发信号
            self.experiment_created.emit(str(json_path))
        except FileExistsError as e:
            QMessageBox.warning(
                self,
                "文件已存在",
                f"{e}\n\n请换一个输出目录或删除已有文件。",
            )
        except OSError as e:
            QMessageBox.critical(
                self,
                "写入失败",
                f"无法写入实验计划文件：\n{e}",
            )
