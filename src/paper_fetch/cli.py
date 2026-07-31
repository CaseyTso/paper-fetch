"""Single-entry-point CLI for paper-fetch."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

from .config import Config, load_config
from .doctor import OVERALL_ERROR, format_human, run_doctor
from .models import FetchResult, Status
from .pipeline import Pipeline
from .sources.ablesci import AbleSciSource
from .sources.institution import InstitutionSource
from .sources.open_access import OpenAccessSource
from .sources.scihub import SciHubSource
from .zotero import ZoteroClient, ZoteroConfigError


def _build_pipeline(args: argparse.Namespace) -> Pipeline:
    overrides: dict = {}
    if args.output:
        overrides["output_dir"] = str(Path(args.output).resolve())
    config = load_config(overrides=overrides)
    session = requests.Session()

    sources = [
        OpenAccessSource(session, config),
        InstitutionSource(session, config),
        SciHubSource(session, config),
        AbleSciSource(session, config),
    ]

    zotero = None
    if not args.no_zotero:
        try:
            zotero = ZoteroClient(session, config)
        except ZoteroConfigError:
            pass  # Zotero is optional

    return Pipeline(config=config, sources=sources, zotero=zotero)


def _format_human(result: FetchResult) -> str:
    lines = []
    if result.success:
        lines.append(f"✅ PDF acquired from {result.source}")
        if result.pdf_path:
            lines.append(f"   Path: {result.pdf_path}")
        if result.zotero_item_key:
            lines.append(f"   Zotero: {result.zotero_item_key}")
    else:
        lines.append(f"❌ Failed: {result.error or 'unknown'}")
        identity = result.identity
        if identity and identity.doi:
            lines.append(f"   DOI: {identity.doi}")
    if result.attempts:
        lines.append("   Attempts:")
        for a in result.attempts:
            lines.append(f"     {a.source}: {a.status.value} ({a.elapsed_ms}ms)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="paper-fetch", description="Single-paper PDF acquisition")
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="Download a paper")
    fetch.add_argument("identifier", help="DOI, PMID, PMCID, title, citation, or Zotero key")
    fetch.add_argument("--output", help="Override output directory")
    fetch.add_argument("--no-zotero", action="store_true", help="Skip Zotero attachment")
    fetch.add_argument("--force", action="store_true", help="Re-acquire even if cached")
    fetch.add_argument("--json", action="store_true", dest="json_output", help="Output structured JSON")

    doctor = sub.add_parser("doctor", help="Check configuration and environment (read-only)")
    doctor.add_argument("--json", action="store_true", dest="json_output", help="Output structured JSON")
    doctor.add_argument("--config", default=None, help="Path to the config file (default ~/.paper-fetch/config.json)")

    args = parser.parse_args(argv)

    if args.command == "doctor":
        report = run_doctor(args.config)
        if args.json_output:
            json.dump(report, sys.stdout, indent=2, default=str)
        else:
            print(format_human(report))
        return 5 if report["overall"] == OVERALL_ERROR else 0

    try:
        pipeline = _build_pipeline(args)
    except Exception as exc:
        result = FetchResult(success=False, error=f"startup failed: {exc}")
        if args.json_output:
            json.dump(result.to_dict(), sys.stdout, indent=2, default=str)
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 5

    result = pipeline.fetch(
        args.identifier,
        force=args.force,
        no_zotero=args.no_zotero,
    )

    if args.json_output:
        json.dump(result.to_dict(), sys.stdout, indent=2, default=str)
    else:
        print(_format_human(result))

    # Exit codes
    if result.success:
        return 0

    # Check if resolution failed
    for a in result.attempts:
        if a.status == Status.AMBIGUOUS_IDENTIFIER:
            return 2
        if a.status == Status.CONFIGURATION_ERROR:
            return 5

    if "zotero_write_failed" in (result.error or ""):
        return 4

    return 3


if __name__ == "__main__":
    sys.exit(main())
