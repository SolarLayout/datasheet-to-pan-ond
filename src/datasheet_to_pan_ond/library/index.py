"""README / CSV indexes and checksums for the generated library."""

from __future__ import annotations

import csv
import hashlib
from collections import Counter, defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from datasheet_to_pan_ond._version import __version__

if TYPE_CHECKING:
    from datasheet_to_pan_ond.library.build import Entry, Excluded

KINDS = ("modules", "inverters")
SUMS_NAME = "SHA256SUMS"
INDEX_NAME = "index.csv"
EXCLUDED_NAME = "excluded.csv"
VERIFICATION_NAME = "VERIFICATION.md"

INVERTER_COLUMNS = [
    "class",
    "manufacturer",
    "model",
    "ond_file",
    "yaml_file",
    "p_nom_ac_kw",
    "v_ac",
    "phases",
    "v_mpp_min",
    "v_mpp_max",
    "v_dc_max",
    "eff_max_pct",
    "eff_euro_pct",
    "eff_cec_pct",
    "hybrid",
    "needs_review",
    "cec_name",
]
MODULE_COLUMNS = [
    "technology",
    "manufacturer",
    "model",
    "pan_file",
    "yaml_file",
    "p_nom",
    "cells_in_series",
    "cells_in_parallel",
    "voc",
    "isc",
    "vmp",
    "imp",
    "temp_coeff_pmax_pct",
    "bifacial",
    "needs_review",
    "cec_name",
]


def _md(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _yes(flag: bool) -> str:
    return "yes" if flag else ""


def _fmt(v: float, nd: int) -> str:
    return f"{v:.{nd}f}"


def _class_info(kind: str) -> dict[str, str]:
    from datasheet_to_pan_ond.library.build import INVERTER_CLASSES, MODULE_CLASSES

    return MODULE_CLASSES if kind == "modules" else INVERTER_CLASSES


def _row(e: Entry) -> dict[str, Any]:
    s = e.spec
    base = Path(e.out_rel).name, Path(e.yaml_rel).name
    rel_out, rel_yaml = f"{e.cls}/{e.mfr_slug}/{base[0]}", f"{e.cls}/{e.mfr_slug}/{base[1]}"
    if e.kind == "inverters":
        return {
            "class": e.cls,
            "manufacturer": e.manufacturer,
            "model": e.model,
            "ond_file": rel_out,
            "yaml_file": rel_yaml,
            "p_nom_ac_kw": s["p_nom_ac_kw"],
            "v_ac": s["v_ac"],
            "phases": s["phases"],
            "v_mpp_min": s["v_mpp_min"],
            "v_mpp_max": s["v_mpp_max"],
            "v_dc_max": s["v_dc_max"],
            "eff_max_pct": s["eff_max_pct"],
            "eff_euro_pct": s["eff_euro_pct"],
            "eff_cec_pct": s["eff_cec_pct"],
            "hybrid": _yes("hybrid" in e.flags),
            "needs_review": _yes(e.needs_review),
            "cec_name": e.name,
        }
    return {
        "technology": e.cls,
        "manufacturer": e.manufacturer,
        "model": e.model,
        "pan_file": rel_out,
        "yaml_file": rel_yaml,
        "p_nom": s["p_nom"],
        "cells_in_series": s["cells_in_series"],
        "cells_in_parallel": s["cells_in_parallel"],
        "voc": s["voc"],
        "isc": s["isc"],
        "vmp": s["vmp"],
        "imp": s["imp"],
        "temp_coeff_pmax_pct": s["temp_coeff_pmax_pct"],
        "bifacial": _yes("bifacial" in e.flags),
        "needs_review": _yes(e.needs_review),
        "cec_name": e.name,
    }


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def _power(e: Entry) -> float:
    return float(e.spec["p_nom_ac_kw"] if e.kind == "inverters" else e.spec["p_nom"])


def _power_range(items: list[Entry]) -> str:
    lo, hi = min(map(_power, items)), max(map(_power, items))
    unit = "kW" if items[0].kind == "inverters" else "W"

    def f(x: float) -> str:
        return f"{x:g}" if unit == "W" else f"{x:,.1f}".rstrip("0").rstrip(".")

    return f"{f(lo)} {unit}" if lo == hi else f"{f(lo)}-{f(hi)} {unit}"


# --------------------------------------------------------------------------- #
# Per-kind
# --------------------------------------------------------------------------- #


def write_kind(
    root: Path, kind: str, built: list[Entry], excluded: list[Excluded], meta: dict[str, Any]
) -> None:
    base = root / kind
    base.mkdir(parents=True, exist_ok=True)
    built = sorted(built, key=lambda e: (e.cls, e.manufacturer.lower(), e.model.lower(), e.stem))
    cols = MODULE_COLUMNS if kind == "modules" else INVERTER_COLUMNS
    _write_csv(base / INDEX_NAME, cols, [_row(e) for e in built])
    _write_csv(
        base / EXCLUDED_NAME,
        ["cec_name", "stage", "reason"],
        [
            {"cec_name": x.name, "stage": x.stage, "reason": x.reason}
            for x in sorted(excluded, key=lambda x: (x.stage, x.name))
        ],
    )

    by_cls: dict[str, list[Entry]] = defaultdict(list)
    for e in built:
        by_cls[e.cls].append(e)
    info = _class_info(kind)
    suffix = "PAN" if kind == "modules" else "OND"
    label = "Modules" if kind == "modules" else "Inverters"
    group = "Technology" if kind == "modules" else "Class"

    lines = [
        f"# {label}",
        "",
        f"{len(built):,} PVsyst `.{suffix}` files generated from the CEC {kind[:-1]} list "
        f"(NREL SAM library {meta.get('library') or 'n/a'}, built {meta.get('date')}). "
        f"Each `.{suffix}` has a `.yaml` spec next to it with the input values and "
        "`provenance_notes` listing every inferred or assumed value.",
        "",
        f"| {group} | Description | Files | Manufacturers |",
        "|---|---|---:|---:|",
    ]
    for cls in info:
        items = by_cls.get(cls, [])
        if items:
            n_mfr = len({e.mfr_slug for e in items})
            lines.append(f"| [{cls}]({cls}/README.md) | {info[cls]} | {len(items):,} | {n_mfr} |")
    review = sum(e.needs_review for e in built)
    stages = Counter(x.stage for x in excluded)
    lines += [
        "",
        f"- Machine-readable index of all files: [`{INDEX_NAME}`]({INDEX_NAME})",
        f"- Records needing review (inferred values marked VERIFY): {review:,} "
        "(`needs_review = yes` in the index)",
        f"- Excluded records: {len(excluded):,} - see [`{EXCLUDED_NAME}`]({EXCLUDED_NAME})",
    ]
    for stage, n in sorted(stages.items()):
        lines.append(f"  - {stage}: {n:,}")
    lines += ["", _KIND_NOTES[kind]]
    (base / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    for cls, items in by_cls.items():
        _write_class(base / cls, kind, cls, info[cls], items)


_KIND_NOTES = {
    "inverters": (
        "## Notes\n\n"
        "- Efficiency curves (main and at Mppt_low / Vdco / Mppt_high) are evaluated from the\n"
        "  Sandia model parameters that SAM fitted to CEC laboratory measurements.\n"
        "- Not in the CEC data and assumed: number of MPPTs (1), phase count (from AC\n"
        "  voltage; 208 V may be single- or three-phase), power factor range (unity).\n"
        "- `hybrid = yes`: CEC lists the unit as battery-capable (hybrid / PCS).\n"
        "- Classes are by rated AC power; CEC does not classify inverters."
    ),
    "modules": (
        "## Notes\n\n"
        "- Not in the CEC data and assumed: low-light (200 W/m2) efficiency (technology\n"
        "  default), bifaciality factor (not written), whether cells are half-cut.\n"
        "- Cell counts that are implausible in the CEC data are corrected from the model\n"
        "  name, or estimated and flagged `needs_review`.\n"
        "- `bifacial = yes`: CEC lists the module as bifacial (front-side model only)."
    ),
}


def _write_class(d: Path, kind: str, cls: str, desc: str, items: list[Entry]) -> None:
    by_mfr: dict[str, list[Entry]] = defaultdict(list)
    for e in items:
        by_mfr[e.mfr_slug].append(e)
    lines = [
        f"# {cls}: {desc}",
        "",
        f"[Up](../README.md) - {len(items):,} files from {len(by_mfr)} manufacturers.",
        "",
        "| Manufacturer | Files | Power range | Needs review |",
        "|---|---:|---|---:|",
    ]
    for slug in sorted(by_mfr, key=lambda s: by_mfr[s][0].manufacturer.lower()):
        es = by_mfr[slug]
        lines.append(
            f"| [{_md(es[0].manufacturer)}]({slug}/README.md) | {len(es):,} | {_power_range(es)} "
            f"| {sum(e.needs_review for e in es) or ''} |"
        )
    d.mkdir(parents=True, exist_ok=True)
    (d / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    for slug, es in by_mfr.items():
        _write_manufacturer(d / slug, kind, cls, es)


def _write_manufacturer(d: Path, kind: str, cls: str, items: list[Entry]) -> None:
    items = sorted(items, key=lambda e: (_power(e), e.model.lower()))
    mfr = items[0].manufacturer
    if kind == "inverters":
        head = (
            "| Model | AC kW | V AC | Phases | MPPT V | V DC max | Euro % | CEC % "
            "| Flags | Files |\n|---|---:|---:|---|---|---:|---:|---:|---|---|"
        )
    else:
        head = (
            "| Model | Pnom W | Cells | Voc V | Isc A | Pmax %/K | Flags | Files |\n"
            "|---|---:|---|---:|---:|---:|---|---|"
        )
    lines = [f"# {_md(mfr)} - {cls}", "", f"[Up](../README.md) - {len(items)} files.", "", head]
    for e in items:
        s = e.spec
        flags = ", ".join([*e.flags, *(["VERIFY"] if e.needs_review else [])])
        out, yml = Path(e.out_rel).name, Path(e.yaml_rel).name
        files = f"[{e.suffix[1:]}]({out}) / [spec]({yml})"
        if kind == "inverters":
            lines.append(
                f"| {_md(e.model)} | {_fmt(s['p_nom_ac_kw'], 3)} | {s['v_ac']:g} | {s['phases']} "
                f"| {s['v_mpp_min']:g}-{s['v_mpp_max']:g} | {s['v_dc_max']:g} "
                f"| {_fmt(s['eff_euro_pct'], 2)} | {_fmt(s['eff_cec_pct'], 2)} "
                f"| {flags} | {files} |"
            )
        else:
            cells = f"{s['cells_in_series']}" + (
                f" x {s['cells_in_parallel']}" if s["cells_in_parallel"] > 1 else ""
            )
            lines.append(
                f"| {_md(e.model)} | {s['p_nom']:g} | {cells} | {s['voc']:g} | {s['isc']:g} "
                f"| {s['temp_coeff_pmax_pct']:g} | {flags} | {files} |"
            )
    d.mkdir(parents=True, exist_ok=True)
    (d / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# Root
# --------------------------------------------------------------------------- #


def component_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for kind in KINDS:
        base = root / kind
        if base.exists():
            out += [
                p for p in base.rglob("*") if p.is_file() and p.suffix in {".PAN", ".OND", ".yaml"}
            ]
    return sorted(out, key=lambda p: p.relative_to(root).as_posix())


def write_sums(root: Path) -> None:
    lines = [
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(root).as_posix()}"
        for p in component_files(root)
    ]
    (root / SUMS_NAME).write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_root(root: Path) -> None:
    import json

    write_sums(root)
    rows: list[str] = []
    meta_lines: list[str] = []
    for kind in KINDS:
        idx = _read_csv(root / kind / INDEX_NAME)
        if not (root / kind / INDEX_NAME).exists():
            continue
        exc = _read_csv(root / kind / EXCLUDED_NAME)
        meta_p = root / ".build" / f"{kind}.meta.json"
        meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}
        cls_key = "technology" if kind == "modules" else "class"
        counts = Counter(r[cls_key] for r in idx)
        info = _class_info(kind)
        for cls in info:
            if counts.get(cls):
                rows.append(
                    f"| {kind} | [{cls}]({kind}/{cls}/README.md) | {info[cls]} | {counts[cls]:,} |"
                )
        rows.append(
            f"| **{kind}** | [all]({kind}/README.md) | of {meta.get('records', '?'):,} "
            f"CEC records; {len(exc):,} excluded | **{len(idx):,}** |"
        )
        meta_lines.append(
            f"| {kind} | {meta.get('library') or 'n/a'} | {meta.get('source', '')} "
            f"| {meta.get('date', '')} | {meta.get('tool_version', '')} |"
        )

    text = f"""# PVsyst component library (generated)

PVsyst `.PAN` (PV module) and `.OND` (inverter) files generated by
[datasheet-to-pan-ond](../README.md) from the public CEC equipment lists.
Every file has passed an independent verification (see
[{VERIFICATION_NAME}]({VERIFICATION_NAME})).

> These files are **estimates built from public list data**, not manufacturer
> PAN/OND files made from laboratory measurements. Inverter curves do come
> from CEC test data. Module low-light behaviour is a technology default.
> Use manufacturer files for bankable energy assessments.

## Contents

| Kind | Group | Description | Files |
|---|---|---|---:|
{chr(10).join(rows)}

## Layout

```
<kind>/README.md                 overview, classes, exclusions
<kind>/index.csv                 one row per file with key specs (machine-readable index)
<kind>/excluded.csv              records not included, with stage and reason
<kind>/<group>/README.md         manufacturers in the group
<kind>/<group>/<maker>/README.md models with key specs and links
<kind>/<group>/<maker>/<model>.PAN|.OND  PVsyst file
<kind>/<group>/<maker>/<model>.yaml      input spec + provenance_notes
SHA256SUMS                        checksums of every PAN/OND/yaml file
VERIFICATION.md                   result of the last `ds2panond library verify`
```

## Quality policy

A CEC record is included only if:

1. it converts to a valid spec (consistent STC values, plausible coefficients,
   consistent voltage ranges). Implausible cell counts are corrected and
   flagged;
2. the model fit completes **without warnings**;
3. the written file passes verification. Verification re-reads it with pvlib,
   rebuilds the PVsyst model from the file's own parameters, and compares the
   result with the spec (Isc, Voc, MPP, temperature coefficient; monotonic
   efficiency profile, efficiency figures, nominal DC power).

Everything else is listed in `<kind>/excluded.csv` with the reason.

## Provenance

| Kind | SAM library | Source | Built | Tool version |
|---|---|---|---|---|
{chr(10).join(meta_lines)}

CEC equipment data comes from the California Energy Commission via the
[NREL SAM](https://github.com/NREL/SAM) library (BSD-3-Clause).
PVsyst is a trademark of PVsyst SA; this library is not affiliated with it.

## Regenerate / verify

```bash
uv run ds2panond library build library --kind inverters --jobs 0
uv run ds2panond library build library --kind modules --jobs 0
uv run ds2panond library verify library --jobs 0
```
"""
    (root / "README.md").write_text(text, encoding="utf-8")


def tool_version() -> str:
    return __version__
