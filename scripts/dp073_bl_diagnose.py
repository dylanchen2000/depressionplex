#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DP-073-R 附带产物：`trial_body_length` 到底在量什么。

**为什么需要这个脚本。** DP-073-R 的分母之争里，「同录像中位 BL」看起来比冻结的
「每试次自己的 BL²」好，而好处**几乎全部来自 4 个试次**，且 |ΔJ| 与「该试次 BL
偏离同录像中位的幅度」Spearman **0.765**。最极端的 `30mg_2周-ch2`：BL **49.06 px**，
同录像另两个隔间 25.75 / 28.54 px，隔间宽度只有约 95 px。**一只鼠不可能比同场
同笼的兄弟长 72%** ⇒ 不是体长差异，是 BL 这个量本身在某些隔间上量错了。

本脚本就是去指认量错的方式：把掩膜的**面积**、**x 跨度**、**y 跨度**、
**外接框填充率**逐帧统计出来，和同录像一个正常隔间并排看。

  · 若面积同比变大  ⇒ 掩膜吞进了别的东西（邻间的鼠、柱子、阴影块）。
  · 若面积基本正常、只有 y 跨度暴涨、填充率掉下去 ⇒ **细长附属物**连在体上
    （尾巴、悬挂胶带），主轴长把它算进了「体长」。

实测（26 试次那批，120x272 px 素材）是第二种：ch2 与 ch1 的 x 跨度**完全相同**
（都 11 px），y 跨度 27 → 51（+89%），面积只 174 → 231（+33%），填充率
0.60 → 0.41。⇒ **BL = 体长 + 尾/胶带，且尾巴是否被分割出来在隔间之间不一致。**

这条也顺手解释了 DP-073 原本那个反直觉读数：干净的体长应当让门槛随 BL 走
log-log 斜率 +2，实测是 **-1.08** —— 因为 BL 里混着与体长无关的分割噪声。

**这个脚本不改任何东西，也不构成改分母的授权。** 它指认的是一个独立的缺陷
（见 DP-075），修 BL 的量法才是正解；用「同录像中位」把坏 BL 平掉只是**遮住**
它，所以 DP-073-R 没有把它设成默认。

用法：
    python3 scripts/dp073_bl_diagnose.py <切片目录> <试次1> [试次2 ...]
例：
    python3 scripts/dp073_bl_diagnose.py ~/Downloads/tst_30clips \\
        30mg_2周-ch2 30mg_2周-ch1
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from depressionplex import runner, video                      # noqa: E402
from depressionplex.assay_core import rad                     # noqa: E402

#: 低于这个像素数的掩膜当「没看见」，不进统计（不是 0，是缺测；DP-032）。
_MIN_PIX = 10

#: 填充率低于此值就提示有细长附属物。取 0.5 是因为一个近似椭圆的体块填充率
#: 约 pi/4 ~ 0.785，实测正常隔间 0.60；掉到 0.41 已经不是形变能解释的。
_FILL_WARN = 0.5


def stats(masks: object) -> dict | None:
    """→ 一个隔间的掩膜形状统计。全帧扫描，缺帧跳过而不是补 0。"""
    n = len(masks)                                            # type: ignore[arg-type]
    area, span_x, span_y = [], [], []
    for i in range(n):
        a = masks[i]                                          # type: ignore[index]
        if a is None:
            continue
        ys, xs = np.nonzero(a)
        if ys.size < _MIN_PIX:
            continue
        area.append(float(ys.size))
        span_x.append(float(np.ptp(xs)))
        span_y.append(float(np.ptp(ys)))
    if len(area) < 10:
        return None
    ar = np.asarray(area)
    sx = np.asarray(span_x)
    sy = np.asarray(span_y)
    fill = ar / np.maximum(sx, 1.0) / np.maximum(sy, 1.0)
    return dict(n=n, seen=ar.size, area=ar, span_x=sx, span_y=sy, fill=fill,
                bl=rad.trial_body_length(masks))               # type: ignore[arg-type]


def one(clip: Path) -> dict | None:
    info = video.probe(clip)
    idx = runner.calibration_indices(info.n_frames)
    plan = runner.build_plan(video.frames_at(info, idx), n_chambers=1,
                             calib_indices=tuple(idx))
    seqs = runner.segment_series(video.iter_gray(info), plan)
    masks = seqs.get(plan.chambers[0].index)
    if masks is None or len(masks) == 0:
        return None
    return stats(masks)


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    root = Path(sys.argv[1]).expanduser()
    rows: list[tuple[str, dict]] = []
    for tid in sys.argv[2:]:
        s = one(root / (tid + ".mp4"))
        if s is None:
            print("跳过 %s：掩膜不可用" % tid)
            continue
        rows.append((tid, s))

    if not rows:
        print("一个试次都没跑成 ⇒ 不出结论")
        return 1

    def q(v: np.ndarray, p: float) -> float:
        return float(np.percentile(v, p))

    print("| 试次 | BL | 面积中位 | x 跨度中位 | y 跨度中位 | 填充率中位 | 主轴/宽 |")
    print("|---|---|---|---|---|---|---|")
    for tid, s in rows:
        print("| `%s` | %.2f | %.0f | %.1f | %.1f | %.2f | %.1f |"
              % (tid, s["bl"], q(s["area"], 50), q(s["span_x"], 50),
                 q(s["span_y"], 50), q(s["fill"], 50),
                 q(s["span_y"], 50) / max(q(s["span_x"], 50), 1e-9)))
    print()
    for tid, s in rows:
        f = q(s["fill"], 50)
        if f < _FILL_WARN:
            print("**%s 填充率 %.2f < %.2f** ⇒ 掩膜里有细长附属物（尾/胶带），"
                  "主轴长把它算进了体长 ⇒ 这个 BL 不是体长。" % (tid, f, _FILL_WARN))
    print()
    print("**这条不构成改分母的授权**：正解是修 BL 的量法（DP-075），"
          "不是拿同录像中位把坏 BL 遮住。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
