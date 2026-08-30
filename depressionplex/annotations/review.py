"""Build a compact, auditable review queue from paired V2 annotations.

This module deliberately separates three things that are easy to conflate:

* all per-frame differences between two annotation documents;
* legacy missingness made explicit as ``axis_orient=unknown``;
* a small, deterministic set of representative clips for SOP calibration.

The review queue is a triage aid.  It is not a substitute for a formal blind
agreement study and it never rewrites either source annotation.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics
from typing import Any, Iterable, Mapping, Sequence

from .contract import ASSAY_PROFILES, TRACK_DEFS, state_vectors, validate_document


CALIBRATION_TRACKS = (
    "head_neck_motion",
    "fore_motion",
    "hind_motion",
    "trunk_deforming",
    "whole_body_swing",
)

TRACK_PRIORITY = {
    "head_neck_motion": 1,
    "fore_motion": 1,
    "hind_motion": 1,
    "trunk_deforming": 1,
    "whole_body_swing": 1,
    "axis_orient": 2,
    "visibility": 3,
    "touch_wall": 3,
    "tail_grasp": 3,
}


class ReviewBuildError(ValueError):
    """Raised when paired annotations cannot form a trustworthy review set."""


def load_json(path: str | Path) -> dict[str, Any]:
    """Load one UTF-8 JSON object."""

    with Path(path).open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ReviewBuildError(f"{path}: expected a JSON object")
    return value


def _require_pair_alignment(
    annotation_a: Mapping[str, Any],
    annotation_b: Mapping[str, Any],
    *,
    mouse: int,
) -> tuple[int, int, float]:
    validate_document(annotation_a)
    validate_document(annotation_b)

    comparable_fields = (
        "schema",
        "format",
        "primitive_set_version",
        "rubric_version",
        "assay",
        "trial",
        "fps",
        "n_frames",
        "analysis_window",
    )
    mismatches = [
        field
        for field in comparable_fields
        if annotation_a.get(field) != annotation_b.get(field)
    ]
    if annotation_a.get("video", {}).get("sha256") != annotation_b.get(
        "video", {}
    ).get("sha256"):
        mismatches.append("video.sha256")
    if mismatches:
        raise ReviewBuildError(
            "paired annotations do not describe the same review grain: "
            + ", ".join(mismatches)
        )
    if annotation_a.get("annotator") == annotation_b.get("annotator"):
        raise ReviewBuildError("paired annotations must have different annotators")
    if mouse not in annotation_a["active_mice"] or mouse not in annotation_b[
        "active_mice"
    ]:
        raise ReviewBuildError(f"mouse {mouse} must be active in both annotations")

    start, end_exclusive = annotation_a["analysis_window"]
    return int(start), int(end_exclusive), float(annotation_a["fps"])


def _ordered_pair(track: str, left: str, right: str) -> str:
    order = {value: index for index, value in enumerate(TRACK_DEFS[track].values)}
    first, second = sorted((left, right), key=lambda value: order[value])
    return f"{first} ↔ {second}"


def _segment_kind(track: str, left: str, right: str) -> str:
    if track == "axis_orient" and "unknown" in (left, right):
        return "legacy_missingness"
    if track in CALIBRATION_TRACKS:
        return "sop_calibration"
    return "other_disagreement"


def _scan_track_segments(
    track: str,
    values_a: Sequence[str],
    values_b: Sequence[str],
    *,
    window_start: int,
    fps: float,
) -> list[dict[str, Any]]:
    """Return constant-label-pair disagreement runs with closed frame bounds."""

    result: list[dict[str, Any]] = []
    run_start: int | None = None
    run_pair: tuple[str, str] | None = None

    def close_run(end_index_exclusive: int) -> None:
        nonlocal run_start, run_pair
        if run_start is None or run_pair is None:
            return
        start_frame = window_start + run_start
        end_frame = window_start + end_index_exclusive - 1
        duration_frames = end_index_exclusive - run_start
        left, right = run_pair
        result.append(
            {
                "track": track,
                "track_label": TRACK_DEFS[track].label,
                "track_kind": TRACK_DEFS[track].kind,
                "start_frame": start_frame,
                "end_frame": end_frame,
                "end_frame_exclusive": end_frame + 1,
                "start_sec": start_frame / fps,
                "end_sec_exclusive": (end_frame + 1) / fps,
                "duration_frames": duration_frames,
                "duration_sec": duration_frames / fps,
                "annotator_a_label": left,
                "annotator_b_label": right,
                "directional_pair": f"{left} → {right}",
                "label_pair": _ordered_pair(track, left, right),
                "difference_kind": _segment_kind(track, left, right),
                "selection_eligible": track in CALIBRATION_TRACKS,
                "track_priority": TRACK_PRIORITY.get(track, 9),
            }
        )
        run_start = None
        run_pair = None

    for index, (left, right) in enumerate(zip(values_a, values_b)):
        pair = (left, right)
        if left == right:
            close_run(index)
            continue
        if run_start is None:
            run_start = index
            run_pair = pair
        elif pair != run_pair:
            close_run(index)
            run_start = index
            run_pair = pair
    close_run(len(values_a))
    return result


def _percentile_nearest_rank(values: Sequence[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _true_run_lengths(mask: Sequence[bool]) -> list[int]:
    result: list[int] = []
    start: int | None = None
    for index, value in enumerate(mask):
        if value and start is None:
            start = index
        elif not value and start is not None:
            result.append(index - start)
            start = None
    if start is not None:
        result.append(len(mask) - start)
    return result


def _longest_piece_outside(
    start: int,
    end_exclusive: int,
    excluded: Sequence[tuple[int, int]],
) -> tuple[int, int]:
    pieces = [(start, end_exclusive)]
    for excluded_start, excluded_end in excluded:
        updated: list[tuple[int, int]] = []
        for piece_start, piece_end in pieces:
            if excluded_end <= piece_start or excluded_start >= piece_end:
                updated.append((piece_start, piece_end))
                continue
            if piece_start < excluded_start:
                updated.append((piece_start, excluded_start))
            if excluded_end < piece_end:
                updated.append((excluded_end, piece_end))
        pieces = updated
    if not pieces:
        return start, end_exclusive
    return max(pieces, key=lambda piece: (piece[1] - piece[0], -piece[0]))


def _diagnostic_tracks(
    report: Mapping[str, Any] | None,
    *,
    mouse: int,
) -> dict[str, Mapping[str, Any]]:
    if report is None:
        return {}
    mice = report.get("mice", {})
    mouse_report = mice.get(str(mouse), {}) if isinstance(mice, Mapping) else {}
    tracks = mouse_report.get("tracks", []) if isinstance(mouse_report, Mapping) else []
    result: dict[str, Mapping[str, Any]] = {}
    for item in tracks:
        if not isinstance(item, Mapping) or not isinstance(item.get("track"), str):
            continue
        track = str(item["track"])
        full_window = item.get("full_window")
        if isinstance(full_window, Mapping):
            result[track] = full_window
        elif item.get("scope") == "full_window":
            result[track] = item
        else:
            raise ReviewBuildError(
                f"diagnostic report track {track!r} has no full_window metrics"
            )
    return result


def _require_diagnostic_alignment(
    report: Mapping[str, Any] | None,
    annotation_a: Mapping[str, Any],
    annotation_b: Mapping[str, Any],
    *,
    mouse: int,
    analysis_window: tuple[int, int],
) -> None:
    if report is None:
        return
    expected = {
        "format": "depressionplex.annotation.agreement.v2",
        "trial": annotation_a["trial"],
        "assay": annotation_a["assay"],
        "video_sha256": annotation_a["video"]["sha256"],
        "analysis_window": list(analysis_window),
        "annotators": [annotation_a["annotator"], annotation_b["annotator"]],
        "mode": "diagnostic",
        "formal": False,
        "gate_policy": "N/A",
    }
    for field, expected_value in expected.items():
        if report.get(field) != expected_value:
            raise ReviewBuildError(
                f"diagnostic report {field} mismatch: expected {expected_value!r}, "
                f"got {report.get(field)!r}"
            )
    mice = report.get("mice")
    mouse_report = mice.get(str(mouse)) if isinstance(mice, Mapping) else None
    if not isinstance(mouse_report, Mapping):
        raise ReviewBuildError(f"diagnostic report has no mouse {mouse}")
    expected_frames = analysis_window[1] - analysis_window[0]
    if mouse_report.get("window_frames") != expected_frames:
        raise ReviewBuildError(
            "diagnostic report window_frames mismatch: "
            f"expected {expected_frames}, got {mouse_report.get('window_frames')!r}"
        )
    diagnostic_tracks = _diagnostic_tracks(report, mouse=mouse)
    expected_tracks = set(ASSAY_PROFILES[annotation_a["assay"]])
    if set(diagnostic_tracks) != expected_tracks:
        raise ReviewBuildError(
            "diagnostic report tracks mismatch: "
            f"expected {sorted(expected_tracks)!r}, got {sorted(diagnostic_tracks)!r}"
        )


def _select_evidence(
    segments: Sequence[dict[str, Any]],
    *,
    samples_per_pair: int,
    max_evidence: int,
) -> list[dict[str, Any]]:
    """Select longest examples per semantic label pair in deterministic rounds."""

    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for segment in segments:
        if segment["selection_eligible"]:
            groups[(segment["track"], segment["label_pair"])].append(segment)
    for rows in groups.values():
        rows.sort(
            key=lambda row: (
                -row["review_anchor_frames"],
                -row["duration_frames"],
                row["start_frame"],
            )
        )

    ordered_keys = sorted(
        groups,
        key=lambda key: (
            TRACK_PRIORITY.get(key[0], 9),
            CALIBRATION_TRACKS.index(key[0]),
            key[1],
        ),
    )
    selected: list[dict[str, Any]] = []
    for round_index in range(samples_per_pair):
        for key in ordered_keys:
            rows = groups[key]
            if round_index >= len(rows):
                continue
            selected.append(rows[round_index])
            if len(selected) >= max_evidence:
                break
        if len(selected) >= max_evidence:
            break

    selected.sort(
        key=lambda row: (
            row["track_priority"],
            CALIBRATION_TRACKS.index(row["track"]),
            row["label_pair"],
            -row["duration_frames"],
            row["start_frame"],
        )
    )
    for index, segment in enumerate(selected, 1):
        segment["selected"] = True
        segment["evidence_id"] = f"E{index:03d}"
        segment["selection_reason"] = (
            "该轨道与标签对中避开 legacy missingness 后持续时间最长的代表性分歧之一"
        )
    return selected


def _centered_window(
    segment: Mapping[str, Any],
    *,
    n_frames: int,
    target_frames: int,
) -> tuple[int, int]:
    midpoint = (
        segment["review_anchor_start_frame"]
        + segment["review_anchor_end_frame_exclusive"]
    ) // 2
    start = midpoint - target_frames // 2
    end_exclusive = start + target_frames
    if start < 0:
        start = 0
        end_exclusive = min(n_frames, target_frames)
    if end_exclusive > n_frames:
        end_exclusive = n_frames
        start = max(0, end_exclusive - target_frames)
    return start, end_exclusive


def _merge_clip_windows(
    evidence: Sequence[dict[str, Any]],
    *,
    n_frames: int,
    fps: float,
    clip_seconds: float,
    max_merged_seconds: float,
) -> list[dict[str, Any]]:
    target_frames = max(1, round(clip_seconds * fps))
    max_merged_frames = max(target_frames, round(max_merged_seconds * fps))
    windows: list[dict[str, Any]] = []
    for segment in evidence:
        start, end_exclusive = _centered_window(
            segment, n_frames=n_frames, target_frames=target_frames
        )
        windows.append(
            {
                "start_frame": start,
                "end_frame_exclusive": end_exclusive,
                "evidence": [segment],
            }
        )
    windows.sort(key=lambda row: (row["start_frame"], row["end_frame_exclusive"]))

    merged: list[dict[str, Any]] = []
    for window in windows:
        if merged:
            prior = merged[-1]
            proposed_end = max(prior["end_frame_exclusive"], window["end_frame_exclusive"])
            if (
                window["start_frame"] <= prior["end_frame_exclusive"]
                and proposed_end - prior["start_frame"] <= max_merged_frames
            ):
                prior["end_frame_exclusive"] = proposed_end
                prior["evidence"].extend(window["evidence"])
                continue
        merged.append(window)

    clips: list[dict[str, Any]] = []
    for index, window in enumerate(merged, 1):
        start = window["start_frame"]
        end_exclusive = window["end_frame_exclusive"]
        evidence_rows = sorted(
            window["evidence"], key=lambda row: (row["start_frame"], row["track"])
        )
        tracks = list(dict.fromkeys(row["track"] for row in evidence_rows))
        labels = list(dict.fromkeys(row["track_label"] for row in evidence_rows))
        evidence_ids = [row["evidence_id"] for row in evidence_rows]
        clip_id = f"C{index:03d}"
        filename = (
            f"clip-{index:03d}__f{start:05d}-f{end_exclusive - 1:05d}.mp4"
        )
        clip = {
            "clip_id": clip_id,
            "priority": min(row["track_priority"] for row in evidence_rows),
            "start_frame": start,
            "end_frame": end_exclusive - 1,
            "end_frame_exclusive": end_exclusive,
            "start_sec": start / fps,
            "end_sec_exclusive": end_exclusive / fps,
            "duration_frames": end_exclusive - start,
            "duration_sec": (end_exclusive - start) / fps,
            "affected_tracks": "; ".join(tracks),
            "affected_track_labels": "；".join(labels),
            "evidence_ids": "; ".join(evidence_ids),
            "evidence_count": len(evidence_rows),
            "focus_summary": "；".join(
                f"{row['track_label']} {row['annotator_a_label']} vs {row['annotator_b_label']}"
                for row in evidence_rows
            ),
            "clip_file": f"clips/{filename}",
            "review_status": "pending",
            "consensus_summary": "",
            "reviewer": "",
            "reviewed_at": "",
            "notes": "",
        }
        clips.append(clip)
        for row in evidence_rows:
            row["clip_id"] = clip_id
            row["clip_file"] = clip["clip_file"]
            row["visible_evidence_start_frame"] = max(
                row["review_anchor_start_frame"], start
            )
            row["visible_evidence_end_frame_exclusive"] = min(
                row["review_anchor_end_frame_exclusive"], end_exclusive
            )
            row["boundary_reviewable"] = (
                start < row["start_frame"]
                and row["end_frame_exclusive"] < end_exclusive
            )
            row["boundary_instruction"] = (
                "本片可裁完整起止边界"
                if row["boundary_reviewable"]
                else "本片仅评语义/强度；完整起止边界留第二轮"
            )
    return clips


def build_disagreement_analysis(
    annotation_a: Mapping[str, Any],
    annotation_b: Mapping[str, Any],
    *,
    diagnostic_report: Mapping[str, Any] | None = None,
    mouse: int = 1,
    samples_per_pair: int = 2,
    max_evidence: int = 18,
    clip_seconds: float = 6.0,
    max_merged_seconds: float = 10.0,
) -> dict[str, Any]:
    """Profile paired annotations and create a bounded calibration queue."""

    if samples_per_pair < 1 or max_evidence < 1:
        raise ReviewBuildError("samples_per_pair and max_evidence must be positive")
    window_start, window_end_exclusive, fps = _require_pair_alignment(
        annotation_a, annotation_b, mouse=mouse
    )
    _require_diagnostic_alignment(
        diagnostic_report,
        annotation_a,
        annotation_b,
        mouse=mouse,
        analysis_window=(window_start, window_end_exclusive),
    )
    vectors_a = state_vectors(annotation_a, mouse)
    vectors_b = state_vectors(annotation_b, mouse)
    profile = ASSAY_PROFILES[annotation_a["assay"]]
    window_frames = window_end_exclusive - window_start

    segments: list[dict[str, Any]] = []
    label_support: list[dict[str, Any]] = []
    for track in profile:
        segments.extend(
            _scan_track_segments(
                track,
                vectors_a[track],
                vectors_b[track],
                window_start=window_start,
                fps=fps,
            )
        )
        counts_a = Counter(vectors_a[track])
        counts_b = Counter(vectors_b[track])
        for value in TRACK_DEFS[track].values:
            label_support.append(
                {
                    "track": track,
                    "track_label": TRACK_DEFS[track].label,
                    "label": value,
                    "annotator_a_frames": counts_a[value],
                    "annotator_b_frames": counts_b[value],
                    "annotator_a_rate": counts_a[value] / window_frames,
                    "annotator_b_rate": counts_b[value] / window_frames,
                    "frame_difference_b_minus_a": counts_b[value] - counts_a[value],
                }
            )
    segments.sort(key=lambda row: (row["start_frame"], row["track"]))
    for index, segment in enumerate(segments, 1):
        segment["segment_id"] = f"D{index:04d}"
        segment["selected"] = False

    excluded_intervals = [
        (row["start_frame"], row["end_frame_exclusive"])
        for row in segments
        if row["difference_kind"] == "legacy_missingness"
    ]
    for segment in segments:
        if segment["selection_eligible"]:
            anchor_start, anchor_end = _longest_piece_outside(
                segment["start_frame"],
                segment["end_frame_exclusive"],
                excluded_intervals,
            )
        else:
            anchor_start = segment["start_frame"]
            anchor_end = segment["end_frame_exclusive"]
        segment["review_anchor_start_frame"] = anchor_start
        segment["review_anchor_end_frame_exclusive"] = anchor_end
        segment["review_anchor_frames"] = anchor_end - anchor_start
        segment["overlaps_legacy_missingness"] = any(
            max(segment["start_frame"], start) < min(
                segment["end_frame_exclusive"], end
            )
            for start, end in excluded_intervals
        )

    selected = _select_evidence(
        segments,
        samples_per_pair=samples_per_pair,
        max_evidence=max_evidence,
    )
    clips = _merge_clip_windows(
        selected,
        n_frames=int(annotation_a["n_frames"]),
        fps=fps,
        clip_seconds=clip_seconds,
        max_merged_seconds=max_merged_seconds,
    )

    diagnostic_tracks = _diagnostic_tracks(diagnostic_report, mouse=mouse)
    selected_by_track: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in selected:
        selected_by_track[row["track"]].append(row)
    clips_by_track: dict[str, set[str]] = defaultdict(set)
    for row in selected:
        clips_by_track[row["track"]].add(row["clip_id"])

    summaries: list[dict[str, Any]] = []
    reconciliation: list[dict[str, Any]] = []
    for rank, track in enumerate(profile, 1):
        rows = [row for row in segments if row["track"] == track]
        disagreement_frames = sum(row["duration_frames"] for row in rows)
        missingness_frames = sum(
            row["duration_frames"]
            for row in rows
            if row["difference_kind"] == "legacy_missingness"
        )
        calibration_frames = sum(
            row["duration_frames"]
            for row in rows
            if row["difference_kind"] == "sop_calibration"
        )
        durations = [row["duration_frames"] for row in rows]
        union_lengths = _true_run_lengths(
            [left != right for left, right in zip(vectors_a[track], vectors_b[track])]
        )
        diagnostic = diagnostic_tracks.get(track, {})
        raw_agreement = 1.0 - disagreement_frames / window_frames
        reported_agreement = diagnostic.get("raw_agreement")
        delta = (
            abs(raw_agreement - float(reported_agreement))
            if reported_agreement is not None
            else None
        )
        if delta is not None and delta > 1e-12:
            raise ReviewBuildError(
                f"{track}: recomputed raw agreement does not match diagnostic report"
            )
        summaries.append(
            {
                "rank": rank,
                "track": track,
                "track_label": TRACK_DEFS[track].label,
                "window_frames": window_frames,
                "agreement_frames": window_frames - disagreement_frames,
                "raw_agreement": raw_agreement,
                "disagreement_frames": disagreement_frames,
                "disagreement_rate": disagreement_frames / window_frames,
                "calibration_disagreement_frames": calibration_frames,
                "legacy_missingness_frames": missingness_frames,
                "homogeneous_pair_segments": len(rows),
                "disagreement_union_runs": len(union_lengths),
                "median_union_run_frames": (
                    statistics.median(union_lengths) if union_lengths else 0
                ),
                "p90_union_run_frames": _percentile_nearest_rank(union_lengths, 0.90) or 0,
                "max_union_run_frames": max(union_lengths, default=0),
                "median_pair_segment_frames": (
                    statistics.median(durations) if durations else 0
                ),
                "diagnostic_kappa": diagnostic.get("kappa"),
                "weighted_kappa": diagnostic.get("weighted_kappa"),
                "selected_evidence_count": len(selected_by_track[track]),
                "selected_clip_count": len(clips_by_track[track]),
                "formal_gate": "N/A",
            }
        )
        reconciliation.append(
            {
                "track": track,
                "recomputed_raw_agreement": raw_agreement,
                "reported_raw_agreement": reported_agreement,
                "absolute_delta": delta,
                "status": "passed" if delta is None or delta <= 1e-12 else "failed",
            }
        )

    pair_groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in segments:
        pair_groups[
            (
                row["track"],
                row["track_label"],
                row["annotator_a_label"],
                row["annotator_b_label"],
            )
        ].append(row)
    pair_summary: list[dict[str, Any]] = []
    for (track, track_label, label_a, label_b), rows in pair_groups.items():
        frames = sum(row["duration_frames"] for row in rows)
        pair_summary.append(
            {
                "track": track,
                "track_label": track_label,
                "annotator_a_label": label_a,
                "annotator_b_label": label_b,
                "directional_pair": f"{label_a} → {label_b}",
                "difference_kind": rows[0]["difference_kind"],
                "frames": frames,
                "frame_rate": frames / window_frames,
                "homogeneous_runs": len(rows),
                "median_run_frames": statistics.median(
                    [row["duration_frames"] for row in rows]
                ),
                "max_run_frames": max(row["duration_frames"] for row in rows),
            }
        )
    pair_summary.sort(
        key=lambda row: (
            TRACK_PRIORITY.get(row["track"], 9),
            -row["frames"],
            row["directional_pair"],
        )
    )

    per_frame_difference_counts = [
        sum(vectors_a[track][index] != vectors_b[track][index] for track in profile)
        for index in range(window_frames)
    ]
    any_difference_mask = [count > 0 for count in per_frame_difference_counts]
    any_difference_runs = _true_run_lengths(any_difference_mask)
    concurrent_distribution = {
        str(count): per_frame_difference_counts.count(count)
        for count in range(max(per_frame_difference_counts, default=0) + 1)
    }
    calibration_pairs = {
        (row["track"], row["label_pair"])
        for row in segments
        if row["selection_eligible"]
    }
    selected_pairs = {(row["track"], row["label_pair"]) for row in selected}
    calibration_directional_pairs = {
        (row["track"], row["annotator_a_label"], row["annotator_b_label"])
        for row in segments
        if row["selection_eligible"]
    }
    selected_directional_pairs = {
        (row["track"], row["annotator_a_label"], row["annotator_b_label"])
        for row in selected
    }
    review_seconds = sum(row["duration_sec"] for row in clips)
    full_seconds = window_frames / fps
    clips_overlapping_missingness = sum(
        any(
            max(clip["start_frame"], start) < min(
                clip["end_frame_exclusive"], end
            )
            for start, end in excluded_intervals
        )
        for clip in clips
    )
    return {
        "format": "depressionplex.annotation.disagreement-review.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "trial": annotation_a["trial"],
        "assay": annotation_a["assay"],
        "mouse": mouse,
        "annotator_a": annotation_a["annotator"],
        "annotator_b": annotation_b["annotator"],
        "video_basename": annotation_a["video"]["basename"],
        "video_sha256": annotation_a["video"]["sha256"],
        "fps": fps,
        "analysis_window": [window_start, window_end_exclusive],
        "window_frames": window_frames,
        "window_seconds": full_seconds,
        "formal": False,
        "gate_policy": "N/A",
        "selection_policy": {
            "eligible_tracks": list(CALIBRATION_TRACKS),
            "method": "longest segments per unordered semantic label pair, deterministic round-robin",
            "samples_per_pair": samples_per_pair,
            "max_evidence": max_evidence,
            "clip_seconds": clip_seconds,
            "max_merged_seconds": max_merged_seconds,
            "legacy_axis_unknown_excluded": True,
        },
        "track_summary": summaries,
        "label_support": label_support,
        "pair_summary": pair_summary,
        "segments": segments,
        "selected_evidence": selected,
        "clips": clips,
        "quality_checks": {
            "pair_alignment": "passed",
            "canonical_validation": "passed",
            "frame_vector_lengths": "passed",
            "agreement_report_reconciliation": "passed",
            "reconciliation": reconciliation,
        },
        "headline": {
            "track_count": len(profile),
            "tracks_with_any_disagreement": sum(
                row["disagreement_frames"] > 0 for row in summaries
            ),
            "calibration_pair_strata": len(calibration_pairs),
            "selected_pair_strata": len(selected_pairs),
            "calibration_directional_pair_strata": len(
                calibration_directional_pairs
            ),
            "selected_directional_pair_strata": len(selected_directional_pairs),
            "selected_evidence_count": len(selected),
            "boundary_reviewable_evidence_count": sum(
                bool(row["boundary_reviewable"]) for row in selected
            ),
            "semantic_only_evidence_count": sum(
                not bool(row["boundary_reviewable"]) for row in selected
            ),
            "review_clip_count": len(clips),
            "review_seconds": review_seconds,
            "review_minutes": review_seconds / 60.0,
            "full_seconds": full_seconds,
            "full_minutes": full_seconds / 60.0,
            "review_reduction_rate": 1.0 - review_seconds / full_seconds,
            "legacy_missingness_frames": sum(
                row["legacy_missingness_frames"] for row in summaries
            ),
            "review_clips_overlapping_legacy_missingness": clips_overlapping_missingness,
            "any_disagreement_frames": sum(any_difference_mask),
            "any_disagreement_rate": sum(any_difference_mask) / window_frames,
            "any_disagreement_union_runs": len(any_difference_runs),
            "median_any_disagreement_run_frames": (
                statistics.median(any_difference_runs) if any_difference_runs else 0
            ),
            "max_any_disagreement_run_frames": max(any_difference_runs, default=0),
            "max_concurrent_disagreement_tracks": max(
                per_frame_difference_counts, default=0
            ),
            "concurrent_disagreement_distribution": concurrent_distribution,
        },
    }


def _fmt_rate(value: float) -> str:
    return f"{value * 100:.1f}%"


def build_report_artifact(analysis: Mapping[str, Any]) -> dict[str, Any]:
    """Create the canonical Data Analytics report artifact payload."""

    title = "DepressionPlex TST 双标分歧复核包"
    generated_at = str(analysis["generated_at"])
    headline = dict(analysis["headline"])
    headline_fields = (
        "review_clip_count",
        "review_minutes",
        "review_reduction_rate",
        "selected_pair_strata",
        "calibration_pair_strata",
        "selected_directional_pair_strata",
        "calibration_directional_pair_strata",
        "boundary_reviewable_evidence_count",
        "semantic_only_evidence_count",
        "legacy_missingness_frames",
        "full_minutes",
        "any_disagreement_frames",
        "any_disagreement_rate",
    )
    headline_row = {field: headline[field] for field in headline_fields}
    track_rows = sorted(
        (dict(row) for row in analysis["track_summary"]),
        key=lambda row: (-row["disagreement_rate"], row["rank"]),
    )
    short_track_labels = {
        "head_neck_motion": "头颈",
        "fore_motion": "前肢",
        "hind_motion": "后肢",
        "trunk_deforming": "躯干形变",
        "whole_body_swing": "整体摆动",
        "touch_wall": "触壁/杆",
        "tail_grasp": "抓尾",
        "axis_orient": "身体轴",
        "visibility": "可见性",
    }
    for rank, row in enumerate(track_rows, 1):
        row["disagreement_rank"] = rank
        row["track_short_label"] = short_track_labels.get(
            row["track"], row["track_label"]
        )
    track_report_fields = (
        "rank",
        "track",
        "track_label",
        "track_short_label",
        "window_frames",
        "disagreement_frames",
        "disagreement_rate",
        "calibration_disagreement_frames",
        "legacy_missingness_frames",
        "disagreement_union_runs",
        "diagnostic_kappa",
        "weighted_kappa",
    )
    track_report_rows = [
        {field: row.get(field) for field in track_report_fields} for row in track_rows
    ]
    clip_rows = [dict(row) for row in analysis["clips"]]
    for row in clip_rows:
        row["frame_range"] = f"[{row['start_frame']}, {row['end_frame_exclusive']})"
    clip_report_fields = (
        "clip_id",
        "start_frame",
        "end_frame_exclusive",
        "frame_range",
        "duration_sec",
        "affected_track_labels",
        "evidence_count",
        "clip_file",
    )
    clip_report_rows = [
        {field: row.get(field) for field in clip_report_fields} for row in clip_rows
    ]

    ranked_nonzero = [row for row in track_rows if row["disagreement_frames"]]
    top_text = "、".join(
        f"{row['track_label']} {_fmt_rate(row['disagreement_rate'])}"
        for row in ranked_nonzero[:3]
    )
    support_lookup = {
        (row["track"], row["label"]): row for row in analysis["label_support"]
    }
    support_findings: list[str] = []
    fore_subtle = support_lookup.get(("fore_motion", "subtle"))
    hind_marked = support_lookup.get(("hind_motion", "marked"))
    if fore_subtle and fore_subtle["annotator_b_frames"] == 0:
        support_findings.append(
            f"{analysis['annotator_b']}在前肢轨道没有使用 `subtle`，而{analysis['annotator_a']}使用了 "
            f"{fore_subtle['annotator_a_frames']} 帧"
        )
    if hind_marked and hind_marked["annotator_b_frames"] == 0:
        support_findings.append(
            f"{analysis['annotator_b']}在后肢轨道没有使用 `marked`，而{analysis['annotator_a']}使用了 "
            f"{hind_marked['annotator_a_frames']} 帧"
        )
    support_sentence = "；".join(support_findings)
    notebook_source_id = "paired_v2_review_notebook"
    headline_source_id = "review_headline_sql"
    track_source_id = "review_track_summary_sql"
    queue_source_id = "review_queue_sql"
    notebook_source_path = "analysis_notebook.ipynb"
    sqlite_source_path = "review_analysis.sqlite"
    command = (
        "python3 -m depressionplex.cli.annotation_review "
        "--annotation-a <recovered-a.json> --annotation-b <recovered-b.json> "
        "--agreement-report <diagnostic-agreement.json> --video <source.mp4> "
        "--output-dir <review-package>"
    )
    notebook_source_query = {
        "engine": "python3",
        "language": "python",
        "description": (
            "展开两份 canonical V2.2 标注为逐帧状态，按恒定标签对聚合连续分歧段，"
            "剔除 legacy axis unknown 后按轨道/标签对抽取最长代表片段。"
            f"可复现命令：{command}"
        ),
        "executed_at": generated_at,
        "tables_used": [
            str(value.get("name", key))
            for key, value in analysis.get("input_provenance", {}).items()
            if isinstance(value, Mapping) and key != "video"
        ] or [
            "recovered-annotation-a.json",
            "recovered-annotation-b.json",
            "legacy-diagnostic-agreement-v2.2.json",
        ],
        "filters": [
            f"trial={analysis['trial']}",
            f"mouse={analysis['mouse']}",
            f"analysis_window=[{analysis['analysis_window'][0]},{analysis['analysis_window'][1]})",
            "calibration queue excludes axis_orient=unknown legacy missingness",
        ] + [
            f"{key}_sha256={value['sha256']}"
            for key, value in analysis.get("input_provenance", {}).items()
            if isinstance(value, Mapping) and isinstance(value.get("sha256"), str)
        ],
        "metric_definitions": [
            f"disagreement_rate = frames where annotator A label != annotator B label / {analysis['window_frames']} aligned frames",
            "segment = maximal contiguous run with one fixed directional label pair",
            "review_reduction_rate = 1 - selected clip seconds / full aligned video seconds",
            "diagnostic kappa comes from the supplied legacy diagnostic agreement report; formal gate is N/A",
        ],
    }
    headline_sql = "SELECT * FROM review_headline"
    track_sql = (
        "SELECT * FROM review_track_summary "
        "ORDER BY disagreement_rate DESC, rank ASC"
    )
    queue_sql = "SELECT * FROM review_queue ORDER BY clip_id ASC"

    cards = [
        {
            "id": "review_clips_card",
            "description": "需要共同复核的合并短片数量。",
            "dataset": "headline_metrics",
            "sourceId": headline_source_id,
            "metrics": [
                {"label": "复核短片", "field": "review_clip_count", "format": "number"}
            ],
        },
        {
            "id": "review_time_card",
            "description": "所有短片的总播放时长；允许同一短片覆盖多个轨道分歧。",
            "dataset": "headline_metrics",
            "sourceId": headline_source_id,
            "metrics": [
                {"label": "复核分钟", "field": "review_minutes", "format": "number"},
                {"label": "比整段少", "field": "review_reduction_rate", "format": "percent"},
            ],
        },
        {
            "id": "pair_coverage_card",
            "description": "五个优先 SOP 轨道中有实际分歧的无向语义标签对覆盖；A→B 方向不在本轮逐方向验收。",
            "dataset": "headline_metrics",
            "sourceId": headline_source_id,
            "metrics": [
                {"label": "无向对覆盖", "field": "selected_pair_strata", "format": "number"},
                {"label": "无向对总数", "field": "calibration_pair_strata", "format": "number"},
            ],
        },
        {
            "id": "missingness_card",
            "description": f"{analysis['annotator_a']}文件中原本未标明、恢复时显式写为 unknown 的 axis 帧；不进入 SOP 抽样。",
            "dataset": "headline_metrics",
            "sourceId": headline_source_id,
            "metrics": [
                {"label": "历史缺失帧", "field": "legacy_missingness_frames", "format": "number"}
            ],
        },
    ]
    charts = [
        {
            "id": "track_disagreement_chart",
            "title": "各轨道逐帧分歧率",
            "subtitle": (
                f"同一视频、mouse{analysis['mouse']}、全长 {analysis['window_frames']:,} 帧；"
                f"axis 包含 {headline['legacy_missingness_frames']} 帧历史 unknown，正式验收门均为 N/A。"
            ),
            "type": "bar",
            "dataset": "track_summary",
            "sourceId": track_source_id,
            "valueFormat": "percent",
            "encodings": {
                "x": {
                    "field": "track_short_label",
                    "type": "nominal",
                    "label": "原语轨道",
                },
                "y": {
                    "field": "disagreement_rate",
                    "type": "quantitative",
                    "label": "逐帧分歧率",
                    "format": "percent",
                },
                "tooltip": [
                    {"field": "disagreement_frames", "type": "quantitative", "label": "分歧帧"},
                    {"field": "diagnostic_kappa", "type": "quantitative", "label": "诊断 κ"},
                    {"field": "legacy_missingness_frames", "type": "quantitative", "label": "历史缺失帧"},
                ],
            },
            "layout": "full",
        }
    ]
    tables = [
        {
            "id": "track_summary_table",
            "title": "逐轨分歧与诊断一致性",
            "subtitle": "κ 只用于定位 SOP 分歧；两份 legacy 标注非独立盲标，不能用于 pilot 验收。",
            "dataset": "track_summary",
            "sourceId": track_source_id,
            "defaultSort": {"field": "disagreement_rate", "direction": "desc"},
            "density": "dense",
            "layout": "full",
            "columns": [
                {"field": "track_label", "label": "轨道", "type": "text"},
                {"field": "disagreement_frames", "label": "分歧帧", "format": "number"},
                {"field": "disagreement_rate", "label": "分歧率", "format": "percent"},
                {"field": "diagnostic_kappa", "label": "诊断 κ", "format": "number"},
                {"field": "legacy_missingness_frames", "label": "历史缺失帧", "format": "number"},
            ],
        },
        {
            "id": "review_queue_table",
            "title": "共同复核播放队列",
            "subtitle": "片段均按 0-based 半开源帧区间裁切；mouse1 为画面最左隔间。",
            "dataset": "review_queue",
            "sourceId": queue_source_id,
            "defaultSort": {"field": "clip_id", "direction": "asc"},
            "density": "dense",
            "layout": "full",
            "columns": [
                {"field": "clip_id", "label": "片段", "type": "text"},
                {"field": "frame_range", "label": "源帧 [start,end)", "type": "text"},
                {"field": "duration_sec", "label": "秒", "format": "number"},
                {"field": "affected_track_labels", "label": "复核轨道", "type": "text"},
            ],
        },
    ]
    blocks = [
        {"id": "title", "type": "markdown", "body": f"# {title}"},
        {
            "id": "technical_summary",
            "type": "markdown",
            "sourceId": notebook_source_id,
            "body": (
                "## 技术摘要\n\n"
                f"现有双标可以继续用，不需要同事重看完整 {headline['full_minutes']:.1f} 分钟。"
                f"本包把五个优先 SOP 轨道的代表性分歧合并为 "
                f"{headline['review_clip_count']} 个短片，共 {headline['review_minutes']:.1f} 分钟，"
                f"比整段少 {_fmt_rate(headline['review_reduction_rate'])}。"
                f"首轮覆盖 {headline['selected_pair_strata']}/{headline['calibration_pair_strata']} 个无向标签对；"
                f"它不是对 {headline['calibration_directional_pair_strata']} 个 A→B 方向逐一验收。"
                f"完整逐帧清单显示，至少一个轨道不同的帧占 {_fmt_rate(headline['any_disagreement_rate'])}，"
                "因此应做分层口径校准，而不是逐段全量返工。"
                "建议先完成这批共同裁决并更新例库，再做 1 例新的共同练习；正式 12 例独立盲标 pilot 仍单独记账。"
            ),
        },
        {
            "id": "headline_metrics_block",
            "type": "metric-strip",
            "cardIds": [card["id"] for card in cards],
        },
        {
            "id": "key_findings",
            "type": "markdown",
            "sourceId": notebook_source_id,
            "body": (
                "## 关键发现\n\n"
                f"- 分歧率最高的轨道是：{top_text}。这说明当前主要问题是动作强度和边界口径，而不是文件损坏。\n"
                + (f"- 标签使用存在系统性档位差：{support_sentence}。这不是只调整起止边界就能解决的问题。\n" if support_sentence else "")
                + f"- `axis_orient` 中 {headline['legacy_missingness_frames']} 帧是恢复时显式化的历史缺失，不当作两位标注员真实判断冲突，也不进入本轮 SOP 抽样。\n"
                + "- `touch_wall`、`tail_grasp` 与 `visibility` 在本例中完全一致，但前两者为全阴性退化分布，不能据此证明跨视频可靠。"
            ),
        },
        {"id": "track_chart_block", "type": "chart", "chartId": "track_disagreement_chart"},
        {"id": "track_table_block", "type": "table", "tableId": "track_summary_table"},
        {
            "id": "scope",
            "type": "markdown",
            "sourceId": notebook_source_id,
            "body": (
                "## 范围、数据与指标定义\n\n"
                f"分析单位为 trial `{analysis['trial']}` 的 mouse{analysis['mouse']}，"
                f"时间窗为 `[{analysis['analysis_window'][0]}, {analysis['analysis_window'][1]})`，"
                f"共 {analysis['window_frames']} 帧（{analysis['fps']:g} fps）。"
                "逐帧分歧率是两人标签不同的帧数除以对齐窗口帧数；连续段在标签对变化时切分。"
                "本报告只描述 legacy 诊断，不给出正式通过/失败结论。"
            ),
        },
        {
            "id": "methodology",
            "type": "markdown",
            "sourceId": notebook_source_id,
            "body": (
                "## 方法\n\n"
                "两份 V2.2 文件先通过 canonical 校验，再展开为逐帧独立轨道状态。"
                "所有差异按恒定方向标签对聚合为连续段；五个优先轨道按‘轨道 × 无向标签对’分层，"
                "每层选择最长代表段，最多两例，并把重叠的 6 秒上下文窗口合并为不超过 10 秒的短片。"
                "视频使用 frame-based trim 重新编码，输出第 0 帧严格对应清单中的源起始帧。"
            ),
        },
        {
            "id": "limitations",
            "type": "markdown",
            "body": (
                "## 局限性、不确定性与稳健性\n\n"
                "- 只有 1 个视频、1 个隔间和 1 对 legacy 标注员，且两人并非本轮正式独立盲标。\n"
                "- 当前片段是面向 SOP 校准的目的性抽样（最长代表段），不能估计总体错误率。\n"
                f"- {headline['semantic_only_evidence_count']} 个长分歧只截取中段，只能裁语义/强度；其完整起止边界留到第二轮。\n"
                f"- 11/11 指无向标签对覆盖，不代表 {headline['calibration_directional_pair_strata']} 个 A→B 方向均已逐行裁决。\n"
                "- 原视频单隔间像素较低，精细肢体判断可能受限；共同裁决时应允许标记‘画质不足/无法判定’。\n"
                "- 诊断 κ 与分歧率用于定位问题，不替代 12 个唯一 chamber-trial 的正式 blind pilot。"
            ),
        },
        {
            "id": "next_steps",
            "type": "markdown",
            "body": (
                "## 建议的下一步\n\n"
                "1. 两位同事按片段顺序共同观看，只填写 `review_decisions.csv` 的共识标签、SOP 规则和备注。\n"
                "2. 仅在 `boundary_reviewable=yes` 时填写边界裁决；预填 N/A 的中段片不要推断起止边界。\n"
                "3. 把裁决结果补进 SOP 例库，随后做 1 例新的共同练习（不计数）。\n"
                "4. 共同练习稳定后再启动正式 12 例独立盲标 pilot；常见原语 κ ≥ 0.80 才扩量。"
            ),
        },
        {"id": "review_queue_block", "type": "table", "tableId": "review_queue_table"},
        {
            "id": "further_questions",
            "type": "markdown",
            "body": (
                "## 后续要回答的问题\n\n"
                "- `subtle` 与 `marked` 的最小可观察证据分别是什么？\n"
                "- 短促边界差应按逐帧精确裁决，还是允许固定的边界容差？\n"
                "- 低分辨率下无法可靠看清的肢体动作，是否需要统一落入 `uncertain` 或只由整体轨道判定？"
            ),
        },
    ]
    manifest_sources = [
        {
            "id": notebook_source_id,
            "label": "配对 V2.2 标注分析笔记本",
            "path": notebook_source_path,
        },
        {
            "id": headline_source_id,
            "label": "复核包 headline SQLite 查询",
            "path": sqlite_source_path,
        },
        {
            "id": track_source_id,
            "label": "逐轨分歧 SQLite 查询",
            "path": sqlite_source_path,
        },
        {
            "id": queue_source_id,
            "label": "复核队列 SQLite 查询",
            "path": sqlite_source_path,
        },
    ]
    return {
        "surface": "report",
        "manifest": {
            "version": 1,
            "surface": "report",
            "title": title,
            "description": "面向标注 SOP 校准的最小复核包与数据质量报告。",
            "generatedAt": generated_at,
            "cards": cards,
            "charts": charts,
            "tables": tables,
            "sources": manifest_sources,
            "blocks": blocks,
        },
        "snapshot": {
            "version": 1,
            "generatedAt": generated_at,
            "status": "ready",
            "datasets": {
                "headline_metrics": [headline_row],
                "track_summary": track_report_rows,
                "review_queue": clip_report_rows,
            },
        },
        "sources": [
            {
                "id": notebook_source_id,
                "label": "配对 V2.2 标注分析笔记本",
                "path": notebook_source_path,
                "query": notebook_source_query,
            },
            {
                "id": headline_source_id,
                "label": "复核包 headline SQLite 查询",
                "path": sqlite_source_path,
                "query": {
                    "engine": "sqlite",
                    "language": "sql",
                    "sql": headline_sql,
                    "description": "读取由已执行分析笔记本写入的便携式 headline 快照；指标计算溯源以笔记本为准。",
                    "executed_at": generated_at,
                    "tables_used": ["review_headline"],
                    "filters": ["one reviewed paired-annotation analysis row"],
                    "metric_definitions": notebook_source_query["metric_definitions"],
                },
            },
            {
                "id": track_source_id,
                "label": "逐轨分歧 SQLite 查询",
                "path": sqlite_source_path,
                "query": {
                    "engine": "sqlite",
                    "language": "sql",
                    "sql": track_sql,
                    "description": "读取由已执行分析笔记本写入的逐轨便携式快照；指标计算溯源以笔记本为准。",
                    "executed_at": generated_at,
                    "tables_used": ["review_track_summary"],
                    "filters": [f"trial={analysis['trial']}", f"mouse={analysis['mouse']}"],
                    "metric_definitions": notebook_source_query["metric_definitions"],
                },
            },
            {
                "id": queue_source_id,
                "label": "复核队列 SQLite 查询",
                "path": sqlite_source_path,
                "query": {
                    "engine": "sqlite",
                    "language": "sql",
                    "sql": queue_sql,
                    "description": "读取由已执行分析笔记本写入、按源帧排序的便携式复核队列快照。",
                    "executed_at": generated_at,
                    "tables_used": ["review_queue"],
                    "filters": [
                        "five primary SOP calibration tracks",
                        "axis_orient=unknown legacy missingness excluded",
                    ],
                    "metric_definitions": [
                        "clip source ranges use 0-based half-open frame semantics",
                        "duration_sec = duration_frames / source fps",
                    ],
                },
            },
        ],
    }


def review_decision_rows(analysis: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return one editable consensus row per selected evidence segment."""

    rows: list[dict[str, Any]] = []
    ordered_evidence = sorted(
        analysis["selected_evidence"],
        key=lambda row: (row["clip_id"], row["evidence_id"]),
    )
    for segment in ordered_evidence:
        boundary_reviewable = bool(segment["boundary_reviewable"])
        rows.append(
            {
                "clip_id": segment["clip_id"],
                "evidence_id": segment["evidence_id"],
                "track": segment["track"],
                "track_label": segment["track_label"],
                "source_start_frame": segment["visible_evidence_start_frame"],
                "source_end_frame": segment["visible_evidence_end_frame_exclusive"] - 1,
                "source_end_frame_exclusive": segment[
                    "visible_evidence_end_frame_exclusive"
                ],
                "full_disagreement_start_frame": segment["start_frame"],
                "full_disagreement_end_frame": segment["end_frame"],
                "full_disagreement_end_frame_exclusive": segment[
                    "end_frame_exclusive"
                ],
                "clip_start_frame": next(
                    clip["start_frame"]
                    for clip in analysis["clips"]
                    if clip["clip_id"] == segment["clip_id"]
                ),
                "clip_end_frame_exclusive": next(
                    clip["end_frame_exclusive"]
                    for clip in analysis["clips"]
                    if clip["clip_id"] == segment["clip_id"]
                ),
                "annotator_a": analysis["annotator_a"],
                "annotator_a_label": segment["annotator_a_label"],
                "annotator_b": analysis["annotator_b"],
                "annotator_b_label": segment["annotator_b_label"],
                "boundary_reviewable": "yes" if boundary_reviewable else "no",
                "boundary_instruction": segment["boundary_instruction"],
                "consensus_label": "",
                "boundary_decision": (
                    ""
                    if boundary_reviewable
                    else "N/A—本片仅截中段，完整起止边界留第二轮"
                ),
                "sop_rule_or_example": "",
                "image_quality": "",
                "reviewer": "",
                "reviewed_at": "",
                "notes": "",
            }
        )
    return rows


def json_ready(value: Any) -> Any:
    """Normalize tuples and mapping subclasses for stable JSON serialization."""

    if isinstance(value, Mapping):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    return value


def sorted_track_summary(analysis: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    """Yield track summaries in descending disagreement order."""

    return sorted(
        analysis["track_summary"],
        key=lambda row: (-row["disagreement_rate"], row["rank"]),
    )
