"""仍为占位的侧栏页。

Welcome 是入口；「复核」「导出」侧栏页**尚未实现**——文案必须说真话，
不许写成「本页由 Bx 交付」让人以为点进去就能用。
（结果页上的「导出…」按钮是 B6 已交付的通路；侧栏「导出」不是那条路。）
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PySide6.QtCore import Qt


class WelcomePage(QWidget):
    """Welcome page - entry point for the application."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        title = QLabel("欢迎")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        description = QLabel("欢迎使用 DEPRESSION-PLEX")
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(description)

        layout.addStretch()


class ReviewPage(QWidget):
    """复核页占位：时间线复核视图尚未交付（B5）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        title = QLabel("复核")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        description = QLabel(
            "本页尚未实现（占位）。\n"
            "目前不能在此做人工复核、掩膜叠加或事件带校对。\n"
            "时间线复核视图待后续交付。"
        )
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        description.setWordWrap(True)
        layout.addWidget(description)

        layout.addStretch()


class ExportPage(QWidget):
    """侧栏「导出」占位：独立导出页未交付。

    当前可用的导出入口在「结果」页的「导出…」按钮（xlsx / 审计包 / PDF）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        title = QLabel("导出")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        description = QLabel(
            "侧栏「导出」页尚未实现（占位）。\n"
            "请先打开「结果」页 →「打开输出目录…」→ 使用「导出…」按钮。\n"
            "不要把本页当成已可用的导出入口。"
        )
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        description.setWordWrap(True)
        layout.addWidget(description)

        layout.addStretch()
