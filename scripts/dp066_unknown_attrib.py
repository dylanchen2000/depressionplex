#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DP-066：把 `unknown` 按特征拆开——它到底限制了哪个判据？

**为什么问这个。** DP-062 拿运行日志里的「可评分帧 = 窗口 − unknown」当**不动**判据的
分母，得出「7/26 个试次判的不动秒数比可评分秒数还多」「`30mg_2周-ch2` 348.7 s 里
184.7 s 是没看见的」这种结论。但 `TstFeatures.unknown` 是**三个特征的并集**：

    unknown = isnan(residual) | isnan(omega) | isnan(rho_hind)

而 `still = AND(~isnan(residual), residual < θ_mob)` **只依赖 residual 一个**。
rules.py 自己的注释就写了「不能用全局 unknown 一刀切（钟摆序列 rho_hind 全 NaN，
Immobility 仍必须成立）」。⇒ **拿并集当不动的分母是错的**，DP-062 那两个数因此可疑。

DP-065 顺手给了第一个反证：`30mg_2周-ch3` 日志里 unknown 19.40%，而 DP-065 量
`isnan(residual)` 得 **0%**（同一切片、同样 `n_chambers=1`、BL 对得上 26.3492）。
本脚本把这件事量全：**逐特征分别报 NaN 率**，并报出「不动判据真正的分母」。

**这不是重跑 DP-053。** 只量 NaN 率，不出 immobility、不碰任何常数。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from depressionplex import runner, video                      # noqa: E402
from depressionplex.assay_core import rules                   # noqa: E402

#: (切片名, DP-053 日志里的 unknown 占比)。挑跨越整个 unknown 量程的三段：
#: 最高、中段、最低——一段是巧合，三段跨量程才叫规律。
TRIALS = [
    ("30mg_2周-ch2", 51.3, "unknown 最高；DP-062 头条就用它"),
    ("20mg_3周-ch1", 34.6, "unknown 中高"),
    ("10mg_2周-ch4", 8.8, "unknown 最低"),
]


def probe(clip: Path) -> dict | None:
    info = video.probe(clip)
    idx = runner.calibration_indices(info.n_frames)
    plan = runner.build_plan(video.frames_at(info, idx), n_chambers=1,
                             calib_indices=tuple(idx))
    seqs = runner.segment_series(video.iter_gray(info), plan)
    ch = plan.chambers[0]
    masks = seqs.get(ch.index)
    if masks is None or len(masks) == 0 or ch.suspension is None:
        return None
    f = rules.build_tst_features(masks, suspension=ch.suspension, fps=info.fps)
    r = np.isnan(np.asarray(f.residual, dtype=np.float64))
    o = np.isnan(np.asarray(f.omega, dtype=np.float64))
    h = np.isnan(np.asarray(f.rho_hind, dtype=np.float64))
    u = r | o | h
    n = r.size
    return dict(n=n, fps=float(info.fps),
                resid=float(r.mean()), omega=float(o.mean()),
                rho=float(h.mean()), union=float(u.mean()),
                # residual 可用但被并集判成 unknown 的帧 = 被冤枉的不动可判帧
                wronged=float((u & ~r).mean()))


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1])
    out: list[str] = []

    def emit(s: str = "") -> None:
        print(s, flush=True)
        out.append(s)

    emit("# DP-066 `unknown` 逐特征拆分")
    emit()
    emit("`unknown = isnan(residual) | isnan(omega) | isnan(rho_hind)`（三者并集）。")
    emit("而 `still = AND(~isnan(residual), residual < θ_mob)` **只依赖 residual**。")
    emit("⇒ **不动判据真正的分母是 `1 − isnan(residual)`，不是 `1 − unknown`。**")
    emit()
    emit("| 试次 | 日志 unknown | 本次并集 | **residual NaN** | omega NaN | rho_hind NaN | "
         "被并集冤枉的帧 | 不动判据真实分母 |")
    emit("|---|---|---|---|---|---|---|---|")
    rows = []
    for stem, log_unk, why in TRIALS:
        clip = root / (stem + ".mp4")
        print("== %s ==" % stem, flush=True)
        if not clip.exists():
            emit("| %s | %.1f%% | — | — | — | — | — | 素材不存在 |" % (stem, log_unk))
            continue
        try:
            r = probe(clip)
        except Exception as e:                                  # noqa: BLE001
            emit("| %s | %.1f%% | — | — | — | — | — | 失败：%s |"
                 % (stem, log_unk, str(e)[:50]))
            continue
        if r is None:
            emit("| %s | %.1f%% | — | — | — | — | — | 不产数字 |" % (stem, log_unk))
            continue
        rows.append((stem, log_unk, r, why))
        emit("| `%s` | %.1f%% | %.1f%% | **%.2f%%** | %.1f%% | %.1f%% | **%.1f%%** | **%.1f%%** |"
             % (stem, log_unk, 100 * r["union"], 100 * r["resid"],
                100 * r["omega"], 100 * r["rho"], 100 * r["wronged"],
                100 * (1 - r["resid"])))
    if rows:
        emit()
        emit("## 判定")
        emit()
        mr = max(r["resid"] for _, _, r, _ in rows)
        if mr < 0.02:
            emit("⇒ **`residual` 几乎从不缺失**（最高 %.2f%%）。`unknown` 全部来自 "
                 "`omega`/`rho_hind`，而这两个特征**与不动判据无关**。" % (100 * mr))
            emit()
            emit("**DP-062 的两条结论因此撤回：**「7/26 个试次判的不动秒数比可评分秒数还多」"
                 "「`30mg_2周-ch2` 的 348.7 s 里 184.7 s 是没看见的」——两条都是"
                 "**拿三特征并集当单特征分母**算出来的假警报。")
            emit()
            emit("**留下来的是一个窄得多、但真实的报告缺陷**：运行日志把「占可评分 x%%」"
                 "印在主口径那一行上，用的是并集分母 ⇒ **那个百分比的分母是错的**"
                 "（分子只需要 residual）。这是**报告层的口径标注问题**，"
                 "不是「unknown 被算成不动」。DP-054 应按这个更窄的版本重写。")
        else:
            emit("⇒ `residual` 缺失率最高 %.2f%%，不可忽略；DP-062 的方向部分成立，"
                 "但仍需按 residual 分母重算，不能用并集。" % (100 * mr))
        emit()
        emit("**没动任何常数、没改判据。** 本脚本只数 NaN。")
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text("\n".join(out) + "\n", encoding="utf-8")
        print("\n已写出 " + sys.argv[2])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
