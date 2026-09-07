#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DP-065：残差判据到底分不分得开？——拿人工逐段时间线当标签量 AUC。

**为什么问这个。** DP-063 把 914 s 的偏差预算分成两半：模式A「软件过判不动」52%、
模式B「软件过判活动」48%。DP-064 补正后 BL 的解释力更小了，而且 |偏差| ≥ 40 s 的
10 个试次里有 4 个 BL 完全正常 ⇒ **模式B 有独立病因**。候选有两个，方向完全相反：

- **候选一：门槛位置不对。** 残差本身分得开，只是 θ_mob 切在了错的地方
  ⇒ 可修，而且便宜。
- **候选二：特征本身分不开。** 残差在「人工说在动」与「人工说不动」两群上重叠严重
  ⇒ **任何门槛都救不了**，只能回去修掩膜（DP-052）。

这两个的下一步动作完全不同，所以必须先分清。**AUC 就是分清它们的那一个数**：
AUC 只看排序，与门槛无关 ⇒ AUC 高说明特征good、问题在门槛；AUC 低说明特征本身不行。

**这个脚本不定门槛，也不改常数。** 用的人工数据是 T2/T0 混杂档（DP-048），
**混杂档不能用来定门**（这条是 DP-047/048 已经裁过的）。所以本脚本只回答
**定性问题**「分不分得开」，输出里的最优门槛只作**参考量**打印，
**不构成改 θ_mob 的授权**——那要等 T1 精标数据到位 + 道俊点头。

**三个坑，都绕开了：**

坑一（DP-064 的教训）：拿软件内部量做归因前要先量合法基线。这里的标签是**人工**
评分，不是软件内部量，所以没有那个循环污染问题——但反过来有个新问题，见坑二。

坑二：人工按键有**反应延迟**（按下去比动作晚，松手比停下晚），逐帧对比会在
每个转换点附近无理由地扣分。所以除了全帧 AUC，另报一个**掐掉转换点附近 ±0.5 s**
的稳态 AUC。**稳态 AUC 才是「特征分不分得开」的答案**，全帧 AUC 里混着人的手速。

坑三：两位评分员本身就不一致（极差中位 17.7 s）。所以**只用两人一致的帧**：
两人都说在动 ⇒ MOBILE，两人都说不动 ⇒ IMMOBILE，**分歧帧直接丢掉并报出比例**。
不许用并集或平均——那是替人做裁决。

**unknown 帧（残差 NaN）一律丢掉并报数**，不许补 0（DP-032）。

用法：
    python3 scripts/dp065_residual_separability.py <切片目录> <人工时间线.csv> [输出.md]

人工时间线由 `python3 -m depressionplex.cli.export_human_timeline` 出（DP-061）。
"""
from __future__ import annotations

import csv
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from depressionplex import runner, video                      # noqa: E402
from depressionplex.assay_core import rad, rules              # noqa: E402

#: 探针试次：4 段「BL 正常却大错」的（模式B 主力）+ 2 段对照。
#: 与 DP-058 的探针有重叠是故意的：两条曲线能对上同一批素材。
TRIALS = [
    ("30mg_2周-ch3", -69.5, "模式B BL正常"),
    ("30mg_2周_2+20_2周2-ch3", -59.2, "模式B BL正常"),
    ("20mg_3周-ch3", -41.4, "模式B BL正常"),
    ("20mg_3周-ch1", +60.7, "模式A BL正常"),
    ("10mg_2周-ch2", +2.8, "对照 偏差小"),
    ("20mg_1周_1-3+20_2周1-ch2", +0.1, "对照 偏差小"),
]

#: 掐掉人工转换点附近这么多秒（人的反应延迟量级）。见 docstring 坑二。
EDGE_S = 0.5

#: 现行冻结门槛，只用来**报出它落在哪个分位**，不参与任何计算。
THETA_MOB_FROZEN = 0.0175


def load_human(csv_path: Path) -> dict[str, dict[str, list[tuple[float, float]]]]:
    """人工时间线 → {trial_id: {scorer_id: [(start_s, end_s), ...]}}（录像时基）。

    说明行（`start_s` 为空）跳过并**不**当成 0 段——那是 DP-032 的整条事故链。
    """
    out: dict[str, dict[str, list[tuple[float, float]]]] = defaultdict(
        lambda: defaultdict(list))
    with csv_path.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["source"] != "human" or r["start_s"] == "":
                continue
            out[r["trial_id"]][r["scorer_id"]].append(
                (float(r["start_s"]), float(r["end_s"])))
    return {k: dict(v) for k, v in out.items()}


def frame_labels(segs: list[tuple[float, float]], n: int, fps: float) -> np.ndarray:
    """段列表 → 逐帧「在动」布尔。半开区间 [start, end)，与 timeline 层同式。"""
    lab = np.zeros(n, dtype=bool)
    for a, b in segs:
        i0 = max(0, int(round(a * fps)))
        i1 = min(n, int(round(b * fps)))
        if i1 > i0:
            lab[i0:i1] = True
    return lab


def transition_mask(lab: np.ndarray, fps: float) -> np.ndarray:
    """转换点附近 ±EDGE_S 的帧 ⇒ True（要掐掉的）。"""
    k = max(1, int(round(EDGE_S * fps)))
    chg = np.zeros(lab.size, dtype=bool)
    d = np.flatnonzero(lab[1:] != lab[:-1])
    for i in d:
        chg[max(0, i - k): min(lab.size, i + k + 1)] = True
    return chg


def auc(scores: np.ndarray, pos: np.ndarray) -> float | None:
    """Mann–Whitney U / (n_pos·n_neg)。两类里有一类为空 ⇒ None，**不返回 0.5 顶上**。"""
    p = scores[pos]
    q = scores[~pos]
    if p.size == 0 or q.size == 0:
        return None
    order = np.argsort(np.concatenate([p, q]), kind="mergesort")
    ranks = np.empty(order.size, dtype=np.float64)
    ranks[order] = np.arange(1, order.size + 1)
    # 并列值取平均秩，否则大量相同残差会把 AUC 系统性拉偏
    allv = np.concatenate([p, q])
    sv = allv[order]
    i = 0
    while i < sv.size:
        j = i
        while j + 1 < sv.size and sv[j + 1] == sv[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + 1 + j + 1) / 2.0
        i = j + 1
    r_pos = ranks[:p.size].sum()
    u = r_pos - p.size * (p.size + 1) / 2.0
    return float(u / (p.size * q.size))


def youden(scores: np.ndarray, pos: np.ndarray) -> tuple[float, float] | None:
    """最大化 (TPR − FPR) 的门槛。**只作参考量报出**，不构成改 θ_mob 的授权。"""
    if pos.all() or (~pos).all():
        return None
    cand = np.unique(np.quantile(scores, np.linspace(0.001, 0.999, 400)))
    best = None
    npos = int(pos.sum())
    nneg = int(pos.size - npos)
    for t in cand:
        hi = scores >= t
        tpr = float(np.count_nonzero(hi & pos)) / npos
        fpr = float(np.count_nonzero(hi & ~pos)) / nneg
        j = tpr - fpr
        if best is None or j > best[1]:
            best = (float(t), j)
    return best


def probe_one(clip: Path, human: dict[str, list[tuple[float, float]]]) -> dict | None:
    info = video.probe(clip)
    idx = runner.calibration_indices(info.n_frames)
    plan = runner.build_plan(video.frames_at(info, idx), n_chambers=1,
                             calib_indices=tuple(idx))
    seqs = runner.segment_series(video.iter_gray(info), plan)
    ch = plan.chambers[0]
    masks = seqs.get(ch.index)
    if masks is None or len(masks) == 0 or ch.suspension is None:
        return None
    bl = rad.trial_body_length(masks)
    if bl <= 0:
        return None
    # **不传 bl**：这里要量的是**已发布口径下**残差分不分得开，所以让
    # `build_tst_features` 自己按内部逻辑取归一化 BL（`main` 上的行为）。
    # DP-058 的 `bl=` 透传（PR #41）是**扰动实验**用的，本脚本不依赖它 ⇒
    # 本脚本可以直接在 `main` 上跑。BL 只作信息打印。
    feats = rules.build_tst_features(masks, suspension=ch.suspension,
                                    fps=info.fps)
    resid = np.asarray(feats.residual, dtype=np.float64)
    n = resid.size
    fps = float(info.fps)

    scorers = sorted(human)
    if len(scorers) < 2:
        return dict(err="人工只有 %d 位评分员，一致帧无从谈起" % len(scorers))

    # 时基对齐自检：人工时间线是**录像时基**，切片理应也从录像 0 s 起。
    # `20mg_1周_1-3+20_2周1` 那一批有过 9000 vs 11470 帧的窗口错位（DP-049）⇒
    # 悄悄错位会直接把 AUC 毒掉，所以先量「有多少人工段落在帧范围之外」。
    clip_end = n / fps
    span = sum(max(0.0, b - a) for s in scorers for a, b in human[s])
    outside = sum(max(0.0, b - min(a, clip_end)) - max(0.0, min(b, clip_end) - min(a, clip_end))
                  for s in scorers for a, b in human[s])
    frac_out = (outside / span) if span > 0 else 0.0
    if frac_out > 0.02:
        return dict(err="时基疑似错位：%.0f%% 的人工段落在切片帧范围之外"
                        "（切片 %.1f s / 人工段最晚 %.1f s）⇒ 不出 AUC，先查映射"
                        % (100 * frac_out, clip_end,
                           max(b for s in scorers for _, b in human[s])))

    la = frame_labels(human[scorers[0]], n, fps)
    lb = frame_labels(human[scorers[1]], n, fps)

    agree = la == lb
    unk = np.isnan(resid)
    usable = agree & ~unk

    res = {"bl": bl, "fps": fps, "n": n, "scorers": scorers,
           "frac_disagree": float(np.count_nonzero(~agree)) / n,
           "frac_unknown": float(np.count_nonzero(unk)) / n,
           "frac_usable": float(np.count_nonzero(usable)) / n}

    for tag, extra in (("全帧", np.ones(n, dtype=bool)),
                       ("稳态", ~transition_mask(la, fps) & ~transition_mask(lb, fps))):
        m = usable & extra
        if np.count_nonzero(m) < 100:
            res[tag] = None
            continue
        s = resid[m]
        pos = la[m]          # 两人一致，取哪个都一样
        a = auc(s, pos)
        y = youden(s, pos)
        res[tag] = dict(
            n=int(m.sum()), auc=a,
            frac_mobile=float(pos.mean()),
            q_mob=np.quantile(s[pos], [0.25, 0.5, 0.75]).tolist() if pos.any() else None,
            q_imm=np.quantile(s[~pos], [0.25, 0.5, 0.75]).tolist() if (~pos).any() else None,
            theta_pct_mob=float(np.mean(s[pos] < THETA_MOB_FROZEN)) if pos.any() else None,
            theta_pct_imm=float(np.mean(s[~pos] < THETA_MOB_FROZEN)) if (~pos).any() else None,
            youden=y)
    return res


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    root = Path(sys.argv[1])
    human_all = load_human(Path(sys.argv[2]))
    lines: list[str] = []

    def emit(s: str = "") -> None:
        print(s, flush=True)
        lines.append(s)

    emit("# DP-065 残差判据可分性实测")
    emit()
    emit("标签 = **两位评分员一致**的帧（分歧帧丢掉）。`unknown`（残差 NaN）帧丢掉。")
    emit("**稳态**列掐掉了人工转换点附近 ±%.1f s（人的反应延迟）——**这一列才是"
         "「特征分不分得开」的答案**，全帧列里混着人的手速。" % EDGE_S)
    emit()
    emit("人工数据是 **T2/T0 混杂档**（DP-048）⇒ **不能用来定门**。"
         "本表只回答定性问题「分不分得开」；参考门槛只作打印，**不构成改 θ_mob 的授权**。")
    emit()
    emit("| 试次 | 角色 | 目前偏差 | 可用帧 | 两人分歧 | unknown | "
         "全帧 AUC | **稳态 AUC** | 稳态里「在动」占比 |")
    emit("|---|---|---|---|---|---|---|---|---|")

    good: list[tuple[str, float]] = []
    detail: list[str] = []
    for stem, bias, role in TRIALS:
        clip = root / (stem + ".mp4")
        print("== %s ==" % stem, flush=True)
        if not clip.exists():
            emit("| %s | %s | %+.1f | — | — | — | — | — | 素材不存在 |"
                 % (stem, role, bias))
            continue
        if stem not in human_all:
            emit("| %s | %s | %+.1f | — | — | — | — | — | 人工时间线里没有此试次 |"
                 % (stem, role, bias))
            continue
        t0 = time.time()
        try:
            r = probe_one(clip, human_all[stem])
        except Exception as e:                                  # noqa: BLE001
            emit("| %s | %s | %+.1f | — | — | — | — | — | 失败：%s |"
                 % (stem, role, bias, str(e)[:60]))
            continue
        if r is None or "err" in (r or {}):
            emit("| %s | %s | %+.1f | — | — | — | — | — | %s |"
                 % (stem, role, bias, (r or {}).get("err", "不产数字")))
            continue
        st = r.get("稳态")
        fl = r.get("全帧")
        f = lambda v: "—" if v is None else "%.3f" % v          # noqa: E731
        emit("| %s | %s | %+.1f | %.0f%% | %.0f%% | %.0f%% | %s | **%s** | %s |"
             % (stem, role, bias, 100 * r["frac_usable"],
                100 * r["frac_disagree"], 100 * r["frac_unknown"],
                f(fl and fl["auc"]), f(st and st["auc"]),
                "—" if not st else "%.0f%%" % (100 * st["frac_mobile"])))
        print("   %.0f s" % (time.time() - t0), flush=True)
        if st and st["auc"] is not None:
            good.append((stem, st["auc"]))
            d = ["", "### `%s`（%s，偏差 %+.1f s）" % (stem, role, bias), ""]
            d.append("- 稳态可用帧 %d，其中「在动」%.0f%%；试次 BL %.2f px"
                     % (st["n"], 100 * st["frac_mobile"], r["bl"]))
            if st["q_imm"] and st["q_mob"]:
                d.append("- 残差四分位（人工说**不动**）：%.5f / **%.5f** / %.5f"
                         % tuple(st["q_imm"]))
                d.append("- 残差四分位（人工说**在动**）：%.5f / **%.5f** / %.5f"
                         % tuple(st["q_mob"]))
                d.append("- 现行冻结门槛 θ_mob = %.4f 切下去："
                         "人工说不动的帧有 **%.0f%%** 落在门下（判对），"
                         "人工说在动的帧有 **%.0f%%** 也落在门下（**判错成不动**）"
                         % (THETA_MOB_FROZEN, 100 * st["theta_pct_imm"],
                            100 * st["theta_pct_mob"]))
            if st["youden"]:
                d.append("- 参考：最大化 (TPR−FPR) 的门槛落在 **%.5f**（J = %.3f）"
                         "——**只作参考量，不是授权**" % st["youden"])
            detail += d

    if good:
        emit()
        emit("## 判定")
        emit()
        aucs = sorted(a for _, a in good)
        med = aucs[len(aucs) // 2]
        emit("稳态 AUC：中位 **%.3f**，范围 %.3f–%.3f（n=%d 个试次）。"
             % (med, aucs[0], aucs[-1], len(aucs)))
        emit()
        if med >= 0.90:
            emit("⇒ **特征分得开，问题在门槛位置**（候选一）。残差的排序能力够，"
                 "θ_mob 切在了错的地方。**下一步：等 T1 精标数据到位再定门**"
                 "（混杂档不能定门，DP-047/048 已裁）。掩膜不是模式B 的主因。")
        elif med >= 0.75:
            emit("⇒ **两个候选都占一部分**：特征有排序能力但不干净，"
                 "光挪门槛拿不回全部 48%%。**下一步：先看残差在人工说不动的帧上"
                 "的高分尾巴是哪些帧**（掩膜抖动的典型signature），"
                 "同时把重定门槛留给 T1。")
        else:
            emit("⇒ **特征本身分不开（候选二）**：残差在两群上重叠严重，"
                 "**任何门槛都救不了模式B**。**下一步只有一条：回去修掩膜（DP-052）**，"
                 "不要在 θ_mob 上浪费时间。")
        emit()
        emit("**本条不构成改任何常数的授权。** 用的是 T2/T0 混杂档人工数据，"
             "DP-048 已裁明「混杂档只作breadth、不作定门锚」；重定 θ_mob 要 T1 + 道俊点头。")
        lines += detail
        for d in detail:
            print(d, flush=True)

    if len(sys.argv) > 3:
        Path(sys.argv[3]).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n已写出 " + sys.argv[3])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
