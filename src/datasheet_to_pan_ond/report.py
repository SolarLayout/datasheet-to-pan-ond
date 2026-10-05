"""Human- and machine-readable fit reports."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from datasheet_to_pan_ond.inverter_fit import InverterFitResult
from datasheet_to_pan_ond.module_fit import ModuleFitResult


def _fmt(v: float | None, nd: int = 4) -> str:
    return "-" if v is None else f"{v:.{nd}f}"


def module_report_text(r: ModuleFitResult) -> str:
    p = r.params
    lines = [f"Module: {r.datasheet.manufacturer} {r.datasheet.model}", "", "Fitted parameters:"]
    lines += [
        f"  RShunt  = {p.R_sh_ref:.1f} ohm   Rp_0 = {p.R_sh_0:.1f} ohm   Rp_Exp = {p.R_sh_exp:g}",
        f"  RSerie  = {p.R_s:.4f} ohm",
        f"  Gamma   = {p.gamma_ref:.4f}       muGamma = {p.mu_gamma:.6f} 1/K",
        f"  I_L_ref = {p.I_L_ref:.4f} A    I_o_ref = {p.I_o_ref:.4e} A",
        "",
        f"  {'check':<20}{'target':>12}{'model':>12}{'error':>12}",
    ]
    for c in r.checks:
        lines.append(
            f"  {c.name + ' [' + c.unit + ']':<20}{_fmt(c.target):>12}{_fmt(c.model):>12}"
            f"{_fmt(c.error):>12}"
        )
    lines += ["", "Assumptions:"] + [f"  - {a}" for a in r.assumptions]
    if r.warnings:
        lines += ["", "WARNINGS:"] + [f"  ! {w}" for w in r.warnings]
    return "\n".join(lines)


def module_report_dict(r: ModuleFitResult) -> dict[str, Any]:
    return {
        "datasheet": r.datasheet.model_dump(mode="json"),
        "params": asdict(r.params),
        "checks": [
            {"name": c.name, "unit": c.unit, "target": c.target, "model": c.model, "error": c.error}
            for c in r.checks
        ],
        "assumptions": r.assumptions,
        "warnings": r.warnings,
    }


def inverter_report_text(r: InverterFitResult) -> str:
    ds = r.datasheet
    src = "supplied points" if r.main.measured else "loss-model fit"
    lines = [f"Inverter: {ds.manufacturer} {ds.model}", "", f"Efficiency curve (main, {src}):"]
    lines.append(f"  {'P_out [kW]':>12}{'P_in [kW]':>12}{'eff [%]':>10}")
    for pin, pout in r.main.points_w[1:]:
        lines.append(f"  {pout / 1e3:>12.3f}{pin / 1e3:>12.3f}{100 * pout / pin:>10.3f}")
    lines += [
        "",
        f"  Threshold power = {r.p_threshold_w:.1f} W, PNomDC = {r.p_nom_dc_kw:.3f} kW",
        "",
        f"  {'check':<16}{'target':>10}{'model':>10}",
    ]
    for name, target, model in r.checks:
        lines.append(f"  {name:<16}{_fmt(target, 3):>10}{_fmt(model, 3):>10}")
    if r.by_voltage:
        lines += ["", "Voltage-dependent curves:"]
        for pr in r.by_voltage:
            lines.append(
                f"  V={pr.v_dc:.0f} V: max {100 * pr.eff_max:.3f} %, "
                f"Euro {100 * pr.eff_euro:.3f} %, CEC {100 * pr.eff_cec:.3f} %"
            )
    lines += ["", "Assumptions:"] + [f"  - {a}" for a in r.assumptions]
    if r.warnings:
        lines += ["", "WARNINGS:"] + [f"  ! {w}" for w in r.warnings]
    return "\n".join(lines)


def inverter_report_dict(r: InverterFitResult) -> dict[str, Any]:
    return {
        "datasheet": r.datasheet.model_dump(mode="json"),
        "loss_model": asdict(r.main.loss),
        "profile_w": r.main.points_w,
        "by_voltage": None
        if r.by_voltage is None
        else [
            {"v_dc": p.v_dc, "loss_model": asdict(p.loss), "profile_w": p.points_w}
            for p in r.by_voltage
        ],
        "checks": [{"name": n, "target": t, "model": m} for n, t, m in r.checks],
        "assumptions": r.assumptions,
        "warnings": r.warnings,
    }
