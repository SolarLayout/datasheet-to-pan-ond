from __future__ import annotations

import csv
import shutil
from pathlib import Path

import pytest

from datasheet_to_pan_ond.cli import main
from datasheet_to_pan_ond.library import index
from datasheet_to_pan_ond.library.build import build, inverter_class
from datasheet_to_pan_ond.library.verify import verify_library, verify_ond, verify_pan
from tests.conftest import REF_OND, REF_PAN
from tests.test_cec import INVERTERS, MODULES

DATE = "2026-01-02"


@pytest.fixture(scope="module")
def lib(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("lib")
    build("modules", root, source=MODULES, jobs=1, date=DATE)
    build("inverters", root, source=INVERTERS, jobs=1, date=DATE)
    return root


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@pytest.mark.parametrize(
    ("kw", "vdc", "cls"),
    [
        (0.3, 60, "micro"),
        (3.0, 100, "micro"),
        (7.6, 600, "string"),
        (100, 1000, "string-utility"),
        (499.9, 1500, "string-utility"),
        (500, 1500, "central"),
        (4200, 1500, "central"),
    ],
)
def test_inverter_class(kw: float, vdc: float, cls: str) -> None:
    assert inverter_class(kw, vdc) == cls


def test_layout(lib: Path) -> None:
    for rel in (
        "README.md",
        "SHA256SUMS",
        "modules/README.md",
        "modules/index.csv",
        "modules/excluded.csv",
        "inverters/README.md",
        "inverters/index.csv",
    ):
        assert (lib / rel).is_file(), rel
    mods = _rows(lib / "modules/index.csv")
    assert {r["technology"] for r in mods} == {"mono-c-si", "multi-c-si"}
    for r in mods:
        assert (lib / "modules" / r["pan_file"]).is_file()
        assert (lib / "modules" / r["yaml_file"]).is_file()
    invs = _rows(lib / "inverters/index.csv")
    assert len(invs) == 4
    assert {r["class"] for r in invs} == {"string"}


def test_exclusions_have_reasons(lib: Path) -> None:
    exc = _rows(lib / "modules/excluded.csv")
    stages = {r["cec_name"]: r["stage"] for r in exc}
    assert stages["Advanced Solar Power (Hangzhou) ASP-S1-80"] == "conversion"
    assert stages["Boviet Solar Technology Co Ltd BVM7612M-590-H-HC-BF-DG"] == "conversion"
    assert all(r["reason"] for r in exc)


def test_review_flag_in_index(lib: Path) -> None:
    mods = {r["model"]: r for r in _rows(lib / "modules/index.csv")}
    assert mods["TSM-295PE06H"]["needs_review"] == "yes"
    assert mods["6MN6A270"]["needs_review"] == ""


def test_verify_passes_and_writes_report(lib: Path) -> None:
    rep = verify_library(lib, jobs=1)
    assert rep.ok, rep.problems
    assert rep.checked == {"modules": 4, "inverters": 4}
    assert "PASSED" in (lib / index.VERIFICATION_NAME).read_text()


def test_build_is_deterministic_and_resumable(lib: Path, tmp_path: Path) -> None:
    other = tmp_path / "lib2"
    build("modules", other, source=MODULES, jobs=1, date=DATE)  # same order as the fixture
    res = build("inverters", other, source=INVERTERS, jobs=1, date=DATE)
    assert res.processed_now == 4
    again = build("inverters", other, source=INVERTERS, jobs=1, date=DATE)
    assert again.processed_now == 0 and again.complete
    for p in [*(other / "inverters").rglob("*.OND"), *(other / "modules").rglob("*.PAN")]:
        assert p.read_bytes() == (lib / p.relative_to(other)).read_bytes()


def test_time_budget_leaves_build_incomplete(tmp_path: Path) -> None:
    res = build("inverters", tmp_path, source=INVERTERS, jobs=1, date=DATE, time_budget=0)
    assert not res.complete and res.pending == 4
    assert not (tmp_path / "inverters" / "README.md").exists()
    done = build("inverters", tmp_path, source=INVERTERS, jobs=1, date=DATE)
    assert done.complete and len(done.built) == 4


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda p: p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n")), "bare LF"),
        (lambda p: p.write_bytes(p.read_bytes() + b"x"), "checksum mismatch"),
        (lambda p: p.unlink(), "missing"),
    ],
)
def test_verify_detects_tampering(lib: Path, tmp_path: Path, mutate, expected: str) -> None:  # type: ignore[no-untyped-def]
    copy = tmp_path / "copy"
    shutil.copytree(lib, copy)
    target = next((copy / "inverters").rglob("*.OND"))
    mutate(target)
    rep = verify_library(copy, jobs=1, write_report=False)
    assert not rep.ok
    assert any(expected in m for _, m in rep.problems)


def test_verify_detects_stray_and_unlinked_files(lib: Path, tmp_path: Path) -> None:
    copy = tmp_path / "copy"
    shutil.copytree(lib, copy)
    folder = next((copy / "inverters").rglob("*.OND")).parent
    (folder / "notes.txt").write_text("junk")
    shutil.copy(next(folder.glob("*.OND")), folder / "extra.OND")
    rep = verify_library(copy, jobs=1, write_report=False)
    msgs = {m for _, m in rep.problems}
    assert "unexpected file" in msgs
    assert "file not in index.csv" in msgs
    assert "not linked from folder README" in msgs


def test_value_checks_catch_bad_physics(lib: Path, tmp_path: Path) -> None:
    pan = next((lib / "modules").rglob("*.PAN"))
    bad = tmp_path / "bad.PAN"
    text = pan.read_bytes().decode("utf-8-sig")
    rs = next(line for line in text.splitlines() if line.strip().startswith("RSerie="))
    bad.write_bytes(b"\xef\xbb\xbf" + text.replace(rs, "  RSerie=0.0100").encode())
    assert any("not on curve" in e or "Pmax" in e for e in verify_pan(bad))

    ond = next((lib / "inverters").rglob("*.OND"))
    bad_o = tmp_path / "bad.OND"
    t = ond.read_bytes().decode("utf-8-sig")
    eff = next(line for line in t.splitlines() if line.strip().startswith("EfficMax="))
    bad_o.write_bytes(b"\xef\xbb\xbf" + t.replace(eff, "    EfficMax=99.90").encode())
    assert any("EfficMax" in e for e in verify_ond(bad_o))


def test_reference_pvsyst_files_pass_value_checks() -> None:
    """Real PVsyst exports pass the physics/consistency checks, with two known exceptions.

    Byte-format rules (BOM, CRLF) are ours, not PVsyst's, and are ignored here.
    """
    byte_rules = ("BOM", "LF", "footer")
    pan = [e for e in verify_pan(REF_PAN) if not any(r in e for r in byte_rules)]
    # The manufacturer did not tune muGamma to muPmpReq (PVsyst allows that).
    assert pan == ["model Pmax temp. coeff -0.294 != -0.34"]
    ond = [e for e in verify_ond(REF_OND) if not any(r in e for r in byte_rules)]
    # PVsyst does not tie PSeuil to the first profile point; our writer does.
    assert ond == ["PSeuil differs from profile threshold"]


def test_cli_library(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "l"
    assert (
        main(
            [
                "library",
                "build",
                str(root),
                "--kind",
                "inverters",
                "--source",
                str(INVERTERS),
                "--jobs",
                "1",
                "--date",
                DATE,
            ]
        )
        == 0
    )
    assert main(["library", "verify", str(root), "--jobs", "1"]) == 0
    assert "PASSED" in capsys.readouterr().out


def test_spec_match_tolerates_last_digit_rounding(tmp_path: Path) -> None:
    """Vmp 43.985 is written as 43.98 (2 decimals); that must not fail verification."""
    from datasheet_to_pan_ond.models import ModuleDatasheet
    from datasheet_to_pan_ond.module_fit import fit_module
    from datasheet_to_pan_ond.writers import write_pan
    from tests.conftest import module_kwargs

    ds = ModuleDatasheet(**module_kwargs(vmp=43.985, imp=12.505, p_nom=550.0, voc=52.0))
    path = write_pan(fit_module(ds), tmp_path / "m.PAN")
    assert not [e for e in verify_pan(path, ds) if "differs from spec" in e]
