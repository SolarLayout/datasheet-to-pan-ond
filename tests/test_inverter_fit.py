from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from datasheet_to_pan_ond.inverter_fit import (
    InverterFitError,
    LossModel,
    fit_inverter,
    fit_loss_model,
)
from datasheet_to_pan_ond.models import InverterDatasheet


def test_closed_form_max_matches_numeric() -> None:
    m = LossModel(0.003, 0.002, 0.01)
    p = np.linspace(0.01, 1.5, 20001)
    assert m.eff_max == pytest.approx(float(np.max(m.efficiency(p))), abs=1e-8)
    assert m.p_out_at_eff_max == pytest.approx(float(p[np.argmax(m.efficiency(p))]), abs=1e-3)


@pytest.mark.parametrize(
    "targets",
    [
        (0.988, 0.985, None),
        (0.988, None, 0.986),
        (0.988, 0.985, 0.986),
        (0.978, 0.973, None),
        (0.9901, 0.9849, None),
    ],
)
def test_loss_model_hits_targets(targets: tuple[float, float | None, float | None]) -> None:
    eff_max, eff_euro, eff_cec = targets
    warns: list[str] = []
    m = fit_loss_model(eff_max, eff_euro, eff_cec, warns)
    assert m.eff_max == pytest.approx(eff_max, abs=2e-5)
    if eff_euro is not None:
        assert m.eff_euro == pytest.approx(eff_euro, abs=2e-5)
    if eff_cec is not None:
        assert m.eff_cec == pytest.approx(eff_cec, abs=2e-5)
    assert warns == []
    # Efficiency stays below 1 everywhere.
    assert np.all(m.efficiency(np.linspace(0.001, 1.5, 500)) < 1.0)


def test_two_figure_fit_peaks_near_half_load() -> None:
    m = fit_loss_model(0.988, 0.985)
    assert 0.3 < m.p_out_at_eff_max < 0.8


def test_reference_curve_shape(ref_ond: dict[str, Any]) -> None:
    """Fitting the reference OND's max/Euro figures reproduces its curve above 10 % load."""
    conv = ref_ond["Converter"]
    m = fit_loss_model(conv["EfficMax"] / 100, conv["EfficEuro"] / 100)
    prof = conv["ProfilPIO"]
    p_nom = conv["PNomConv"] * 1e3
    for i in range(1, prof["NPtsEff"] + 1):
        p_in, p_out = prof[f"Point_{i}"]
        if p_out / p_nom >= 0.1:
            assert 100 * m.efficiency(p_out / p_nom) == pytest.approx(100 * p_out / p_in, abs=0.5)


def test_inconsistent_figures_raise() -> None:
    with pytest.raises(InverterFitError):
        fit_loss_model(0.99, 0.90, 0.989)


def test_fit_inverter_example(inverter_ds: InverterDatasheet) -> None:
    res = fit_inverter(inverter_ds)
    assert res.by_voltage is not None and len(res.by_voltage) == 3
    # Profile: threshold point, 7 fractions, plus the PMax point.
    assert len(res.main.points_w) == 9
    assert res.main.points_w[0][1] == 0.0
    pins = [p for p, _ in res.main.points_w]
    assert pins == sorted(pins)
    assert res.p_nom_dc_kw > inverter_ds.p_nom_ac_kw
    for _, target, model in res.checks:
        if target is not None:
            assert model == pytest.approx(target, abs=2e-3)


def test_fit_inverter_minimal(inverter_min_ds: InverterDatasheet) -> None:
    res = fit_inverter(inverter_min_ds)
    assert res.by_voltage is None
    assert len(res.main.points_w) == 8  # no PMax > PNom point
    assert any("two efficiency figures" in a for a in res.assumptions)
