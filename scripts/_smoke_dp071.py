# -*- coding: utf-8 -*-
"""DP-071 的 B 段冒烟测试：用桩数据把汇总/判定路径走一遍，**不重跑分割**。

同 `_smoke_dp069.py` 的理由：B 的汇总段只在 6 个试次全跑完之后执行一次 ⇒
**最贵却最少被执行的代码路径**。这里用三种桩把三个分支都走到：
全部改善 / 全部没用 / 混合，外加「空转率不达标」那条分支。
"""
import importlib.util
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("dp071", HERE / "dp071_warp_floor.py")
dp071 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dp071)

_STEMS = [t[0] for t in dp071.dp065.TRIALS]


def make(noop, ih, it, mh, mt, dauc):
    """造一组满足指定中位数与 ΔAUC 的桩。AUC 用两个正态样本凑到目标差值。"""
    rng = np.random.default_rng(7)
    imm_h = np.full(400, float(ih)); mob_h = np.full(400, float(mh))
    # 让 AUC 硬 = 0.80；容差 AUC = 0.80 + dauc，用重叠比例控制
    def pair(target):
        n = 400
        k = int(round(np.clip(target, 0.0, 1.0) * n))
        p = np.concatenate([np.full(k, 2.0), np.full(n - k, 0.0)])
        q = np.full(n, 1.0)
        return p, q
    ph, qh = pair(0.80)
    pt, qt = pair(0.80 + dauc)
    return dict(bl=30.0,
                imm=dict(n=400, hard=imm_h, tol=np.full(400, float(it)),
                         noop=noop, trans=0.04),
                mob=dict(n=400, hard=mob_h, tol=np.full(400, float(mt)),
                         noop=0.1, trans=0.6),
                auc_hard=dp071.auc(ph, qh), auc_tol=dp071.auc(pt, qt))


def run(case_name, table, out):
    fake = {s: v for s, v in zip(_STEMS, table)}
    dp071.probe_one = lambda clip, human: fake[clip.stem]
    dp071.dp065.load_human = lambda p: {s: {"a": [], "b": []} for s in _STEMS}
    real_exists = Path.exists
    Path.exists = lambda self: True if self.suffix == ".mp4" else real_exists(self)
    sys.argv = ["dp071", "/nonexistent", "/nonexistent.csv", out]
    try:
        rc = dp071.main()
    finally:
        Path.exists = real_exists
    print("\n=== %s → rc=%d ===" % (case_name, rc))
    if rc != 0:
        raise SystemExit("%s 汇总段没走通" % case_name)
    return Path(out).read_text(encoding="utf-8")


ALL_GOOD = [make(0.92, 4, 0, 24, 10, +0.08) for _ in _STEMS]
ALL_BAD = [make(0.92, 4, 0, 24, 10, -0.05) for _ in _STEMS]
MIXED = [make(0.92, 4, 0, 24, 10, +0.08 if i % 2 else -0.05)
         for i in range(len(_STEMS))]
LOW_NOOP = [make(0.10, 4, 0, 24, 10, +0.08) for _ in _STEMS]

t1 = run("全部改善", ALL_GOOD, "/tmp/dp071_smoke_good.md")
assert "6/6 都改善" in t1, "没走到「全部改善」分支"
t2 = run("全部没用", ALL_BAD, "/tmp/dp071_smoke_bad.md")
assert "都没用" in t2, "没走到「全部没用」分支"
t3 = run("混合", MIXED, "/tmp/dp071_smoke_mixed.md")
assert "各试次不一致" in t3, "没走到「不一致」分支"
t4 = run("空转率不达标", LOW_NOOP, "/tmp/dp071_smoke_lownoop.md")
assert "按预先声明这条不算成立" in t4, "没走到「空转率不达标」分支"

print("\n四个分支（改善/没用/混合/空转率不达标）全部执行通过")
