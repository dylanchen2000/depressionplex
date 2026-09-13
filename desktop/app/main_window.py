"""主窗口：左侧导航 + 右侧页面栈。

**页面清单只有这一份**（`PAGE_ORDER`）：自检拿它核对，别处不许再抄一份顺序。
"""

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QListWidget, QStackedWidget, QListWidgetItem
)

from desktop.app.pages.placeholders import (
    WelcomePage, ResultsPage,
    ReviewPage, ExportPage, SelfCheckPage,
)
from desktop.app.pages.new_experiment import NewExperimentPage
from desktop.app.pages.queue import QueuePage

# (侧栏显示名, 页面类名)。类名以字符串出现，是为了让 main.py 的自检能逐个单独构造。
PAGE_ORDER = (
    ("欢迎", "WelcomePage"),
    ("新建实验", "NewExperimentPage"),
    ("分析队列", "QueuePage"),
    ("结果", "ResultsPage"),
    ("复核", "ReviewPage"),
    ("导出", "ExportPage"),
    ("自检", "SelfCheckPage"),
)

#: 类名 → 类。**「哪个类是哪一页」只许在这里回答一次**（DP-101 修）：
#: `main.py` 的自检原来自己 `getattr(placeholders, cls_name)`，是第二个解析器；
#: 页面一旦从 placeholders 搬进自己的模块，两个解析器就指到不同的地方。
#: 页面模块化是每一页的必经之路，所以这个口子迟早会被踩到——B2 第一次踩上就是它。
PAGE_CLASSES = {
    "WelcomePage": WelcomePage,
    "NewExperimentPage": NewExperimentPage,
    "QueuePage": QueuePage,
    "ResultsPage": ResultsPage,
    "ReviewPage": ReviewPage,
    "ExportPage": ExportPage,
    "SelfCheckPage": SelfCheckPage,
}


class MainWindow(QMainWindow):
    """主窗口。模式徽章由 B8 挂上来（资质边界，不在本件范围）。"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("DEPRESSION-PLEX")
        self.resize(1200, 800)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sidebar = QListWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setMaximumWidth(200)
        self.sidebar.currentRowChanged.connect(self.display_page)

        self.page_stack = QStackedWidget()

        # 全部页面在构造时就建好（自检要求：不许有「点了才建」的页面）
        self.pages = {name: PAGE_CLASSES[cls_name](self) for name, cls_name in PAGE_ORDER}
        for name, widget in self.pages.items():
            self.sidebar.addItem(QListWidgetItem(name))
            self.page_stack.addWidget(widget)

        layout.addWidget(self.sidebar)
        layout.addWidget(self.page_stack, 1)
        self.sidebar.setCurrentRow(0)

    def display_page(self, index: int) -> None:
        self.page_stack.setCurrentIndex(index)
