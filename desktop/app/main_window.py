"""Main window for DEPRESSION-PLEX desktop application."""

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QStackedWidget, QListWidgetItem
)
from PySide6.QtCore import Qt

from desktop.app.pages.placeholders import (
    WelcomePage, NewExperimentPage, QueuePage, ResultsPage,
    ReviewPage, ExportPage, SelfCheckPage
)


class MainWindow(QMainWindow):
    """Main application window with sidebar navigation."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("DEPRESSION-PLEX")
        self.resize(1200, 800)

        # Create central widget and layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Create sidebar
        self.sidebar = QListWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setMaximumWidth(200)
        self.sidebar.currentRowChanged.connect(self.display_page)

        # Create page stack
        self.page_stack = QStackedWidget()

        # Initialize all pages upfront (required for self-test)
        self.pages = {
            "欢迎": WelcomePage(self),
            "新建实验": NewExperimentPage(self),
            "分析队列": QueuePage(self),
            "结果": ResultsPage(self),
            "复核": ReviewPage(self),
            "导出": ExportPage(self),
            "自检": SelfCheckPage(self),
        }

        # Add pages to sidebar and stack
        for page_name, page_widget in self.pages.items():
            item = QListWidgetItem(page_name)
            self.sidebar.addItem(item)
            self.page_stack.addWidget(page_widget)

        # Add widgets to main layout
        main_layout.addWidget(self.sidebar)
        main_layout.addWidget(self.page_stack, 1)

        # Set initial page
        self.sidebar.setCurrentRow(0)

    def display_page(self, index: int):
        """Display the page corresponding to the sidebar selection."""
        self.page_stack.setCurrentIndex(index)
