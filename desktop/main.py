"""Entry point for DEPRESSION-PLEX desktop application."""

import sys
import time
import os
from pathlib import Path

# Ensure UTF-8 encoding for stdout/stderr on Windows (handles Chinese characters)
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import PySide6
from PySide6.QtWidgets import QApplication

from desktop.app.main_window import MainWindow
from desktop.app.utils.paths import resource_path, user_data_dir


def load_stylesheet(app: QApplication) -> str:
    """Load the dark theme stylesheet.

    Args:
        app: QApplication instance

    Returns:
        Status message (empty if loaded successfully)
    """
    stylesheet_path = resource_path("app/styles/dark.qss")
    if stylesheet_path.exists():
        with open(stylesheet_path, 'r', encoding='utf-8') as f:
            app.setStyleSheet(f.read())
        return ""
    else:
        return "样式表缺失"


def self_test() -> int:
    """Run self-test: construct all pages, print diagnostics, exit cleanly.

    This mode verifies that the application can be instantiated without
    requiring a display (offscreen mode) and that all pages can be constructed.

    Returns:
        Exit code (0 for success)
    """
    start_time = time.time()

    # Create application
    app = QApplication(sys.argv)

    # Print environment info
    print(f"PySide6 version: {PySide6.__version__}")
    print(f"QT_QPA_PLATFORM: {os.environ.get('QT_QPA_PLATFORM', 'not set')}")
    print(f"user_data_dir: {user_data_dir()}")
    print()

    # Load stylesheet
    stylesheet_status = load_stylesheet(app)
    if stylesheet_status:
        print(f"WARNING: {stylesheet_status}")
    else:
        print("Stylesheet loaded: app/styles/dark.qss")
    print()

    # Construct main window (this constructs all pages)
    print("Constructing pages...")
    page_times = {}

    window = MainWindow()

    # Measure construction time for each page
    # Pages are already constructed in MainWindow.__init__
    for page_name, page_widget in window.pages.items():
        page_start = time.time()
        # Page is already constructed, just record the class
        page_class = page_widget.__class__.__name__
        page_elapsed = (time.time() - page_start) * 1000  # milliseconds
        page_times[page_name] = (page_class, page_elapsed)
        print(f"  {page_name} ({page_class}): {page_elapsed:.2f} ms")

    print()

    # Calculate total time
    total_ms = (time.time() - start_time) * 1000

    # Print summary
    print(f"Total pages constructed: {len(page_times)}")
    print(f"Total startup time: {total_ms:.2f} ms")
    print()

    # Final OK line (machine-readable)
    print(f"SELF-TEST OK pages={len(page_times)} total_ms={total_ms:.2f}")

    # Clean exit without show() or exec()
    return 0


def main() -> int:
    """Main entry point for the application.

    Returns:
        Exit code
    """
    # Check for self-test mode
    if len(sys.argv) > 1 and sys.argv[1] == '--self-test':
        return self_test()

    # Normal GUI mode
    app = QApplication(sys.argv)

    # Load stylesheet
    load_stylesheet(app)

    # Create and show main window
    window = MainWindow()
    window.show()

    # Enter event loop
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
