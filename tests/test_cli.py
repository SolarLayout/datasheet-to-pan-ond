from __future__ import annotations

import json
from pathlib import Path

import pytest

from datasheet_to_pan_ond.cli import main
from tests.conftest import EXAMPLES


@pytest.mark.parametrize(
    ("cmd", "spec", "suffix"),
    [
        ("module", "module_example.yaml", ".PAN"),
        ("module", "module_minimal.yaml", ".PAN"),
        ("inverter", "inverter_example.yaml", ".OND"),
        ("inverter", "inverter_minimal.yaml", ".OND"),
    ],
)
def test_cli_writes_output(tmp_path: Path, cmd: str, spec: str, suffix: str) -> None:
    out = tmp_path / f"x{suffix}"
    rep = tmp_path / "r.json"
    assert main([cmd, str(EXAMPLES / spec), "-o", str(out), "--report-json", str(rep)]) == 0
    assert out.exists()
    assert "assumptions" in json.loads(rep.read_text())


def test_cli_inspect(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    out = tmp_path / "x.PAN"
    main(["module", str(EXAMPLES / "module_minimal.yaml"), "-o", str(out)])
    capsys.readouterr()
    assert main(["inspect", str(out)]) == 0
    assert json.loads(capsys.readouterr().out)["PVObject_"]["NCelS"] == 60


def test_cli_invalid_spec(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("manufacturer: X\nmodel: Y\n")
    assert main(["module", str(bad)]) == 1
    assert "Invalid datasheet spec" in capsys.readouterr().err


def test_cli_strict_refuses_on_warning(tmp_path: Path) -> None:
    spec = tmp_path / "m.yaml"
    text = (EXAMPLES / "module_minimal.yaml").read_text() + "low_light_rel_eff_pct: 4.0\n"
    spec.write_text(text)
    out = tmp_path / "m.PAN"
    assert main(["module", str(spec), "-o", str(out), "--strict"]) == 2
    assert not out.exists()
