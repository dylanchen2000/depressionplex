"""Explicit, auditable V2.2 recovery for legacy annotation artifacts.

Recovery is intentionally diagnostic-only: output is always ``pool=train``
with ``annotator_role=legacy_rater`` and ``blind=false``.  A caller may approve
the complete source-video window, producing a confirmed/completed recovered
document, but it can never become formal validation truth without new blind
annotation.
"""

from __future__ import annotations

from collections import defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping

from .contract import (
    ANALYSIS_WINDOW_SEMANTICS,
    ASSAY_PROFILES,
    FORMAT,
    INTERVAL_SEMANTICS,
    PRIMITIVE_SET_VERSION,
    RUBRIC_VERSION,
    SCHEMA,
    TOOL_VERSION,
    TRACK_DEFS,
    TST_MIN_FORMAL_WINDOW_FRAMES,
    validate_document,
)
from .csv_v2 import LEGACY_COLUMNS


class LegacyRecoveryError(ValueError):
    """Raised when recovery would guess metadata or alter primitive truth."""


_METADATA_REQUIRED = (
    "trial",
    "assay",
    "annotator",
    "annotator_role",
    "assignment_id",
    "fps",
    "n_frames",
    "video",
    "active_mice",
    "analysis_window",
    "pool",
    "prefill",
    "blind",
    "created_at",
    "full_window_approved",
    "fill_axis_orient_unknown",
)

_PROVENANCE_REQUIRED = (
    "repair_reason",
    "repaired_by",
    "repaired_at",
    "metadata_source",
    "repaired_fields",
)

_RECOVERY_REPAIRED_FIELDS = {
    "schema",
    "format",
    "tool_version",
    "primitive_set_version",
    "rubric_version",
    "interval_semantics",
    "analysis_window_semantics",
    "analysis_window_confirmed",
    "annotator_role",
    "pool",
    "blind",
    "completed",
    "metadata_repaired",
    "provenance",
}

_JSON_STRICT_IDENTITY_FIELDS = (
    "assay",
    "annotator",
    "fps",
    "n_frames",
    "active_mice",
    "prefill",
)

_JSON_SOURCE_METADATA_FIELDS = (
    "schema",
    "format",
    "tool_version",
    "primitive_set_version",
    "rubric_version",
    "trial",
    "assay",
    "annotator",
    "fps",
    "n_frames",
    "active_mice",
    "analysis_window",
    "pool",
    "prefill",
    "blind",
    "completed",
)


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _require_recovery_inputs(
    metadata: Mapping[str, Any], provenance: Mapping[str, Any], *, csv_mouse: bool
) -> None:
    missing = [key for key in _METADATA_REQUIRED if key not in metadata]
    if csv_mouse and "mouse" not in metadata:
        missing.append("mouse")
    if missing:
        raise LegacyRecoveryError("explicit metadata is missing: " + ", ".join(missing))
    missing_provenance = [key for key in _PROVENANCE_REQUIRED if key not in provenance]
    if missing_provenance:
        raise LegacyRecoveryError(
            "explicit provenance is missing: " + ", ".join(missing_provenance)
        )
    for key in ("repair_reason", "repaired_by", "repaired_at", "metadata_source"):
        if not isinstance(provenance.get(key), str) or not provenance[key].strip():
            raise LegacyRecoveryError(f"provenance.{key} must be a non-empty string")
    repaired_fields = provenance.get("repaired_fields")
    if (
        not isinstance(repaired_fields, list)
        or not repaired_fields
        or any(not isinstance(value, str) or not value.strip() for value in repaired_fields)
    ):
        raise LegacyRecoveryError("provenance.repaired_fields must be a non-empty string list")

    if metadata.get("annotator_role") != "legacy_rater":
        raise LegacyRecoveryError("recovery metadata annotator_role must be 'legacy_rater'")
    if metadata.get("pool") != "train":
        raise LegacyRecoveryError("recovery metadata pool must be 'train'")
    if metadata.get("blind") is not False:
        raise LegacyRecoveryError("recovery metadata blind must be false")
    if not isinstance(metadata.get("prefill"), bool):
        raise LegacyRecoveryError("recovery metadata prefill must be explicit boolean")
    if not isinstance(metadata.get("full_window_approved"), bool):
        raise LegacyRecoveryError("metadata.full_window_approved must be boolean")
    if not isinstance(metadata.get("fill_axis_orient_unknown"), bool):
        raise LegacyRecoveryError("metadata.fill_axis_orient_unknown must be boolean")

    for key in ("trial", "annotator", "assignment_id", "created_at"):
        if not isinstance(metadata.get(key), str) or not metadata[key].strip():
            raise LegacyRecoveryError(f"metadata.{key} must be a non-empty string")
    assay = metadata.get("assay")
    if assay not in ASSAY_PROFILES:
        raise LegacyRecoveryError(f"metadata.assay must be one of {sorted(ASSAY_PROFILES)}")
    fps = metadata.get("fps")
    if (
        not isinstance(fps, (int, float))
        or isinstance(fps, bool)
        or not math.isfinite(float(fps))
        or float(fps) <= 0
    ):
        raise LegacyRecoveryError("metadata.fps must be a finite positive number")
    n_frames = metadata.get("n_frames")
    if not _is_int(n_frames) or n_frames <= 0:
        raise LegacyRecoveryError("metadata.n_frames must be a positive integer")
    active_mice = metadata.get("active_mice")
    if (
        not isinstance(active_mice, list)
        or not active_mice
        or any(not _is_int(mouse) or not 1 <= mouse <= 4 for mouse in active_mice)
        or active_mice != sorted(set(active_mice))
    ):
        raise LegacyRecoveryError(
            "metadata.active_mice must be a sorted, unique list of integers in 1..4"
        )
    window = metadata.get("analysis_window")
    if (
        not isinstance(window, list)
        or len(window) != 2
        or any(not _is_int(value) for value in window)
        or not (0 <= window[0] < window[1] <= n_frames)
    ):
        raise LegacyRecoveryError(
            "metadata.analysis_window must satisfy 0 <= start < end_exclusive <= n_frames"
        )
    if not isinstance(metadata.get("video"), Mapping):
        raise LegacyRecoveryError("metadata.video must be an object")
    if csv_mouse:
        mouse = metadata.get("mouse")
        if not _is_int(mouse) or active_mice != [mouse]:
            raise LegacyRecoveryError(
                "legacy CSV recovery requires integer mouse and active_mice=[mouse]"
            )

    if metadata.get("full_window_approved"):
        if window != [0, n_frames]:
            raise LegacyRecoveryError(
                "full_window_approved=true requires analysis_window=[0,n_frames]"
            )
        if (
            metadata.get("assay") == "TST"
            and n_frames < TST_MIN_FORMAL_WINDOW_FRAMES
        ):
            raise LegacyRecoveryError(
                f"approved TST recovery requires at least {TST_MIN_FORMAL_WINDOW_FRAMES} frames"
            )


def _validate_raw_interval(
    interval: Mapping[str, Any],
    *,
    track_id: str,
    active_mice: set[int],
    n_frames: int,
) -> dict[str, Any]:
    for key in ("start", "end", "value", "mouse"):
        if key not in interval:
            raise LegacyRecoveryError(f"{track_id} interval is missing {key}")
    start = interval["start"]
    end = interval["end"]
    mouse = interval["mouse"]
    value = interval["value"]
    if not _is_int(start) or not _is_int(end) or not (0 <= start <= end < n_frames):
        raise LegacyRecoveryError(
            f"{track_id} mouse {mouse}: invalid closed interval [{start!r},{end!r}]"
        )
    if not _is_int(mouse) or mouse not in active_mice:
        raise LegacyRecoveryError(
            f"{track_id}: interval mouse {mouse!r} is not in active_mice"
        )
    if value not in TRACK_DEFS[track_id].values:
        raise LegacyRecoveryError(
            f"{track_id} mouse {mouse}: value {value!r} is outside V2.2 enum"
        )
    return {"start": start, "end": end, "value": value, "mouse": mouse}


def _union_group(
    intervals: Iterable[Mapping[str, Any]], *, track_id: str, mouse: int
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Union equal-value spans and reject every different-value overlap."""

    stats = {
        "input_intervals": 0,
        "duplicate_unions": 0,
        "overlap_unions": 0,
        "adjacent_unions": 0,
        "output_intervals": 0,
    }
    unique: list[dict[str, Any]] = []
    seen: set[tuple[int, int, str]] = set()
    for interval in intervals:
        stats["input_intervals"] += 1
        key = (interval["start"], interval["end"], interval["value"])
        if key in seen:
            stats["duplicate_unions"] += 1
            continue
        seen.add(key)
        unique.append(dict(interval))
    unique.sort(key=lambda item: (item["start"], item["end"], item["value"]))

    merged: list[dict[str, Any]] = []
    for interval in unique:
        if not merged:
            merged.append(interval)
            continue
        previous = merged[-1]
        if interval["start"] <= previous["end"]:
            if interval["value"] != previous["value"]:
                raise LegacyRecoveryError(
                    f"different-value overlap: {track_id} mouse {mouse} "
                    f"[{previous['start']},{previous['end']}]={previous['value']} vs "
                    f"[{interval['start']},{interval['end']}]={interval['value']}"
                )
            previous["end"] = max(previous["end"], interval["end"])
            stats["overlap_unions"] += 1
        elif (
            interval["start"] == previous["end"] + 1
            and interval["value"] == previous["value"]
        ):
            previous["end"] = interval["end"]
            stats["adjacent_unions"] += 1
        else:
            merged.append(interval)
    stats["output_intervals"] = len(merged)
    return merged, stats


def _canonicalize_tracks(
    raw_tracks: Mapping[str, Any], metadata: Mapping[str, Any]
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    assay = metadata["assay"]
    if assay not in ASSAY_PROFILES:
        raise LegacyRecoveryError(f"unsupported assay {assay!r}")
    expected = set(ASSAY_PROFILES[assay])
    extra = sorted(set(raw_tracks) - expected)
    if extra:
        raise LegacyRecoveryError(f"legacy source contains tracks outside {assay}: {extra}")
    active_mice = set(metadata["active_mice"])
    n_frames = metadata["n_frames"]
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for track_id in ASSAY_PROFILES[assay]:
        intervals = raw_tracks.get(track_id, [])
        if not isinstance(intervals, list):
            raise LegacyRecoveryError(f"legacy tracks.{track_id} must be an array")
        for interval in intervals:
            if not isinstance(interval, Mapping):
                raise LegacyRecoveryError(f"legacy tracks.{track_id} has non-object interval")
            clean = _validate_raw_interval(
                interval,
                track_id=track_id,
                active_mice=active_mice,
                n_frames=n_frames,
            )
            grouped[(track_id, clean["mouse"])].append(clean)

    tracks = {track_id: [] for track_id in ASSAY_PROFILES[assay]}
    per_group: dict[str, dict[str, int]] = {}
    totals = {
        "input_intervals": 0,
        "duplicate_unions": 0,
        "overlap_unions": 0,
        "adjacent_unions": 0,
        "output_intervals": 0,
    }
    for track_id in ASSAY_PROFILES[assay]:
        for mouse in metadata["active_mice"]:
            merged, stats = _union_group(
                grouped.get((track_id, mouse), ()), track_id=track_id, mouse=mouse
            )
            tracks[track_id].extend(merged)
            per_group[f"{track_id}:mouse{mouse}"] = stats
            for key in totals:
                totals[key] += stats[key]
    return tracks, {"totals": totals, "per_track_mouse": per_group}


def _axis_unknown_gaps(
    tracks: dict[str, list[dict[str, Any]]], metadata: Mapping[str, Any]
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "enabled": bool(metadata["fill_axis_orient_unknown"]),
        "filled_intervals": 0,
        "filled_frames": 0,
        "post_fill_same_value_unions": 0,
        "per_mouse": {},
    }
    if metadata["assay"] != "TST" or not metadata["fill_axis_orient_unknown"]:
        return result
    window_start, window_end = metadata["analysis_window"]
    axis = tracks["axis_orient"]
    for mouse in metadata["active_mice"]:
        covered = sorted(
            (
                max(window_start, interval["start"]),
                min(window_end, interval["end"] + 1),
            )
            for interval in axis
            if interval["mouse"] == mouse
            and interval["start"] < window_end
            and interval["end"] >= window_start
        )
        cursor = window_start
        fills: list[dict[str, Any]] = []
        for start, end_exclusive in covered:
            if start > cursor:
                fills.append(
                    {
                        "start": cursor,
                        "end": start - 1,
                        "value": "unknown",
                        "mouse": mouse,
                    }
                )
            cursor = max(cursor, end_exclusive)
        if cursor < window_end:
            fills.append(
                {
                    "start": cursor,
                    "end": window_end - 1,
                    "value": "unknown",
                    "mouse": mouse,
                }
            )
        axis.extend(fills)
        filled_frames = sum(item["end"] - item["start"] + 1 for item in fills)
        result["per_mouse"][str(mouse)] = {
            "intervals": len(fills),
            "frames": filled_frames,
        }
        result["filled_intervals"] += len(fills)
        result["filled_frames"] += filled_frames
    post_fill_unions = 0
    canonical_axis: list[dict[str, Any]] = []
    for mouse in metadata["active_mice"]:
        merged, stats = _union_group(
            (interval for interval in axis if interval["mouse"] == mouse),
            track_id="axis_orient",
            mouse=mouse,
        )
        canonical_axis.extend(merged)
        post_fill_unions += (
            stats["duplicate_unions"]
            + stats["overlap_unions"]
            + stats["adjacent_unions"]
        )
    tracks["axis_orient"] = canonical_axis
    result["post_fill_same_value_unions"] = post_fill_unions
    return result


def _build_recovered_document(
    *,
    source_path: Path,
    source_format: str,
    raw_tracks: Mapping[str, Any],
    metadata: Mapping[str, Any],
    repair_provenance: Mapping[str, Any],
    original_trial: Any = None,
    source_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    _require_recovery_inputs(metadata, repair_provenance, csv_mouse=False)
    tracks, union_stats = _canonicalize_tracks(raw_tracks, metadata)
    axis_fill = _axis_unknown_gaps(tracks, metadata)
    approved = metadata["full_window_approved"]

    repaired_fields = sorted(
        _RECOVERY_REPAIRED_FIELDS | set(repair_provenance["repaired_fields"])
    )
    if any(
        union_stats["totals"][key]
        for key in ("duplicate_unions", "overlap_unions", "adjacent_unions")
    ):
        repaired_fields = sorted(set(repaired_fields) | {"tracks"})
    if axis_fill["filled_frames"]:
        repaired_fields = sorted(set(repaired_fields) | {"tracks.axis_orient"})
    provenance = dict(repair_provenance)
    provenance.update(
        {
            "source_paths": [source_path.name],
            "source_sha256": [_sha256_path(source_path)],
            "source_mtime": datetime.fromtimestamp(
                source_path.stat().st_mtime, timezone.utc
            ).isoformat(),
            "original_trial": original_trial,
            "source_metadata": dict(source_metadata or {}),
            "source_format": source_format,
            "repaired_fields": repaired_fields,
            "migration_tool_version": TOOL_VERSION,
            "recovery": {
                "mode": "lossless_union_with_explicit_axis_unknown",
                "full_window_approved": approved,
                "union": union_stats,
                "axis_orient_unknown_fill": axis_fill,
            },
        }
    )
    doc: dict[str, Any] = {
        "schema": SCHEMA,
        "format": FORMAT,
        "tool_version": TOOL_VERSION,
        "primitive_set_version": PRIMITIVE_SET_VERSION,
        "rubric_version": RUBRIC_VERSION,
        "interval_semantics": INTERVAL_SEMANTICS,
        "analysis_window_semantics": ANALYSIS_WINDOW_SEMANTICS,
        "trial": metadata["trial"],
        "assay": metadata["assay"],
        "annotator": metadata["annotator"],
        "annotator_role": "legacy_rater",
        "assignment_id": metadata["assignment_id"],
        "fps": metadata["fps"],
        "n_frames": metadata["n_frames"],
        "video": dict(metadata["video"]),
        "active_mice": list(metadata["active_mice"]),
        "analysis_window": list(metadata["analysis_window"]),
        "analysis_window_confirmed": approved,
        "pool": "train",
        "prefill": metadata["prefill"],
        "blind": False,
        "completed": approved,
        "created_at": metadata["created_at"],
        "updated_at": repair_provenance["repaired_at"],
        "metadata_repaired": True,
        "provenance": provenance,
        "tracks": tracks,
    }
    if approved:
        doc["completed_at"] = repair_provenance["repaired_at"]
    validate_document(doc, require_completed=approved)
    return doc


def _read_legacy_csv_tracks(
    path: Path, metadata: Mapping[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != LEGACY_COLUMNS:
            raise LegacyRecoveryError("source is not the known legacy 7-column CSV")
        rows = list(reader)
    mouse = metadata["mouse"]
    if metadata["active_mice"] != [mouse]:
        raise LegacyRecoveryError("legacy CSV requires active_mice=[mouse]")
    tracks: dict[str, list[dict[str, Any]]] = {
        track_id: [] for track_id in ASSAY_PROFILES[metadata["assay"]]
    }
    expected_index: dict[str, int] = defaultdict(int)
    for row_number, row in enumerate(rows, start=2):
        track_id = row["track"]
        if track_id not in tracks:
            raise LegacyRecoveryError(
                f"row {row_number}: track {track_id!r} is outside assay profile"
            )
        expected_index[track_id] += 1
        try:
            interval_index = int(row["interval_index"])
            start = int(row["start"])
            end = int(row["end"])
            duration_frames = int(row["duration_frames"])
            duration_sec = float(row["duration_sec"])
        except (TypeError, ValueError) as exc:
            raise LegacyRecoveryError(f"row {row_number}: invalid numeric field") from exc
        if interval_index != expected_index[track_id]:
            raise LegacyRecoveryError(
                f"row {row_number}: interval_index for {track_id} is not sequential"
            )
        if duration_frames != end - start + 1:
            raise LegacyRecoveryError(f"row {row_number}: duration_frames mismatch")
        if not math.isclose(
            duration_sec,
            duration_frames / float(metadata["fps"]),
            rel_tol=0.0,
            abs_tol=5e-4,
        ):
            raise LegacyRecoveryError(f"row {row_number}: duration_sec mismatch")
        tracks[track_id].append(
            {
                "start": start,
                "end": end,
                "value": row["value"],
                "mouse": mouse,
            }
        )
    return tracks


def recover_legacy_csv(
    source: str | Path,
    *,
    metadata: Mapping[str, Any],
    repair_provenance: Mapping[str, Any],
) -> dict[str, Any]:
    """Recover a legacy seven-column, single-mouse CSV as diagnostic V2.2."""

    path = Path(source)
    _require_recovery_inputs(metadata, repair_provenance, csv_mouse=True)
    raw_tracks = _read_legacy_csv_tracks(path, metadata)
    return _build_recovered_document(
        source_path=path,
        source_format="annotation-v2-legacy-csv-7-column",
        raw_tracks=raw_tracks,
        metadata=metadata,
        repair_provenance=repair_provenance,
        original_trial=None,
        source_metadata={
            "available": False,
            "reason": (
                "legacy 7-column CSV contains interval rows only; canonical "
                "metadata came from the explicit recovery manifest"
            ),
            "metadata_source": repair_provenance["metadata_source"],
        },
    )


def recover_legacy_json(
    source: str | Path,
    *,
    metadata: Mapping[str, Any],
    repair_provenance: Mapping[str, Any],
) -> dict[str, Any]:
    """Recover unversioned/obsolete schema-2 JSON as diagnostic V2.2."""

    path = Path(source)
    _require_recovery_inputs(metadata, repair_provenance, csv_mouse=False)
    try:
        source_doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise LegacyRecoveryError(f"invalid JSON: {exc}") from exc
    if not isinstance(source_doc, Mapping) or source_doc.get("schema") != 2:
        raise LegacyRecoveryError("recover-json accepts legacy schema=2 objects only")
    if (
        source_doc.get("format") == FORMAT
        and source_doc.get("tool_version") == TOOL_VERSION
        and source_doc.get("primitive_set_version") == PRIMITIVE_SET_VERSION
        and source_doc.get("rubric_version") == RUBRIC_VERSION
    ):
        raise LegacyRecoveryError("source is already current canonical V2.2; validate it instead")
    raw_tracks = source_doc.get("tracks")
    if not isinstance(raw_tracks, Mapping):
        raise LegacyRecoveryError("legacy schema2 JSON must contain a tracks object")
    for key in _JSON_STRICT_IDENTITY_FIELDS:
        if key not in source_doc:
            raise LegacyRecoveryError(
                f"legacy JSON is missing source identity field {key!r}"
            )
        if source_doc[key] != metadata[key]:
            raise LegacyRecoveryError(
                f"legacy JSON source {key}={source_doc[key]!r} does not match "
                f"explicit metadata {metadata[key]!r}"
            )
    if not isinstance(source_doc.get("trial"), str) or not source_doc["trial"].strip():
        raise LegacyRecoveryError("legacy JSON source trial must be a non-empty string")
    source_metadata = {
        key: source_doc.get(key) for key in _JSON_SOURCE_METADATA_FIELDS
    }
    return _build_recovered_document(
        source_path=path,
        source_format="annotation-v2-legacy-schema2-json",
        raw_tracks=raw_tracks,
        metadata=metadata,
        repair_provenance=repair_provenance,
        original_trial=source_doc.get("trial"),
        source_metadata=source_metadata,
    )


__all__ = [
    "LegacyRecoveryError",
    "recover_legacy_csv",
    "recover_legacy_json",
]
