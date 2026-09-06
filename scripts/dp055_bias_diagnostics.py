#!/usr/bin/env python3
"""均值几乎重合(+1.74 s)、r 只有 0.764、残差 SD 41.3 s ⇒ 不是偏移问题。
那是什么问题？回归斜率能直接答：软件把差异放大了还是压缩了。"""
from __future__ import annotations
import csv, glob, math, os

truth: dict[str, list[float]] = {}
with open("data/human_scores/recomputed/human_scores_recomputed_DP-012.csv",
          encoding="utf-8") as fh:
    for r in csv.DictReader(fh):
        if r.get("status") == "accepted":
            try:
                truth.setdefault(r["trial_id"], []).append(float(r["immobility_s"]))
            except (TypeError, ValueError):
                pass

xs, ys, names = [], [], []
for p in sorted(glob.glob("/tmp/dp053_runs/*.csv")):
    stem = os.path.basename(p)[:-4]
    rr = list(csv.DictReader(open(p, encoding="utf-8")))
    if len(rr) != 1 or not rr[0]["immobility_s"] or stem not in truth:
        continue
    t = truth[stem]
    xs.append(sum(t) / len(t))            # 人工
    ys.append(float(rr[0]["immobility_s"]))  # 软件
    names.append(stem)

n = len(xs)
mx, my = sum(xs) / n, sum(ys) / n
sdx = math.sqrt(sum((x - mx) ** 2 for x in xs) / (n - 1))
sdy = math.sqrt(sum((y - my) ** 2 for y in ys) / (n - 1))
cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (n - 1)
slope = cov / sdx ** 2
print(f"n = {n}")
print(f"人工 SD = {sdx:.2f} s   软件 SD = {sdy:.2f} s   SD 比 = {sdy/sdx:.3f}")
print(f"回归 软件 = {slope:.3f} × 人工 + {my - slope*mx:+.1f}")
print(f"r = {cov/(sdx*sdy):.4f}")
print()
print("同一只动物两个人工评分员之间的离散（作为噪声下界）：")
pair = [t for t in truth.values() if len(t) >= 2]
sp = [max(t) - min(t) for t in pair]
print(f"  n={len(pair)} 对，极差中位 {sorted(sp)[len(sp)//2]:.2f} s，最大 {max(sp):.2f} s")
print()
print("偏差最大的 5 个试次（要人工回看的优先级）：")
d = sorted(zip(names, xs, ys), key=lambda t: -abs(t[2] - t[1]))[:5]
for nm, h, s in d:
    print(f"  {nm}: 人工 {h:.1f} 软件 {s:.1f} 偏 {s-h:+.1f}")
