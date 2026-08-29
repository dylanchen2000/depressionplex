"""Strict, mouse-isolated agreement reporting for canonical V2 annotations."""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Mapping, Sequence

from .contract import ASSAY_PROFILES, TRACK_DEFS, state_vectors, validate_document


class AgreementPreflightError(ValueError):
    """Raised before metrics when two documents do not identify the same truth."""


_MATCH_FIELDS = (
    "schema",
    "format",
    "tool_version",
    "primitive_set_version",
    "rubric_version",
    "interval_semantics",
    "analysis_window_semantics",
    "trial",
    "assay",
    "fps",
    "n_frames",
    "analysis_window",
    "analysis_window_confirmed",
    "active_mice",
    "video",
)


def agreement_preflight(a: Mapping[str, Any], b: Mapping[str, Any]) -> None:
    """Validate both documents and enforce identity/independence invariants."""

    validate_document(a)
    validate_document(b)
    mismatches = [key for key in _MATCH_FIELDS if a.get(key) != b.get(key)]
    if mismatches:
        raise AgreementPreflightError(
            "agreement identity mismatch: " + ", ".join(mismatches)
        )
    if a["annotator"] == b["annotator"]:
        raise AgreementPreflightError("agreement requires two different annotators")
    if a["pool"] != "validate" or b["pool"] != "validate":
        raise AgreementPreflightError("agreement is restricted to the validate pool")
    if not a["blind"] or not b["blind"] or a["prefill"] or b["prefill"]:
        raise AgreementPreflightError("agreement requires two blind, non-prefilled annotations")
    if not a["completed"] or not b["completed"]:
        raise AgreementPreflightError("agreement requires completed annotations")
    if not a["analysis_window_confirmed"] or not b["analysis_window_confirmed"]:
        raise AgreementPreflightError("agreement requires a confirmed analysis_window")


def _confusion(
    a: Sequence[str], b: Sequence[str], labels: Sequence[str]
) -> dict[str, dict[str, int]]:
    counts = Counter(zip(a, b))
    return {
        row: {column: counts[(row, column)] for column in labels}
        for row in labels
    }


def _cohen_kappa(
    a: Sequence[str], b: Sequence[str], labels: Sequence[str]
) -> float | None:
    if len(a) != len(b) or not a:
        raise ValueError("kappa requires equal, non-empty vectors")
    n = len(a)
    counts_a = Counter(a)
    counts_b = Counter(b)
    observed = sum(left == right for left, right in zip(a, b)) / n
    expected = sum(counts_a[label] * counts_b[label] for label in labels) / (n * n)
    if expected >= 1.0 - 1e-15:
        return None
    return (observed - expected) / (1.0 - expected)


def _linear_weighted_kappa(
    a: Sequence[str], b: Sequence[str], labels: Sequence[str]
) -> float | None:
    """Linearly weighted Cohen kappa for ordered level values."""

    if len(a) != len(b) or not a:
        raise ValueError("weighted kappa requires equal, non-empty vectors")
    n = len(a)
    k = len(labels)
    positions = {label: index for index, label in enumerate(labels)}
    counts_a = Counter(a)
    counts_b = Counter(b)

    def weight(left: str, right: str) -> float:
        if k <= 1:
            return 1.0
        return 1.0 - abs(positions[left] - positions[right]) / (k - 1)

    observed = sum(weight(left, right) for left, right in zip(a, b)) / n
    expected = sum(
        weight(left, right) * counts_a[left] * counts_b[right]
        for left in labels
        for right in labels
    ) / (n * n)
    if expected >= 1.0 - 1e-15:
        return None
    return (observed - expected) / (1.0 - expected)


def _occurrence_report(
    a: Sequence[str],
    b: Sequence[str],
    *,
    default: str,
    rare_threshold: float,
) -> dict[str, Any]:
    active_a = [value != default for value in a]
    active_b = [value != default for value in b]
    labels = (False, True)
    n = len(active_a)
    rate_a = sum(active_a) / n
    rate_b = sum(active_b) / n
    prevalence = sum(left or right for left, right in zip(active_a, active_b)) / n
    raw = sum(left == right for left, right in zip(active_a, active_b)) / n
    degenerate = len(set(active_a) | set(active_b)) < 2
    encoded_a = [str(value) for value in active_a]
    encoded_b = [str(value) for value in active_b]
    report: dict[str, Any] = {
        "kappa": _cohen_kappa(encoded_a, encoded_b, ("False", "True")),
        "raw_agreement": raw,
        "prevalence": prevalence,
        "rate_a": rate_a,
        "rate_b": rate_b,
        "rare": prevalence < rare_threshold,
        "degenerate": degenerate,
        "confusion": {
            str(row): {
                str(column): sum(
                    left is row and right is column
                    for left, right in zip(active_a, active_b)
                )
                for column in labels
            }
            for row in labels
        },
    }
    if report["rare"]:
        report["pabak"] = 2.0 * raw - 1.0
    return report


def _select(values: Sequence[str], mask: Sequence[bool]) -> list[str]:
    return [value for value, keep in zip(values, mask) if keep]


def _track_report(
    track_id: str,
    a: Sequence[str],
    b: Sequence[str],
    *,
    rare_threshold: float,
    common_kappa_threshold: float,
) -> dict[str, Any]:
    track_def = TRACK_DEFS[track_id]
    labels = track_def.values
    raw = sum(left == right for left, right in zip(a, b)) / len(a)
    support_a = Counter(a)
    support_b = Counter(b)
    degenerate = len(set(a) | set(b)) < 2
    report: dict[str, Any] = {
        "track": track_id,
        "label": track_def.label,
        "kind": track_def.kind,
        "frames": len(a),
        "raw_agreement": raw,
        "support_a": {label: support_a[label] for label in labels},
        "support_b": {label: support_b[label] for label in labels},
        "degenerate": degenerate,
        "confusion": _confusion(a, b, labels),
    }

    if track_def.kind == "level":
        occurrence = _occurrence_report(
            a, b, default=track_def.default, rare_threshold=rare_threshold
        )
        exact = _cohen_kappa(a, b, labels)
        weighted = _linear_weighted_kappa(a, b, labels)
        report.update(
            {
                "kappa": exact,
                "weighted_kappa": weighted,
                "occurrence": occurrence,
                "rare": occurrence["rare"],
            }
        )
        if degenerate or occurrence["degenerate"] or occurrence["rare"]:
            report["passes_common_threshold"] = None
        else:
            report["passes_common_threshold"] = (
                exact >= common_kappa_threshold
                and occurrence["kappa"] >= common_kappa_threshold
            )
    elif track_def.kind == "bool":
        occurrence = _occurrence_report(
            a, b, default=track_def.default, rare_threshold=rare_threshold
        )
        report.update(
            {
                "kappa": occurrence["kappa"],
                "prevalence": occurrence["prevalence"],
                "rate_a": occurrence["rate_a"],
                "rate_b": occurrence["rate_b"],
                "rare": occurrence["rare"],
            }
        )
        if occurrence["rare"]:
            report["pabak"] = occurrence["pabak"]
            report["passes_common_threshold"] = None
        elif degenerate or occurrence["degenerate"]:
            report["passes_common_threshold"] = None
        else:
            report["passes_common_threshold"] = (
                occurrence["kappa"] >= common_kappa_threshold
            )
    else:
        exact_kappa = _cohen_kappa(a, b, labels)
        report.update(
            {
                "kappa": exact_kappa,
                "passes_common_threshold": (
                    None if degenerate else exact_kappa >= common_kappa_threshold
                ),
            }
        )
    return report


def agreement_report(
    a: Mapping[str, Any],
    b: Mapping[str, Any],
    *,
    mouse: int | None = None,
    rare_threshold: float = 0.05,
    common_kappa_threshold: float = 0.80,
) -> dict[str, Any]:
    """Return per-mouse agreement with no cross-mouse mixing.

    Behaviour tracks are evaluated only where both raters marked visibility as
    ``clear``.  The visibility track itself uses the complete confirmed window.
    """

    agreement_preflight(a, b)
    if not 0.0 < rare_threshold < 1.0:
        raise ValueError("rare_threshold must be between 0 and 1")
    if not 0.0 <= common_kappa_threshold <= 1.0:
        raise ValueError("common_kappa_threshold must be between 0 and 1")
    mice = list(a["active_mice"])
    if mouse is not None:
        if mouse not in mice:
            raise AgreementPreflightError(f"mouse {mouse} is not in active_mice")
        mice = [mouse]

    output: dict[str, Any] = {
        "format": "depressionplex.annotation.agreement.v2",
        "trial": a["trial"],
        "assay": a["assay"],
        "video_sha256": a["video"]["sha256"],
        "analysis_window": list(a["analysis_window"]),
        "annotators": [a["annotator"], b["annotator"]],
        "rare_threshold": rare_threshold,
        "common_kappa_threshold": common_kappa_threshold,
        "mice": {},
    }
    total_frames = a["analysis_window"][1] - a["analysis_window"][0]
    for mouse_id in mice:
        vectors_a = state_vectors(a, mouse_id, validate=False)
        vectors_b = state_vectors(b, mouse_id, validate=False)
        clear_mask = [
            left == "clear" and right == "clear"
            for left, right in zip(vectors_a["visibility"], vectors_b["visibility"])
        ]
        jointly_clear = sum(clear_mask)
        if jointly_clear == 0:
            raise AgreementPreflightError(
                f"mouse {mouse_id} has no jointly clear frames in analysis_window"
            )
        track_reports = []
        for track_id in ASSAY_PROFILES[a["assay"]]:
            if track_id == "visibility":
                selected_a = vectors_a[track_id]
                selected_b = vectors_b[track_id]
                main_scope = "full_window"
                full_window_report = None
            else:
                selected_a = _select(vectors_a[track_id], clear_mask)
                selected_b = _select(vectors_b[track_id], clear_mask)
                main_scope = "jointly_clear"
                full_window_report = _track_report(
                    track_id,
                    vectors_a[track_id],
                    vectors_b[track_id],
                    rare_threshold=rare_threshold,
                    common_kappa_threshold=common_kappa_threshold,
                )
            main_report = _track_report(
                track_id,
                selected_a,
                selected_b,
                rare_threshold=rare_threshold,
                common_kappa_threshold=common_kappa_threshold,
            )
            main_report["scope"] = main_scope
            if full_window_report is not None:
                full_window_report["scope"] = "full_window"
                main_report["full_window"] = full_window_report
            track_reports.append(main_report)
        output["mice"][str(mouse_id)] = {
            "window_frames": total_frames,
            "jointly_clear_frames": jointly_clear,
            "jointly_clear_fraction": jointly_clear / total_frames,
            "visibility_a": {
                value: {
                    "frames": vectors_a["visibility"].count(value),
                    "fraction": vectors_a["visibility"].count(value) / total_frames,
                }
                for value in TRACK_DEFS["visibility"].values
            },
            "visibility_b": {
                value: {
                    "frames": vectors_b["visibility"].count(value),
                    "fraction": vectors_b["visibility"].count(value) / total_frames,
                }
                for value in TRACK_DEFS["visibility"].values
            },
            "tracks": track_reports,
        }
    return output


__all__ = [
    "AgreementPreflightError",
    "agreement_preflight",
    "agreement_report",
]
