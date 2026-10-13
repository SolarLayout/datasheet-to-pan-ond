from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pytest
from pvlib.iotools import read_panond

from datasheet_to_pan_ond.inverter_fit import fit_inverter
from datasheet_to_pan_ond.models import InverterDatasheet, ModuleDatasheet
from datasheet_to_pan_ond.module_fit import fit_module
from datasheet_to_pan_ond.writers import FileFormat, build_pan, write_ond, write_pan
from datasheet_to_pan_ond.writers.pvsyst_format import render, sanitize

NOW = dt.datetime(2026, 1, 2, 3, 4)


@pytest.fixture
def pan_path(tmp_path: Path, module_ds: ModuleDatasheet) -> Path:
    return write_pan(fit_module(module_ds), tmp_path / "m.PAN", now=NOW)


@pytest.fixture
def ond_path(tmp_path: Path, inverter_ds: InverterDatasheet) -> Path:
    return write_ond(fit_inverter(inverter_ds), tmp_path / "i.OND", now=NOW)


def _keys(d: dict[str, Any]) -> set[str]:
    return set(d.keys())


def test_pan_bytes(pan_path: Path) -> None:
    raw = pan_path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbfPVObject_=pvModule\r\n")
    assert raw.endswith(b"End of PVObject pvModule\r\n")
    assert b"\n" not in raw.replace(b"\r\n", b"")


def test_pan_roundtrip(pan_path: Path, module_ds: ModuleDatasheet) -> None:
    res = fit_module(module_ds)
    d = read_panond(pan_path, encoding="utf-8-sig")["PVObject_"]
    assert d["PVObject_"] == "pvModule"
    assert d["NCelS"] == module_ds.cells_in_series
    assert d["Isc"] == pytest.approx(module_ds.isc)
    assert d["Vmp"] == pytest.approx(module_ds.vmp)
    assert d["RSerie"] == pytest.approx(res.params.R_s, abs=1e-4)
    assert d["Gamma"] == pytest.approx(res.params.gamma_ref, abs=1e-4)
    assert d["muGamma"] == pytest.approx(res.params.mu_gamma, abs=1e-6)
    assert d["muISC"] == pytest.approx(module_ds.isc * module_ds.temp_coeff_isc_pct * 10, abs=0.01)
    assert d["PVObject_Commercial"]["Model"] == module_ds.model
    iam = d["PVObject_IAM"]["IAMProfile"]
    assert iam["NPtsEff"] == len(module_ds.iam_profile or [])


def test_pan_structure_matches_reference(pan_path: Path, ref_pan: dict[str, Any]) -> None:
    ours = read_panond(pan_path, encoding="utf-8-sig")["PVObject_"]
    optional = {"IMaxDiode"}  # present in reference, no datasheet basis
    missing = _keys(ref_pan) - _keys(ours) - optional
    assert not missing, f"keys in PVsyst reference but not written: {missing}"
    extra = _keys(ours) - _keys(ref_pan)
    assert not extra, f"keys written that PVsyst does not use: {extra}"
    ref_c, our_c = ref_pan["PVObject_Commercial"], ours["PVObject_Commercial"]
    assert _keys(ref_c) <= _keys(our_c)


def test_pan_key_order_matches_reference(pan_path: Path, ref_pan: dict[str, Any]) -> None:
    ours = list(read_panond(pan_path, encoding="utf-8-sig")["PVObject_"].keys())
    common = [k for k in ref_pan if k in ours]
    assert [k for k in ours if k in common] == common


def test_ond_roundtrip(ond_path: Path, inverter_ds: InverterDatasheet) -> None:
    d = read_panond(ond_path, encoding="utf-8-sig")["PVObject_"]
    conv = d["Converter"]
    assert conv["PNomConv"] == pytest.approx(inverter_ds.p_nom_ac_kw)
    assert conv["EfficMax"] == pytest.approx(inverter_ds.eff_max_pct, abs=0.01)
    assert conv["EfficEuro"] == pytest.approx(inverter_ds.eff_euro_pct, abs=0.01)
    assert conv["ProfilPIO"]["NPtsMax"] == 11
    assert conv["ProfilPIO"]["NPtsEff"] == 9
    assert conv["ProfilPIO"]["Point_10"] == [0, 0]
    assert len(conv["VNomEff"]) == 4  # trailing comma, as PVsyst writes
    assert d["TanPhiMax"] == pytest.approx(0.75)
    assert d["NbMPPT"] == inverter_ds.n_mppt


#: Converter keys written only when the datasheet gives them, and absent from the reference export
#: (``docs/file-format.md``, "VmppNom").
OPTIONAL_CONVERTER = {"VmppNom"}


def test_ond_structure_matches_reference(ond_path: Path, ref_ond: dict[str, Any]) -> None:
    ours = read_panond(ond_path, encoding="utf-8-sig")["PVObject_"]
    optional_top = {"NbMSInterne", "MS_Thresh"}
    assert not (_keys(ref_ond) - _keys(ours) - optional_top)
    assert not (_keys(ours) - _keys(ref_ond))
    assert not (_keys(ref_ond["Converter"]) - _keys(ours["Converter"]))
    assert not (_keys(ours["Converter"]) - _keys(ref_ond["Converter"]) - OPTIONAL_CONVERTER)


def test_ond_without_nominal_mpp_voltage_has_no_vmppnom(ond_path: Path) -> None:
    assert "VmppNom" not in ond_path.read_bytes().decode("utf-8-sig")


def test_ond_nominal_mpp_voltage_sits_between_vmppmax_and_vabsmax(
    tmp_path: Path, inverter_ds: InverterDatasheet
) -> None:
    ds = inverter_ds.model_copy(update={"v_mpp_nom": 620.0})
    path = write_ond(fit_inverter(ds), tmp_path / "n.OND", now=NOW)
    lines = [ln.strip() for ln in path.read_bytes().decode("utf-8-sig").splitlines()]
    i = lines.index("VmppNom=620.0")
    assert lines[i - 1].startswith("VMPPMax=")
    assert lines[i + 1].startswith("VAbsMax=")
    converter = read_panond(path, encoding="utf-8-sig")["PVObject_"]["Converter"]
    assert converter["VmppNom"] == 620.0


def test_ond_quirky_keys_verbatim(ond_path: Path) -> None:
    text = ond_path.read_bytes().decode("utf-8-sig")
    assert "\r\n  IsolSurvey =Yes\r\n" in text
    assert "\r\n    PInEffMax =" in text
    assert text.splitlines()[1].count("\t") == 2  # tab-separated top comment


def test_lf_and_encoding_options(module_ds: ModuleDatasheet) -> None:
    text = render(build_pan(fit_module(module_ds), NOW), FileFormat(newline="\n"))
    assert "\r" not in text


@pytest.mark.parametrize(
    ("raw", "clean"),
    [("a = b", "a: b"), ("x=y", "x:y"), ("line1\nline2", "line1 line2"), ("  sp  ", "sp")],
)
def test_sanitize(raw: str, clean: str) -> None:
    assert sanitize(raw) == clean
