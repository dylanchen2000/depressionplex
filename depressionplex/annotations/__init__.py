"""Canonical V2 annotation contract and analysis helpers.

The browser tool is the annotation UI; this package is the authoritative
machine-readable contract.  Schema-1 support intentionally remains isolated in
``depressionplex.cli.annotate`` as a legacy regression surface.
"""

from .contract import (
    ANALYSIS_WINDOW_SEMANTICS,
    FORMAT,
    INTERVAL_SEMANTICS,
    PRIMITIVE_SET_VERSION,
    RUBRIC_VERSION,
    SCHEMA,
    TOOL_VERSION,
    ASSAY_PROFILES,
    TRACK_DEFS,
    AnnotationValidationError,
    TrackDef,
    atomic_segments,
    state_vectors,
    validate_document,
    validation_errors,
)
from .rubrics import (
    RUBRICS,
    RubricDecision,
    RubricDef,
    classify_state,
    derive_frame_results,
    derive_rubric_report,
    rubric_names,
    summarize_frame_results,
)
from .recovery import LegacyRecoveryError, recover_legacy_csv, recover_legacy_json

__all__ = [
    "ANALYSIS_WINDOW_SEMANTICS",
    "FORMAT",
    "INTERVAL_SEMANTICS",
    "PRIMITIVE_SET_VERSION",
    "RUBRIC_VERSION",
    "SCHEMA",
    "TOOL_VERSION",
    "ASSAY_PROFILES",
    "TRACK_DEFS",
    "AnnotationValidationError",
    "TrackDef",
    "atomic_segments",
    "state_vectors",
    "validate_document",
    "validation_errors",
    "RUBRICS",
    "RubricDecision",
    "RubricDef",
    "classify_state",
    "derive_frame_results",
    "derive_rubric_report",
    "rubric_names",
    "summarize_frame_results",
    "LegacyRecoveryError",
    "recover_legacy_csv",
    "recover_legacy_json",
]
