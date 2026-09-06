#!/usr/bin/env python3
"""把 DP-053 批量实测结果与人工真值对齐，出逐试次对比表。

**这不是验收报告。** 正式 G7/G8 走 `lovo_cv.py` 的 LOVO 流程（含 θ 拟合折、
G1–G11 捆绑判定），在 DP-014。这里只是驱动层首次在真素材上跑通后的实测记账：
θ_mob 冻结在 0.0175，一个参数都没拟合，所以这是一次干净的\"照原样跑\"对比。
"""
from __future__ import annotations

import csv
import glob
import math
import os

RUNS = "/tmp/dp053_runs"
TRUTH = "data/human_scores/recomputed/human_scores_recomputed_DP-012.csv"


def load_truth() -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    with open(TRUTH, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r.get("status") != "accepted":
                continue
            try:
                v = float(r["immobility_s"])
            except (TypeError, ValueError):
                continue
            out.setdefault(r["trial_id"], {})[r["scorer_id"]] = v
    return out


def load_soft() -> tuple[dict[str, dict], dict[str, str]]:
    soft: dict[str, dict] = {}
    bad: dict[str, str] = {}
    for p in sorted(glob.glob(f"{RUNS}/*.csv")):
        stem = os.path.basename(p)[:-4]
        rows = list(csv.DictReader(open(p, encoding="utf-8")))
        if not rows:
            bad[stem] = "CSV 无数据行（该隔间未产出数字）"
            continue
        if len(rows) != 1:
            bad[stem] = f"{len(rows)} 行（单隔间切片应只有 1 行）"
            continue
        soft[stem] = rows[0]
    for p in sorted(glob.glob(f"{RUNS}/*.log")):
        stem = os.path.basename(p)[:-4]
        if stem in soft:
            continue
        txt = open(p, encoding="utf-8", errors="replace").read()
        last = [l for l in txt.strip().splitlines() if l.strip()]
        bad.setdefault(stem, last[-1][:160] if last else "日志为空（可能仍在跑）")
    return soft, bad


def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def main() -> int:
    truth = load_truth()
    soft, bad = load_soft()
    print(f"素材 {len(soft) + len(bad)} 段：出数字 {len(soft)}，未出 {len(bad)}")
    print(f"人工真值试次 {len(truth)} 个\n")

    rows = []
    for stem, r in sorted(soft.items()):
        t = truth.get(stem)
        imm = r["immobility_s"]
        s_imm = float(imm) if imm not in ("", None) else None
        wf = int(r["window_frames"]) or 1
        unk = 100.0 * int(r["unknown_frames_window"]) / wf
        h = (sum(t.values()) / len(t)) if t else None
        rows.append((stem, s_imm, h, len(t) if t else 0, unk,
                     r["validity_status"], r["scored"], r["mobility_bouts"]))

    print("| 试次 | 软件 immob (s) | 人工均值 (s) | 评分员 | 偏差 (s) | "
          "窗口内 unknown | 有效性 | Mobility bouts |")
    print("|---|---|---|---|---|---|---|---|")
    for stem, s, h, k, unk, st, sc, mb in rows:
        d = "—" if (s is None or h is None) else f"{s - h:+.2f}"
        print(f"| {stem} | {'—' if s is None else f'{s:.2f}'} | "
              f"{'—' if h is None else f'{h:.2f}'} | {k or '—'} | {d} | "
              f"{unk:.1f}% | {st} | {mb or '—'} |")

    pairs = [(s, h) for _, s, h, k, *_ in rows if s is not None and h is not None and k]
    if pairs:
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        bias = [x - y for x, y in pairs]
        mb = sum(bias) / len(bias)
        r = pearson(xs, ys)
        print(f"\n配对试次 n={len(pairs)}")
        print(f"  平均偏差（软件 − 人工）= {mb:+.2f} s")
        print(f"  平均绝对偏差            = {sum(abs(b) for b in bias) / len(bias):.2f} s")
        print(f"  偏差范围                = {min(bias):+.2f} … {max(bias):+.2f} s")
        print(f"  Pearson r               = {'不可算' if r is None else f'{r:.4f}'}")
        print(f"  窗口内 unknown 占比中位 = "
              f"{sorted(u for _, _, _, _, u, _, _, _ in rows)[len(rows) // 2]:.1f}%")
        print("\n**口径声明**：以上不是 G7/G8 验收数字。正式判定走 lovo_cv 的 LOVO "
              "流程 + G1–G11 捆绑（DP-014）。此处 θ_mob 冻结 0.0175、零参数拟合、"
              "单隔间切片窗口（DP-052 未修）、人工侧为混杂档（T0/T2，见 DP-048）。")

    if bad:
        print(f"\n未出数字的 {len(bad)} 段（原因必须逐条列出，不许静默少行）：")
        for stem, why in sorted(bad.items()):
            print(f"  - {stem}：{why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
