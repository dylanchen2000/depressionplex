#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DP-059：把 DP-053 全量跑的 27 份结果冻成仓库里的快照，并生成软件↔人工对比表。

**为什么**：DP-053 跑完全量之后，27 个试次的软件数字**只存在于 `/tmp/dp053_runs/`**。
`/tmp` 是易失目录，机器一重启就没了；而 DP-055 里所有的偏差数字（r=0.7641、
平均绝对偏差 35.15 s、达标 5/26）都是从这批数据算出来的——**它们的原始出处随时会消失**。
先冻住，再谈别的。

**顺带解决 UI 支线的输入问题**：只读结果查看器需要一份稳定、有表头、能 join 的表。
**口径计算全部在这里做完**，UI 只负责画——UI 不许自己平均、自己算偏差、自己补空值。

**自校验（本脚本的重点）**：聚合口径必须**逐个复现** DP-055 报过的数
（平均偏差 +1.74 s、平均绝对偏差 35.15 s、达标 5/26、r=0.7641）。
复现不了就说明我这里用了另一套口径 ⇒ 报错退出，不许写出一份和已发布数字打架的表。

用法：python3 scripts/dp059_freeze_run_snapshot.py <dp053_runs 目录> <输出目录>
"""
from __future__ import annotations

import csv
import re
import statistics
import sys
from pathlib import Path

#: G8 验收门（DP-047 定，单位秒）。这里只用来填 `g8_pass` 列，**不改它**。
G8_ABS_BIAS_S = 17.7

#: DP-055 报过的数，本脚本必须复现。容差留 0.02，因为发布时是四舍五入到两位的。
EXPECT = {"mean_bias": 1.74, "mean_abs_bias": 35.15, "n_pass": 5, "n": 26,
          "pearson_r": 0.7641}
TOL = 0.02

#: 单隔间切片跑分析时，隔间索引恒为 1 ⇒ trial_id 会多挂一个 `-ch1`
#: （`10mg_2周-ch3` → `10mg_2周-ch3-ch1`）。只在「剥掉之后仍以 -chN 结尾」时剥，
#: 免得把真正的隔间号剥掉。
_DOUBLE_CH = re.compile(r"^(?P<base>.+-ch\d+)-ch1$")


def normalize_trial_id(raw: str) -> str:
    m = _DOUBLE_CH.match(raw)
    return m.group("base") if m else raw


def read_software(runs: Path) -> list[dict]:
    rows = []
    for f in sorted(runs.glob("*.csv")):
        with f.open(encoding="utf-8") as fh:
            got = list(csv.DictReader(fh))
        if len(got) != 1:
            raise SystemExit("%s 有 %d 个数据行，预期 1" % (f.name, len(got)))
        r = got[0]
        r["trial_id_raw"] = r["trial_id"]
        r["trial_id"] = normalize_trial_id(r["trial_id"])
        r["_source_csv"] = f.name
        rows.append(r)
    ids = [r["trial_id"] for r in rows]
    if len(set(ids)) != len(ids):
        dup = [i for i in set(ids) if ids.count(i) > 1]
        raise SystemExit("归一化后 trial_id 撞了：%s" % dup)
    return rows


def read_human(path: Path) -> dict[str, list[dict]]:
    """按 trial 归拢人工评分。**只收 `status == accepted`。**

    被拒的行留在原文件里作为痕迹，但不参与聚合——那是 DP-012 已经裁过的口径，
    这里不重裁。
    """
    by_trial: dict[str, list[dict]] = {}
    with path.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r.get("status") != "accepted":
                continue
            if not r.get("immobility_s"):
                continue
            by_trial.setdefault(r["trial_id"], []).append(r)
    return by_trial


def pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    dx = sum((a - mx) ** 2 for a in xs) ** 0.5
    dy = sum((b - my) ** 2 for b in ys) ** 0.5
    return num / (dx * dy)


OUT_FIELDS = [
    "trial_id", "assay", "validity_status", "scored",
    "software_immobility_s", "software_immobility_raw_s",
    "software_mobility_s", "software_mobility_bouts",
    "unknown_frames_window", "unknown_fraction_window",
    "n_human_scorers", "human_immobility_mean_s",
    "human_immobility_min_s", "human_immobility_max_s", "human_range_s",
    "human_scorers", "human_playback_rates",
    "bias_s", "abs_bias_s", "g8_pass", "note",
]


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    runs, outdir = Path(sys.argv[1]), Path(sys.argv[2])
    repo = Path(__file__).resolve().parent.parent
    human = read_human(repo / "data/human_scores/recomputed"
                              "/human_scores_recomputed_DP-012.csv")
    soft = read_software(runs)
    print("软件 %d 个试次；人工覆盖 %d 个试次" % (len(soft), len(human)))

    out = []
    for r in soft:
        tid = r["trial_id"]
        hs = human.get(tid, [])
        vals = [float(h["immobility_s"]) for h in hs]
        row = {
            "trial_id": tid,
            "assay": r["assay"],
            "validity_status": r["validity_status"],
            "scored": r["scored"],
            "software_immobility_s": r["immobility_s"],
            "software_immobility_raw_s": r["immobility_raw_s"],
            "software_mobility_s": r["mobility_s"],
            "software_mobility_bouts": r["mobility_bouts"],
            "unknown_frames_window": r["unknown_frames_window"],
            "unknown_fraction_window": "%.4f" % (
                int(r["unknown_frames_window"]) / int(r["window_frames"])),
            "n_human_scorers": len(vals),
            "human_scorers": "|".join(sorted(h["scorer_id"] for h in hs)),
            "human_playback_rates": "|".join(
                sorted({h.get("playback_rate", "") for h in hs})),
            "note": "",
        }
        if vals:
            mean = statistics.fmean(vals)
            row["human_immobility_mean_s"] = "%.2f" % mean
            row["human_immobility_min_s"] = "%.2f" % min(vals)
            row["human_immobility_max_s"] = "%.2f" % max(vals)
            row["human_range_s"] = "%.2f" % (max(vals) - min(vals))
            if r["scored"] == "True" and r["immobility_s"]:
                bias = float(r["immobility_s"]) - mean
                row["bias_s"] = "%.2f" % bias
                row["abs_bias_s"] = "%.2f" % abs(bias)
                row["g8_pass"] = str(abs(bias) <= G8_ABS_BIAS_S)
            else:
                # **不许拿 0 顶上**（DP-032）：没产出数字就留空。
                row["bias_s"] = row["abs_bias_s"] = row["g8_pass"] = ""
                row["note"] = "软件未产出数字 ⇒ 偏差留空，不填 0"
        else:
            for k in ("human_immobility_mean_s", "human_immobility_min_s",
                      "human_immobility_max_s", "human_range_s",
                      "bias_s", "abs_bias_s", "g8_pass"):
                row[k] = ""
            row["note"] = "无人工评分 ⇒ 无法比对（不是 0，是没有）"
        out.append(row)

    out.sort(key=lambda r: r["trial_id"])

    # ── 自校验：必须复现 DP-055 已发布的数 ────────────────────────────────
    pairs = [(float(r["software_immobility_s"]), float(r["human_immobility_mean_s"]))
             for r in out if r["bias_s"] != ""]
    biases = [s - h for s, h in pairs]
    got = {
        "n": len(pairs),
        "mean_bias": statistics.fmean(biases),
        "mean_abs_bias": statistics.fmean([abs(b) for b in biases]),
        "n_pass": sum(1 for b in biases if abs(b) <= G8_ABS_BIAS_S),
        "pearson_r": pearson([s for s, _ in pairs], [h for _, h in pairs]),
    }
    print("自校验：n=%d 平均偏差 %+.2f 平均绝对偏差 %.2f 达标 %d r=%.4f"
          % (got["n"], got["mean_bias"], got["mean_abs_bias"],
             got["n_pass"], got["pearson_r"]))
    bad = []
    for k, want in EXPECT.items():
        g = got[k]
        ok = (g == want) if isinstance(want, int) else abs(g - want) <= TOL
        if not ok:
            bad.append("%s：得 %s 期望 %s" % (k, g, want))
    if bad:
        raise SystemExit(
            "聚合口径与 DP-055 已发布的数字不一致 ⇒ 不写出。\n  " + "\n  ".join(bad)
            + "\n（这不是容差问题，是口径问题：要么我这里聚合方式不同，"
              "要么已发布的数字来自另一份输入。查清再写。）")
    print("自校验通过：口径与 DP-055 一致")

    outdir.mkdir(parents=True, exist_ok=True)
    cmp_path = outdir / "DP-059_软件人工对比_2026-09-07.csv"
    with cmp_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=OUT_FIELDS)
        w.writeheader()
        for r in out:
            w.writerow({k: r.get(k, "") for k in OUT_FIELDS})
    print("已写 " + str(cmp_path))

    # 原始快照：软件那 27 行**原样**留档（列名不动，多带两列出处）
    raw_path = outdir / "DP-059_软件全量快照_DP-053.csv"
    base_cols = [c for c in soft[0] if not c.startswith("_")
                 and c != "trial_id_raw"]
    cols = base_cols + ["trial_id_raw", "_source_csv"]
    with raw_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in sorted(soft, key=lambda x: x["trial_id"]):
            w.writerow({k: r.get(k, "") for k in cols})
    print("已写 " + str(raw_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
