"""路径隔离守卫：把「FST 研究入口没碰 TST 专属判据」变成机器可查的事实。

Spec A §4 A1 的验收原话是「路径测试证明未调用 TST 专属判据」。
光靠人读代码不算数——TST 判据散在 `runner` / `assay_core.rules` /
`assay_core.segment` 三个模块里，名字又都长得像通用几何函数
（`build_plan`、`panel_band`、`find_chambers`），抄错一行没人看得出来。
所以这里给两道闸：

- **静态闸** `audit_package()`：AST 扫本包与 CLI 入口，
  任何对 TST 专属符号的**引用**（import / 属性 / 裸名）都算违规。
  只扫代码节点，不扫字符串常量——文档字符串里点名批评某个函数是允许的。
- **动态闸** `tst_forbidden_raising()`：把每个 TST 专属符号就地换成
  「一被碰就 raise」的哨兵，然后真跑一遍研究路径。
  静态闸抓得住"写了名字"，动态闸抓得住"名字是拼出来的/反射调的"。

另外钉一条：研究入口**不依赖 CSI** 才能运行（Spec A §4 A1 末句），
所以 `depressionplex.csi` 整包也在禁止名单里。
"""

from __future__ import annotations

import ast
import contextlib
import importlib
import unittest.mock as mock
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent

#: 整包禁止 import 的模块前缀。研究入口不依赖它们才能运行（Spec A §4 A1）。
FORBIDDEN_MODULE_PREFIXES: tuple[str, ...] = (
    "depressionplex.runner",
    "depressionplex.assay_core.rules",
    "depressionplex.csi",
    "depressionplex.lovo_cv",
)

#: TST 专属符号黑名单：(模块, 属性名)。
#: 左列是静态闸扫的名字，右列同时是动态闸要武装的落点。
TST_ONLY_SYMBOLS: tuple[tuple[str, str], ...] = (
    # runner：悬挂点/标定/出数链条，全部建在"胶带走廊"这个 TST 事实上
    ("depressionplex.runner", "analyze_chamber"),
    ("depressionplex.runner", "build_plan"),
    ("depressionplex.runner", "_suspension_from_corridor"),
    ("depressionplex.runner", "N_CALIB_FRAMES"),
    # rules：TST 事件判定与冻结阈值（theta_mob 是评审护栏，只许双人 ethogram 重标）
    ("depressionplex.assay_core.rules", "build_tst_features"),
    ("depressionplex.assay_core.rules", "label_tst_events"),
    ("depressionplex.assay_core.rules", "summarize_tst"),
    ("depressionplex.assay_core.rules", "TstFeatures"),
    ("depressionplex.assay_core.rules", "TstRulesParams"),
    ("depressionplex.assay_core.rules", "TstEventLabels"),
    ("depressionplex.assay_core.rules", "hind_index_for_frame"),
    ("depressionplex.assay_core.rules", "theta_mob"),
    # segment：胶带走廊 / 背光面板行带 / TST 四隔间立柱，全是悬挂场景的几何事实
    ("depressionplex.assay_core.segment", "calibrate_tape_corridor"),
    ("depressionplex.assay_core.segment", "TapeCorridor"),
    ("depressionplex.assay_core.segment", "_segment_with_corridor"),
    ("depressionplex.assay_core.segment", "panel_band"),
    ("depressionplex.assay_core.segment", "find_chambers"),
    ("depressionplex.assay_core.segment", "segment_animal"),
    # geometry：悬挂点推断是 TST 概念；FST 只用 tank / water_surface
    ("depressionplex.assay_core.geometry", "ROLE_SUSPENSION_BAR"),
    ("depressionplex.assay_core.geometry", "ROLE_SUSPENSION_POINT"),
    ("depressionplex.assay_core.geometry", "ROLE_TAPE_CORRIDOR"),
)

#: 名字层面的黑名单 = 上面所有属性名。AST 扫到同名裸名/属性即违规。
TST_ONLY_NAMES: frozenset[str] = frozenset(name for _, name in TST_ONLY_SYMBOLS)

#: 动态闸武装不了、但静态闸仍然扫的名字，逐个写明原因。
#: 这张表是**穷举**的：`tst_forbidden_raising` 遇到表外缺失的落点会直接 raise，
#: 防止哪天上游改了名，动态闸悄悄少武装一个而没人发现。
RUNTIME_UNARMABLE: dict[str, str] = {
    # theta_mob 是 TstRulesParams 的**字段默认值**（rules.py:198），不是模块级名字，
    # patch 一个 dataclass 字段默认值没有意义；引用它的代码一定先碰 TstRulesParams，
    # 而 TstRulesParams 在武装名单里。静态闸照扫 theta_mob 这个名字。
    "depressionplex.assay_core.rules.theta_mob":
        "dataclass 字段默认值，非模块级名字；被 TstRulesParams 的武装覆盖",
}


def _scanned_files() -> list[Path]:
    files = sorted(PACKAGE_DIR.glob("*.py"))
    cli = PACKAGE_DIR.parent / "cli" / "fst_research.py"
    if cli.exists():
        files.append(cli)
    return files


def audit_package() -> list[str]:
    """静态闸：返回违规列表，空列表 = 干净。

    扫两种节点：
    - import / from-import：模块前缀命中 `FORBIDDEN_MODULE_PREFIXES`，
      或 from-import 的名字命中 `TST_ONLY_NAMES`；
    - 裸名与属性名命中 `TST_ONLY_NAMES`。

    字符串常量**不扫**——docstring 里写"不许调 calibrate_tape_corridor"是好事。
    """
    violations: list[str] = []
    for path in _scanned_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.name
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _prefix_hit(alias.name):
                        violations.append(
                            f"{rel}:{node.lineno}: import {_prefix_hit(alias.name)}"
                            f"（{alias.name}）——研究入口不依赖它才能运行")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                hit = _prefix_hit(mod)
                if hit:
                    violations.append(
                        f"{rel}:{node.lineno}: from {mod} import …（{hit} 被禁止）")
                for alias in node.names:
                    if alias.name in TST_ONLY_NAMES:
                        violations.append(
                            f"{rel}:{node.lineno}: from {mod} import {alias.name}"
                            f"（TST 专属符号）")
            elif isinstance(node, ast.Attribute):
                if node.attr in TST_ONLY_NAMES:
                    violations.append(
                        f"{rel}:{node.lineno}: 属性 .{node.attr}（TST 专属符号）")
            elif isinstance(node, ast.Name):
                if node.id in TST_ONLY_NAMES:
                    violations.append(
                        f"{rel}:{node.lineno}: 裸名 {node.id}（TST 专属符号）")
    return violations


def _prefix_hit(module: str) -> str | None:
    for prefix in FORBIDDEN_MODULE_PREFIXES:
        if module == prefix or module.startswith(prefix + "."):
            return prefix
    return None


class IsolationViolation(RuntimeError):
    """动态闸被触发：研究路径碰了一个 TST 专属符号。"""


@contextlib.contextmanager
def tst_forbidden_raising():
    """动态闸：把每个 TST 专属符号换成哨兵，退出时原样还原。

    哨兵被**调用**或**读取**都炸——`mock.patch` 的 side_effect 只管调用，
    所以哨兵本身做成一个 property-less 的类实例，`__getattr__`/`__call__`
    全指向 raise，读属性（如 `rules.theta_mob`）也逃不掉。
    """
    patches = []
    try:
        for module_path, attr in TST_ONLY_SYMBOLS:
            dotted = f"{module_path}.{attr}"
            if dotted in RUNTIME_UNARMABLE:
                continue
            mod = importlib.import_module(module_path)
            if not hasattr(mod, attr):
                raise IsolationViolation(
                    f"武装名单里的落点不存在了：{dotted}。"
                    "上游改名/删除后动态闸会悄悄少武装一个——"
                    "要么更新名单，要么把它写进 RUNTIME_UNARMABLE 并说明由谁覆盖。")
            p = mock.patch(dotted, _Sentinel(dotted), create=False)
            p.start()
            patches.append(p)
        yield
    finally:
        while patches:
            patches.pop().stop()


class _Sentinel:
    """一碰就炸的占位对象。调用、读属性、下标、比较全部拒绝。"""

    def __init__(self, label: str) -> None:
        object.__setattr__(self, "_label", label)

    def _boom(self, what: str) -> None:
        raise IsolationViolation(
            f"FST 研究入口碰到了 TST 专属符号 {self._label}（{what}）。"
            "研究路径必须完全绕开悬挂点/胶带走廊/TST 规则（Spec A §4 A1）。")

    def __call__(self, *a, **k):
        self._boom("调用")

    def __getattr__(self, name):
        self._boom(f"读属性 .{name}")

    def __getitem__(self, key):
        self._boom("下标")

    def __bool__(self):
        self._boom("真值判断")

    def __repr__(self):
        return f"<IsolationSentinel {self._label}>"


def self_check() -> tuple[list[str], list[str]]:
    """给人跑的一键自检：(静态违规, 被扫文件)。"""
    return audit_package(), [str(p) for p in _scanned_files()]
