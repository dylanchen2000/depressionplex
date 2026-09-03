"""定位两位评分员之间的分歧：在时间轴上，不在总时长上。

产出 `docs/分析_评分员分歧定位_2026-09-03.md` 里的全部数字，可复现。

为什么需要这个工具：r 与 ICC 只看两个标量的协变，对"哪些秒在动"完全不敏感。
实测有试次总时长只差 0.9 s 而逐秒 Jaccard 仅 0.51 ——总量一致掩盖了判断不一致。

一律用 union(sorted(holds))，绝不读 mobile_seconds（工具每按键多算约 0.121 s 墙钟）。
"""
from __future__ import annotations

import json
import math
import pathlib
import sys

Seg = tuple[float, float]


def normalize(holds) -> list[Seg]:
    """排序 + 取并集。43 个试次里 12 个 holds 数组乱序自嵌套，不排序会虚高最多 +30%。"""
    pairs = []
    for h in holds or []:
        a, b = (h[0], h[1]) if isinstance(h, (list, tuple)) else (h["start"], h["end"])
        pairs.append((min(a, b), max(a, b)))
    out: list[Seg] = []
    for s, e in sorted(pairs):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def total(segs: list[Seg]) -> float:
    return sum(e - s for s, e in segs)


def overlap(a: Seg, b: Seg) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def intersect_total(A: list[Seg], B: list[Seg]) -> float:
    i = j = 0
    tot = 0.0
    while i < len(A) and j < len(B):
        tot += overlap(A[i], B[j])
        if A[i][1] < B[j][1]:
            i += 1
        else:
            j += 1
    return tot


def subtract(A: list[Seg], B: list[Seg]) -> list[Seg]:
    """A \\ B，用于看单向分歧是几大段还是一堆碎片。"""
    out: list[Seg] = []
    for s, e in A:
        cur = [(s, e)]
        for bs, be in B:
            nxt = []
            for cs, ce in cur:
                if be <= cs or bs >= ce:
                    nxt.append((cs, ce))
                    continue
                if bs > cs:
                    nxt.append((cs, bs))
                if be < ce:
                    nxt.append((be, ce))
            cur = nxt
        out += cur
    return [(s, e) for s, e in out if e - s > 1e-9]


def merge_gap(segs: list[Seg], gap: float) -> list[Seg]:
    """声明式合并：间隙 ≤ gap 的相邻段并成一段。同一个 gap 必须同时施加到人工与软件。"""
    out: list[Seg] = []
    for s, e in segs:
        if out and s - out[-1][1] <= gap:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def pearson(x, y) -> float:
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sx = math.sqrt(sum((a - mx) ** 2 for a in x))
    sy = math.sqrt(sum((b - my) ** 2 for b in y))
    if sx == 0 or sy == 0:
        return float("nan")
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sx * sy)


def mutual_best_edges(W: list[Seg], C: list[Seg]) -> list[tuple[float, float]]:
    """互为最佳重叠的段对 → (起始差, 结束差)。

    一对多匹配会把同一个长段的偏移重复计入每个短段（实测虚高到 834%），必须互为最佳。
    起始差 > 0 = W 更早按下；结束差 > 0 = W 更晚松手。
    """
    bw = {i: max(range(len(C)), key=lambda j: overlap(W[i], C[j]))
          for i in range(len(W)) if any(overlap(W[i], c) > 0 for c in C)}
    bc = {j: max(range(len(W)), key=lambda i: overlap(W[i], C[j]))
          for j in range(len(C)) if any(overlap(w, C[j]) > 0 for w in W)}
    return [(C[j][0] - W[i][0], W[i][1] - C[j][1]) for i, j in bw.items() if bc.get(j) == i]


def load(path: pathlib.Path) -> dict[str, list[Seg]]:
    d = json.loads(path.read_text(encoding="utf-8"))
    return {r["trial_id"]: normalize(r.get("holds")) for r in d["records"]}


def _describe(vals, label: str) -> float:
    v = sorted(vals)
    n = len(v)
    m = sum(v) / n
    pos = sum(1 for x in v if x > 0) / n * 100
    print(f"  {label}: n={n} 中位 {v[n // 2]:+.3f}s 均值 {m:+.3f}s  为正 {pos:.0f}%")
    return m


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("用法: python3 -m depressionplex.cli.scorer_disagreement <A.json> <B.json>",
              file=sys.stderr)
        return 2
    pa, pb = pathlib.Path(argv[1]), pathlib.Path(argv[2])
    A, B = load(pa), load(pb)
    common = sorted(set(A) & set(B))
    if not common:
        print("两份文件没有共同试次，无法比对（不静默返回 0）", file=sys.stderr)
        return 1
    na, nb = pa.stem, pb.stem
    print(f"配对试次 n={len(common)}   A={na}   B={nb}\n")

    print("── 逐试次：总量 vs 时间轴 ──")
    print(f"{'试次':34s} {'A':>7s} {'B':>7s} {'差':>7s} {'交集':>7s} {'A独有':>7s} {'B独有':>7s} {'Jac':>5s}")
    ta = tb = ti = 0.0
    jac, diffs, means = [], [], []
    for t in common:
        a, b = total(A[t]), total(B[t])
        x = intersect_total(A[t], B[t])
        j = x / (a + b - x) if (a + b - x) > 0 else float("nan")
        ta, tb, ti = ta + a, tb + b, ti + x
        jac.append(j)
        diffs.append(a - b)
        means.append((a + b) / 2)
        print(f"{t[:34]:34s} {a:7.1f} {b:7.1f} {a - b:+7.1f} {x:7.1f} "
              f"{a - x:7.1f} {b - x:7.1f} {j:5.2f}")
    bias = sum(diffs) / len(diffs)
    print(f"\n偏差(A−B) {bias:+.2f} s   |差|均值 {sum(abs(d) for d in diffs) / len(diffs):.2f} s")
    print(f"A独有 {ta - ti:.1f} s   B独有 {tb - ti:.1f} s   "
          f"单向净差占对称差 {abs(ta - tb) / ((ta - ti) + (tb - ti)) * 100:.0f}%")
    print(f"**平均 Jaccard {sum(jac) / len(jac):.3f}** ← 时间轴上的真实一致水平，G11 门槛取此值")
    print(f"总量一致但时间轴不一致的试次（|差|<10 s 且 Jaccard<0.7）: "
          f"{[t for t, d, j in zip(common, diffs, jac) if abs(d) < 10 and j < 0.7] or '无'}")

    print("\n── 单向分歧是几大段还是一堆碎片 ──")
    for lab, X, Y in ((f"A独有({nb}说静止)", A, B), (f"B独有({na}说静止)", B, A)):
        segs = sorted(e - s for t in common for s, e in subtract(X[t], Y[t]))
        n, tot = len(segs), sum(segs)
        print(f"  {lab}: {n} 段 合计 {tot:.1f} s 中位 {segs[n // 2]:.2f} s 最大 {segs[-1]:.2f} s")

    print("\n── 边界：按下 vs 松手（互为最佳匹配）──")
    edges = [e for t in common for e in mutual_best_edges(A[t], B[t])]
    _describe([o for o, _ in edges], "起始差 (A 更早按下为正)")
    _describe([f for _, f in edges], "结束差 (A 更晚松手为正)")

    print("\n── 统一合并间隙 g 能否消掉偏差 ──")
    print(f"{'g(s)':>6s} {'偏差':>9s} {'|差|':>8s} {'r':>7s}")
    for g in (0, 0.5, 1.0, 2.0, 5.0):
        xs = [total(merge_gap(A[t], g)) for t in common]
        ys = [total(merge_gap(B[t], g)) for t in common]
        d = [p - q for p, q in zip(xs, ys)]
        print(f"{g:6.2f} {sum(d) / len(d):+9.2f} "
              f"{sum(abs(v) for v in d) / len(d):8.2f} {pearson(xs, ys):+7.3f}")

    print("\n── 分歧 vs 活跃度（倒 U 形检验）──")
    order = sorted(range(len(common)), key=lambda k: means[k])
    ranks = [k + 1 for k, i in enumerate(order) if abs(diffs[i]) >= 20]
    print(f"  |差| vs 活跃度 r={pearson(means, [abs(d) for d in diffs]):+.3f}；"
          f"大分歧(|差|≥20s)按活跃度排序后的名次 {ranks}")
    print(f"  连续区间 ⇒ 倒 U 形；随机排列下出现连续区间的概率很低（见分析文档 §3）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
