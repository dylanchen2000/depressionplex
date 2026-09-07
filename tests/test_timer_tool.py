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
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "tools" / "timer" / "test_timer_assay.js"
HTML = ROOT / "tools" / "timer" / "DepressionPlex_stopwatch_timer_v1.html"
MANIFEST = ROOT / "data" / "human_scores" / "manifests" / "FST全程_视频清单_给评分员.csv"


def test_stopwatch_timer_js_selftest() -> None:
    node = shutil.which("node")
    assert node is not None, (
        "找不到 node，秒表工具的自测跑不了。这里不跳过——采集端没测过就不许当过。"
        "装法：brew install node")
    for p in (JS, HTML, MANIFEST):
        assert p.exists(), "缺文件：%s" % p
    r = subprocess.run([node, str(JS), str(HTML), str(MANIFEST)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, "秒表工具自测未通过：\n" + r.stdout + r.stderr
    assert "全部通过" in r.stdout, "自测输出异常：\n" + r.stdout + r.stderr


def test_tst_manifest_still_has_no_assay_column() -> None:
    """向后兼容的护栏：老的 TST 清单**不带** assay 列，工具必须仍当它是 TST。

    这条钉的是「不要顺手给旧清单补列」——评分员手里已经下载了那份 CSV，
    改了它就会和手上的不一致。
    """
    tst = ROOT / "data" / "human_scores" / "manifests" / "T1精标_视频清单_给评分员.csv"
    assert tst.exists(), "缺 TST 清单：%s" % tst
    head = tst.read_text(encoding="utf-8-sig").splitlines()[0].strip()
    assert head == "trial_id,video_filename", "TST 清单列头被改动了：" + head
