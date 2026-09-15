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
import shutil
import sys
import tempfile
from pathlib import Path

from desktop.app.models.badge import (
    NO_METROLOGY_LINE, BadgeView, build_badge_view,
)
from desktop.app.services.calibration import (
    Badge, CalibrationStatus, Mode, evaluate_calibration, file_sha256,
)
from desktop.app.utils import paths as paths_mod
from desktop.app.utils.paths import bundled_calibration_path, is_frozen

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

    **判据是「任何形状」，不是「比较表达式」**（B8 复核 R3）：第一版只看
    `ast.Compare`，实测（M4）往页面里塞
    `_CAN_REPORT = {Mode.VALIDATED: True}` + `_CAN_REPORT.get(status.mode, False)`
    时 12 条守卫全绿——字典派发里没有 Compare 节点，而那段注入连一个中文字都没有，
    所以「文案只许一处」那条也抓不到它。两条守卫的覆盖之间有缝，
    第二个判定器正好从缝里长出来。改成扫所有 `Attribute` 之后，
    `match status.mode: case Mode.VALIDATED:` 也一并盖住了（`MatchValue`
    里面就是一个 `Attribute` 节点）——CI 是 3.11，那里 `match` 是合法语法。
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
            if isinstance(node, ast.Attribute) and node.attr in ("VALIDATED",
                                                                 "RESEARCH"):
                offenders.append(f"{rel}:{node.lineno}")
            if isinstance(node, ast.Constant) and node.value in ("validated",
                                                                 "research") \
                    and id(node) not in doc_ids:
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


# --------------------------------------------------------------------------
# 守卫 9（B8 复核 R2）：三块徽章的文案逐字钉死
# --------------------------------------------------------------------------

#: badge → 徽章上必须**逐字**出现的那句话。
#:
#: 用**全等**不用「包含」：这三句是对客户的资质声明，改动它们必须是一次自觉的决定，
#: 而不是顺手改文案时的副作用。想改词就得连这张表一起改，改的时候会看见这段说明。
EXPECTED_BADGE_TEXT = {
    Badge.YELLOW: "研究用途 · 未计量标定",
    Badge.GREEN: "计量模式",
    Badge.RED: "标定不可信 · 已按研究用途运行",
}


def test_badge_wording_pinned_verbatim():
    """徽章文案不许被换成另一种口径，也不许三块之间对调。

    这一条补的是复核 R2 实测出来的洞（变异 M2 / M3）：
    - M2 把绿徽章的「计量模式」改成「研究用途 · 已验证」⇒ **12 条守卫全绿**；
    - M3 把黄徽章与红徽章的 label 互换 ⇒ **12 条守卫全绿**；
    - M2b 把绿徽章文案改成空串 ⇒ 红。

    也就是说：**「不为空」被验了，「说的是真话」没被验**（共同约定 §6）。
    `test_badge_text_exists_in_exactly_one_file` 只断言「别的文件里*没有*这四个字」，
    从来没断言 `models/badge.py` 里*有*这四个字；
    `test_claims_metrology_mirrors_judgement` 对绿徽章只断了「tooltip 里没有免责句」
    这个反方向。只断一个方向的守卫拦不住换口径。
    """
    assert set(EXPECTED_BADGE_TEXT) == set(Badge), \
        f"Badge 枚举变了，这张表要跟着改：{set(Badge) ^ set(EXPECTED_BADGE_TEXT)}"

    for badge, want in EXPECTED_BADGE_TEXT.items():
        view = build_badge_view(_consistent(badge))
        assert view.text == want, \
            f"{badge.value} 徽章的文案变了：期望 {want!r}，实际 {view.text!r}"

    # 反方向：没有资质的两块，文案里绝不许出现「计量模式」
    for badge in (Badge.YELLOW, Badge.RED):
        assert "计量模式" not in EXPECTED_BADGE_TEXT[badge]


def test_no_metrology_line_pinned_verbatim():
    """免责句的**值**钉一次 —— 常量被读到 ≠ 值是对的（共同约定 §6）。

    全仓现在只有 `NO_METROLOGY_LINE in view.tooltip` 这种用法，
    把这个常量改成「本软件功能齐全。」，所有断言照样成立。
    """
    assert NO_METROLOGY_LINE == \
        "本软件当前没有计量资质，秒数不得作为计量结果或合规证据使用。", \
        f"免责句被改了：{NO_METROLOGY_LINE!r}——这句话是对客户的声明，改它要走复核"


# --------------------------------------------------------------------------
# 守卫 10（B8 复核 R1）：随包标定文件的路径，**目录**也要验，还要有一次正向往返
# --------------------------------------------------------------------------

def test_calibration_path_pinned_in_source_mode():
    """源码模式下派生的是**仓根**的 `calibration.json`，不是别的某一级目录。

    原来唯一碰这个函数的断言是 `path.name == "calibration.json"`——只验文件名。
    实测（M1）把 `paths.py` 的 `parents[3]` 改成 `parents[2]`（指向 `desktop/`）
    ⇒ **12 条守卫全绿**。今天不报错的原因是：仓根本来就没有标定文件，
    指哪儿都读不到 ⇒ 黄徽章 ⇒ 端到端那条断言的 YELLOW / RESEARCH 照样成立。
    **那条测试分不清「路径对但文件不存在」和「路径压根指错了」。**
    """
    assert not is_frozen(), "本条只在源码模式下有意义（CI 与本地都不是冻结态）"
    assert bundled_calibration_path() == ROOT / "calibration.json", \
        f"源码模式派生的路径不对：{bundled_calibration_path()}，应为 {ROOT / 'calibration.json'}"


def test_calibration_path_pinned_in_frozen_mode():
    """冻结模式下派生的是 **exe 同级目录**，不是 `_MEIPASS`。

    实测（M1b）把冻结分支改回 `resource_path(...)` ⇒ **12 条守卫全绿**，
    而 `paths.py` 的 docstring 为这个决定论证了半屏：`_MEIPASS` 在 onefile 下是
    每次启动重建的临时目录，标定文件是**资质凭据**，必须跟 exe 一起被签名、
    被安装脚本放在 IT 审计得到的位置。**论证了半屏、零个守卫。**
    """
    tmp = tempfile.TemporaryDirectory()
    try:
        app_dir = Path(tmp.name) / "app"
        app_dir.mkdir()
        fake_exe = app_dir / "DEPRESSION-PLEX.exe"
        orig_frozen, orig_exe = paths_mod.is_frozen, sys.executable
        try:
            paths_mod.is_frozen = lambda: True
            sys.executable = str(fake_exe)
            got = paths_mod.bundled_calibration_path()
        finally:
            # 打了补丁不恢复会污染同进程后面的测试——本仓 runner 是单进程
            paths_mod.is_frozen = orig_frozen
            sys.executable = orig_exe
        assert got == app_dir / "calibration.json", \
            f"冻结模式派生的路径不对：{got}，应为 exe 同级的 {app_dir / 'calibration.json'}"
    finally:
        tmp.cleanup()


def test_calibration_file_is_read_from_the_derived_path():
    """**正向往返**：把真标定文件放到派生出来的那个路径上，判定必须真的读到它。

    前两条钉的是「路径等于某个值」；这一条钉的是**「写文件的位置」和「读文件的位置」
    是同一个地方**——名册双向对账，不是只查一个方向（共同约定 §6）。

    为什么非要有这一条：到 M4 真签发了标定文件、安装脚本按 `{app}\\calibration.json`
    放好之后，路径错的后果是产品**永远认不出自己的资质**，徽章永远黄，
    CI 全绿、无任何报错，由客户来发现。反过来若那时哈希常量已填，
    路径错会走「该有却没有」规则 ⇒ 红徽章，而根因是路径 bug 不是标定问题,
    **失败归因被抹平成另一件事**。

    用 `docs/标定文件示例_研究版.json` 而不是手搓 payload：
    `tests/test_calibration_contract.py` 已经在防这份示例漂移，两边共用同一份就不会各自漂。
    """
    example = ROOT / "docs/标定文件示例_研究版.json"
    assert example.exists(), "示例标定文件不在了，本条没法验往返"

    tmp = tempfile.TemporaryDirectory()
    try:
        app_dir = Path(tmp.name) / "app"
        app_dir.mkdir()
        fake_exe = app_dir / "DEPRESSION-PLEX.exe"
        orig_frozen, orig_exe = paths_mod.is_frozen, sys.executable
        try:
            paths_mod.is_frozen = lambda: True
            sys.executable = str(fake_exe)
            target = paths_mod.bundled_calibration_path()
            shutil.copyfile(example, target)

            # 正向：文件就在派生路径上 ⇒ 必须被读到、被解析出来
            status = evaluate_calibration(
                paths_mod.bundled_calibration_path(),
                expected_sha256=file_sha256(target))
            assert status.calibration is not None, \
                f"文件明明在 {target}，判定却没读到：{status.reasons}"
            assert status.calibration.theta_mob == 0.0175, \
                "读到了但内容不对——示例文件里的 θ_mob 不是冻结值"
            assert status.badge is Badge.YELLOW, \
                f"示例是研究版（G11 门槛为 null）应判黄，实际 {status.badge}：{status.reasons}"

            # 反向：把文件挪到派生路径的**上一级**，就必须读不到。
            # 少了这一臂，「读到了」有可能只是因为碰巧哪儿都能读到。
            target.unlink()
            shutil.copyfile(example, Path(tmp.name) / "calibration.json")
            miss = evaluate_calibration(
                paths_mod.bundled_calibration_path(),
                expected_sha256=file_sha256(example))
            assert miss.calibration is None, \
                "文件在上一级目录却被读到了——派生路径没起作用，这条往返测不出东西"
        finally:
            paths_mod.is_frozen = orig_frozen
            sys.executable = orig_exe
    finally:
        tmp.cleanup()


# --------------------------------------------------------------------------
# 守卫 11（B8 复核 R4）：标定文件只许有一个读它的地方
# --------------------------------------------------------------------------

def _references(tree: ast.Module, target: str) -> list[int]:
    """`target` 这个名字在一棵 AST 里被**引用**到的所有行号。

    **判据落在 import 上，不落在调用上**：第一版只找 `ast.Call` 里 `func` 是
    `ast.Name(id=target)` 的地方，结果变异 M5 用
    `from ...paths import bundled_calibration_path as _bcp` 一行别名就绕过去了
    （调用点的 Name 是 `_bcp`，守卫看不见）——**守卫自己有洞，是它自己的变异测出来的**。
    别名可以随便起，但 `ImportFrom` 里的原名改不了，所以判据钉在原名上。
    三种形状全收：`from x import target [as y]`、`x.target`、裸 `target`。
    """
    hits: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            hits.extend(node.lineno for a in node.names if a.name == target)
        elif isinstance(node, ast.Attribute) and node.attr == target:
            hits.append(node.lineno)
        elif isinstance(node, ast.Name) and node.id == target:
            hits.append(node.lineno)
    return sorted(set(hits))


#: 标定这条链上「只许启动自检碰」的两个入口，以及各自的定义文件（定义处不算引用）。
CALIBRATION_ENTRY_POINTS = {
    "bundled_calibration_path": Path("desktop/app/utils/paths.py"),
    "evaluate_calibration": Path("desktop/app/services/calibration.py"),
}


def test_calibration_entry_points_only_in_main_window():
    """派生标定路径、判定标定结论，`desktop/` 里都只许主窗口启动时碰。

    `evaluate_calibration` 原本有一条单调用点守卫（`test_calibration_evaluated_in_
    exactly_one_place`），但它和本条的第一版一样只看调用点的名字，**别名 import
    就能绕过**。那条保留（它没错，只是窄），本条从 import 名字上把两个入口一起钉死。

    为什么 `bundled_calibration_path` 也要管：实测（M5）往页面里塞
    `json.loads(bundled_calibration_path().read_text())` ⇒ 补齐前 12 条守卫全绿。
    这比「第二个判定器」更糟——第二个判定器至少还走 `evaluate_calibration` 的规则，
    自己读文件的那份**绕过哈希校验、绕过降级、绕过 reasons**，
    连「这个文件可疑」都判不出来。
    """
    allowed = Path("desktop/app/main_window.py")
    offenders = []
    seen_in_main = {name: 0 for name in CALIBRATION_ENTRY_POINTS}

    for py in sorted((ROOT / "desktop").rglob("*.py")):
        rel = py.relative_to(ROOT)
        tree = _parse(py)
        for name, definer in CALIBRATION_ENTRY_POINTS.items():
            if rel == definer:
                continue                      # 定义处
            lines = _references(tree, name)
            if not lines:
                continue
            if rel == allowed:
                seen_in_main[name] += len(lines)
            else:
                offenders.append(f"{rel}:{lines} 碰了 {name}")

    assert not offenders, \
        f"标定入口只许主窗口启动时碰，这些地方绕过去了：{offenders}"
    for name, count in seen_in_main.items():
        assert count, f"主窗口里找不到 {name} 的引用——启动自检丢了"


def test_calibration_filename_literal_in_exactly_one_file():
    """`calibration.json` 这个字面量在 `desktop/` 里只许出现在 `utils/paths.py`。

    单调用点守卫拦得住 `bundled_calibration_path()`，拦不住有人自己写
    `Path("calibration.json")` 再读一遍。文件名分叉之后，
    安装脚本放一个名字、产品找另一个名字，症状是「装好了但徽章永远黄」。

    **只看代码里的字符串常量，不看注释与 docstring**（同
    `test_badge_text_exists_in_exactly_one_file` 的口径：假违规的下一步
    永远是有人把守卫改松）。
    """
    allowed = Path("desktop/app/utils/paths.py")
    offenders = []
    for py in sorted((ROOT / "desktop").rglob("*.py")):
        rel = py.relative_to(ROOT)
        if rel == allowed:
            continue
        tree = _parse(py)
        doc_ids = _docstring_ids(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and id(node) not in doc_ids and "calibration.json" in node.value:
                offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, \
        f"这些地方自己写了标定文件名，应改成引 paths.CALIBRATION_FILENAME：{offenders}"
