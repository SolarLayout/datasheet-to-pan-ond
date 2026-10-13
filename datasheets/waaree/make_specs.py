"""Write one module spec per power bin from Waaree's datasheets.

Usage::

    uv run python datasheets/waaree/make_specs.py <text-dir> datasheets/waaree/specs

``<text-dir>`` holds ``pdftotext -layout`` output of the PDFs in ``sources.csv``, one
``<pdf stem>.txt`` each (see README.md). Only the datasheets listed in ``sources.csv`` are read.
A figure a datasheet does not give is written into the spec's ``provenance_notes``, which end up
in the PAN file's Remarks.
"""

from __future__ import annotations

import csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from datasheet_to_pan_ond.io import dump_spec

HERE = Path(__file__).resolve().parent
MANUFACTURER = "Waaree Energies Ltd."
NUM = r"-?\d+(?:\.\d+)?"
MODEL = r"(?:BiN|BiH|Bi|WSMDi|WSMD)-\d+(?:-\d+)?"

# Bifacial PERC datasheets that state no bifaciality factor; the series' other bifacial PERC
# datasheets state 70 %.
SISTER_BIFACIALITY = {"Bi-53": 0.70, "Bi-57": 0.70}


@dataclass(frozen=True)
class Bin:
    """One row of a datasheet's electrical table, at STC."""

    model: str
    p_nom: float
    vmp: float
    imp: float
    isc: float
    voc: float


@dataclass(frozen=True)
class Datasheet:
    document: str
    bins: list[Bin]
    low_light_reduction_pct: float
    v_max: float
    temp_coeff_isc_pct: float
    temp_coeff_voc_pct: float
    temp_coeff_pmax_pct: float
    length_m: float
    width_m: float
    depth_m: float
    weight_kg: float | None
    cells: int
    bifaciality: float | None
    bypass_diodes: int | None


def _one(pattern: str, text: str, what: str) -> str:
    m = re.search(pattern, text)
    if m is None:
        raise ValueError(f"{what} not found")
    return m.group(1)


def _maybe(pattern: str, text: str) -> str | None:
    m = re.search(pattern, text)
    return m.group(1) if m else None


def _bins(text: str) -> list[Bin]:
    """The electrical table: per model, STC and NOCT pairs of Pmax, Vmp, Imp, Isc, Voc, then
    the efficiency."""
    bins = []
    for line in text.splitlines():
        # Some PDFs space the digits of a number apart ("1 1 . 24"); join them.
        line = re.sub(r"(?<=\d) (?=[\d.])", "", line)
        line = re.sub(r"(?<=\.) (?=\d)", "", line)
        m = re.match(r"^\s*(" + MODEL + r")\s+((?:" + NUM + r"\s+){10}" + NUM + r")\s*$", line)
        if m:
            v = [float(x) for x in m.group(2).split()]
            bins.append(Bin(m.group(1), p_nom=v[0], vmp=v[2], imp=v[4], isc=v[6], voc=v[8]))
    if not bins:
        raise ValueError("no electrical table found")
    return bins


def _coefficient(label: str, text: str) -> float:
    pattern = r"\(" + label + r"\)[^\n]*?\(%/.C\)[ \t]*(" + NUM + ")"
    return float(_one(pattern, text, f"{label} coefficient"))


def parse(text: str) -> Datasheet:
    flat = re.sub(r"[ \t]+", " ", text)
    dims = re.search(r"(\d{3,4}) ?mm ?\(L\) ?x ?(\d{3,4}) ?mm ?\(W\) ?x ?(\d{2}) ?mm", flat)
    if dims is None:
        raise ValueError("module size not found")
    weight = _maybe(r"Weight (" + NUM + r") ?[Kk]g", flat)
    bifaciality = _maybe(r"Bifaciality(?: Factor)?(?: \(%\))? (\d+)", flat)
    diodes = _maybe(r"(\d+) ?(?:Nos\.? )?(?:Bypass )?Diodes", flat)
    return Datasheet(
        document=_one(r"(WEL/E&PD/\S+)", text, "document number"),
        bins=_bins(text),
        low_light_reduction_pct=float(
            _one(r"reduction of (" + NUM + r") ?% at 200", flat, "200 W/m2 figure")
        ),
        v_max=float(_one(r"System Voltage (\d{3,4}) ?V", flat, "system voltage")),
        temp_coeff_isc_pct=_coefficient("Isc", text),
        temp_coeff_voc_pct=_coefficient("Voc", text),
        temp_coeff_pmax_pct=_coefficient("Pm", text),
        length_m=int(dims.group(1)) / 1000,
        width_m=int(dims.group(2)) / 1000,
        depth_m=int(dims.group(3)) / 1000,
        weight_kg=float(weight) if weight else None,
        cells=int(_one(r"(\d+) cells ?/", flat, "cell count")),
        bifaciality=int(bifaciality) / 100 if bifaciality else None,
        bypass_diodes=int(diodes) if diodes else None,
    )


def specs(ds: Datasheet, technology: str) -> list[dict[str, Any]]:
    """One spec per power bin. Every cell is half-cut: N cells are two parallel strings of N/2."""
    family = ds.bins[0].model.rsplit("-", 1)[0]
    notes = []
    bifaciality = ds.bifaciality
    if bifaciality is None and family in SISTER_BIFACIALITY:
        bifaciality = SISTER_BIFACIALITY[family]
        notes.append(
            f"bifaciality {bifaciality:.2f}: the datasheet states none; "
            "the series' other bifacial PERC datasheets state 70 %"
        )
    if ds.bypass_diodes is None:
        notes.append("3 bypass diodes: the datasheet does not state the count")
    dimensions: dict[str, float] = {
        "width_m": ds.width_m,
        "height_m": ds.length_m,
        "depth_m": ds.depth_m,
    }
    if ds.weight_kg is not None:
        dimensions["weight_kg"] = ds.weight_kg
    year = int(_one(r"(20\d\d)", ds.document, "year"))
    out = []
    for b in ds.bins:
        spec: dict[str, Any] = {
            "manufacturer": MANUFACTURER,
            "model": b.model,
            "data_source": f"Waaree datasheet {ds.document}",
            "year": year,
            "provenance_notes": notes,
            "technology": "mtHIT" if technology == "HJT" else "mtSiMono",
            "cells_in_series": ds.cells // 2,
            "cells_in_parallel": 2,
            "bypass_diodes": ds.bypass_diodes or 3,
            "half_cell": True,
            "p_nom": b.p_nom,
            "isc": b.isc,
            "voc": b.voc,
            "imp": b.imp,
            "vmp": b.vmp,
            "temp_coeff_isc_pct": ds.temp_coeff_isc_pct,
            "temp_coeff_voc_pct": ds.temp_coeff_voc_pct,
            "temp_coeff_pmax_pct": ds.temp_coeff_pmax_pct,
            "low_light_rel_eff_pct": -ds.low_light_reduction_pct,
            "v_max_iec": ds.v_max,
            "dimensions": dimensions,
        }
        if bifaciality is not None:
            spec["bifaciality"] = bifaciality
        out.append(spec)
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    text_dir, spec_dir = Path(argv[0]), Path(argv[1])
    with (HERE / "sources.csv").open(encoding="utf-8", newline="") as f:
        sources = list(csv.DictReader(f))
    written = 0
    for src in sources:
        txt = text_dir / (Path(src["pdf"]).stem + ".txt")
        try:
            ds = parse(txt.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"{txt.name}: {e}", file=sys.stderr)
            return 1
        if ds.document != src["document"]:
            print(
                f"{txt.name}: document {ds.document}, sources.csv says {src['document']}",
                file=sys.stderr,
            )
            return 1
        for spec in specs(ds, src["technology"]):
            header = [f"Waaree {src['series']} series ({src['technology']}): {src['url']}"]
            dump_spec(spec, spec_dir / f"Waaree_{spec['model']}.yaml", header=header)
            written += 1
    print(f"Wrote {written} specs from {len(sources)} datasheets to {spec_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
