from __future__ import annotations

from typing import Any

import pytest

from datasheet_to_pan_ond.models import ModuleDatasheet
from datasheet_to_pan_ond.module_fit import (
    DEFAULT_RSH_FACTOR,
    PVsystSDMParams,
    fit_module,
    solve_stc,
)
from tests.conftest import module_kwargs


def _ref_datasheet(ref: dict[str, Any], **fit: Any) -> ModuleDatasheet:
    """Datasheet values as stated in the reference PVsyst PAN file."""
    return ModuleDatasheet(
        manufacturer="ref",
        model="ref",
        cells_in_series=ref["NCelS"],
        cells_in_parallel=ref["NCelP"],
        p_nom=ref["PNom"],
        isc=ref["Isc"],
        voc=ref["Voc"],
        imp=ref["Imp"],
        vmp=ref["Vmp"],
        temp_coeff_isc_pct=ref["muISC"] / 1000 / ref["Isc"] * 100,
        temp_coeff_voc_pct=ref["muVocSpec"] / 1000 / ref["Voc"] * 100,
        temp_coeff_pmax_pct=ref["muPmpReq"],
        fit={"r_sh_ref": ref["RShunt"], "r_sh_0_ratio": ref["Rp_0"] / ref["RShunt"], **fit},
    )


def _ref_params(ref: dict[str, Any], ds: ModuleDatasheet) -> PVsystSDMParams:
    il, io, gamma = solve_stc(ds, ref["RShunt"], ref["RSerie"])
    return PVsystSDMParams(
        I_L_ref=il,
        I_o_ref=io,
        gamma_ref=gamma,
        mu_gamma=ref["muGamma"],
        R_s=ref["RSerie"],
        R_sh_ref=ref["RShunt"],
        R_sh_0=ref["Rp_0"],
        R_sh_exp=ref["Rp_Exp"],
        cells_in_series=ref["NCelS"],
        alpha_sc=ref["muISC"] / 1000,
        EgRef=1.121,
    )


def test_stc_solve_reproduces_pvsyst_gamma(ref_pan: dict[str, Any]) -> None:
    """Given PVsyst's own RShunt/RSerie, we recover PVsyst's Gamma."""
    ds = _ref_datasheet(ref_pan)
    _, _, gamma = solve_stc(ds, ref_pan["RShunt"], ref_pan["RSerie"])
    assert gamma == pytest.approx(ref_pan["Gamma"], abs=0.002)


def test_low_light_fit_recovers_pvsyst_rserie(ref_pan: dict[str, Any]) -> None:
    """Fitting RSerie to the reference model's low-light efficiency gives PVsyst's RSerie."""
    ds0 = _ref_datasheet(ref_pan)
    target = _ref_params(ref_pan, ds0).low_light_rel_eff()
    ds = ds0.model_copy(update={"low_light_rel_eff_pct": 100 * target})
    res = fit_module(ds)
    assert res.params.R_s == pytest.approx(ref_pan["RSerie"], abs=1e-4)
    assert res.params.gamma_ref == pytest.approx(ref_pan["Gamma"], abs=0.002)


def test_fit_matches_datasheet_targets(module_ds: ModuleDatasheet) -> None:
    res = fit_module(module_ds)
    checks = {c.name: c for c in res.checks}
    assert checks["Isc @STC"].error == pytest.approx(0, abs=1e-6)
    assert checks["Voc @STC"].error == pytest.approx(0, abs=1e-6)
    assert checks["muPmp"].error == pytest.approx(0, abs=1e-6)
    assert checks["rel. eff @200W/m2"].error == pytest.approx(0, abs=1e-6)
    # Datasheet MPP lies on the curve; model Pmax within 0.5 % of Vmp*Imp.
    assert abs(checks["Pmp @STC"].error) / checks["Pmp @STC"].target < 0.005  # type: ignore[operator]
    assert res.warnings == []


def test_mpp_point_on_curve(module_ds: ModuleDatasheet) -> None:
    from pvlib import pvsystem

    p = fit_module(module_ds).params
    il, io, rs, rsh, nnsvth = pvsystem.calcparams_pvsyst(
        1000,
        25,
        p.alpha_sc,
        p.gamma_ref,
        p.mu_gamma,
        p.I_L_ref,
        p.I_o_ref,
        p.R_sh_ref,
        p.R_sh_0,
        p.R_s,
        p.cells_in_series,
        p.R_sh_exp,
        p.EgRef,
    )
    i_at_vmp = pvsystem.i_from_v(module_ds.vmp, il, io, rs, rsh, nnsvth)
    assert float(i_at_vmp) == pytest.approx(module_ds.imp, abs=1e-6)


def test_default_assumptions_are_reported(module_min_ds: ModuleDatasheet) -> None:
    res = fit_module(module_min_ds)
    expected_rsh = DEFAULT_RSH_FACTOR * module_min_ds.vmp / module_min_ds.imp
    assert res.params.R_sh_ref == pytest.approx(expected_rsh)
    assert any("heuristic" in a for a in res.assumptions)
    assert any("default; not on datasheet" in a for a in res.assumptions)


def test_user_fixed_rs() -> None:
    ds = ModuleDatasheet(**module_kwargs(fit={"r_s": 0.15}, low_light_rel_eff_pct=-3.0))
    res = fit_module(ds)
    assert res.params.R_s == 0.15
    assert any("ignored" in w for w in res.warnings)


@pytest.mark.parametrize("target", [-1.5, -2.5, -4.0])
def test_low_light_targets(target: float) -> None:
    res = fit_module(ModuleDatasheet(**module_kwargs(low_light_rel_eff_pct=target)))
    assert 100 * res.params.low_light_rel_eff() == pytest.approx(target, abs=1e-6)
    assert 0.7 < res.params.gamma_ref < 2.0


def test_unrealistic_low_light_warns() -> None:
    """A +4 % low-light gain is only reachable with a non-physical diode factor."""
    res = fit_module(ModuleDatasheet(**module_kwargs(low_light_rel_eff_pct=4.0)))
    assert any("Gamma" in w for w in res.warnings)
    assert any("Model Pmax deviates" in w for w in res.warnings)


def test_higher_rs_means_better_low_light() -> None:
    ds = ModuleDatasheet(**module_kwargs())
    from datasheet_to_pan_ond.module_fit import build_params

    ll = [build_params(ds, 320.0, rs).low_light_rel_eff() for rs in (0.10, 0.15, 0.20)]
    assert ll == sorted(ll)
