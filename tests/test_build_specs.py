"""``datasheets/build_specs.py``: the specs it writes from a maker's ``sheets.yaml``, and the
check that refuses a transcription its PDF does not bear out."""

from __future__ import annotations

import copy
import hashlib
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

DATASHEETS = Path(__file__).resolve().parents[1] / "datasheets"
sys.path.insert(0, str(DATASHEETS))
import build_specs as bs  # noqa: E402

MAKERS = sorted(p.parent for p in DATASHEETS.glob("*/sheets.yaml"))


@pytest.mark.parametrize("maker", MAKERS, ids=lambda p: p.name)
def test_committed_specs_are_the_scripts_output(maker: Path, tmp_path: Path) -> None:
    copy_dir = tmp_path / maker.name
    copy_dir.mkdir()
    shutil.copy(maker / "sheets.yaml", copy_dir / "sheets.yaml")
    (built,) = bs.load_makers(tmp_path)
    bs.write_maker(built)
    for sub in ("specs", "inverter-specs"):
        made = sorted(p.name for p in (copy_dir / sub).glob("*.yaml"))
        committed = sorted(p.name for p in (maker / sub).glob("*.yaml"))
        assert made == committed, sub
        for name in made:
            assert (copy_dir / sub / name).read_text() == (maker / sub / name).read_text(), name
    assert (copy_dir / "sources.csv").read_text() == (maker / "sources.csv").read_text()


# ── the check, on a sheet of our own ─────────────────────────────────────────

TEXT = """\
   Peak power, Pmax(Wp)            550     555     560
   Maximum voltage, Vmpp (V)     42.84   43.05   43.22        Tc of Voc  -0.24% /°C
   Maximum current, Impp (A)     12.86   12.91   12.97        Tc of Isc  0.028% /°C
   Open circuit voltage, Voc (V)  51.3    51.5    51.7        Tc of Pmax -0.32% /°C
   Short circuit current, Isc (A) 13.59   13.65   13.71       Maximum system voltage 1500 VDC
   144 half-cut cells   2278 x 1133 x 30 mm   31.3 kg   Bifaciality 80 ± 5 %
"""


def _sheet(pdf: Path) -> dict[str, Any]:
    return {
        "pdf": pdf.name,
        "sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
        "url": "https://example.com/sheet.pdf",
        "document": "EX-01",
        "technology": "TOPCon",
        "model": "EX-{p}",
        "cells": 144,
        "half_cell": True,
        "bins": {
            "p_nom": [550, 555, 560],
            "vmp": [42.84, 43.05, 43.22],
            "imp": [12.86, 12.91, 12.97],
            "voc": [51.3, 51.5, 51.7],
            "isc": [13.59, 13.65, 13.71],
        },
        "temp_coeff_pmax_pct": -0.32,
        "temp_coeff_voc_pct": -0.24,
        "temp_coeff_isc_pct": 0.028,
        "v_max_iec": 1500,
        "bifaciality_pct": 80,
        "size_mm": [2278, 1133, 30],
        "weight_kg": 31.3,
    }


@pytest.fixture
def pdf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "sheet.pdf"
    path.write_bytes(b"%PDF a stand-in")
    monkeypatch.setattr(bs, "pdf_text", lambda _p: TEXT)
    return path


def _check(sheet: dict[str, Any], pdf: Path) -> list[str]:
    return bs.check_sheet(sheet, "module", pdf.parent)


def test_a_faithful_transcription_checks(pdf: Path) -> None:
    assert _check(_sheet(pdf), pdf) == []


@pytest.mark.parametrize(
    "mistake",
    [
        lambda s: s["bins"]["vmp"].__setitem__(1, 43.15),  # a typo
        lambda s: s["bins"].update(imp=[12.91, 12.97, 13.03]),  # a column shifted a bin
        lambda s: s["bins"].update(voc=s["bins"]["isc"], isc=s["bins"]["voc"]),  # columns swapped
        lambda s: s.update(temp_coeff_voc_pct=-0.25),
        lambda s: s.update(weight_kg=32.3),
        lambda s: s.update(size_mm=[2278, 1134, 30]),
        lambda s: s.update(sha256="0" * 64),  # another revision of the file
    ],
    ids=["typo", "shifted", "swapped", "coefficient", "weight", "size", "revision"],
)
def test_a_mistake_is_refused(pdf: Path, mistake: Any) -> None:
    sheet = _sheet(pdf)
    mistake(sheet)
    assert _check(sheet, pdf)


def test_a_figure_read_by_eye_is_named_and_still_checked_for_sense(pdf: Path) -> None:
    sheet = _sheet(pdf)
    sheet["bins"]["vmp"][1] = 43.06  # not in the text, but read so from the page
    assert _check(sheet, pdf)
    sheet["read_by_eye"] = {"bins.vmp": "fused with a drawing dimension in the text"}
    assert _check(sheet, pdf) == []
    sheet["bins"]["vmp"][1] = 45.0  # Vmp x Imp far from 555 W
    assert _check(sheet, pdf)


def test_a_sheet_without_text_must_say_it_was_read_by_eye(
    pdf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bs, "pdf_text", lambda _p: "")
    sheet = _sheet(pdf)
    assert _check(sheet, pdf)
    sheet["unchecked"] = "image-only sheet"
    assert _check(sheet, pdf) == []


def test_a_row_split_over_two_lines_is_put_together_by_column() -> None:
    lines = [
        "Maximum Power Voltage - Vmp [V]          42.78        42.92",
        "",
        "                          42.57    42.70        42.85",
    ]
    rows = bs._split_rows(lines)
    assert any(bs._in_order([42.57, 42.70, 42.78, 42.85, 42.92], row) for row in rows)


def test_a_current_may_repeat_but_power_must_rise(pdf: Path) -> None:
    sheet = _sheet(pdf)
    sheet["unchecked"] = "testing the sense checks alone"
    sheet["bins"]["isc"] = [13.59, 13.65, 13.65]
    assert _check(sheet, pdf) == []
    sheet["bins"]["p_nom"] = [550, 555, 555]
    assert _check(sheet, pdf)


def test_quarter_cut_cells_and_a_missing_system_voltage(pdf: Path) -> None:
    sheet = copy.deepcopy(_sheet(pdf))
    sheet.update(cells=264, cells_in_parallel=4)
    del sheet["v_max_iec"]
    maker = bs.Maker(folder=pdf.parent, manufacturer="Example", prefix="Ex")
    spec = bs.module_specs(maker, sheet)[0]
    assert (spec["cells_in_series"], spec["cells_in_parallel"]) == (66, 4)
    assert "v_max_iec" not in spec
    assert any("does not state one" in n for n in spec["provenance_notes"])
