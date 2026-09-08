#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DP-085：FST 09-07/09-08 两批的交叉核查。

这个脚本是 DP-085 结论的唯一依据，改结论前先跑它。三件事：

1. 找出同一（评分员, 试次）被两批各评过一次的重复对，算自身重复性；
2. 判 09-07 那批能不能靠换窗口（后 4 分钟）救回来；
3. 核 `mobile_seconds` 与 `holds` 并集的差（DP-086 那 0.115 s/按键）。

用法：
    python3 tools/probe/fst_repeat_check.py <09-07目录> <09-08目录> [...]
"""
from __future__ import annotations
import glob, json, os, statistics, sys
from collections import defaultdict

LAST_WINDOW_S = 240.0  # FST 国际惯例只算后 4 分钟；本项目窗口口径见 DP-074


def holds_of(rec: dict) -> list[tuple[float, float]]:
    out = []
    for x in rec.get("holds") or []:
        a, b = (x[0], x[1]) if isinstance(x, list) else (x.get("start"), x.get("end"))
        if a is None or b is None:
            continue
        a, b = float(a), float(b)
        if b > a:
            out.append((a, b))
    return out


def union_len(intervals, lo=None, hi=None) -> float:
    """区间并集长度。**必须取并集**——回看重按会产生重叠段，直接相加会重复计数。"""
    iv = []
    for a, b in intervals:
        if lo is not None:
            a = max(a, lo)
        if hi is not None:
            b = min(b, hi)
        if b > a:
            iv.append((a, b))
    iv.sort()
    total = cur_s = cur_e = None
    total = 0.0
    for a, b in iv:
        if cur_s is None:
            cur_s, cur_e = a, b
        elif a <= cur_e:
            cur_e = max(cur_e, b)
        else:
            total += cur_e - cur_s
            cur_s, cur_e = a, b
    if cur_s is not None:
        total += cur_e - cur_s
    return total


def immobility_pct(hs, window_s: float, mode: str) -> float:
    if mode == "full":
        return 100.0 * (1.0 - union_len(hs, 0.0, window_s) / window_s)
    lo = window_s - LAST_WINDOW_S
    return 100.0 * (1.0 - union_len(hs, lo, window_s) / LAST_WINDOW_S)


def load(dirs: list[str]):
    """→ {(scorer, trial): {batch_tag: (holds, window_s)}}, [每按键差值]"""
    data: dict = defaultdict(dict)
    per_press: list[float] = []
    for d in dirs:
        for p in sorted(glob.glob(os.path.join(d, "timer_audit_FST_*.json"))):
            doc = json.load(open(p, encoding="utf-8"))
            tag = (doc.get("exported_at") or "")[:10] or os.path.basename(p)
            for r in doc.get("records", []):
                if r.get("declared_empty") or r.get("unscoreable"):
                    continue
                w = r.get("window_s")
                if not w:
                    continue
                hs = holds_of(r)
                if not hs:
                    continue
                w = float(w)
                u = union_len(hs, 0.0, w)
                per_press.append((float(r["mobile_seconds"]) - u) / len(hs))
                data[(doc["scorer_id"], r["trial_id"])][tag] = (hs, w)
    return data, per_press


def main(dirs: list[str]) -> int:
    data, per_press = load(dirs)
    if not data:
        print("没读到任何 timer_audit_FST_*.json", file=sys.stderr)
        return 1

    print("=== 1. 自身重复性：同一人同一试次被评过两次 ===")
    rows = defaultdict(list)
    for (scorer, trial), byb in sorted(data.items()):
        tags = sorted(byb)
        if len(tags) < 2:
            continue
        a, b = byb[tags[0]], byb[tags[-1]]
        if abs(a[1] - b[1]) > 0.01:
            print("  跳过 %s/%s：两次 window_s 不同（%.2f vs %.2f），不可比" % (scorer, trial, a[1], b[1]))
            continue
        f1, f2 = immobility_pct(*a, "full"), immobility_pct(*b, "full")
        rows[scorer].append(f2 - f1)
        print("  %-22s %-5s %s→%s  不动 %5.1f%% → %5.1f%%  (%+6.1f 点)"
              % (trial, scorer, tags[0], tags[-1], f1, f2, f2 - f1))
    print("\n  %-6s %5s %12s %s" % ("人", "对数", "平均绝对变化", "方向"))
    for scorer, ds in sorted(rows.items(), key=lambda kv: statistics.mean(abs(x) for x in kv[1])):
        same = "全部同向" if len({x > 0 for x in ds}) == 1 else "**方向不一致**"
        print("  %-6s %5d %11.1f 点 %s" % (scorer, len(ds), statistics.mean(abs(x) for x in ds), same))

    print("\n=== 2. 只被评过一次的试次：换后 4 分钟窗口能不能救 ===")
    # 按批次分开报。混在一起报会让分母含义不明——DP-085 引的是 09-07 那批的 11/28。
    once = defaultdict(list)
    for byb in data.values():
        if len(byb) == 1:
            tag, (hs, w) = next(iter(byb.items()))
            once[tag].append((immobility_pct(hs, w, "full"), immobility_pct(hs, w, "last4")))
    print("  %-12s %4s %10s %10s %12s %12s" % ("批次", "n", "中位全程", "中位后4分", "<3%全程", "<3%后4分"))
    for tag in sorted(once):
        rs = once[tag]
        print("  %-12s %4d %9.1f%% %9.1f%% %8d/%-4d %8d/%-4d"
              % (tag, len(rs), statistics.median(r[0] for r in rs), statistics.median(r[1] for r in rs),
                 sum(1 for r in rs if r[0] < 3), len(rs), sum(1 for r in rs if r[1] < 3), len(rs)))
    print("  ⇒ 换窗口救不回来就说明不是窗口问题，是读数本身不可用（DP-085）")

    print("\n=== 3. DP-086：mobile_seconds 与 holds 并集的差 ===")
    print("  n=%d 条记录  (工具−并集)/按键段数：中位 %.4f s  均值 %.4f  标准差 %.4f"
          % (len(per_press), statistics.median(per_press), statistics.mean(per_press), statistics.pstdev(per_press)))
    print("  ⇒ DP-007 当年报的是 0.121 s/按键。SOP v1.4 称其在工具 v1.2 已修，实测 v1.5/v1.6 仍在。")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1:]))

