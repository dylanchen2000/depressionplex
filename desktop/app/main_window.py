"""主窗口：左侧导航 + 右侧页面栈。

**页面清单只有这一份**（`PAGE_ORDER`）：自检拿它核对，别处不许再抄一份顺序。
"""

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QListWidget, QStackedWidget, QListWidgetItem
)

from desktop.app.pages.placeholders import (
    WelcomePage,
    ReviewPage, ExportPage, SelfCheckPage,
)
from desktop.app.pages.new_experiment import NewExperimentPage
from desktop.app.pages.queue import QueuePage
from desktop.app.pages.results import ResultsPage
from desktop.app.services.calibration import evaluate_calibration
from desktop.app.utils.paths import bundled_calibration_path
from desktop.app.widgets.badge import ModeBadge

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
    """主窗口：左侧导航 + 右侧页面栈 + 状态栏上的模式徽章。

    **模式徽章挂在状态栏、且是 permanent widget**（B8 / DP-111）：状态栏跟着窗口
    而不是跟着页面，所以七页里的**每一页**都带着它，包括将来新加的页。
    换成「在结果页放一个」的做法，用户从新建实验页直接导出时就看不到资质声明了——
    而看不见的声明等于没有声明。
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("DEPRESSION-PLEX")
        self.resize(1200, 800)

        # 启动自检：**先判资质，再建界面**。判定只有这一次、只有这一个来源，
        # 各页要用就问 `window.calibration_status` / `window.mode_badge.view`，
        # 不许自己再调一次 `evaluate_calibration`（第二次调用可能读到不同的文件）。
        self.calibration_status = evaluate_calibration(bundled_calibration_path())
        self.mode_badge = ModeBadge.from_status(self.calibration_status)
        self.statusBar().addPermanentWidget(self.mode_badge)

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
