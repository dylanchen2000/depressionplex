"""Placeholder pages for DEPRESSION-PLEX desktop application.

Each page is a simple placeholder to be implemented in subsequent deliverables.
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


class QueuePage(QWidget):
    """Analysis queue page - to be implemented in B3."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        title = QLabel("分析队列")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        description = QLabel("本页由 B3 交付")
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(description)

        layout.addStretch()


class ResultsPage(QWidget):
    """Results page - to be implemented in B4."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        title = QLabel("结果")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        description = QLabel("本页由 B4 交付")
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(description)

        layout.addStretch()


class ReviewPage(QWidget):
    """Review page - to be implemented in B5."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        title = QLabel("复核")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        description = QLabel("本页由 B5 交付")
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(description)

        layout.addStretch()


class ExportPage(QWidget):
    """Export page - to be implemented in B6."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        title = QLabel("导出")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        description = QLabel("本页由 B6 交付")
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(description)

        layout.addStretch()


class SelfCheckPage(QWidget):
    """Self-check page - to be implemented in B7."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        title = QLabel("自检")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        description = QLabel("本页由 B7 交付")
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(description)

        layout.addStretch()
