"""DEPRESSION-PLEX 桌面外壳入口。

启动方式**只有一种**（架构 §3.4）：仓根为 cwd，`python -m desktop.main`；
`--self-test` 是无显示自检，CI 的唯一判据。

退出码：0 = 正常 / 自检通过；2 = 自检不通过（与引擎 CLI 的 2 同义：判据不满足）。
"""

import os
import sys
import time

import PySide6
from PySide6.QtWidgets import QApplication

from desktop.app.main_window import MainWindow, PAGE_ORDER
from desktop.app.pages import placeholders
from desktop.app.utils.paths import resource_path, user_data_dir

# Windows 控制台默认不是 UTF-8，中文页名会抛 UnicodeEncodeError 而不是打印乱码
# ——那会让自检以「崩溃」而不是「不通过」的形式失败，归因时容易找错方向。
if sys.platform == "win32":
    import io

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

STYLESHEET = "app/styles/dark.qss"


def load_stylesheet(app: QApplication) -> bool:
    """套上暗色皮肤。返回是否加载成功。"""
    path = resource_path(STYLESHEET)
    if not path.exists():
        return False
    app.setStyleSheet(path.read_text(encoding="utf-8"))
    return True


def self_test() -> int:
    """无显示自检：逐个构造页面、核对页面清单、打印耗时，**不起窗不进事件循环**。

    任何一条不满足都返回 2。**尤其是皮肤缺失也算不通过**——`dark.qss` 是打包时
    靠 spec 的 `datas` 带进去的，漏了它产品能启动但一身默认灰皮，
    而「能启动」正是最容易被当成通过的那种失败（B10 改 spec 时最容易踩）。
    """
    t0 = time.perf_counter()
    app = QApplication(sys.argv)
    problems: list[str] = []

    print(f"PySide6 {PySide6.__version__}")
    print(f"QT_QPA_PLATFORM={os.environ.get('QT_QPA_PLATFORM', '(未设置)')}")
    # 只算路径不建目录：自检是诊断，不该在客户机上留下任何东西
    print(f"user_data_dir={user_data_dir(create=False)}")

    if load_stylesheet(app):
        print(f"皮肤已加载 {STYLESHEET}")
    else:
        problems.append(f"皮肤缺失：{resource_path(STYLESHEET)}")

    # 逐个单独构造，时间才是真的量出来的；顺带证明每个页面**不依赖父窗口**也能构造
    print("逐页构造：")
    for name, cls_name in PAGE_ORDER:
        cls = getattr(placeholders, cls_name)
        t = time.perf_counter()
        cls()
        print(f"  {name} ({cls_name}) {(time.perf_counter() - t) * 1000:.2f} ms")

    window = MainWindow()
    got = tuple(window.pages)
    want = tuple(name for name, _ in PAGE_ORDER)
    if got != want:
        problems.append(f"页面清单不符：期望 {want}，实际 {got}")

    total_ms = (time.perf_counter() - t0) * 1000
    if problems:
        for p in problems:
            print(f"自检不通过：{p}", file=sys.stderr)
        print(f"SELF-TEST FAILED pages={len(got)} total_ms={total_ms:.2f}")
        return 2

    print(f"SELF-TEST OK pages={len(got)} total_ms={total_ms:.2f}")
    return 0


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        return self_test()

    app = QApplication(sys.argv)
    if not load_stylesheet(app):
        # 正常启动时皮肤缺失不拦人（能用比好看重要），但必须说出来
        print(f"警告：皮肤缺失 {resource_path(STYLESHEET)}", file=sys.stderr)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
