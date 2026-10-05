from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pvlib import inverter as pvinv

from datasheet_to_pan_ond.inverter_fit import fit_inverter
from datasheet_to_pan_ond.models import InverterDatasheet, ModuleDatasheet
from datasheet_to_pan_ond.module_fit import fit_module
from datasheet_to_pan_ond.sources import cec

CEC_DIR = Path(__file__).parent / "data" / "cec"
MODULES = CEC_DIR / "CEC Modules subset.csv"
INVERTERS = CEC_DIR / "CEC Inverters subset.csv"


@pytest.fixture(scope="module")
def mods() -> pd.DataFrame:
    return cec.load("modules", MODULES)


@pytest.fixture(scope="module")
def invs() -> pd.DataFrame:
    return cec.load("inverters", INVERTERS)


def _row(df: pd.DataFrame, needle: str) -> pd.Series:
    return cec.search(df, [needle]).iloc[0]


def test_read_sam_csv_skips_unit_rows(mods: pd.DataFrame, invs: pd.DataFrame) -> None:
    assert len(mods) == 7 and len(invs) == 4
    assert mods["STC"].dtype.kind in "if"  # numeric, not str (unit row dropped)
    assert not any(c.startswith("Unnamed") for c in mods.columns)


def test_read_rejects_non_sam_csv(tmp_path: Path) -> None:
    p = tmp_path / "x.csv"
    p.write_text("a,b\n1,2\n3,4\n5,6\n")
    with pytest.raises(ValueError, match="SAM library"):
        cec.read_sam_csv(p)


def test_search_all_terms_case_insensitive(mods: pd.DataFrame) -> None:
    assert len(cec.search(mods, ["jinko", "640"])) == 1
    assert len(cec.search(mods, ["jinko", "999"])) == 0
    assert len(cec.search(mods, [r"^(?:Ablytek|Trina)"], regex=True)) == 2


def test_clean_module(mods: pd.DataFrame) -> None:
    conv = cec.module_row_to_spec(_row(mods, "Ablytek"), "2026.7.3")
    assert conv.ok and conv.spec is not None
    s = conv.spec
    assert (s["manufacturer"], s["model"]) == ("Ablytek", "6MN6A270")
    assert s["cells_in_series"] == 60 and s["cells_in_parallel"] == 1
    assert s["temp_coeff_isc_pct"] == pytest.approx(0.00486614 / 9.34 * 100, rel=1e-4)
    assert s["dimensions"] == {"height_m": 1.64, "width_m": 0.992}
    assert "2026.7.3" in s["data_source"]
    ModuleDatasheet.model_validate(s)


def test_cell_count_from_model_name(mods: pd.DataFrame) -> None:
    conv = cec.module_row_to_spec(_row(mods, "JKM640N"))
    assert conv.spec is not None and conv.spec["cells_in_series"] == 66
    assert any("model name" in n for n in conv.notes)
    assert any("Bifacial" in n for n in conv.notes)


def test_half_cell_total_count(mods: pd.DataFrame) -> None:
    conv = cec.module_row_to_spec(_row(mods, "E530HCBG144"))
    assert conv.spec is not None
    assert (conv.spec["cells_in_series"], conv.spec["cells_in_parallel"]) == (72, 2)
    assert conv.spec["half_cell"] is True


def test_statistical_cell_estimate_flags_review(mods: pd.DataFrame) -> None:
    conv = cec.module_row_to_spec(_row(mods, "TSM-295PE06H"))
    assert conv.spec is not None
    assert conv.spec["cells_in_series"] == 60  # 60-cell poly module (Voc 39.5 V)
    assert any("VERIFY" in n for n in conv.notes)


def test_thin_film_and_inconsistent_rows_are_skipped(mods: pd.DataFrame) -> None:
    thin = cec.module_row_to_spec(_row(mods, "ASP-S1-80"))
    assert not thin.ok and "ambiguous" in (thin.error or "")
    bad = cec.module_row_to_spec(_row(mods, "BVM7612M"))
    assert not bad.ok and "vmp * imp" in (bad.error or "")


def test_cdte_module_fits(mods: pd.DataFrame) -> None:
    conv = cec.module_row_to_spec(_row(mods, "FS-6385"))
    assert conv.spec is not None and conv.spec["technology"] == "mtCdTe"
    res = fit_module(ModuleDatasheet.model_validate(conv.spec))
    assert all(n in res.assumptions for n in conv.notes)


@pytest.mark.parametrize(
    ("voc", "a_ref", "tech", "expected"),
    [(39.5, 1.62, "mtSiPoly", 60), (49.0, 1.85, "mtSiMono", 72), (41.0, 1.52, "mtSiMono", 60)],
)
def test_estimate_cells(voc: float, a_ref: float, tech: str, expected: int) -> None:
    assert cec.estimate_cells_in_series(voc, a_ref, cec.Technology(tech)) == expected


def test_parse_inverter_name() -> None:
    assert cec.parse_inverter_name("SMA America: STP 20-US-50 {480V}") == (
        "SMA America",
        "STP 20-US-50",
        "480V",
    )
    assert cec.parse_inverter_name("Acme: X1") == ("Acme", "X1", None)


def test_sandia_efficiency_matches_pvlib(invs: pd.DataFrame) -> None:
    row = _row(invs, "STP 20")
    p = cec.SandiaParams(
        *(float(row[k]) for k in ("Paco", "Pdco", "Vdco", "Pso", "C0", "C1", "C2", "C3"))
    )
    params = row[["Paco", "Pdco", "Vdco", "Pso", "C0", "C1", "C2", "C3", "Pnt"]].astype(float)
    for v in (float(row["Mppt_low"]), float(row["Vdco"]), float(row["Mppt_high"])):
        for frac in (0.1, 0.5, 0.9):
            eff = float(p.efficiency(v, frac))
            p_dc = frac * p.Paco / eff
            p_ac = float(pvinv.sandia(np.array(v), np.array(p_dc), params))
            assert p_ac == pytest.approx(frac * p.Paco, rel=1e-9)


def test_inverter_conversion(invs: pd.DataFrame) -> None:
    conv = cec.inverter_row_to_spec(_row(invs, "STP 20"))
    assert conv.ok and conv.spec is not None
    s = conv.spec
    assert (s["manufacturer"], s["model"]) == ("SMA America", "STP 20-US-50 480V")
    assert s["phases"] == "three" and s["p_nom_ac_kw"] == 20.0
    assert len(s["efficiency_curve"]) == 7
    assert len(s["efficiency_by_voltage"]) == 3
    res = fit_inverter(InverterDatasheet.model_validate(s))
    assert res.main.measured and res.warnings == []
    assert res.p_threshold_w == pytest.approx(s["p_threshold_w"])
    # Written curve reproduces the Sandia model exactly at the profile points.
    for pt in s["efficiency_curve"]:
        assert 100 * res.main.efficiency(pt["p_out_frac"]) == pytest.approx(pt["eff_pct"], abs=1e-6)


def test_inverter_phase_and_missing_pnt(invs: pd.DataFrame) -> None:
    single = cec.inverter_row_to_spec(_row(invs, "SB70"))
    assert single.spec is not None and single.spec["phases"] == "single"
    nopnt = cec.inverter_row_to_spec(_row(invs, "Sanjing"))
    assert nopnt.ok and nopnt.spec is not None and nopnt.spec["night_loss_w"] == 0.0
    assert any("Pnt" in n for n in nopnt.notes)


def test_inverter_inconsistent_voltages_skipped(invs: pd.DataFrame) -> None:
    row = _row(invs, "STP 20").copy()
    row["Mppt_low"] = row["Mppt_high"]
    conv = cec.inverter_row_to_spec(row)
    assert not conv.ok and "inconsistent DC voltages" in (conv.error or "")


def test_cache_dir_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DS2PANOND_CACHE", str(tmp_path))
    assert cec.cache_dir() == tmp_path


def test_load_uses_cache_without_download(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DS2PANOND_CACHE", str(tmp_path))
    (tmp_path / cec.CACHE_FILES["inverters"]).write_bytes(INVERTERS.read_bytes())

    def boom(*a: object, **k: object) -> None:
        raise AssertionError("should not download")

    monkeypatch.setattr(cec.urllib.request, "urlopen", boom)
    assert len(cec.load("inverters")) == 4
