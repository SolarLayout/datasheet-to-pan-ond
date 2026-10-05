"""Import modules and inverters from the CEC equipment lists (SAM library format).

The California Energy Commission (CEC) publishes lists of certified PV modules
and grid-support inverters. NREL's System Advisor Model (SAM) redistributes
them as CSV files with a consistent schema, updated with each SAM release:

* ``CEC Modules.csv``: STC values, temperature coefficients, cell count,
  dimensions, plus SAM's own CEC 6-parameter fit.
* ``CEC Inverters.csv``: Sandia inverter model parameters (Paco, Pdco, Pso,
  C0..C3, ...) fitted to CEC test measurements at three DC voltages.

This module maps one CSV row to a :class:`ModuleDatasheet` /
:class:`InverterDatasheet` mapping. Values that the lists don't hold, or
that look wrong, are filled in or inferred, and each such step is recorded in
``provenance_notes`` so it ends up in the generated file's Remarks.

Known data-quality issues handled here:

* Modules: ``N_s`` is sometimes the total half-cell count (e.g. 144) or plainly
  wrong (e.g. 22 for a 132 half-cell module). It is checked against Voc per
  cell and re-inferred when implausible.
* Inverters: names look like ``"Maker: Model {240V}"``; phase count is not
  listed and is inferred from the AC voltage.
"""

from __future__ import annotations

import math
import os
import re
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import ValidationError

from datasheet_to_pan_ond.inverter_fit import CEC_WEIGHTS, EURO_WEIGHTS, PROFILE_FRACTIONS
from datasheet_to_pan_ond.models import InverterDatasheet, ModuleDatasheet, Technology

Kind = Literal["modules", "inverters"]

SAM_LIBRARY_BASE = "https://raw.githubusercontent.com/NREL/SAM/develop/deploy/libraries/"
SAM_URLS: dict[Kind, str] = {
    "modules": SAM_LIBRARY_BASE + "CEC%20Modules.csv",
    "inverters": SAM_LIBRARY_BASE + "CEC%20Inverters.csv",
}
CACHE_FILES: dict[Kind, str] = {
    "modules": "CEC Modules.csv",
    "inverters": "CEC Inverters.csv",
}


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


def cache_dir() -> Path:
    """Where downloaded SAM CSVs are cached (``$DS2PANOND_CACHE`` overrides)."""
    env = os.environ.get("DS2PANOND_CACHE")
    if env:
        return Path(env)
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "datasheet-to-pan-ond"


def fetch(kind: Kind, refresh: bool = False) -> Path:
    """Download the SAM CSV for ``kind`` into the cache (if needed) and return its path."""
    path = cache_dir() / CACHE_FILES[kind]
    if refresh or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        with urllib.request.urlopen(SAM_URLS[kind], timeout=120) as resp:
            tmp.write_bytes(resp.read())
        tmp.replace(path)
    return path


def read_sam_csv(path: str | Path) -> pd.DataFrame:
    """Read a SAM library CSV (row 0 names, row 1 units, row 2 SAM variable ids)."""
    df = pd.read_csv(path, skiprows=[1, 2], low_memory=False)
    df = df.loc[:, ~df.columns.str.startswith("Unnamed")]
    if "Name" not in df.columns:
        raise ValueError(f"{path}: not a SAM library CSV (no 'Name' column)")
    out: pd.DataFrame = df.dropna(subset=["Name"]).reset_index(drop=True)
    return out


def load(kind: Kind, source: str | Path | None = None, refresh: bool = False) -> pd.DataFrame:
    """Load the CEC list for ``kind`` from ``source`` (path) or the cached download."""
    return read_sam_csv(source if source is not None else fetch(kind, refresh))


def search(df: pd.DataFrame, terms: Iterable[str], regex: bool = False) -> pd.DataFrame:
    """Rows whose Name contains every term (case-insensitive), or matches ``regex``."""
    names = df["Name"].astype(str)
    mask = pd.Series(True, index=df.index)
    for term in terms:
        mask &= names.str.contains(term, case=False, regex=regex)
    out: pd.DataFrame = df.loc[mask]
    return out


# --------------------------------------------------------------------------- #
# Conversion result
# --------------------------------------------------------------------------- #


@dataclass
class Conversion:
    """Outcome of converting one database row."""

    name: str
    spec: dict[str, Any] | None
    notes: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.spec is not None and self.error is None


def _finite(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _validated(
    name: str,
    spec: dict[str, Any],
    notes: list[str],
    model: type[ModuleDatasheet] | type[InverterDatasheet],
) -> Conversion:
    spec["provenance_notes"] = notes
    try:
        model.model_validate(spec)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(x) for x in first["loc"]) or "spec"
        return Conversion(name, None, notes, f"invalid {loc}: {first['msg']}")
    return Conversion(name, spec, notes)


# --------------------------------------------------------------------------- #
# Modules
# --------------------------------------------------------------------------- #

TECHNOLOGY_MAP: dict[str, Technology] = {
    "mono-c-si": Technology.SI_MONO,
    "multi-c-si": Technology.SI_POLY,
    "cdte": Technology.CDTE,
    "cigs": Technology.CIS,
    "a-si/nc": Technology.A_SI_H,
    "hit-si": Technology.HIT,
}
CRYSTALLINE = {Technology.SI_MONO, Technology.SI_POLY, Technology.HIT}

#: Plausible open-circuit voltage per crystalline-silicon cell at STC [V].
VOC_PER_CELL_RANGE = (0.55, 0.80)
#: Voc per cell (mean, std) [V] and CEC diode factor n = a_ref/(Ns*Vth) (mean, std),
#: measured on the ~18,700 CEC c-Si records whose listed N_s is plausible.
VOC_PER_CELL_STATS: dict[Technology, tuple[float, float]] = {
    Technology.SI_MONO: (0.684, 0.030),
    Technology.SI_POLY: (0.630, 0.018),
    Technology.HIT: (0.735, 0.020),
}
DIODE_FACTOR_STATS = (1.00, 0.045)
VTH_STC = 1.380649e-23 * 298.15 / 1.602176634e-19
STANDARD_CELL_COUNTS = (32, 36, 48, 54, 60, 66, 72, 78, 96)
_NAME_CELLS = re.compile(r"(?<!\d)(54|60|66|72|78|96|108|120|132|144|156)(?!\d)")


def _plausible(voc: float, ns: float) -> bool:
    lo, hi = VOC_PER_CELL_RANGE
    return ns > 0 and lo <= voc / ns <= hi


def estimate_cells_in_series(voc: float, a_ref: float | None, tech: Technology) -> int:
    """Most likely series cell count from Voc and (if known) SAM's CEC ``a_ref``.

    Scores each standard count by how typical the implied Voc per cell and diode
    factor are. On CEC records with a trustworthy N_s this picks the listed
    count about 92 % of the time (Voc alone: ~53 %).
    """
    v_mu, v_sd = VOC_PER_CELL_STATS.get(tech, VOC_PER_CELL_STATS[Technology.SI_MONO])
    n_mu, n_sd = DIODE_FACTOR_STATS

    def score(c: int) -> float:
        s = ((voc / c - v_mu) / v_sd) ** 2
        if a_ref is not None and a_ref > 0:
            s += ((a_ref / (c * VTH_STC) - n_mu) / n_sd) ** 2
        return s

    return min(STANDARD_CELL_COUNTS, key=score)


def infer_cell_layout(
    voc: float,
    n_s_listed: float | None,
    model: str,
    a_ref: float | None = None,
    tech: Technology = Technology.SI_MONO,
) -> tuple[int, int, bool, list[str]]:
    """Return ``(cells_in_series, cells_in_parallel, half_cell, notes)`` for c-Si modules.

    Order of evidence: listed N_s if plausible; listed N_s as a total half-cell
    count; a cell count in the model name (99 % agreement on valid records);
    statistical estimate from Voc and ``a_ref``.
    """
    if n_s_listed is not None and _plausible(voc, n_s_listed):
        return (
            int(n_s_listed),
            1,
            False,
            ["NCelP=1 assumed (CEC data does not say whether cells are half-cut)"],
        )
    if n_s_listed is not None and n_s_listed % 2 == 0 and _plausible(voc, n_s_listed / 2):
        return (
            int(n_s_listed // 2),
            2,
            True,
            [
                f"CEC N_s={int(n_s_listed)} read as total half-cells: "
                f"NCelS={int(n_s_listed // 2)}, NCelP=2"
            ],
        )
    listed = "missing" if n_s_listed is None else f"{n_s_listed:g}"
    for match in _NAME_CELLS.finditer(model):
        count = int(match.group(1))
        ns, npar = (count, 1) if count <= 96 else (count // 2, 2)
        if _plausible(voc, ns):
            return (
                ns,
                npar,
                npar == 2,
                [
                    f"CEC N_s ({listed}) implausible for Voc={voc:g} V; cell count {count} taken "
                    f"from model name: NCelS={ns}, NCelP={npar}"
                ],
            )
    ns = estimate_cells_in_series(voc, a_ref, tech)
    return (
        ns,
        1,
        False,
        [
            f"CEC N_s ({listed}) implausible for Voc={voc:g} V; NCelS={ns} estimated from Voc and "
            "CEC diode parameter (about 92% reliable). VERIFY against the datasheet"
        ],
    )


def module_row_to_spec(row: pd.Series | dict[str, Any], library: str = "") -> Conversion:
    """Convert one row of ``CEC Modules.csv`` to a :class:`ModuleDatasheet` mapping."""
    r = dict(row)
    name = str(r.get("Name", "")).strip()
    manufacturer = str(r.get("Manufacturer") or "").strip() or name.split(" ")[0]
    model = name[len(manufacturer) :].strip() if name.startswith(manufacturer) else name
    model = model or name
    notes: list[str] = []

    tech_raw = str(r.get("Technology") or "").strip()
    tech = TECHNOLOGY_MAP.get(tech_raw.lower())
    if tech is None:
        hint = " (ambiguous: CdTe, CIS or a-Si)" if tech_raw.lower() == "thin film" else ""
        return Conversion(name, None, notes, f"unsupported technology {tech_raw!r}{hint}")

    vals = {
        k: _finite(r.get(k))
        for k in (
            "STC",
            "I_sc_ref",
            "V_oc_ref",
            "I_mp_ref",
            "V_mp_ref",
            "alpha_sc",
            "beta_oc",
            "gamma_pmp",
        )
    }
    missing = [k for k, v in vals.items() if v is None]
    if missing:
        return Conversion(name, None, notes, f"missing values: {', '.join(missing)}")
    p_nom, isc, voc, imp, vmp = (
        vals[k] for k in ("STC", "I_sc_ref", "V_oc_ref", "I_mp_ref", "V_mp_ref")
    )
    assert p_nom and isc and voc and imp and vmp  # for type checkers; all non-None here
    n_s = _finite(r.get("N_s"))

    if tech in CRYSTALLINE:
        ns, npar, half, cell_notes = infer_cell_layout(
            voc, n_s, model, _finite(r.get("a_ref")), tech
        )
        notes += cell_notes
    else:
        if n_s is None or n_s <= 0:
            return Conversion(name, None, notes, "missing cell count N_s (thin film)")
        ns, npar, half = int(n_s), 1, False

    spec: dict[str, Any] = {
        "manufacturer": manufacturer,
        "model": model,
        "data_source": f"CEC module list via NREL SAM library {library}".strip(),
        "technology": tech.value,
        "cells_in_series": ns,
        "cells_in_parallel": npar,
        "half_cell": half,
        "p_nom": p_nom,
        "isc": isc,
        "voc": voc,
        "imp": imp,
        "vmp": vmp,
        "temp_coeff_isc_pct": round(vals["alpha_sc"] / isc * 100, 5),  # type: ignore[operator]
        "temp_coeff_voc_pct": round(vals["beta_oc"] / voc * 100, 5),  # type: ignore[operator]
        "temp_coeff_pmax_pct": vals["gamma_pmp"],
    }
    if str(r.get("Bifacial")).strip() in {"1", "1.0", "True", "Y"}:
        notes.append("Bifacial module: bifaciality factor not in CEC data, not written")
    dims = {}
    for key, col in (("height_m", "Length"), ("width_m", "Width")):
        v = _finite(r.get(col))
        if v is not None and v > 0:
            dims[key] = v
    if dims:
        spec["dimensions"] = dims
    notes.append("Low-light (200 W/m2) efficiency not in CEC data: technology default used")
    return _validated(name, spec, notes, ModuleDatasheet)


# --------------------------------------------------------------------------- #
# Inverters
# --------------------------------------------------------------------------- #

_INV_NAME = re.compile(r"^(?P<mfr>[^:]+):\s*(?P<model>.*?)\s*(?:\{(?P<v>[^}]*)\})?\s*$")
SINGLE_PHASE_VAC = {100, 110, 115, 120, 127, 200, 220, 230, 240, 277}


@dataclass(frozen=True)
class SandiaParams:
    """Sandia inverter model parameters (King et al., 2007), as in SAM/pvlib."""

    Paco: float
    Pdco: float
    Vdco: float
    Pso: float
    C0: float
    C1: float
    C2: float
    C3: float

    def efficiency(self, v_dc: float, p_ac_frac: np.ndarray | float) -> np.ndarray:
        """Efficiency at DC voltage ``v_dc`` and AC output ``p_ac_frac`` x Paco.

        Inverts the Sandia equation
        ``Pac = (Paco/(A-B) - C*(A-B)) * (Pdc-B) + C * (Pdc-B)**2`` for Pdc.
        """
        dv = v_dc - self.Vdco
        a = self.Pdco * (1 + self.C1 * dv)
        b = self.Pso * (1 + self.C2 * dv)
        c = self.C0 * (1 + self.C3 * dv)
        p_ac = np.asarray(p_ac_frac, dtype=float) * self.Paco
        k = self.Paco / (a - b) - c * (a - b)
        # Linear case when C0 == 0; otherwise the positive root of the quadratic.
        x = p_ac / k if abs(c) < 1e-15 else (-k + np.sqrt(k * k + 4 * c * p_ac)) / (2 * c)
        eff: np.ndarray = p_ac / (x + b)
        return eff

    def threshold_w(self, v_dc: float) -> float:
        """DC power at which AC output starts (Sandia ``Pso`` at ``v_dc``) [W]."""
        return self.Pso * (1 + self.C2 * (v_dc - self.Vdco))

    def curve(self, v_dc: float) -> list[dict[str, float]]:
        """Efficiency points at the PVsyst profile fractions, as spec mappings."""
        fr = np.array(PROFILE_FRACTIONS)
        eff = self.efficiency(v_dc, fr)
        return [
            {"p_out_frac": float(f), "eff_pct": round(100 * float(e), 4)}
            for f, e in zip(fr, eff, strict=True)
        ]

    def figures(self, v_dc: float) -> tuple[float, float, float]:
        """(max, Euro, CEC) efficiency [%] at ``v_dc``."""
        grid = np.linspace(0.02, 1.0, 491)
        eff_max = float(np.max(self.efficiency(v_dc, grid)))

        def weighted(w: dict[float, float]) -> float:
            fr = np.array(list(w))
            return float(np.dot(list(w.values()), self.efficiency(v_dc, fr)))

        return 100 * eff_max, 100 * weighted(EURO_WEIGHTS), 100 * weighted(CEC_WEIGHTS)


def _threshold(sandia: SandiaParams, v_dc: float, notes: list[str]) -> float:
    """Start-up power at ``v_dc``; falls back when the Sandia extrapolation is <= 0."""
    thr = sandia.threshold_w(v_dc)
    if thr > 0:
        return round(thr, 3)
    fallback = sandia.Pso if sandia.Pso > 0 else 1e-3 * sandia.Paco
    notes.append(f"Sandia start-up power <= 0 at {v_dc:g} V; {fallback:.1f} W used")
    return round(fallback, 3)


def parse_inverter_name(name: str) -> tuple[str, str, str | None]:
    """``"Maker: Model {240V}"`` -> ``("Maker", "Model", "240V")``."""
    m = _INV_NAME.match(name.strip())
    if not m:
        return name.split(" ")[0], name, None
    return m.group("mfr").strip(), m.group("model").strip(), m.group("v")


def inverter_row_to_spec(row: pd.Series | dict[str, Any], library: str = "") -> Conversion:
    """Convert one row of ``CEC Inverters.csv`` to an :class:`InverterDatasheet` mapping."""
    r = dict(row)
    name = str(r.get("Name", "")).strip()
    manufacturer, model, v_tag = parse_inverter_name(name)
    if v_tag:
        model = f"{model} {v_tag}"
    notes: list[str] = []

    cols = (
        "Vac",
        "Paco",
        "Pdco",
        "Vdco",
        "Pso",
        "C0",
        "C1",
        "C2",
        "C3",
        "Vdcmax",
        "Idcmax",
        "Mppt_low",
        "Mppt_high",
    )
    v = {k: _finite(r.get(k)) for k in cols}
    pnt = _finite(r.get("Pnt"))
    if pnt is None:
        notes.append("Night consumption (Pnt) missing: 0 W assumed")
    missing = [k for k, x in v.items() if x is None]
    if missing:
        return Conversion(name, None, notes, f"missing values: {', '.join(missing)}")
    known: dict[str, float] = {k: x for k, x in v.items() if x is not None}
    f: Callable[[str], float] = known.__getitem__
    if not (0 < f("Mppt_low") < f("Mppt_high") <= f("Vdcmax")):
        return Conversion(
            name,
            None,
            notes,
            f"inconsistent DC voltages (Mppt_low={f('Mppt_low'):g}, Mppt_high={f('Mppt_high'):g}, "
            f"Vdcmax={f('Vdcmax'):g})",
        )
    if f("Paco") <= 0 or f("Pdco") <= f("Paco"):
        return Conversion(name, None, notes, "invalid Paco/Pdco")

    sandia = SandiaParams(*(f(k) for k in ("Paco", "Pdco", "Vdco", "Pso", "C0", "C1", "C2", "C3")))
    eff_max, eff_euro, eff_cec = sandia.figures(f("Vdco"))
    notes.append(
        f"Efficiency curve evaluated from Sandia model parameters (CEC test data) at "
        f"Vdco={f('Vdco'):g} V; start-up power = Sandia Pso"
    )

    vac = f("Vac")
    phases = "single" if round(vac) in SINGLE_PHASE_VAC else "three"
    notes.append(f"{phases.capitalize()}-phase assumed from AC voltage {vac:g} V (not in CEC data)")
    notes.append("Number of MPPTs not in CEC data: 1 assumed")
    notes.append(
        "Idcmax taken from the Sandia parameter set (may differ from datasheet max current)"
    )

    spec: dict[str, Any] = {
        "manufacturer": manufacturer,
        "model": model,
        "data_source": f"CEC inverter list via NREL SAM library {library}".strip(),
        # 1 W resolution, which is what the OND format can represent (kW, 3 decimals).
        "p_nom_ac_kw": round(f("Paco") / 1e3, 3),
        "v_ac": vac,
        "phases": phases,
        "v_mpp_min": f("Mppt_low"),
        "v_mpp_max": f("Mppt_high"),
        "v_dc_max": f("Vdcmax"),
        "i_max_dc": f("Idcmax"),
        "n_mppt": 1,
        "eff_max_pct": round(eff_max, 4),
        "eff_euro_pct": round(eff_euro, 4),
        "eff_cec_pct": round(eff_cec, 4),
        "night_loss_w": max(pnt or 0.0, 0.0),
        "efficiency_curve": sandia.curve(f("Vdco")),
        "p_threshold_w": _threshold(sandia, f("Vdco"), notes),
    }

    volts = (f("Mppt_low"), f("Vdco"), f("Mppt_high"))
    if volts[0] < volts[1] < volts[2]:
        by_v = []
        for vdc in volts:
            m, e, c = sandia.figures(vdc)
            by_v.append(
                {
                    "v_dc": vdc,
                    "eff_max_pct": round(m, 4),
                    "eff_euro_pct": round(e, 4),
                    "eff_cec_pct": round(c, 4),
                    "curve": sandia.curve(vdc),
                    "p_threshold_w": _threshold(sandia, vdc, notes),
                }
            )
        spec["efficiency_by_voltage"] = by_v
        notes.append("Voltage-dependent curves at Mppt_low / Vdco / Mppt_high from Sandia model")
    if str(r.get("CEC_hybrid")).strip().upper() == "Y":
        notes.append("Listed by CEC as a hybrid (battery-capable) inverter")

    conv = _validated(name, spec, notes, InverterDatasheet)
    if not conv.ok and "efficiency_by_voltage" in spec:
        # Sandia extrapolation to the MPPT limits can be non-physical; retry without it.
        spec.pop("efficiency_by_voltage")
        notes = [n for n in notes if not n.startswith("Voltage-dependent")]
        notes.append("Voltage-dependent curves dropped (Sandia extrapolation invalid)")
        conv = _validated(name, spec, notes, InverterDatasheet)
    return conv


def library_version(df: pd.DataFrame) -> str:
    """SAM library version string for modules (``Version`` column), else ''."""
    if "Version" in df.columns and len(df):
        vals = df["Version"].dropna().astype(str)
        if len(vals):
            return str(vals.mode().iloc[0])
    return ""


def convert(kind: Kind, row: pd.Series, library: str = "") -> Conversion:
    if kind == "modules":
        return module_row_to_spec(row, library)
    return inverter_row_to_spec(row, library)
