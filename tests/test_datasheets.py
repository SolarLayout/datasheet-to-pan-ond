"""The specs transcribed from real manufacturer datasheets (``datasheets/<maker>/``)."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from datasheet_to_pan_ond import fit_module
from datasheet_to_pan_ond.io import load_module

DATASHEETS = Path(__file__).resolve().parents[1] / "datasheets"
SPECS = sorted(DATASHEETS.glob("*/specs/*.yaml"))


def _top_bin_per_datasheet() -> list[Path]:
    """One spec per datasheet, its highest power: fitting all of them takes most of a minute."""
    top: dict[tuple[Path, str], tuple[float, Path]] = {}
    for spec in SPECS:
        ds = load_module(spec)
        key = (spec.parents[1], ds.data_source)
        if key not in top or ds.p_nom > top[key][0]:
            top[key] = (ds.p_nom, spec)
    return sorted(spec for _, spec in top.values())


def _documents(maker_dir: Path) -> set[str]:
    with (maker_dir / "sources.csv").open(encoding="utf-8", newline="") as f:
        return {row["document"] for row in csv.DictReader(f)}


def test_there_are_specs() -> None:
    assert SPECS, f"no specs under {DATASHEETS}"


@pytest.mark.parametrize("spec", _top_bin_per_datasheet(), ids=lambda p: f"{p.parts[-3]}/{p.stem}")
def test_spec_fits_without_warnings(spec: Path) -> None:
    result = fit_module(load_module(spec))
    assert result.warnings == []


@pytest.mark.parametrize("spec", SPECS, ids=lambda p: f"{p.parts[-3]}/{p.stem}")
def test_spec_names_a_listed_datasheet(spec: Path) -> None:
    ds = load_module(spec)
    documents = _documents(spec.parents[1])
    assert any(ds.data_source.endswith(doc) for doc in documents), ds.data_source
