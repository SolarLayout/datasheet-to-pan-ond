"""``datasheets/build_samples.py``: the sample files SolarLayout Desktop bundles, named by
what they are and never by the maker whose datasheet gave their values."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
import yaml

DATASHEETS = Path(__file__).resolve().parents[1] / "datasheets"
sys.path.insert(0, str(DATASHEETS))
import build_samples as bsm  # noqa: E402

SAMPLES = bsm.load_samples()
COMMITTED = DATASHEETS / "samples"


def _kv(text: str) -> dict[str, str]:
    """The file's first value for each key, as SolarLayout Desktop reads it."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key not in out:
            out[key] = value.strip()
    return out


def _text(sample: bsm.Sample) -> str:
    return (COMMITTED / sample.file).read_text(encoding="utf-8")


def test_committed_samples_are_the_scripts_output(tmp_path: Path) -> None:
    bsm.write_samples(tmp_path)
    made = sorted(p.name for p in tmp_path.iterdir())
    assert made == sorted(p.name for p in COMMITTED.iterdir())
    for name in made:
        assert (tmp_path / name).read_bytes() == (COMMITTED / name).read_bytes(), name


def test_the_index() -> None:
    index = json.loads((COMMITTED / "index.json").read_text())
    assert index["version"] == 1
    assert [s["id"] for s in index["samples"]] == [s.id for s in SAMPLES]
    assert [s["kind"] for s in index["samples"]].count("module") == 8
    assert [s["kind"] for s in index["samples"]].count("string") == 4
    assert [s["kind"] for s in index["samples"]].count("central") == 5


def _source_words() -> set[str]:
    words: set[str] = set()
    for sheets in DATASHEETS.glob("*/sheets.yaml"):
        words.add(yaml.safe_load(sheets.read_text())["manufacturer"])
    for s in SAMPLES:
        words.add(yaml.safe_load((DATASHEETS / s.spec).read_text())["model"])
    return words


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda s: s.id)
def test_no_maker_model_or_url_in_a_sample(sample: bsm.Sample) -> None:
    text = _text(sample).lower()
    for word in _source_words() | {"http"}:
        assert not re.search(rf"\b{re.escape(word.lower())}\b", text), word


MODULE_KEYS = {"PNom": "p_nom", "Isc": "isc", "Voc": "voc", "Imp": "imp", "Vmp": "vmp"}
INVERTER_KEYS = {
    "PNomConv": "p_nom_ac_kw",
    "VMppMin": "v_mpp_min",
    "VMPPMax": "v_mpp_max",
    "VAbsMax": "v_dc_max",
    "NbMPPT": "n_mppt",
    "EfficEuro": "eff_euro_pct",
}


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda s: s.id)
def test_a_sample_keeps_its_specs_figures(sample: bsm.Sample) -> None:
    kv = _kv(_text(sample))
    spec = yaml.safe_load((DATASHEETS / sample.spec).read_text())
    if sample.kind == "module":
        for key, field in MODULE_KEYS.items():
            assert float(kv[key]) == pytest.approx(spec[field], abs=1e-3), key
        assert float(kv["Width"]) == pytest.approx(spec["dimensions"]["width_m"])
        assert float(kv["Height"]) == pytest.approx(spec["dimensions"]["height_m"])
        assert float(kv["BifacialityFactor"]) == pytest.approx(spec["bifaciality"])
    else:
        for key, field in INVERTER_KEYS.items():
            assert float(kv[key]) == pytest.approx(spec[field], abs=1e-3), key


def test_names() -> None:
    names = {s.id: _kv(_text(s)) for s in SAMPLES}
    assert {kv["Manufacturer"] for kv in names.values()} == {"Sample"}
    assert names["module-630"]["Model"] == "630 Wp bifacial module"
    assert names["string-320"]["Model"] == "320 kW string inverter"
    assert names["central-3300"]["Model"] == "3,300 kW central inverter"


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda s: s.id)
def test_every_sample_fits_strictly(sample: bsm.Sample) -> None:
    assert bsm.fit_sample(sample).warnings == []
