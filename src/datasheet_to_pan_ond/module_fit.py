"""Fit PVsyst one-diode model parameters to module datasheet values.

The PVsyst model (as implemented by ``pvlib.pvsystem.calcparams_pvsyst``)::

    I = IL - I0 * (exp((V + I*Rs) / (gamma*Ns*Vth)) - 1) - (V + I*Rs) / Rsh

with irradiance-dependent Rsh and temperature-dependent gamma.

The procedure mirrors PVsyst's own (and reproduces the RSerie/Gamma pairs
found in manufacturer PAN files):

1. ``RShunt`` is chosen (user value or heuristic); ``Rp_0``/``Rp_Exp`` follow.
2. For a given ``RSerie``, ``IL``, ``I0`` and ``gamma`` are solved so the
   curve passes exactly through Isc, Voc and the datasheet MPP point.
3. ``RSerie`` is the remaining degree of freedom. It mainly controls the
   low-irradiance efficiency, so it is chosen to match the relative
   efficiency at 200 W/m2 (datasheet value, or a technology default).
4. ``muGamma`` is fitted so the model's Pmax temperature coefficient matches
   the datasheet.

Because the datasheet MPP is only forced to lie *on* the curve (not to be its
maximum), the model Pmax is typically 0-0.2 % above Vmp*Imp, as in PVsyst.
See ``docs/methodology.md`` for details and limitations.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field, replace

import numpy as np
from pvlib import pvsystem
from pvlib.ivtools.sdm import pvsyst_temperature_coeff
from scipy.optimize import brentq

from datasheet_to_pan_ond.models import ModuleDatasheet, Technology

K_BOLTZMANN = 1.380649e-23  # J/K
Q_ELECTRON = 1.602176634e-19  # C
T_REF_K = 298.15
G_REF = 1000.0

#: Bandgap [eV] at reference conditions per technology, used for I0(T).
BANDGAP_EV: dict[Technology, float] = {
    Technology.SI_MONO: 1.121,
    Technology.SI_POLY: 1.121,
    Technology.HIT: 1.121,
    Technology.CDTE: 1.475,
    Technology.CIS: 1.010,
    Technology.A_SI_H: 1.700,
}

#: Heuristic default Rsh_ref = factor * Vmp / Imp. Calibrated so that a
#: typical 550 W c-Si module lands near the ~300 ohm PVsyst manufacturers use.
DEFAULT_RSH_FACTOR = 100.0

#: Relative efficiency at 200 W/m2 versus STC assumed when the datasheet is
#: silent. Typical of current manufacturer PAN files (e.g. -2.5 % for c-Si).
DEFAULT_LOW_LIGHT_REL_EFF: dict[Technology, float] = {
    Technology.SI_MONO: -0.025,
    Technology.SI_POLY: -0.030,
    Technology.HIT: -0.020,
    Technology.CDTE: -0.010,
    Technology.CIS: -0.030,
    Technology.A_SI_H: 0.000,
}


class FitError(RuntimeError):
    """Raised when the datasheet values cannot be reproduced by the model."""


@dataclass(frozen=True)
class PVsystSDMParams:
    """PVsyst one-diode parameters at reference conditions (pvlib naming)."""

    I_L_ref: float
    I_o_ref: float
    gamma_ref: float
    mu_gamma: float
    R_s: float
    R_sh_ref: float
    R_sh_0: float
    R_sh_exp: float
    cells_in_series: int
    alpha_sc: float  # A/K
    EgRef: float

    def iv_summary(self, irradiance: float = G_REF, temp_cell: float = 25.0) -> dict[str, float]:
        """Isc, Voc, Imp, Vmp, Pmp at the given conditions."""
        il, io, rs, rsh, nnsvth = pvsystem.calcparams_pvsyst(
            irradiance,
            temp_cell,
            alpha_sc=self.alpha_sc,
            gamma_ref=self.gamma_ref,
            mu_gamma=self.mu_gamma,
            I_L_ref=self.I_L_ref,
            I_o_ref=self.I_o_ref,
            R_sh_ref=self.R_sh_ref,
            R_sh_0=self.R_sh_0,
            R_s=self.R_s,
            cells_in_series=self.cells_in_series,
            R_sh_exp=self.R_sh_exp,
            EgRef=self.EgRef,
        )
        out = pvsystem.singlediode(il, io, rs, rsh, nnsvth)
        return {
            "isc": float(out["i_sc"]),
            "voc": float(out["v_oc"]),
            "imp": float(out["i_mp"]),
            "vmp": float(out["v_mp"]),
            "pmp": float(out["p_mp"]),
        }

    def temp_coeff_pmax(self) -> float:
        """Relative Pmax temperature coefficient at STC [1/K]."""
        return float(
            pvsyst_temperature_coeff(
                self.alpha_sc,
                self.gamma_ref,
                self.mu_gamma,
                self.I_L_ref,
                self.I_o_ref,
                self.R_sh_ref,
                self.R_sh_0,
                self.R_s,
                self.cells_in_series,
                R_sh_exp=self.R_sh_exp,
                EgRef=self.EgRef,
            )
        )

    def temp_coeff_voc(self, delta_t: float = 1.0) -> float:
        """Relative Voc temperature coefficient at STC [1/K] (central difference)."""
        hi = self.iv_summary(G_REF, 25.0 + delta_t)["voc"]
        lo = self.iv_summary(G_REF, 25.0 - delta_t)["voc"]
        ref = self.iv_summary(G_REF, 25.0)["voc"]
        return (hi - lo) / (2 * delta_t) / ref

    def low_light_rel_eff(self, irradiance: float = 200.0) -> float:
        """Efficiency at ``irradiance`` relative to STC, minus one [-]."""
        p_ref = self.iv_summary(G_REF, 25.0)["pmp"] / G_REF
        p_low = self.iv_summary(irradiance, 25.0)["pmp"] / irradiance
        return p_low / p_ref - 1.0


@dataclass
class CheckRow:
    """One line of the fit report: datasheet target versus model value."""

    name: str
    unit: str
    target: float | None
    model: float

    @property
    def error(self) -> float | None:
        return None if self.target is None else self.model - self.target


@dataclass
class ModuleFitResult:
    datasheet: ModuleDatasheet
    params: PVsystSDMParams
    checks: list[CheckRow]
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# STC solve
# --------------------------------------------------------------------------- #


def _vth_cell() -> float:
    return K_BOLTZMANN * T_REF_K / Q_ELECTRON


def _io_il(ds: ModuleDatasheet, gamma: float, rs: float, rsh: float) -> tuple[float, float]:
    """I0 and IL that satisfy the Isc and Voc conditions exactly."""
    a = gamma * ds.cells_in_series * _vth_cell()
    num = ds.isc * (1.0 + rs / rsh) - ds.voc / rsh
    den = math.exp(ds.voc / a) - math.exp(ds.isc * rs / a)
    io = num / den
    il = io * math.expm1(ds.voc / a) + ds.voc / rsh
    return io, il


def _mpp_current_residual(ds: ModuleDatasheet, gamma: float, rs: float, rsh: float) -> float:
    """Model current at Vmp minus Imp, normalised by Imp."""
    a = gamma * ds.cells_in_series * _vth_cell()
    io, il = _io_il(ds, gamma, rs, rsh)
    vd = ds.vmp + ds.imp * rs
    return (il - io * math.expm1(vd / a) - vd / rsh - ds.imp) / ds.imp


GAMMA_BOUNDS = (0.3, 3.0)


def solve_stc(ds: ModuleDatasheet, rsh: float, rs: float) -> tuple[float, float, float]:
    """Solve ``(I_L, I_o, gamma)`` at STC for given shunt and series resistances.

    The curve passes exactly through (0, Isc), (Voc, 0) and (Vmp, Imp).
    """
    gamma = _bracket_root(
        lambda g: _mpp_current_residual(ds, g, rs, rsh), *GAMMA_BOUNDS, f"gamma (Rs={rs:.4f})"
    )
    io, il = _io_il(ds, gamma, rs, rsh)
    if io <= 0 or il <= 0:
        raise FitError(f"Non-physical solution: I0={io:.3e}, IL={il:.3f}")
    return il, io, gamma


def _bracket_root(f: Callable[[float], float], lo: float, hi: float, what: str) -> float:
    grid = np.linspace(lo, hi, 271)
    vals = [_safe(f, x) for x in grid]
    for a, b, fa, fb in zip(grid[:-1], grid[1:], vals[:-1], vals[1:], strict=True):
        if np.isfinite(fa) and np.isfinite(fb) and fa * fb <= 0:
            return float(brentq(f, a, b, xtol=1e-14))
    raise FitError(f"Could not find a root for {what} in [{lo}, {hi}]")


def _safe(f: Callable[[float], float], x: float) -> float:
    try:
        return f(x)
    except (FitError, OverflowError, ZeroDivisionError):
        return math.nan


# --------------------------------------------------------------------------- #
# Full fit
# --------------------------------------------------------------------------- #


def build_params(
    ds: ModuleDatasheet, rsh: float, rs: float, mu_gamma: float = 0.0
) -> PVsystSDMParams:
    """Complete parameter set for given RShunt / RSerie (and muGamma)."""
    il, io, gamma = solve_stc(ds, rsh, rs)
    return PVsystSDMParams(
        I_L_ref=il,
        I_o_ref=io,
        gamma_ref=gamma,
        mu_gamma=mu_gamma,
        R_s=rs,
        R_sh_ref=rsh,
        R_sh_0=rsh * ds.fit.r_sh_0_ratio,
        R_sh_exp=ds.fit.r_sh_exp,
        cells_in_series=ds.cells_in_series,
        alpha_sc=ds.isc * ds.temp_coeff_isc_pct / 100.0,
        EgRef=BANDGAP_EV[ds.technology],
    )


def _fit_rs_to_low_light(ds: ModuleDatasheet, rsh: float, target: float) -> tuple[float, list[str]]:
    """Series resistance giving the target relative efficiency at 200 W/m2."""
    r_char = ds.vmp / ds.imp
    grid = np.linspace(0.0, 0.3 * r_char, 121)

    def f(rs: float) -> float:
        return build_params(ds, rsh, rs).low_light_rel_eff() - target

    vals = np.array([_safe(f, x) for x in grid])
    ok = np.isfinite(vals)
    if not ok.any():
        raise FitError("No series resistance gives a valid fit; check datasheet values.")
    for a, b, fa, fb in zip(grid[:-1], grid[1:], vals[:-1], vals[1:], strict=True):
        if np.isfinite(fa) and np.isfinite(fb) and fa * fb <= 0:
            return float(brentq(f, a, b, xtol=1e-12)), []
    # Not bracketed: take the closest feasible value.
    i = int(np.nanargmin(np.abs(vals)))
    achieved = vals[i] + target
    return float(grid[i]), [
        f"Low-light target {target:+.2%} not reachable by RSerie with RShunt={rsh:.0f} ohm; "
        f"closest {achieved:+.2%} at RSerie={grid[i]:.4f} ohm."
    ]


def _fit_mu_gamma(base: PVsystSDMParams, target: float) -> tuple[float, list[str]]:
    def f(mu: float) -> float:
        return replace(base, mu_gamma=mu).temp_coeff_pmax() - target

    lo, hi = -0.02, 0.02
    if f(lo) * f(hi) > 0:
        return 0.0, [
            f"Pmax temperature coefficient {target:.4%}/K not reachable with muGamma in "
            f"[{lo}, {hi}]; muGamma set to 0."
        ]
    return float(brentq(f, lo, hi, xtol=1e-12)), []


def fit_module(ds: ModuleDatasheet) -> ModuleFitResult:
    """Fit PVsyst one-diode parameters to a module datasheet."""
    assumptions: list[str] = list(ds.provenance_notes)
    warnings: list[str] = []

    # 1. Shunt resistance
    if ds.fit.r_sh_ref is not None:
        rsh = ds.fit.r_sh_ref
        assumptions.append(f"RShunt = {rsh:.1f} ohm (user supplied)")
    else:
        rsh = DEFAULT_RSH_FACTOR * ds.vmp / ds.imp
        assumptions.append(f"RShunt = {rsh:.1f} ohm (heuristic {DEFAULT_RSH_FACTOR:g} * Vmp/Imp)")
    assumptions.append(f"Rp_0 = {ds.fit.r_sh_0_ratio:g} * RShunt, Rp_Exp = {ds.fit.r_sh_exp:g}")

    # 2./3. Series resistance
    if ds.fit.r_s is not None:
        rs = ds.fit.r_s
        assumptions.append(f"RSerie = {rs:.4f} ohm (user supplied)")
        if ds.low_light_rel_eff_pct is not None:
            warnings.append("low_light_rel_eff_pct ignored because fit.r_s is set.")
    else:
        if ds.low_light_rel_eff_pct is not None:
            target = ds.low_light_rel_eff_pct / 100.0
            source = "datasheet"
        else:
            target = DEFAULT_LOW_LIGHT_REL_EFF[ds.technology]
            source = f"{ds.technology.value} default; not on datasheet"
        rs, w = _fit_rs_to_low_light(ds, rsh, target)
        warnings += w
        assumptions.append(
            f"RSerie = {rs:.4f} ohm, fitted to {target:+.2%} relative efficiency "
            f"at 200 W/m2 ({source})"
        )

    base = build_params(ds, rsh, rs)

    # 4. muGamma
    mu_gamma, w = _fit_mu_gamma(base, ds.temp_coeff_pmax_pct / 100.0)
    warnings += w
    params = replace(base, mu_gamma=mu_gamma)

    # Sanity
    if not 0.7 <= params.gamma_ref <= 2.0:
        warnings.append(f"Diode factor Gamma={params.gamma_ref:.3f} is outside the usual 0.7-2.0.")
    pmp_err = params.iv_summary()["pmp"] / (ds.vmp * ds.imp) - 1.0
    if abs(pmp_err) > 0.005:
        warnings.append(
            f"Model Pmax deviates {pmp_err:+.2%} from Vmp*Imp; the datasheet MPP may be "
            "rounded too coarsely or the low-light target is unrealistic."
        )

    return ModuleFitResult(ds, params, _checks(ds, params), assumptions, warnings)


def _checks(ds: ModuleDatasheet, p: PVsystSDMParams) -> list[CheckRow]:
    stc = p.iv_summary()
    rows = [
        CheckRow("Pmp @STC", "W", ds.vmp * ds.imp, stc["pmp"]),
        CheckRow("Isc @STC", "A", ds.isc, stc["isc"]),
        CheckRow("Voc @STC", "V", ds.voc, stc["voc"]),
        CheckRow("Imp @STC", "A", ds.imp, stc["imp"]),
        CheckRow("Vmp @STC", "V", ds.vmp, stc["vmp"]),
        CheckRow("muPmp", "%/K", ds.temp_coeff_pmax_pct, 100 * p.temp_coeff_pmax()),
        CheckRow("muVoc", "%/K", ds.temp_coeff_voc_pct, 100 * p.temp_coeff_voc()),
        CheckRow("rel. eff @200W/m2", "%", ds.low_light_rel_eff_pct, 100 * p.low_light_rel_eff()),
    ]
    return rows
