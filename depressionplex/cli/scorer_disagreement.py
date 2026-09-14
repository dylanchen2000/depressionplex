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


def _both_empty(A: list[Seg], B: list[Seg]) -> bool:
    """两位评分员在这一试次都没按出任何运动段（DP-045）。

    典型来源是**真的空隔间**（`20mg_3周-ch4`，两人独立都给 0）。这不是一致
    也不是分歧：留在分母里会稀释偏差、并把 Jaccard 记成 nan 毒掉整个均值。
    与 `lovo_cv` 的 G11 侧同一条规则，两边不许两套账。
    """
    return total(A) <= 1e-12 and total(B) <= 1e-12


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


def load_rates(path: pathlib.Path) -> dict[str, float | None]:
    """逐试次的 `playback_rate`（DP-046）。缺字段一律 `None`，**不许填默认值**。

    倍速不是备注是受控参数：倍速减半 ⇒ 按键粒度减半（徐 1.55@0.25x / 3.22@0.5x、
    陈璇 2.33@0.5x / 4.85@1.0x，两人独立复现），而**一处倍速错配就把 ICC 由 0.864
    打到 0.344**，与"一个人粗一个人细"同量级。所以配对分析必须按倍速分层，
    合在一起的数**混杂、不得单独引用**。
    """
    d = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, float | None] = {}
    for r in d["records"]:
        v = r.get("playback_rate")
        try:
            out[r["trial_id"]] = None if v is None else float(v)
        except (TypeError, ValueError):
            out[r["trial_id"]] = None      # 记成 None 而不是猜，缺就是缺
    return out


def _rate_ledger(label: str, rates: dict[str, float | None], trials: list[str]) -> None:
    """打这一份文件在本次配对试次上的倍速台账，批内换速要点名（DP-046）。"""
    hist: dict[float | None, int] = {}
    for t in trials:
        v = rates.get(t)
        hist[v] = hist.get(v, 0) + 1
    txt = " ".join(f"{'未记录' if k is None else k}x×{v}" for k, v in
                   sorted(hist.items(), key=lambda kv: (kv[0] is None, kv[0])))
    print(f"  {label}: [{txt}]"
          + ("  ⚠ **批内换过倍速**" if len(hist) > 1 else ""))


def _stratum(label: str, idx: list[int], diffs: list[float], jac: list[float]) -> None:
    if not idx:
        print(f"  {label}: 0 个试次")
        return
    d = [diffs[i] for i in idx]
    j = [jac[i] for i in idx]
    print(f"  {label}: n={len(idx)}  偏差 {sum(d) / len(d):+.2f} s"
          f"  |差| {sum(abs(v) for v in d) / len(d):.2f} s"
          f"  Jaccard {sum(j) / len(j):.3f}")


def _describe(vals, label: str) -> float:
    v = sorted(vals)
    n = len(v)
    m = sum(v) / n
    pos = sum(1 for x in v if x > 0) / n * 100
    print(f"  {label}: n={n} 中位 {v[n // 2]:+.3f}s 均值 {m:+.3f}s  为正 {pos:.0f}%")
    return m


def main(argv: list[str]) -> int:
    _stdio.force_utf8()

    if len(argv) != 3:
        print("用法: python3 -m depressionplex.cli.scorer_disagreement <A.json> <B.json>",
              file=sys.stderr)
        return 2
    pa, pb = pathlib.Path(argv[1]), pathlib.Path(argv[2])
    A, B = load(pa), load(pb)
    RA, RB = load_rates(pa), load_rates(pb)
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
    # DP-045：双方皆空的试次剔除出所有均值的分母。它既不是一致也不是分歧——
    # 按 DP-043，声明为空的隔间根本不该进分析；留在分母里会把偏差稀释、把
    # Jaccard 变成 nan（曾把 G11 基线整个毒成 nan）。分母一律显式报出。
    keep = [i for i, t in enumerate(common) if not _both_empty(A[t], B[t])]
    empty = [common[i] for i in range(len(common)) if i not in set(keep)]
    if empty:
        print(f"\n⚠ 剔除 {len(empty)} 个**双方皆空**试次（DP-045，不记 1.0 不记 0）："
              f"{empty}\n  以下所有均值分母 = {len(keep)}/{len(common)}")
    if not keep:
        print("剔除后没有可评试次，拒绝输出均值（不静默返回 0）", file=sys.stderr)
        return 1
    kd = [diffs[i] for i in keep]
    kj = [jac[i] for i in keep]
    bias = sum(kd) / len(kd)
    # DP-046：先分层再报数。倍速不一致时，合并数是**混杂量**，必须当场贴标签——
    # 不然它会被当成"人工-人工一致性"去定 G8/G11 的门（实测差别大到 ICC 0.864→0.344）。
    matched = [i for i in keep
               if RA.get(common[i]) is not None and RA[common[i]] == RB.get(common[i])]
    mixed = [i for i in keep
             if RA.get(common[i]) is not None and RB.get(common[i]) is not None
             and RA[common[i]] != RB[common[i]]]
    unknown = [i for i in keep if RA.get(common[i]) is None or RB.get(common[i]) is None]
    tag = ("" if not (mixed or unknown) else
           f"  ⚠ **混杂：{len(mixed)} 个倍速错配 + {len(unknown)} 个倍速未记录，"
           "此合并值不得单独引用**（DP-046，分层见下）")
    print(f"\n偏差(A−B) {bias:+.2f} s   |差|均值 {sum(abs(d) for d in kd) / len(kd):.2f} s"
          f"   （n={len(kd)}）{tag}")
    print(f"A独有 {ta - ti:.1f} s   B独有 {tb - ti:.1f} s   "
          f"单向净差占对称差 {abs(ta - tb) / ((ta - ti) + (tb - ti)) * 100:.0f}%")
    print(f"**平均 Jaccard {sum(kj) / len(kj):.3f}**（n={len(kj)}）"
          + ("  ⚠ **混杂值，不得单独引用**（DP-046）" if (mixed or unknown)
             else " ← 时间轴上的真实一致水平，G11 门槛取此值"))
    print(f"总量一致但时间轴不一致的试次（|差|<10 s 且 Jaccard<0.7）: "
          f"{[common[i] for i in keep if abs(diffs[i]) < 10 and jac[i] < 0.7] or '无'}")

    print("\n── 倍速台账与分层（DP-046，倍速是受控参数不是备注）──")
    _rate_ledger(f"A={na}", RA, common)
    _rate_ledger(f"B={nb}", RB, common)
    _stratum("倍速匹配", matched, diffs, jac)
    _stratum("倍速错配", mixed, diffs, jac)
    _stratum("倍速未记录", unknown, diffs, jac)
    if mixed or unknown:
        print("  ⇒ 定门槛（G8/G11）只许用**倍速匹配**那一层；混杂层留作参考值记账。"
              "SOP v1.3 起全项目锁 0.5x、同一对两人必须同速，此分层将退化为单层")

    print("\n── 单向分歧是几大段还是一堆碎片 ──")
    for lab, X, Y in ((f"A独有({nb}说静止)", A, B), (f"B独有({na}说静止)", B, A)):
        segs = sorted(e - s for t in common for s, e in subtract(X[t], Y[t]))
        n, tot = len(segs), sum(segs)
        if not segs:      # 一侧完全被另一侧包含：如实说"没有单侧片段"，不许崩
            print(f"  {lab}: 0 段（该侧完全被另一侧包含，无单向分歧）")
            continue
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
