from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pvlib.iotools import read_panond

from datasheet_to_pan_ond.io import load_inverter, load_module
from datasheet_to_pan_ond.models import InverterDatasheet, ModuleDatasheet

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
REFERENCE = Path(__file__).parent / "data" / "reference"
REF_PAN = REFERENCE / "ET-M772BH550GL.PAN"
REF_OND = REFERENCE / "CPS SCH275KTL-DO-US-800-250kW_275kVA_1.OND"


@pytest.fixture(scope="session")
def ref_pan() -> dict[str, Any]:
    return read_panond(REF_PAN, encoding="utf-8-sig")["PVObject_"]


@pytest.fixture(scope="session")
def ref_ond() -> dict[str, Any]:
    return read_panond(REF_OND, encoding="utf-8-sig")["PVObject_"]


@pytest.fixture
def module_ds() -> ModuleDatasheet:
    return load_module(EXAMPLES / "module_example.yaml")


@pytest.fixture
def module_min_ds() -> ModuleDatasheet:
    return load_module(EXAMPLES / "module_minimal.yaml")


@pytest.fixture
def inverter_ds() -> InverterDatasheet:
    return load_inverter(EXAMPLES / "inverter_example.yaml")


@pytest.fixture
def inverter_min_ds() -> InverterDatasheet:
    return load_inverter(EXAMPLES / "inverter_minimal.yaml")


def module_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        manufacturer="Test",
        model="T-550",
        cells_in_series=72,
        cells_in_parallel=2,
        p_nom=550.0,
        isc=14.0,
        voc=49.9,
        imp=13.11,
        vmp=41.96,
        temp_coeff_isc_pct=0.052,
        temp_coeff_voc_pct=-0.2565,
        temp_coeff_pmax_pct=-0.34,
    )
    base.update(overrides)
    return base
