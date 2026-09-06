#!/usr/bin/env python3
"""DP-054 线索量化：unknown 占比与 immobility / 偏差的关系。

问的是一个很具体的问题：那 25.7% 中位 unknown，是"看不清"还是"不动本身"？
如果 unknown 主要由 immobility 引起（残差≈0 ⇒ rho_hind = 0/0 ⇒ NaN），
那 unknown 与 immobility 应强正相关，且它进分母就是把"判对了"当成"没看见"。
"""
from __future__ import annotations
import csv, glob, math, os

RUNS = "/tmp/dp053_runs"
TRUTH = "data/human_scores/recomputed/human_scores_recomputed_DP-012.csv"


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


truth: dict[str, list[float]] = {}
with open(TRUTH, encoding="utf-8") as fh:
    for r in csv.DictReader(fh):
        if r.get("status") != "accepted":
            continue
        try:
            truth.setdefault(r["trial_id"], []).append(float(r["immobility_s"]))
        except (TypeError, ValueError):
            pass

rows = []
for p in sorted(glob.glob(f"{RUNS}/*.csv")):
    stem = os.path.basename(p)[:-4]
    rr = list(csv.DictReader(open(p, encoding="utf-8")))
    if len(rr) != 1 or not rr[0]["immobility_s"]:
        continue
    r = rr[0]
    wf = int(r["window_frames"])
    imm = float(r["immobility_s"])
    unk = int(r["unknown_frames_window"]) / wf
    fps = float(r["fps"])
    t = truth.get(stem)
    h = sum(t) / len(t) if t else None
    rows.append(dict(stem=stem, imm=imm, unk=unk, wf=wf, fps=fps, h=h,
                     unk_s=int(r["unknown_frames_window"]) / fps,
                     raw=float(r["immobility_raw_s"]),
                     mob=float(r["mobility_s"]), bouts=int(r["mobility_bouts"])))

print(f"参与统计的试次 {len(rows)} 个（有软件数字）\n")

imm = [r["imm"] for r in rows]
unk = [r["unk"] for r in rows]
unk_s = [r["unk_s"] for r in rows]
print("## ① unknown 是不是由 immobility 本身造成的")
print(f"  r(unknown 占比, 软件 immobility) = {pearson(unk, imm):.4f}")
print(f"  r(unknown 秒数, 软件 immobility) = {pearson(unk_s, imm):.4f}")
print(f"  unknown 秒数 / immobility 秒数 —— 中位 "
      f"{sorted(r['unk_s'] / r['imm'] for r in rows)[len(rows)//2]:.3f}")
paired = [r for r in rows if r["h"] is not None]
print(f"  r(unknown 占比, 人工 immobility) = {pearson([r['unk'] for r in paired], [r['h'] for r in paired]):.4f}")

print("\n## ② unknown 与偏差的关系（unknown 高 ⇒ 是否系统性偏高）")
bias = [r["imm"] - r["h"] for r in paired]
print(f"  r(unknown 占比, 偏差) = {pearson([r['unk'] for r in paired], bias):.4f}")
lo = [b for r, b in zip(paired, bias) if r["unk"] < 0.20]
hi = [b for r, b in zip(paired, bias) if r["unk"] >= 0.20]
print(f"  unknown < 20%（n={len(lo)}）平均偏差 = {sum(lo)/len(lo):+.2f} s")
print(f"  unknown ≥ 20%（n={len(hi)}）平均偏差 = {sum(hi)/len(hi):+.2f} s")

print("\n## ③ 软件是否只是整体偏高/偏低（可否靠平移一个常数救）")
print(f"  软件 immobility 均值 = {sum(imm)/len(imm):.2f} s，"
      f"人工均值 = {sum(r['h'] for r in paired)/len(paired):.2f} s")
b_ok = [abs(b) <= 17.7 for b in bias]
print(f"  |偏差| ≤ 17.7 s（G8 门）的试次 = {sum(b_ok)}/{len(bias)}")
res = [b - sum(bias)/len(bias) for b in bias]
print(f"  去掉平均偏差后残差标准差 = "
      f"{math.sqrt(sum(x*x for x in res)/(len(res)-1)):.2f} s "
      f"⇒ 常数平移救不了（G8 是逐试次门不是均值门）")

print("\n## ④ 主口径 vs raw（镜像口径差多少）")
d = [r["raw"] - r["imm"] for r in rows]
print(f"  raw − 主口径：{min(d):+.2f} … {max(d):+.2f} s，均值 {sum(d)/len(d):+.2f} s")
