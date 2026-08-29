"""Canonical V2 annotation contract, CSV, sweep, and agreement tests."""

from __future__ import annotations

from contextlib import redirect_stdout
import csv
import hashlib
import io
import json
import re
import tempfile
from pathlib import Path

from depressionplex.annotations.agreement import (
    AgreementPreflightError,
    agreement_report,
)
from depressionplex.annotations.contract import (
    ANALYSIS_WINDOW_SEMANTICS,
    ASSAY_PROFILES,
    FORMAT,
    INTERVAL_SEMANTICS,
    PRIMITIVE_SET_VERSION,
    RUBRIC_VERSION,
    SCHEMA,
    TOOL_VERSION,
    TRACK_DEFS,
    AnnotationValidationError,
    atomic_segments,
    state_vectors,
    validate_document,
)
from depressionplex.annotations.csv_v2 import (
    CSV_COLUMNS,
    LegacyCSVError,
    export_csv,
    import_csv,
    migrate_legacy_csv,
)
from depressionplex.annotations.rubrics import (
    RUBRICS,
    classify_state,
    derive_frame_results,
    derive_rubric_report,
    rubric_names,
)
from depressionplex.annotations.recovery import (
    LegacyRecoveryError,
    recover_legacy_csv,
    recover_legacy_json,
)
from depressionplex.cli import annotation_v2 as CLI


def _doc(annotator: str = "rater-a", *, mice: list[int] | None = None) -> dict:
    active_mice = list(mice or [1])
    tracks = {track: [] for track in ASSAY_PROFILES["TST"]}
    for mouse in active_mice:
        tracks["axis_orient"].append(
            {"start": 500, "end": 9499, "value": "down", "mouse": mouse}
        )
        tracks["visibility"].append(
            {"start": 500, "end": 9499, "value": "clear", "mouse": mouse}
        )
    suffix = annotator.replace(" ", "-")
    return {
        "schema": SCHEMA,
        "format": FORMAT,
        "tool_version": TOOL_VERSION,
        "primitive_set_version": PRIMITIVE_SET_VERSION,
        "rubric_version": RUBRIC_VERSION,
        "interval_semantics": INTERVAL_SEMANTICS,
        "analysis_window_semantics": ANALYSIS_WINDOW_SEMANTICS,
        "trial": "trial-001",
        "assay": "TST",
        "annotator": annotator,
        "annotator_role": "independent_rater",
        "assignment_id": f"assignment-{suffix}",
        "fps": 25,
        "n_frames": 10000,
        "video": {
            "basename": "source.mp4",
            "size_bytes": 123456,
            "duration_sec": 400.0,
            "sha256": "a" * 64,
        },
        "active_mice": active_mice,
        "analysis_window": [500, 9500],
        "analysis_window_confirmed": True,
        "pool": "validate",
        "prefill": False,
        "blind": True,
        "completed": True,
        "created_at": "2026-08-29T09:00:00+08:00",
        "updated_at": "2026-08-29T10:00:00+08:00",
        "completed_at": "2026-08-29T10:00:00+08:00",
        "metadata_repaired": False,
        "tracks": tracks,
    }


def _legacy_csv(path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "track",
                "interval_index",
                "start",
                "end",
                "value",
                "duration_frames",
                "duration_sec",
            ]
        )
        writer.writerow(["head_neck_motion", 1, 500, 524, "subtle", 25, "1.000"])


def _full_doc(n_frames: int, annotator: str = "rater-a") -> dict:
    doc = _doc(annotator)
    doc["n_frames"] = n_frames
    doc["video"]["duration_sec"] = n_frames / doc["fps"]
    doc["analysis_window"] = [0, n_frames]
    doc["tracks"]["axis_orient"] = [
        {"start": 0, "end": n_frames - 1, "value": "down", "mouse": 1}
    ]
    doc["tracks"]["visibility"] = [
        {"start": 0, "end": n_frames - 1, "value": "clear", "mouse": 1}
    ]
    return doc


def _recovery_metadata(
    n_frames: int = 9000, *, annotator: str = "legacy-a", csv_mouse: bool = False
) -> dict:
    metadata = {
        "trial": "TST::legacy-fixture::mouse1",
        "assay": "TST",
        "annotator": annotator,
        "annotator_role": "legacy_rater",
        "assignment_id": f"recovery::{annotator}::mouse1",
        "fps": 25,
        "n_frames": n_frames,
        "video": {
            "basename": "legacy-source.mp4",
            "size_bytes": 123456,
            "duration_sec": n_frames / 25,
            "sha256": "c" * 64,
        },
        "active_mice": [1],
        "analysis_window": [0, n_frames],
        "pool": "train",
        "prefill": False,
        "blind": False,
        "created_at": "2026-08-30T12:00:00+08:00",
        "full_window_approved": True,
        "fill_axis_orient_unknown": True,
    }
    if csv_mouse:
        metadata["mouse"] = 1
    return metadata


def _legacy_recovery_tracks(n_frames: int = 9000) -> dict:
    tracks = {track_id: [] for track_id in ASSAY_PROFILES["TST"]}
    tracks["head_neck_motion"] = [
        {"start": 0, "end": 9, "value": "subtle", "mouse": 1},
        {"start": 0, "end": 9, "value": "subtle", "mouse": 1},
        {"start": 5, "end": 14, "value": "subtle", "mouse": 1},
        {"start": 15, "end": 19, "value": "subtle", "mouse": 1},
        {"start": 30, "end": 39, "value": "marked", "mouse": 1},
    ]
    tracks["axis_orient"] = [
        {"start": 0, "end": 99, "value": "down", "mouse": 1}
    ]
    tracks["visibility"] = [
        {"start": 0, "end": n_frames - 1, "value": "clear", "mouse": 1}
    ]
    return tracks


def _write_legacy_recovery_json(
    path: Path, *, conflict: bool = False, annotator: str = "legacy-a"
) -> None:
    tracks = _legacy_recovery_tracks()
    if conflict:
        tracks["head_neck_motion"].append(
            {"start": 8, "end": 12, "value": "marked", "mouse": 1}
        )
    path.write_text(
        json.dumps(
            {
                "schema": 2,
                "trial": "legacy spelling",
                "assay": "TST",
                "annotator": annotator,
                "fps": 25,
                "n_frames": 9000,
                "active_mice": [1],
                "analysis_window": [0, 0],
                "pool": "validate",
                "prefill": False,
                "tracks": tracks,
            }
        ),
        encoding="utf-8",
    )


def _write_legacy_recovery_csv(path: Path, n_frames: int = 9000) -> None:
    rows = [
        ("head_neck_motion", 1, 0, 9, "subtle"),
        ("head_neck_motion", 2, 0, 9, "subtle"),
        ("head_neck_motion", 3, 5, 14, "subtle"),
        ("head_neck_motion", 4, 15, 19, "subtle"),
        ("axis_orient", 1, 0, 99, "down"),
        ("visibility", 1, 0, n_frames - 1, "clear"),
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "track",
                "interval_index",
                "start",
                "end",
                "value",
                "duration_frames",
                "duration_sec",
            ]
        )
        for track, index, start, end, value in rows:
            duration = end - start + 1
            writer.writerow(
                [track, index, start, end, value, duration, f"{duration / 25:.3f}"]
            )


def _legacy_metadata() -> dict:
    doc = _doc()
    return {
        "trial": doc["trial"],
        "assay": doc["assay"],
        "annotator": doc["annotator"],
        "annotator_role": doc["annotator_role"],
        "assignment_id": doc["assignment_id"],
        "fps": doc["fps"],
        "n_frames": doc["n_frames"],
        "video": doc["video"],
        "active_mice": doc["active_mice"],
        "mouse": 1,
        "analysis_window": doc["analysis_window"],
        "pool": "validate",
        "prefill": False,
        "blind": True,
        "created_at": doc["created_at"],
    }


def _provenance() -> dict:
    return {
        "repair_reason": "legacy CSV omitted mandatory audit metadata",
        "repaired_by": "data-manager",
        "repaired_at": "2026-08-29T11:00:00+08:00",
        "metadata_source": "signed assignment manifest A-001",
        "prefill_basis": "colleague reported annotation started from blank tracks",
        "repaired_fields": [
            "trial",
            "assay",
            "annotator",
            "mouse",
            "analysis_window",
            "video",
        ],
    }


def _state(assay: str) -> dict[str, str]:
    state = {}
    for track_id in ASSAY_PROFILES[assay]:
        default = TRACK_DEFS[track_id].default
        state[track_id] = default if default is not None else TRACK_DEFS[track_id].values[0]
    state["visibility"] = "clear"
    return state


def _assert_rejected(doc: dict, needle: str, *, require_completed: bool = True) -> None:
    try:
        validate_document(doc, require_completed=require_completed)
    except AnnotationValidationError as exc:
        assert needle in str(exc), str(exc)
    else:
        raise AssertionError(f"expected validation failure containing {needle!r}")


def test_v2_complete_document_validates() -> None:
    assert validate_document(_doc())["format"] == FORMAT
    assert TRACK_DEFS["head_neck_motion"].default == "none"
    assert TRACK_DEFS["axis_orient"].default is None


def test_v2_rejects_unversioned_old_schema2_and_zero_window() -> None:
    old = {
        "schema": 2,
        "trial": "old",
        "assay": "TST",
        "n_frames": 100,
        "analysis_window": [0, 0],
        "tracks": {},
    }
    _assert_rejected(old, "format: required")
    _assert_rejected(old, "0 <= start < end_exclusive")


def test_v2_explicitly_rejects_retired_schema_v1_and_v3() -> None:
    for schema in (1, 3):
        doc = _doc()
        doc["schema"] = schema
        _assert_rejected(doc, "schema: expected 2")


def test_v2_rejects_deprecated_identity_aliases() -> None:
    doc = _doc()
    doc["trial_id"] = doc["trial"]
    doc["video"]["duration_seconds"] = doc["video"]["duration_sec"]
    _assert_rejected(doc, "deprecated alias")


def test_v2_requires_confirmed_formal_tst_window() -> None:
    doc = _doc()
    doc["analysis_window_confirmed"] = False
    _assert_rejected(doc, "explicit confirmation")
    doc = _doc()
    doc["analysis_window"] = [500, 9499]
    _assert_rejected(doc, "at least 9000 frames")
    doc = _doc()
    doc["fps"] = 24
    _assert_rejected(doc, "requires 25 fps")


def test_v22_formal_tst_accepts_full_9661_and_11470_frame_windows() -> None:
    for n_frames in (9661, 11470):
        a = _full_doc(n_frames, "a")
        b = _full_doc(n_frames, "b")
        validate_document(a)
        report = agreement_report(a, b)
        assert report["analysis_window"] == [0, n_frames]
        assert report["mice"]["1"]["window_frames"] == n_frames


def test_v2_train_pool_may_be_prefilled_nonblind_and_nonformal_window() -> None:
    doc = _doc()
    doc["pool"] = "train"
    doc["prefill"] = True
    doc["blind"] = False
    doc["analysis_window"] = [500, 1500]
    doc["tracks"]["axis_orient"] = [
        {"start": 500, "end": 1499, "value": "down", "mouse": 1}
    ]
    doc["tracks"]["visibility"] = [
        {"start": 500, "end": 1499, "value": "clear", "mouse": 1}
    ]
    validate_document(doc)


def test_v22_legacy_rater_is_train_diagnostic_only() -> None:
    doc = _doc()
    doc["pool"] = "train"
    doc["prefill"] = True
    doc["blind"] = False
    doc["annotator_role"] = "legacy_rater"
    validate_document(doc)

    doc["blind"] = True
    _assert_rejected(doc, "legacy_rater recovery must explicitly use false")

    doc = _doc()
    doc["annotator_role"] = "legacy_rater"
    _assert_rejected(doc, "validate annotations require 'independent_rater'")

    doc = _doc()
    doc["pool"] = "train"
    doc["blind"] = False
    doc["annotator_role"] = "training_rater"
    _assert_rejected(doc, "expected 'independent_rater' or 'legacy_rater'")


def test_v22_axis_unknown_is_valid_and_does_not_change_rubric_logic() -> None:
    doc = _full_doc(9661)
    doc["tracks"]["axis_orient"] = [
        {"start": 0, "end": 9660, "value": "unknown", "mouse": 1}
    ]
    validate_document(doc)
    assert "unknown" in TRACK_DEFS["axis_orient"].values

    down = _state("TST")
    down["head_neck_motion"] = "marked"
    unknown = dict(down)
    unknown["axis_orient"] = "unknown"
    assert classify_state(down, "ours_tst") == classify_state(unknown, "ours_tst")


def test_v2_validate_pool_rejects_prefill_and_nonblind_annotation() -> None:
    doc = _doc()
    doc["prefill"] = True
    doc["blind"] = False
    _assert_rejected(doc, "must not be prefilled")
    _assert_rejected(doc, "independently blind")


def test_v2_mouse_ids_and_completion_timestamp_match_browser_contract() -> None:
    doc = _doc(mice=[1, 5])
    _assert_rejected(doc, "integers in 1..4")

    doc = _doc()
    doc["completed"] = False
    _assert_rejected(doc, "must be null or absent", require_completed=False)
    doc["completed_at"] = None
    validate_document(doc, require_completed=False)


def test_v2_video_duration_fps_and_frame_count_must_agree() -> None:
    doc = _doc()
    doc["video"]["duration_sec"] = 399.0
    _assert_rejected(doc, "within one frame")


def test_v2_unrepaired_documents_cannot_carry_migration_provenance() -> None:
    doc = _doc()
    doc["provenance"] = {"repair_reason": "should not exist"}
    _assert_rejected(doc, "must be absent")


def test_v2_rejects_bad_interval_value_mouse_and_bounds() -> None:
    doc = _doc()
    doc["tracks"]["head_neck_motion"] = [
        {"start": 0, "end": 10000, "value": "medium", "mouse": 2}
    ]
    _assert_rejected(doc, "outside n_frames")
    _assert_rejected(doc, "expected one of")
    _assert_rejected(doc, "not present in active_mice")


def test_v2_rejects_overlap_duplicate_and_adjacent_equal() -> None:
    doc = _doc()
    doc["tracks"]["head_neck_motion"] = [
        {"start": 600, "end": 700, "value": "subtle", "mouse": 1},
        {"start": 650, "end": 710, "value": "marked", "mouse": 1},
    ]
    _assert_rejected(doc, "overlap")
    doc["tracks"]["head_neck_motion"] = [
        {"start": 600, "end": 700, "value": "subtle", "mouse": 1},
        {"start": 600, "end": 700, "value": "subtle", "mouse": 1},
    ]
    _assert_rejected(doc, "duplicate")
    doc["tracks"]["head_neck_motion"] = [
        {"start": 600, "end": 700, "value": "subtle", "mouse": 1},
        {"start": 701, "end": 710, "value": "subtle", "mouse": 1},
    ]
    _assert_rejected(doc, "must be merged")


def test_v2_completed_cat_and_orient_tracks_require_full_coverage() -> None:
    doc = _doc()
    doc["tracks"]["axis_orient"] = [
        {"start": 500, "end": 9000, "value": "down", "mouse": 1}
    ]
    _assert_rejected(doc, "explicitly cover")


def test_v2_video_file_can_be_cryptographically_verified() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "source.mp4"
        path.write_bytes(b"not really an mp4")
        doc = _doc()
        doc["video"]["size_bytes"] = path.stat().st_size
        doc["video"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        validate_document(doc, video_path=path)
        doc["video"]["sha256"] = "b" * 64
        _assert_rejected_video(doc, path, "does not match")


def _assert_rejected_video(doc: dict, path: Path, needle: str) -> None:
    try:
        validate_document(doc, video_path=path)
    except AnnotationValidationError as exc:
        assert needle in str(exc)
    else:
        raise AssertionError("expected video verification failure")


def test_v2_sweep_uses_start_and_end_plus_one_without_losing_levels() -> None:
    doc = _doc()
    doc["tracks"]["head_neck_motion"] = [
        {"start": 510, "end": 519, "value": "subtle", "mouse": 1}
    ]
    doc["tracks"]["fore_motion"] = [
        {"start": 515, "end": 524, "value": "marked", "mouse": 1}
    ]
    segments = atomic_segments(doc, 1)
    spans = [(item["start"], item["end"]) for item in segments[:5]]
    assert spans == [(500, 509), (510, 514), (515, 519), (520, 524), (525, 9499)]
    assert segments[2]["values"]["head_neck_motion"] == "subtle"
    assert segments[2]["values"]["fore_motion"] == "marked"


def test_v2_sweep_keeps_mice_isolated() -> None:
    doc = _doc(mice=[1, 2])
    doc["tracks"]["head_neck_motion"] = [
        {"start": 500, "end": 600, "value": "subtle", "mouse": 1},
        {"start": 500, "end": 600, "value": "marked", "mouse": 2},
    ]
    assert atomic_segments(doc, 1)[0]["values"]["head_neck_motion"] == "subtle"
    assert atomic_segments(doc, 2)[0]["values"]["head_neck_motion"] == "marked"


def test_v2_agreement_strict_preflight_checks_video_trial_and_mouse() -> None:
    a = _doc("a")
    b = _doc("b")
    b["video"]["sha256"] = "b" * 64
    try:
        agreement_report(a, b)
    except AgreementPreflightError as exc:
        assert "video" in str(exc)
    else:
        raise AssertionError("mismatched video identity must be rejected")


def test_v2_agreement_rejects_train_pool_even_when_structurally_complete() -> None:
    a = _doc("a")
    b = _doc("b")
    a["pool"] = b["pool"] = "train"
    try:
        agreement_report(a, b)
    except AgreementPreflightError as exc:
        assert "validate pool" in str(exc)
    else:
        raise AssertionError("train annotations must not produce formal agreement")


def test_v2_level_agreement_reports_exact_weighted_active_and_confusion() -> None:
    a = _doc("a")
    b = _doc("b")
    a["tracks"]["head_neck_motion"] = [
        {"start": 500, "end": 1499, "value": "subtle", "mouse": 1}
    ]
    b["tracks"]["head_neck_motion"] = [
        {"start": 500, "end": 1499, "value": "marked", "mouse": 1}
    ]
    report = agreement_report(a, b)
    head = report["mice"]["1"]["tracks"][0]
    assert set(("kappa", "weighted_kappa", "occurrence", "confusion")) <= set(head)
    assert head["occurrence"]["kappa"] == 1.0
    assert head["kappa"] < 0.8
    assert head["passes_common_threshold"] is False
    assert head["confusion"]["subtle"]["marked"] == 1000


def test_v2_agreement_behaviour_uses_jointly_clear_frames() -> None:
    a = _doc("a")
    b = _doc("b")
    b["tracks"]["visibility"] = [
        {"start": 500, "end": 599, "value": "clear", "mouse": 1},
        {"start": 600, "end": 699, "value": "occluded", "mouse": 1},
        {"start": 700, "end": 9499, "value": "clear", "mouse": 1},
    ]
    b["tracks"]["head_neck_motion"] = [
        {"start": 600, "end": 699, "value": "marked", "mouse": 1}
    ]
    report = agreement_report(a, b)["mice"]["1"]
    assert report["jointly_clear_frames"] == 8900
    assert report["tracks"][0]["raw_agreement"] == 1.0
    assert report["tracks"][0]["scope"] == "jointly_clear"
    assert report["tracks"][0]["full_window"]["raw_agreement"] < 1.0
    visibility = next(item for item in report["tracks"] if item["track"] == "visibility")
    assert visibility["raw_agreement"] < 1.0
    assert visibility["scope"] == "full_window"
    assert report["visibility_a"]["occluded"]["frames"] == 0
    assert report["visibility_b"]["occluded"]["frames"] == 100


def test_v2_rare_bool_reports_raw_agreement_and_pabak_without_gate() -> None:
    a = _doc("a")
    b = _doc("b")
    a["tracks"]["tail_grasp"] = [
        {"start": 500, "end": 509, "value": "true", "mouse": 1}
    ]
    report = agreement_report(a, b)["mice"]["1"]
    tail = next(item for item in report["tracks"] if item["track"] == "tail_grasp")
    assert tail["rare"] is True
    assert "pabak" in tail and "raw_agreement" in tail
    assert tail["passes_common_threshold"] is None


def test_v2_rare_level_pabak_is_only_on_binary_occurrence() -> None:
    report = agreement_report(_doc("a"), _doc("b"))["mice"]["1"]
    head = next(item for item in report["tracks"] if item["track"] == "head_neck_motion")
    assert head["rare"] is True
    assert "pabak" not in head
    assert head["kappa"] is None
    assert head["occurrence"]["kappa"] is None
    assert head["occurrence"]["pabak"] == 1.0


def test_v2_rare_prevalence_is_union_not_mean_rate() -> None:
    a = _doc("a")
    b = _doc("b")
    a["tracks"]["tail_grasp"] = [
        {"start": 500, "end": 1399, "value": "true", "mouse": 1}
    ]
    report = agreement_report(a, b)["mice"]["1"]
    tail = next(item for item in report["tracks"] if item["track"] == "tail_grasp")
    assert tail["prevalence"] == 0.1
    assert tail["rare"] is False


def test_v2_categorical_tracks_never_use_rare_pabak_degradation() -> None:
    report = agreement_report(_doc("a"), _doc("b"))["mice"]["1"]
    axis = next(item for item in report["tracks"] if item["track"] == "axis_orient")
    assert axis["kappa"] is None
    assert "rare" not in axis and "pabak" not in axis
    assert axis["degenerate"] is True
    assert axis["passes_common_threshold"] is None


def test_v2_agreement_reports_each_mouse_separately() -> None:
    a = _doc("a", mice=[1, 2])
    b = _doc("b", mice=[1, 2])
    a["tracks"]["trunk_deforming"] = [
        {"start": 500, "end": 1499, "value": "true", "mouse": 1}
    ]
    b["tracks"]["trunk_deforming"] = [
        {"start": 500, "end": 1499, "value": "true", "mouse": 2}
    ]
    report = agreement_report(a, b)
    assert set(report["mice"]) == {"1", "2"}
    assert all(item["window_frames"] == 9000 for item in report["mice"].values())


def test_v2_enriched_csv_roundtrip_is_lossless() -> None:
    doc = _doc()
    doc["tracks"]["head_neck_motion"] = [
        {"start": 500, "end": 524, "value": "subtle", "mouse": 1},
        {"start": 530, "end": 554, "value": "marked", "mouse": 1},
    ]
    buffer = io.StringIO()
    export_csv(doc, buffer)
    loaded = import_csv(io.StringIO(buffer.getvalue()))
    assert loaded == doc


def test_v2_csv_is_single_mouse_and_multi_json_requires_explicit_selection() -> None:
    doc = _doc(mice=[1, 2])
    doc["tracks"]["head_neck_motion"] = [
        {"start": 500, "end": 524, "value": "subtle", "mouse": 1},
        {"start": 600, "end": 624, "value": "marked", "mouse": 2},
    ]
    try:
        export_csv(doc, io.StringIO())
    except ValueError as exc:
        assert "explicit mouse" in str(exc)
    else:
        raise AssertionError("multi-mouse JSON must require explicit CSV subject")
    buffer = io.StringIO()
    export_csv(doc, buffer, mouse=2)
    csv_rows = list(csv.DictReader(io.StringIO(buffer.getvalue())))
    assert all(row["active_mice"] == "[2]" for row in csv_rows)
    loaded = import_csv(io.StringIO(buffer.getvalue()))
    assert loaded["active_mice"] == [2]
    assert loaded["tracks"]["head_neck_motion"] == [
        {"start": 600, "end": 624, "value": "marked", "mouse": 2}
    ]


def test_v2_strict_csv_import_refuses_legacy_seven_columns() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "old.csv"
        _legacy_csv(path)
        try:
            import_csv(path)
        except LegacyCSVError as exc:
            assert "explicit metadata" in str(exc)
        else:
            raise AssertionError("legacy CSV must not be silently imported")


def test_v2_legacy_migration_requires_explicit_metadata_and_provenance() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "old.csv"
        _legacy_csv(path)
        metadata = _legacy_metadata()
        del metadata["video"]
        try:
            migrate_legacy_csv(path, metadata=metadata, repair_provenance=_provenance())
        except LegacyCSVError as exc:
            assert "video" in str(exc)
        else:
            raise AssertionError("migration must not guess video identity")


def test_v2_legacy_migration_is_repaired_unconfirmed_incomplete_draft() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "old.csv"
        _legacy_csv(path)
        doc = migrate_legacy_csv(
            path, metadata=_legacy_metadata(), repair_provenance=_provenance()
        )
        assert doc["metadata_repaired"] is True
        assert doc["analysis_window_confirmed"] is False
        assert doc["completed"] is False
        assert doc["provenance"]["source_sha256"] == [
            hashlib.sha256(path.read_bytes()).hexdigest()
        ]
        validate_document(doc, require_completed=False)
        _assert_rejected(doc, "formal validation requires true")


def test_v22_recover_json_preserves_frame_truth_and_records_every_repair() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "legacy.json"
        _write_legacy_recovery_json(source)
        recovered = recover_legacy_json(
            source,
            metadata=_recovery_metadata(),
            repair_provenance=_provenance(),
        )

        assert recovered["pool"] == "train"
        assert recovered["annotator_role"] == "legacy_rater"
        assert recovered["blind"] is False
        assert recovered["completed"] is True
        assert recovered["analysis_window_confirmed"] is True
        validate_document(recovered)

        recovery = recovered["provenance"]["recovery"]
        assert recovery["union"]["totals"]["duplicate_unions"] == 1
        assert recovery["union"]["totals"]["overlap_unions"] == 1
        assert recovery["union"]["totals"]["adjacent_unions"] == 1
        assert recovery["axis_orient_unknown_fill"]["filled_frames"] == 8900
        assert recovered["provenance"]["original_trial"] == "legacy spelling"
        assert recovered["provenance"]["source_metadata"]["pool"] == "validate"
        assert recovered["provenance"]["source_metadata"]["analysis_window"] == [0, 0]
        assert recovered["provenance"]["source_mtime"].endswith("+00:00")
        assert "tracks" in recovered["provenance"]["repaired_fields"]
        assert "tracks.axis_orient" in recovered["provenance"]["repaired_fields"]

        vectors = state_vectors(recovered, 1)
        raw_tracks = _legacy_recovery_tracks()
        for track_id in ASSAY_PROFILES["TST"]:
            if track_id == "axis_orient":
                assert vectors[track_id][:100] == ["down"] * 100
                assert vectors[track_id][100:] == ["unknown"] * 8900
                continue
            expected = [TRACK_DEFS[track_id].default] * 9000
            for interval in raw_tracks[track_id]:
                for frame in range(interval["start"], interval["end"] + 1):
                    prior = expected[frame]
                    assert prior in (TRACK_DEFS[track_id].default, interval["value"])
                    expected[frame] = interval["value"]
            assert vectors[track_id] == expected


def test_v22_recovery_hard_rejects_different_value_overlap() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "conflict.json"
        _write_legacy_recovery_json(source, conflict=True)
        try:
            recover_legacy_json(
                source,
                metadata=_recovery_metadata(),
                repair_provenance=_provenance(),
            )
        except LegacyRecoveryError as exc:
            assert "different-value overlap" in str(exc)
        else:
            raise AssertionError("recovery must never choose between conflicting values")


def test_v22_recover_json_rejects_operator_identity_override() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "legacy.json"
        _write_legacy_recovery_json(source)
        metadata = _recovery_metadata()
        metadata["n_frames"] = 9001
        metadata["analysis_window"] = [0, 9001]
        metadata["video"]["duration_sec"] = 9001 / 25
        try:
            recover_legacy_json(
                source,
                metadata=metadata,
                repair_provenance=_provenance(),
            )
        except LegacyRecoveryError as exc:
            assert "source n_frames=9000" in str(exc)
        else:
            raise AssertionError("operator metadata must not override source identity")


def test_v22_recover_csv_requires_explicit_mouse_and_can_complete_full_window() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "legacy.csv"
        _write_legacy_recovery_csv(source)
        metadata = _recovery_metadata(csv_mouse=True)
        recovered = recover_legacy_csv(
            source,
            metadata=metadata,
            repair_provenance=_provenance(),
        )
        assert recovered["active_mice"] == [metadata["mouse"]]
        assert recovered["tracks"]["head_neck_motion"] == [
            {"start": 0, "end": 19, "value": "subtle", "mouse": 1}
        ]
        assert recovered["tracks"]["axis_orient"] == [
            {"start": 0, "end": 99, "value": "down", "mouse": 1},
            {"start": 100, "end": 8999, "value": "unknown", "mouse": 1},
        ]

        del metadata["mouse"]
        try:
            recover_legacy_csv(
                source,
                metadata=metadata,
                repair_provenance=_provenance(),
            )
        except LegacyRecoveryError as exc:
            assert "mouse" in str(exc)
        else:
            raise AssertionError("legacy CSV recovery must not guess its subject")


def test_v22_legacy_recovery_is_never_formal_but_supports_diagnostic_agreement() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source_a = Path(directory) / "legacy-a.json"
        source_b = Path(directory) / "legacy-b.json"
        _write_legacy_recovery_json(source_a, annotator="legacy-a")
        _write_legacy_recovery_json(source_b, annotator="legacy-b")
        a = recover_legacy_json(
            source_a,
            metadata=_recovery_metadata(annotator="legacy-a"),
            repair_provenance=_provenance(),
        )
        b = recover_legacy_json(
            source_b,
            metadata=_recovery_metadata(annotator="legacy-b"),
            repair_provenance=_provenance(),
        )
        try:
            agreement_report(a, b)
        except AgreementPreflightError as exc:
            assert "validate pool" in str(exc)
        else:
            raise AssertionError("legacy recovery must never enter formal agreement")

        report = agreement_report(a, b, diagnostic=True)
        assert report["formal"] is False
        assert report["mode"] == "diagnostic"
        assert report["gate_policy"] == "N/A"
        for track in report["mice"]["1"]["tracks"]:
            assert track["gate_status"] == "N/A"
            assert track["passes_common_threshold"] is None
            if "occurrence" in track:
                assert track["occurrence"]["gate_status"] == "N/A"
            if "full_window" in track:
                assert track["full_window"]["gate_status"] == "N/A"
                assert track["full_window"]["passes_common_threshold"] is None

        a_path = Path(directory) / "recovered-a.json"
        b_path = Path(directory) / "recovered-b.json"
        a_path.write_text(json.dumps(a), encoding="utf-8")
        b_path.write_text(json.dumps(b), encoding="utf-8")
        output = io.StringIO()
        with redirect_stdout(output):
            assert CLI.main(
                ["agree", str(a_path), str(b_path), "--diagnostic"]
            ) == 0
        assert json.loads(output.getvalue())["formal"] is False


def test_v2_enriched_csv_detects_repeated_metadata_drift() -> None:
    doc = _doc()
    doc["tracks"]["head_neck_motion"] = [
        {"start": 500, "end": 524, "value": "subtle", "mouse": 1}
    ]
    buffer = io.StringIO()
    export_csv(doc, buffer)
    rows = list(csv.DictReader(io.StringIO(buffer.getvalue())))
    rows[1]["trial"] = "different"
    changed = io.StringIO()
    writer = csv.DictWriter(changed, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    try:
        import_csv(io.StringIO(changed.getvalue()))
    except ValueError as exc:
        assert "metadata differs" in str(exc)
    else:
        raise AssertionError("per-row metadata drift must be rejected")


def test_v2_enriched_csv_rejects_interval_payload_on_metadata_row() -> None:
    buffer = io.StringIO()
    export_csv(_doc(), buffer)
    rows = list(csv.DictReader(io.StringIO(buffer.getvalue())))
    rows[0]["track"] = "head_neck_motion"
    changed = io.StringIO()
    writer = csv.DictWriter(changed, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    try:
        import_csv(io.StringIO(changed.getvalue()))
    except ValueError as exc:
        assert "must not contain interval payload" in str(exc)
    else:
        raise AssertionError("metadata row interval payload must be rejected")


def test_v2_cli_export_and_import_csv() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "source.json"
        csv_path = root / "annotation.csv"
        output = root / "roundtrip.json"
        source.write_text(json.dumps(_doc()), encoding="utf-8")
        assert CLI.main(["export-csv", str(source), str(csv_path)]) == 0
        assert CLI.main(["import-csv", str(csv_path), str(output)]) == 0
        assert json.loads(output.read_text()) == _doc()


def test_v2_cli_migration_and_allow_draft_validation() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "old.csv"
        metadata = root / "metadata.json"
        provenance = root / "provenance.json"
        output = root / "draft.json"
        _legacy_csv(source)
        metadata.write_text(json.dumps(_legacy_metadata()), encoding="utf-8")
        provenance.write_text(json.dumps(_provenance()), encoding="utf-8")
        assert CLI.main(
            [
                "migrate-csv",
                str(source),
                str(output),
                "--metadata",
                str(metadata),
                "--provenance",
                str(provenance),
            ]
        ) == 0
        assert CLI.main(["validate", str(output), "--allow-draft"]) == 0


def test_v22_cli_recover_json_and_csv_refuse_every_overwrite() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        provenance = root / "provenance.json"
        provenance.write_text(json.dumps(_provenance()), encoding="utf-8")

        json_source = root / "legacy.json"
        json_metadata = root / "json-metadata.json"
        json_output = root / "recovered-json.json"
        _write_legacy_recovery_json(json_source)
        original_bytes = json_source.read_bytes()
        json_metadata.write_text(json.dumps(_recovery_metadata()), encoding="utf-8")
        assert CLI.main(
            [
                "recover-json",
                str(json_source),
                str(json_output),
                "--metadata",
                str(json_metadata),
                "--provenance",
                str(provenance),
            ]
        ) == 0
        assert json.loads(json_output.read_text())["completed"] is True
        try:
            CLI.main(
                [
                    "recover-json",
                    str(json_source),
                    str(json_output),
                    "--metadata",
                    str(json_metadata),
                    "--provenance",
                    str(provenance),
                ]
            )
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError("recovery CLI must reject an existing output")
        try:
            CLI.main(
                [
                    "recover-json",
                    str(json_source),
                    str(json_source),
                    "--metadata",
                    str(json_metadata),
                    "--provenance",
                    str(provenance),
                ]
            )
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError("recovery CLI must never overwrite its source")
        assert json_source.read_bytes() == original_bytes

        csv_source = root / "legacy.csv"
        csv_metadata = root / "csv-metadata.json"
        csv_output = root / "recovered-csv.json"
        _write_legacy_recovery_csv(csv_source)
        csv_metadata.write_text(
            json.dumps(_recovery_metadata(csv_mouse=True)), encoding="utf-8"
        )
        assert CLI.main(
            [
                "recover-csv",
                str(csv_source),
                str(csv_output),
                "--metadata",
                str(csv_metadata),
                "--provenance",
                str(provenance),
            ]
        ) == 0
        assert json.loads(csv_output.read_text())["active_mice"] == [1]


def test_v2_cli_never_calls_completed_training_data_formal_eligible() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "train.json"
        doc = _doc()
        doc["pool"] = "train"
        doc["prefill"] = True
        doc["blind"] = False
        doc["annotator_role"] = "legacy_rater"
        path.write_text(json.dumps(doc), encoding="utf-8")
        output = io.StringIO()
        with redirect_stdout(output):
            assert CLI.main(["validate", str(path)]) == 0
        assert json.loads(output.getvalue())["formal_eligible"] is False


def test_v2_cli_requires_source_video_verification_for_formal_eligibility() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        video = root / "source.mp4"
        video.write_bytes(b"cryptographic identity fixture")
        doc = _doc()
        doc["video"]["size_bytes"] = video.stat().st_size
        doc["video"]["sha256"] = hashlib.sha256(video.read_bytes()).hexdigest()
        annotation = root / "annotation.json"
        annotation.write_text(json.dumps(doc), encoding="utf-8")

        output = io.StringIO()
        with redirect_stdout(output):
            assert CLI.main(["validate", str(annotation)]) == 0
        assert json.loads(output.getvalue())["formal_eligible"] is False

        output = io.StringIO()
        with redirect_stdout(output):
            assert CLI.main(["validate", str(annotation), "--video", str(video)]) == 0
        verified = json.loads(output.getvalue())
        assert verified["video_verified"] is True
        assert verified["formal_eligible"] is True


def test_v22_allow_draft_can_never_bypass_formal_eligibility_gates() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        video = root / "source.mp4"
        video.write_bytes(b"formal bypass regression fixture")

        bad_fps = _doc()
        bad_fps["fps"] = 24
        bad_fps["video"]["duration_sec"] = bad_fps["n_frames"] / 24
        bad_fps["video"]["size_bytes"] = video.stat().st_size
        bad_fps["video"]["sha256"] = hashlib.sha256(video.read_bytes()).hexdigest()

        short_window = _doc()
        short_window["analysis_window"] = [500, 9499]
        short_window["tracks"]["axis_orient"] = [
            {"start": 500, "end": 9498, "value": "down", "mouse": 1}
        ]
        short_window["tracks"]["visibility"] = [
            {"start": 500, "end": 9498, "value": "clear", "mouse": 1}
        ]
        short_window["video"]["size_bytes"] = video.stat().st_size
        short_window["video"]["sha256"] = hashlib.sha256(video.read_bytes()).hexdigest()

        for index, doc in enumerate((bad_fps, short_window)):
            annotation = root / f"bad-{index}.json"
            annotation.write_text(json.dumps(doc), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                assert CLI.main(
                    [
                        "validate",
                        str(annotation),
                        "--video",
                        str(video),
                        "--allow-draft",
                    ]
                ) == 0
            report = json.loads(output.getvalue())
            assert report["video_verified"] is True
            assert report["formal_eligible"] is False


def test_v2_cli_agree_writes_strict_report() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        a = root / "a.json"
        b = root / "b.json"
        output = root / "agreement.json"
        a.write_text(json.dumps(_doc("a")), encoding="utf-8")
        b.write_text(json.dumps(_doc("b")), encoding="utf-8")
        assert CLI.main(["agree", str(a), str(b), "--mouse", "1", "--out", str(output)]) == 0
        report = json.loads(output.read_text())
        assert set(report["mice"]) == {"1"}


def test_v2_rubric_catalog_and_versions_are_canonical() -> None:
    assert rubric_names() == (
        "academic_tst",
        "csi_tst",
        "ours_tst",
        "academic_fst",
        "csi_fst",
        "ours_fst",
    )
    assert rubric_names("TST") == ("academic_tst", "csi_tst", "ours_tst")
    assert all(definition.assay in ASSAY_PROFILES for definition in RUBRICS.values())
    report = derive_rubric_report(_doc(), 1, "academic_tst", include_frames=False)
    assert report["primitive_set_version"] == PRIMITIVE_SET_VERSION
    assert report["rubric_version"] == RUBRIC_VERSION


def test_v2_rubric_preserves_subtle_versus_marked() -> None:
    subtle = _state("TST")
    subtle["hind_motion"] = "subtle"
    marked = dict(subtle)
    marked["hind_motion"] = "marked"
    assert classify_state(subtle, "academic_tst").primary_label == "Immobility"
    assert classify_state(marked, "academic_tst").primary_label == "Mobility"


def test_v2_rubric_nonclear_visibility_is_always_unknown() -> None:
    state = _state("TST")
    state["hind_motion"] = "marked"
    for visibility in ("occluded", "uncertain"):
        state["visibility"] = visibility
        decision = classify_state(state, "academic_tst")
        assert decision.primary_label == "unknown"
        assert decision.all_labels == ("unknown",)


def test_v2_rubric_reports_all_hits_and_authoritative_priority() -> None:
    academic = _state("FST")
    academic["fore_motion"] = "subtle"
    academic["hind_motion"] = "subtle"
    academic["fore_wall_upstroke"] = "true"
    decision = classify_state(academic, "academic_fst")
    assert decision.all_labels == ("Immobility", "Swimming", "Climbing")
    assert decision.primary_label == "Climbing"

    csi = _state("FST")
    csi.update(
        {
            "fore_motion": "marked",
            "hind_motion": "marked",
            "trunk_deforming": "true",
            "wall_contact": "none",
            "fore_wall_upstroke": "false",
            "waterline_state": "head_submerged",
        }
    )
    decision = classify_state(csi, "csi_fst")
    assert decision.all_labels == ("Swim", "Struggle", "FullDive")
    assert decision.primary_label == "Struggle"


def test_v2_ours_rubrics_keep_overlapping_hits() -> None:
    tst = _state("TST")
    tst["head_neck_motion"] = "marked"
    tst["whole_body_swing"] = "true"
    decision = classify_state(tst, "ours_tst")
    assert decision.all_labels == ("HeadNeckStruggle", "ActiveDrivenSwing")
    assert decision.primary_label == "HeadNeckStruggle"

    fst = _state("FST")
    fst["fore_motion"] = "subtle"
    decision = classify_state(fst, "ours_fst")
    assert decision.all_labels == ("ForeOnlySubtle",)
    assert decision.primary_label == "ForeOnlySubtle"


def test_v2_rubric_state_must_exactly_match_assay_profile() -> None:
    state = _state("TST")
    del state["visibility"]
    try:
        classify_state(state, "academic_tst")
    except ValueError as exc:
        assert "missing tracks" in str(exc)
    else:
        raise AssertionError("rubric state must include every profile track")


def test_v2_rubric_assay_mismatch_is_rejected() -> None:
    try:
        derive_frame_results(_doc(), 1, "academic_fst")
    except ValueError as exc:
        assert "belongs to FST" in str(exc)
    else:
        raise AssertionError("FST rubric must not run on a TST document")


def test_v2_rubric_derives_absolute_frames_unknown_and_closed_bouts() -> None:
    doc = _doc()
    doc["tracks"]["hind_motion"] = [
        {"start": 500, "end": 501, "value": "marked", "mouse": 1}
    ]
    doc["tracks"]["visibility"] = [
        {"start": 500, "end": 501, "value": "clear", "mouse": 1},
        {"start": 502, "end": 503, "value": "occluded", "mouse": 1},
        {"start": 504, "end": 9499, "value": "clear", "mouse": 1},
    ]
    report = derive_rubric_report(doc, 1, "academic_tst")
    frames = report["frames"]
    assert len(frames) == 9000
    assert frames[0] == {
        "frame": 500,
        "all_labels": ["Mobility"],
        "primary_label": "Mobility",
    }
    assert frames[2] == {
        "frame": 502,
        "all_labels": ["unknown"],
        "primary_label": "unknown",
    }
    assert frames[-1]["frame"] == 9499
    summary = report["primary_summary"]
    assert summary["Mobility"]["frames"] == 2
    assert summary["Mobility"]["bout_intervals"] == [[500, 501]]
    assert summary["unknown"]["frames"] == 2
    assert summary["unknown"]["bout_intervals"] == [[502, 503]]
    assert summary["Immobility"]["frames"] == 8996
    assert summary["Immobility"]["bout_intervals"] == [[504, 9499]]
    assert all(
        item["interval_semantics"] == INTERVAL_SEMANTICS
        for item in summary.values()
    )
    assert report["analysis_window"] == [500, 9500]
    assert report["analysis_window_semantics"] == ANALYSIS_WINDOW_SEMANTICS
    assert report["interval_semantics"] == INTERVAL_SEMANTICS


def test_v2_rubric_report_can_omit_large_per_frame_payload() -> None:
    report = derive_rubric_report(_doc(), 1, "csi_tst", include_frames=False)
    assert "frames" not in report
    assert report["primary_summary"]["Immobility"]["frames"] == 9000


def test_v2_authoritative_html_and_python_contract_do_not_drift() -> None:
    repo = Path(__file__).resolve().parents[1]
    html_path = repo / "tools" / "annotation" / "DepressionPlex_annotation_tool_v2.html"
    html = html_path.read_text(encoding="utf-8")

    schema_match = re.search(r"\bSCHEMA:\s*(\d+)\s*,", html)
    assert schema_match and int(schema_match.group(1)) == SCHEMA
    expected_strings = {
        "FORMAT": FORMAT,
        "TOOL_VERSION": TOOL_VERSION,
        "PRIMITIVE_SET_VERSION": PRIMITIVE_SET_VERSION,
        "RUBRIC_VERSION": RUBRIC_VERSION,
    }
    for name, expected in expected_strings.items():
        match = re.search(rf"\b{name}:\s*'([^']+)'\s*,", html)
        assert match, f"authoritative HTML is missing {name}"
        assert match.group(1) == expected, f"{name} drifted between HTML and Python"

    header_match = re.search(r"const header=\[([^\]]+)\];", html)
    assert header_match, "authoritative HTML is missing enriched CSV header"
    html_columns = tuple(re.findall(r"'([^']+)'", header_match.group(1)))
    assert html_columns == CSV_COLUMNS
    assert "['metadata',...metadata,...Array(8).fill('')]" in html
    assert "extraTracks.length" in html
    assert "doc.annotator_role==='legacy_rater'&&doc.blind!==false" in html
    assert "this.migrateLegacyV2(source,file.name,sourceSha256)" in html
    assert "sha256Text" not in html
