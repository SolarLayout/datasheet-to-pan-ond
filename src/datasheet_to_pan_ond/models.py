"""Input schemas: the datasheet values a user provides.

Units follow datasheet conventions (W, V, A, %/K, metres, kg) rather than
PVsyst's internal units. Conversion to PVsyst units happens in the writers.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_STRICT = ConfigDict(extra="forbid", frozen=True)


class Technology(StrEnum):
    """PVsyst cell technology codes (``Technol`` key)."""

    SI_MONO = "mtSiMono"
    SI_POLY = "mtSiPoly"
    CDTE = "mtCdTe"
    CIS = "mtCIS"
    A_SI_H = "mtAsiH"
    HIT = "mtHIT"


class Dimensions(BaseModel):
    """Physical data used in the PVsyst ``pvCommercial`` block."""

    model_config = _STRICT

    width_m: float | None = Field(default=None, gt=0)
    height_m: float | None = Field(default=None, gt=0)
    depth_m: float | None = Field(default=None, gt=0)
    weight_kg: float | None = Field(default=None, gt=0)


# --------------------------------------------------------------------------- #
# Module
# --------------------------------------------------------------------------- #


class ModuleFitOptions(BaseModel):
    """Assumptions used where the datasheet does not determine the model.

    PVsyst's one-diode model needs a shunt resistance and its irradiance
    dependence, which datasheets almost never give. These options let the
    user override the defaults. See ``docs/methodology.md``.
    """

    model_config = _STRICT

    r_sh_ref: float | None = Field(
        default=None,
        gt=0,
        description="Shunt resistance at STC [ohm]. Default: heuristic 100 * Vmp / Imp.",
    )
    r_sh_0_ratio: float = Field(
        default=4.0,
        gt=1,
        description="Rsh at 0 W/m2 as a multiple of Rsh at STC (PVsyst Rp_0 / RShunt).",
    )
    r_sh_exp: float = Field(default=5.5, gt=0, description="PVsyst Rp_Exp.")
    r_s: float | None = Field(
        default=None,
        ge=0,
        description="Fix the series resistance [ohm]. Default: fitted to the low-light "
        "efficiency (datasheet value or technology default).",
    )


class IAMPoint(BaseModel):
    model_config = _STRICT

    angle_deg: float = Field(ge=0, le=90)
    iam: float = Field(ge=0, le=1.0)


class ModuleDatasheet(BaseModel):
    """Datasheet values for one power bin of a PV module."""

    model_config = _STRICT

    manufacturer: str = Field(min_length=1)
    model: str = Field(min_length=1)
    data_source: str = "Manufacturer datasheet"
    year: int | None = Field(default=None, ge=1990, le=2100)
    provenance_notes: list[str] = Field(
        default_factory=list,
        description="Notes on how input values were obtained (e.g. inferred by an importer). "
        "Carried into the fit assumptions and the file's Remarks.",
    )

    technology: Technology = Technology.SI_MONO
    cells_in_series: int = Field(gt=0, description="PVsyst NCelS (e.g. 72 for a 144 half-cell).")
    cells_in_parallel: int = Field(default=1, gt=0, description="PVsyst NCelP (2 for half-cell).")
    bypass_diodes: int = Field(default=3, ge=0)
    half_cell: bool = Field(
        default=False, description="Twin half-cell layout (SubModuleLayout=slTwinHalfCells)."
    )

    # STC electrical values
    p_nom: float = Field(gt=0, description="Nominal power at STC [W].")
    p_nom_tol_up_pct: float = Field(default=0.0, ge=0, description="Positive tolerance [%].")
    isc: float = Field(gt=0, description="Short-circuit current at STC [A].")
    voc: float = Field(gt=0, description="Open-circuit voltage at STC [V].")
    imp: float = Field(gt=0, description="Current at MPP at STC [A].")
    vmp: float = Field(gt=0, description="Voltage at MPP at STC [V].")

    # Temperature coefficients, datasheet convention (%/K)
    temp_coeff_isc_pct: float = Field(gt=-0.5, lt=0.5, description="Isc temp. coeff. [%/K].")
    temp_coeff_voc_pct: float = Field(lt=0, gt=-1.0, description="Voc temp. coeff. [%/K].")
    temp_coeff_pmax_pct: float = Field(lt=0, gt=-1.0, description="Pmax temp. coeff. [%/K].")

    # Optional
    low_light_rel_eff_pct: float | None = Field(
        default=None,
        gt=-20,
        lt=5,
        description="Relative efficiency at 200 W/m2, 25 C versus STC [%], e.g. -2.5.",
    )
    v_max_iec: float | None = Field(default=None, gt=0, description="Max system voltage IEC [V].")
    v_max_ul: float | None = Field(default=None, gt=0, description="Max system voltage UL [V].")
    bifaciality: float | None = Field(default=None, gt=0, le=1.0)
    cell_area_cm2: float | None = Field(default=None, gt=0)
    iam_profile: list[IAMPoint] | None = None
    dimensions: Dimensions = Dimensions()
    fit: ModuleFitOptions = ModuleFitOptions()

    @model_validator(mode="after")
    def _check_consistency(self) -> ModuleDatasheet:
        if not self.imp < self.isc:
            raise ValueError(f"imp ({self.imp}) must be below isc ({self.isc})")
        if not self.vmp < self.voc:
            raise ValueError(f"vmp ({self.vmp}) must be below voc ({self.voc})")
        pmp = self.vmp * self.imp
        if abs(pmp - self.p_nom) / self.p_nom > 0.02:
            raise ValueError(
                f"vmp * imp = {pmp:.1f} W differs from p_nom = {self.p_nom} W by more than 2%; "
                "check the datasheet values (wrong power bin or a typo?)"
            )
        ff = pmp / (self.voc * self.isc)
        if not 0.55 < ff < 0.9:
            raise ValueError(f"fill factor {ff:.3f} is implausible for a PV module")
        if self.iam_profile is not None:
            angles = [p.angle_deg for p in self.iam_profile]
            if angles != sorted(angles) or len(set(angles)) != len(angles):
                raise ValueError("iam_profile angles must be strictly increasing")
        return self


# --------------------------------------------------------------------------- #
# Inverter
# --------------------------------------------------------------------------- #


class EfficiencyPoint(BaseModel):
    """One point of a measured / digitised efficiency curve."""

    model_config = _STRICT

    p_out_frac: float = Field(gt=0, le=2.0, description="AC output as a fraction of nominal.")
    eff_pct: float = Field(gt=0, lt=100)


def _check_curve(curve: list[EfficiencyPoint] | None) -> None:
    if curve is None:
        return
    fr = [p.p_out_frac for p in curve]
    if len(fr) < 3 or fr != sorted(fr) or len(set(fr)) != len(fr):
        raise ValueError("efficiency curve needs >= 3 points with increasing p_out_frac")
    if fr[-1] < 1.0 - 1e-9:
        raise ValueError("efficiency curve must extend to nominal power (p_out_frac >= 1)")


class VoltageEfficiency(BaseModel):
    """Efficiency figures at one DC input voltage (for voltage-dependent curves)."""

    model_config = _STRICT

    v_dc: float = Field(gt=0)
    eff_max_pct: float = Field(gt=50, lt=100)
    eff_euro_pct: float | None = Field(default=None, gt=50, lt=100)
    eff_cec_pct: float | None = Field(default=None, gt=50, lt=100)
    curve: list[EfficiencyPoint] | None = Field(
        default=None, description="Measured curve at this voltage; used instead of the fit."
    )
    p_threshold_w: float | None = Field(default=None, gt=0, description="Start-up DC power [W].")

    @model_validator(mode="after")
    def _need_weighted(self) -> VoltageEfficiency:
        _check_efficiencies(self.eff_max_pct, self.eff_euro_pct, self.eff_cec_pct)
        _check_curve(self.curve)
        return self


class TemperatureDerating(BaseModel):
    """Optional ambient-temperature derating, as in the PVsyst OND (TPNom etc.)."""

    model_config = _STRICT

    t_p_nom_c: float = Field(description="Max ambient temp. at nominal power [C].")
    t_p_max_c: float = Field(description="Max ambient temp. at max power [C].")
    t_lim1_c: float = Field(description="Temp. of first derating step [C].")
    p_lim1_kw: float = Field(gt=0)
    t_lim_abs_c: float = Field(description="Absolute max ambient temp. [C].")
    p_lim_abs_kw: float = Field(ge=0)


class InverterDatasheet(BaseModel):
    """Datasheet values for a grid-connected inverter."""

    model_config = _STRICT

    manufacturer: str = Field(min_length=1)
    model: str = Field(min_length=1)
    data_source: str = "Manufacturer datasheet"
    year: int | None = Field(default=None, ge=1990, le=2100)
    provenance_notes: list[str] = Field(
        default_factory=list,
        description="Notes on how input values were obtained (e.g. inferred by an importer). "
        "Carried into the fit assumptions and the file's Remarks.",
    )

    # AC side
    p_nom_ac_kw: float = Field(gt=0, description="Nominal AC power [kW].")
    p_max_ac_kw: float | None = Field(default=None, gt=0, description="Max AC power [kW].")
    v_ac: float = Field(gt=0, description="Nominal AC grid voltage (line-to-line for 3-phase) [V].")
    phases: Literal["three", "single"] = "three"
    i_nom_ac: float | None = Field(default=None, gt=0, description="Nominal AC current [A].")
    i_max_ac: float | None = Field(default=None, gt=0, description="Max AC current [A].")
    power_factor_min: float | None = Field(
        default=None, gt=0, le=1.0, description="Min power factor (e.g. 0.8 leading/lagging)."
    )
    transformer: bool = False

    # DC side
    p_max_dc_kw: float | None = Field(default=None, gt=0, description="Max DC input power [kW].")
    v_mpp_min: float = Field(gt=0, description="Min MPPT voltage [V].")
    v_mpp_max: float = Field(gt=0, description="Max MPPT voltage [V].")
    v_mpp_nom: float | None = Field(
        default=None,
        gt=0,
        description="Nominal MPP voltage, the datasheet's rated or nominal DC input voltage [V] "
        "(PVsyst VmppNom). Omitted from the file when not given.",
    )
    v_dc_max: float = Field(gt=0, description="Absolute max DC input voltage [V].")
    i_max_dc: float = Field(gt=0, description="Max DC input current (all MPPTs) [A].")
    n_mppt: int = Field(default=1, gt=0)
    n_inputs: int | None = Field(default=None, gt=0, description="Number of DC string inputs.")

    # Efficiency
    eff_max_pct: float = Field(gt=50, lt=100)
    eff_euro_pct: float | None = Field(default=None, gt=50, lt=100)
    eff_cec_pct: float | None = Field(default=None, gt=50, lt=100)
    efficiency_curve: list[EfficiencyPoint] | None = Field(
        default=None,
        description="Measured / digitised efficiency curve at nominal DC voltage. When given, "
        "the OND profile uses these points instead of the loss-model fit.",
    )
    p_threshold_w: float | None = Field(
        default=None, gt=0, description="DC power at which the inverter starts producing [W]."
    )
    efficiency_by_voltage: list[VoltageEfficiency] | None = Field(
        default=None,
        description="Exactly three entries (low / nominal / high DC voltage) when known.",
    )

    night_loss_w: float = Field(default=1.0, ge=0)
    temperature_derating: TemperatureDerating | None = None
    dimensions: Dimensions = Dimensions()

    @model_validator(mode="after")
    def _check_consistency(self) -> InverterDatasheet:
        _check_efficiencies(self.eff_max_pct, self.eff_euro_pct, self.eff_cec_pct)
        _check_curve(self.efficiency_curve)
        if not self.v_mpp_min < self.v_mpp_max <= self.v_dc_max:
            raise ValueError("require v_mpp_min < v_mpp_max <= v_dc_max")
        if self.v_mpp_nom is not None and not self.v_mpp_min <= self.v_mpp_nom <= self.v_mpp_max:
            window = f"{self.v_mpp_min} to {self.v_mpp_max} V"
            raise ValueError(f"v_mpp_nom must lie within the MPPT window ({window})")
        if self.p_max_ac_kw is not None and self.p_max_ac_kw < self.p_nom_ac_kw:
            raise ValueError("p_max_ac_kw must be >= p_nom_ac_kw")
        if self.efficiency_by_voltage is not None:
            volts = [e.v_dc for e in self.efficiency_by_voltage]
            if len(volts) != 3 or volts != sorted(volts) or len(set(volts)) != 3:
                raise ValueError("efficiency_by_voltage needs 3 entries with increasing v_dc")
            if not (self.v_mpp_min <= volts[0] and volts[-1] <= self.v_mpp_max):
                raise ValueError("efficiency_by_voltage voltages must lie within the MPPT range")
        return self

    @property
    def p_max_ac(self) -> float:
        return self.p_max_ac_kw if self.p_max_ac_kw is not None else self.p_nom_ac_kw


def _check_efficiencies(eff_max: float, eff_euro: float | None, eff_cec: float | None) -> None:
    if eff_euro is None and eff_cec is None:
        raise ValueError("give at least one weighted efficiency: eff_euro_pct or eff_cec_pct")
    for name, val in (("eff_euro_pct", eff_euro), ("eff_cec_pct", eff_cec)):
        if val is not None and val >= eff_max:
            raise ValueError(f"{name} ({val}) must be below eff_max_pct ({eff_max})")
