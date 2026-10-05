"""Build an inverter efficiency curve from datasheet efficiency figures.

Inverter losses are modelled with the standard three-term loss model
(Schmidt & Sauer, 1996), in per-unit of nominal AC power ``p``::

    p_in = p + l0 + l1 * p + l2 * p**2

``l0`` is the constant self-consumption, ``l1`` the voltage-drop-like linear
loss and ``l2`` the ohmic loss. ``l0`` and ``l2`` are kept positive; ``l1`` is
allowed to go slightly negative (as in empirical fits such as Sandia's), which
lets the quadratic model reproduce the flat top of modern inverter curves.
Efficiency is ``eta(p) = p / p_in``, whose maximum has the closed form
``1 / (1 + l1 + 2*sqrt(l0*l2))`` at ``p = sqrt(l0/l2)``.

The three coefficients are fitted to the datasheet's max, European and/or
CEC weighted efficiencies. With three figures the fit is exact; with two, a
weak prior on the loss split picks one solution out of the family that
matches. See ``docs/methodology.md``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import least_squares

from datasheet_to_pan_ond.models import EfficiencyPoint, InverterDatasheet

#: European efficiency weights {fraction of nominal output: weight}.
EURO_WEIGHTS: dict[float, float] = {
    0.05: 0.03,
    0.10: 0.06,
    0.20: 0.13,
    0.30: 0.10,
    0.50: 0.48,
    1.00: 0.20,
}
#: CEC (California Energy Commission) weights.
CEC_WEIGHTS: dict[float, float] = {
    0.10: 0.04,
    0.20: 0.05,
    0.30: 0.12,
    0.50: 0.21,
    0.75: 0.53,
    1.00: 0.05,
}
#: Output-power fractions at which the PVsyst ProfilPIO points are written,
#: following the layout of PVsyst-generated OND files.
PROFILE_FRACTIONS: tuple[float, ...] = (0.05, 0.10, 0.20, 0.30, 0.50, 0.75, 1.00)

#: Weak priors used to make two-figure fits well posed. Real inverter curves
#: peak at roughly 40-60 % load (PVsyst ``PInEffMax``), so the location of the
#: efficiency maximum is pulled towards 0.5 p.u.
_PRIOR_P_EFF_MAX = 0.5
#: Parameter vector is (log l0, l1, log l2).
_PRIOR_X = np.array([math.log(0.002), 0.0, math.log(0.006)])
_W_PEAK_UNDERDETERMINED = 1e-1
_W_PEAK_DETERMINED = 1e-4
_W_LOSSES = 1e-4
#: Residual (percentage points) above which a warning / an error is raised.
WARN_PP = 0.02
FAIL_PP = 0.3


class InverterFitError(RuntimeError):
    """Raised when the efficiency figures cannot be matched."""


@dataclass(frozen=True)
class LossModel:
    """Per-unit loss coefficients; see module docstring."""

    l0: float
    l1: float
    l2: float

    def efficiency(self, p_out_pu: float | np.ndarray) -> np.ndarray:
        p = np.asarray(p_out_pu, dtype=float)
        eta: np.ndarray = p / self.p_in(p)
        return eta

    def p_in(self, p_out_pu: float | np.ndarray) -> np.ndarray:
        p = np.asarray(p_out_pu, dtype=float)
        return p + self.l0 + self.l1 * p + self.l2 * p**2

    @property
    def eff_max(self) -> float:
        return 1.0 / (1.0 + self.l1 + 2.0 * math.sqrt(self.l0 * self.l2))

    @property
    def p_out_at_eff_max(self) -> float:
        return math.sqrt(self.l0 / self.l2)

    def weighted(self, weights: dict[float, float]) -> float:
        return float(sum(w * self.efficiency(p) for p, w in weights.items()))

    @property
    def eff_euro(self) -> float:
        return self.weighted(EURO_WEIGHTS)

    @property
    def eff_cec(self) -> float:
        return self.weighted(CEC_WEIGHTS)


def fit_loss_model(
    eff_max: float,
    eff_euro: float | None = None,
    eff_cec: float | None = None,
    warnings: list[str] | None = None,
) -> LossModel:
    """Fit a :class:`LossModel` to efficiency fractions (0-1, not %).

    With all three figures the fit is (near) exact. With two, the location
    of the efficiency peak is pulled towards 0.5 p.u. to pick a realistic
    curve shape. Datasheet rounding can make three figures slightly
    inconsistent; residuals above ``WARN_PP`` are reported in ``warnings``,
    above ``FAIL_PP`` an :class:`InverterFitError` is raised.
    """
    if eff_euro is None and eff_cec is None:
        raise InverterFitError("need eff_euro or eff_cec")
    targets = [("max", eff_max), ("euro", eff_euro), ("cec", eff_cec)]
    given = [(n, t) for n, t in targets if t is not None]
    w_peak = _W_PEAK_DETERMINED if len(given) == 3 else _W_PEAK_UNDERDETERMINED

    def model(x: np.ndarray) -> LossModel:
        return LossModel(math.exp(x[0]), float(x[1]), math.exp(x[2]))

    def values(m: LossModel) -> dict[str, float]:
        return {"max": m.eff_max, "euro": m.eff_euro, "cec": m.eff_cec}

    def resid(x: np.ndarray) -> np.ndarray:
        m = model(x)
        v = values(m)
        r = [(v[n] - t) * 1e3 for n, t in given]  # 1 unit = 0.1 percentage point
        r.append(w_peak * math.log(m.p_out_at_eff_max / _PRIOR_P_EFF_MAX))
        return np.concatenate([np.array(r), _W_LOSSES * (x - _PRIOR_X)])

    sol = least_squares(
        resid,
        x0=_PRIOR_X,
        bounds=([math.log(1e-6), -0.05, math.log(1e-6)], [math.log(0.1), 0.1, math.log(0.2)]),
        xtol=1e-15,
        ftol=1e-15,
        gtol=1e-15,
    )
    m = model(sol.x)
    if m.l1 < 0 and m.l1**2 >= 4 * m.l0 * m.l2:
        raise InverterFitError(f"fitted loss model is non-physical (efficiency >= 1): {m}")
    v = values(m)
    errs_pp = {n: 100 * (v[n] - t) for n, t in given}
    worst = max(abs(e) for e in errs_pp.values())
    detail = ", ".join(f"{n} {e:+.3f}" for n, e in errs_pp.items())
    if worst > FAIL_PP:
        raise InverterFitError(
            f"efficiency figures are inconsistent with the loss model (errors in pp: {detail})"
        )
    if warnings is not None:
        if worst > WARN_PP:
            warnings.append(
                f"Efficiency figures matched only approximately (errors in pp: {detail})"
            )
        if not 0.2 <= m.p_out_at_eff_max <= 1.2:
            warnings.append(
                f"Efficiency peak falls at {m.p_out_at_eff_max:.2f} p.u. of nominal power, "
                "which is unusual; check the max / Euro / CEC figures."
            )
    return m


def fit_loss_model_to_curve(curve: list[EfficiencyPoint]) -> LossModel:
    """Least-squares :class:`LossModel` through measured efficiency points.

    Used for reporting when a curve is supplied; the curve itself is written.
    """
    fr = np.array([p.p_out_frac for p in curve])
    eff = np.array([p.eff_pct for p in curve]) / 100.0

    def resid(x: np.ndarray) -> np.ndarray:
        m = LossModel(math.exp(x[0]), float(x[1]), math.exp(x[2]))
        return np.asarray((m.efficiency(fr) - eff) * 1e3)

    sol = least_squares(
        resid,
        x0=_PRIOR_X,
        bounds=([math.log(1e-7), -0.2, math.log(1e-7)], [math.log(0.2), 0.2, math.log(0.5)]),
    )
    return LossModel(math.exp(sol.x[0]), float(sol.x[1]), math.exp(sol.x[2]))


@dataclass
class EfficiencyProfile:
    """One PVsyst ProfilPIO curve: (P_in [W], P_out [W]) points.

    ``measured`` profiles come from supplied curve points; their headline
    figures are interpolated from the points. Otherwise they are evaluated
    from the loss model.
    """

    v_dc: float | None
    loss: LossModel
    points_w: list[tuple[float, float]]
    p_nom_w: float
    measured: bool = False

    def _curve(self) -> tuple[np.ndarray, np.ndarray]:
        pts = [(pout / self.p_nom_w, pout / pin) for pin, pout in self.points_w if pout > 0]
        fr, eff = zip(*pts, strict=True)
        return np.array(fr), np.array(eff)

    def efficiency(self, p_out_frac: float) -> float:
        if not self.measured:
            return float(self.loss.efficiency(p_out_frac))
        fr, eff = self._curve()
        return float(np.interp(p_out_frac, fr, eff))

    def _weighted(self, weights: dict[float, float]) -> float:
        return sum(w * self.efficiency(p) for p, w in weights.items())

    @property
    def eff_max(self) -> float:
        return float(np.max(self._curve()[1])) if self.measured else self.loss.eff_max

    @property
    def eff_euro(self) -> float:
        return self._weighted(EURO_WEIGHTS)

    @property
    def eff_cec(self) -> float:
        return self._weighted(CEC_WEIGHTS)

    @property
    def p_threshold_w(self) -> float:
        return self.points_w[0][0]

    @property
    def p_in_eff_max_w(self) -> float:
        if self.measured:
            fr, eff = self._curve()
            i = int(np.argmax(eff))
            return float(fr[i] / eff[i] * self.p_nom_w)
        loss = self.loss
        return float(loss.p_in(loss.p_out_at_eff_max)) * self.p_nom_w


@dataclass
class InverterFitResult:
    datasheet: InverterDatasheet
    main: EfficiencyProfile
    by_voltage: list[EfficiencyProfile] | None
    checks: list[tuple[str, float | None, float]] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def p_threshold_w(self) -> float:
        """DC input power below which output is zero [W]."""
        return self.main.p_threshold_w

    @property
    def p_in_eff_max_w(self) -> float:
        return self.main.p_in_eff_max_w

    @property
    def p_nom_dc_kw(self) -> float:
        return self.datasheet.p_nom_ac_kw / self.main.efficiency(1.0)


def _profile(
    ds: InverterDatasheet,
    loss: LossModel,
    v_dc: float | None,
    curve: list[EfficiencyPoint] | None = None,
    threshold_w: float | None = None,
) -> EfficiencyProfile:
    p_nom_w = ds.p_nom_ac_kw * 1e3
    thr = threshold_w if threshold_w is not None else loss.l0 * p_nom_w
    pts = [(thr, 0.0)]
    if curve is not None:
        pts += [(p.p_out_frac * p_nom_w / (p.eff_pct / 100), p.p_out_frac * p_nom_w) for p in curve]
        return EfficiencyProfile(v_dc, loss, pts, p_nom_w, measured=True)
    fracs = list(PROFILE_FRACTIONS)
    p_max_pu = ds.p_max_ac / ds.p_nom_ac_kw
    if p_max_pu > 1.0 + 1e-9:
        fracs.append(p_max_pu)
    pts += [(float(loss.p_in(f)) * p_nom_w, f * p_nom_w) for f in fracs]
    return EfficiencyProfile(v_dc, loss, pts, p_nom_w)


def _loss_for(
    eff_max_pct: float,
    eff_euro_pct: float | None,
    eff_cec_pct: float | None,
    curve: list[EfficiencyPoint] | None,
    warnings: list[str],
) -> LossModel:
    if curve is not None:
        return fit_loss_model_to_curve(curve)
    pct = 0.01
    return fit_loss_model(
        eff_max_pct * pct,
        None if eff_euro_pct is None else eff_euro_pct * pct,
        None if eff_cec_pct is None else eff_cec_pct * pct,
        warnings,
    )


def fit_inverter(ds: InverterDatasheet) -> InverterFitResult:
    """Fit efficiency curve(s) for an inverter datasheet."""
    warnings: list[str] = []
    main_loss = _loss_for(
        ds.eff_max_pct, ds.eff_euro_pct, ds.eff_cec_pct, ds.efficiency_curve, warnings
    )
    main = _profile(ds, main_loss, None, ds.efficiency_curve, ds.p_threshold_w)
    assumptions = list(ds.provenance_notes)
    if ds.efficiency_curve is not None:
        assumptions.append(
            f"Efficiency curve taken from {len(ds.efficiency_curve)} supplied points"
        )
    else:
        assumptions.append(
            "Efficiency curve from the 3-term loss model fitted to datasheet efficiencies"
        )
        n_given = 1 + (ds.eff_euro_pct is not None) + (ds.eff_cec_pct is not None)
        if n_given < 3:
            assumptions.append(
                "Only two efficiency figures given: efficiency peak location assumed near "
                f"{_PRIOR_P_EFF_MAX:.0%} load (shape at very low load is approximate)"
            )
    assumptions.append(
        f"Loss coefficients (p.u.): l0={main_loss.l0:.5f}, l1={main_loss.l1:+.5f}, "
        f"l2={main_loss.l2:.5f}"
    )

    by_voltage = None
    if ds.efficiency_by_voltage is not None:
        by_voltage = []
        for ev in ds.efficiency_by_voltage:
            loss = _loss_for(ev.eff_max_pct, ev.eff_euro_pct, ev.eff_cec_pct, ev.curve, warnings)
            by_voltage.append(_profile(ds, loss, ev.v_dc, ev.curve, ev.p_threshold_w))
    else:
        assumptions.append("No voltage-dependent data: one efficiency curve for all DC voltages")

    if ds.p_max_dc_kw is None:
        assumptions.append("PMaxDC not given: left at PVsyst default (omitted)")
    if ds.power_factor_min is None:
        assumptions.append("Power factor range not given: unity power factor only (TanPhi 0)")

    checks: list[tuple[str, float | None, float]] = [
        ("EfficMax %", ds.eff_max_pct, 100 * main.eff_max),
        ("EfficEuro %", ds.eff_euro_pct, 100 * main.eff_euro),
        ("EfficCEC %", ds.eff_cec_pct, 100 * main.eff_cec),
    ]
    return InverterFitResult(
        datasheet=ds,
        main=main,
        by_voltage=by_voltage,
        checks=checks,
        assumptions=assumptions,
        warnings=warnings,
    )
