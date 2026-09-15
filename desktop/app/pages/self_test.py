"""采集自检页（DP-109 B7）。

这一页在产品里的作用：客户机上、客户自己的素材上，回答一句话——
**「你这套采集条件，我们的算法能不能用？」**
不达标要当场说不达标，而不是让他跑完 6 分钟拿到一份看着正常的数字。

页面纪律：
- 不自己算科学量（含 px 换算），所有数字从 AcqCheckResult 读
- 门槛字面量不许出现在本文件（用 result.xxx_threshold 属性）
- 参考值只从 JSON 里读（不许写死），参考列表头 = 「参考素材（2026-08-24 实测）」
- 不自己拼 argv（`engine.acq_check_argv`）、不自己判退出码（`result_from_process`）：
  本文件在沙箱里跑不起来，写进来的判断没有守卫看着（DP-120）
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFileDialog, QTableWidget, QTableWidgetItem,
    QGroupBox, QScrollArea, QFrame, QSpinBox,
)
from PySide6.QtCore import Qt, QProcess

from desktop.app.models.self_test import (
    AcqCheckResult, AcqCheckError, conclusion_lines,
    result_from_process, engine_error_text,
)
from desktop.app.services import engine
from desktop.app.services import progress as prog


class AcqCheckPage(QWidget):
    """采集自检页。类名 AcqCheckPage，用户可见名称「采集自检」。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._result: AcqCheckResult | None = None

        # 自检子进程（DP-120）。页面只搬运：argv 在 services/engine.py 里拼、
        # 退出码在 models/self_test.py 里判、进度行在 services/progress.py 里变人话。
        self._process: QProcess | None = None
        self._pump: prog.StderrPump | None = None
        self._stdout_chunks: list[bytes] = []
        self._stderr_chunks: list[bytes] = []
        self._cancel_requested = False
        self._kill_timer_id: int | None = None

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

        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._on_cancel)
        ctrl.addWidget(self._cancel_btn)

        # 隔间数是自检子命令的必填参数。范围与新建实验向导同一份（1–100，默认 4），
        # 这不是门槛、是输入范围——门槛一律从 result 的属性读。
        ctrl.addWidget(QLabel("隔间数："))
        self._chambers_spin = QSpinBox()
        self._chambers_spin.setMinimum(1)
        self._chambers_spin.setMaximum(100)
        self._chambers_spin.setValue(4)
        ctrl.addWidget(self._chambers_spin)

        ctrl.addStretch()
        root_layout.addLayout(ctrl)

        # 状态行：进度、取消、报错都摆这儿；「人话结论」摆下面的 conclusion_label
        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        self._status_label.setObjectName("acqCheckStatus")
        root_layout.addWidget(self._status_label)

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
        """选视频 → 起自检子进程（DP-120）。

        页面在这里只做三件事：拿路径、把 argv 交给 engine 拼、起进程。
        判断一件都不在这儿——沙箱里没有 PySide6，本文件在本地只被 AST 解析，
        判断写进来就等于没有守卫看着它。
        """
        if self._process is not None:
            return  # 已经有一次自检在跑
        path, _ = QFileDialog.getOpenFileName(
            self, "选择视频文件", "", "视频文件 (*.mp4 *.avi *.mov *.mkv)"
        )
        if not path:
            return

        try:
            argv = engine.acq_check_argv(path, self._chambers_spin.value())
        except (ValueError, FileNotFoundError) as exc:
            self._status_label.setText(f"起不了自检：{exc}")
            return

        # 上一次的读数先清掉：报错旁边留着上一段素材的数字，
        # 是最容易被当成本次结果的东西。
        self._clear_readings()

        self._pump = prog.StderrPump()
        self._stdout_chunks = []
        self._stderr_chunks = []
        self._cancel_requested = False

        proc = QProcess(self)
        self._process = proc
        proc.readyReadStandardOutput.connect(self._on_stdout_ready)
        proc.readyReadStandardError.connect(self._on_stderr_ready)
        proc.finished.connect(self._on_process_finished)
        proc.errorOccurred.connect(self._on_process_error)

        cwd = engine.get_cwd()
        if cwd is not None:
            proc.setWorkingDirectory(str(cwd))

        self._set_running(True)
        self._status_label.setText(f"正在自检：{path}")
        proc.start(argv[0], argv[1:])

    # ── 子进程事件 ────────────────────────────────────────────────────────────

    def _on_stdout_ready(self) -> None:
        """stdout 是唯一的数字出口（一整份 JSON）：只攒着，结束时交给模型层解。"""
        if self._process is None:
            return
        self._stdout_chunks.append(bytes(self._process.readAllStandardOutput().data()))

    def _on_stderr_ready(self) -> None:
        """stderr 是进度与报错：喂进 StderrPump，人话摆到状态行。

        原始字节一并留着——结束时要把引擎自己那句报错原文带给用户。
        """
        if self._process is None or self._pump is None:
            return
        chunk = bytes(self._process.readAllStandardError().data())
        if not chunk:
            return
        self._stderr_chunks.append(chunk)
        for event in self._pump.feed(chunk):
            if isinstance(event, prog.LogLine):
                self._status_label.setText(prog.status_text(event))
            elif isinstance(event, prog.ProgressEvent):
                pct = prog.percent(event.frame, event.n)
                self._status_label.setText(
                    f"解码中 第 {event.frame} 帧" if pct is None
                    else f"解码中 {pct:.0f}%"
                )

    def _on_process_error(self, error) -> None:
        """进程级错误。**只有 FailedToStart 要在这里收摊**——那种情形 finished 不会来，
        不自己收就会留下一个永远转不完、按钮永远灰着的页面。
        """
        if error == QProcess.ProcessError.FailedToStart:
            self._status_label.setText(
                "自检进程起不来（找到了引擎，但没能把它跑起来）。"
                "请把这句话连同安装目录一起报给我们。"
            )
            self._teardown()

    def _on_process_finished(self, exit_code: int, exit_status) -> None:
        """子进程结束：读干管子 → 交模型层判 → 摆结果或摆人话。"""
        if self._process is None:
            return
        # finished 之后管子里可能还剩没读的，先读干
        self._stdout_chunks.append(bytes(self._process.readAllStandardOutput().data()))
        self._on_stderr_ready()

        stdout = b"".join(self._stdout_chunks).decode("utf-8", errors="replace")
        stderr = b"".join(self._stderr_chunks).decode("utf-8", errors="replace")
        cancelled = self._cancel_requested
        crashed = exit_status == QProcess.ExitStatus.CrashExit
        self._teardown()

        if cancelled:
            self._status_label.setText("已取消。这一次没有读数——取消不产出任何数字。")
            return

        if crashed:
            self._status_label.setText(
                "自检进程异常退出（不是它自己 return 的）。"
                f"引擎最后一句：{engine_error_text(stderr)}"
            )
            return

        try:
            result = result_from_process(exit_code, stdout, stderr)
        except AcqCheckError as exc:
            self._status_label.setText(f"这一段没测出结果：{exc}")
            return

        self._status_label.setText("自检完成")
        self.load_result(result)

    def _on_cancel(self) -> None:
        """取消 = 杀进程（与 QueuePage 同一条路子：先 terminate，3 秒后 kill）。"""
        if self._process is None:
            return
        self._cancel_requested = True
        self._status_label.setText("正在取消…")
        self._process.terminate()
        self._kill_timer_id = self.startTimer(3000)

    def timerEvent(self, event) -> None:
        """terminate 超时后 kill（照 QueuePage）。"""
        if self._kill_timer_id is not None and event.timerId() == self._kill_timer_id:
            if self._process is not None:
                self._process.kill()
            self.killTimer(self._kill_timer_id)
            self._kill_timer_id = None

    # ── 运行状态 ──────────────────────────────────────────────────────────────

    def _set_running(self, running: bool) -> None:
        self._run_btn.setEnabled(not running)
        self._chambers_spin.setEnabled(not running)
        self._cancel_btn.setEnabled(running)

    def _teardown(self) -> None:
        if self._kill_timer_id is not None:
            self.killTimer(self._kill_timer_id)
            self._kill_timer_id = None
        self._process = None
        self._pump = None
        self._set_running(False)

    def _clear_readings(self) -> None:
        """清掉上一次的读数（三行门的读数列、诊断表、结论）。"""
        self._result = None
        for row in range(self._gate_table.rowCount()):
            for col in range(1, self._gate_table.columnCount()):
                self._gate_table.setItem(row, col, QTableWidgetItem(""))
        self._diag_table.setRowCount(0)
        self._conclusion_label.setText("")

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
        text = "\n\n".join(conclusion_lines(self._result))
        self._conclusion_label.setText(text)
