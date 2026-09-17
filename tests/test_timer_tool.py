# -*- coding: utf-8 -*-
"""把秒表工具（单文件 HTML）的自测接进 `run_tests.py`。

为什么要接进来：秒表工具是**采集端**——它写错一个列、卡住一个试次，代价是
评分员重评一批视频，比任何 .py 的 bug 都贵。而它此前完全没有自动测试，
v1.5 加 FST 时正好补上。

为什么用 node 而不是重写成 Python：被测对象是 HTML 里的那段 JS 本身。
用 Python 复写一遍解析/导出逻辑，测的就是复写件而不是评分员真正在用的代码。
node 测试把 <script> 原样跑起来，不复制一行被测逻辑。

node 缺失时**红**而不是跳过：本仓两个工作环境（Mac 与沙箱）都有 node，
静默跳过等于"不报当过"，违反 G1–G11 的同一条纪律。

DP-132 §1 补的是「连条数都不数」这一环。原来这条代理只断言 rc == 0 与
「全部通过」四个字：node 自测整体退化成 3 条、仍然印「全部通过」，代理一样是绿的
（DP-131 的变异 M43 量过同一件事——CI 三个 job 对这类错一律是绿的）。现在三段：
① rc == 0 且**末行**是「全部通过」（人工验收脚本场景 9 认的就是末行）；
② 自洽：印出来的 pass 行数 == 自测自己汇总的那个数；
③ 下限：pass 行数 >= NODE_SELFTEST_MIN。
②③缺一不可——只有自洽挡不住整体退化（3 条也自洽），只有下限会随时间发霉
（加了检查不调下限，下限就成了摆设）。
"""
from __future__ import annotations

import ast
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "tools" / "timer" / "test_timer_assay.js"
HTML = ROOT / "tools" / "timer" / "DepressionPlex_stopwatch_timer_v1.html"
MANIFEST = ROOT / "data" / "human_scores" / "manifests" / "FST全程_视频清单_给评分员.csv"

# node 自测的 pass 行数下限。**这个数字全仓只许写在这一处**（守卫
# test_node_selftest_count_has_exactly_one_source 盯着：别处再写一个条数就红）。
# 加检查时一并上调；下调等于把「自测整体退化」那道口子重新放开。
NODE_SELFTEST_MIN = 90

PASS_LINE_PREFIX = "  pass  "          # node runner 印一条通过的固定前缀
SUMMARY_RE = re.compile(r"^自测汇总：通过 (\d+) 条，不通过 (\d+) 条$", re.M)
# 「第二处条数」的形状：数字必须紧贴着「条」并且紧挨 node/自测/pass 这类名词，
# 或者是「条数」「通过 …… 条」后面直接跟数字。只写「退化成 3 条」「§1 第 2 条」
# 这种与自测条数无关的行不算——否则这条守卫连自己的说明书都写不出来。
COUNT_CLAIM = re.compile(
    r"(?:node|自测|自检)\s*(?:那|的)?\s*\d+\s*条"        # 「node 那 <数> 条」
    r"|\d+\s*条\s*(?:node|自测|自检|pass)"               # 「<数> 条 pass」
    r"|(?:node|自测|自检)的?\s*条数\D{0,4}\d+"           # 「自测条数 <数>」
    r"|通过\s*\d+\s*条")                                 # 「通过 <数> 条」


def test_stopwatch_timer_js_selftest() -> None:
    """跑 node 自测（真代码，不复写一行被测逻辑），并且**数条数**。

    条数为什么由这条代理来数：node 那一批自测是「谎报第一次」「不勾重评却把已评
    场次重新派一遍」等关卡的行为判据，而它们在套件里只有这一条代跑入口；代理不数
    条数，就等于自测少了多少条没人知道。
    """
    node = shutil.which("node")
    assert node is not None, (
        "找不到 node，秒表工具的自测跑不了。这里不跳过——采集端没测过就不许当过。"
        "装法：brew install node")
    for p in (JS, HTML, MANIFEST):
        assert p.exists(), "缺文件：%s" % p
    r = subprocess.run([node, str(JS), str(HTML), str(MANIFEST)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, "秒表工具自测未通过：\n" + r.stdout + r.stderr
    lines = r.stdout.splitlines()
    assert lines and lines[-1] == "全部通过", (
        "自测输出的**末行**不是「全部通过」（实得末行 %r）。汇总行要印在末行之前——"
        "人工验收脚本场景 9 认的是末行：\n%s"
        % (lines[-1] if lines else "", r.stdout + r.stderr))

    n_pass = sum(1 for ln in lines if ln.startswith(PASS_LINE_PREFIX))
    m = SUMMARY_RE.search(r.stdout)
    assert m, (
        "自测没印汇总行「自测汇总：通过 N 条，不通过 M 条」——少了它，条数就又只有"
        "人眼在数（DP-132 §1 第 1 条要的就是这个数）：\n" + r.stdout)
    n_sum_pass, n_sum_fail = int(m.group(1)), int(m.group(2))
    assert n_sum_fail == 0, "自测自己汇总的不通过条数不是 0：%d 条" % n_sum_fail
    assert n_pass == n_sum_pass, (
        "自洽断了：印出来 %d 行 pass，自测自己汇总 %d 条——两边说的不是同一件事，"
        "汇总数被写死了或漏计数了（DP-132 §1 第 1 条）" % (n_pass, n_sum_pass))
    assert n_pass >= NODE_SELFTEST_MIN, (
        "node 自测退化了：只有 %d 行 pass，下限是 %d（NODE_SELFTEST_MIN）。"
        "少了哪几条要一条条说清；真是加了检查就把下限一并上调，不许下调"
        "（DP-132 §1 第 2 条）" % (n_pass, NODE_SELFTEST_MIN))


def _docstring_of(src: str, fname: str) -> str:
    """按名字取一个测试函数的 docstring（ast 解析，不靠行号，也不靠正则）。"""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == fname:
            doc = ast.get_docstring(node)
            assert doc, "%s 没有 docstring" % fname
            return doc
    raise AssertionError("找不到函数 %s——它被改名/挪走了" % fname)


def test_node_selftest_count_has_exactly_one_source() -> None:
    """DP-132 §1 第 3 条：node 自测的条数，全仓只许有**一处**写着。

    由来：DP-131 §6-(13) 上交的就是这件事——契约模块 docstring 里那句「node 那
    〔若干〕条」是当时（5a280cc）的真值，node 后来加了几条，它就落后了一位，而 CI
    对它是绿的（纯注释，不改行为、不放宽任何关）。裁决是：不许只把那个数字换成新的
    （下次加检查还会再落后一位），要让这个数字只有一个来源并被钉住。所以这里钉的
    不是「数字对不对」，是「有没有第二处数字」。本函数的说明书里也就刻意不把那个
    数字写出来——写出来这一行自己就成了第二来源。

    「只有一处」的可操作口径：裸数字在别的常量里也会合法出现（超时、行宽、阈值），
    扫裸数字没有意义。所以扫的是**条数判据的形状**（见 COUNT_CLAIM）：数字紧贴
    「条」并且紧挨 node/自测/pass 这类名词，或「条数」「通过 …… 条」后面直接跟数字。
    抓不到所有可能的措辞（换成「项」「个」就漏），这一点如实写明；要的是「顺手把
    数字抄一遍」这个最常见的形状必红。

    范围是活判据文件（run_tests.py、tests/*.py、tools/timer/*.js 与 *.html）；
    docs/ 不扫，因为派工单与人工验收脚本是存档，里面的数字是当时的真值（B13 场景 9
    至今写着 DP-128 那时的那三个数），改它们等于改历史。
    """
    live = [ROOT / "run_tests.py"]
    live += sorted((ROOT / "tests").glob("*.py"))
    live += sorted((ROOT / "tools" / "timer").glob("*.js"))
    live += sorted((ROOT / "tools" / "timer").glob("*.html"))
    for f in live:
        assert f.is_file(), "活判据文件缺了：%s（扫描范围自己塌了，这条守卫就成了空的）" % f
    assert len(live) >= 30, "只扫到 %d 个活判据文件——扫描范围塌了，这条守卫等于没跑" % len(live)

    offenders = []
    for f in live:
        for ln, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if COUNT_CLAIM.search(line):
                offenders.append("%s:%d  %s" % (f.relative_to(ROOT), ln, line.strip()))
    assert not offenders, (
        "node 自测的条数在别处又写了一遍（每行都是一处第二来源）：\n  %s\n"
        "条数只许 NODE_SELFTEST_MIN 一处写；要引用就引用它的名字，不要抄数字"
        % "\n  ".join(offenders))

    defs = []
    for f in live:
        for ln, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if line.startswith("NODE_SELFTEST_MIN"):
                defs.append("%s:%d" % (f.relative_to(ROOT), ln))
    assert len(defs) == 1, (
        "NODE_SELFTEST_MIN 出现在 %d 个地方（%s）——两处定义就是两个来源，"
        "迟早各说各话" % (len(defs), defs))

    contract = (ROOT / "tests" / "test_timer_tool_contract.py").read_text(encoding="utf-8")
    doc = _docstring_of(contract, "test_dp131_source_level_pins_for_session_shape_gates")
    assert len(doc) > 200, (
        "取到的 docstring 只有 %d 字符——切出空串/半句的假绿形状，先修取法再谈判据" % len(doc))
    assert "NODE_SELFTEST_MIN" in doc, (
        "契约模块那句「看守链太长」的 docstring 不再引用 NODE_SELFTEST_MIN——"
        "引用一断，下一个人就会顺手把数字写回去（DP-131 §6-(13)）")
    assert not COUNT_CLAIM.search(doc), (
        "契约模块的 docstring 又自带条数了（%s）——那就是第二个来源。"
        "数字只许 NODE_SELFTEST_MIN 一处写，docstring 只引用名字"
        % COUNT_CLAIM.findall(doc))


def test_tst_manifest_still_has_no_assay_column() -> None:
    """向后兼容的护栏：老的 TST 清单**不带** assay 列，工具必须仍当它是 TST。

    这条钉的是「不要顺手给旧清单补列」——评分员手里已经下载了那份 CSV，
    改了它就会和手上的不一致。
    """
    tst = ROOT / "data" / "human_scores" / "manifests" / "T1精标_视频清单_给评分员.csv"
    assert tst.exists(), "缺 TST 清单：%s" % tst
    head = tst.read_text(encoding="utf-8-sig").splitlines()[0].strip()
    assert head == "trial_id,video_filename", "TST 清单列头被改动了：" + head
