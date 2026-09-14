#!/usr/bin/env python3
"""把一段录像跑成 trial 级数字（TST / FST）。

    python3 -m depressionplex.cli.analyze 视频.mp4 --assay TST
    python3 -m depressionplex.cli.analyze 视频.mp4 --assay TST --chambers 4 \
        --csv out.csv

输出的每个 immobility 数字都带分母（可评分帧/窗口帧、unknown 占比、
`TrialValidity`）——无分母的指标不可审计，这是我方对 CSI 的差异化，也是
GLP 硬要求。**排除态隔间不产数字**，只产报警行（`score_gate`，DP-028 N1/N3）。

退出码：0 = 至少一个隔间出了数字；2 = 一个都没出（不许静默返回 0 让上游
以为跑过了）；1 = 解码/探测失败。
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import importlib.metadata
import json
import os
import subprocess
import sys
import time
from collections.abc import Collection
from pathlib import Path

from .. import runner, video
from ..assay_core import rules, timeline, trial_report


def _plan_text(info: video.VideoInfo, plan: runner.TrialPlan) -> str:
    L = [f"==== 素材 {info.path.name} ====",
         f"  fps={info.fps:g}（读自文件）｜帧数={info.n_frames}"
         f"（来源 {info.frame_count_source}）｜画幅 {info.width}×{info.height}"
         f"｜时长 {info.duration_s:.2f} s",
         f"  标定抽帧 {len(plan.calib_indices)} 帧："
         f"{list(plan.calib_indices[:6])}{'…' if len(plan.calib_indices) > 6 else ''}",
         f"  隔间 {len(plan.chambers)} 个（结构立柱提案，非人工确认几何）："]
    for ch in plan.chambers:
        s = ("走廊未标定" if ch.corridor is None
             else f"走廊列 {ch.corridor.col_range}、带 {ch.corridor.band_range}、"
                  f"BL估计 {ch.corridor.bl_est if ch.corridor.bl_est else '不可估'}"
                  f"{'、**未收口**' if not ch.corridor.sealed else ''}")
        L.append(f"    ch{ch.index}  列 {ch.col_range}（宽 {ch.width}）｜{s}"
                 f"｜悬挂点 {ch.suspension}")
    for w in plan.warnings:
        L.append(f"  [警告] {w}")
    for cv in plan.trial_validity.chambers:
        L.append(f"  有效性 ch{cv.chamber}: {cv.status}"
                 f"｜在场占比={cv.occupied_fraction}"
                 f"｜不可分割帧占比={cv.unsegmentable_fraction}"
                 f"｜{cv.note or '—'}")
    return "\n".join(L)


CSV_FIELDS = ("trial_id", "assay", "fps", "recording_frames", "window_frames",
              "scorable_frames", "unknown_frames_window", "validity_status",
              "occupied_fraction", "scored", "immobility_s", "immobility_raw_s",
              "mobility_s", "mobility_bouts", "first_mobility_onset_s",
              "gate_messages")


def _row(r: trial_report.TrialReport) -> dict[str, object]:
    mob = r.categories.get("Mobility")
    return {
        "trial_id": r.trial_id, "assay": r.assay, "fps": r.fps,
        "recording_frames": r.recording_frames,
        "window_frames": r.window_frames,
        "scorable_frames": r.scorable_frames_window,
        "unknown_frames_window": r.unknown_frames_window,
        "validity_status": r.validity_status,
        "occupied_fraction": r.occupied_fraction,
        "scored": r.scored,
        # 未放行 ⇒ 留空，**不填 0**：0 会被下游读成"一秒都没不动"。
        "immobility_s": ("" if r.immobility_mirror_pipeline_s is None
                         else round(r.immobility_mirror_pipeline_s, 3)),
        "immobility_raw_s": ("" if r.immobility_mirror_raw_s is None
                             else round(r.immobility_mirror_raw_s, 3)),
        "mobility_s": "" if mob is None else round(mob.seconds_pipeline, 3),
        "mobility_bouts": "" if mob is None else mob.bouts,
        "first_mobility_onset_s": ("" if mob is None or mob.first_onset_s is None
                                   else round(mob.first_onset_s, 3)),
        "gate_messages": " | ".join(r.gate_messages),
    }


def _version_from_pyproject(text: str) -> str | None:
    """从 pyproject.toml 文本里取 **`[project]` 表的** `version`，取不到返回 None。

    刻意认表头：原写法 `line.startswith("version")` 命中任意表里的 version
    ——`[tool.某某]` 下面随手加一行 `version = "9"`，run.json 里记的版本号就变了。
    这里不用 tomllib（3.10 没有），只需要认一个键，逐行扫足够且不引依赖。
    """
    table: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            table = line[1:-1].strip()
            continue
        if table != "project":
            continue
        key, sep, val = line.partition("=")
        if sep and key.strip() == "version":
            return val.split("#")[0].strip().strip('"').strip("'") or None
    return None


def _get_tool_version() -> str:
    """算出这批数字的**这份代码**的版本号；取不到返回 "unknown" 并且说出来。

    先读源码树里的 pyproject.toml：从源码跑时它才是权威——editable 安装下
    `importlib.metadata` 拿到的是安装那一刻的版本，可能和工作树里的代码不是同一份。
    冻结后没有 pyproject.toml，才退回 `importlib.metadata`。

    这里刻意不写 `except Exception: pass`：`tool_version` 是「这批数字是哪份代码算的」
    的唯一凭据，静默变成 "unknown" 比报错危险（DP-052）。所以每一次退化都往 stderr
    写一行原因（stdout 要逐位稳定，不许碰）。
    """
    pyproject = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"
    if pyproject.exists():
        try:
            text = pyproject.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            print(f"[警告] 读不到 {pyproject}：{e}", file=sys.stderr)
        else:
            v = _version_from_pyproject(text)
            if v is not None:
                return v
            print(f"[警告] {pyproject} 的 [project] 表里没有 version", file=sys.stderr)
    try:
        return importlib.metadata.version("depressionplex")
    except importlib.metadata.PackageNotFoundError:
        print("[警告] 版本号取不到（源码树没有 pyproject.toml，包也没安装）"
              "⇒ run.json 的 tool_version 记 unknown", file=sys.stderr)
        return "unknown"


def _get_ffmpeg_version(ffmpeg_path: str) -> str | None:
    """取 ffmpeg -version 第一行。取不到返回 None 并往 stderr 写一行原因。

    照 `_get_tool_version()` 的先例：stdout 要逐位稳定，不许碰；
    退化必须往 stderr 说一行，**不许写空串冒充**。
    """
    try:
        p = subprocess.run([ffmpeg_path, "-version"],
                           capture_output=True, text=True, timeout=5)
        if p.returncode == 0 and p.stdout:
            first_line = p.stdout.splitlines()[0] if p.stdout.splitlines() else ""
            if first_line:
                return first_line
        print(f"[警告] ffmpeg -version 未返回有效输出（退出码 {p.returncode}）",
              file=sys.stderr)
    except FileNotFoundError:
        print(f"[警告] ffmpeg 可执行文件不存在：{ffmpeg_path}", file=sys.stderr)
    except subprocess.TimeoutExpired:
        print(f"[警告] ffmpeg -version 超时（5 秒）", file=sys.stderr)
    except Exception as e:
        print(f"[警告] 取 ffmpeg 版本失败：{e}", file=sys.stderr)
    return None


def _build_run_json(info: video.VideoInfo, plan: runner.TrialPlan,
                    assay: str, skipped: dict[int, str],
                    scored: Collection[int]) -> dict:
    """构造 run.json 数据结构（**CSV 故意不含的上下文**）。

    `scored` 是产出了 CSV 行的隔间号集合，必传：`chamber_validity` 只写不在 CSV 里的
    隔间。理由是架构 §3.5 那句「一个数字只许有一个序列化器」——CSV 已有
    `validity_status` / `occupied_fraction`，而 run.json 这边取的是
    `plan.trial_validity`，CSV 那边取的是 `TrialReport`，是**两个对象**。
    同时写就等于同一个数字有两条来路，哪天其中一条变了没人会发现（DP-054）。
    未产出数字的隔间没有 CSV 行，它的有效性只能在这里说，所以留在这里。
    """
    # numpy 类型需要转成 Python int/float
    def to_native(val):
        if hasattr(val, "item"):  # numpy scalar
            return val.item()
        return val

    # decoder 块：这批帧是哪个解码器解出来的（架构 §3.5，B6 审计包要印）
    ffmpeg_path = video._resolve_ffmpeg_tool("ffmpeg")
    ffprobe_path = video._resolve_ffmpeg_tool("ffprobe")

    # 判断来源：env → bundled → path
    if os.environ.get("DPX_FFMPEG") or os.environ.get("DPX_FFPROBE"):
        decoder_source = "env"
    else:
        # 判断是否来自随包
        is_frozen = getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")
        if is_frozen:
            bundled_dir = Path(sys.executable).parent / "ffmpeg"
        else:
            repo_root = Path(__file__).resolve().parent.parent.parent
            bundled_dir = repo_root / "vendor" / "ffmpeg"

        # 如果 ffmpeg_path 在 bundled_dir 下，就是 bundled
        try:
            Path(ffmpeg_path).relative_to(bundled_dir)
            decoder_source = "bundled"
        except (ValueError, OSError):
            # 不在 bundled_dir 下，就是 PATH
            decoder_source = "path"

    ffmpeg_version = _get_ffmpeg_version(ffmpeg_path)

    scoring_window_s = list(trial_report.ASSAY_WINDOWS[assay])

    chambers_list = []
    for ch in plan.chambers:
        ch_obj = {
            "index": ch.index,
            "col_range": list(ch.col_range),
            "width": ch.width,
            "source": ch.source,
        }
        if ch.corridor is not None:
            ch_obj["corridor"] = {
                "col_range": list(ch.corridor.col_range),
                "band_range": list(ch.corridor.band_range),
                # `if bl_est else None` 会把 0.0 写成 null，把「量出来是 0」和
                # 「量不出来」抹成同一件事。这一层只许问 is None。
                "bl_est": to_native(ch.corridor.bl_est),
                "sealed": ch.corridor.sealed,
            }
        else:
            ch_obj["corridor"] = None
        ch_obj["suspension"] = (None if ch.suspension is None
                                else [to_native(v) for v in ch.suspension])
        chambers_list.append(ch_obj)

    chamber_validity_list = []
    for cv in plan.trial_validity.chambers:
        if cv.chamber in scored:
            continue                      # 有 CSV 行 ⇒ 有效性由 CSV 说，见 docstring
        chamber_validity_list.append({
            "chamber": cv.chamber,
            "status": cv.status,
            "occupied_fraction": to_native(cv.occupied_fraction),
            "unsegmentable_fraction": to_native(cv.unsegmentable_fraction),
            # 不写 `cv.note or ""`：note 是 None（没话说）和 ""（有话说但是空串）
            # 是两件事，抹平了下游就分不出「没测」和「测了没备注」。
            "note": cv.note,
        })

    not_scored_list = []
    for chamber, reason in skipped.items():
        not_scored_list.append({"chamber": chamber, "reason": reason})

    return {
        "schema_version": "1",
        "tool_version": _get_tool_version(),
        "assay": assay,
        "scoring_window_s": scoring_window_s,
        # 实际生效的 FROZEN 判定参数（§3.5 点名要 θ_mob；这里整份都给）。
        # **从 dataclass 现读，不许在这里抄一份字面量**——抄了就是第二个真值，
        # 而这些数字是「这批秒数凭什么这么算」的全部依据（B6 的报告要印 θ_mob）。
        "rules": dataclasses.asdict(rules.TstRulesParams()),
        # decoder 块：审计包必须能回答「这批帧是哪个解码器解出来的」（B6 要印）
        "decoder": {
            "ffmpeg_path": ffmpeg_path,
            "ffprobe_path": ffprobe_path,
            "source": decoder_source,  # "env" | "bundled" | "path"
            "ffmpeg_version": ffmpeg_version,  # None（取不到）| str（第一行）
        },
        "video": {
            "path": str(info.path.resolve()),
            "name": info.path.name,
            "fps": info.fps,
            "n_frames": info.n_frames,
            "frame_count_source": info.frame_count_source,
            "width": info.width,
            "height": info.height,
            "duration_s": info.duration_s,
        },
        "calib_indices": [int(i) for i in plan.calib_indices],
        "chambers": chambers_list,
        "plan_warnings": list(plan.warnings),
        "chamber_validity": chamber_validity_list,
        "not_scored": not_scored_list,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="录像 → trial 级 immobility 数字")
    ap.add_argument("video", type=Path)
    ap.add_argument("--assay", required=True, choices=sorted(trial_report.ASSAY_WINDOWS),
                    help="计分窗口两范式不许抹平：TST 全程 6 min / FST 后 4 min")
    ap.add_argument("--chambers", type=int, default=4)
    ap.add_argument("--calib-frames", type=int, default=runner.N_CALIB_FRAMES)
    ap.add_argument("--trial-prefix", default=None,
                    help="试次号前缀，默认取文件名（每隔间后缀 -chN，左起为序）")
    ap.add_argument("--body-area-prior", type=float, default=None,
                    help="身体级面积绝对先验（硬件规格级）；整批脱落时相对判据不可决")
    ap.add_argument("--csv", type=Path, default=None)
    ap.add_argument("--timeline-csv", type=Path, default=None,
                    help="逐段时间线（Mobility 段，**录像起点**时基）。"
                         "给了才写；与人工侧 export_human_timeline 同一种行形状，"
                         "两张表可直接拼起来比。总量比不出分歧长在哪一段，段比得出")
    ap.add_argument("--progress-json", action="store_true",
                    help="写进度 NDJSON 到 stderr（节流到约 1 行/秒）")
    ap.add_argument("--run-json", type=Path, default=None,
                    help="写上下文 JSON（素材信息、隔间几何、未产出数字的隔间等 CSV 故意不含的东西）")
    args = ap.parse_args(argv)

    # 进度回调（--progress-json 开启时写 NDJSON 到 stderr，节流到约 1 行/秒）
    progress_cb = None
    if args.progress_json:
        last_emit = [0.0]  # 可变容器，用于闭包捕获

        def progress_cb(frame: int, n: int | None) -> None:
            now = time.monotonic()
            # 第一帧和最后一帧必须各出一行；中间帧节流到约 1 行/秒。
            # `frame == n` 在 n 为 None（总帧数未知）时自然为假，不用另写分支；
            # n 也照原样写进 JSON（未知就是 null，不许写 0 冒充数字）。
            if frame == 1 or frame == n or (now - last_emit[0] >= 1.0):
                obj = {"ev": "progress", "frame": frame, "n": n}
                print(json.dumps(obj, ensure_ascii=False), file=sys.stderr, flush=True)
                last_emit[0] = now

    try:
        info, plan, reports, skipped = runner.analyze_video(
            args.video, assay=args.assay, n_chambers=args.chambers,
            trial_prefix=args.trial_prefix, n_calib=args.calib_frames,
            body_area_prior=args.body_area_prior, progress=progress_cb)
    except video.VideoError as e:
        print(f"[解码失败] {e}", file=sys.stderr)
        return 1

    print(_plan_text(info, plan))
    for k in sorted(reports):
        print()
        print(trial_report.trial_report_text(reports[k]))
    for k in sorted(skipped):
        print()
        print(f"==== ch{k}：**未产出数字** ====\n  {skipped[k]}")

    if args.csv:
        with args.csv.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
            w.writeheader()
            for k in sorted(reports):
                w.writerow(_row(reports[k]))
        print(f"\nCSV 已写：{args.csv}（{len(reports)} 行；"
              f"未产出的 {len(skipped)} 个隔间不写进 CSV，见上面的原因）")

    if args.timeline_csv:
        rows: list[dict[str, object]] = []
        for k in sorted(reports):
            rows += timeline.rows_from_report(reports[k])
        n = timeline.write_csv(rows, args.timeline_csv)
        seg = sum(1 for r in rows if r["start_s"] != "")
        print(f"\n时间线已写：{args.timeline_csv}（{n} 行，其中段 {seg} 行，"
              f"说明行 {n - seg} 行）")
        if skipped:
            # 与上面的 CSV 同一处理：未产出的隔间不进表，原因在上面的报告里。
            print(f"  未产出数字的 {len(skipped)} 个隔间不进时间线，"
                  "原因见上面各 ch 的说明——不是悄悄少行")

    if args.run_json:
        # `reports` 的键就是「进了 CSV 的隔间」，传进去让 chamber_validity 避开它们
        run_data = _build_run_json(info, plan, args.assay, skipped, set(reports))
        with args.run_json.open("w", encoding="utf-8") as fh:
            json.dump(run_data, fh, indent=2, ensure_ascii=False)
        print(f"\n上下文已写：{args.run_json}")

    if not reports:
        print("\n[结果] 没有任何隔间产出数字——退出码 2", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
