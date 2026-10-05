"""Independent verification of generated PAN / OND files.

The checks deliberately do not reuse the fitting code. Each file is re-read
from disk with ``pvlib.iotools.read_panond``, its PVsyst parameters are put
back into pvlib's PVsyst model, and the result is compared with the YAML spec
the file was generated from. A file passes only if:

* the bytes are well formed (UTF-8 BOM, CRLF only, no NaN/inf, correct footer);
* all keys PVsyst needs are present and numeric where they should be;
* values are physically sane (ordering of currents/voltages, ranges);
* the model rebuilt from the file reproduces the datasheet values
  (PAN: Isc, Voc, MPP on the curve, Pmax temperature coefficient;
  OND: monotonic profile, efficiency figures, nominal DC power);
* key values match the YAML spec.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

import numpy as np
from pvlib import pvsystem
from pvlib.iotools import read_panond
from pvlib.ivtools.sdm import pvsyst_temperature_coeff

from datasheet_to_pan_ond.inverter_fit import EURO_WEIGHTS
from datasheet_to_pan_ond.models import InverterDatasheet, ModuleDatasheet, Technology
from datasheet_to_pan_ond.module_fit import BANDGAP_EV
from datasheet_to_pan_ond.writers.pvsyst_format import sanitize

_BAD_NUMBER = re.compile(rb"=\s*(?:-?nan|-?inf|None)\b", re.IGNORECASE)

PAN_REQUIRED = (
    "Technol",
    "NCelS",
    "NCelP",
    "NDiode",
    "GRef",
    "TRef",
    "PNom",
    "Isc",
    "Voc",
    "Imp",
    "Vmp",
    "muISC",
    "muVocSpec",
    "muPmpReq",
    "RShunt",
    "Rp_0",
    "Rp_Exp",
    "RSerie",
    "Gamma",
    "muGamma",
)
OND_REQUIRED_TOP = ("Transfo", "NbInputs", "NbMPPT", "TanPhiMin", "TanPhiMax", "Night_Loss")
OND_REQUIRED_CONV = (
    "PNomConv",
    "PMaxOUT",
    "VOutConv",
    "VMppMin",
    "VMPPMax",
    "VAbsMax",
    "PSeuil",
    "EfficMax",
    "EfficEuro",
    "MonoTri",
    "PNomDC",
    "IMaxDC",
    "INomAC",
    "IMaxAC",
    "ProfilPIO",
)


def _text(v: Any) -> str:
    """Text value as written (pvlib splits values containing commas into lists)."""
    if isinstance(v, list):
        return ",".join(str(x) for x in v)
    return "" if v is None else str(v)


class _Errors(list[str]):
    def check(self, cond: bool, msg: str) -> bool:
        if not cond:
            self.append(msg)
        return cond


def _num(d: dict[str, Any], key: str, errs: _Errors) -> float:
    v = d.get(key)
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
        errs.append(f"{key} missing or not a finite number ({v!r})")
        return math.nan
    return float(v)


def check_bytes(path: Path, root: str) -> list[str]:
    errs = _Errors()
    raw = path.read_bytes()
    errs.check(raw.startswith(b"\xef\xbb\xbf"), "missing UTF-8 BOM")
    try:
        raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        errs.append(f"not valid UTF-8: {exc}")
    errs.check(b"\n" not in raw.replace(b"\r\n", b""), "bare LF line ending")
    errs.check(raw.endswith(f"End of PVObject {root}\r\n".encode()), "missing/incorrect footer")
    errs.check(not _BAD_NUMBER.search(raw), "NaN/inf/None value written")
    errs.check(b"\r\n\r\n\r\n" not in raw, "repeated blank lines")
    return errs


# --------------------------------------------------------------------------- #
# PAN
# --------------------------------------------------------------------------- #

_TECH_BY_CODE = {t.value: t for t in Technology}


def verify_pan(path: str | Path, spec: ModuleDatasheet | None = None) -> list[str]:
    """Return a list of problems (empty when the PAN file is sound)."""
    path = Path(path)
    byte_errs = check_bytes(path, "pvModule")
    return byte_errs + _verify_pan_values(path, spec)


def _verify_pan_values(path: Path, spec: ModuleDatasheet | None) -> list[str]:
    errs = _Errors()
    try:
        top = read_panond(path, encoding="utf-8-sig")
    except Exception as exc:  # parser failure is itself the finding
        return [f"unparseable: {exc}"]
    d = top.get("PVObject_", {})
    if not errs.check(d.get("PVObject_") == "pvModule", "root object is not pvModule"):
        return errs
    com = d.get("PVObject_Commercial", {})
    errs.check(
        bool(_text(com.get("Manufacturer"))) and bool(_text(com.get("Model"))),
        "Manufacturer/Model empty",
    )
    for key in PAN_REQUIRED:
        errs.check(key in d, f"missing key {key}")
    if errs:
        return errs

    tech = _TECH_BY_CODE.get(str(d["Technol"]))
    errs.check(tech is not None, f"unknown Technol {d['Technol']!r}")
    v = {k: _num(d, k, errs) for k in PAN_REQUIRED if k != "Technol"}
    if errs:
        return errs
    isc, voc, imp, vmp = v["Isc"], v["Voc"], v["Imp"], v["Vmp"]
    ns = int(v["NCelS"])
    errs.check(0 < imp < isc, f"require 0 < Imp < Isc ({imp}, {isc})")
    errs.check(0 < vmp < voc, f"require 0 < Vmp < Voc ({vmp}, {voc})")
    errs.check(ns > 0 and v["NCelP"] >= 1, "invalid cell counts")
    errs.check(0.4 <= voc / max(ns, 1) <= 1.6, f"implausible Voc per cell {voc / max(ns, 1):.3f} V")
    errs.check(v["RShunt"] > 0 and v["Rp_0"] > v["RShunt"], "require Rp_0 > RShunt > 0")
    errs.check(v["RSerie"] >= 0, "negative RSerie")
    errs.check(0.5 <= v["Gamma"] <= 2.5, f"Gamma {v['Gamma']} outside 0.5-2.5")
    errs.check(abs(v["muGamma"]) < 0.02, f"muGamma {v['muGamma']} implausible")
    errs.check(v["muPmpReq"] < 0, "muPmpReq must be negative")
    errs.check(abs(vmp * imp - v["PNom"]) / v["PNom"] < 0.02, "Vmp*Imp differs from PNom > 2 %")
    if errs or tech is None:
        return errs

    # Rebuild PVsyst model from the file's own parameters.
    kw = dict(
        alpha_sc=v["muISC"] / 1000.0,
        gamma_ref=v["Gamma"],
        mu_gamma=v["muGamma"],
        R_sh_ref=v["RShunt"],
        R_sh_0=v["Rp_0"],
        R_s=v["RSerie"],
        cells_in_series=ns,
        R_sh_exp=v["Rp_Exp"],
        EgRef=BANDGAP_EV[tech],
    )
    # I_L and I_o are not stored in PAN files: PVsyst derives them from Isc/Voc.
    vth = 1.380649e-23 * 298.15 / 1.602176634e-19
    a = v["Gamma"] * ns * vth
    rs, rsh = v["RSerie"], v["RShunt"]
    io = (isc * (1 + rs / rsh) - voc / rsh) / (math.exp(voc / a) - math.exp(isc * rs / a))
    il = io * math.expm1(voc / a) + voc / rsh
    if not errs.check(io > 0 and il > 0, "derived I0/IL not positive"):
        return errs
    il_, io_, rs_, rsh_, nnsvth = pvsystem.calcparams_pvsyst(
        1000.0, 25.0, I_L_ref=il, I_o_ref=io, **kw
    )
    out = pvsystem.singlediode(il_, io_, rs_, rsh_, nnsvth)
    i_at_vmp = float(pvsystem.i_from_v(vmp, il_, io_, rs_, rsh_, nnsvth))
    pmp = float(out["p_mp"])
    errs.check(
        abs(float(out["i_sc"]) / isc - 1) < 2e-3, f"model Isc {float(out['i_sc']):.4f} != {isc}"
    )
    errs.check(
        abs(float(out["v_oc"]) / voc - 1) < 2e-3, f"model Voc {float(out['v_oc']):.4f} != {voc}"
    )
    errs.check(
        abs(i_at_vmp / imp - 1) < 5e-3, f"datasheet MPP not on curve (I(Vmp)={i_at_vmp:.4f})"
    )
    errs.check(-1e-3 <= pmp / (vmp * imp) - 1 <= 0.01, f"model Pmax {pmp:.2f} vs Vmp*Imp off > 1 %")
    tc = 100 * float(
        pvsyst_temperature_coeff(
            kw["alpha_sc"],
            v["Gamma"],
            v["muGamma"],
            il,
            io,
            rsh,
            v["Rp_0"],
            rs,
            ns,
            R_sh_exp=v["Rp_Exp"],
            EgRef=BANDGAP_EV[tech],
        )
    )
    errs.check(
        abs(tc - v["muPmpReq"]) < 0.02, f"model Pmax temp. coeff {tc:.3f} != {v['muPmpReq']}"
    )

    if spec is not None:
        errs.check(_text(com.get("Model")) == sanitize(spec.model), "Model differs from spec")
        errs.check(ns == spec.cells_in_series, "NCelS differs from spec")
        # Tolerance: half a unit of the last digit written, plus float slack.
        for key, val, tol in (
            ("Isc", spec.isc, 5e-4),
            ("Voc", spec.voc, 5e-3),
            ("Imp", spec.imp, 5e-4),
            ("Vmp", spec.vmp, 5e-3),
            ("PNom", spec.p_nom, 0.05),
        ):
            errs.check(
                abs(v[key] - val) <= tol * (1 + 1e-6), f"{key} {v[key]} differs from spec {val}"
            )
    return errs


# --------------------------------------------------------------------------- #
# OND
# --------------------------------------------------------------------------- #


def _check_profile(prof: Any, p_nom_w: float, label: str, errs: _Errors) -> np.ndarray | None:
    if not errs.check(isinstance(prof, dict), f"{label} missing"):
        return None
    n_eff, n_max = prof.get("NPtsEff"), prof.get("NPtsMax")
    if not errs.check(
        isinstance(n_eff, int) and isinstance(n_max, int) and 3 <= n_eff <= n_max,
        f"{label}: bad NPtsEff/NPtsMax",
    ):
        return None
    pts = []
    for i in range(1, n_max + 1):
        p = prof.get(f"Point_{i}")
        if not errs.check(isinstance(p, list) and len(p) == 2, f"{label}: Point_{i} malformed"):
            return None
        pts.append((float(p[0]), float(p[1])))
    arr = np.array(pts[:n_eff])
    errs.check(all(x == (0.0, 0.0) for x in pts[n_eff:]), f"{label}: padding points not zero")
    pin, pout = arr[:, 0], arr[:, 1]
    errs.check(pout[0] == 0 and pin[0] > 0, f"{label}: first point must be (threshold, 0)")
    errs.check(bool(np.all(np.diff(pin) > 0)), f"{label}: P_in not strictly increasing")
    errs.check(bool(np.all(np.diff(pout) > 0)), f"{label}: P_out not strictly increasing")
    eff = pout[1:] / pin[1:]
    errs.check(bool(np.all((eff > 0) & (eff < 1))), f"{label}: efficiency outside 0-100 %")
    hi = pout[1:] >= 0.1 * p_nom_w
    errs.check(bool(np.all(eff[hi] > 0.5)), f"{label}: efficiency < 50 % above 10 % load")
    # PNomConv is written in kW with 3 decimals: allow its 0.5 W rounding.
    errs.check(pout[-1] >= p_nom_w - 0.5 - 1e-6, f"{label}: does not reach nominal power")
    return arr


def _profile_figures(arr: np.ndarray, p_nom_w: float) -> tuple[float, float, float]:
    fr = arr[1:, 1] / p_nom_w
    eff = arr[1:, 1] / arr[1:, 0]
    euro = sum(w * float(np.interp(p, fr, eff)) for p, w in EURO_WEIGHTS.items())
    p_in_nom = float(np.interp(1.0, fr, arr[1:, 0], right=arr[-1, 0]))
    return 100 * float(np.max(eff)), 100 * euro, p_in_nom


def verify_ond(path: str | Path, spec: InverterDatasheet | None = None) -> list[str]:
    """Return a list of problems (empty when the OND file is sound)."""
    path = Path(path)
    byte_errs = check_bytes(path, "pvGInverter")
    return byte_errs + _verify_ond_values(path, spec)


def _verify_ond_values(path: Path, spec: InverterDatasheet | None) -> list[str]:
    errs = _Errors()
    try:
        top = read_panond(path, encoding="utf-8-sig")
    except Exception as exc:
        return [f"unparseable: {exc}"]
    d = top.get("PVObject_", {})
    if not errs.check(d.get("PVObject_") == "pvGInverter", "root object is not pvGInverter"):
        return errs
    com = d.get("PVObject_Commercial", {})
    errs.check(
        bool(_text(com.get("Manufacturer"))) and bool(_text(com.get("Model"))),
        "Manufacturer/Model empty",
    )
    conv = d.get("Converter")
    if not errs.check(isinstance(conv, dict), "Converter block missing"):
        return errs
    for key in OND_REQUIRED_TOP:
        errs.check(key in d, f"missing key {key}")
    for key in OND_REQUIRED_CONV:
        errs.check(key in conv, f"missing Converter key {key}")
    if errs:
        return errs

    num = {k: _num(conv, k, errs) for k in OND_REQUIRED_CONV if k not in ("MonoTri", "ProfilPIO")}
    if errs:
        return errs
    p_nom_w = num["PNomConv"] * 1e3
    errs.check(num["PNomConv"] > 0 and num["PMaxOUT"] >= num["PNomConv"], "PMaxOUT < PNomConv")
    errs.check(
        0 < num["VMppMin"] < num["VMPPMax"] <= num["VAbsMax"],
        "require 0 < VMppMin < VMPPMax <= VAbsMax",
    )
    errs.check(conv["MonoTri"] in ("Mono", "Tri"), "MonoTri must be Mono/Tri")
    errs.check(
        50 < num["EfficEuro"] < num["EfficMax"] < 100, "require 50 < EfficEuro < EfficMax < 100"
    )
    errs.check(
        num["IMaxDC"] > 0 and num["INomAC"] > 0 and num["IMaxAC"] >= num["INomAC"] * 0.999,
        "invalid currents",
    )
    errs.check(d["NbMPPT"] >= 1 and d["NbInputs"] >= d["NbMPPT"], "invalid NbMPPT/NbInputs")
    errs.check(d["TanPhiMin"] <= 0 <= d["TanPhiMax"], "invalid TanPhi range")

    arr = _check_profile(conv["ProfilPIO"], p_nom_w, "ProfilPIO", errs)
    if arr is not None:
        eff_max, euro, p_in_nom = _profile_figures(arr, p_nom_w)
        errs.check(
            abs(eff_max - num["EfficMax"]) < 0.06,
            f"EfficMax {num['EfficMax']} inconsistent with profile ({eff_max:.3f})",
        )
        errs.check(
            abs(euro - num["EfficEuro"]) < 0.06,
            f"EfficEuro {num['EfficEuro']} inconsistent with profile ({euro:.3f})",
        )
        errs.check(
            abs(p_in_nom / (num["PNomDC"] * 1e3) - 1) < 5e-3, "PNomDC inconsistent with profile"
        )
        errs.check(abs(arr[0, 0] - num["PSeuil"]) < 0.1, "PSeuil differs from profile threshold")

    if "VNomEff" in conv:
        volts = [x for x in conv["VNomEff"] if x != ""]
        ok = errs.check(
            len(volts) == 3 and volts == sorted(volts), "VNomEff must hold 3 rising values"
        )
        if ok:
            errs.check(
                num["VMppMin"] - 1e-6 <= volts[0] and volts[-1] <= num["VMPPMax"] + 1e-6,
                "VNomEff outside MPPT range",
            )
        for i in (1, 2, 3):
            _check_profile(conv.get(f"ProfilPIOV{i}"), p_nom_w, f"ProfilPIOV{i}", errs)

    if spec is not None:
        errs.check(_text(com.get("Model")) == sanitize(spec.model), "Model differs from spec")
        errs.check(
            abs(num["PNomConv"] - spec.p_nom_ac_kw) <= 5e-4 + 1e-9, "PNomConv differs from spec"
        )
        # Integer MPPT window, rounded inwards by the writer.
        errs.check(
            spec.v_mpp_min <= num["VMppMin"] < spec.v_mpp_min + 1, "VMppMin differs from spec"
        )
        errs.check(
            spec.v_mpp_max - 1 < num["VMPPMax"] <= spec.v_mpp_max, "VMPPMax differs from spec"
        )
        errs.check(spec.v_dc_max - 1 < num["VAbsMax"] <= spec.v_dc_max, "VAbsMax differs from spec")
    return errs


# --------------------------------------------------------------------------- #
# Whole library
# --------------------------------------------------------------------------- #


def _verify_one(args: tuple[str, str, str]) -> list[str]:
    from datasheet_to_pan_ond.io import load_inverter, load_module

    kind, out_path, yaml_path = args
    yml = Path(yaml_path)
    try:
        if not yml.read_text(encoding="utf-8").startswith("# Generated by"):
            return ["spec YAML lacks provenance header"]
        if kind == "modules":
            return verify_pan(out_path, load_module(yml))
        return verify_ond(out_path, load_inverter(yml))
    except Exception as exc:
        return [f"spec unreadable or invalid: {type(exc).__name__}: {exc}"]


class LibraryReport:
    """Outcome of :func:`verify_library`."""

    def __init__(self) -> None:
        self.checked: dict[str, int] = {}
        self.problems: list[tuple[str, str]] = []  # (path, message)

    @property
    def ok(self) -> bool:
        return not self.problems

    def add(self, path: str, msg: str) -> None:
        self.problems.append((path, msg))


def verify_library(root: str | Path, jobs: int = 0, write_report: bool = True) -> LibraryReport:
    """Verify every file, checksum, index row and README link in a library."""
    import csv
    import datetime as dt
    import hashlib
    import os
    from concurrent.futures import ProcessPoolExecutor

    from datasheet_to_pan_ond._version import __version__
    from datasheet_to_pan_ond.library import index

    root = Path(root)
    rep = LibraryReport()
    if not (root / "README.md").exists():
        rep.add("README.md", "missing")

    # 1. checksums
    sums: dict[str, str] = {}
    sums_path = root / index.SUMS_NAME
    if sums_path.exists():
        for line in sums_path.read_text(encoding="utf-8").splitlines():
            digest, _, rel = line.partition("  ")
            sums[rel] = digest
    else:
        rep.add(index.SUMS_NAME, "missing")
    on_disk = {p.relative_to(root).as_posix(): p for p in index.component_files(root)}
    for rel in sorted(set(sums) - set(on_disk)):
        rep.add(rel, "listed in SHA256SUMS but missing")
    for rel in sorted(set(on_disk) - set(sums)):
        rep.add(rel, "not listed in SHA256SUMS")
    for rel in sorted(set(sums) & set(on_disk)):
        if hashlib.sha256(on_disk[rel].read_bytes()).hexdigest() != sums[rel]:
            rep.add(rel, "checksum mismatch")

    # 2. index rows <-> files, stray files, READMEs
    tasks: list[tuple[str, str, str]] = []
    listed: set[str] = set()
    for kind in index.KINDS:
        base = root / kind
        if not base.exists():
            continue
        for name in ("README.md", index.INDEX_NAME, index.EXCLUDED_NAME):
            if not (base / name).exists():
                rep.add(f"{kind}/{name}", "missing")
        out_col = "pan_file" if kind == "modules" else "ond_file"
        rows = []
        if (base / index.INDEX_NAME).exists():
            with (base / index.INDEX_NAME).open(encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
        rep.checked[kind] = len(rows)
        for r in rows:
            out_rel, yml_rel = f"{kind}/{r[out_col]}", f"{kind}/{r['yaml_file']}"
            listed |= {out_rel, yml_rel}
            if out_rel not in on_disk or yml_rel not in on_disk:
                rep.add(out_rel, "index row without file")
                continue
            tasks.append((kind, str(on_disk[out_rel]), str(on_disk[yml_rel])))
        allowed = {"README.md", index.INDEX_NAME, index.EXCLUDED_NAME}
        for p in base.rglob("*"):
            rel = p.relative_to(root).as_posix()
            if p.is_dir():
                if not (p / "README.md").exists():
                    rep.add(rel, "folder without README.md")
                continue
            if p.suffix in {".PAN", ".OND", ".yaml"}:
                if rel not in listed:
                    rep.add(rel, "file not in index.csv")
            elif p.name not in allowed:
                rep.add(rel, "unexpected file")
        for d in {p.parent for p in base.rglob("*") if p.suffix in {".PAN", ".OND"}}:
            readme = (
                (d / "README.md").read_text(encoding="utf-8") if (d / "README.md").exists() else ""
            )
            for f in d.iterdir():
                if f.suffix in {".PAN", ".OND", ".yaml"} and f"]({f.name})" not in readme:
                    rep.add(f.relative_to(root).as_posix(), "not linked from folder README")

    # 3. per-file verification
    n_jobs = jobs if jobs > 0 else (os.cpu_count() or 1)
    if n_jobs == 1 or len(tasks) < 50:
        results = list(map(_verify_one, tasks))
    else:
        with ProcessPoolExecutor(max_workers=n_jobs) as pool:
            results = list(pool.map(_verify_one, tasks, chunksize=64))
    for (_, out, _), errs in zip(tasks, results, strict=True):
        for msg in errs:
            rep.add(Path(out).relative_to(root).as_posix(), msg)

    if write_report:
        _write_verification(root, rep, __version__, dt.date.today().isoformat())
    return rep


def _write_verification(root: Path, rep: LibraryReport, version: str, date: str) -> None:
    from datasheet_to_pan_ond.library import index

    status = "PASSED" if rep.ok else f"FAILED ({len(rep.problems)} problems)"
    counts = "\n".join(f"| {k} | {n:,} |" for k, n in rep.checked.items())
    lines = [
        "# Verification report",
        "",
        f"**{status}** - `ds2panond library verify`, datasheet-to-pan-ond {version}, {date}.",
        "",
        "| Kind | Files verified |",
        "|---|---:|",
        counts,
        "",
        "## Checks",
        "",
        "Per file (independent of the fitting code; files re-read from disk with pvlib):",
        "",
        "- bytes: UTF-8 with BOM, CRLF line endings only, correct closing line, no NaN/inf;",
        "- all keys PVsyst needs are present, numeric values finite;",
        "- PAN: 0 < Imp < Isc, 0 < Vmp < Voc, plausible Voc per cell, Rp_0 > RShunt > 0,",
        "  RSerie >= 0, Gamma 0.5-2.5; the PVsyst one-diode model rebuilt from the file",
        "  reproduces Isc and Voc (0.2 %), passes through the datasheet MPP (0.5 %),",
        "  gives Pmax within 0..+1 % of Vmp*Imp and the stated Pmax temperature",
        "  coefficient (0.02 %/K);",
        "- OND: VMppMin < VMPPMax <= VAbsMax, PMaxOUT >= PNomConv, every efficiency",
        "  profile strictly increasing in P_in and P_out with efficiency 0-100 % (> 50 %",
        "  above 10 % load) reaching nominal power, zero padding; EfficMax, EfficEuro,",
        "  PNomDC and PSeuil consistent with the profile; voltage-dependent profiles valid;",
        "- key values (model, cells, Isc/Voc/Imp/Vmp/Pnom; Pnom AC, MPPT and max DC",
        "  voltage) match the YAML spec, which must validate against the input schema.",
        "",
        "Library level: every file listed in "
        f"`{index.SUMS_NAME}` with a matching checksum and vice versa; every file in",
        "`index.csv` and linked from its folder README; every folder has a README; no",
        "unexpected or temporary files.",
    ]
    if rep.problems:
        lines += ["", "## Problems", ""]
        lines += [f"- `{p}`: {m}" for p, m in rep.problems[:500]]
        if len(rep.problems) > 500:
            lines.append(f"- ... and {len(rep.problems) - 500} more")
    (root / index.VERIFICATION_NAME).write_text("\n".join(lines) + "\n", encoding="utf-8")
