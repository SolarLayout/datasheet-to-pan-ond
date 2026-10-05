"""Compare committed library files with the manufacturer's own PVsyst files.

Two products in the library also exist as real PVsyst exports in
``tests/data/reference``: the ET Solar ET-M772BH550GL module (listed in CEC
under "ELITE Solar") and the CPS SCH275KTL-DO/US-800 inverter. The expected
agreement and the known, explained differences are documented in
``docs/validation.md``.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from pvlib import pvsystem
from pvlib.iotools import read_panond

from tests.conftest import REF_OND, REF_PAN

LIB = Path(__file__).resolve().parents[1] / "library"
OUR_PAN = LIB / "modules/mono-c-si/ELITE_Solar/ET-M772BH550GL.PAN"
OUR_OND = (
    LIB / "inverters/string-utility/Chint_Power_Systems_America/CPS_SCH275KTL-DO_US-800_800V.OND"
)
pytestmark = pytest.mark.skipif(
    not (OUR_PAN.exists() and OUR_OND.exists()), reason="library not built"
)


def _read(p: Path) -> dict[str, Any]:
    return read_panond(p, encoding="utf-8-sig")["PVObject_"]


def _pmp(d: dict[str, Any], g: float, t: float, mu_gamma: float | None = None) -> float:
    ns, rs, rsh, isc, voc = d["NCelS"], d["RSerie"], d["RShunt"], d["Isc"], d["Voc"]
    a = d["Gamma"] * ns * 1.380649e-23 * 298.15 / 1.602176634e-19
    io = (isc * (1 + rs / rsh) - voc / rsh) / (math.exp(voc / a) - math.exp(isc * rs / a))
    il = io * math.expm1(voc / a) + voc / rsh
    mu = d["muGamma"] if mu_gamma is None else mu_gamma
    params = pvsystem.calcparams_pvsyst(
        g, t, d["muISC"] / 1000, d["Gamma"], mu, il, io, rsh, d["Rp_0"], rs, ns, d["Rp_Exp"]
    )
    return float(pvsystem.singlediode(*params)["p_mp"])


@pytest.mark.parametrize(("g", "t"), [(1000, 25), (400, 30), (200, 25), (100, 20)])
def test_module_matches_manufacturer_at_25c_and_low_light(g: float, t: float) -> None:
    ref, ours = _read(REF_PAN), _read(OUR_PAN)
    assert _pmp(ours, g, t) / _pmp(ref, g, t) - 1 == pytest.approx(0, abs=0.003)


def test_module_temperature_difference_is_explained() -> None:
    """At 65 C our file is ~2 % lower, because the manufacturer's file does not
    honour its own stated Pmax coefficient (-0.34 %/K; its model gives ~-0.29 %/K)
    while ours does. With the same thermal behaviour the files agree within 0.3 %.
    """
    ref, ours = _read(REF_PAN), _read(OUR_PAN)
    raw = _pmp(ours, 1000, 65) / _pmp(ref, 1000, 65) - 1
    assert -0.03 < raw < -0.01
    tc_ours = (_pmp(ours, 1000, 26) - _pmp(ours, 1000, 24)) / 2 / _pmp(ours, 1000, 25)
    assert 100 * tc_ours == pytest.approx(ref["muPmpReq"], abs=0.01)


def _eff(c: dict[str, Any], key: str, p_out: np.ndarray) -> np.ndarray:
    prof = c[key]
    a = np.array([prof[f"Point_{i}"] for i in range(2, prof["NPtsEff"] + 1)], float)
    return 100 * np.interp(p_out, a[:, 1], a[:, 1] / a[:, 0])


@pytest.mark.parametrize("key", ["ProfilPIOV1", "ProfilPIOV2", "ProfilPIOV3"])
def test_inverter_efficiency_close_to_manufacturer(key: str) -> None:
    ref, ours = _read(REF_OND)["Converter"], _read(OUR_OND)["Converter"]
    p_out = np.array([0.2, 0.3, 0.5, 0.75, 1.0]) * 250_000
    diff = _eff(ours, key, p_out) - _eff(ref, key, p_out)
    assert np.max(np.abs(diff)) < 0.45  # percentage points


def test_inverter_rating_differences_are_definitional() -> None:
    """CEC Paco (275 kW) is the max continuous AC power; the manufacturer file uses
    250 kW nominal / 275 kVA. CEC MPPT limits are the full-power window
    (900-1300 V); the manufacturer file states the operating window (500-1500 V).
    """
    ref, ours = _read(REF_OND)["Converter"], _read(OUR_OND)["Converter"]
    assert (ref["PNomConv"], ours["PNomConv"]) == (250.0, 275.0)
    assert ref["VMppMin"] < ours["VMppMin"] and ours["VMPPMax"] < ref["VMPPMax"]
