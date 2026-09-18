"""桌面 UI 交接守卫（实验路径 → 队列；结果页多视频；占位页诚实）。

沙箱无 PySide6：本文件只用 AST / 源码字符串断言。
真实交互（emit → 队列加载、视频下拉切换）在 `desktop.main --self-test` 里跑。
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUEUE_PY = ROOT / "desktop" / "app" / "pages" / "queue.py"
MAIN_WINDOW_PY = ROOT / "desktop" / "app" / "main_window.py"
RESULTS_PY = ROOT / "desktop" / "app" / "pages" / "results.py"
PLACEHOLDERS_PY = ROOT / "desktop" / "app" / "pages" / "placeholders.py"
MAIN_PY = ROOT / "desktop" / "main.py"
README = ROOT / "desktop" / "README.md"


def _non_docstring_strings(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    doc_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                doc_nodes.add(id(body[0].value))
    out: list[str] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in doc_nodes:
            out.append(n.value)
        elif isinstance(n, ast.JoinedStr):
            for v in n.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    out.append(v.value)
    return out


def test_queue_must_not_hardcode_test_experiment_json():
    """队列不许再写死 test_experiment.json（交接必须吃真实路径）。

    变异：把 load 路径改回 user_data_dir()/\"test_experiment.json\" ⇒ 本条红。
    """
    strings = _non_docstring_strings(QUEUE_PY)
    assert "test_experiment.json" not in strings, (
        "queue.py 代码里仍出现 test_experiment.json 字面量——"
        "加载必须走 load_experiment_from_path(真实路径) / 文件选择器"
    )


def test_queue_exposes_load_experiment_from_path():
    """公共入口：向导与「加载实验」共用 load_experiment_from_path。"""
    tree = ast.parse(QUEUE_PY.read_text(encoding="utf-8"))
    methods = {
        n.name
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef)
    }
    assert "load_experiment_from_path" in methods, (
        "QueuePage 缺少 load_experiment_from_path——主窗口没法把向导路径交进去"
    )


def test_main_window_wires_experiment_created():
    """主窗口必须把 NewExperimentPage.experiment_created 接到队列加载。

    变异：删掉 .experiment_created.connect(...) ⇒ 本条红。
    """
    src = MAIN_WINDOW_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    connected = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # …experiment_created.connect(...)
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "connect"
            and isinstance(func.value, ast.Attribute)
            and func.value.attr == "experiment_created"
        ):
            connected = True
            break
    assert connected, (
        "main_window.py 没有 experiment_created.connect——"
        "向导写完 experiment.json 后队列收不到路径"
    )
    assert "_on_experiment_created" in src
    assert "load_experiment_from_path" in src


def test_results_page_has_video_selector_not_first_only():
    """结果页必须有视频选择，不许只写死 video_index=0。

    变异：删掉 video_combo / set_experiment，恢复「暂时只显示第一个」⇒ 本条红。
    """
    src = RESULTS_PY.read_text(encoding="utf-8")
    assert "暂时只显示第一个视频" not in src
    assert "video_combo" in src
    assert "set_experiment" in src
    tree = ast.parse(src)
    methods = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "set_experiment" in methods
    assert "_on_video_changed" in methods


def test_placeholder_pages_honestly_labeled():
    """复核 / 侧栏导出必须标明「尚未实现」或「占位」，不许「本页由 Bx 交付」。"""
    src = PLACEHOLDERS_PY.read_text(encoding="utf-8")
    assert "本页由 B5 交付" not in src
    assert "本页由 B6 交付" not in src
    assert "尚未实现" in src
    assert "占位" in src
    # 侧栏导出必须指回结果页的导出按钮
    assert "结果" in src and "导出…" in src


def test_desktop_readme_documents_placeholder_honesty():
    """产品说明（desktop/README）必须诚实标注复核/侧栏导出仍为占位。"""
    text = README.read_text(encoding="utf-8")
    assert "占位" in text
    assert "复核" in text
    # 不许还写「六个页面均为占位」那种过时总表
    assert "其余六个页面均为占位实现" not in text
    assert "未实现" in text or "尚未实现" in text


def test_self_test_runs_handoff_probe():
    """自检必须调用交接探针（真 Qt 路径），不许只构造页面。"""
    tree = ast.parse(MAIN_PY.read_text(encoding="utf-8"))
    names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "_probe_experiment_handoff" in names
    # self_test 里要调用它
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "self_test")
    called = False
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "_probe_experiment_handoff":
                called = True
    assert called, "self_test() 没有调用 _probe_experiment_handoff"
