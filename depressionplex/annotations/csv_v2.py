"""Lossless enriched CSV exchange and explicit legacy-CSV migration for V2."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, TextIO

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
    validate_document,
)


CSV_FORMAT = "depressionplex.annotation.v2.csv"
CSV_VERSION = "1.0.0"

LEGACY_COLUMNS = (
    "track",
    "interval_index",
    "start",
    "end",
    "value",
    "duration_frames",
    "duration_sec",
)

CSV_COLUMNS = (
    "row_type",
    "csv_format",
    "csv_version",
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
    "video_basename",
    "video_size_bytes",
    "video_duration_sec",
    "video_sha256",
    "active_mice",
    "analysis_start",
    "analysis_end_exclusive",
    "analysis_window_confirmed",
    "pool",
    "prefill",
    "blind",
    "completed",
    "created_at",
    "updated_at",
    "completed_at",
    "metadata_repaired",
    "provenance",
    "track",
    "interval_index",
    "start",
    "end",
    "value",
    "mouse",
    "duration_frames",
    "duration_sec",
)

_METADATA_COLUMNS = CSV_COLUMNS[1:34]


class CSVContractError(ValueError):
    """Raised for a malformed enriched CSV."""


class LegacyCSVError(CSVContractError):
    """Raised when a legacy 7-column CSV is passed to the strict importer."""


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _parse_bool(value: str, field: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise CSVContractError(f"{field}: expected 'true' or 'false', got {value!r}")


def _parse_int(value: str, field: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise CSVContractError(f"{field}: expected an integer, got {value!r}") from exc
    if str(parsed) != value.strip():
        raise CSVContractError(f"{field}: non-canonical integer {value!r}")
    return parsed


def _parse_float(value: str, field: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise CSVContractError(f"{field}: expected a number, got {value!r}") from exc
    if not math.isfinite(parsed):
        raise CSVContractError(f"{field}: expected a finite number")
    return parsed


def _metadata_row(doc: Mapping[str, Any], mouse: int) -> dict[str, str]:
    video = doc["video"]
    provenance = doc.get("provenance")
    return {
        "csv_format": CSV_FORMAT,
        "csv_version": CSV_VERSION,
        "schema": str(doc["schema"]),
        "format": doc["format"],
        "tool_version": doc["tool_version"],
        "primitive_set_version": doc["primitive_set_version"],
        "rubric_version": doc["rubric_version"],
        "interval_semantics": doc["interval_semantics"],
        "analysis_window_semantics": doc["analysis_window_semantics"],
        "trial": doc["trial"],
        "assay": doc["assay"],
        "annotator": doc["annotator"],
        "annotator_role": doc["annotator_role"],
        "assignment_id": doc["assignment_id"],
        "fps": format(float(doc["fps"]), ".12g"),
        "n_frames": str(doc["n_frames"]),
        "video_basename": video["basename"],
        "video_size_bytes": str(video["size_bytes"]),
        "video_duration_sec": format(float(video["duration_sec"]), ".12g"),
        "video_sha256": video["sha256"],
        "active_mice": json.dumps([mouse], separators=(",", ":")),
        "analysis_start": str(doc["analysis_window"][0]),
        "analysis_end_exclusive": str(doc["analysis_window"][1]),
        "analysis_window_confirmed": _bool_text(doc["analysis_window_confirmed"]),
        "pool": doc["pool"],
        "prefill": _bool_text(doc["prefill"]),
        "blind": _bool_text(doc["blind"]),
        "completed": _bool_text(doc["completed"]),
        "created_at": doc["created_at"],
        "updated_at": doc["updated_at"],
        "completed_at": doc.get("completed_at", ""),
        "metadata_repaired": _bool_text(doc["metadata_repaired"]),
        "provenance": (
            json.dumps(provenance, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if provenance is not None
            else ""
        ),
    }


def export_csv(
    doc: Mapping[str, Any],
    destination: str | Path | TextIO,
    *,
    mouse: int | None = None,
) -> None:
    """Write a metadata-complete single-mouse V2 CSV.

    Multi-mouse JSON is supported, but the caller must explicitly select one
    mouse.  This prevents one CSV from silently mixing independent subjects.
    """

    validate_document(doc)
    active_mice = list(doc["active_mice"])
    if mouse is None:
        if len(active_mice) != 1:
            raise CSVContractError(
                "multi-mouse JSON requires an explicit mouse for CSV export"
            )
        mouse = active_mice[0]
    if mouse not in active_mice:
        raise CSVContractError(f"mouse {mouse} is not in active_mice")
    metadata = _metadata_row(doc, mouse)
    close = False
    if hasattr(destination, "write"):
        handle = destination  # type: ignore[assignment]
    else:
        handle = Path(destination).open("w", encoding="utf-8", newline="")
        close = True
    try:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerow({**metadata, "row_type": "metadata"})
        for track_id in ASSAY_PROFILES[doc["assay"]]:
            counters: dict[int, int] = {}
            intervals = sorted(
                (
                    interval
                    for interval in doc["tracks"][track_id]
                    if interval["mouse"] == mouse
                ),
                key=lambda item: (item["mouse"], item["start"], item["end"]),
            )
            for interval in intervals:
                mouse = interval["mouse"]
                counters[mouse] = counters.get(mouse, 0) + 1
                duration_frames = interval["end"] - interval["start"] + 1
                writer.writerow(
                    {
                        **metadata,
                        "row_type": "interval",
                        "track": track_id,
                        "interval_index": counters[mouse],
                        "start": interval["start"],
                        "end": interval["end"],
                        "value": interval["value"],
                        "mouse": mouse,
                        "duration_frames": duration_frames,
                        "duration_sec": f"{duration_frames / float(doc['fps']):.6f}",
                    }
                )
    finally:
        if close:
            handle.close()


def _read_rows(source: str | Path | TextIO) -> tuple[list[str], list[dict[str, str]]]:
    close = False
    if hasattr(source, "read"):
        handle = source  # type: ignore[assignment]
    else:
        handle = Path(source).open("r", encoding="utf-8-sig", newline="")
        close = True
    try:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or ())
        rows = [dict(row) for row in reader]
    finally:
        if close:
            handle.close()
    return fields, rows


def _doc_from_metadata(row: Mapping[str, str]) -> dict[str, Any]:
    if row["csv_format"] != CSV_FORMAT or row["csv_version"] != CSV_VERSION:
        raise CSVContractError("unsupported csv_format/csv_version")
    if not row["active_mice"]:
        raise CSVContractError("active_mice: empty")
    try:
        mice = json.loads(row["active_mice"])
    except json.JSONDecodeError as exc:
        raise CSVContractError("active_mice: expected a JSON array") from exc
    if (
        not isinstance(mice, list)
        or len(mice) != 1
        or not isinstance(mice[0], int)
        or isinstance(mice[0], bool)
    ):
        raise CSVContractError("active_mice: enriched CSV must contain one integer mouse")
    provenance_text = row["provenance"]
    provenance = None
    if provenance_text:
        try:
            provenance = json.loads(provenance_text)
        except json.JSONDecodeError as exc:
            raise CSVContractError("provenance: invalid JSON") from exc
    doc: dict[str, Any] = {
        "schema": _parse_int(row["schema"], "schema"),
        "format": row["format"],
        "tool_version": row["tool_version"],
        "primitive_set_version": row["primitive_set_version"],
        "rubric_version": row["rubric_version"],
        "interval_semantics": row["interval_semantics"],
        "analysis_window_semantics": row["analysis_window_semantics"],
        "trial": row["trial"],
        "assay": row["assay"],
        "annotator": row["annotator"],
        "annotator_role": row["annotator_role"],
        "assignment_id": row["assignment_id"],
        "fps": _parse_float(row["fps"], "fps"),
        "n_frames": _parse_int(row["n_frames"], "n_frames"),
        "video": {
            "basename": row["video_basename"],
            "size_bytes": _parse_int(row["video_size_bytes"], "video_size_bytes"),
            "duration_sec": _parse_float(row["video_duration_sec"], "video_duration_sec"),
            "sha256": row["video_sha256"],
        },
        "active_mice": mice,
        "analysis_window": [
            _parse_int(row["analysis_start"], "analysis_start"),
            _parse_int(row["analysis_end_exclusive"], "analysis_end_exclusive"),
        ],
        "analysis_window_confirmed": _parse_bool(
            row["analysis_window_confirmed"], "analysis_window_confirmed"
        ),
        "pool": row["pool"],
        "prefill": _parse_bool(row["prefill"], "prefill"),
        "blind": _parse_bool(row["blind"], "blind"),
        "completed": _parse_bool(row["completed"], "completed"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "metadata_repaired": _parse_bool(
            row["metadata_repaired"], "metadata_repaired"
        ),
        "tracks": {track_id: [] for track_id in ASSAY_PROFILES.get(row["assay"], ())},
    }
    if row["completed_at"]:
        doc["completed_at"] = row["completed_at"]
    if provenance is not None:
        doc["provenance"] = provenance
    return doc


def import_csv(source: str | Path | TextIO) -> dict[str, Any]:
    """Read enriched V2 CSV; explicitly refuse the old metadata-free CSV."""

    fields, rows = _read_rows(source)
    if tuple(fields) == LEGACY_COLUMNS:
        raise LegacyCSVError(
            "legacy 7-column CSV has no authoritative trial/video/mouse/window metadata; "
            "use migrate_legacy_csv with explicit metadata and repair provenance"
        )
    if tuple(fields) != CSV_COLUMNS:
        raise CSVContractError("CSV header does not exactly match enriched V2 contract")
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise CSVContractError("CSV row has a missing or extra column")
    if not rows or rows[0]["row_type"] != "metadata":
        raise CSVContractError("first CSV row must be the single metadata row")
    if sum(row["row_type"] == "metadata" for row in rows) != 1:
        raise CSVContractError("CSV must contain exactly one metadata row")
    payload_columns = CSV_COLUMNS[34:]
    if any(rows[0].get(key) not in (None, "") for key in payload_columns):
        raise CSVContractError("metadata row must not contain interval payload")

    metadata = {key: rows[0][key] for key in _METADATA_COLUMNS}
    for number, row in enumerate(rows[1:], start=3):
        if row["row_type"] != "interval":
            raise CSVContractError(f"row {number}: expected row_type='interval'")
        changed = [key for key in _METADATA_COLUMNS if row[key] != metadata[key]]
        if changed:
            raise CSVContractError(
                f"row {number}: repeated metadata differs in {', '.join(changed)}"
            )

    doc = _doc_from_metadata(rows[0])
    indices: dict[tuple[str, int], list[int]] = {}
    for number, row in enumerate(rows[1:], start=3):
        track_id = row["track"]
        if track_id not in doc["tracks"]:
            raise CSVContractError(f"row {number}: track {track_id!r} not in assay profile")
        mouse = _parse_int(row["mouse"], f"row {number} mouse")
        if mouse != doc["active_mice"][0]:
            raise CSVContractError(
                f"row {number}: mouse must equal the CSV's sole active_mice entry"
            )
        interval_index = _parse_int(
            row["interval_index"], f"row {number} interval_index"
        )
        start = _parse_int(row["start"], f"row {number} start")
        end = _parse_int(row["end"], f"row {number} end")
        duration_frames = _parse_int(
            row["duration_frames"], f"row {number} duration_frames"
        )
        if duration_frames != end - start + 1:
            raise CSVContractError(f"row {number}: duration_frames is inconsistent")
        duration_sec = _parse_float(row["duration_sec"], f"row {number} duration_sec")
        if not math.isclose(
            duration_sec, duration_frames / doc["fps"], rel_tol=0.0, abs_tol=5e-7
        ):
            raise CSVContractError(f"row {number}: duration_sec is inconsistent")
        indices.setdefault((track_id, mouse), []).append(interval_index)
        doc["tracks"][track_id].append(
            {"start": start, "end": end, "value": row["value"], "mouse": mouse}
        )
    for key, values in indices.items():
        if values != list(range(1, len(values) + 1)):
            raise CSVContractError(
                f"interval_index for track={key[0]} mouse={key[1]} must be 1..N in row order"
            )
    validate_document(doc)
    return doc


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


_LEGACY_METADATA_REQUIRED = (
    "trial",
    "assay",
    "annotator",
    "annotator_role",
    "assignment_id",
    "fps",
    "n_frames",
    "video",
    "active_mice",
    "mouse",
    "analysis_window",
    "pool",
    "prefill",
    "blind",
    "created_at",
)


def migrate_legacy_csv(
    source: str | Path,
    *,
    metadata: Mapping[str, Any],
    repair_provenance: Mapping[str, Any],
) -> dict[str, Any]:
    """Migrate the adopted tool's old 7-column CSV without guessing metadata.

    The result is deliberately incomplete with an unconfirmed window.  A human
    must review it in the V2 workflow before it can pass formal validation.
    """

    path = Path(source)
    fields, rows = _read_rows(path)
    if tuple(fields) != LEGACY_COLUMNS:
        raise LegacyCSVError("source is not the known 7-column legacy V2 CSV")
    missing = [key for key in _LEGACY_METADATA_REQUIRED if key not in metadata]
    if missing:
        raise LegacyCSVError("explicit metadata is missing: " + ", ".join(missing))
    for key in (
        "repair_reason",
        "repaired_by",
        "repaired_at",
        "metadata_source",
    ):
        value = repair_provenance.get(key)
        if not isinstance(value, str) or not value.strip():
            raise LegacyCSVError(f"repair provenance requires non-empty {key}")
    repaired_fields = repair_provenance.get("repaired_fields")
    if (
        not isinstance(repaired_fields, list)
        or not repaired_fields
        or any(not isinstance(value, str) or not value.strip() for value in repaired_fields)
    ):
        raise LegacyCSVError("repair provenance requires non-empty repaired_fields")

    assay = metadata["assay"]
    if assay not in ASSAY_PROFILES:
        raise LegacyCSVError(f"unsupported assay {assay!r}")
    mouse = metadata["mouse"]
    if not isinstance(mouse, int) or isinstance(mouse, bool):
        raise LegacyCSVError("metadata.mouse must be an integer")
    if metadata["active_mice"] != [mouse]:
        raise LegacyCSVError(
            "legacy single-mouse CSV requires active_mice to equal [mouse] exactly"
        )

    provenance = dict(repair_provenance)
    provenance.update(
        {
            "source_paths": [path.name],
            "source_sha256": [_sha256_path(path)],
            "source_format": "annotation-v2-legacy-csv-7-column",
            "migration_tool_version": TOOL_VERSION,
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
        "assay": assay,
        "annotator": metadata["annotator"],
        "annotator_role": metadata["annotator_role"],
        "assignment_id": metadata["assignment_id"],
        "fps": metadata["fps"],
        "n_frames": metadata["n_frames"],
        "video": dict(metadata["video"]),
        "active_mice": list(metadata["active_mice"]),
        "analysis_window": list(metadata["analysis_window"]),
        "analysis_window_confirmed": False,
        "pool": metadata["pool"],
        "prefill": metadata["prefill"],
        "blind": metadata["blind"],
        "completed": False,
        "created_at": metadata["created_at"],
        "updated_at": repair_provenance["repaired_at"],
        "metadata_repaired": True,
        "provenance": provenance,
        "tracks": {track_id: [] for track_id in ASSAY_PROFILES[assay]},
    }

    expected_indices: dict[str, int] = {}
    for number, row in enumerate(rows, start=2):
        track_id = row["track"]
        if track_id not in doc["tracks"]:
            raise LegacyCSVError(
                f"row {number}: track {track_id!r} is not in the {assay} profile"
            )
        expected_indices[track_id] = expected_indices.get(track_id, 0) + 1
        interval_index = _parse_int(row["interval_index"], f"row {number} interval_index")
        if interval_index != expected_indices[track_id]:
            raise LegacyCSVError(
                f"row {number}: interval_index for {track_id} must be sequential"
            )
        start = _parse_int(row["start"], f"row {number} start")
        end = _parse_int(row["end"], f"row {number} end")
        duration_frames = _parse_int(
            row["duration_frames"], f"row {number} duration_frames"
        )
        if duration_frames != end - start + 1:
            raise LegacyCSVError(f"row {number}: duration_frames is inconsistent")
        duration_sec = _parse_float(row["duration_sec"], f"row {number} duration_sec")
        if not math.isclose(
            duration_sec,
            duration_frames / float(metadata["fps"]),
            rel_tol=0.0,
            abs_tol=5e-4,
        ):
            raise LegacyCSVError(f"row {number}: duration_sec is inconsistent")
        value = row["value"]
        if value not in TRACK_DEFS[track_id].values:
            raise LegacyCSVError(
                f"row {number}: value {value!r} invalid for track {track_id}"
            )
        doc["tracks"][track_id].append(
            {"start": start, "end": end, "value": value, "mouse": mouse}
        )

    # Structural checks still apply, but no migration may become formal truth
    # until a human confirms the analysis window and completion state.
    validate_document(doc, require_completed=False)
    return doc


__all__ = [
    "CSV_COLUMNS",
    "CSV_FORMAT",
    "CSV_VERSION",
    "CSVContractError",
    "LegacyCSVError",
    "export_csv",
    "import_csv",
    "migrate_legacy_csv",
]
