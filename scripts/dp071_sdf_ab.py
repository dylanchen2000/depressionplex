#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DP-071-R：新旧两个残差口径的正面对比（同一次分割、同一批帧、只差口径）。

**为什么必须做这一步而不是直接改默认。** DP-071 查出 `warp_mask` 有精确的 0.5 px
死区，并用算术把「升采样」「软掩膜」「1 px 容差」三条路都排掉，只剩距离场。
距离场已经实现（`rad.RESIDUAL_MODES`），三条硬性质当场验过：整数位移下与旧口径
**逐位相等**、同帧残差**恰好 0**、亚像素位移下响应**严格线性**（0.05 px → 2.700 px，
正好是 0.05 × 周长）。**但合成序列上的读数是反的**：钟摆残差底 0.00760 → 0.01277
（+68%），关节残差 0.03921 → 0.04111（+5%）⇒ 区分度（均值比）从 **5.16 掉到 3.22**。

这个反向读数有一个不能忽视的解释：**硬 XOR 是「量化自洽」的**——它按整像素测变化，
也按整像素补偿，两边的量化互相抵消；而覆盖率口径对**它根本观测不到**的亚像素几何
敏感（掩膜就是二值的），于是把光栅化噪声也照实算进残差。**死区可能不是病，而是
和分子的分辨率配套的。** 这条不能靠推理定，只能量。

**已经排掉的一个候选解释**：亚像素精修（在距离场代价上搜 ±0.6 px / ±1.5°）
只把钟摆压回 0.01255（−1.7%），却把关节压掉 24%（0.04111 → 0.03130），
且 |dy| 与 |dtheta| 双双顶到搜索边界 ⇒ **精修在新代价函数下仍然是「把关节运动
当刚体吸收」**，DP-071 原来那条结论在换了代价函数之后依然成立 ⇒ 这条路关掉。

**跑之前写死的判定（不许事后改）：**

| 读数 | 结论 |
|---|---|
| 稳态 ΔAUC 中位 ≥ +0.02 **且** ≥ 2/3 试次为正 **且** 门槛极差比没变差 | 距离场口径**有证据** ⇒ 翻默认，PR 交道俊审 |
| 稳态 ΔAUC 中位 ≤ 0 | 距离场口径是**错方向** ⇒ **不翻默认**，本条只留机制、工具与这份对比 |
| 其间 | **分不出** ⇒ 不翻默认，两个口径都留着，等 T1 |

**这份对比不是验收。** AUC 是帧级量、G7/G8/G11 是试次级量，两者不可混用
（DP-071 已立）。人工标签是 T2/T0 混杂档（DP-048）⇒ 只作定性判别，
**门槛列一律只作参考量，不构成改 θ_mob 的授权**。

用法：
    python3 scripts/dp071_sdf_ab.py <切片目录> <人工时间线.csv> <冻结对比.csv> [输出.md]
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

#: 判「距离场方向有证据」的稳态 AUC 增量门槛。
_DAUC_GOOD = 0.02

#: 为「有证据」额外要求的正向试次占比——防止一两个试次的大改善拉动中位数。
_WIN_FRAC = 2.0 / 3.0

_SIB = Path(__file__).resolve().parent / "dp065_residual_separability.py"
_spec = importlib.util.spec_from_file_location("_dp065", _SIB)
assert _spec is not None and _spec.loader is not None
dp065 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dp065)


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
    """→ (极差比, CV%)。与 DP-073 同式，便于两条横向对照。"""
    if not v or min(v) <= 0:
        return (float("nan"), float("nan"))
    m = sum(v) / len(v)
    sd = (sum((x - m) ** 2 for x in v) / (len(v) - 1)) ** 0.5 if len(v) > 1 else 0.0
    return (max(v) / min(v), 100.0 * sd / m)


def probe_one(clip: Path, human: dict[str, list[tuple[float, float]]]) -> dict | None:
    """一个试次：分割一次，两个口径各出一条残差序列，在**同一批帧**上比。"""
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
    n = len(masks)
    fps = float(info.fps)

    scorers = sorted(human)
    if len(scorers) < 2:
        return dict(err="人工只有 %d 位评分员，一致帧无从谈起" % len(scorers))

    # 与 DP-065 同一条时基自检：错位会直接把 AUC 毒掉（DP-049 的 9000 vs 11470）。
    clip_end = n / fps
    span = sum(max(0.0, b - a) for s in scorers for a, b in human[s])
    outside = sum(max(0.0, b - min(a, clip_end))
                  - max(0.0, min(b, clip_end) - min(a, clip_end))
                  for s in scorers for a, b in human[s])
    if span > 0 and outside / span > 0.02:
        return dict(err="时基疑似错位：%.0f%% 人工段在帧范围外 ⇒ 不出 AUC"
                        % (100 * outside / span))

    la = dp065.frame_labels(human[scorers[0]], n, fps)
    lb = dp065.frame_labels(human[scorers[1]], n, fps)
    steady = (la == lb) & ~dp065.transition_mask(la, fps) \
        & ~dp065.transition_mask(lb, fps)

    out: dict = {"bl": bl, "n": n, "fps": fps, "scorers": scorers}
    resid: dict[str, np.ndarray] = {}
    for mode in rad.RESIDUAL_MODES:
        rows = rad.decompose_series(masks, lags=(1,), bl=bl, residual_mode=mode)
        resid[mode] = np.asarray(
            [np.nan if r.get("residual_lag1") is None else r["residual_lag1"]
             for r in rows], dtype=np.float64)

    # unknown 应当**与口径无关**（None 只来自剪影缺失）。不假设，量一遍。
    unk = {m: np.isnan(v) for m, v in resid.items()}
    out["unknown_same"] = bool(np.array_equal(*unk.values()))
    out["frac_unknown"] = float(np.mean(list(unk.values())[0]))

    usable = steady & ~unk["binary_xor"] & ~unk["sdf_coverage"]
    if int(usable.sum()) < 100:
        return dict(err="稳态一致可用帧只有 %d 个（<100）" % int(usable.sum()))
    pos = la[usable]
    out["n_usable"] = int(usable.sum())
    out["frac_mobile"] = float(pos.mean())
    if pos.all() or (~pos).all():
        return dict(err="稳态帧里只有一类标签，AUC 无从谈起")

    for mode in rad.RESIDUAL_MODES:
        s = resid[mode][usable]
        y = dp065.youden(s, pos)
        out[mode] = dict(
            auc=dp065.auc(s, pos),
            youden=None if y is None else y[0],
            j=None if y is None else y[1],
            med_imm=float(np.median(s[~pos])),
            med_mob=float(np.median(s[pos])),
            zero_frac=float(np.mean(s == 0.0)),
        )
    return out


def verdict(dauc_med: float, win_frac: float, worse_spread: bool) -> tuple[str, str]:
    if dauc_med >= _DAUC_GOOD and win_frac >= _WIN_FRAC and not worse_spread:
        return ("距离场口径有证据",
                "稳态 ΔAUC 中位 **%+.3f**（≥%+.2f）、正向试次 **%.0f%%**（≥67%%）、"
                "门槛离散度没变差 ⇒ 三条**全部**满足 ⇒ 翻默认，PR 交道俊审。"
                % (dauc_med, _DAUC_GOOD, 100 * win_frac))
    if dauc_med <= 0:
        return ("距离场口径是错方向",
                "稳态 ΔAUC 中位 **%+.3f**（≤0）⇒ 按预先声明的规则，**不翻默认**。"
                "死区确实存在（那是代码级恒等式），但补掉它并没有让判据更分得开——"
                "支持「硬 XOR 与分子的整像素分辨率是配套的」这个解释。"
                % dauc_med)
    return ("分不出",
            "稳态 ΔAUC 中位 %+.3f 落在 0 与 %+.2f 之间（或正向试次不足 67%%、"
            "或门槛离散度变差）⇒ **不翻默认**，两个口径都留着等 T1。"
            % (dauc_med, _DAUC_GOOD))


def main() -> int:
    argv = sys.argv[1:]
    if len(argv) < 3:
        print(__doc__)
        return 2
    root, human_csv, frozen = Path(argv[0]), Path(argv[1]), Path(argv[2])
    out_path = argv[3] if len(argv) > 3 else None
    lines: list[str] = []

    def emit(s: str = "") -> None:
        lines.append(s)
        print(s, flush=True)

    human_all = dp065.load_human(human_csv)
    trials = dp065.trials_from_frozen(frozen, root, human_all)
    if not trials:
        print("没有可用试次 ⇒ 不出结论")
        return 1

    rows: list[tuple[str, str, dict]] = []
    for tid, bias, role in trials:
        t0 = time.time()
        try:
            r = probe_one(root / (tid + ".mp4"), human_all.get(tid, {}))
        except Exception as exc:                      # noqa: BLE001
            print("跳过 %s：%s: %s" % (tid, type(exc).__name__, exc), flush=True)
            continue
        if r is None:
            print("跳过 %s：分割不可用" % tid, flush=True)
            continue
        if "err" in r:
            print("跳过 %s：%s" % (tid, r["err"]), flush=True)
            continue
        rows.append((tid, role, r))
        print("  %-34s %5.0f s  AUC %.3f → %.3f  门槛 %.5f → %.5f"
              % (tid, time.time() - t0, r["binary_xor"]["auc"],
                 r["sdf_coverage"]["auc"], r["binary_xor"]["youden"],
                 r["sdf_coverage"]["youden"]), flush=True)

    if len(rows) < 6:
        print("只跑成 %d 个试次（<6）⇒ 不出结论" % len(rows))
        return 1

    emit("# DP-071-R 残差口径 A/B：硬 XOR vs 距离场覆盖率")
    emit()
    emit("同一次分割、同一批稳态一致帧，只差 `residual_mode`。n=%d 试次。" % len(rows))
    emit()
    emit("| 试次 | 角色 | BL px | 稳态帧 | AUC 硬 | AUC 距离场 | ΔAUC "
         "| 门槛 硬 | 门槛 距离场 | 不动中位 硬 | 不动中位 距离场 | 硬口径恰好 0 的帧 |")
    emit("|---|---|---|---|---|---|---|---|---|---|---|---|")
    daucs, th_x, th_s, bls = [], [], [], []
    for tid, role, r in rows:
        a, b = r["binary_xor"], r["sdf_coverage"]
        d = b["auc"] - a["auc"]
        daucs.append(d)
        th_x.append(a["youden"])
        th_s.append(b["youden"])
        bls.append(r["bl"])
        emit("| `%s` | %s | %.2f | %d | %.3f | %.3f | **%+.3f** | %.5f | %.5f "
             "| %.5f | %.5f | %.0f%% |"
             % (tid, role, r["bl"], r["n_usable"], a["auc"], b["auc"], d,
                a["youden"], b["youden"], a["med_imm"], b["med_imm"],
                100 * a["zero_frac"]))
    emit()

    same = all(r["unknown_same"] for _, _, r in rows)
    emit("**unknown 与口径无关**：逐试次比较两个口径的 NaN 掩膜，%s"
         % ("**%d/%d 个试次逐位相同** ⇒ unknown 占比不受本改动影响（它只来自剪影缺失，"
            "不来自残差口径）。" % (len(rows), len(rows)) if same
            else "**有试次不相同 ⇒ 这与设计不符，先查这条再看别的读数。**"))
    emit()

    rx, cx = dispersion(th_x)
    rs, cs = dispersion(th_s)
    emit("## 门槛离散度（与 DP-073 同式，便于横向对照）")
    emit()
    emit("| 口径 | 极差比 | CV | rho(BL, 门槛) |")
    emit("|---|---|---|---|")
    emit("| 硬 XOR（冻结） | %.1fx | %.1f%% | %+.3f |"
         % (rx, cx, spearman(bls, th_x)))
    emit("| 距离场覆盖率 | %.1fx | %.1f%% | %+.3f |"
         % (rs, cs, spearman(bls, th_s)))
    emit()
    emit("**注意这一列量的不是同一件事**：DP-073 量的是「换分母」，这里量的是"
         "「换分子」。分母不动 ⇒ 若极差比也没动，说明离散度确实来自 BL 而不是分子。")
    emit()

    med = float(np.median(daucs))
    win = float(np.mean([d > 0 for d in daucs]))
    worse = rs > rx * 1.05
    tag, why = verdict(med, win, worse)
    emit("## 判定：%s" % tag)
    emit()
    emit(why)
    emit()
    emit("稳态 ΔAUC：中位 **%+.3f**、范围 %+.3f–%+.3f、正向 **%d/%d**。"
         % (med, min(daucs), max(daucs), sum(d > 0 for d in daucs), len(daucs)))
    emit()
    emit("**这条不是什么**：① 不是验收——AUC 是帧级、G7/G8/G11 是试次级，"
         "不可混用；② 不构成改 θ_mob 的授权——人工标签是 T2/T0 混杂档（DP-048），"
         "门槛列只是参考量；③ 不报秒数——θ_mob 未重标之前报秒数是假精度。")

    if out_path:
        Path(out_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n已写 " + out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
