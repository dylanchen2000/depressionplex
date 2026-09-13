"""分析队列页面（DP-102 / B3）。

串行队列（MAX_CONCURRENT = 1），QProcess 起子进程，取消 = 杀进程。
"""

import json
from pathlib import Path
from typing import Dict

from PySide6.QtCore import QProcess, Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget,
    QTableWidgetItem, QProgressBar, QLabel, QHeaderView, QMessageBox
)

from desktop.app.models.queue_models import (
    ItemStatus, QueueItem, status_for_exit, validate_transition
)
from desktop.app.services import engine, progress as prog
from desktop.app.utils.paths import user_data_dir

# 串行队列：同时只跑一条（派工单 §2.4：并行解码会把 I/O 打满）
MAX_CONCURRENT = 1


class QueuePage(QWidget):
    """分析队列页面。

    核心逻辑在 services/ 与 models/ 里，本页面只做 Qt 事件接线。
    """

    # 队列状态改变时发射（用于通知其它页面刷新）
    queue_updated = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self._experiment: dict | None = None  # experiment.json 的内容
        self._items: list[QueueItem] = []  # 队列项
        self._current_process: QProcess | None = None  # 当前运行的子进程
        self._current_index: int = -1  # 当前运行的项目在 _items 中的索引
        self._stderr_pump: prog.StderrPump | None = None  # stderr 行缓冲
        self._cancel_requested: bool = False  # 是否请求了取消
        self._kill_timer_id: int | None = None  # terminate 后的 kill 超时定时器

        layout = QVBoxLayout(self)

        # 标题
        title = QLabel("分析队列")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        # 按钮栏
        btn_layout = QHBoxLayout()
        self.btn_load = QPushButton("加载实验")
        self.btn_start = QPushButton("开始")
        self.btn_cancel = QPushButton("取消当前")
        self.btn_load.clicked.connect(self._on_load_experiment)
        self.btn_start.clicked.connect(self._on_start_queue)
        self.btn_cancel.clicked.connect(self._on_cancel_current)
        btn_layout.addWidget(self.btn_load)
        btn_layout.addWidget(self.btn_start)
        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        # 队列表格
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["视频", "状态", "进度", "帧", "消息"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(2, 200)
        layout.addWidget(self.table)

        self._update_ui_state()

    def _update_ui_state(self):
        """根据队列状态更新按钮可用性。"""
        has_exp = self._experiment is not None
        is_running = self._current_process is not None
        has_pending = any(item.status == ItemStatus.PENDING for item in self._items)

        self.btn_start.setEnabled(has_exp and not is_running and has_pending)
        self.btn_cancel.setEnabled(is_running)

    def _on_load_experiment(self):
        """加载 experiment.json（临时写死路径，B2 会提供真正的选择器）。"""
        # TODO: B2 会提供实验选择器，这里先写死一个测试路径
        exp_path = user_data_dir() / "test_experiment.json"
        if not exp_path.exists():
            QMessageBox.warning(self, "错误", f"实验文件不存在：{exp_path}")
            return

        try:
            with open(exp_path, encoding="utf-8") as f:
                self._experiment = json.load(f)

            # 构造队列项
            self._items = []
            for i, video in enumerate(self._experiment["videos"]):
                video_path = Path(video["path"])
                self._items.append(QueueItem(
                    video_index=i,
                    video_name=video_path.name,
                    status=ItemStatus.PENDING
                ))

            self._refresh_table()
            self._update_ui_state()

        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载实验失败：{e}")

    def _refresh_table(self):
        """刷新表格显示。"""
        self.table.setRowCount(len(self._items))
        for i, item in enumerate(self._items):
            self.table.setItem(i, 0, QTableWidgetItem(item.video_name))
            self.table.setItem(i, 1, QTableWidgetItem(item.status.value))

            # 进度条
            pbar = QProgressBar()
            pct = prog.percent(item.progress_frame, item.progress_total)
            if pct is None:
                # 不确定进度：n 为 None
                pbar.setRange(0, 0)
                pbar.setFormat(f"已处理 {item.progress_frame} 帧")
            else:
                pbar.setRange(0, 100)
                pbar.setValue(int(pct))
                pbar.setFormat(f"{pct:.1f}%")
            self.table.setCellWidget(i, 2, pbar)

            # 帧数
            if item.progress_total is None:
                frame_text = f"{item.progress_frame} / ?"
            else:
                frame_text = f"{item.progress_frame} / {item.progress_total}"
            self.table.setItem(i, 3, QTableWidgetItem(frame_text))

            # 错误消息
            self.table.setItem(i, 4, QTableWidgetItem(item.error_message))

    def _on_start_queue(self):
        """开始处理队列。"""
        self._run_next()

    def _run_next(self):
        """运行下一个待处理项。"""
        if self._current_process is not None:
            return  # 已经有进程在跑

        # 找第一个待跑的项
        next_idx = next((i for i, item in enumerate(self._items)
                        if item.status == ItemStatus.PENDING), None)
        if next_idx is None:
            # 没有待跑的了
            self._save_queue_state()
            self.queue_updated.emit()
            return

        self._current_index = next_idx
        item = self._items[next_idx]

        # 构造 argv
        try:
            argv = engine.build_argv(self._experiment, item.video_index)
        except (FileExistsError, ValueError) as e:
            # 输出文件已存在或参数错误，置失败
            self._items[next_idx] = item._replace(
                status=ItemStatus.FAILED,
                error_message=str(e)
            )
            self._refresh_table()
            self._save_queue_state()
            # 不继续跑下一条（按取消同理：客户按下时是想停下来看看）
            self._update_ui_state()
            return

        # 置为运行中
        self._items[next_idx] = item._replace(status=ItemStatus.RUNNING)
        self._refresh_table()

        # 起子进程
        self._stderr_pump = prog.StderrPump()
        self._cancel_requested = False

        proc = QProcess(self)
        self._current_process = proc

        proc.readyReadStandardError.connect(self._on_stderr_ready)
        proc.finished.connect(self._on_process_finished)

        cwd = engine.get_cwd()
        if cwd is not None:
            proc.setWorkingDirectory(str(cwd))

        # argv[0] 是程序，argv[1:] 是参数
        proc.start(argv[0], argv[1:])

        self._update_ui_state()

    def _on_stderr_ready(self):
        """QProcess 的 stderr 有数据可读。"""
        if self._current_process is None or self._stderr_pump is None:
            return

        chunk = self._current_process.readAllStandardError().data()
        events = self._stderr_pump.feed(chunk)

        for event in events:
            if isinstance(event, prog.ProgressEvent):
                # 更新进度
                item = self._items[self._current_index]
                self._items[self._current_index] = item._replace(
                    progress_frame=event.frame,
                    progress_total=event.n
                )
                self._refresh_table()
            elif isinstance(event, prog.LogLine):
                # 日志行暂时不处理（B4 会有详情页）
                pass

    def _on_process_finished(self, exit_code: int, exit_status):
        """子进程结束。"""
        if self._current_process is None or self._current_index < 0:
            return

        item = self._items[self._current_index]

        if self._cancel_requested:
            # 取消：删除半成品输出
            self._cleanup_outputs(item.video_index)
            new_status = ItemStatus.CANCELLED
            msg = "用户取消"
        else:
            # 正常结束：根据退出码映射状态
            new_status = status_for_exit(exit_code)
            if new_status == ItemStatus.FAILED:
                # 读 stderr 最后几行作为错误消息
                stderr_tail = self._current_process.readAllStandardError().data().decode(
                    "utf-8", errors="replace"
                ).strip()
                msg = stderr_tail[-500:] if stderr_tail else f"退出码 {exit_code}"
            elif new_status == ItemStatus.NO_OUTPUT:
                # 无产出：读 run.json 的 not_scored
                msg = self._read_not_scored_reasons(item.video_index)
            else:
                msg = ""

        # 更新状态
        try:
            validate_transition(item.status, new_status)
        except Exception as e:
            # 非法状态迁移，强制置失败
            new_status = ItemStatus.FAILED
            msg = f"状态迁移错误：{e}"

        self._items[self._current_index] = item._replace(
            status=new_status,
            error_message=msg
        )

        self._current_process = None
        self._current_index = -1
        self._stderr_pump = None

        self._refresh_table()
        self._save_queue_state()
        self._update_ui_state()

        # 不自动跑下一条（客户按取消或看到失败时，想停下来看看）

    def _on_cancel_current(self):
        """取消当前运行的项。"""
        if self._current_process is None:
            return

        self._cancel_requested = True

        # 先 terminate，3 秒后 kill
        self._current_process.terminate()
        self._kill_timer_id = self.startTimer(3000)

    def timerEvent(self, event):
        """定时器事件：terminate 超时后 kill。"""
        if event.timerId() == self._kill_timer_id:
            if self._current_process is not None:
                self._current_process.kill()
            self.killTimer(self._kill_timer_id)
            self._kill_timer_id = None

    def _cleanup_outputs(self, video_index: int):
        """删除一条 item 的半成品输出（四个文件）。"""
        if self._experiment is None:
            return

        video = self._experiment["videos"][video_index]
        video_path = Path(video["path"])
        output_dir = Path(self._experiment["output_dir"])
        video_stem = video_path.stem

        # 四个文件：csv / timeline_csv / run_json / report.txt
        paths_to_delete = [
            output_dir / f"{video_stem}.csv",
            output_dir / f"{video_stem}_timeline.csv",
            output_dir / f"{video_stem}_run.json",
            output_dir / f"{video_stem}_report.txt",
        ]

        for p in paths_to_delete:
            if p.exists():
                try:
                    p.unlink()
                except Exception:
                    pass  # 删不掉也不阻塞

    def _read_not_scored_reasons(self, video_index: int) -> str:
        """读取 run.json 的 not_scored 原因。"""
        if self._experiment is None:
            return ""

        video = self._experiment["videos"][video_index]
        video_path = Path(video["path"])
        output_dir = Path(self._experiment["output_dir"])
        run_json_path = output_dir / f"{video_path.stem}_run.json"

        if not run_json_path.exists():
            return "run.json 不存在"

        try:
            with open(run_json_path, encoding="utf-8") as f:
                run_data = json.load(f)
            not_scored = run_data.get("not_scored", {})
            if not not_scored:
                return "run.json 中 not_scored 为空"
            # 逐个列出
            reasons = []
            for ch, reason in sorted(not_scored.items()):
                reasons.append(f"ch{ch}: {reason}")
            return " | ".join(reasons)
        except Exception as e:
            return f"读取 run.json 失败：{e}"

    def _save_queue_state(self):
        """保存队列状态到磁盘。"""
        state_path = user_data_dir(create=True) / "queue_state.json"
        try:
            state = {
                "experiment": self._experiment,
                "items": [
                    {
                        "video_index": item.video_index,
                        "video_name": item.video_name,
                        "status": item.status.value,
                        "progress_frame": item.progress_frame,
                        "progress_total": item.progress_total,
                        "error_message": item.error_message,
                    }
                    for item in self._items
                ]
            }
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
        except Exception:
            pass  # 保存失败不阻塞
