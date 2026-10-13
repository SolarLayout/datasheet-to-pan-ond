from __future__ import annotations

import pytest
from pydantic import ValidationError

from datasheet_to_pan_ond.models import InverterDatasheet, ModuleDatasheet
from tests.conftest import module_kwargs


def test_valid_module() -> None:
    ds = ModuleDatasheet(**module_kwargs())
    assert ds.technology.value == "mtSiMono"


@pytest.mark.parametrize(
    ("override", "msg"),
    [
        ({"imp": 14.5}, "imp"),
        ({"vmp": 50.0}, "vmp"),
        ({"p_nom": 500.0}, "differs from p_nom"),
        ({"temp_coeff_pmax_pct": 0.3}, "less than 0"),
        ({"unknown_field": 1}, "Extra inputs"),
    ],
)
def test_invalid_module(override: dict[str, object], msg: str) -> None:
    with pytest.raises(ValidationError, match=msg):
        ModuleDatasheet(**module_kwargs(**override))


def test_iam_must_increase() -> None:
    iam = [{"angle_deg": 50, "iam": 0.98}, {"angle_deg": 30, "iam": 1.0}]
    with pytest.raises(ValidationError, match="strictly increasing"):
        ModuleDatasheet(**module_kwargs(iam_profile=iam))


INV = dict(
    manufacturer="T",
    model="I",
    p_nom_ac_kw=100,
    v_ac=400,
    v_mpp_min=200,
    v_mpp_max=1000,
    v_dc_max=1100,
    i_max_dc=200,
    eff_max_pct=98.8,
    eff_euro_pct=98.5,
)


def test_valid_inverter() -> None:
    assert InverterDatasheet(**INV).p_max_ac == 100


def test_inverter_nominal_mpp_voltage_is_optional_and_kept() -> None:
    assert InverterDatasheet(**INV).v_mpp_nom is None
    assert InverterDatasheet(**{**INV, "v_mpp_nom": 600}).v_mpp_nom == 600


@pytest.mark.parametrize(
    ("override", "msg"),
    [
        ({"eff_euro_pct": None}, "at least one weighted"),
        ({"eff_euro_pct": 99.0}, "must be below eff_max"),
        ({"v_mpp_max": 1200}, "v_mpp_min < v_mpp_max <= v_dc_max"),
        ({"p_max_ac_kw": 90}, "p_max_ac_kw"),
        ({"v_mpp_nom": 150}, "v_mpp_nom must lie within"),
        ({"v_mpp_nom": 1050}, "v_mpp_nom must lie within"),
        (
            {"efficiency_by_voltage": [{"v_dc": 300, "eff_max_pct": 98, "eff_euro_pct": 97}]},
            "3 entries",
        ),
    ],
)
def test_invalid_inverter(override: dict[str, object], msg: str) -> None:
    with pytest.raises(ValidationError, match=msg):
        InverterDatasheet(**{**INV, **override})
