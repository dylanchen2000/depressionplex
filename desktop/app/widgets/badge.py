"""模式徽章的渲染层：把 `BadgeView` 摆到屏幕上。**只摆，不判。**

这个文件里**不许出现**：中文文案常量、颜色字面量、对 `Mode` / `Badge` 的判断。
三样都由 `models/badge.py` 给（守卫 5 按 AST 钉住）。

为什么这么死板：徽章是「这个软件有没有资质报秒数」的唯一可见声明。文案一旦
在渲染层也有一份，两份就会各自演化，而**不一致的那一次不会报错**——
用户只会看到状态栏和结果页说着两件事，然后自己挑一个信。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from desktop.app.models.badge import BadgeView, build_badge_view
from desktop.app.services.calibration import CalibrationStatus

_QSS = (
    "QLabel#modeBadge {{ color: {fg}; background-color: {bg};"
    " border: 1px solid {border}; border-radius: 3px;"
    " padding: 2px 8px; font-weight: bold; }}"
)


class ModeBadge(QLabel):
    """状态栏/页头上的模式徽章。内容全部来自 `BadgeView`。"""

    def __init__(self, view: BadgeView, parent=None):
        super().__init__(parent)
        self.setObjectName("modeBadge")
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._view: BadgeView | None = None
        self.set_view(view)

    @classmethod
    def from_status(cls, status: CalibrationStatus, parent=None) -> "ModeBadge":
        """由启动自检结论直接建一个徽章。调用点（主窗口、各页）用这一个入口。"""
        return cls(build_badge_view(status), parent)

    def set_view(self, view: BadgeView) -> None:
        self._view = view
        self.setText(view.text)
        self.setToolTip(view.tooltip)
        self.setStyleSheet(_QSS.format(fg=view.fg, bg=view.bg, border=view.border))

    @property
    def view(self) -> BadgeView | None:
        """当前挂着的内容。**测试与页面只许读它，不许自己拼文案。**"""
        return self._view
