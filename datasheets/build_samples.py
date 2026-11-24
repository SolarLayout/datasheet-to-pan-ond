"""Write the sample modules and inverters SolarLayout Desktop bundles for draft layouts.

Usage::

    uv run python datasheets/build_samples.py     # write datasheets/samples/

``samples.yaml`` names, for each sample, the checked spec its values come from. A sample is
that spec with the maker taken out: the manufacturer is "Sample", the model says what it is
("630 Wp bifacial module", "320 kW string inverter"), and the remarks name no source, so a
customer never reads a maker's name on a module or an inverter the maker did not supply.
``samples/`` holds one PAN or OND file per sample and ``index.json`` (id, kind, file,
SHA-256), which the app copies as they are. They are output: change ``samples.yaml`` and run
this again, never edit them.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from datasheet_to_pan_ond.inverter_fit import InverterFitResult, fit_inverter
from datasheet_to_pan_ond.io import load_inverter, load_module
from datasheet_to_pan_ond.models import InverterDatasheet, ModuleDatasheet
from datasheet_to_pan_ond.module_fit import ModuleFitResult, fit_module
from datasheet_to_pan_ond.writers import write_ond, write_pan

HERE = Path(__file__).resolve().parent
OUT = HERE / "samples"
INDEX_VERSION = 1
KINDS = ("module", "string", "central")
# The date written into the files (their price date): fixed, so a rebuild is byte-identical.
WRITTEN = dt.datetime(2026, 11, 24)
DATA_SOURCE = "Typical values from published datasheets"


@dataclass(frozen=True)
class Sample:
    id: str
    kind: str  # "module", "string" or "central"
    spec: Path  # relative to datasheets/

    @property
    def file(self) -> str:
        return f"{self.id}.{'PAN' if self.kind == 'module' else 'OND'}"


def load_samples(root: Path = HERE) -> list[Sample]:
    data = yaml.safe_load((root / "samples.yaml").read_text(encoding="utf-8"))
    return [
        Sample(sid, kind, Path(spec))
        for kind in KINDS
        for sid, spec in (data.get(kind) or {}).items()
    ]


def sample_module(spec: ModuleDatasheet) -> ModuleDatasheet:
    face = "bifacial" if spec.bifaciality else "monofacial"
    return spec.model_copy(
        update={
            "manufacturer": "Sample",
            "model": f"{spec.p_nom:g} Wp {face} module",
            "data_source": DATA_SOURCE,
            "provenance_notes": [],
        }
    )


def sample_inverter(spec: InverterDatasheet, kind: str) -> InverterDatasheet:
    return spec.model_copy(
        update={
            "manufacturer": "Sample",
            "model": f"{spec.p_nom_ac_kw:,g} kW {kind} inverter",
            "data_source": DATA_SOURCE,
            "provenance_notes": [],
        }
    )


def fit_sample(sample: Sample, root: Path = HERE) -> ModuleFitResult | InverterFitResult:
    path = root / sample.spec
    if sample.kind == "module":
        return fit_module(sample_module(load_module(path)))
    return fit_inverter(sample_inverter(load_inverter(path), sample.kind))


def write_samples(out: Path = OUT, root: Path = HERE) -> None:
    """Every sample into *out* (emptied first), with its index; refuses a fit with warnings."""
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    index = []
    for sample in load_samples(root):
        result = fit_sample(sample, root)
        if result.warnings:
            raise SystemExit(f"{sample.id}: the fit has warnings: {result.warnings}")
        path = out / sample.file
        if isinstance(result, ModuleFitResult):
            write_pan(result, path, now=WRITTEN)
        else:
            write_ond(result, path, now=WRITTEN)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        index.append({"id": sample.id, "kind": sample.kind, "file": sample.file, "sha256": digest})
    body = {"version": INDEX_VERSION, "samples": index}
    (out / "index.json").write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    write_samples()
    print(f"Wrote {len(load_samples())} samples to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
