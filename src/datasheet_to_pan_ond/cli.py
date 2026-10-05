"""Command-line interface: ``ds2panond``."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pvlib.iotools import read_panond
from pydantic import ValidationError

from datasheet_to_pan_ond._version import __version__
from datasheet_to_pan_ond.inverter_fit import InverterFitError, fit_inverter
from datasheet_to_pan_ond.io import load_inverter, load_module
from datasheet_to_pan_ond.module_fit import FitError, fit_module
from datasheet_to_pan_ond.report import (
    inverter_report_dict,
    inverter_report_text,
    module_report_dict,
    module_report_text,
)
from datasheet_to_pan_ond.writers import FileFormat, write_ond, write_pan


def _default_out(spec: Path, suffix: str) -> Path:
    return spec.with_suffix(suffix)


def _fmt(args: argparse.Namespace) -> FileFormat:
    return FileFormat(encoding=args.encoding, newline="\n" if args.lf else "\r\n")


def _cmd_module(args: argparse.Namespace) -> int:
    result = fit_module(load_module(args.spec))
    print(module_report_text(result))
    if args.report_json:
        Path(args.report_json).write_text(json.dumps(module_report_dict(result), indent=2))
    if args.strict and result.warnings:
        print("\nRefusing to write output: warnings present (--strict).", file=sys.stderr)
        return 2
    out = write_pan(result, args.output or _default_out(args.spec, ".PAN"), _fmt(args))
    print(f"\nWrote {out}")
    return 0


def _cmd_inverter(args: argparse.Namespace) -> int:
    result = fit_inverter(load_inverter(args.spec))
    print(inverter_report_text(result))
    if args.report_json:
        Path(args.report_json).write_text(json.dumps(inverter_report_dict(result), indent=2))
    if args.strict and result.warnings:
        print("\nRefusing to write output: warnings present (--strict).", file=sys.stderr)
        return 2
    out = write_ond(result, args.output or _default_out(args.spec, ".OND"), _fmt(args))
    print(f"\nWrote {out}")
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    data = read_panond(args.file, encoding="utf-8-sig")
    print(json.dumps(data, indent=2, default=str))
    return 0


def _cec_rows(args: argparse.Namespace) -> tuple[Any, str, str]:
    from datasheet_to_pan_ond.sources import cec

    kind: cec.Kind = args.kind
    src = args.source if args.source else cec.fetch(kind, refresh=args.refresh)
    df = cec.read_sam_csv(src)
    library = cec.library_version(df)
    if not getattr(args, "all", False) and not args.terms:
        raise ValueError("give search terms, or --all to select every record")
    rows = cec.search(df, args.terms, regex=args.regex) if args.terms else df
    if args.limit:
        rows = rows.head(args.limit)
    return rows, library, str(src)


def _cmd_cec_search(args: argparse.Namespace) -> int:
    rows, library, src = _cec_rows(args)
    print(f"{len(rows)} match(es) in {src}" + (f" (SAM library {library})" if library else ""))
    if args.kind == "modules":
        cols = ["Name", "Technology", "STC", "N_s", "V_oc_ref", "I_sc_ref"]
    else:
        cols = ["Name", "Paco", "Vac", "Mppt_low", "Mppt_high", "Vdcmax"]
    if len(rows):
        print(rows[[c for c in cols if c in rows.columns]].to_string(index=False))
    return 0


def _cmd_cec_export(args: argparse.Namespace) -> int:
    from datasheet_to_pan_ond.sources.export import REPORT_NAME, export_records

    rows, library, src = _cec_rows(args)
    if len(rows) == 0:
        print("No matching records.", file=sys.stderr)
        return 1
    report = export_records(
        args.kind,
        rows,
        args.output,
        library=library,
        source=src,
        build=args.build,
        strict=args.strict,
        jobs=args.jobs,
    )
    counts: dict[str, int] = {}
    for r in report:
        counts[r.status] = counts.get(r.status, 0) + 1
    review = sum(r.needs_review for r in report)
    print(f"{len(report)} record(s) -> {args.output}")
    for status, n in sorted(counts.items()):
        print(f"  {status:<14}{n}")
    if review:
        print(f"  needs review  {review}  (inferred values marked VERIFY)")
    print(f"Details: {Path(args.output) / REPORT_NAME}")
    failed = counts.get("build_failed", 0) + counts.get("build_refused", 0)
    return 0 if counts.get("skipped", 0) + failed < len(report) else 1


def _cmd_library_build(args: argparse.Namespace) -> int:
    from datasheet_to_pan_ond.library.build import build
    from datasheet_to_pan_ond.sources.cec import Kind

    kinds: list[Kind] = ["inverters", "modules"] if args.kind == "all" else [args.kind]
    incomplete = False
    for kind in kinds:
        res = build(
            kind,
            args.root,
            source=args.source if len(kinds) == 1 else None,
            refresh=args.refresh,
            jobs=args.jobs,
            date=args.date,
            time_budget=args.time_budget,
        )
        stages: dict[str, int] = {}
        for x in res.excluded:
            stages[x.stage] = stages.get(x.stage, 0) + 1
        print(
            f"{kind}: {len(res.built):,} built, {len(res.excluded):,} excluded "
            f"({', '.join(f'{k} {v:,}' for k, v in sorted(stages.items())) or 'none'}); "
            f"processed this run: {res.processed_now:,}; pending: {res.pending:,}"
        )
        if not res.complete:
            incomplete = True
            print(f"  incomplete - run the same command again to resume ({args.root})")
    if incomplete:
        return 3
    print(f"Indexes written. Next: ds2panond library verify {args.root}")
    return 0


def _cmd_library_verify(args: argparse.Namespace) -> int:
    from datasheet_to_pan_ond.library.verify import verify_library

    rep = verify_library(args.root, jobs=args.jobs)
    for kind, n in rep.checked.items():
        print(f"{kind}: {n:,} files verified")
    if rep.ok:
        print("PASSED")
        return 0
    print(f"FAILED: {len(rep.problems)} problem(s)")
    for path, msg in rep.problems[:50]:
        print(f"  {path}: {msg}")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ds2panond",
        description="Generate PVsyst PAN/OND files from datasheet values.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser, suffix: str) -> None:
        p.add_argument("spec", type=Path, help="Datasheet spec (.yaml/.yml/.json)")
        p.add_argument("-o", "--output", type=Path, help=f"Output file (default: spec{suffix})")
        p.add_argument("--report-json", type=Path, help="Also write the fit report as JSON")
        p.add_argument("--strict", action="store_true", help="Fail instead of writing on warnings")
        p.add_argument(
            "--encoding", default="utf-8-sig", help="Output encoding (default UTF-8+BOM)"
        )
        p.add_argument("--lf", action="store_true", help="Use LF line endings instead of CRLF")

    pm = sub.add_parser("module", help="Fit a module datasheet and write a .PAN file")
    add_common(pm, ".PAN")
    pm.set_defaults(func=_cmd_module)

    pi = sub.add_parser("inverter", help="Fit an inverter datasheet and write a .OND file")
    add_common(pi, ".OND")
    pi.set_defaults(func=_cmd_inverter)

    pc = sub.add_parser("cec", help="Import from the CEC module/inverter lists (NREL SAM library)")
    csub = pc.add_subparsers(dest="cec_command", required=True)

    def add_cec_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("kind", choices=["modules", "inverters"])
        p.add_argument("terms", nargs="*", help="Words that must all appear in the record name")
        p.add_argument("--regex", action="store_true", help="Treat terms as regular expressions")
        p.add_argument("--limit", type=int, default=0, help="Use at most N matching records")
        p.add_argument("--source", type=Path, help="Local SAM CSV instead of the cached download")
        p.add_argument("--refresh", action="store_true", help="Re-download the SAM CSV")

    cs = csub.add_parser("search", help="List matching records")
    add_cec_common(cs)
    cs.set_defaults(func=_cmd_cec_search)

    ce = csub.add_parser("export", help="Write YAML specs (and optionally PAN/OND files)")
    add_cec_common(ce)
    ce.add_argument("-o", "--output", type=Path, required=True, help="Output directory")
    ce.add_argument("--all", action="store_true", help="Export every record (no search terms)")
    ce.add_argument("--build", action="store_true", help="Also fit and write PAN/OND files")
    ce.add_argument("--strict", action="store_true", help="With --build: skip files with warnings")
    ce.add_argument("--jobs", type=int, default=1, help="Parallel build workers (0 = all CPUs)")
    ce.set_defaults(func=_cmd_cec_export)

    pl = sub.add_parser("library", help="Build / verify a curated PAN/OND library")
    lsub = pl.add_subparsers(dest="library_command", required=True)
    lb = lsub.add_parser("build", help="Build (or resume) the library from the CEC lists")
    lb.add_argument("root", type=Path, help="Library folder, e.g. library")
    lb.add_argument("--kind", choices=["inverters", "modules", "all"], default="all")
    lb.add_argument("--source", type=Path, help="Local SAM CSV (only with a single --kind)")
    lb.add_argument("--refresh", action="store_true", help="Re-download the SAM CSVs")
    lb.add_argument("--jobs", type=int, default=0, help="Parallel workers (0 = all CPUs)")
    lb.add_argument("--date", help="Build date YYYY-MM-DD written into files (default: kept)")
    lb.add_argument("--time-budget", type=float, help="Stop after N seconds; rerun to resume")
    lb.set_defaults(func=_cmd_library_build)
    lv = lsub.add_parser("verify", help="Verify every file, checksum, index and README link")
    lv.add_argument("root", type=Path)
    lv.add_argument("--jobs", type=int, default=0)
    lv.set_defaults(func=_cmd_library_verify)

    px = sub.add_parser("inspect", help="Parse a PAN/OND file (via pvlib) and print it as JSON")
    px.add_argument("file", type=Path)
    px.set_defaults(func=_cmd_inspect)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except ValidationError as exc:
        print(f"Invalid datasheet spec:\n{exc}", file=sys.stderr)
    except (FitError, InverterFitError, ValueError, OSError) as exc:  # incl. URLError
        print(f"Error: {exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
