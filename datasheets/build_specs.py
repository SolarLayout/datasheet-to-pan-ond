"""Write module and inverter specs from transcribed datasheets, and check the transcription.

Usage::

    uv run python datasheets/build_specs.py                      # write every maker's specs
    uv run python datasheets/build_specs.py --check <pdf-root>   # also check against the PDFs

A maker's ``sheets.yaml`` (``datasheets/<maker>/sheets.yaml``) holds its datasheets' figures as
printed: one entry per datasheet, the power bins of a module sheet and the models of an inverter
sheet as lists in the sheet's own order. This script turns them into specs
(``<maker>/specs/*.yaml``, ``<maker>/inverter-specs/*.yaml``) and the maker's ``sources.csv``,
which are output: change ``sheets.yaml`` and run this again, never edit them.

``--check`` reads each PDF (paths relative to ``<pdf-root>``) and refuses a transcription that
the PDF does not bear out:

* the file's SHA-256 must be the one in ``sheets.yaml``, so a new revision is a deliberate change;
* every list (a bin table's column, or a figure per model) must appear in order on one line of
  the PDF's text (``pdftotext -layout``), or each bin's figures together on one line, for sheets
  laid out a bin to a row;
* every single figure must appear in the text;
* a module sheet's bins must rise in power and current, and Vmp x Imp must be within
  ``MPP_TOLERANCE`` of the rated power;
* a figure the text does not hold (an image, or text fused with a drawing) is refused unless the
  sheet names it in ``read_by_eye`` (``{"bins.vmp": why, "size_mm": why}``), or names the whole
  sheet in ``unchecked`` (why), after a reader and a second, independent reader agree on it from
  the page. Such a figure is still checked for sense (bins rising, Vmp x Imp, the spec's rules).

The PDFs are the makers' documents and are not committed.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from datasheet_to_pan_ond.io import dump_spec
from datasheet_to_pan_ond.models import InverterDatasheet, ModuleDatasheet

HERE = Path(__file__).resolve().parent
MPP_TOLERANCE = 0.006  # Vmp x Imp against the rated power: the sheets' rounding stays well inside
BIN_COLUMNS = ("p_nom", "vmp", "imp", "voc", "isc")
TECHNOLOGY = {"PERC": "mtSiMono", "TOPCon": "mtSiMono", "BC": "mtSiMono", "HJT": "mtHIT"}

# Inverter figures that may be one value for every model on the sheet, or a list, one a model.
INVERTER_FIGURES = (
    "p_nom_ac_kw",
    "p_max_ac_kw",
    "v_ac",
    "i_nom_ac",
    "i_max_ac",
    "p_max_dc_kw",
    "v_mpp_min",
    "v_mpp_max",
    "v_mpp_nom",
    "v_dc_max",
    "i_max_dc",
    "i_max_mppt",
    "n_mppt",
    "n_inputs",
    "eff_max_pct",
    "eff_euro_pct",
    "eff_cec_pct",
    "weight_kg",
)


# --------------------------------------------------------------------------- #
# Reading a maker's sheets.yaml
# --------------------------------------------------------------------------- #


@dataclass
class Maker:
    folder: Path
    manufacturer: str
    prefix: str  # file-name prefix of the specs, e.g. "Adani"
    modules: list[dict[str, Any]] = field(default_factory=list)
    inverters: list[dict[str, Any]] = field(default_factory=list)


def load_makers(root: Path = HERE) -> list[Maker]:
    makers = []
    for path in sorted(root.glob("*/sheets.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        makers.append(
            Maker(
                folder=path.parent,
                manufacturer=raw["manufacturer"],
                prefix=raw["prefix"],
                modules=raw.get("modules") or [],
                inverters=raw.get("inverters") or [],
            )
        )
    return makers


def _document(sheet: dict[str, Any]) -> str:
    """The sheet's document number as printed, else its file's SHA-256 (first 12 digits)."""
    doc = sheet.get("document")
    return str(doc) if doc else f"sha256 {sheet['sha256'][:12]}"


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._+-]+", "-", name).strip("-")


# --------------------------------------------------------------------------- #
# Specs
# --------------------------------------------------------------------------- #


def module_specs(maker: Maker, sheet: dict[str, Any]) -> list[dict[str, Any]]:
    bins = sheet["bins"]
    n = len(bins["p_nom"])
    for col in BIN_COLUMNS:
        if len(bins[col]) != n:
            raise ValueError(f"{sheet['pdf']}: bins.{col} has {len(bins[col])} values, not {n}")
    cells = int(sheet["cells"])
    half = bool(sheet["half_cell"])
    parallel = int(sheet.get("cells_in_parallel", 2 if half else 1))  # 4 for quarter-cut cells
    if cells % parallel:
        raise ValueError(f"{sheet['pdf']}: {cells} cells do not split into {parallel} strings")
    notes = list(sheet.get("notes") or [])
    if sheet.get("v_max_iec") is None:
        notes.append(
            "Maximum system voltage: the datasheet does not state one, and the file gives none."
        )
    if "bypass_diodes" not in sheet:
        notes.append("Bypass diodes: 3, the usual count; the datasheet does not state it.")
    length_mm, width_mm, depth_mm = sheet["size_mm"]
    specs = []
    for i in range(n):
        p = bins["p_nom"][i]
        spec: dict[str, Any] = {
            "manufacturer": maker.manufacturer,
            "model": sheet["model"].format(p=_num(p)),
            "data_source": f"{maker.manufacturer} datasheet {_document(sheet)}",
        }
        if sheet.get("year"):
            spec["year"] = int(sheet["year"])
        spec |= {
            "provenance_notes": notes,
            "technology": TECHNOLOGY[sheet["technology"]],
            "cells_in_series": cells // parallel,
            "cells_in_parallel": parallel,
            "bypass_diodes": int(sheet.get("bypass_diodes", 3)),
            "half_cell": half,
            "p_nom": float(p),
            "isc": float(bins["isc"][i]),
            "voc": float(bins["voc"][i]),
            "imp": float(bins["imp"][i]),
            "vmp": float(bins["vmp"][i]),
            "temp_coeff_isc_pct": float(sheet["temp_coeff_isc_pct"]),
            "temp_coeff_voc_pct": float(sheet["temp_coeff_voc_pct"]),
            "temp_coeff_pmax_pct": float(sheet["temp_coeff_pmax_pct"]),
        }
        if sheet.get("low_light_loss_pct") is not None:
            spec["low_light_rel_eff_pct"] = -float(sheet["low_light_loss_pct"])
        if sheet.get("v_max_iec") is not None:
            spec["v_max_iec"] = float(sheet["v_max_iec"])
        spec["dimensions"] = {
            "width_m": width_mm / 1000,
            "height_m": length_mm / 1000,
            "depth_m": depth_mm / 1000,
            "weight_kg": float(sheet["weight_kg"]),
        }
        if sheet.get("bifaciality_pct") is not None:
            spec["bifaciality"] = round(float(sheet["bifaciality_pct"]) / 100, 3)
        ModuleDatasheet.model_validate(spec)
        specs.append(spec)
    return specs


def _per_model(sheet: dict[str, Any], key: str, i: int) -> Any:
    value = sheet.get(key)
    if isinstance(value, list):
        if len(value) != len(sheet["models"]):
            raise ValueError(
                f"{sheet['pdf']}: {key} has {len(value)} values for {len(sheet['models'])} models"
            )
        return value[i]
    return value


def inverter_specs(maker: Maker, sheet: dict[str, Any]) -> list[dict[str, Any]]:
    specs = []
    for i, model in enumerate(sheet["models"]):
        fig = {k: _per_model(sheet, k, i) for k in INVERTER_FIGURES}
        notes = list(sheet.get("notes") or [])
        if fig["i_max_dc"] is None:
            fig["i_max_dc"] = fig["i_max_mppt"] * fig["n_mppt"]
            notes.append(
                f"Maximum DC input current: {_num(fig['i_max_mppt'])} A per MPPT x "
                f"{fig['n_mppt']} MPPTs = {_num(fig['i_max_dc'])} A; the datasheet gives it "
                "per MPPT."
            )
        spec: dict[str, Any] = {
            "manufacturer": maker.manufacturer,
            "model": model,
            "data_source": f"{maker.manufacturer} datasheet {_document(sheet)}",
        }
        if sheet.get("year"):
            spec["year"] = int(sheet["year"])
        spec["provenance_notes"] = notes
        spec["p_nom_ac_kw"] = float(fig["p_nom_ac_kw"])
        for key in ("p_max_ac_kw",):
            if fig[key] is not None:
                spec[key] = float(fig[key])
        spec["v_ac"] = float(fig["v_ac"])
        spec["phases"] = "three"
        for key in ("i_nom_ac", "i_max_ac", "p_max_dc_kw"):
            if fig[key] is not None:
                spec[key] = float(fig[key])
        spec |= {"v_mpp_min": float(fig["v_mpp_min"]), "v_mpp_max": float(fig["v_mpp_max"])}
        if fig["v_mpp_nom"] is not None:
            spec["v_mpp_nom"] = float(fig["v_mpp_nom"])
        spec |= {
            "v_dc_max": float(fig["v_dc_max"]),
            "i_max_dc": float(fig["i_max_dc"]),
            "n_mppt": int(fig["n_mppt"]),
        }
        if fig["n_inputs"] is not None:
            spec["n_inputs"] = int(fig["n_inputs"])
        spec["eff_max_pct"] = float(fig["eff_max_pct"])
        for key in ("eff_euro_pct", "eff_cec_pct"):
            if fig[key] is not None:
                spec[key] = float(fig[key])
        if sheet.get("size_mm") or fig["weight_kg"] is not None:
            dims: dict[str, float] = {}
            if sheet.get("size_mm"):
                w, h, d = sheet["size_mm"]
                dims = {"width_m": w / 1000, "height_m": h / 1000, "depth_m": d / 1000}
            if fig["weight_kg"] is not None:
                dims["weight_kg"] = float(fig["weight_kg"])
            spec["dimensions"] = dims
        InverterDatasheet.model_validate(spec)
        specs.append(spec)
    return specs


def _num(x: float) -> str:
    return f"{x:g}"


def write_maker(maker: Maker) -> tuple[int, int]:
    """Write the maker's specs and sources.csv; returns (modules, inverters) written."""
    rows = []
    counts = [0, 0]
    for kind, sheets, sub, build in (
        ("module", maker.modules, "specs", module_specs),
        ("inverter", maker.inverters, "inverter-specs", inverter_specs),
    ):
        out = maker.folder / sub
        if sheets:
            out.mkdir(exist_ok=True)
            for old in out.glob("*.yaml"):
                old.unlink()
        for sheet in sheets:
            rows.append(
                {
                    "kind": kind,
                    "pdf": sheet["pdf"],
                    "series": sheet.get("series", ""),
                    "technology": sheet.get("technology", ""),
                    "document": _document(sheet),
                    "url": sheet["url"],
                }
            )
            for spec in build(maker, sheet):
                header = [f"{maker.manufacturer} {sheet.get('series', '')}: {sheet['url']}"]
                dump_spec(spec, out / f"{maker.prefix}_{_safe(spec['model'])}.yaml", header=header)
                counts[kind == "inverter"] += 1
    with (maker.folder / "sources.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["kind", "pdf", "series", "technology", "document", "url"])
        w.writeheader()
        w.writerows(rows)
    return counts[0], counts[1]


# --------------------------------------------------------------------------- #
# Checking a transcription against its PDF
# --------------------------------------------------------------------------- #

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def _values(token: str) -> set[float]:
    """The numbers a printed token may mean: "1,500" is 1500 or 1.5; "52.10" is 52.1."""
    out = set()
    for candidate in (token.replace(",", ""), token.replace(",", ".")):
        with contextlib.suppress(ValueError):
            out.add(round(float(candidate), 6))
    return out


def _line_numbers(line: str) -> list[set[float]]:
    return [_values(t) for t in _NUMBER.findall(line)]


def _split_rows(text_lines: list[str], reach: int = 2) -> list[list[set[float]]]:
    """Rows the text layer split over neighbouring lines, put together again left to right.

    ``pdftotext -layout`` keeps each figure's column, so a table row printed with its figures
    at two heights comes out as two lines whose figures interleave by column.
    """
    placed = [
        [(m.start(), _values(m.group())) for m in _NUMBER.finditer(line)] for line in text_lines
    ]
    out = []
    for i, first in enumerate(placed):
        for j in range(i + 1, min(i + 1 + reach, len(placed))):
            if first and placed[j]:
                out.append([v for _, v in sorted(first + placed[j], key=lambda t: t[0])])
    return out


def _in_order(wanted: list[float], found: list[set[float]]) -> bool:
    it = iter(found)
    return all(any(round(abs(w), 6) in vals for vals in it) for w in wanted)


def _contains(wanted: list[float], found: list[set[float]]) -> bool:
    pool = list(found)
    for w in wanted:
        for k, vals in enumerate(pool):
            if round(abs(w), 6) in vals:
                del pool[k]
                break
        else:
            return False
    return True


def pdf_text(pdf: Path) -> str:
    done = subprocess.run(
        ["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, check=True
    )
    return done.stdout


def check_sheet(sheet: dict[str, Any], kind: str, root: Path) -> list[str]:
    """What the PDF does not bear out, as sentences; empty when the transcription checks."""
    pdf = root / sheet["pdf"]
    if not pdf.is_file():
        return [f"no file {pdf}"]
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    if sha != sheet["sha256"]:
        return [f"SHA-256 is {sha}, not {sheet['sha256']}: a new revision?"]
    problems = _check_sense(sheet, kind)
    text = pdf_text(pdf)
    if sheet.get("unchecked"):
        return problems
    if len(_NUMBER.findall(text)) < 20:
        return [
            *problems,
            "the PDF has no text to check against (an image?); say why in "
            "'unchecked' after reading it by eye",
        ]
    by_eye = set(sheet.get("read_by_eye") or {})
    lines = [_line_numbers(line) for line in text.splitlines()]
    lines += _split_rows(text.splitlines())
    everything = [v for line in lines for v in line]

    def seq_ok(values: list[float]) -> bool:
        if len(set(values)) == 1:
            return _contains(values[:1], everything)
        return any(_in_order(values, line) for line in lines)

    if kind == "module":
        bins = sheet["bins"]
        n = len(bins["p_nom"])
        row_ok = [
            any(_contains([bins[c][i] for c in BIN_COLUMNS], ln) for ln in lines) for i in range(n)
        ]
        for col in BIN_COLUMNS:
            if f"bins.{col}" in by_eye or seq_ok(bins[col]):
                continue
            bad = [i for i in range(n) if not row_ok[i]]
            if bad:
                problems.append(
                    f"bins.{col} {bins[col]} is not a line of the PDF, and bins "
                    f"{[bins['p_nom'][i] for i in bad]} are not rows of it"
                )
        scalars = {
            k: sheet.get(k)
            for k in (
                "temp_coeff_isc_pct",
                "temp_coeff_voc_pct",
                "temp_coeff_pmax_pct",
                "low_light_loss_pct",
                "v_max_iec",
                "bifaciality_pct",
                "weight_kg",
                "cells",
            )
        }
        if "size_mm" not in by_eye:
            for i, v in enumerate(sheet["size_mm"]):
                scalars[f"size_mm[{i}]"] = v
    else:
        scalars = {}
        for key in INVERTER_FIGURES:
            value = sheet.get(key)
            if key in by_eye:
                continue
            if isinstance(value, list):
                if not seq_ok([float(v) for v in value]):
                    problems.append(f"{key} {value} is not a line of the PDF")
            elif value is not None:
                scalars[key] = value
        if "size_mm" not in by_eye:
            for i, v in enumerate(sheet.get("size_mm") or []):
                scalars[f"size_mm[{i}]"] = v
    for key, value in scalars.items():
        if key not in by_eye and value is not None and not _contains([float(value)], everything):
            problems.append(f"{key} {value} is not in the PDF")
    return problems


def _check_sense(sheet: dict[str, Any], kind: str) -> list[str]:
    maker = Maker(folder=HERE, manufacturer="check", prefix="check")
    try:
        (module_specs if kind == "module" else inverter_specs)(maker, sheet)
    except (ValueError, KeyError, TypeError) as exc:  # pydantic's ValidationError is a ValueError
        lines = str(exc).splitlines()
        reason = (lines[1] if len(lines) > 1 else lines[0]).strip().split(" [type=")[0]
        return [f"not a valid spec: {reason}"]
    if kind != "module":
        return []
    out = []
    b = sheet["bins"]
    # Power rises strictly; a current may repeat where the sheet rounds two bins alike.
    for col, step_ok in (
        ("p_nom", lambda x, y: x < y),
        ("imp", lambda x, y: x <= y),
        ("isc", lambda x, y: x <= y),
    ):
        if not all(step_ok(x, y) for x, y in zip(b[col], b[col][1:], strict=False)):
            out.append(f"bins.{col} does not rise bin to bin: {b[col]}")
    for p, v, i in zip(b["p_nom"], b["vmp"], b["imp"], strict=True):
        if abs(v * i - p) / p > MPP_TOLERANCE:
            out.append(f"bin {p} W: Vmp x Imp = {v * i:.1f} W")
    return out


# --------------------------------------------------------------------------- #


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--check",
        type=Path,
        metavar="PDF_ROOT",
        help="check every transcription against the PDFs under PDF_ROOT",
    )
    ap.add_argument("makers", nargs="*", help="maker folders to build (default: all)")
    args = ap.parse_args(argv)
    makers = [m for m in load_makers() if not args.makers or m.folder.name in args.makers]
    failed = 0
    if args.check:
        for maker in makers:
            for kind, sheets in (("module", maker.modules), ("inverter", maker.inverters)):
                for sheet in sheets:
                    problems = check_sheet(sheet, kind, args.check)
                    flag = "ok  " if not problems else "FAIL"
                    eye = (
                        [f"all: {sheet['unchecked']}"]
                        if sheet.get("unchecked")
                        else [f"{k}: {v}" for k, v in (sheet.get("read_by_eye") or {}).items()]
                    )
                    note = f"  (read by eye: {'; '.join(eye)})" if eye and not problems else ""
                    print(f"{flag} {maker.folder.name}: {sheet['pdf']}{note}")
                    for p in problems:
                        print(f"       {p}")
                    failed += bool(problems)
        if failed:
            print(f"{failed} sheet(s) failed the check; nothing written")
            return 1
    for maker in makers:
        m, i = write_maker(maker)
        print(f"{maker.folder.name}: {m} module specs, {i} inverter specs")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
