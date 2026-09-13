#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DP-073-R：残差分母该取哪个 BL —— 用**共享门槛下的判别力**判，不再只看离散度。

**为什么要重做这条的判据。** DP-073 用「每试次最优门槛的离散度」（极差比 19.3x、
CV 50.4%）论证 BL² 归一化是反作用的。DP-071-R 随后打出一个反例：距离场口径把
离散度改善了（19.4x → 9.3x、CV 51.4% → 37.2%），帧级 AUC 却 25/26 变差 ⇒
**给所有试次加一个共同底噪就能压低极差比，却不会让任何一帧更好分** ⇒
**离散度单独不构成判据。** 所以本条换一套判据。

**换成什么判据。** 归一化分母的作用只有一个：让**同一个门槛**在不同试次上意思一样。
所以要量的就是「共享一个门槛时，判别力剩多少」：

  · **pooled AUC / pooled J**：把 26 个试次的可用帧**汇到一起**算。分母只要没把
    试次间的尺度对齐，汇总后两类分布就会互相糊住，pooled 量直接掉。
  · **J 损失** = 每试次最优 J − 每试次在**全局门槛**下的 J。这就是"被迫共用一个
    门槛"付出的代价，也正是归一化该负责减小的量。

**每试次 AUC 对分母完全不敏感**（同一试次里分母是一个常数，除法保序）⇒
分母之争在单试次里根本看不出来，只能在汇总层看。脚本会把这条当自检打印出来：
若各候选的每试次 AUC 不是逐位相等，说明实现有 bug，先查它。

**跑之前写死的判定（不许事后改）：**

| 读数 | 结论 |
|---|---|
| 某候选 pooled J 比冻结口径高 **≥ +0.02** 且 pooled AUC 不降 | 换分母**有证据** ⇒ 改默认，PR 交道俊审 |
| 最佳候选 pooled J 提升 **≤ 0** | 冻结口径没输 ⇒ **不换** |
| 其间 | **分不出** ⇒ 不换，两条都留着等 T1 |

**这条不是验收**：pooled AUC/J 是帧级量，G7/G8/G11 是试次级量，不可混用。
门槛列一律只作参考量，**不构成改 θ_mob 的授权**（DP-055）。**不报秒数**。

用法：
    python3 scripts/dp073_pooled_threshold.py <切片目录> <人工时间线.csv> <冻结对比.csv> \\
        [输出.md] [--reuse 缓存.npz]
"""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from depressionplex import runner, video                      # noqa: E402
from depressionplex.assay_core import rad                     # noqa: E402

#: 判「换分母有证据」的 pooled J 提升门槛。
_DJ_GOOD = 0.02

#: 残差原始量的缓存（bl=1 ⇒ 纯像素数）。分母只是一个 per-trial 常数除法，
#: 所以**一次采集能评所有候选**——不必为每个候选重跑 22 分钟。
_CACHE = Path("/tmp/dp073/raw_residuals.npz")

_SIB = Path(__file__).resolve().parent / "dp065_residual_separability.py"
_spec = importlib.util.spec_from_file_location("_dp065", _SIB)
assert _spec is not None and _spec.loader is not None
dp065 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dp065)


def recording_of(trial_id: str) -> str:
    """`10mg_2周-ch3` → `10mg_2周`。同一段录像 = 同一相机、同一距离、同一场次。"""
    return trial_id.rsplit("-ch", 1)[0]


def spearman(a: list[float], b: list[float]) -> float:
    if len(a) < 3:
        return float("nan")

    def rank(v: list[float]) -> list[float]:
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            mean = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = mean
            i = j + 1
        return r

    ra, rb = rank(a), rank(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = sum((x - ma) ** 2 for x in ra) ** 0.5
    db = sum((y - mb) ** 2 for y in rb) ** 0.5
    return num / (da * db) if da > 0 and db > 0 else float("nan")


def dispersion(v: list[float]) -> tuple[float, float]:
    """→ (极差比, CV%)。与 DP-073 同式，**只作对照**，不作判据。"""
    if not v or min(v) <= 0:
        return (float("nan"), float("nan"))
    m = sum(v) / len(v)
    sd = (sum((x - m) ** 2 for x in v) / (len(v) - 1)) ** 0.5 if len(v) > 1 else 0.0
    return (max(v) / min(v), 100.0 * sd / m)


def collect_one(clip: Path, human: dict[str, list[tuple[float, float]]]) -> dict | None:
    """一个试次 → 稳态一致帧上的**原始像素**残差 + 人工标签 + 试次级 BL。"""
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
    n, fps = len(masks), float(info.fps)

    scorers = sorted(human)
    if len(scorers) < 2:
        return dict(err="人工只有 %d 位评分员，一致帧无从谈起" % len(scorers))
    clip_end = n / fps
    span = sum(max(0.0, b - a) for s in scorers for a, b in human[s])
    outside = sum(max(0.0, b - min(a, clip_end))
                  - max(0.0, min(b, clip_end) - min(a, clip_end))
                  for s in scorers for a, b in human[s])
    if span > 0 and outside / span > 0.02:
        return dict(err="时基疑似错位：%.0f%% 人工段在帧范围外" % (100 * outside / span))

    la = dp065.frame_labels(human[scorers[0]], n, fps)
    lb = dp065.frame_labels(human[scorers[1]], n, fps)
    steady = (la == lb) & ~dp065.transition_mask(la, fps) \
        & ~dp065.transition_mask(lb, fps)

    # bl=1.0 ⇒ 残差就是**像素数本身**。分母留到评估阶段再除。
    rows = rad.decompose_series(masks, lags=(1,), bl=1.0)
    raw = np.asarray([np.nan if r.get("residual_lag1") is None else r["residual_lag1"]
                      for r in rows], dtype=np.float64)
    usable = steady & ~np.isnan(raw)
    if int(usable.sum()) < 100:
        return dict(err="稳态一致可用帧只有 %d 个（<100）" % int(usable.sum()))
    pos = la[usable]
    if pos.all() or (~pos).all():
        return dict(err="稳态帧里只有一类标签")
    return dict(raw=raw[usable], pos=pos, bl=bl, n=n,
                frac_unknown=float(np.mean(np.isnan(raw))))


def collect(root: Path, human_csv: Path, frozen: Path) -> dict:
    human_all = dp065.load_human(human_csv)
    trials = dp065.trials_from_frozen(frozen, root, human_all)
    got: dict[str, dict] = {}
    for tid, _bias, _role in trials:
        t0 = time.time()
        try:
            r = collect_one(root / (tid + ".mp4"), human_all.get(tid, {}))
        except Exception as exc:                      # noqa: BLE001
            print("跳过 %s：%s: %s" % (tid, type(exc).__name__, exc), flush=True)
            continue
        if r is None:
            print("跳过 %s：分割不可用" % tid, flush=True)
            continue
        if "err" in r:
            print("跳过 %s：%s" % (tid, r["err"]), flush=True)
            continue
        got[tid] = r
        print("  %-34s %5.0f s  BL %.2f  可用帧 %d  unknown %.0f%%"
              % (tid, time.time() - t0, r["bl"], r["raw"].size,
                 100 * r["frac_unknown"]), flush=True)
    return got


def save(got: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flat: dict[str, np.ndarray] = {}
    for tid, r in got.items():
        flat["raw::" + tid] = r["raw"]
        flat["pos::" + tid] = r["pos"]
        flat["meta::" + tid] = np.asarray([r["bl"], r["n"], r["frac_unknown"]])
    np.savez_compressed(path, **flat)
    print("缓存已写 %s（%d 试次）" % (path, len(got)))


def load(path: Path) -> dict:
    z = np.load(path, allow_pickle=False)
    tids = sorted({k.split("::", 1)[1] for k in z.files})
    out = {}
    for tid in tids:
        m = z["meta::" + tid]
        out[tid] = dict(raw=z["raw::" + tid], pos=z["pos::" + tid].astype(bool),
                        bl=float(m[0]), n=int(m[1]), frac_unknown=float(m[2]))
    print("从缓存读入 %d 试次：%s" % (len(out), path))
    return out


def candidates(got: dict) -> list[tuple[str, str, dict[str, float]]]:
    """→ [(键, 说明, {试次: 分母})]。分母是**除在原始像素残差上**的那个数。"""
    tids = sorted(got)
    bl = {t: got[t]["bl"] for t in tids}
    by_rec: dict[str, list[float]] = {}
    for t in tids:
        by_rec.setdefault(recording_of(t), []).append(bl[t])
    rec_med = {r: float(np.median(v)) for r, v in by_rec.items()}
    all_med = float(np.median([bl[t] for t in tids]))
    return [
        ("frozen_bl2", "**冻结口径**：每试次自己的 BL²",
         {t: bl[t] ** 2 for t in tids}),
        ("bl1", "每试次自己的 BL¹", {t: bl[t] for t in tids}),
        ("raw_px", "不归一化（原始像素）", {t: 1.0 for t in tids}),
        ("rec_med_bl2", "**同录像**中位 BL 的平方",
         {t: rec_med[recording_of(t)] ** 2 for t in tids}),
        ("rec_med_bl1", "同录像中位 BL 的一次方",
         {t: rec_med[recording_of(t)] for t in tids}),
        ("all_med_bl2", "全批一个常数（全体中位 BL）²",
         {t: all_med ** 2 for t in tids}),
    ]


def evaluate(got: dict, den: dict[str, float]) -> dict:
    tids = sorted(got)
    scores = {t: got[t]["raw"] / den[t] for t in tids}
    pooled_s = np.concatenate([scores[t] for t in tids])
    pooled_p = np.concatenate([got[t]["pos"] for t in tids])
    pooled_auc = dp065.auc(pooled_s, pooled_p)
    gy = dp065.youden(pooled_s, pooled_p)
    per_best, per_at_global, per_thr = [], [], []
    for t in tids:
        y = dp065.youden(scores[t], got[t]["pos"])
        if y is not None:
            per_thr.append(y[0])
            per_best.append(y[1])
        if gy is not None:
            hi = scores[t] >= gy[0]
            p = got[t]["pos"]
            per_at_global.append(
                float(np.count_nonzero(hi & p)) / max(int(p.sum()), 1)
                - float(np.count_nonzero(hi & ~p)) / max(int((~p).sum()), 1))
    rr, cv = dispersion(per_thr)
    return dict(
        pooled_auc=pooled_auc,
        pooled_j=None if gy is None else gy[1],
        global_thr=None if gy is None else gy[0],
        med_best=float(np.median(per_best)),
        med_at_global=float(np.median(per_at_global)) if per_at_global else float("nan"),
        range_ratio=rr, cv=cv,
        rho_bl_thr=spearman([got[t]["bl"] for t in tids], per_thr),
        per_auc=[dp065.auc(scores[t], got[t]["pos"]) for t in tids],
    )


def main() -> int:
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    reuse = "--reuse" in sys.argv
    if len(argv) < 3 and not reuse:
        print(__doc__)
        return 2
    lines: list[str] = []

    def emit(s: str = "") -> None:
        lines.append(s)
        print(s, flush=True)

    if reuse and _CACHE.exists():
        got = load(_CACHE)
    else:
        got = collect(Path(argv[0]), Path(argv[1]), Path(argv[2]))
        if len(got) < 6:
            print("只跑成 %d 个试次（<6）⇒ 不出结论" % len(got))
            return 1
        save(got, _CACHE)

    cands = candidates(got)
    res = {k: evaluate(got, d) for k, _why, d in cands}

    ref = res["frozen_bl2"]["per_auc"]
    same = all(all(abs((a or -1) - (b or -1)) < 1e-12
                   for a, b in zip(ref, res[k]["per_auc"])) for k, _w, _d in cands)
    emit("# DP-073-R 分母之争：用共享门槛下的判别力判，不只看离散度")
    emit()
    emit("n=%d 试次，稳态一致帧共 %d 个。残差**采集一次**（bl=1 ⇒ 原始像素），"
         "各候选只是除以不同的 per-trial 常数。"
         % (len(got), sum(got[t]["raw"].size for t in got)))
    emit()
    emit("**自检：每试次 AUC 对分母不敏感** —— %s"
         % ("**6/6 候选逐位相等** ⇒ 实现正确（同一试次内分母是常数，除法保序）⇒ "
            "分母之争只能在汇总层看，单试次 AUC 看不出来。" if same
            else "**竟然不相等 ⇒ 实现有 bug，先查这条，别看下面的读数。**"))
    emit()
    emit("| 分母 | 说明 | pooled AUC | pooled J | 每试次最优 J 中位 | "
         "共用全局门槛后 J 中位 | **J 损失** | 极差比 | CV | rho(BL,门槛) |")
    emit("|---|---|---|---|---|---|---|---|---|---|")
    for k, why, _d in cands:
        r = res[k]
        loss = r["med_best"] - r["med_at_global"]
        emit("| `%s` | %s | %.4f | %.4f | %.4f | %.4f | **%.4f** | %.1fx | %.1f%% | %+.3f |"
             % (k, why, r["pooled_auc"], r["pooled_j"], r["med_best"],
                r["med_at_global"], loss, r["range_ratio"], r["cv"], r["rho_bl_thr"]))
    emit()

    base = res["frozen_bl2"]
    best_k = max((k for k, _w, _d in cands), key=lambda k: res[k]["pooled_j"])
    dj = res[best_k]["pooled_j"] - base["pooled_j"]
    dauc = res[best_k]["pooled_auc"] - base["pooled_auc"]
    emit("## 判定")
    emit()
    if best_k == "frozen_bl2" or dj <= 0:
        emit("pooled J 最高的候选就是**冻结口径本身**（或提升 ≤ 0）⇒ 按预先声明的规则，"
             "**不换分母**。")
    elif dj >= _DJ_GOOD and dauc >= 0:
        emit("最佳候选 `%s`：pooled J **%+.4f**（≥%+.2f）、pooled AUC **%+.4f**（不降）"
             "⇒ 三条满足 ⇒ **换分母有证据**，改默认并把 PR 交道俊审。"
             % (best_k, dj, _DJ_GOOD, dauc))
    else:
        emit("最佳候选 `%s`：pooled J %+.4f、pooled AUC %+.4f ⇒ 落在「分不出」一档 "
             "⇒ **不换默认**，两条都留着等 T1。" % (best_k, dj, dauc))
    emit()
    emit("**离散度这一列只作对照，不作判据**（DP-071-R 的反例：距离场口径把极差比从 "
         "19.4x 压到 9.3x，帧级 AUC 却 25/26 变差）。")
    emit()
    emit("**这条不是什么**：① 不是验收——pooled AUC/J 是帧级量，G7/G8/G11 是试次级量；"
         "② 不构成改 θ_mob 的授权（DP-055）；③ 不报秒数。")

    if len(argv) > 3:
        Path(argv[3]).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n已写 " + argv[3])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
