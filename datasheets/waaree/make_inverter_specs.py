"""Write one inverter spec per model from Waaree's on-grid inverter catalogue.

Usage::

    uv run python datasheets/waaree/make_inverter_specs.py datasheets/waaree/inverter-specs

The catalogue's tables span columns (one figure for a whole family, another per model), so they are
transcribed here as data, family by family, in the catalogue's own order and units, rather than
parsed from its text. Every figure below is printed in the catalogue (``sources.csv``, kind
``inverter``); a figure taken for want of one is written into the spec's ``provenance_notes``,
which end up in the OND file's Remarks.

Left out: the 350 kW model. Its MPPT minimum (480 V) leaves no string length under a 600 V system
voltage, and its 800 V AC output is for a plant with its own transformer, not a rooftop.

The catalogue names a model only by its rating ("3000", "75"), and two families share a rating
(3 kW, 75 kW), so a model is named by its phases, rating and family: ``3P-75K (75-136 kW)``.

Current: the catalogue gives the maximum input current per MPPT; ``i_max_dc`` is that times the
MPPTs, as PVsyst's ``IMaxDC`` is the whole inverter's.
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from datasheet_to_pan_ond.io import dump_spec

HERE = Path(__file__).resolve().parent
MANUFACTURER = "Waaree Energies Ltd."


@dataclass(frozen=True)
class Model:
    """The figures the catalogue gives per model."""

    rating: str  # as the catalogue names it: "3000" (W) or "75" (kW)
    p_nom_kw: float
    p_max_ac_kw: float  # the catalogue's maximum AC (apparent) power
    eff_max_pct: float
    eff_euro_pct: float
    n_mppt: int
    p_max_dc_kw: float | None = None
    i_nom_ac: float | None = None
    i_max_ac: float | None = None
    weight_kg: float | None = None
    note: str | None = None


@dataclass(frozen=True)
class Family:
    """The figures the catalogue gives once for a family, and its models."""

    title: str  # the catalogue's heading, e.g. "3kW to 25kW"
    label: str  # the family in a model's name, e.g. "3-25 kW"
    phases: Literal["single", "three"]
    v_ac: float  # line-to-line for three phases
    v_mpp_min: float
    v_mpp_max: float
    v_dc_max: float
    strings_per_mppt: int
    i_max_mppt: dict[str, float]  # per MPPT, by rating
    width_mm: float
    height_mm: float
    depth_mm: float
    night_loss_w: float | None
    models: list[Model]


def _same(value: float, ratings: list[str]) -> dict[str, float]:
    return dict.fromkeys(ratings, value)


R1 = ["700", "1000", "1500", "2000", "2500", "3000", "3300"]
SINGLE_1_TO_3 = Family(
    title="1kW to 3.3kW",
    label="1-3.3 kW",
    phases="single",
    v_ac=230,
    v_mpp_min=50,
    v_mpp_max=480,
    v_dc_max=500,
    strings_per_mppt=1,
    i_max_mppt=_same(20, R1),
    width_mm=290,
    height_mm=220,
    depth_mm=116,
    night_loss_w=1,
    models=[
        Model(r, p / 1000, pmax / 1000, emax, eeu, 1, pdc / 1000, inom, imax, 5.4)
        for r, p, pmax, pdc, inom, imax, eeu, emax in [
            ("700", 700, 800, 1050, 3.0, 3.5, 96.5, 97.2),
            ("1000", 1000, 1100, 1500, 4.3, 4.8, 96.5, 97.2),
            ("1500", 1500, 1650, 2250, 6.5, 7.2, 96.5, 97.3),
            ("2000", 2000, 2200, 3000, 8.7, 9.6, 96.8, 97.4),
            ("2500", 2500, 2750, 3750, 10.9, 12.0, 96.5, 97.4),
            ("3000", 3000, 3300, 4500, 13.0, 14.3, 96.5, 97.4),
            ("3300", 3300, 4000, 5400, 14.3, 17.4, 96.5, 97.4),
        ]
    ],
)

R2 = ["3000", "3600", "4600", "5000", "5300", "6000"]
SINGLE_3_TO_6 = Family(
    title="3kW to 6kW",
    label="3-6 kW",
    phases="single",
    v_ac=230,
    v_mpp_min=80,
    v_mpp_max=550,
    v_dc_max=600,
    strings_per_mppt=1,
    i_max_mppt=_same(14, R2),
    width_mm=402,
    height_mm=453,
    depth_mm=148,
    night_loss_w=1,
    models=[
        Model(r, p / 1000, pmax / 1000, 97.4, 96.8, 2, pdc / 1000, inom, imax, 14.8)
        for r, p, pmax, pdc, inom, imax in [
            ("3000", 3000, 3300, 4500, 13.0, 14.3),
            ("3600", 3600, 3960, 5400, 15.7, 17.2),
            ("4600", 4600, 5060, 6900, 20.0, 22.0),
            ("5000", 5000, 5500, 7500, 21.7, 23.9),
            ("5300", 5300, 5830, 7950, 23.0, 25.3),
            ("6000", 6000, 6000, 9000, 26.1, 26.1),
        ]
    ],
)

R3 = ["3", "4", "5", "6", "8", "10", "12", "15", "17", "20", "23", "25"]
THREE_3_TO_25 = Family(
    title="3kW to 25kW",
    label="3-25 kW",
    phases="three",
    v_ac=400,
    v_mpp_min=140,
    v_mpp_max=1000,
    v_dc_max=1100,
    strings_per_mppt=1,  # 1+1 up to 12 kW; 2+2 from 15 kW (set below)
    i_max_mppt={r: (14 if float(r) <= 12 else 28) for r in R3},
    width_mm=370,
    height_mm=480,
    depth_mm=183.5,
    night_loss_w=3,
    models=[
        Model(r, p, pmax, 98.6, 97.8, 2, pdc, inom, imax, 17 if p <= 12 else 20)
        for r, p, pmax, pdc, inom, imax in [
            ("3", 3, 3.3, 4.5, 4.3, 4.8),
            ("4", 4, 4.4, 6, 5.8, 6.4),
            ("5", 5, 5.5, 7.5, 7.2, 8.0),
            ("6", 6, 6.6, 9, 8.7, 9.6),
            ("8", 8, 8.8, 12, 11.6, 12.8),
            ("10", 10, 11, 15, 14.5, 15.9),
            ("12", 12, 13.2, 18, 17.4, 19.1),
            ("15", 15, 16.5, 22.5, 21.7, 23.9),
            ("17", 17, 18.7, 25.5, 24.6, 27.1),
            ("20", 20, 22, 30, 29.0, 31.9),
            ("23", 23, 25.3, 34.5, 33.3, 36.7),
            ("25", 25, 27.5, 37.5, 36.2, 39.9),
        ]
    ],
)

R4 = ["30", "33", "36", "40", "50", "60", "75"]
THREE_30_TO_75 = Family(
    title="30kW to 75kW",
    label="30-75 kW",
    phases="three",
    v_ac=400,
    v_mpp_min=200,
    v_mpp_max=1000,
    v_dc_max=1100,
    strings_per_mppt=2,
    i_max_mppt=_same(40, R4),
    width_mm=640,
    height_mm=430,
    depth_mm=243,
    night_loss_w=None,
    models=[
        Model(r, p, pmax, emax, eeu, n, None, None, imax, kg)
        for r, p, pmax, n, imax, eeu, emax, kg in [
            ("30", 30, 33, 3, 50.2, 98.4, 98.7, 41),
            ("33", 33, 36.3, 3, 55.2, 98.4, 98.7, 41),
            ("36", 36, 39.6, 4, 60.2, 98.4, 98.7, 42),
            ("40", 40, 44, 4, 66.9, 98.4, 98.7, 42),
            ("50", 50, 55, 4, 83.6, 98.1, 98.5, 50),
            ("60", 60, 66, 6, 100.3, 98.1, 98.5, 50),
            ("75", 75, 75, 6, 114, 98.1, 98.5, 50),
        ]
    ],
)

R5 = ["75", "80", "100", "110", "125", "136"]
THREE_75_TO_136 = Family(
    title="75kW to 136kW",
    label="75-136 kW",
    phases="three",
    v_ac=400,
    v_mpp_min=200,
    v_mpp_max=1000,
    v_dc_max=1100,
    strings_per_mppt=2,
    i_max_mppt=_same(40, R5),
    width_mm=975,
    height_mm=630,
    depth_mm=360,
    night_loss_w=3.5,
    models=[
        Model(r, p, pmax, 98.6, 98.2, n, None, None, imax, 90, note)
        for r, p, pmax, n, imax, note in [
            (
                "75",
                75,
                78,
                9,
                113.7,
                "maximum AC power 78 kVA, the lower of the catalogue's '78*/82.5' "
                "(its asterisk is not explained)",
            ),
            ("80", 80, 88, 9, 133.3, None),
            ("100", 100, 110, 9, 166.7, None),
            ("110", 110, 121, 10, 183.3, None),
            ("125", 125, 137.5, 10, 199.3, None),
            ("136", 136, 138, 10, 200, None),
        ]
    ],
)

FAMILIES = [
    SINGLE_1_TO_3,
    SINGLE_3_TO_6,
    THREE_3_TO_25,
    THREE_30_TO_75,
    THREE_75_TO_136,
]


def model_name(family: Family, model: Model) -> str:
    kw = model.p_nom_kw
    rating = f"{kw:g}K"
    return f"{'1P' if family.phases == 'single' else '3P'}-{rating} ({family.label})"


def file_stem(family: Family, model: Model) -> str:
    phases = "1P" if family.phases == "single" else "3P"
    return f"Waaree_{phases}-{model.p_nom_kw:g}K_{family.label.replace(' ', '')}"


def spec(family: Family, model: Model, document: str, year: int) -> dict[str, Any]:
    strings = family.strings_per_mppt
    if family is THREE_3_TO_25 and model.p_nom_kw >= 15:
        strings = 2
    notes = []
    if model.note:
        notes.append(model.note)
    if family.night_loss_w is None:
        notes.append("night loss 1 W: the catalogue does not state it")
    out: dict[str, Any] = {
        "manufacturer": MANUFACTURER,
        "model": model_name(family, model),
        "data_source": f"Waaree catalogue {document}",
        "year": year,
        "provenance_notes": notes,
        "p_nom_ac_kw": model.p_nom_kw,
        "p_max_ac_kw": model.p_max_ac_kw,
        "v_ac": family.v_ac,
        "phases": family.phases,
        "power_factor_min": 0.8,
        "transformer": False,
        "v_mpp_min": family.v_mpp_min,
        "v_mpp_max": family.v_mpp_max,
        "v_dc_max": family.v_dc_max,
        "i_max_dc": round(family.i_max_mppt[model.rating] * model.n_mppt, 1),
        "n_mppt": model.n_mppt,
        "n_inputs": model.n_mppt * strings,
        "eff_max_pct": model.eff_max_pct,
        "eff_euro_pct": model.eff_euro_pct,
        "night_loss_w": family.night_loss_w if family.night_loss_w is not None else 1.0,
        "dimensions": {
            "width_m": family.width_mm / 1000,
            "height_m": family.height_mm / 1000,
            "depth_m": family.depth_mm / 1000,
            **({"weight_kg": model.weight_kg} if model.weight_kg is not None else {}),
        },
    }
    if model.p_max_dc_kw is not None:
        out["p_max_dc_kw"] = model.p_max_dc_kw
    if model.i_nom_ac is not None:
        out["i_nom_ac"] = model.i_nom_ac
    if model.i_max_ac is not None:
        out["i_max_ac"] = model.i_max_ac
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    spec_dir = Path(argv[0])
    with (HERE / "sources.csv").open(encoding="utf-8", newline="") as f:
        rows = [row for row in csv.DictReader(f) if row["kind"] == "inverter"]
    if len(rows) != 1:
        print(f"sources.csv: expected one inverter catalogue, found {len(rows)}", file=sys.stderr)
        return 1
    src = rows[0]
    year = 2025  # "Inverter Vr 01 / Oct 2025", printed at the foot of the catalogue's last page
    written = 0
    for family in FAMILIES:
        for model in family.models:
            header = [f"Waaree on-grid inverter, {family.title} family: {src['url']}"]
            path = spec_dir / f"{file_stem(family, model)}.yaml"
            dump_spec(spec(family, model, src["document"], year), path, header=header)
            written += 1
    print(f"Wrote {written} inverter specs to {spec_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
