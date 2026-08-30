"""Disagreement review queue tests."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile

from depressionplex.annotations.agreement import agreement_report
from depressionplex.annotations.contract import ASSAY_PROFILES
from depressionplex.annotations.review import (
    ReviewBuildError,
    build_disagreement_analysis,
    build_report_artifact,
    review_decision_rows,
)
from depressionplex.cli.annotation_review import _require_unmodified_generated_package


def _review_doc(annotator: str) -> dict:
    n_frames = 9000
    tracks = {track: [] for track in ASSAY_PROFILES["TST"]}
    tracks["axis_orient"] = [
        {"start": 0, "end": n_frames - 1, "value": "down", "mouse": 1}
    ]
    tracks["visibility"] = [
        {"start": 0, "end": n_frames - 1, "value": "clear", "mouse": 1}
    ]
    return {
        "schema": 2,
        "format": "depressionplex.annotation.v2",
        "tool_version": "2.2.0",
        "primitive_set_version": "depressionplex-primitives-v2.1.0",
        "rubric_version": "depressionplex-rubrics-v2.1.0",
        "interval_semantics": "closed",
        "analysis_window_semantics": "half_open",
        "trial": "tst-review-fixture-m1",
        "assay": "TST",
        "annotator": annotator,
        "annotator_role": "independent_rater",
        "assignment_id": f"review-{annotator}",
        "fps": 25,
        "n_frames": n_frames,
        "video": {
            "basename": "fixture.mp4",
            "size_bytes": 1234,
            "duration_sec": 360.0,
            "sha256": "d" * 64,
        },
        "active_mice": [1],
        "analysis_window": [0, n_frames],
        "analysis_window_confirmed": True,
        "pool": "validate",
        "prefill": False,
        "blind": True,
        "completed": True,
        "created_at": "2026-08-30T08:00:00+08:00",
        "updated_at": "2026-08-30T09:00:00+08:00",
        "completed_at": "2026-08-30T09:00:00+08:00",
        "metadata_repaired": False,
        "tracks": tracks,
    }


def _review_pair() -> tuple[dict, dict]:
    left = _review_doc("rater-a")
    right = _review_doc("rater-b")
    left["tracks"]["head_neck_motion"] = [
        {"start": 100, "end": 119, "value": "subtle", "mouse": 1},
        {"start": 300, "end": 350, "value": "subtle", "mouse": 1},
    ]
    right["tracks"]["head_neck_motion"] = [
        {"start": 100, "end": 119, "value": "marked", "mouse": 1},
        {"start": 300, "end": 350, "value": "marked", "mouse": 1},
    ]
    left["tracks"]["fore_motion"] = [
        {"start": 500, "end": 539, "value": "subtle", "mouse": 1}
    ]
    right["tracks"]["fore_motion"] = [
        {"start": 500, "end": 539, "value": "marked", "mouse": 1}
    ]
    left["tracks"]["hind_motion"] = [
        {"start": 700, "end": 729, "value": "marked", "mouse": 1}
    ]
    right["tracks"]["hind_motion"] = [
        {"start": 700, "end": 729, "value": "subtle", "mouse": 1}
    ]
    left["tracks"]["trunk_deforming"] = [
        {"start": 900, "end": 924, "value": "true", "mouse": 1}
    ]
    left["tracks"]["whole_body_swing"] = [
        {"start": 1100, "end": 1124, "value": "true", "mouse": 1}
    ]
    left["tracks"]["axis_orient"] = [
        {"start": 0, "end": 1299, "value": "down", "mouse": 1},
        {"start": 1300, "end": 1310, "value": "unknown", "mouse": 1},
        {"start": 1311, "end": 8999, "value": "down", "mouse": 1},
    ]
    return left, right


def test_review_excludes_axis_missingness_and_covers_primary_pairs() -> None:
    left, right = _review_pair()
    analysis = build_disagreement_analysis(
        left,
        right,
        samples_per_pair=1,
        max_evidence=10,
        clip_seconds=4,
        max_merged_seconds=8,
    )
    assert analysis["headline"]["legacy_missingness_frames"] == 11
    assert analysis["headline"]["calibration_pair_strata"] == 5
    assert analysis["headline"]["selected_pair_strata"] == 5
    assert analysis["headline"]["review_clips_overlapping_legacy_missingness"] == 0
    assert all(
        row["difference_kind"] == "sop_calibration"
        for row in analysis["selected_evidence"]
    )
    assert not any(row["track"] == "axis_orient" for row in analysis["selected_evidence"])
    assert len(review_decision_rows(analysis)) == 5


def test_review_selection_is_deterministic_and_prefers_longest_segment() -> None:
    left, right = _review_pair()
    first = build_disagreement_analysis(left, right, samples_per_pair=1, max_evidence=10)
    second = build_disagreement_analysis(
        deepcopy(left), deepcopy(right), samples_per_pair=1, max_evidence=10
    )
    first_signature = [
        (row["track"], row["label_pair"], row["start_frame"], row["end_frame"])
        for row in first["selected_evidence"]
    ]
    second_signature = [
        (row["track"], row["label_pair"], row["start_frame"], row["end_frame"])
        for row in second["selected_evidence"]
    ]
    assert first_signature == second_signature
    head = next(row for row in first["selected_evidence"] if row["track"] == "head_neck_motion")
    assert (head["start_frame"], head["end_frame"]) == (300, 350)


def test_review_report_uses_executed_sql_sources_and_bounded_rows() -> None:
    left, right = _review_pair()
    analysis = build_disagreement_analysis(left, right, samples_per_pair=1, max_evidence=10)
    artifact = build_report_artifact(analysis)
    assert artifact["surface"] == "report"
    assert artifact["manifest"]["blocks"][0]["body"] == (
        "# DepressionPlex TST 双标分歧复核包"
    )
    assert any(block["type"] == "chart" for block in artifact["manifest"]["blocks"])
    sql_sources = [
        source for source in artifact["sources"] if source.get("query", {}).get("sql")
    ]
    assert len(sql_sources) == 3
    assert all(source["query"]["sql"].startswith("SELECT") for source in sql_sources)
    for rows in artifact["snapshot"]["datasets"].values():
        for row in rows:
            assert all(not isinstance(value, (dict, list)) for value in row.values())


def test_review_decisions_mark_center_only_clips_as_not_boundary_reviewable() -> None:
    left, right = _review_pair()
    analysis = build_disagreement_analysis(
        left,
        right,
        samples_per_pair=1,
        max_evidence=10,
        clip_seconds=1,
        max_merged_seconds=1,
    )
    decisions = review_decision_rows(analysis)
    assert [row["clip_id"] for row in decisions] == sorted(
        row["clip_id"] for row in decisions
    )
    assert analysis["headline"]["semantic_only_evidence_count"] > 0
    assert any(row["boundary_reviewable"] == "no" for row in decisions)
    for row in decisions:
        assert row["clip_start_frame"] <= row["source_start_frame"]
        assert row["source_end_frame_exclusive"] <= row["clip_end_frame_exclusive"]
        if row["boundary_reviewable"] == "no":
            assert row["boundary_decision"].startswith("N/A")


def test_review_diagnostic_identity_and_full_window_scope_are_enforced() -> None:
    left, right = _review_pair()
    left["tracks"]["visibility"] = [
        {"start": 0, "end": 299, "value": "clear", "mouse": 1},
        {"start": 300, "end": 350, "value": "occluded", "mouse": 1},
        {"start": 351, "end": 8999, "value": "clear", "mouse": 1},
    ]
    report = agreement_report(left, right, mouse=1, diagnostic=True)
    analysis = build_disagreement_analysis(
        left,
        right,
        diagnostic_report=report,
        samples_per_pair=1,
        max_evidence=10,
    )
    assert analysis["quality_checks"]["agreement_report_reconciliation"] == "passed"

    bad_report = deepcopy(report)
    bad_report["trial"] = "wrong-trial"
    try:
        build_disagreement_analysis(
            left,
            right,
            diagnostic_report=bad_report,
            samples_per_pair=1,
            max_evidence=10,
        )
    except ReviewBuildError as error:
        assert "trial mismatch" in str(error)
    else:
        raise AssertionError("mismatched diagnostic identity must be rejected")


def test_review_force_guard_rejects_edited_generated_package() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        output = root / "review.csv"
        output.write_text("pending\n", encoding="utf-8")
        payload = output.read_bytes()
        manifest = {
            "format": "depressionplex.annotation.disagreement-review-package.v1",
            "outputs": [
                {
                    "path": output.name,
                    "size_bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            ],
        }
        (root / "package_manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        _require_unmodified_generated_package(root)
        output.write_text("reviewed\n", encoding="utf-8")
        try:
            _require_unmodified_generated_package(root)
        except ReviewBuildError as error:
            assert "was edited" in str(error)
        else:
            raise AssertionError("edited human decision file must never be replaced")
