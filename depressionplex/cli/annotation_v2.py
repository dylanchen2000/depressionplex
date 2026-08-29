"""Command-line entry point for the canonical independent-track V2 contract.

Examples::

    python3 -m depressionplex.cli.annotation_v2 validate annotation.json \
        --video source.mp4
    python3 -m depressionplex.cli.annotation_v2 agree rater-a.json rater-b.json
    python3 -m depressionplex.cli.annotation_v2 export-csv annotation.json out.csv
    python3 -m depressionplex.cli.annotation_v2 import-csv out.csv roundtrip.json
    python3 -m depressionplex.cli.annotation_v2 migrate-csv old.csv draft.json \
        --metadata metadata.json --provenance repair.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from ..annotations.agreement import agreement_report
from ..annotations.contract import validate_document
from ..annotations.csv_v2 import export_csv, import_csv, migrate_legacy_csv


def _load_json(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object in {source}")
    return value


def _write_json(path: str | Path, value: Any, *, force: bool = False) -> None:
    destination = Path(path)
    if destination.exists() and not force:
        raise ValueError(f"refusing to overwrite existing file without --force: {destination}")
    destination.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def cmd_validate(args: argparse.Namespace) -> int:
    doc = _load_json(args.file)
    validate_document(
        doc, video_path=args.video, require_completed=not args.allow_draft
    )
    result = {
        "valid": True,
        "format": doc["format"],
        "tool_version": doc["tool_version"],
        "primitive_set_version": doc["primitive_set_version"],
        "rubric_version": doc["rubric_version"],
        "trial": doc["trial"],
        "assay": doc["assay"],
        "active_mice": doc["active_mice"],
        "analysis_window": doc["analysis_window"],
        "completed": doc["completed"],
        "analysis_window_confirmed": doc["analysis_window_confirmed"],
        "formal_eligible": bool(
            args.video is not None
            and doc["pool"] == "validate"
            and doc["blind"] is True
            and doc["prefill"] is False
            and doc["annotator_role"] == "independent_rater"
            and doc["completed"]
            and doc["analysis_window_confirmed"]
        ),
        "video_verified": args.video is not None,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_agree(args: argparse.Namespace) -> int:
    a = _load_json(args.a)
    b = _load_json(args.b)
    report = agreement_report(
        a,
        b,
        mouse=args.mouse,
        rare_threshold=args.rare_threshold,
        common_kappa_threshold=args.kappa_threshold,
    )
    if args.out:
        _write_json(args.out, report, force=args.force)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_export_csv(args: argparse.Namespace) -> int:
    destination = Path(args.out)
    if destination.exists() and not args.force:
        raise ValueError(f"refusing to overwrite existing file without --force: {destination}")
    export_csv(_load_json(args.file), destination, mouse=args.mouse)
    print(f"exported canonical V2 CSV: {destination}")
    return 0


def cmd_import_csv(args: argparse.Namespace) -> int:
    doc = import_csv(args.file)
    _write_json(args.out, doc, force=args.force)
    print(f"imported canonical V2 CSV: {args.out}")
    return 0


def cmd_migrate_csv(args: argparse.Namespace) -> int:
    metadata = _load_json(args.metadata)
    provenance = _load_json(args.provenance)
    doc = migrate_legacy_csv(
        args.file, metadata=metadata, repair_provenance=provenance
    )
    _write_json(args.out, doc, force=args.force)
    print(
        "migrated legacy CSV as an incomplete V2 draft; "
        "analysis_window_confirmed=false and completed=false until human review"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="DepressionPlex canonical V2 annotation contract"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    command = sub.add_parser("validate", help="strictly validate canonical V2 JSON")
    command.add_argument("file")
    command.add_argument(
        "--video",
        help="optionally verify basename, byte size, and SHA-256 against the source video",
    )
    command.add_argument(
        "--allow-draft",
        action="store_true",
        help="validate draft structure without treating it as formal pilot truth",
    )
    command.set_defaults(function=cmd_validate)

    command = sub.add_parser("agree", help="strict blind per-mouse agreement")
    command.add_argument("a")
    command.add_argument("b")
    command.add_argument("--mouse", type=int)
    command.add_argument("--rare-threshold", type=float, default=0.05)
    command.add_argument("--kappa-threshold", type=float, default=0.80)
    command.add_argument("--out")
    command.add_argument("--force", action="store_true")
    command.set_defaults(function=cmd_agree)

    command = sub.add_parser("export-csv", help="export lossless enriched V2 CSV")
    command.add_argument("file")
    command.add_argument("out")
    command.add_argument(
        "--mouse", type=int, help="required when the JSON contains multiple active_mice"
    )
    command.add_argument("--force", action="store_true")
    command.set_defaults(function=cmd_export_csv)

    command = sub.add_parser("import-csv", help="import enriched V2 CSV only")
    command.add_argument("file")
    command.add_argument("out")
    command.add_argument("--force", action="store_true")
    command.set_defaults(function=cmd_import_csv)

    command = sub.add_parser(
        "migrate-csv", help="explicitly migrate the metadata-free legacy 7-column CSV"
    )
    command.add_argument("file")
    command.add_argument("out")
    command.add_argument("--metadata", required=True)
    command.add_argument("--provenance", required=True)
    command.add_argument("--force", action="store_true")
    command.set_defaults(function=cmd_migrate_csv)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.function(args)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
        return 2  # pragma: no cover - argparse.error raises


if __name__ == "__main__":
    sys.exit(main())
