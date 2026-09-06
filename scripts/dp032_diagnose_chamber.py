#!/usr/bin/env python3
"""DP-032 诊断：软件为什么把有老鼠的隔间判成"从未有动物"。

只读。不改视频、不改标定、不写仓库。三个子模式，结论见
`docs/核验_DP-042切片映射与DP-032隔间定位_2026-09-06.md`：

    source  7 段源视频：find_chambers → 走廊标定 → 分割 → validity 逐隔间判定
    dense   连续 50 帧 × 4 个时间点，排除"抽帧太稀导致标定拿不到运动块"
    clips   27 个切片各自整幅当一个隔间跑同一条链（脚手架，非生产路径）

用法：
    python3 scripts/dp032_diagnose_chamber.py source [视频名...]
    python3 scripts/dp032_diagnose_chamber.py dense
    python3 scripts/dp032_diagnose_chamber.py clips

素材路径用环境变量覆盖：DP_SRC_DIR（源视频）、DP_CLIPS_DIR（切片）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
SRC = Path(os.environ.get("DP_SRC_DIR", Path.home() / "Work/heavy/depression/悬尾"))
CLIPS = Path(os.environ.get("DP_CLIPS_DIR", Path.home() / "Downloads/tst_30clips"))
sys.path.insert(0, str(REPO))

from depressionplex.assay_core import segment as S  # noqa: E402
from depressionplex.assay_core.validity import assess_trial_validity  # noqa: E402

N_CALIB = 12
DENSE_STARTS = (0, 2000, 4500, 7000)
DENSE_N = 50


def probe(p: Path) -> tuple[int, int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
         "-show_entries", "stream=width,height,nb_read_frames",
         "-of", "csv=p=0:s=,", str(p)],
        capture_output=True, text=True, check=True).stdout.strip().split(",")
    return int(out[0]), int(out[1]), int(out[2])


def _decode(args: list[str], w: int, h: int) -> list[np.ndarray]:
    raw = subprocess.run(args, capture_output=True, check=True).stdout
    n = len(raw) // (w * h)
    return [np.frombuffer(raw[i*w*h:(i+1)*w*h], np.uint8).reshape(h, w)
            for i in range(n)]


def frames_even(p: Path, w: int, h: int, nb: int, n: int) -> list[np.ndarray]:
    idx = [round(i * (nb - 1) / (n - 1)) for i in range(n)]
    expr = "+".join(f"eq(n\\,{i})" for i in idx)
    return _decode(["ffmpeg", "-v", "error", "-i", str(p), "-vf", f"select='{expr}'",
                    "-vsync", "0", "-f", "rawvideo", "-pix_fmt", "gray", "-"], w, h)


def frames_seq(p: Path, w: int, h: int, start: int, n: int) -> list[np.ndarray]:
    return _decode(["ffmpeg", "-v", "error", "-i", str(p),
                    "-vf", f"select='gte(n\\,{start})'", "-vsync", "0",
                    "-frames:v", str(n), "-f", "rawvideo", "-pix_fmt", "gray", "-"], w, h)


def run_chain(crops: list[np.ndarray]) -> tuple[S.TapeCorridor | None,
                                                list[float | None],
                                                list[str], list[str]]:
    """走廊标定 + 逐帧分割。返回 (走廊, 面积剖面, flags, 失败原因)。"""
    corr = S.calibrate_tape_corridor(crops) if len(crops) >= 2 else None
    res = [S.segment_animal(c, corridor=corr) for c in crops]
    areas = [float(r.mask.sum()) if r.ok and r.mask is not None else None for r in res]
    flags = sorted({f for r in res for f in r.flags})
    reasons = sorted({r.reason for r in res if not r.ok})
    return corr, areas, flags, reasons


def seal_str(corr: S.TapeCorridor | None) -> str:
    if corr is None:
        return "标定None"
    return f"{'收口' if corr.sealed else '未收口'} 带{corr.band_range}"


def mode_source(names: list[str]) -> None:
    for name in names:
        p = SRC / name
        if not p.exists():
            print(f"[缺文件] {p}")
            continue
        w, h, nb = probe(p)
        gs = frames_even(p, w, h, nb, N_CALIB)
        print(f"\n{'='*78}\n{name}  {w}x{h}  {nb} 帧\n{'='*78}")
        chambers = S.find_chambers(gs[0])
        print(f"find_chambers: {chambers}   整帧 panel_band: "
              f"{S.panel_band(gs[0].astype(float))}")
        profiles: dict[int, list[float | None]] = {}
        for k, (c0, c1) in enumerate(chambers, 1):
            corr, areas, flags, reasons = run_chain([g[:, c0:c1+1] for g in gs])
            profiles[k] = areas
            ok = [a for a in areas if a is not None]
            shown = " ".join("-" if a is None else f"{a:.0f}" for a in areas)
            print(f"\n  隔间{k} x=[{c0},{c1}] 宽{c1-c0+1}  走廊 {seal_str(corr)}")
            print(f"    面积剖面 = {shown}")
            print(f"    {'max=%.0f 中位=%.0f' % (max(ok), np.median(ok)) if ok else '全部无掩膜'}")
            if flags:
                print(f"    flags = {flags}")
            if reasons:
                print(f"    失败原因 = {reasons}")
        print("\n  --- 无走廊对照（corridor=None）---")
        for k, (c0, c1) in enumerate(chambers, 1):
            res = [S.segment_animal(g[:, c0:c1+1]) for g in gs]
            ok = [float(r.mask.sum()) for r in res if r.ok and r.mask is not None]
            print(f"    隔间{k}: {'max=%.0f' % max(ok) if ok else '全 None'}")
        print("\n  --- validity 判定 ---")
        tv = assess_trial_validity(profiles)
        for c in tv.chambers:
            of = "None" if c.occupied_fraction is None else f"{c.occupied_fraction:.3f}"
            print(f"    隔间{c.chamber}: {c.status:<16} max={c.max_area:.0f} "
                  f"thr={c.body_threshold:.0f} 在场占比={of}")
        print(f"    never_occupied={tv.never_occupied}  detached={tv.detached}")


def mode_dense() -> None:
    """连续帧对照：排除稀疏抽帧解释。隔间 3 作对照，隔间 4 是可疑对象。"""
    for name in ("30mg 2周.mp4", "20mg 3周.mp4"):
        p = SRC / name
        w, h, _ = probe(p)
        print(f"\n=== {name} ===")
        for start in DENSE_STARTS:
            gs = frames_seq(p, w, h, start, DENSE_N)
            if len(gs) < 2:
                continue
            chambers = S.find_chambers(gs[0])
            for k in (3, 4):
                if k > len(chambers):
                    continue
                c0, c1 = chambers[k-1]
                corr, areas, _, _ = run_chain([g[:, c0:c1+1] for g in gs])
                ok = [a for a in areas if a is not None]
                stat = f"中位={np.median(ok):.0f}" if ok else "全部无掩膜"
                print(f"  帧{start:>5}..{start+len(gs)-1:<5} 隔间{k} x=[{c0},{c1}] "
                      f"{seal_str(corr)}  None {len(areas)-len(ok)}/{len(areas)}  {stat}")


def mode_clips() -> None:
    """切片整幅当一个隔间。注意：切片上"未收口"是常态（27/27），只看 None 计数。"""
    print(f"{'切片':<34}{'均匀12帧':<28}{'连续40帧(1000起)':<28}")
    for p in sorted(CLIPS.glob("*.mp4")):
        w, h, _ = probe(p)
        cells = []
        for gs in (frames_even(p, w, h, 9000, 12), frames_seq(p, w, h, 1000, 40)):
            corr, areas, _, _ = run_chain(gs)
            ok = [a for a in areas if a is not None]
            cells.append(f"{seal_str(corr).split()[0]} None{len(areas)-len(ok)}"
                         f"/{len(areas)} 中位{np.median(ok) if ok else 0:.0f}")
        print(f"{p.stem:<34}{cells[0]:<28}{cells[1]:<28}")


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "source"
    if mode == "source":
        names = sys.argv[2:] or ["30mg 2周.mp4", "20mg 3周.mp4"]
        mode_source(names)
    elif mode == "dense":
        mode_dense()
    elif mode == "clips":
        mode_clips()
    else:
        sys.exit(f"未知模式 {mode}（source / dense / clips）")


if __name__ == "__main__":
    main()
