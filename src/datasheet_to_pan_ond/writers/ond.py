"""Build a PVsyst ``.OND`` (inverter) file from an inverter fit.

Layout mirrors a PVsyst 6.81 export
(``tests/data/reference/CPS SCH275KTL-DO-US-800-250kW_275kVA_1.OND``),
including its quirks (``PInEffMax =``, ``IsolSurvey =``, tab-separated top
comment, trailing comma on voltage lists).
"""

from __future__ import annotations

import datetime as dt
import math
from pathlib import Path

from datasheet_to_pan_ond.inverter_fit import EfficiencyProfile, InverterFitResult
from datasheet_to_pan_ond.models import InverterDatasheet
from datasheet_to_pan_ond.writers.pan import provenance_lines
from datasheet_to_pan_ond.writers.pvsyst_format import (
    DEFAULT_FORMAT,
    Blank,
    FileFormat,
    Section,
    cubic_profile,
    num,
    price_date,
    pvobject,
    remarks,
    sanitize,
    write,
)

OND_VERSION = "6.81"
#: Opaque PVsyst bit flags, copied from PVsyst exports (semantics unknown).
FLAGS_INVERTER = "$00381562"
FLAGS_COMMERCIAL = "$0041"
PIO_LAST_COMPILE = "$8085"
PIO_V_LAST_COMPILE = "$8089"
PIO_NPTS_MAX = 11


def build_ond(result: InverterFitResult, now: dt.datetime | None = None) -> Section:
    ds = result.datasheet
    root = pvobject("PVObject_", "pvGInverter")
    root.add("Comment", "\t".join(sanitize(x) for x in (ds.manufacturer, ds.model, ds.data_source)))
    root.add("Version", OND_VERSION)
    root.add_opt("ParObj1", ds.year)
    root.add("Flags", FLAGS_INVERTER)
    root.items.append(Blank())
    root.items.append(_commercial(ds, result, now))
    root.add("Transfo", "With" if ds.transformer else "Without")
    root.items.append(Blank())
    root.items.append(_converter(result))

    root.add("NbInputs", ds.n_inputs if ds.n_inputs is not None else ds.n_mppt)
    root.add("NbMPPT", ds.n_mppt)
    tan_phi = 0.0
    if ds.power_factor_min is not None:
        tan_phi = math.tan(math.acos(ds.power_factor_min))
    root.add("TanPhiMin", num(-tan_phi, 3)).add("TanPhiMax", num(tan_phi, 3))
    root.add("MasterSlave", "No_M_S")
    root.add("IsolSurvey ", "Yes")
    root.add("DC_Switch", "Yes")
    root.add("Night_Loss", num(ds.night_loss_w, 2))
    return root


def _commercial(
    ds: InverterDatasheet, result: InverterFitResult, now: dt.datetime | None
) -> Section:
    c = pvobject("PVObject_Commercial", "pvCommercial")
    c.add("Comment", sanitize(f"{ds.manufacturer} - generated from datasheet"))
    c.add("Flags", FLAGS_COMMERCIAL)
    c.add("Manufacturer", sanitize(ds.manufacturer)).add("Model", sanitize(ds.model))
    c.add("DataSource", sanitize(ds.data_source))
    c.add_opt("YearBeg", ds.year)
    dim = ds.dimensions
    c.add_opt("Width", None if dim.width_m is None else num(dim.width_m, 3))
    c.add_opt("Height", None if dim.height_m is None else num(dim.height_m, 3))
    c.add_opt("Depth", None if dim.depth_m is None else num(dim.depth_m, 3))
    c.add_opt("Weight", None if dim.weight_kg is None else num(dim.weight_kg, 3))
    c.add("NPieces", 0)
    c.add("PriceDate", price_date(now))
    c.items.append(remarks(provenance_lines(result.assumptions)))
    return c


def _ac_current(ds: InverterDatasheet, p_kw: float) -> float:
    factor = math.sqrt(3) if ds.phases == "three" else 1.0
    return p_kw * 1e3 / (factor * ds.v_ac)


def _profile_section(key: str, prof: EfficiencyProfile, last_compile: str) -> Section:
    pts = [(num(pin, 1), num(pout, 1)) for pin, pout in prof.points_w]
    return cubic_profile(key, pts, mode=1, last_compile=last_compile, n_pts_max=PIO_NPTS_MAX)


def mppt_window(ds: InverterDatasheet) -> tuple[int, int, int]:
    """Integer (VMppMin, VMPPMax, VAbsMax), rounded inwards (ceil / floor / floor)."""
    eps = 1e-6
    return (
        math.ceil(ds.v_mpp_min - eps),
        math.floor(ds.v_mpp_max + eps),
        math.floor(ds.v_dc_max + eps),
    )


def _converter(result: InverterFitResult) -> Section:
    ds = result.datasheet
    main = result.main
    c = Section("Converter=TConverter", "End of TConverter")
    c.add("PNomConv", num(ds.p_nom_ac_kw, 3))
    c.add("PMaxOUT", num(ds.p_max_ac, 3))
    c.add("VOutConv", num(ds.v_ac, 1))
    # PVsyst writes these as integers; round the MPPT window inwards so it is never
    # wider than the datasheet's.
    v_min, v_max, v_abs = mppt_window(ds)
    c.add("VMppMin", v_min)
    c.add("VMPPMax", v_max)
    c.add("VAbsMax", v_abs)
    c.add("PSeuil", num(result.p_threshold_w, 1))
    c.add("EfficMax", num(100 * main.eff_max, 2))
    c.add("EfficEuro", num(100 * main.eff_euro, 2))
    c.add("FResNorm", "0.00")
    c.add("ModeOper", "MPPT")
    c.add("CompPMax", "Lim").add("CompVMax", "Lim")
    c.add("MonoTri", "Tri" if ds.phases == "three" else "Mono")
    c.add("ModeAffEnum", "Efficf_POut").add("UnitAffEnum", "kW")
    c.add("PNomDC", num(result.p_nom_dc_kw, 3))
    c.add_opt("PMaxDC", None if ds.p_max_dc_kw is None else num(ds.p_max_dc_kw, 3))
    c.add("IDCMax", "0.0")
    c.add("IMaxDC", num(ds.i_max_dc, 1))
    i_nom = ds.i_nom_ac if ds.i_nom_ac is not None else _ac_current(ds, ds.p_nom_ac_kw)
    i_max = ds.i_max_ac if ds.i_max_ac is not None else _ac_current(ds, ds.p_max_ac)
    c.add("INomAC", num(i_nom, 1)).add("IMaxAC", num(i_max, 1))
    td = ds.temperature_derating
    if td is not None:
        c.add("TPNom", num(td.t_p_nom_c, 1)).add("TPMax", num(td.t_p_max_c, 1))
        c.add("TPLim1", num(td.t_lim1_c, 1)).add("TPLimAbs", num(td.t_lim_abs_c, 1))
        c.add("PLim1", num(td.p_lim1_kw, 3)).add("PLimAbs", num(td.p_lim_abs_kw, 3))
    c.add("PInEffMax ", num(result.p_in_eff_max_w, 3))
    c.add("PThreshEff", num(result.p_threshold_w, 1))
    c.add("HasdefaultPThresh", "False")
    c.items.append(Blank())
    c.items.append(_profile_section("ProfilPIO", result.main, PIO_LAST_COMPILE))

    if result.by_voltage:
        profs = result.by_voltage
        vs = [min(max(pr.v_dc or 0.0, v_min), v_max) for pr in profs]
        c.add("VNomEff", ",".join(num(v, 1) for v in vs) + ",")
        c.add("EfficMaxV", ",".join(num(100 * pr.eff_max, 3) for pr in profs) + ",")
        c.add("EfficEuroV", ",".join(num(100 * pr.eff_euro, 3) for pr in profs) + ",")
        for i, pr in enumerate(profs, start=1):
            c.items.append(Blank())
            c.items.append(_profile_section(f"ProfilPIOV{i}", pr, PIO_V_LAST_COMPILE))
    return c


def write_ond(
    result: InverterFitResult,
    path: str | Path,
    fmt: FileFormat = DEFAULT_FORMAT,
    now: dt.datetime | None = None,
) -> Path:
    return write(build_ond(result, now), path, fmt)
