# -*- coding: utf-8 -*-
"""DP-073 的冒烟测试：把**真数据永远走不到**的两条分支走一遍。

正常路径每次跑都会执行（本条是纯算术，秒级）⇒ 不用测。要测的是两条
「出事才会进」的分支，它们一旦崩掉，症状是「探针看起来跑完了但结论是错的」：

1. **标定硬门失败** ⇒ 必须 `rc=1` 且**不出**任何结论。
   真数据上三次全指对 ⇒ 这条分支从来没被执行过 ⇒ 正是 DP-069/071 那个教训里的
   「最少被执行的路径」。这里把 `exponent_table` 换成一个永远选 BL^0 的桩来触发。
2. **冻结表里缺试次** ⇒ 必须 `rc=1`，不能拿部分试次出结论。

外加一条：正常路径 `rc=0` 且**关键读数出现在输出里**（防止哪天格式化崩了却静默）。
"""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("dp073", HERE / "dp073_bl_normalization.py")
dp073 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dp073)

_MD, _CSV = sys.argv[1], sys.argv[2]


def run(name, out):
    sys.argv = ["dp073", _MD, _CSV, out]
    rc = dp073.main()
    print("\n=== %s → rc=%d ===" % (name, rc))
    return rc, Path(out).read_text(encoding="utf-8")


# ---- 正常路径 ----
rc, t = run("正常路径", "/tmp/dp073_smoke_ok.md")
assert rc == 0, "正常路径没走通"
assert "三次全指对" in t, "标定通过的措辞不见了"
assert "单调地越归一化越差" in t, "正题的结论行不见了"
assert "不报秒数" in t, "「这条不是什么」不见了"

# ---- 分支一：标定硬门失败 ----
_real = dp073.exponent_table
dp073.exponent_table = lambda bl, raw: [(0, 1.0, 1.0, 0.0, 0.0),
                                        (1, 9.0, 9.0, 0.0, 0.0),
                                        (2, 9.0, 9.0, 0.0, 0.0)]
try:
    rc, t = run("标定硬门失败", "/tmp/dp073_smoke_calfail.md")
finally:
    dp073.exponent_table = _real
assert rc == 1, "标定没过却没有 rc=1"
assert "标定没过 ⇒ 不出结论" in t, "没走到「标定没过」分支"
assert "单调地越归一化越差" not in t, "标定没过却仍然出了结论 —— 这是最要紧的一条"

# ---- 分支二：冻结表缺试次 ----
_realf = dp073.parse_frozen
dp073.parse_frozen = lambda p: {}
try:
    rc, t = run("冻结表缺试次", "/tmp/dp073_smoke_missing.md")
finally:
    dp073.parse_frozen = _realf
assert rc == 1, "缺试次却没有 rc=1"
assert "不出结论" in t, "没走到「缺试次」分支"
assert "单调地越归一化越差" not in t, "缺试次却仍然出了结论"

print("\n三条分支（正常 / 标定失败 / 缺试次）全部执行通过")
