"""Build a curated, verified PAN/OND library from the CEC equipment lists.

Layout::

    <root>/
      README.md, VERIFICATION.md, SHA256SUMS
      inverters/
        README.md, index.csv, excluded.csv
        <class>/README.md
        <class>/<manufacturer>/README.md
        <class>/<manufacturer>/<model>.OND + <model>.yaml
      modules/
        <technology>/<manufacturer>/<model>.PAN + <model>.yaml   (same pattern)
      .build/            resumable build state (not part of the library)

Quality policy: a record is included only if it converts cleanly, fits
without warnings, and its written file passes :mod:`.verify`. Everything else
is listed in ``excluded.csv`` with the stage and reason.

Builds are resumable: per-record results are appended to
``.build/<kind>.jsonl`` as they complete. A run with a time budget stops
early and the next run continues where it left off. The README indexes are
(re)written only when every record has been processed.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from datasheet_to_pan_ond._version import __version__
from datasheet_to_pan_ond.inverter_fit import fit_inverter
from datasheet_to_pan_ond.io import dump_spec
from datasheet_to_pan_ond.library.verify import verify_ond, verify_pan
from datasheet_to_pan_ond.models import InverterDatasheet, ModuleDatasheet
from datasheet_to_pan_ond.module_fit import fit_module
from datasheet_to_pan_ond.sources import cec
from datasheet_to_pan_ond.sources.export import REVIEW_MARKER, slugify, source_label
from datasheet_to_pan_ond.writers import write_ond, write_pan

STATE_DIR = ".build"
COMPONENT_SUFFIXES = {".PAN", ".OND", ".yaml"}

#: Inverter classes by rated AC power (CEC does not classify inverters).
INVERTER_CLASSES: dict[str, str] = {
    "micro": "Microinverters (<= 2 kW AC, or <= 120 V max DC)",
    "string": "String inverters, 2-100 kW AC",
    "string-utility": "Utility-scale string inverters, 100-500 kW AC",
    "central": "Central inverters, >= 500 kW AC",
}
MICRO_MAX_KW = 2.0
MICRO_MAX_VDC = 120.0
STRING_MAX_KW = 100.0
CENTRAL_MIN_KW = 500.0

MODULE_CLASSES: dict[str, str] = {
    "mono-c-si": "Monocrystalline silicon",
    "multi-c-si": "Multicrystalline silicon",
    "hit": "Heterojunction silicon",
    "cdte": "Cadmium telluride (thin film)",
    "cigs": "CIGS / CIS (thin film)",
    "a-si": "Amorphous / nanocrystalline silicon (thin film)",
}
_MODULE_CLASS_BY_TECH = {
    "mtSiMono": "mono-c-si",
    "mtSiPoly": "multi-c-si",
    "mtHIT": "hit",
    "mtCdTe": "cdte",
    "mtCIS": "cigs",
    "mtAsiH": "a-si",
}


def inverter_class(p_nom_ac_kw: float, v_dc_max: float) -> str:
    if p_nom_ac_kw <= MICRO_MAX_KW or v_dc_max <= MICRO_MAX_VDC:
        return "micro"
    if p_nom_ac_kw < STRING_MAX_KW:
        return "string"
    if p_nom_ac_kw < CENTRAL_MIN_KW:
        return "string-utility"
    return "central"


@dataclass
class Entry:
    """One library item (a planned file pair)."""

    kind: cec.Kind
    name: str  # CEC record name
    cls: str
    manufacturer: str
    mfr_slug: str
    model: str
    stem: str
    spec: dict[str, Any]
    needs_review: bool
    flags: list[str] = field(default_factory=list)

    @property
    def suffix(self) -> str:
        return ".PAN" if self.kind == "modules" else ".OND"

    @property
    def rel_dir(self) -> str:
        return f"{self.kind}/{self.cls}/{self.mfr_slug}"

    @property
    def yaml_rel(self) -> str:
        return f"{self.rel_dir}/{self.stem}.yaml"

    @property
    def out_rel(self) -> str:
        return f"{self.rel_dir}/{self.stem}{self.suffix}"

    def spec_hash(self) -> str:
        payload = json.dumps(
            {"spec": self.spec, "paths": [self.yaml_rel, self.out_rel], "code": CODE_FINGERPRINT},
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass
class Excluded:
    name: str
    stage: str  # duplicate | conversion | fit | verification
    reason: str


@dataclass
class BuildResult:
    kind: cec.Kind
    complete: bool
    entries: list[Entry]
    built: list[Entry]
    excluded: list[Excluded]
    processed_now: int
    pending: int
    library_version: str


def _code_fingerprint() -> str:
    """Hash of this package's source: any code change invalidates resumable results."""
    pkg = Path(__file__).resolve().parents[1]
    h = hashlib.sha256()
    for p in sorted(pkg.rglob("*.py")):
        h.update(p.relative_to(pkg).as_posix().encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


CODE_FINGERPRINT = _code_fingerprint()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- #
# Planning
# --------------------------------------------------------------------------- #


def plan(kind: cec.Kind, df: pd.DataFrame, library: str) -> tuple[list[Entry], list[Excluded]]:
    """Convert all rows and assign classes and file paths (deterministic)."""
    excluded: list[Excluded] = []
    df = df.copy()
    dup = df["Name"].duplicated(keep="last")
    for name in df.loc[dup, "Name"]:
        excluded.append(
            Excluded(str(name), "duplicate", "duplicate record in CEC list (later kept)")
        )
    df = df.loc[~dup]

    raw: list[tuple[str, str, str, dict[str, Any], list[str]]] = []
    for _, row in df.iterrows():
        conv = cec.convert(kind, row, library)
        if not conv.ok or conv.spec is None:
            excluded.append(Excluded(conv.name, "conversion", conv.error or "unknown"))
            continue
        s = conv.spec
        if kind == "modules":
            cls = _MODULE_CLASS_BY_TECH[s["technology"]]
        else:
            cls = inverter_class(s["p_nom_ac_kw"], s["v_dc_max"])
        raw.append((conv.name, cls, s["manufacturer"], s, conv.notes))

    # Normalise manufacturer spelling variants (e.g. "POWER ELECTRONICS").
    variants: dict[str, Counter[str]] = {}
    for _, _, mfr, _, _ in raw:
        variants.setdefault(slugify(mfr).lower(), Counter())[mfr] += 1
    display = {k: c.most_common(1)[0][0] for k, c in variants.items()}

    entries: list[Entry] = []
    used: dict[str, set[str]] = {}
    for name, cls, mfr, spec, notes in sorted(raw, key=lambda r: (r[1], r[2].lower(), r[0])):
        disp = display[slugify(mfr).lower()]
        mfr_slug = slugify(disp)
        spec["manufacturer"] = disp
        stem = slugify(spec["model"])
        folder = used.setdefault(f"{cls}/{mfr_slug}".lower(), set())
        base, i = stem, 2
        while stem.lower() in folder:
            stem = f"{base}_{i}"
            i += 1
        folder.add(stem.lower())
        flags = []
        if any("hybrid" in n for n in notes):
            flags.append("hybrid")
        if any(n.startswith("Bifacial") for n in notes):
            flags.append("bifacial")
        entries.append(
            Entry(
                kind,
                name,
                cls,
                disp,
                mfr_slug,
                spec["model"],
                stem,
                spec,
                any(REVIEW_MARKER in n for n in notes),
                flags,
            )
        )
    return entries, excluded


# --------------------------------------------------------------------------- #
# Worker
# --------------------------------------------------------------------------- #


def _header(e: Entry, library: str, source: str, date: str) -> list[str]:
    return [
        f"Generated by datasheet-to-pan-ond {__version__} on {date}",
        f"Source: CEC {e.kind[:-1]} list via NREL SAM library {library}".rstrip(),
        f"        {source_label(source)}",
        f"Record: {e.name}",
        "Inferred / assumed values are listed under provenance_notes.",
        "Check them against the manufacturer datasheet before relying on the output.",
    ]


def _work(args: tuple[str, dict[str, Any], str, str, list[str], str]) -> dict[str, Any]:
    """Fit, write, verify one record. Writes nothing that does not pass."""
    kind, spec, yaml_path, out_path, header, date = args
    out, yml = Path(out_path), Path(yaml_path)
    now = dt.datetime.fromisoformat(date)
    try:
        if kind == "modules":
            mds = ModuleDatasheet.model_validate(spec)
            mres = fit_module(mds)
            if mres.warnings:
                return {"status": "excluded", "stage": "fit", "reason": " | ".join(mres.warnings)}
            write_pan(mres, out, now=now)
            errors = verify_pan(out, mds)
        else:
            ids = InverterDatasheet.model_validate(spec)
            ires = fit_inverter(ids)
            if ires.warnings:
                return {"status": "excluded", "stage": "fit", "reason": " | ".join(ires.warnings)}
            write_ond(ires, out, now=now)
            errors = verify_ond(out, ids)
    except Exception as exc:  # any failure excludes the record, with the reason
        out.unlink(missing_ok=True)
        return {"status": "excluded", "stage": "fit", "reason": f"{type(exc).__name__}: {exc}"}
    if errors:
        out.unlink(missing_ok=True)
        return {"status": "excluded", "stage": "verification", "reason": " | ".join(errors)}
    dump_spec(spec, yml, header)
    return {"status": "built", "out_sha": sha256_file(out), "yaml_sha": sha256_file(yml)}


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #


def _state_path(root: Path, kind: str) -> Path:
    return root / STATE_DIR / f"{kind}.jsonl"


def _meta_path(root: Path, kind: str) -> Path:
    return root / STATE_DIR / f"{kind}.meta.json"


def load_state(root: Path, kind: str) -> dict[str, dict[str, Any]]:
    path = _state_path(root, kind)
    state: dict[str, dict[str, Any]] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                state[rec["name"]] = rec
    return state


def load_meta(root: Path, kind: str) -> dict[str, Any]:
    p = _meta_path(root, kind)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _is_done(e: Entry, rec: dict[str, Any] | None, root: Path) -> bool:
    if rec is None or rec.get("spec_hash") != e.spec_hash():
        return False
    if rec["status"] != "built":
        return True
    out, yml = root / e.out_rel, root / e.yaml_rel
    return (
        out.exists()
        and yml.exists()
        and sha256_file(out) == rec.get("out_sha")
        and sha256_file(yml) == rec.get("yaml_sha")
    )


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #


def build(
    kind: cec.Kind,
    root: str | Path,
    *,
    source: str | Path | None = None,
    refresh: bool = False,
    jobs: int = 0,
    date: str | None = None,
    time_budget: float | None = None,
) -> BuildResult:
    """Build (or resume building) the ``kind`` part of the library under ``root``."""
    import os

    from datasheet_to_pan_ond.library import index

    root = Path(root)
    (root / STATE_DIR).mkdir(parents=True, exist_ok=True)
    src = Path(source) if source is not None else cec.fetch(kind, refresh)
    df = cec.read_sam_csv(src)
    library = (
        cec.library_version(df)
        or _sibling_library_version(src)
        or load_meta(root, "modules").get("library", "")
    )

    meta = load_meta(root, kind)
    if date is None:
        date = meta.get("date") or dt.date.today().isoformat()
    meta.update(
        {
            "date": date,
            "library": library,
            "source": source_label(str(src)),
            "tool_version": __version__,
            "records": len(df),
        }
    )
    _meta_path(root, kind).write_text(json.dumps(meta, indent=2), encoding="utf-8")

    entries, excluded = plan(kind, df, library)
    state = load_state(root, kind)
    pending = [e for e in entries if not _is_done(e, state.get(e.name), root)]

    start = time.monotonic()
    processed = 0
    state_file = _state_path(root, kind).open("a", encoding="utf-8")
    try:
        n_jobs = jobs if jobs > 0 else (os.cpu_count() or 1)
        with ProcessPoolExecutor(max_workers=n_jobs) as pool:
            futures: dict[Future[dict[str, Any]], Entry] = {}
            queue = list(pending)
            while queue or futures:
                over = time_budget is not None and time.monotonic() - start > time_budget
                while queue and not over and len(futures) < n_jobs * 4:
                    e = queue.pop(0)
                    args = (
                        kind,
                        e.spec,
                        str(root / e.yaml_rel),
                        str(root / e.out_rel),
                        _header(e, library, str(src), date),
                        date,
                    )
                    futures[pool.submit(_work, args)] = e
                if not futures:
                    break
                done, _ = wait(futures, timeout=5, return_when=FIRST_COMPLETED)
                for fut in done:
                    e = futures.pop(fut)
                    rec = {"name": e.name, "spec_hash": e.spec_hash(), **fut.result()}
                    state[e.name] = rec
                    state_file.write(json.dumps(rec) + "\n")
                    state_file.flush()
                    processed += 1
    finally:
        state_file.close()

    remaining = [e for e in entries if not _is_done(e, state.get(e.name), root)]
    built = [e for e in entries if state.get(e.name, {}).get("status") == "built"]
    for e in entries:
        prev = state.get(e.name)
        if prev and prev["status"] == "excluded" and prev.get("spec_hash") == e.spec_hash():
            excluded.append(Excluded(e.name, prev["stage"], prev["reason"]))

    complete = not remaining
    if complete:
        _remove_strays(root, kind, built)
        _compact_state(root, kind, entries, state)
        index.write_kind(root, kind, built, excluded, meta)
        index.write_root(root)
    return BuildResult(kind, complete, entries, built, excluded, processed, len(remaining), library)


def _sibling_library_version(src: Path) -> str:
    """The inverter CSV has no version column; use the modules CSV fetched alongside it."""
    sib = src.with_name(cec.CACHE_FILES["modules"])
    if sib != src and sib.exists():
        try:
            return cec.library_version(cec.read_sam_csv(sib))
        except (OSError, ValueError):
            return ""
    return ""


def _remove_strays(root: Path, kind: str, built: list[Entry]) -> None:
    """Delete component files / READMEs under ``root/kind`` that are not in the plan."""
    keep = {root / e.out_rel for e in built} | {root / e.yaml_rel for e in built}
    base = root / kind
    if not base.exists():
        return
    for p in base.rglob("*"):
        if (
            p.is_file()
            and (p.suffix in COMPONENT_SUFFIXES or p.name.endswith(".tmp"))
            and p not in keep
        ):
            p.unlink()
    for d in sorted((p for p in base.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
        if not any(x for x in d.iterdir() if x.name != "README.md"):
            for x in d.iterdir():
                x.unlink()
            d.rmdir()


def _compact_state(root: Path, kind: str, entries: list[Entry], state: dict[str, Any]) -> None:
    names = {e.name for e in entries}
    lines = [json.dumps(state[n]) for n in sorted(names) if n in state]
    _state_path(root, kind).write_text("\n".join(lines) + "\n", encoding="utf-8")


def entry_record(e: Entry) -> dict[str, Any]:
    return asdict(e)
