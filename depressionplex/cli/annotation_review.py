"""Generate a bounded disagreement-review package for paired V2 annotations."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import csv
import hashlib
import io
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from typing import Any, Iterable, Mapping, Sequence

from depressionplex.annotations.contract import validate_document
from depressionplex.annotations.review import (
    ReviewBuildError,
    build_disagreement_analysis,
    build_report_artifact,
    json_ready,
    load_json,
    review_decision_rows,
    sorted_track_summary,
)


TRACK_SUMMARY_FIELDS = (
    "rank",
    "track",
    "track_label",
    "window_frames",
    "agreement_frames",
    "raw_agreement",
    "disagreement_frames",
    "disagreement_rate",
    "calibration_disagreement_frames",
    "legacy_missingness_frames",
    "homogeneous_pair_segments",
    "disagreement_union_runs",
    "median_union_run_frames",
    "p90_union_run_frames",
    "max_union_run_frames",
    "diagnostic_kappa",
    "weighted_kappa",
    "selected_evidence_count",
    "selected_clip_count",
    "formal_gate",
)

SEGMENT_FIELDS = (
    "segment_id",
    "track",
    "track_label",
    "track_kind",
    "start_frame",
    "end_frame",
    "end_frame_exclusive",
    "start_sec",
    "end_sec_exclusive",
    "duration_frames",
    "duration_sec",
    "annotator_a_label",
    "annotator_b_label",
    "directional_pair",
    "label_pair",
    "difference_kind",
    "selection_eligible",
    "overlaps_legacy_missingness",
    "review_anchor_start_frame",
    "review_anchor_end_frame_exclusive",
    "review_anchor_frames",
    "selected",
    "evidence_id",
    "clip_id",
    "clip_file",
    "selection_reason",
)

PAIR_SUMMARY_FIELDS = (
    "track",
    "track_label",
    "annotator_a_label",
    "annotator_b_label",
    "directional_pair",
    "difference_kind",
    "frames",
    "frame_rate",
    "homogeneous_runs",
    "median_run_frames",
    "max_run_frames",
)

LABEL_SUPPORT_FIELDS = (
    "track",
    "track_label",
    "label",
    "annotator_a_frames",
    "annotator_b_frames",
    "annotator_a_rate",
    "annotator_b_rate",
    "frame_difference_b_minus_a",
)

REVIEW_QUEUE_FIELDS = (
    "clip_id",
    "priority",
    "start_frame",
    "end_frame",
    "end_frame_exclusive",
    "start_sec",
    "end_sec_exclusive",
    "duration_frames",
    "duration_sec",
    "affected_tracks",
    "affected_track_labels",
    "evidence_ids",
    "evidence_count",
    "focus_summary",
    "clip_file",
    "review_status",
    "consensus_summary",
    "reviewer",
    "reviewed_at",
    "notes",
)

DECISION_FIELDS = (
    "clip_id",
    "evidence_id",
    "track",
    "track_label",
    "source_start_frame",
    "source_end_frame",
    "source_end_frame_exclusive",
    "full_disagreement_start_frame",
    "full_disagreement_end_frame",
    "full_disagreement_end_frame_exclusive",
    "clip_start_frame",
    "clip_end_frame_exclusive",
    "annotator_a",
    "annotator_a_label",
    "annotator_b",
    "annotator_b_label",
    "boundary_reviewable",
    "boundary_instruction",
    "consensus_label",
    "boundary_decision",
    "sop_rule_or_example",
    "image_quality",
    "reviewer",
    "reviewed_at",
    "notes",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _write_json(path: Path, value: Any) -> None:
    _write_text(
        path,
        json.dumps(json_ready(value), ensure_ascii=False, indent=2, sort_keys=False)
        + "\n",
    )


def _csv_value(value: Any) -> Any:
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value


def _write_csv(
    path: Path,
    rows: Iterable[Mapping[str, Any]],
    fields: Sequence[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_value(row.get(field, "")) for field in fields})
    temporary.replace(path)


def _sql_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _sqlite_type(rows: Sequence[Mapping[str, Any]], field: str) -> str:
    values = [row.get(field) for row in rows if row.get(field) is not None]
    if not values:
        return "TEXT"
    if all(isinstance(value, (bool, int)) and not isinstance(value, float) for value in values):
        return "INTEGER"
    if all(isinstance(value, (bool, int, float)) for value in values):
        return "REAL"
    return "TEXT"


def _write_review_sqlite(path: Path, artifact: Mapping[str, Any]) -> None:
    """Materialize and execute the exact SQL exposed by the report sources."""

    dataset_to_table = {
        "headline_metrics": "review_headline",
        "track_summary": "review_track_summary",
        "review_queue": "review_queue",
    }
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists():
        temporary.unlink()
    connection = sqlite3.connect(temporary)
    try:
        datasets = artifact["snapshot"]["datasets"]
        for dataset, table in dataset_to_table.items():
            rows = list(datasets[dataset])
            fields: list[str] = []
            for row in rows:
                for field in row:
                    if field not in fields:
                        fields.append(field)
            if not fields:
                raise ReviewBuildError(f"{dataset}: cannot materialize an empty-schema table")
            definitions = ", ".join(
                f"{_sql_identifier(field)} {_sqlite_type(rows, field)}" for field in fields
            )
            connection.execute(f"CREATE TABLE {_sql_identifier(table)} ({definitions})")
            placeholders = ", ".join("?" for _ in fields)
            columns = ", ".join(_sql_identifier(field) for field in fields)
            connection.executemany(
                f"INSERT INTO {_sql_identifier(table)} ({columns}) VALUES ({placeholders})",
                [
                    tuple(
                        int(value) if isinstance(value := row.get(field), bool) else value
                        for field in fields
                    )
                    for row in rows
                ],
            )
        connection.commit()

        expected_rows = {
            "review_headline_sql": len(datasets["headline_metrics"]),
            "review_track_summary_sql": len(datasets["track_summary"]),
            "review_queue_sql": len(datasets["review_queue"]),
        }
        for source in artifact["sources"]:
            sql = source.get("query", {}).get("sql")
            if not sql:
                continue
            rows = connection.execute(sql).fetchall()
            expected = expected_rows[source["id"]]
            if len(rows) != expected:
                raise ReviewBuildError(
                    f"{source['id']}: SQL returned {len(rows)} rows; expected {expected}"
                )
    finally:
        connection.close()
    temporary.replace(path)


def _run(command: Sequence[str], *, label: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as error:
        raise ReviewBuildError(f"{label}: executable not found: {command[0]}") from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or str(error)).strip()
        raise ReviewBuildError(f"{label} failed: {detail}") from error


def _parse_crop(value: str) -> tuple[int, int, int, int]:
    try:
        width, height, x, y = (int(part) for part in value.split(":"))
    except (TypeError, ValueError) as error:
        raise argparse.ArgumentTypeError("crop must be WIDTH:HEIGHT:X:Y") from error
    if min(width, height) <= 0 or min(x, y) < 0:
        raise argparse.ArgumentTypeError("crop dimensions must be positive and offsets non-negative")
    return width, height, x, y


def _parse_scale(value: str) -> tuple[int, int]:
    try:
        width, height = (int(part) for part in value.lower().split("x"))
    except (TypeError, ValueError) as error:
        raise argparse.ArgumentTypeError("scale must be WIDTHxHEIGHT") from error
    if min(width, height) <= 0:
        raise argparse.ArgumentTypeError("scale dimensions must be positive")
    return width, height


def _ffprobe_video(ffprobe: str, path: Path) -> dict[str, Any]:
    result = _run(
        (
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-count_frames",
            "-show_entries",
            "stream=nb_read_frames,nb_frames,duration,width,height,r_frame_rate",
            "-of",
            "json",
            str(path),
        ),
        label=f"ffprobe {path.name}",
    )
    payload = json.loads(result.stdout)
    streams = payload.get("streams", [])
    if len(streams) != 1:
        raise ReviewBuildError(f"{path}: expected exactly one probed video stream")
    return streams[0]


def _render_clips(
    analysis: dict[str, Any],
    *,
    video_path: Path,
    output_dir: Path,
    ffmpeg: str,
    ffprobe: str,
    crop: tuple[int, int, int, int],
    scale: tuple[int, int],
) -> list[dict[str, Any]]:
    clips_dir = output_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    expected_clip_names = {
        Path(str(clip["clip_file"])).name for clip in analysis["clips"]
    }
    for stale_clip in clips_dir.glob("clip-*.mp4"):
        if stale_clip.name not in expected_clip_names:
            stale_clip.unlink()
    crop_width, crop_height, crop_x, crop_y = crop
    scale_width, scale_height = scale
    fps = float(analysis["fps"])
    filter_suffix = (
        f"crop={crop_width}:{crop_height}:{crop_x}:{crop_y},"
        f"scale={scale_width}:{scale_height}:flags=lanczos"
    )
    verification_rows: list[dict[str, Any]] = []
    for clip in analysis["clips"]:
        start = int(clip["start_frame"])
        end_exclusive = int(clip["end_frame_exclusive"])
        expected_frames = end_exclusive - start
        clip_path = output_dir / clip["clip_file"]
        clip_path.parent.mkdir(parents=True, exist_ok=True)
        comment = (
            f"source frames [{start},{end_exclusive}); 0-based half-open; "
            f"output frame 0 = source frame {start}; fps={fps:g}"
        )
        video_filter = (
            f"trim=start_frame={start}:end_frame={end_exclusive},"
            f"setpts=PTS-STARTPTS,{filter_suffix}"
        )
        _run(
            (
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(video_path),
                "-map",
                "0:v:0",
                "-vf",
                video_filter,
                "-an",
                "-r",
                f"{fps:g}",
                "-fps_mode",
                "cfr",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-g",
                f"{round(fps):d}",
                "-keyint_min",
                f"{round(fps):d}",
                "-sc_threshold",
                "0",
                "-movflags",
                "+faststart",
                "-metadata",
                f"comment={comment}",
                str(clip_path),
            ),
            label=f"render {clip['clip_id']}",
        )
        stream = _ffprobe_video(ffprobe, clip_path)
        actual_frames = int(stream.get("nb_read_frames") or stream.get("nb_frames") or 0)
        actual_duration = float(stream.get("duration") or 0.0)
        expected_duration = expected_frames / fps
        passed = (
            actual_frames == expected_frames
            and stream.get("r_frame_rate") == f"{round(fps)}/1"
            and int(stream.get("width", 0)) == scale_width
            and int(stream.get("height", 0)) == scale_height
            and abs(actual_duration - expected_duration) <= max(0.001, 0.5 / fps)
        )
        if not passed:
            raise ReviewBuildError(
                f"{clip['clip_id']}: clip verification failed; expected "
                f"{expected_frames} frames/{expected_duration:.3f}s/{scale_width}x{scale_height}, "
                f"got {stream}"
            )
        verification_rows.append(
            {
                "clip_id": clip["clip_id"],
                "clip_file": clip["clip_file"],
                "source_frame_start": start,
                "source_frame_end_exclusive": end_exclusive,
                "expected_frames": expected_frames,
                "actual_frames": actual_frames,
                "expected_duration_sec": expected_duration,
                "actual_duration_sec": actual_duration,
                "width": int(stream["width"]),
                "height": int(stream["height"]),
                "fps": stream["r_frame_rate"],
                "sha256": _sha256_file(clip_path),
                "status": "passed",
            }
        )

    locator_path = output_dir / "mouse1_locator.png"
    locator_filter = (
        "trim=start_frame=0:end_frame=1,setpts=PTS-STARTPTS,"
        f"drawbox=x={crop_x}:y={crop_y}:w={crop_width}:h={crop_height}:"
        "color=yellow@0.9:t=4"
    )
    _run(
        (
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video_path),
            "-map",
            "0:v:0",
            "-vf",
            locator_filter,
            "-frames:v",
            "1",
            str(locator_path),
        ),
        label="render mouse1 locator",
    )
    return verification_rows


def _notebook_code_cell(source: str) -> dict[str, Any]:
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": source,
    }


def _notebook_markdown_cell(source: str) -> dict[str, Any]:
    return {"cell_type": "markdown", "metadata": {}, "source": source}


def _execute_notebook_cells(
    cells: list[dict[str, Any]],
    path: Path,
    *,
    initial_namespace: Mapping[str, Any] | None = None,
) -> None:
    namespace: dict[str, Any] = {"__name__": "__notebook__"}
    if initial_namespace:
        namespace.update(initial_namespace)
    execution_count = 0
    for cell in cells:
        if cell["cell_type"] != "code":
            continue
        execution_count += 1
        output = io.StringIO()
        try:
            with redirect_stdout(output):
                exec(compile(cell["source"], str(path), "exec"), namespace, namespace)
        except Exception as error:
            raise ReviewBuildError(
                f"notebook cell {execution_count} failed: {type(error).__name__}: {error}"
            ) from error
        cell["execution_count"] = execution_count
        text = output.getvalue()
        if text:
            cell["outputs"] = [
                {
                    "name": "stdout",
                    "output_type": "stream",
                    "text": text,
                }
            ]


def _write_notebook(
    path: Path,
    *,
    repository_root: Path,
    annotation_a_path: Path,
    annotation_b_path: Path,
    diagnostic_path: Path | None,
    analysis: Mapping[str, Any],
    mouse: int,
    samples_per_pair: int,
    max_evidence: int,
    clip_seconds: float,
    max_merged_seconds: float,
) -> None:
    headline = analysis["headline"]
    paths_source = "\n".join(
        [
            "from pathlib import Path",
            "from pprint import pprint",
            "import os",
            "import sys",
            "if 'REPOSITORY_ROOT' not in globals():",
            "    REPOSITORY_ROOT = Path(os.environ['DEPRESSIONPLEX_REPOSITORY_ROOT'])",
            "if 'ANNOTATION_A' not in globals():",
            "    ANNOTATION_A = Path(os.environ['DEPRESSIONPLEX_ANNOTATION_A'])",
            "if 'ANNOTATION_B' not in globals():",
            "    ANNOTATION_B = Path(os.environ['DEPRESSIONPLEX_ANNOTATION_B'])",
            "if 'DIAGNOSTIC_REPORT' not in globals():",
            "    diagnostic_value = os.environ.get('DEPRESSIONPLEX_DIAGNOSTIC_REPORT')",
            "    DIAGNOSTIC_REPORT = Path(diagnostic_value) if diagnostic_value else None",
            "sys.path.insert(0, str(REPOSITORY_ROOT))",
            "from depressionplex.annotations.review import load_json, build_disagreement_analysis, sorted_track_summary",
            "print('repository:', REPOSITORY_ROOT.name)",
            "print('annotation A:', ANNOTATION_A.name)",
            "print('annotation B:', ANNOTATION_B.name)",
            "print('diagnostic:', DIAGNOSTIC_REPORT.name if DIAGNOSTIC_REPORT else None)",
        ]
    )
    build_source = "\n".join(
        [
            "annotation_a = load_json(ANNOTATION_A)",
            "annotation_b = load_json(ANNOTATION_B)",
            "diagnostic = load_json(DIAGNOSTIC_REPORT) if DIAGNOSTIC_REPORT else None",
            "analysis = build_disagreement_analysis(",
            "    annotation_a, annotation_b, diagnostic_report=diagnostic,",
            f"    mouse={mouse}, samples_per_pair={samples_per_pair}, max_evidence={max_evidence},",
            f"    clip_seconds={clip_seconds!r}, max_merged_seconds={max_merged_seconds!r},",
            ")",
            "print('trial:', analysis['trial'])",
            "print('frames:', analysis['window_frames'])",
            "print('quality checks:', analysis['quality_checks']['agreement_report_reconciliation'])",
        ]
    )
    results_source = "\n".join(
        [
            "for row in sorted_track_summary(analysis):",
            "    print(f\"{row['track']:<20} disagreement={row['disagreement_rate']:.3%} frames={row['disagreement_frames']:>5} kappa={row['diagnostic_kappa']}\")",
            "print('headline:')",
            "pprint(analysis['headline'])",
        ]
    )
    checks_source = "\n".join(
        [
            "assert analysis['quality_checks']['pair_alignment'] == 'passed'",
            "assert analysis['quality_checks']['canonical_validation'] == 'passed'",
            "assert all(row['status'] == 'passed' for row in analysis['quality_checks']['reconciliation'])",
            "assert analysis['headline']['selected_pair_strata'] == analysis['headline']['calibration_pair_strata']",
            "assert analysis['headline']['legacy_missingness_frames'] == 211",
            "assert analysis['headline']['review_clips_overlapping_legacy_missingness'] == 0",
            "print('all reproducibility and reconciliation assertions passed')",
        ]
    )
    cells = [
        _notebook_markdown_cell(
            "## tl;dr\n\n"
            f"本例共有 **{headline['any_disagreement_frames']}/{analysis['window_frames']} "
            f"帧（{headline['any_disagreement_rate']:.1%}）**至少一个轨道分歧。"
            f"首轮队列覆盖 {headline['selected_pair_strata']}/{headline['calibration_pair_strata']} 个优先无向标签对，"
            f"合并为 **{headline['review_clip_count']} 个片段、{headline['review_seconds']:.1f} 秒**。"
            "211 帧 axis unknown 属于历史缺失，已排除出 SOP 抽样。"
        ),
        _notebook_markdown_cell(
            "## Context & Methods\n\n"
            "目的：把一对 legacy V2.2 标注中的系统性口径差异压缩成可共同裁决的首轮片段。\n\n"
            "### Key Assumptions\n\n"
            "- 两份文件描述同一视频、同一 mouse、同一完整分析窗口。\n"
            "- interval 为闭区间；分析窗口为半开区间。\n"
            "- axis unknown 的 211 帧只代表历史缺失，不代表真实方向判断。\n"
            "- 抽样用于 SOP 校准，不用于估计正式盲标可靠性。"
        ),
        _notebook_markdown_cell("## Data\n\n### 1. Load inputs and analysis code"),
        _notebook_code_cell(paths_source),
        _notebook_markdown_cell("### 2. Validate and expand paired tracks"),
        _notebook_code_cell(build_source),
        _notebook_markdown_cell("## Results\n\n### 3. Inspect track-level evidence"),
        _notebook_code_cell(results_source),
        _notebook_markdown_cell("### 4. Reconcile the highest-impact calculations"),
        _notebook_code_cell(checks_source),
        _notebook_markdown_cell(
            "## Takeaways\n\n"
            "- 三级运动轨道存在系统性的强度档位差，不能只修边界。\n"
            f"- {headline['semantic_only_evidence_count']} 个长分歧只截中段，边界裁决已预填 N/A；第一轮只裁语义/强度。\n"
            "- 第一轮先共同裁决代表片段并把结论写入 SOP 例库；原标注文件保持不变。\n"
            "- 如第一轮仍有未解决口径，再把 `max_evidence` 提高到 18 生成第二轮；无需重看全片。\n"
            "- 正式 blind pilot 与本 legacy 诊断继续分账。"
        ),
    ]
    _execute_notebook_cells(
        cells,
        path,
        initial_namespace={
            "REPOSITORY_ROOT": repository_root,
            "ANNOTATION_A": annotation_a_path,
            "ANNOTATION_B": annotation_b_path,
            "DIAGNOSTIC_REPORT": diagnostic_path,
        },
    )
    notebook = {
        "cells": cells,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python", "version": sys.version.split()[0]},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    if not notebook["cells"] or notebook["nbformat"] != 4:
        raise ReviewBuildError("generated notebook failed structural validation")
    _write_json(path, notebook)


def _readme_text(
    analysis: Mapping[str, Any],
    *,
    annotation_a_path: Path,
    annotation_b_path: Path,
    diagnostic_path: Path | None,
    video_path: Path,
    crop: tuple[int, int, int, int],
    scale: tuple[int, int],
    report_receipt: Mapping[str, Any] | None,
) -> str:
    headline = analysis["headline"]
    verification = (
        report_receipt.get("stages", {}).get("verification", "not_built")
        if report_receipt
        else "not_built"
    )
    report_line = (
        f"- `report.html`：可直接打开的技术报告（portable builder QA: `{verification}`）。\n"
        if report_receipt
        else "- `artifact.json`：canonical 报告输入；传 `--report-builder` 后生成 `report.html`。\n"
    )
    return f"""# DepressionPlex TST 双标分歧复核包

## 先做什么

按 `review_queue.csv` 的顺序播放 `clips/`。每看完一个片段，只在
`review_decisions.csv` 填写 `consensus_label`、`sop_rule_or_example` 和必要备注；
仅在 `boundary_reviewable=yes` 时填写 `boundary_decision`，预填 N/A 的行不要改。
**不要修改两份原标注，也不需要重看整段视频。**

首轮共 {headline['review_clip_count']} 个短片、{headline['review_seconds']:.2f} 秒，
覆盖五个优先轨道中 {headline['selected_pair_strata']}/{headline['calibration_pair_strata']}
个实际分歧无向标签对；比完整 {headline['full_seconds']:.1f} 秒少
{headline['review_reduction_rate']:.1%}。这不是对全部
{headline['calibration_directional_pair_strata']} 个 A→B 方向逐一验收；如果首轮仍不能定规则，
再用完整分歧清单扩第二轮。

## 文件

{report_line}- `artifact.json`：报告的 canonical manifest/snapshot/source 契约。
- `mouse1_locator.png`：黄色框是 mouse1；正式短片只裁这个最左隔间。
- `review_queue.csv`：播放顺序、源帧、轨道和文件名。
- `review_decisions.csv`：共同裁决表；这是同事需要填写的唯一文件。`boundary_reviewable=no`
  的 {headline['semantic_only_evidence_count']} 行只评语义/强度，不能据中段画面推断起止边界。
- `disagreement_segments.csv/.json`：全部恒定标签对分歧段，供追溯和第二轮扩样。
- `track_summary.csv`：逐轨分歧率、连续段、诊断 κ 和 missingness。
- `label_pair_summary.csv`：A→B 方向标签对的帧数和段数。
- `label_support.csv`：两位标注员各标签的使用帧数，可识别系统性档位偏差。
- `analysis_notebook.ipynb`：已按顺序执行并保存输出的可复查分析。
- `review_analysis.sqlite`：报告所展示数据的只读快照；报告内 SQL 已在生成时实际执行。
- `clip_verification.json`：每个 MP4 的帧数、时长、分辨率和 SHA 验收。
- `package_manifest.json`：本包文件 SHA 与输入 provenance。

## 帧语义

- 源标注 interval 是 0-based 闭区间 `[start,end]`。
- 清单和 MP4 裁切使用 0-based 半开区间 `[start,end_exclusive)`。
- 每个 MP4 的第 0 帧严格对应清单的 `start_frame`。
- mouse1 crop 为 `{crop[0]}:{crop[1]}:{crop[2]}:{crop[3]}`，输出放大为
  `{scale[0]}×{scale[1]}`；画面放大不改变帧数或时间。

## 必须保留的边界

- 这两份文件是 `legacy_rater / train / blind=false`；诊断报告
  `formal=false / gate=N/A`。
- `axis_orient` 的 211 帧 `unknown` 来自历史缺失，已从 SOP 抽样和短片窗口排除。
- 本包只用于校准 SOP，不计入 12 例正式独立盲标 pilot。
- `touch_wall`、`tail_grasp` 虽在本例完全一致，但都是全阴性，不能当作可靠性已验证。

## 输入（只读）

- A: `{annotation_a_path.name}`（SHA-256 `{_sha256_file(annotation_a_path)}`）
- B: `{annotation_b_path.name}`（SHA-256 `{_sha256_file(annotation_b_path)}`）
- diagnostic: `{diagnostic_path.name if diagnostic_path else 'not supplied'}`{f'（SHA-256 `{_sha256_file(diagnostic_path)}`）' if diagnostic_path else ''}
- video: `{video_path.name}`（SHA-256 `{_sha256_file(video_path)}`）
"""


def _build_report(
    artifact_path: Path,
    report_path: Path,
    *,
    builder_path: Path,
    node: str,
) -> dict[str, Any]:
    result = _run(
        (
            node,
            str(builder_path),
            "--input",
            str(artifact_path),
            "--output",
            str(report_path),
        ),
        label="portable report delivery",
    )
    try:
        receipt = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ReviewBuildError(
            f"portable report builder returned non-JSON output: {result.stdout!r}"
        ) from error
    if receipt.get("ok") is not True:
        raise ReviewBuildError(f"portable report delivery failed: {receipt}")
    if isinstance(receipt.get("html"), str):
        receipt["html"] = Path(receipt["html"]).name
    return receipt


def _package_manifest(
    output_dir: Path,
    *,
    inputs: Sequence[Path],
    analysis: Mapping[str, Any],
) -> dict[str, Any]:
    files = []
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file() or path.name == "package_manifest.json":
            continue
        files.append(
            {
                "path": path.relative_to(output_dir).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    return {
        "format": "depressionplex.annotation.disagreement-review-package.v1",
        "generated_at": analysis["generated_at"],
        "trial": analysis["trial"],
        "formal": False,
        "gate_policy": "N/A",
        "inputs": [
            {
                "path": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
            for path in inputs
        ],
        "outputs": files,
        "source_policy": "read-only",
    }


def _build_package_direct(args: argparse.Namespace) -> dict[str, Any]:
    annotation_a_path = args.annotation_a.resolve()
    annotation_b_path = args.annotation_b.resolve()
    diagnostic_path = args.agreement_report.resolve() if args.agreement_report else None
    video_path = args.video.resolve()
    output_dir = args.output_dir.resolve()
    repository_root = args.repository_root.resolve()
    for path in (annotation_a_path, annotation_b_path, video_path):
        if not path.is_file():
            raise ReviewBuildError(f"required input does not exist: {path}")
    if diagnostic_path and not diagnostic_path.is_file():
        raise ReviewBuildError(f"diagnostic report does not exist: {diagnostic_path}")

    annotation_a = load_json(annotation_a_path)
    annotation_b = load_json(annotation_b_path)
    diagnostic = load_json(diagnostic_path) if diagnostic_path else None
    validate_document(annotation_a, video_path=video_path)
    validate_document(annotation_b, video_path=video_path)
    analysis = build_disagreement_analysis(
        annotation_a,
        annotation_b,
        diagnostic_report=diagnostic,
        mouse=args.mouse,
        samples_per_pair=args.samples_per_pair,
        max_evidence=args.max_evidence,
        clip_seconds=args.clip_seconds,
        max_merged_seconds=args.max_merged_seconds,
    )
    analysis["input_provenance"] = {
        "annotation_a": {
            "name": annotation_a_path.name,
            "sha256": _sha256_file(annotation_a_path),
        },
        "annotation_b": {
            "name": annotation_b_path.name,
            "sha256": _sha256_file(annotation_b_path),
        },
        "diagnostic_report": (
            {
                "name": diagnostic_path.name,
                "sha256": _sha256_file(diagnostic_path),
            }
            if diagnostic_path
            else None
        ),
        "video": {"name": video_path.name, "sha256": _sha256_file(video_path)},
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "disagreement_segments.json", analysis)
    _write_csv(
        output_dir / "disagreement_segments.csv", analysis["segments"], SEGMENT_FIELDS
    )
    _write_csv(
        output_dir / "track_summary.csv", analysis["track_summary"], TRACK_SUMMARY_FIELDS
    )
    _write_csv(
        output_dir / "label_pair_summary.csv", analysis["pair_summary"], PAIR_SUMMARY_FIELDS
    )
    _write_csv(
        output_dir / "label_support.csv", analysis["label_support"], LABEL_SUPPORT_FIELDS
    )
    _write_csv(
        output_dir / "review_queue.csv", analysis["clips"], REVIEW_QUEUE_FIELDS
    )
    _write_csv(
        output_dir / "review_decisions.csv",
        review_decision_rows(analysis),
        DECISION_FIELDS,
    )

    clip_verification: list[dict[str, Any]] = []
    if not args.skip_clips:
        clip_verification = _render_clips(
            analysis,
            video_path=video_path,
            output_dir=output_dir,
            ffmpeg=args.ffmpeg,
            ffprobe=args.ffprobe,
            crop=args.crop,
            scale=args.scale,
        )
    _write_json(
        output_dir / "clip_verification.json",
        {
            "status": "skipped" if args.skip_clips else "passed",
            "crop": {
                "width": args.crop[0],
                "height": args.crop[1],
                "x": args.crop[2],
                "y": args.crop[3],
            },
            "scale": {"width": args.scale[0], "height": args.scale[1]},
            "clips": clip_verification,
        },
    )

    notebook_path = output_dir / "analysis_notebook.ipynb"
    _write_notebook(
        notebook_path,
        repository_root=repository_root,
        annotation_a_path=annotation_a_path,
        annotation_b_path=annotation_b_path,
        diagnostic_path=diagnostic_path,
        analysis=analysis,
        mouse=args.mouse,
        samples_per_pair=args.samples_per_pair,
        max_evidence=args.max_evidence,
        clip_seconds=args.clip_seconds,
        max_merged_seconds=args.max_merged_seconds,
    )

    artifact = build_report_artifact(analysis)
    artifact_path = output_dir / "artifact.json"
    _write_review_sqlite(output_dir / "review_analysis.sqlite", artifact)
    _write_json(artifact_path, artifact)
    report_receipt: dict[str, Any] | None = None
    if args.report_builder:
        builder_path = args.report_builder.resolve()
        if not builder_path.is_file():
            raise ReviewBuildError(f"report builder does not exist: {builder_path}")
        report_receipt = _build_report(
            artifact_path,
            output_dir / "report.html",
            builder_path=builder_path,
            node=args.node,
        )
        _write_json(output_dir / "report_delivery_receipt.json", report_receipt)

    _write_text(
        output_dir / "README.md",
        _readme_text(
            analysis,
            annotation_a_path=annotation_a_path,
            annotation_b_path=annotation_b_path,
            diagnostic_path=diagnostic_path,
            video_path=video_path,
            crop=args.crop,
            scale=args.scale,
            report_receipt=report_receipt,
        ),
    )
    inputs = [annotation_a_path, annotation_b_path, video_path]
    if diagnostic_path:
        inputs.append(diagnostic_path)
    manifest = _package_manifest(output_dir, inputs=inputs, analysis=analysis)
    _write_json(output_dir / "package_manifest.json", manifest)
    return {
        "ok": True,
        "output_dir": str(output_dir),
        "review_clip_count": analysis["headline"]["review_clip_count"],
        "review_seconds": analysis["headline"]["review_seconds"],
        "selected_pair_strata": analysis["headline"]["selected_pair_strata"],
        "calibration_pair_strata": analysis["headline"]["calibration_pair_strata"],
        "report_verification": (
            report_receipt.get("stages", {}).get("verification")
            if report_receipt
            else "not_built"
        ),
    }


def _require_unmodified_generated_package(output_dir: Path) -> None:
    manifest_path = output_dir / "package_manifest.json"
    if not manifest_path.is_file():
        raise ReviewBuildError(
            "--force only replaces a prior generated package with a valid manifest"
        )
    manifest = load_json(manifest_path)
    if manifest.get("format") != "depressionplex.annotation.disagreement-review-package.v1":
        raise ReviewBuildError("existing output manifest has an unexpected format")
    expected: dict[str, Mapping[str, Any]] = {}
    for row in manifest.get("outputs", []):
        if not isinstance(row, Mapping) or not isinstance(row.get("path"), str):
            raise ReviewBuildError("existing output manifest contains an invalid row")
        relative = str(row["path"])
        if relative.startswith("/") or ".." in Path(relative).parts:
            raise ReviewBuildError("existing output manifest contains an unsafe path")
        expected[relative] = row
    actual = {
        path.relative_to(output_dir).as_posix()
        for path in output_dir.rglob("*")
        if path.is_file() and path.name != "package_manifest.json"
    }
    if actual != set(expected):
        raise ReviewBuildError(
            "existing output differs from its generated manifest; refusing to replace it"
        )
    for relative, row in expected.items():
        path = output_dir / relative
        if path.stat().st_size != row.get("size_bytes") or _sha256_file(path) != row.get(
            "sha256"
        ):
            raise ReviewBuildError(
                f"existing generated file was edited; refusing to replace: {relative}"
            )


def build_package(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = args.output_dir.resolve()
    if output_dir.exists() and not output_dir.is_dir():
        raise ReviewBuildError(f"output path is not a directory: {output_dir}")
    existing_nonempty = output_dir.is_dir() and any(output_dir.iterdir())
    if existing_nonempty:
        if not args.force:
            raise ReviewBuildError(
                f"output directory is not empty; use a new versioned directory: {output_dir}"
            )
        _require_unmodified_generated_package(output_dir)

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.build-", dir=output_dir.parent)
    )
    direct_args = argparse.Namespace(**vars(args))
    direct_args.output_dir = staging_dir
    try:
        result = _build_package_direct(direct_args)
    except Exception:
        shutil.rmtree(staging_dir)
        raise

    backup_dir: Path | None = None
    try:
        if existing_nonempty:
            _require_unmodified_generated_package(output_dir)
        elif output_dir.exists() and any(output_dir.iterdir()):
            raise ReviewBuildError(
                "output directory changed during generation; refusing to replace it"
            )
        if output_dir.exists():
            if any(output_dir.iterdir()):
                backup_dir = Path(
                    tempfile.mkdtemp(
                        prefix=f".{output_dir.name}.previous-", dir=output_dir.parent
                    )
                )
                backup_dir.rmdir()
                output_dir.rename(backup_dir)
            else:
                output_dir.rmdir()
        staging_dir.rename(output_dir)
    except Exception:
        if backup_dir and backup_dir.exists() and not output_dir.exists():
            backup_dir.rename(output_dir)
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        raise
    if backup_dir and backup_dir.exists():
        shutil.rmtree(backup_dir)
    result["output_dir"] = str(output_dir)
    return result


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        description=(
            "Create an auditable, frame-accurate review package from two aligned "
            "DepressionPlex V2 annotations."
        )
    )
    command.add_argument("--annotation-a", type=Path, required=True)
    command.add_argument("--annotation-b", type=Path, required=True)
    command.add_argument("--agreement-report", type=Path)
    command.add_argument("--video", type=Path, required=True)
    command.add_argument("--output-dir", type=Path, required=True)
    command.add_argument(
        "--force",
        action="store_true",
        help=(
            "Atomically replace a prior unmodified generated package. Edited decision "
            "sheets or unknown files are never overwritten."
        ),
    )
    command.add_argument("--repository-root", type=Path, default=Path.cwd())
    command.add_argument("--mouse", type=int, default=1)
    command.add_argument("--samples-per-pair", type=int, default=2)
    command.add_argument(
        "--max-evidence",
        type=int,
        default=11,
        help="Maximum selected semantic disagreement examples (default: 11, one per current primary label-pair stratum).",
    )
    command.add_argument("--clip-seconds", type=float, default=6.0)
    command.add_argument("--max-merged-seconds", type=float, default=10.0)
    command.add_argument(
        "--crop",
        type=_parse_crop,
        default=_parse_crop("104:266:16:0"),
        help="Mouse chamber crop WIDTH:HEIGHT:X:Y.",
    )
    command.add_argument(
        "--scale",
        type=_parse_scale,
        default=_parse_scale("312x798"),
        help="Review clip output size WIDTHxHEIGHT.",
    )
    command.add_argument("--ffmpeg", default=shutil.which("ffmpeg") or "ffmpeg")
    command.add_argument("--ffprobe", default=shutil.which("ffprobe") or "ffprobe")
    command.add_argument("--skip-clips", action="store_true")
    command.add_argument("--report-builder", type=Path)
    command.add_argument("--node", default=shutil.which("node") or "node")
    return command


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = build_package(args)
    except (ReviewBuildError, ValueError, OSError, json.JSONDecodeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
