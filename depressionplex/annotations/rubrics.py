"""Canonical V2 rubric engine over independent primitive tracks.

This module mirrors the authoritative V2 HTML rule tables.  Rubrics are a
derived view: they never alter primitive truth.  ``visibility != clear`` is a
hard observability gate and always derives ``unknown`` without evaluating
behaviour predicates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .contract import (
    ANALYSIS_WINDOW_SEMANTICS,
    ASSAY_PROFILES,
    INTERVAL_SEMANTICS,
    PRIMITIVE_SET_VERSION,
    RUBRIC_VERSION,
    TOOL_VERSION,
    TRACK_DEFS,
    state_vectors,
    validate_document,
)


Predicate = Callable[[Mapping[str, str]], bool]


@dataclass(frozen=True)
class RubricDef:
    """Ordered rules and canonical primary-label priority for one assay."""

    assay: str
    rules: tuple[tuple[str, Predicate], ...]
    priority: tuple[str, ...]


@dataclass(frozen=True)
class RubricDecision:
    """All predicate hits plus the one canonical primary label."""

    all_labels: tuple[str, ...]
    primary_label: str


def _rules(*items: tuple[str, Predicate]) -> tuple[tuple[str, Predicate], ...]:
    return items


_ACADEMIC_TST = _rules(
    (
        "Mobility",
        lambda d: (
            d["hind_motion"] == "marked"
            or d["trunk_deforming"] == "true"
            or d["touch_wall"] == "true"
        )
        and d["tail_grasp"] != "true",
    ),
    (
        "Immobility",
        lambda d: not (
            d["hind_motion"] == "marked"
            or d["trunk_deforming"] == "true"
            or d["touch_wall"] == "true"
        )
        and d["tail_grasp"] != "true",
    ),
)

_CSI_TST = _rules(
    (
        "Mobility",
        lambda d: (
            d["head_neck_motion"] != "none"
            or d["fore_motion"] != "none"
            or d["hind_motion"] != "none"
            or d["trunk_deforming"] == "true"
            or d["whole_body_swing"] == "true"
        ),
    ),
    (
        "Immobility",
        lambda d: (
            d["head_neck_motion"] == "none"
            and d["fore_motion"] == "none"
            and d["hind_motion"] == "none"
            and d["trunk_deforming"] == "false"
            and d["whole_body_swing"] == "false"
        ),
    ),
)

_OURS_TST = _rules(
    (
        "Mobility",
        lambda d: (
            d["hind_motion"] == "marked" or d["trunk_deforming"] == "true"
        )
        and d["whole_body_swing"] != "true"
        and d["tail_grasp"] != "true",
    ),
    (
        "Immobility",
        lambda d: (
            d["head_neck_motion"] == "none"
            and d["fore_motion"] == "none"
            and d["hind_motion"] == "none"
            and d["trunk_deforming"] == "false"
            and d["whole_body_swing"] != "true"
            and d["tail_grasp"] != "true"
        ),
    ),
    (
        "PassiveSwing",
        lambda d: (
            d["whole_body_swing"] == "true"
            and d["head_neck_motion"] == "none"
            and d["fore_motion"] == "none"
            and d["hind_motion"] == "none"
            and d["trunk_deforming"] == "false"
            and d["tail_grasp"] != "true"
        ),
    ),
    (
        "ForelimbOnly",
        lambda d: (
            d["fore_motion"] != "none"
            and d["hind_motion"] == "none"
            and d["trunk_deforming"] == "false"
            and d["tail_grasp"] != "true"
        ),
    ),
    ("TailClimbing", lambda d: d["tail_grasp"] == "true"),
    (
        "HeadNeckStruggle",
        lambda d: (
            d["head_neck_motion"] == "marked"
            and d["hind_motion"] == "none"
            and d["trunk_deforming"] == "false"
            and d["tail_grasp"] != "true"
        ),
    ),
    (
        "ActiveDrivenSwing",
        lambda d: d["whole_body_swing"] == "true"
        and (
            d["head_neck_motion"] != "none"
            or d["fore_motion"] != "none"
            or d["hind_motion"] != "none"
            or d["trunk_deforming"] == "true"
        ),
    ),
)

_ACADEMIC_FST = _rules(
    (
        "Immobility",
        lambda d: (
            d["fore_motion"] != "marked"
            and d["hind_motion"] != "marked"
            and d["trunk_deforming"] == "false"
        ),
    ),
    (
        "Swimming",
        lambda d: d["hind_motion"] != "none" and d["wall_contact"] == "none",
    ),
    (
        "Climbing",
        lambda d: d["fore_wall_upstroke"] == "true"
        or (d["fore_motion"] == "marked" and d["wall_contact"] != "none"),
    ),
)

_CSI_FST = _rules(
    (
        "Float",
        lambda d: (
            d["head_neck_motion"] == "none"
            and d["fore_motion"] == "none"
            and d["hind_motion"] == "none"
            and d["trunk_deforming"] == "false"
        ),
    ),
    (
        "Swim",
        lambda d: (
            d["hind_motion"] != "none"
            and d["wall_contact"] == "none"
            and d["fore_wall_upstroke"] != "true"
        ),
    ),
    (
        "Climb",
        lambda d: d["fore_wall_upstroke"] == "true"
        or (d["fore_motion"] == "marked" and d["wall_contact"] != "none"),
    ),
    (
        "Struggle",
        lambda d: (
            d["trunk_deforming"] == "true"
            and d["fore_motion"] != "none"
            and d["hind_motion"] != "none"
        ),
    ),
    (
        "FullDive",
        lambda d: d["waterline_state"] in ("head_submerged", "body_submerged")
        and (d["fore_motion"] != "none" or d["hind_motion"] != "none"),
    ),
    (
        "PassiveDive",
        lambda d: d["waterline_state"] in ("head_submerged", "body_submerged")
        and d["fore_motion"] == "none"
        and d["hind_motion"] == "none",
    ),
)

_OURS_FST = _rules(
    (
        "QuietImmobility",
        lambda d: (
            d["fore_motion"] == "none"
            and d["hind_motion"] == "none"
            and d["trunk_deforming"] == "false"
            and d["waterline_state"] == "nose_above"
        ),
    ),
    (
        "PassiveResidual",
        lambda d: (
            d["fore_motion"] == "none"
            and d["hind_motion"] == "none"
            and d["trunk_deforming"] == "false"
            and d["waterline_state"] != "nose_above"
        ),
    ),
    (
        "ForeOnlySubtle",
        lambda d: (
            d["fore_motion"] == "subtle"
            and d["hind_motion"] == "none"
            and d["trunk_deforming"] == "false"
        ),
    ),
    (
        "ActiveStruggle",
        lambda d: d["hind_motion"] == "marked"
        and d["trunk_deforming"] == "true",
    ),
    (
        "EscapeDirected",
        lambda d: d["fore_wall_upstroke"] == "true"
        and d["body_axis"] == "vertical",
    ),
    (
        "Swimming",
        lambda d: (
            d["hind_motion"] != "none"
            and d["wall_contact"] == "none"
            and d["fore_wall_upstroke"] != "true"
            and d["body_translation"] != "upward"
        ),
    ),
    (
        "Climbing",
        lambda d: d["fore_wall_upstroke"] == "true"
        and d["body_axis"] != "horizontal",
    ),
)


def _declared_priority(rules: tuple[tuple[str, Predicate], ...]) -> tuple[str, ...]:
    return tuple(label for label, _predicate in rules)


RUBRICS: dict[str, RubricDef] = {
    "academic_tst": RubricDef(
        "TST", _ACADEMIC_TST, ("Mobility", "Immobility")
    ),
    "csi_tst": RubricDef("TST", _CSI_TST, ("Mobility", "Immobility")),
    "ours_tst": RubricDef("TST", _OURS_TST, _declared_priority(_OURS_TST)),
    "academic_fst": RubricDef(
        "FST", _ACADEMIC_FST, ("Climbing", "Swimming", "Immobility")
    ),
    "csi_fst": RubricDef(
        "FST",
        _CSI_FST,
        ("Struggle", "Climb", "Swim", "FullDive", "PassiveDive", "Float"),
    ),
    "ours_fst": RubricDef("FST", _OURS_FST, _declared_priority(_OURS_FST)),
}


def rubric_names(assay: str | None = None) -> tuple[str, ...]:
    """Return canonical rubric names in authoritative declaration order."""

    if assay is None:
        return tuple(RUBRICS)
    if assay not in ASSAY_PROFILES:
        raise ValueError(f"unsupported assay {assay!r}")
    return tuple(name for name, definition in RUBRICS.items() if definition.assay == assay)


def _validate_state(state: Mapping[str, str], definition: RubricDef) -> None:
    expected = set(ASSAY_PROFILES[definition.assay])
    actual = set(state)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing tracks {missing}")
        if extra:
            details.append(f"unexpected tracks {extra}")
        raise ValueError("state/profile mismatch: " + "; ".join(details))
    for track_id in ASSAY_PROFILES[definition.assay]:
        value = state[track_id]
        if value not in TRACK_DEFS[track_id].values:
            raise ValueError(
                f"state.{track_id}: expected one of {TRACK_DEFS[track_id].values}, got {value!r}"
            )


def classify_state(state: Mapping[str, str], rubric: str) -> RubricDecision:
    """Classify one independent-track state without mutating primitive truth."""

    if rubric not in RUBRICS:
        raise ValueError(f"unsupported rubric {rubric!r}")
    definition = RUBRICS[rubric]
    _validate_state(state, definition)
    if state["visibility"] != "clear":
        return RubricDecision(("unknown",), "unknown")
    hits = tuple(label for label, predicate in definition.rules if predicate(state))
    primary = next((label for label in definition.priority if label in hits), None)
    if primary is None:
        primary = hits[0] if hits else "none"
    return RubricDecision(hits, primary)


def _frame_results(
    doc: Mapping[str, Any], mouse: int, rubric: str
) -> list[dict[str, Any]]:
    definition = RUBRICS[rubric]
    if definition.assay != doc["assay"]:
        raise ValueError(
            f"rubric {rubric!r} belongs to {definition.assay}, not {doc['assay']}"
        )
    vectors = state_vectors(doc, mouse, validate=False)
    window_start, window_end = doc["analysis_window"]
    results: list[dict[str, Any]] = []
    for offset in range(window_end - window_start):
        state = {
            track_id: vectors[track_id][offset]
            for track_id in ASSAY_PROFILES[doc["assay"]]
        }
        decision = classify_state(state, rubric)
        results.append(
            {
                "frame": window_start + offset,
                "all_labels": list(decision.all_labels),
                "primary_label": decision.primary_label,
            }
        )
    return results


def derive_frame_results(
    doc: Mapping[str, Any], mouse: int, rubric: str
) -> list[dict[str, Any]]:
    """Derive one result per frame in the confirmed half-open analysis window."""

    validate_document(doc)
    if rubric not in RUBRICS:
        raise ValueError(f"unsupported rubric {rubric!r}")
    return _frame_results(doc, mouse, rubric)


def _label_summary(
    present: Sequence[bool], *, window_start: int, fps: float
) -> dict[str, Any]:
    frames = sum(present)
    intervals: list[list[int]] = []
    start: int | None = None
    for offset, is_present in enumerate(present):
        if is_present and start is None:
            start = offset
        elif not is_present and start is not None:
            intervals.append([window_start + start, window_start + offset - 1])
            start = None
    if start is not None:
        intervals.append([window_start + start, window_start + len(present) - 1])
    total = len(present)
    return {
        "frames": frames,
        "seconds": frames / fps,
        "duration_pct": (100.0 * frames / total) if total else 0.0,
        "bouts": len(intervals),
        "bout_intervals": intervals,
        "interval_semantics": INTERVAL_SEMANTICS,
    }


def summarize_frame_results(
    frame_results: Sequence[Mapping[str, Any]],
    *,
    analysis_window: Sequence[int],
    fps: float,
) -> dict[str, Any]:
    """Summarize primary labels and overlapping all-label hits."""

    if len(analysis_window) != 2:
        raise ValueError("analysis_window must have two elements")
    window_start, window_end = analysis_window
    if window_end - window_start != len(frame_results):
        raise ValueError("frame_results length does not match analysis_window")
    if fps <= 0:
        raise ValueError("fps must be positive")
    expected_frames = list(range(window_start, window_end))
    actual_frames = [item.get("frame") for item in frame_results]
    if actual_frames != expected_frames:
        raise ValueError("frame_results must be ordered and cover analysis_window exactly")

    primary_labels = sorted({str(item["primary_label"]) for item in frame_results})
    hit_labels = sorted(
        {
            str(label)
            for item in frame_results
            for label in item.get("all_labels", ())
        }
    )
    return {
        "analysis_window": [window_start, window_end],
        "analysis_window_semantics": ANALYSIS_WINDOW_SEMANTICS,
        "interval_semantics": INTERVAL_SEMANTICS,
        "total_frames": len(frame_results),
        "total_seconds": len(frame_results) / fps,
        "primary_summary": {
            label: _label_summary(
                [item["primary_label"] == label for item in frame_results],
                window_start=window_start,
                fps=fps,
            )
            for label in primary_labels
        },
        "hit_summary": {
            label: _label_summary(
                [label in item.get("all_labels", ()) for item in frame_results],
                window_start=window_start,
                fps=fps,
            )
            for label in hit_labels
        },
    }


def derive_rubric_report(
    doc: Mapping[str, Any],
    mouse: int,
    rubric: str,
    *,
    include_frames: bool = True,
) -> dict[str, Any]:
    """Derive canonical per-frame labels and a closed-interval summary."""

    validate_document(doc)
    if rubric not in RUBRICS:
        raise ValueError(f"unsupported rubric {rubric!r}")
    frames = _frame_results(doc, mouse, rubric)
    report: dict[str, Any] = {
        "format": "depressionplex.annotation.rubric-report.v2",
        "tool_version": TOOL_VERSION,
        "primitive_set_version": PRIMITIVE_SET_VERSION,
        "rubric_version": RUBRIC_VERSION,
        "trial": doc["trial"],
        "assay": doc["assay"],
        "mouse": mouse,
        "rubric": rubric,
        **summarize_frame_results(
            frames, analysis_window=doc["analysis_window"], fps=float(doc["fps"])
        ),
    }
    if include_frames:
        report["frames"] = frames
    return report


__all__ = [
    "RUBRICS",
    "RubricDecision",
    "RubricDef",
    "classify_state",
    "derive_frame_results",
    "derive_rubric_report",
    "rubric_names",
    "summarize_frame_results",
]
