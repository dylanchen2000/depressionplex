"""采集自检页（DP-109 B7）。

这一页在产品里的作用：客户机上、客户自己的素材上，回答一句话——
**「你这套采集条件，我们的算法能不能用？」**
不达标要当场说不达标，而不是让他跑完 6 分钟拿到一份看着正常的数字。

页面纪律：
- 不自己算科学量（含 px 换算），所有数字从 AcqCheckResult 读
- 门槛字面量 100 / 2 / 0.02 不许出现在本文件（用 result.xxx_threshold 属性）
- 参考值只从 JSON 里读（不许写死），参考列表头 = 「参考素材（2026-08-24 实测）」
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFileDialog, QTableWidget, QTableWidgetItem,
    QGroupBox, QScrollArea, QFrame,
)
from PySide6.QtCore import Qt

from desktop.app.models.self_test import AcqCheckResult, AcqCheckError


class AcqCheckPage(QWidget):
    """采集自检页。类名 AcqCheckPage，用户可见名称「采集自检」。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._result: AcqCheckResult | None = None

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(12)

        # ── 标题 ──────────────────────────────────────────────────────────────
        title = QLabel("采集自检")
        title.setObjectName("pageTitle")
        root_layout.addWidget(title)

        subtitle = QLabel(
            "用一段录像检查采集条件是否满足算法要求。\n"
            "三项指标全部通过时方可开始正式分析。"
        )
        subtitle.setWordWrap(True)
        root_layout.addWidget(subtitle)

        # ── 控制区 ────────────────────────────────────────────────────────────
        ctrl = QHBoxLayout()
        self._run_btn = QPushButton("选择视频并自检…")
        self._run_btn.clicked.connect(self._on_run)
        ctrl.addWidget(self._run_btn)
        ctrl.addStretch()
        root_layout.addLayout(ctrl)

        # ── 门判定表（三行门 + 参考列）────────────────────────────────────────
        gate_group = QGroupBox("门判定")
        gate_layout = QVBoxLayout(gate_group)
        self._gate_table = QTableWidget(3, 5)
        self._gate_table.setHorizontalHeaderLabels([
            "指标", "本机读数", "门槛", "判定", "参考素材（2026-08-24 实测）",
        ])
        self._gate_table.horizontalHeader().setStretchLastSection(True)
        self._gate_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._gate_table.setRowCount(3)
        row_labels = ["对比度（绝对差）", "分割噪声底（px）", "面积抖动 p90 / BL²"]
        for i, lbl in enumerate(row_labels):
            self._gate_table.setItem(i, 0, QTableWidgetItem(lbl))
        gate_layout.addWidget(self._gate_table)
        root_layout.addWidget(gate_group)

        # ── 结论 / 人话 ───────────────────────────────────────────────────────
        self._conclusion_label = QLabel("")
        self._conclusion_label.setWordWrap(True)
        self._conclusion_label.setObjectName("acqCheckConclusion")
        root_layout.addWidget(self._conclusion_label)

        # ── 诊断表（逐隔间读数）──────────────────────────────────────────────
        diag_group = QGroupBox("各隔间诊断数据")
        diag_layout = QVBoxLayout(diag_group)
        self._diag_table = QTableWidget(0, 3)
        self._diag_table.setHorizontalHeaderLabels(["隔间", "面积抖动 p90/BL²", "RAD 残差"])
        self._diag_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        diag_layout.addWidget(self._diag_table)
        root_layout.addWidget(diag_group)

        root_layout.addStretch()

    # ── 事件处理 ──────────────────────────────────────────────────────────────

    def _on_run(self) -> None:
        """选择视频、弹出自检进度（占位，实际运行由 QueuePage 机制在 B10+ 完善）。"""
        # TODO B10+：用 engine.acq_check_argv 起子进程，用进度 NDJSON 更新 UI。
        # 此处占位，仅演示 UI 路径。
        path, _ = QFileDialog.getOpenFileName(
            self, "选择视频文件", "", "视频文件 (*.mp4 *.avi *.mov *.mkv)"
        )
        if not path:
            return
        self._conclusion_label.setText(f"已选择：{path}\n（自检进程集成待 B10 完成）")

    def load_result(self, result: AcqCheckResult) -> None:
        """加载一次自检结果并刷新页面。

        Args:
            result: AcqCheckResult 实例（由调用方从引擎 JSON 构造）
        """
        self._result = result
        self._refresh_gate_table()
        self._refresh_diag_table()
        self._refresh_conclusion()

    # ── 刷新门判定表 ──────────────────────────────────────────────────────────

    def _refresh_gate_table(self) -> None:
        if self._result is None:
            return
        r = self._result
        ref = r.reference or {}

        def _fmt(v: float | None) -> str:
            return f"{v:.4f}" if v is not None else "—"

        def _pass_str(p: bool | None) -> str:
            if p is True:
                return "通过"
            if p is False:
                return "不通过"
            return "无法测量"

        # 对比度行（第 0 行）
        # 门槛从 result 属性读，不写死字面量
        self._gate_table.setItem(0, 1, QTableWidgetItem(
            f"{_fmt(r.contrast_value)} 灰阶"
        ))
        self._gate_table.setItem(0, 2, QTableWidgetItem(
            f"≥{r.contrast_threshold_abs:.0f} 灰阶 且 ≥{r.contrast_threshold_ratio:.1f}×"
        ))
        self._gate_table.setItem(0, 3, QTableWidgetItem(_pass_str(r.contrast_passed)))
        self._gate_table.setItem(0, 4, QTableWidgetItem(
            _fmt(ref.get("contrast_abs")) + " 灰阶"
        ))

        # 噪声底行（第 1 行）—— 诊断量，无通过/不通过
        self._gate_table.setItem(1, 1, QTableWidgetItem(_fmt(r.noise_floor_value) + " px"))
        self._gate_table.setItem(1, 2, QTableWidgetItem("（诊断量）"))
        self._gate_table.setItem(1, 3, QTableWidgetItem("—"))
        self._gate_table.setItem(1, 4, QTableWidgetItem(
            _fmt(ref.get("noise_floor_px")) + " px"
        ))

        # 面积抖动行（第 2 行）
        jitter_thr_str = f"≤{r.area_jitter_threshold:.4f}"
        self._gate_table.setItem(2, 1, QTableWidgetItem(_fmt(r.area_jitter_value)))
        self._gate_table.setItem(2, 2, QTableWidgetItem(jitter_thr_str))
        self._gate_table.setItem(2, 3, QTableWidgetItem(_pass_str(r.area_jitter_passed)))
        self._gate_table.setItem(2, 4, QTableWidgetItem(
            _fmt(ref.get("area_jitter_p90"))
        ))

    # ── 刷新诊断表 ────────────────────────────────────────────────────────────

    def _refresh_diag_table(self) -> None:
        if self._result is None:
            return
        chambers = self._result.chambers
        self._diag_table.setRowCount(len(chambers))
        selected_ch = self._result.area_jitter_chamber
        for row, ch in enumerate(chambers):
            idx = ch["index"]
            mark = " ★" if idx == selected_ch else ""
            self._diag_table.setItem(row, 0, QTableWidgetItem(f"隔间 {idx}{mark}"))
            jitter = ch.get("area_jitter_p90")
            residual = ch.get("rad_residual")
            self._diag_table.setItem(
                row, 1,
                QTableWidgetItem(f"{jitter:.4f}" if jitter is not None else "—")
            )
            self._diag_table.setItem(
                row, 2,
                QTableWidgetItem(f"{residual:.4f}" if residual is not None else "—")
            )

    # ── 刷新人话结论 ──────────────────────────────────────────────────────────

    def _refresh_conclusion(self) -> None:
        if self._result is None:
            return
        r = self._result
        lines: list[str] = []

        # 对比度不达标
        if r.contrast_passed is False:
            lines.append(
                "【对比度不达标】背光不足或曝光不当。"
                "我们的分割靠亮背板 + 黑剪影，"
                f"绝对差 < {r.contrast_threshold_abs:.0f} 灰阶时阈值分割会不稳。"
                "建议加背光板或调曝光后重录一段再自检。"
            )

        # 动物在动（抖动读数含信号）
        if r.area_jitter_moving is True:
            lines.append(
                "【动物持续运动】本次抽到的帧里动物都在动，抖动读数含真实形变，"
                "不能当噪声底看。换一段有静止时段的素材再自检。"
            )

        # 测不出（无面板带）
        if r.contrast_value is None:
            lines.append(
                "【无法测量】没找到背光面板行带，说明画面结构与我们假设的不同"
                "（亮背板横贯全宽、动物在其下方）。"
                "这不是「不达标」，是「量不了」——请把一帧截图发给我们。"
            )

        if not lines:
            # 全部通过或测量完成无报警
            if r.contrast_passed and r.area_jitter_passed:
                lines.append("全部指标通过，可以开始正式分析。")
            elif r.area_jitter_passed is False:
                lines.append(
                    f"面积抖动超过门槛（{r.area_jitter_value:.4f} > "
                    f"{r.area_jitter_threshold:.4f}），分割噪声底偏高，"
                    "可能影响 immobility 判定精度。"
                )

        self._conclusion_label.setText("\n\n".join(lines) if lines else "")
