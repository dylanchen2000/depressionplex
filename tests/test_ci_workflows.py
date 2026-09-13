"""CI workflow 守卫测试（DP-098）。

确保 GitHub Actions workflow 文件存在且不被悄悄删除或破坏。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_tests_workflow_exists():
    """tests.yml 必须存在且调用 run_tests.py"""
    workflow = ROOT / ".github/workflows/tests.yml"
    assert workflow.exists(), "tests.yml 不存在"
    content = workflow.read_text()
    assert "run_tests.py" in content, "tests.yml 未调用 run_tests.py"


def test_desktop_workflow_exists():
    """desktop-selftest.yml 必须存在且覆盖 ubuntu/windows"""
    workflow = ROOT / ".github/workflows/desktop-selftest.yml"
    assert workflow.exists(), "desktop-selftest.yml 不存在"
    content = workflow.read_text()
    assert "ubuntu-latest" in content, "desktop-selftest.yml 缺少 ubuntu-latest"
    assert "windows-latest" in content, "desktop-selftest.yml 缺少 windows-latest"


def test_no_error_masking():
    """CI 文件不得包含掩盖失败的写法"""
    forbidden = ["continue-on-error", "|| true"]
    workflows = [
        ROOT / ".github/workflows/tests.yml",
        ROOT / ".github/workflows/desktop-selftest.yml",
    ]
    for workflow in workflows:
        if not workflow.exists():
            continue
        content = workflow.read_text()
        for pattern in forbidden:
            assert pattern not in content, f"{workflow.name} 包含 {pattern}（绿灯造假）"


def test_offscreen_qt():
    """desktop-selftest.yml 必须设置 QT_QPA_PLATFORM=offscreen"""
    workflow = ROOT / ".github/workflows/desktop-selftest.yml"
    if not workflow.exists():
        return
    content = workflow.read_text()
    assert "QT_QPA_PLATFORM" in content, "desktop-selftest.yml 缺少 QT_QPA_PLATFORM"
    assert "offscreen" in content, "desktop-selftest.yml QT_QPA_PLATFORM 值不是 offscreen"
