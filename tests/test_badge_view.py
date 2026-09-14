"""模式徽章守卫（B8 / DP-111）：徽章说的话必须和资质判定完全一致。

这套测试**不 import PySide6**（沙箱里没有）。因此分两层：
内容层 `models/badge.py` 直接调用真函数测；渲染层 `widgets/badge.py` 与主窗口的
接线用 AST 静态检查。渲染层「真的挂上去了」这一条 AST 看不见，
由 `desktop/main.py --self-test` 在 CI 的真 Qt 里验（那是它唯一的运行期证明）。

守卫的对象是**同一类事故**：界面上写着一件事，导出/判定是另一件事。
它不会报错，只会让用户在两句矛盾的话里挑一句信。
"""

from __future__ import annotations

import ast
from pathlib import Path

from desktop.app.models.badge import (
    NO_METROLOGY_LINE, BadgeView, build_badge_view,
)
from desktop.app.services.calibration import (
    Badge, CalibrationStatus, Mode, evaluate_calibration,
)
from desktop.app.utils.paths import bundled_calibration_path

ROOT = Path(__file__).resolve().parent.parent

WIDGET_PY = ROOT / "desktop/app/widgets/badge.py"
MODEL_PY = ROOT / "desktop/app/models/badge.py"
MAIN_WINDOW_PY = ROOT / "desktop/app/main_window.py"


def _parse(path: Path) -> ast.Module:
    """语法坏掉必须让守卫红——不许静默跳过（同 test_desktop_boundary 的口径）。"""
    assert path.exists(), f"{path.relative_to(ROOT)} 不存在"
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as e:
        raise AssertionError(f"{path.relative_to(ROOT)} 语法错误：{e}") from e


def _docstring_ids(tree: ast.Module) -> set[int]:
    """所有文档字符串节点的 id。中文注释/docstring 不算违规，只有**代码里的**文案算。"""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(body[0].value, ast.Constant) and \
                    isinstance(body[0].value.value, str):
                ids.add(id(body[0].value))
    return ids


def _status(badge: Badge, *, mode: Mode = Mode.RESEARCH,
            reasons: tuple[str, ...] = ()) -> CalibrationStatus:
    return CalibrationStatus(mode=mode, badge=badge, reasons=reasons)


def _consistent(badge: Badge) -> CalibrationStatus:
    """该徽章对应的**自洽**状态（绿 ⇔ validated）。

    自洽与不自洽分别由不同的守卫负责：这里要的是「每种徽章都有文案」，
    所以必须先自洽，否则测到的是 `test_inconsistent_status_refuses_to_render` 那条。
    """
    mode = Mode.VALIDATED if badge is Badge.GREEN else Mode.RESEARCH
    return _status(badge, mode=mode)


# --------------------------------------------------------------------------
# 守卫 1：每种徽章都必须有文案，**不许有默认分支**
# --------------------------------------------------------------------------

def test_every_badge_has_a_view():
    """遍历 `Badge` 枚举本身，不是遍历一张手抄的清单。

    将来加第四种徽章而忘了写文案 ⇒ 本条红。若 `build_badge_view` 改用
    `_STYLES.get(badge, 某个默认)`，新徽章会**静默长成旧样子**——那才是危险的。
    """
    assert len(list(Badge)) >= 3, "Badge 枚举被削减了？"
    for badge in Badge:
        view = build_badge_view(_consistent(badge))
        assert isinstance(view, BadgeView)
        assert view.text.strip(), f"{badge} 的徽章文字是空的"
        assert view.tooltip.strip(), f"{badge} 的 tooltip 是空的"

    # 文案表不许带默认值兜底（AST 层面禁 `_STYLES.get(...)`）
    for node in ast.walk(_parse(MODEL_PY)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "get" \
                and isinstance(node.func.value, ast.Name) \
                and node.func.value.id == "_STYLES":
            raise AssertionError(
                f"models/badge.py 第 {node.lineno} 行用了 _STYLES.get(...)："
                "缺文案必须当场 KeyError，不许兜出一个看着正常的徽章")


# --------------------------------------------------------------------------
# 守卫 2：徽章的声称 = 判定模块的结论（唯一来源）
# --------------------------------------------------------------------------

def test_claims_metrology_mirrors_judgement():
    """`claims_metrology` 只许抄 `may_report_metrology`，且**只有绿徽章能声称计量**。

    这一条守的是「界面说计量模式、导出按研究版声明」这类分叉。
    """
    cases = [
        _status(Badge.YELLOW),
        _status(Badge.RED),
        _status(Badge.GREEN, mode=Mode.VALIDATED, reasons=("计量模式：验收批次 X",)),
    ]
    for st in cases:
        view = build_badge_view(st)
        assert view.claims_metrology == st.may_report_metrology, \
            f"{st.badge}/{st.mode} 的 claims_metrology 与判定结论不一致"
        if not st.may_report_metrology:
            assert "计量模式" not in view.text, \
                f"{st.badge}/{st.mode} 没有资质，徽章上却写着「计量模式」：{view.text!r}"
            assert NO_METROLOGY_LINE in view.tooltip, \
                f"{st.badge}/{st.mode} 的 tooltip 缺免责声明"
        else:
            assert NO_METROLOGY_LINE not in view.tooltip, \
                "计量模式下不该出现「没有计量资质」的免责句（自相矛盾）"

    # 内容层不许自己判模式：一个 `Mode.VALIDATED` / "validated" 都不许出现
    src = MODEL_PY.read_text(encoding="utf-8")
    tree = _parse(MODEL_PY)
    doc_ids = _docstring_ids(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "VALIDATED":
            raise AssertionError(
                f"models/badge.py 第 {node.lineno} 行自己判了 Mode.VALIDATED："
                "模式判定只在 services/calibration.py，这里只许抄 may_report_metrology")
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in doc_ids and node.value in ("validated", "research"):
            raise AssertionError(
                f"models/badge.py 第 {node.lineno} 行出现模式字面量 {node.value!r}")
    assert "may_report_metrology" in src, \
        "内容层必须显式引用 may_report_metrology（唯一的资质来源）"


def test_inconsistent_status_refuses_to_render():
    """**绿徽章 ⇔ 有计量资质**，不自洽时不许画，要当场抛。

    这一条是我自己写完守卫 2 第一次跑就抓到的真缺陷：`CalibrationStatus` 里
    `badge` 与 `mode` 是两个独立字段，文案按 `badge` 取、声称按 `mode` 算，
    于是「绿徽章 + research」会在屏幕上写出「计量模式」而 `claims_metrology=False`
    ——界面声称有资质、导出声明没资质，两句话同时存在于一个产品里。
    `evaluate_calibration` 今天产不出这种状态，但**「今天产不出来」不是保证**。
    """
    for st in (_status(Badge.GREEN),                              # 绿 + 无资质
               _status(Badge.YELLOW, mode=Mode.VALIDATED),        # 有资质却不是绿
               _status(Badge.RED, mode=Mode.VALIDATED)):
        try:
            view = build_badge_view(st)
        except ValueError:
            continue
        raise AssertionError(
            f"badge={st.badge}/mode={st.mode} 这种自相矛盾的状态被画了出来："
            f"text={view.text!r}, claims_metrology={view.claims_metrology}")


# --------------------------------------------------------------------------
# 守卫 3：每一条原因都必须原样出现在 tooltip 里
# --------------------------------------------------------------------------

def test_every_reason_appears_verbatim():
    """原因是用户唯一能看到的「为什么是这个徽章」，少一条等于把一个已知问题藏起来。"""
    reasons = (
        "标定文件哈希不符（期望 abc123456789…，实际 def987654321…）——文件被改过，已忽略其内容",
        "第二条原因：门槛被改过",
        "第三条原因：G10a 有假阳性",
    )
    view = build_badge_view(_status(Badge.RED, reasons=reasons))
    for r in reasons:
        assert r in view.tooltip, f"tooltip 里少了这条原因：{r!r}\n实际：{view.tooltip!r}"


# --------------------------------------------------------------------------
# 守卫 4：红徽章必须同时说明「还能用」
# --------------------------------------------------------------------------

def test_red_badge_says_degraded_not_broken():
    """红 = 降级，不是故障。

    `calibration.py` 的设计是「降级 + 说明原因，不是报错退出」。徽章只写
    「标定文件不可信」，用户合理的理解是「这软件坏了」，于是他去重装、去删文件、
    或者干脆不用了——**把一次降级说成一次故障，代价比故障本身大。**
    """
    view = build_badge_view(_status(Badge.RED, reasons=("哈希不符",)))
    assert "研究用途" in view.text, \
        f"红徽章上没说明「仍按研究用途运行」：{view.text!r}"
    assert "仍可继续使用" in view.tooltip, \
        f"红徽章的 tooltip 没说明软件仍可使用：{view.tooltip!r}"


# --------------------------------------------------------------------------
# 守卫 5：渲染层不许持有文案 / 颜色 / 模式判断
# --------------------------------------------------------------------------

def test_widget_holds_no_content():
    """`widgets/badge.py` 只摆不判：**代码里**不许有中文文案、颜色字面量、模式判断。

    文案在渲染层再存一份，两份就会各自演化，而不一致的那一次**不报错**。
    （docstring 与注释不算——文档里写「不许出现颜色」这句话本身不构成违规，
    这是 `test_no_pyside_and_no_engine_import` 第一版踩过的坑。）
    """
    tree = _parse(WIDGET_PY)
    doc_ids = _docstring_ids(tree)

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in doc_ids:
            text = node.value
            if any("一" <= ch <= "鿿" for ch in text):
                raise AssertionError(
                    f"widgets/badge.py 第 {node.lineno} 行有中文文案常量 {text!r}："
                    "文案只许在 models/badge.py 里有一份")
            if "#" in text and any(
                    len(seg) >= 6 and all(c in "0123456789abcdefABCDEF" for c in seg[:6])
                    for seg in text.split("#")[1:]):
                raise AssertionError(
                    f"widgets/badge.py 第 {node.lineno} 行有颜色字面量 {text!r}："
                    "颜色由 BadgeView 给")
        if isinstance(node, ast.Attribute) and node.attr in ("VALIDATED", "RESEARCH",
                                                             "YELLOW", "GREEN", "RED"):
            raise AssertionError(
                f"widgets/badge.py 第 {node.lineno} 行判了 {node.attr}："
                "渲染层不许按模式/徽章种类分支")


def test_model_layer_has_no_qt():
    """内容层必须能在无显示环境里被测：不许 import PySide6，也不许 import 分析包。"""
    imported: set[str] = set()
    for node in ast.walk(_parse(MODEL_PY)):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                raise AssertionError("models/badge.py 用了相对 import（外壳约定绝对 import）")
            if node.module:
                imported.add(node.module.split(".")[0])
    for forbidden in ("PySide6", "depressionplex"):
        assert forbidden not in imported, f"models/badge.py import 了 {forbidden}"


# --------------------------------------------------------------------------
# 守卫 6：文案与判定各自只有一处
# --------------------------------------------------------------------------

def test_badge_text_exists_in_exactly_one_file():
    """「计量模式」这四个字作为**可显示的字符串**，在 `desktop/` 里只许有一处。

    出现第二处 = 出现第二份文案 = 迟早分叉（状态栏说研究版、结果页说计量模式）。
    判定层（`services/calibration.py`）允许有一处：它给的 reason 也会进 tooltip。

    **只看代码里的字符串常量，不看注释与 docstring。**（`desktop/main.py` 的注释里
    就写着「界面写着计量模式而导出按研究版声明」这句解释——第一版按全文 grep，
    它被判成违规。假违规的下一步永远是有人把守卫改松，那才是真损失。）
    """
    allowed = {
        Path("desktop/app/models/badge.py"),
        Path("desktop/app/services/calibration.py"),
    }
    offenders = []
    for py in sorted((ROOT / "desktop").rglob("*.py")):
        rel = py.relative_to(ROOT)
        if rel in allowed:
            continue
        tree = _parse(py)
        doc_ids = _docstring_ids(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and id(node) not in doc_ids and "计量模式" in node.value:
                offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, \
        f"这些地方有第二份「计量模式」文案：{offenders}"


def test_calibration_evaluated_in_exactly_one_place():
    """`evaluate_calibration` 在 `desktop/` 里只许被调用一次（主窗口启动时）。

    调两次 = 两次判定：两次之间文件可以被换掉，于是状态栏与导出可以基于**不同的**
    标定文件各说一句话。各页要用就问 `window.calibration_status`。
    """
    callers = []
    for py in sorted((ROOT / "desktop").rglob("*.py")):
        for node in ast.walk(_parse(py)):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else \
                    (fn.id if isinstance(fn, ast.Name) else None)
                if name == "evaluate_calibration":
                    callers.append(f"{py.relative_to(ROOT)}:{node.lineno}")
    assert callers, "没有任何地方调用 evaluate_calibration —— 启动自检丢了"
    assert len(callers) == 1, f"evaluate_calibration 的调用点不止一个：{callers}"
    assert callers[0].startswith("desktop/app/main_window.py"), \
        f"唯一的调用点应该在主窗口启动时，实际在 {callers[0]}"


# --------------------------------------------------------------------------
# 守卫 7：主窗口真的把徽章接上了（AST 能查的那一半）
# --------------------------------------------------------------------------

def test_main_window_wires_badge_into_status_bar():
    """徽章必须挂在**状态栏的 permanent widget** 上，而不是某一页里。

    挂在页面里的后果：用户从新建实验页直接跑完、直接导出，全程没见过资质声明——
    **看不见的声明等于没有声明。**（「真的挂上去了」由 `--self-test` 在 CI 验。）
    """
    src = MAIN_WINDOW_PY.read_text(encoding="utf-8")
    tree = _parse(MAIN_WINDOW_PY)

    calls = {node.func.attr for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    assert "addPermanentWidget" in calls, \
        "主窗口没有把徽章加成状态栏的 permanent widget（换页就看不见了）"
    assert "from_status" in calls, \
        "主窗口没有走 ModeBadge.from_status（唯一的构造入口）"
    assert "statusBar" in calls, "主窗口没有状态栏"
    assert "mode_badge" in src, "主窗口没有暴露 mode_badge 给各页复用"
    assert "calibration_status" in src, "主窗口没有暴露 calibration_status"


def test_no_second_mode_decision_in_desktop():
    """`desktop/` 里只有判定层能拿模式做分支。

    第二处分支 = 第二个判定器（DP-102 那四个静默缺陷就是这么长出来的）。
    """
    allowed = Path("desktop/app/services/calibration.py")
    offenders = []
    for py in sorted((ROOT / "desktop").rglob("*.py")):
        rel = py.relative_to(ROOT)
        if rel == allowed:
            continue
        tree = _parse(py)
        doc_ids = _docstring_ids(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                for side in [node.left, *node.comparators]:
                    if isinstance(side, ast.Attribute) and side.attr in ("VALIDATED",
                                                                        "RESEARCH"):
                        offenders.append(f"{rel}:{node.lineno}")
                    if isinstance(side, ast.Constant) and side.value in ("validated",
                                                                         "research") \
                            and id(side) not in doc_ids:
                        offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, \
        f"这些地方在判定层之外拿模式做了分支：{sorted(set(offenders))}"


# --------------------------------------------------------------------------
# 守卫 8：端到端走产品真实路径（不用手搓 status）
# --------------------------------------------------------------------------

def test_end_to_end_with_real_startup_path():
    """用**产品启动时真正调用的那两个函数**过一遍：路径派生 + 资质判定 + 文案。

    手搓 `CalibrationStatus` 测得再全，也不能证明产品启动时拿到的是这个结论。
    当前状态（2026-09-14）：仓根没有 `calibration.json`、
    `EXPECTED_CALIBRATION_SHA256` 是 None ⇒ 黄徽章、研究版、无计量资质。
    """
    path = bundled_calibration_path()
    assert path.name == "calibration.json", f"派生出来的路径不对：{path}"

    status = evaluate_calibration(path)
    view = build_badge_view(status)

    assert status.mode is Mode.RESEARCH, \
        f"当前发布态应为研究版，实际 {status.mode}（随包标定文件出现了？）"
    assert status.badge is Badge.YELLOW, f"应为黄徽章，实际 {status.badge}：{status.reasons}"
    assert view.claims_metrology is False
    assert NO_METROLOGY_LINE in view.tooltip
    assert "计量模式" not in view.text
    for reason in status.reasons:
        assert reason in view.tooltip
