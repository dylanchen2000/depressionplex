"""Strict contract for the adopted independent-track V2 annotation format.

Time semantics are deliberately asymmetric and explicit:

* annotation intervals are closed: ``[start, end]``;
* ``analysis_window`` is half-open: ``[start, end_exclusive)``.

Missing intervals mean the track default, not "unknown".  Different tracks may
overlap freely.  Intervals on the same track and mouse may not overlap.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
import hashlib
import math
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


SCHEMA = 2
FORMAT = "depressionplex.annotation.v2"
TOOL_VERSION = "2.1.0"
PRIMITIVE_SET_VERSION = "depressionplex-primitives-v2.0.0"
RUBRIC_VERSION = "depressionplex-rubrics-v2.0.0"
INTERVAL_SEMANTICS = "closed"
ANALYSIS_WINDOW_SEMANTICS = "half_open"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TST_FORMAL_FPS = 25.0
TST_FORMAL_WINDOW_FRAMES = 9000


@dataclass(frozen=True)
class TrackDef:
    """Definition of one independent V2 annotation track."""

    label: str
    kind: str
    values: tuple[str, ...]
    assays: frozenset[str]
    default: str | None


def _track(label: str, kind: str, values: Iterable[str], assays: str) -> TrackDef:
    vals = tuple(values)
    default = vals[0] if kind in ("level", "bool") else None
    return TrackDef(label, kind, vals, frozenset(assays.split(",")), default)


TRACK_DEFS: dict[str, TrackDef] = {
    "head_neck_motion": _track(
        "头颈部运动", "level", ("none", "subtle", "marked"), "TST,FST"
    ),
    "fore_motion": _track(
        "前肢运动", "level", ("none", "subtle", "marked"), "TST,FST"
    ),
    "hind_motion": _track(
        "后肢运动", "level", ("none", "subtle", "marked"), "TST,FST"
    ),
    "trunk_deforming": _track(
        "躯干动态形变", "bool", ("false", "true"), "TST,FST"
    ),
    "whole_body_swing": _track(
        "整体钟摆摆动", "bool", ("false", "true"), "TST"
    ),
    "fore_wall_upstroke": _track(
        "前肢扒壁", "bool", ("false", "true"), "FST"
    ),
    "body_translation": _track(
        "整体位移方向",
        "cat",
        ("none", "horizontal", "upward", "downward"),
        "FST",
    ),
    "body_axis": _track(
        "身体轴姿态", "cat", ("horizontal", "oblique", "vertical"), "FST"
    ),
    "wall_contact": _track(
        "缸壁接触", "cat", ("none", "forepaw", "body"), "FST"
    ),
    "waterline_state": _track(
        "水线状态",
        "cat",
        ("nose_above", "head_partial", "head_submerged", "body_submerged"),
        "FST",
    ),
    "touch_wall": _track(
        "触壁/触悬挂杆", "bool", ("false", "true"), "TST"
    ),
    "tail_grasp": _track(
        "前爪抓尾", "bool", ("false", "true"), "TST"
    ),
    "axis_orient": _track(
        "身体轴朝向", "orient", ("down", "level", "up"), "TST"
    ),
    "visibility": _track(
        "可见性", "cat", ("clear", "occluded", "uncertain"), "TST,FST"
    ),
}

ASSAY_PROFILES: dict[str, tuple[str, ...]] = {
    "TST": (
        "head_neck_motion",
        "fore_motion",
        "hind_motion",
        "trunk_deforming",
        "whole_body_swing",
        "touch_wall",
        "tail_grasp",
        "axis_orient",
        "visibility",
    ),
    "FST": (
        "head_neck_motion",
        "fore_motion",
        "hind_motion",
        "trunk_deforming",
        "fore_wall_upstroke",
        "body_translation",
        "body_axis",
        "wall_contact",
        "waterline_state",
        "visibility",
    ),
}


class AnnotationValidationError(ValueError):
    """Raised when a document is not eligible as canonical V2 truth."""

    def __init__(self, errors: Iterable[str]):
        self.errors = tuple(errors)
        super().__init__("V2 annotation validation failed:\n- " + "\n- ".join(self.errors))


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_timestamp(value: Any) -> bool:
    if not _nonempty_string(value):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _require_exact(doc: Mapping[str, Any], key: str, expected: Any, errors: list[str]) -> None:
    if doc.get(key) != expected:
        errors.append(f"{key}: expected {expected!r}, got {doc.get(key)!r}")


def _required_keys(doc: Mapping[str, Any], keys: Iterable[str], errors: list[str]) -> None:
    for key in keys:
        if key not in doc:
            errors.append(f"{key}: required field is missing")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validation_errors(
    doc: Any,
    *,
    video_path: str | Path | None = None,
    require_completed: bool = True,
) -> list[str]:
    """Return every canonical-contract violation in deterministic order.

    ``require_completed=False`` exists only for inspecting an explicitly marked
    legacy migration.  Formal pilot validation and agreement always use the
    default ``True``.
    """

    errors: list[str] = []
    if not isinstance(doc, Mapping):
        return ["document: expected a JSON object"]

    required = (
        "schema",
        "format",
        "tool_version",
        "primitive_set_version",
        "rubric_version",
        "interval_semantics",
        "analysis_window_semantics",
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
        "analysis_window_confirmed",
        "pool",
        "prefill",
        "blind",
        "completed",
        "created_at",
        "updated_at",
        "metadata_repaired",
        "tracks",
    )
    _required_keys(doc, required, errors)
    deprecated_top_level = sorted(
        key for key in ("trial_id", "video_filename", "video_sha256") if key in doc
    )
    if deprecated_top_level:
        errors.append(
            "document: deprecated alias fields are forbidden: "
            + ", ".join(deprecated_top_level)
        )
    _require_exact(doc, "schema", SCHEMA, errors)
    _require_exact(doc, "format", FORMAT, errors)
    _require_exact(doc, "tool_version", TOOL_VERSION, errors)
    _require_exact(doc, "primitive_set_version", PRIMITIVE_SET_VERSION, errors)
    _require_exact(doc, "rubric_version", RUBRIC_VERSION, errors)
    _require_exact(doc, "interval_semantics", INTERVAL_SEMANTICS, errors)
    _require_exact(
        doc, "analysis_window_semantics", ANALYSIS_WINDOW_SEMANTICS, errors
    )

    for key in ("trial", "annotator", "assignment_id"):
        if not _nonempty_string(doc.get(key)):
            errors.append(f"{key}: expected a non-empty string")
    role = doc.get("annotator_role")
    if role != "independent_rater":
        errors.append("annotator_role: expected 'independent_rater'")

    assay = doc.get("assay")
    if assay not in ASSAY_PROFILES:
        errors.append(f"assay: expected one of {sorted(ASSAY_PROFILES)}, got {assay!r}")

    fps = doc.get("fps")
    if not _is_number(fps) or not math.isfinite(float(fps)) or float(fps) <= 0:
        errors.append("fps: expected a finite positive number")
    n_frames = doc.get("n_frames")
    if not _is_int(n_frames) or n_frames <= 0:
        errors.append("n_frames: expected a positive integer")

    video = doc.get("video")
    if not isinstance(video, Mapping):
        errors.append("video: expected an object")
    else:
        _required_keys(video, ("basename", "size_bytes", "duration_sec", "sha256"), errors)
        deprecated_video = sorted(
            key for key in ("filename", "name", "duration_seconds") if key in video
        )
        if deprecated_video:
            errors.append(
                "video: deprecated alias fields are forbidden: "
                + ", ".join(deprecated_video)
            )
        if not _nonempty_string(video.get("basename")):
            errors.append("video.basename: expected a non-empty filename")
        elif (
            Path(video["basename"]).name != video["basename"]
            or "/" in video["basename"]
            or "\\" in video["basename"]
        ):
            errors.append("video.basename: must be a basename, not a path")
        if not _is_int(video.get("size_bytes")) or video.get("size_bytes", 0) <= 0:
            errors.append("video.size_bytes: expected a positive integer")
        duration = video.get("duration_sec")
        if (
            not _is_number(duration)
            or not math.isfinite(float(duration))
            or float(duration) <= 0
        ):
            errors.append("video.duration_sec: expected a finite positive number")
        elif (
            _is_number(fps)
            and math.isfinite(float(fps))
            and float(fps) > 0
            and _is_int(n_frames)
            and n_frames > 0
            and abs(float(duration) * float(fps) - n_frames) > 1.0
        ):
            errors.append(
                "video.duration_sec: duration_sec * fps must agree with n_frames within one frame"
            )
        digest = video.get("sha256")
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            errors.append("video.sha256: expected 64 lowercase hexadecimal characters")

    mice = doc.get("active_mice")
    valid_mice = False
    if not isinstance(mice, list) or not mice:
        errors.append("active_mice: expected a non-empty array")
    elif any(not _is_int(m) or not 1 <= m <= 4 for m in mice):
        errors.append("active_mice: mouse ids must be integers in 1..4")
    elif len(set(mice)) != len(mice):
        errors.append("active_mice: duplicate mouse id")
    elif mice != sorted(mice):
        errors.append("active_mice: must be sorted")
    else:
        valid_mice = True

    window = doc.get("analysis_window")
    valid_window = False
    if (
        not isinstance(window, list)
        or len(window) != 2
        or any(not _is_int(x) for x in window)
    ):
        errors.append("analysis_window: expected [start, end_exclusive] integers")
    elif _is_int(n_frames) and n_frames > 0:
        start, end_exclusive = window
        if not (0 <= start < end_exclusive <= n_frames):
            errors.append(
                "analysis_window: expected 0 <= start < end_exclusive <= n_frames"
            )
        else:
            valid_window = True
    if not isinstance(doc.get("analysis_window_confirmed"), bool):
        errors.append("analysis_window_confirmed: expected a boolean")
    elif doc.get("completed") is True and doc.get("analysis_window_confirmed") is not True:
        errors.append("analysis_window_confirmed: completed annotations require explicit confirmation")

    if (
        require_completed
        and doc.get("completed") is True
        and doc.get("pool") == "validate"
        and assay == "TST"
    ):
        if _is_number(fps) and float(fps) != TST_FORMAL_FPS:
            errors.append(f"fps: formal TST V2 requires {TST_FORMAL_FPS:g} fps")
        if valid_window and window[1] - window[0] != TST_FORMAL_WINDOW_FRAMES:
            errors.append(
                "analysis_window: formal TST V2 requires exactly "
                f"{TST_FORMAL_WINDOW_FRAMES} frames (360 seconds at 25 fps)"
            )

    pool = doc.get("pool")
    if pool not in ("train", "validate"):
        errors.append("pool: expected 'train' or 'validate'")
    if not isinstance(doc.get("prefill"), bool):
        errors.append("prefill: expected a boolean")
    if not isinstance(doc.get("blind"), bool):
        errors.append("blind: expected a boolean")
    if pool == "validate":
        if doc.get("prefill") is not False:
            errors.append("prefill: validate annotations must not be prefilled")
        if doc.get("blind") is not True:
            errors.append("blind: validate annotations must be independently blind")
    if not isinstance(doc.get("completed"), bool):
        errors.append("completed: expected a boolean")
    elif require_completed and doc.get("completed") is not True:
        errors.append("completed: formal validation requires true")

    for key in ("created_at", "updated_at"):
        if not _valid_timestamp(doc.get(key)):
            errors.append(f"{key}: expected an ISO-8601 timestamp with timezone")
    if doc.get("completed") is True:
        if "completed_at" not in doc:
            errors.append("completed_at: required when completed is true")
        elif not _valid_timestamp(doc.get("completed_at")):
            errors.append("completed_at: expected an ISO-8601 timestamp with timezone")
    elif doc.get("completed") is False and doc.get("completed_at") is not None:
        errors.append("completed_at: must be null or absent when completed is false")

    repaired = doc.get("metadata_repaired")
    if not isinstance(repaired, bool):
        errors.append("metadata_repaired: expected a boolean")
    elif repaired:
        provenance = doc.get("provenance")
        if not isinstance(provenance, Mapping) or not provenance:
            errors.append("provenance: required and non-empty when metadata_repaired is true")
        else:
            for key in (
                "repair_reason",
                "repaired_by",
                "repaired_at",
                "migration_tool_version",
            ):
                if not _nonempty_string(provenance.get(key)):
                    errors.append(f"provenance.{key}: expected a non-empty string")
            source_paths = provenance.get("source_paths")
            source_hashes = provenance.get("source_sha256")
            if (
                not isinstance(source_paths, list)
                or not source_paths
                or any(not _nonempty_string(path) for path in source_paths)
            ):
                errors.append("provenance.source_paths: expected a non-empty string array")
            if (
                not isinstance(source_hashes, list)
                or not source_hashes
                or any(
                    not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest)
                    for digest in source_hashes
                )
            ):
                errors.append(
                    "provenance.source_sha256: expected a non-empty array of lowercase SHA-256"
                )
            if (
                isinstance(source_paths, list)
                and isinstance(source_hashes, list)
                and len(source_paths) != len(source_hashes)
            ):
                errors.append(
                    "provenance: source_paths and source_sha256 must have equal length"
                )
            repaired_fields = provenance.get("repaired_fields")
            if (
                not isinstance(repaired_fields, list)
                or not repaired_fields
                or any(not _nonempty_string(field) for field in repaired_fields)
            ):
                errors.append("provenance.repaired_fields: expected a non-empty string array")
            elif len(set(repaired_fields)) != len(repaired_fields):
                errors.append("provenance.repaired_fields: duplicate field")
            if provenance.get("repaired_at") is not None and not _valid_timestamp(
                provenance.get("repaired_at")
            ):
                errors.append("provenance.repaired_at: expected ISO-8601 with timezone")
    elif "provenance" in doc and doc.get("provenance") not in (None, {}):
        errors.append("provenance: must be absent when metadata_repaired is false")

    tracks = doc.get("tracks")
    if not isinstance(tracks, Mapping):
        errors.append("tracks: expected an object")
        tracks = {}
    expected_tracks = set(ASSAY_PROFILES.get(assay, ()))
    actual_tracks = set(tracks)
    missing_tracks = sorted(expected_tracks - actual_tracks)
    extra_tracks = sorted(actual_tracks - expected_tracks)
    if missing_tracks:
        errors.append(f"tracks: missing assay tracks {missing_tracks}")
    if extra_tracks:
        errors.append(f"tracks: unexpected assay tracks {extra_tracks}")

    active_set = set(mice) if valid_mice else set()
    frame_count = n_frames if _is_int(n_frames) and n_frames > 0 else None
    for track_id in ASSAY_PROFILES.get(assay, ()):
        intervals = tracks.get(track_id)
        if not isinstance(intervals, list):
            errors.append(f"tracks.{track_id}: expected an array")
            continue
        track_def = TRACK_DEFS[track_id]
        valid_by_mouse: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
        for index, interval in enumerate(intervals):
            prefix = f"tracks.{track_id}[{index}]"
            if not isinstance(interval, Mapping):
                errors.append(f"{prefix}: expected an object")
                continue
            _required_keys(interval, ("start", "end", "value", "mouse"), errors)
            start = interval.get("start")
            end = interval.get("end")
            value = interval.get("value")
            mouse = interval.get("mouse")
            if not _is_int(start) or not _is_int(end):
                errors.append(f"{prefix}: start/end must be integers")
            elif start < 0 or end < start:
                errors.append(f"{prefix}: expected 0 <= start <= end")
            elif frame_count is not None and end >= frame_count:
                errors.append(f"{prefix}: end {end} is outside n_frames={frame_count}")
            if value not in track_def.values:
                errors.append(
                    f"{prefix}.value: expected one of {list(track_def.values)}, got {value!r}"
                )
            if not _is_int(mouse) or mouse <= 0:
                errors.append(f"{prefix}.mouse: expected a positive integer")
            elif valid_mice and mouse not in active_set:
                errors.append(f"{prefix}.mouse: {mouse} not present in active_mice")
            if _is_int(start) and _is_int(end) and _is_int(mouse) and start >= 0 and end >= start:
                valid_by_mouse[mouse].append((start, end, index))

        for mouse, spans in sorted(valid_by_mouse.items()):
            spans.sort()
            for previous, current in zip(spans, spans[1:]):
                p_start, p_end, p_idx = previous
                c_start, c_end, c_idx = current
                if c_start <= p_end:
                    relation = "duplicate" if (c_start, c_end) == (p_start, p_end) else "overlap"
                    errors.append(
                        f"tracks.{track_id}: mouse {mouse} {relation} between "
                        f"intervals {p_idx} [{p_start},{p_end}] and {c_idx} [{c_start},{c_end}]"
                    )
                elif c_start == p_end + 1:
                    previous_value = intervals[p_idx].get("value")
                    current_value = intervals[c_idx].get("value")
                    if previous_value == current_value:
                        errors.append(
                            f"tracks.{track_id}: mouse {mouse} adjacent equal-value "
                            f"intervals {p_idx} and {c_idx} must be merged"
                        )

        if (
            require_completed
            and doc.get("completed") is True
            and valid_window
            and valid_mice
            and track_def.kind in ("cat", "orient")
        ):
            window_start, window_end = window
            for mouse in mice:
                clipped = sorted(
                    (max(start, window_start), min(end + 1, window_end))
                    for start, end, _index in valid_by_mouse.get(mouse, ())
                    if start < window_end and end >= window_start
                )
                cursor = window_start
                for segment_start, segment_end in clipped:
                    if segment_start > cursor:
                        break
                    cursor = max(cursor, segment_end)
                if cursor < window_end:
                    errors.append(
                        f"tracks.{track_id}: mouse {mouse} categorical/orient track "
                        "must explicitly cover the complete analysis_window"
                    )

    if video_path is not None and isinstance(video, Mapping):
        path = Path(video_path)
        if not path.is_file():
            errors.append(f"video_path: file does not exist: {path}")
        else:
            if video.get("basename") != path.name:
                errors.append(
                    f"video.basename: metadata {video.get('basename')!r} != file {path.name!r}"
                )
            size = path.stat().st_size
            if video.get("size_bytes") != size:
                errors.append(
                    f"video.size_bytes: metadata {video.get('size_bytes')!r} != file {size}"
                )
            digest = _sha256_file(path)
            if video.get("sha256") != digest:
                errors.append("video.sha256: metadata does not match supplied video file")

    # Avoid a misleading unused local while retaining the validation branch's
    # explicitness around a well-formed half-open window.
    _ = valid_window
    return errors


def validate_document(
    doc: Any,
    *,
    video_path: str | Path | None = None,
    require_completed: bool = True,
) -> Mapping[str, Any]:
    """Validate and return ``doc``; raise with all violations otherwise."""

    errors = validation_errors(
        doc, video_path=video_path, require_completed=require_completed
    )
    if errors:
        raise AnnotationValidationError(errors)
    return doc


def atomic_segments(
    doc: Mapping[str, Any],
    mouse: int,
    *,
    window: tuple[int, int] | list[int] | None = None,
    validate: bool = True,
) -> list[dict[str, Any]]:
    """Sweep independent tracks into lossless atomic closed segments.

    Boundaries are generated at every interval ``start`` and ``end + 1``.  The
    result therefore preserves ``subtle`` versus ``marked`` and never forces
    unrelated tracks to share their original annotation boundaries.
    """

    if validate:
        validate_document(doc)
    if mouse not in doc["active_mice"]:
        raise ValueError(f"mouse {mouse} is not in active_mice")
    start, end_exclusive = tuple(window or doc["analysis_window"])
    if not (0 <= start < end_exclusive <= doc["n_frames"]):
        raise ValueError("window must satisfy 0 <= start < end_exclusive <= n_frames")

    profile = ASSAY_PROFILES[doc["assay"]]
    state = {track_id: TRACK_DEFS[track_id].default for track_id in profile}
    starts: dict[int, list[tuple[str, str]]] = defaultdict(list)
    ends: dict[int, list[tuple[str, str]]] = defaultdict(list)
    boundaries = {start, end_exclusive}

    for track_id in profile:
        for interval in doc["tracks"][track_id]:
            if interval["mouse"] != mouse:
                continue
            clipped_start = max(start, interval["start"])
            clipped_end_exclusive = min(end_exclusive, interval["end"] + 1)
            if clipped_start >= clipped_end_exclusive:
                continue
            starts[clipped_start].append((track_id, interval["value"]))
            ends[clipped_end_exclusive].append((track_id, interval["value"]))
            boundaries.add(clipped_start)
            boundaries.add(clipped_end_exclusive)

    result: list[dict[str, Any]] = []
    previous = start
    for boundary in sorted(boundaries):
        if boundary > previous:
            result.append(
                {"start": previous, "end": boundary - 1, "values": dict(state)}
            )
        # Closed intervals ending at boundary-1 leave before intervals beginning
        # at boundary enter.  Adjacent intervals of one track remain lossless.
        for track_id, _value in ends.get(boundary, ()):
            state[track_id] = TRACK_DEFS[track_id].default
        for track_id, value in starts.get(boundary, ()):
            state[track_id] = value
        previous = boundary

    return result


def state_vectors(
    doc: Mapping[str, Any],
    mouse: int,
    *,
    window: tuple[int, int] | list[int] | None = None,
    validate: bool = True,
) -> dict[str, list[str]]:
    """Expand atomic segments to per-frame categorical values for reporting."""

    start, end_exclusive = tuple(window or doc["analysis_window"])
    profile = ASSAY_PROFILES[doc["assay"]]
    vectors = {track_id: [] for track_id in profile}
    for segment in atomic_segments(doc, mouse, window=window, validate=validate):
        count = segment["end"] - segment["start"] + 1
        for track_id in profile:
            vectors[track_id].extend([segment["values"][track_id]] * count)
    expected = end_exclusive - start
    if any(len(values) != expected for values in vectors.values()):
        raise AssertionError("atomic segmentation did not cover the analysis window")
    return vectors
