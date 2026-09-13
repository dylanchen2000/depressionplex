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
        # 不许 `if not exists: continue`——文件没了就该红，而不是安静通过。
        # 本仓吃过三次这个教训（DP-069 / DP-071 / DP-076）：**只有失败时才执行的分支
        # 必须被测试直接踩**，能被跳过的守卫等于装饰。
        assert workflow.exists(), f"{workflow.name} 不存在"
        content = workflow.read_text()
        for pattern in forbidden:
            assert pattern not in content, f"{workflow.name} 包含 {pattern}（绿灯造假）"


def test_no_duplicate_triggers():
    """两个 workflow 都不许同时挂 push 与 pull_request。

    同一个 SHA 会跑两遍，结论完全相同，白烧一倍 Actions 分钟数（windows 还按 2× 计费）。
    检查结果挂在 commit SHA 上，只挂 push 时 PR 页面照样看得到。
    """
    for name in ("tests.yml", "desktop-selftest.yml"):
        workflow = ROOT / ".github/workflows" / name
        assert workflow.exists(), f"{name} 不存在"
        content = workflow.read_text()
        # 只看 `on:` 那一段之前的触发声明，注释里提到 pull_request 不算违规
        body = "\n".join(ln for ln in content.splitlines()
                         if not ln.lstrip().startswith("#"))
        assert "pull_request" not in body, f"{name} 挂了 pull_request，会让同一个 SHA 跑两遍"


def test_offscreen_qt():
    """desktop-selftest.yml 必须设置 QT_QPA_PLATFORM=offscreen"""
    workflow = ROOT / ".github/workflows/desktop-selftest.yml"
    assert workflow.exists(), "desktop-selftest.yml 不存在"   # 同上，不许静默 return
    content = workflow.read_text()
    assert "QT_QPA_PLATFORM" in content, "desktop-selftest.yml 缺少 QT_QPA_PLATFORM"
    assert "offscreen" in content, "desktop-selftest.yml QT_QPA_PLATFORM 值不是 offscreen"
