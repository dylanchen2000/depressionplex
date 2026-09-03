#!/usr/bin/env python3
"""DP-013：LOVO-CV 框架演示——合成真值跑通，人工数据接入在 DP-014。

默认构造与交付集同构的合成数据（7 视频 × 4 隔间 − 1 = 27 试次，
缺 30mg_2周-ch4 沿用 DP-005 事实），跑完整 LOVO-CV 并打印：
每折 θ_mob、G2（跨折变异系数）、样本外 r、Bland-Altman。

    python3 cli/lovo_cv_demo.py                 # 360 s × 10 fps 全量口径
    python3 cli/lovo_cv_demo.py --window 60     # 快跑
    python3 cli/lovo_cv_demo.py --out predictions.csv

**读结果须知**：这里的 θ 数值、r、CV 全是合成量级的管道演示，不构成 G2/G7
证据；真 G2 用人工真值（DP-014）才算。合成 CV 若 >15%，只说明框架对折间
异质性敏感（这正是 G2 要考的属性），不是"门槛失败"。
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from depressionplex import lovo_cv as L


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--window", type=float, default=360.0,
                    help="试次窗口秒数（工具 360 s 硬收口口径）")
    ap.add_argument("--fps", type=float, default=10.0,
                    help="合成帧率；越高拟合越慢（默认 10 足够验管道）")
    ap.add_argument("--seed", type=int, default=20260903)
    ap.add_argument("--coarse", action="store_true",
                    help="只用 31 点粗网格（快，θ 分辨率 0.0015）")
    ap.add_argument("--out", metavar="CSV", help="把样本外预测表写到该路径")
    args = ap.parse_args()

    t0 = time.time()
    samples = L.synthetic_lovo_trials(window_s=args.window, fps=args.fps,
                                      seed=args.seed)
    print(f"合成数据：{len(samples)} 试次 × {len({s.video for s in samples})} 视频"
          f"（窗口 {args.window:.0f} s @ {args.fps:g} fps，seed={args.seed}）"
          f"，用时 {time.time() - t0:.1f} s\n")

    t0 = time.time()
    res = L.lovo_cv(samples, theta_grid=L._default_grid() if args.coarse else None)
    print(res.summary())
    print(f"\n（LOVO 全程用时 {time.time() - t0:.1f} s；"
          f"拟合只动了 {L.FITTED_PARAM}，bout 参数保持 FROZEN）")

    if args.out:
        rows = L.prediction_rows(res)
        out = Path(args.out)
        with out.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"预测表（G9 口径，{len(rows)} 行）→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
