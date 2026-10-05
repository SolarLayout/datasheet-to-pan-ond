# Validation against manufacturer PVsyst files

PVsyst itself is not available to this project. Instead, generated files are
compared with real PVsyst exports from the manufacturer, for products present
in both. Test: `tests/test_against_manufacturer.py`.

| Product | Manufacturer file | Our file (from CEC data) |
|---|---|---|
| ET Solar ET-M772BH550GL (550 W mono, half-cell) | `tests/data/reference/ET-M772BH550GL.PAN` (PVsyst 7.2) | `library/modules/mono-c-si/ELITE_Solar/ET-M772BH550GL.PAN` |
| CPS SCH275KTL-DO/US-800 (string inverter) | `tests/data/reference/CPS SCH275KTL-DO-US-800-250kW_275kVA_1.OND` (PVsyst 6.81) | `library/inverters/string-utility/Chint_Power_Systems_America/CPS_SCH275KTL-DO_US-800_800V.OND` |

## Module

Maximum power from each file's PVsyst model:

| Conditions | Manufacturer | Ours | Difference |
|---|---:|---:|---:|
| 1000 W/m², 25 °C | 550.62 W | 550.92 W | +0.05 % |
| 400 W/m², 30 °C | 215.48 W | 215.58 W | +0.05 % |
| 200 W/m², 25 °C | 107.36 W | 107.43 W | +0.07 % |
| 100 W/m², 20 °C | 52.95 W | 52.92 W | −0.07 % |
| 1000 W/m², 45 °C | 517.79 W | 513.19 W | −0.89 % |
| 1000 W/m², 65 °C | 484.16 W | 474.29 W | −2.04 % |

- **Low light and STC agree within 0.1 %.** The technology default for
  low-light efficiency (−2.5 % at 200 W/m²) happens to match this
  manufacturer's lab-derived file.
- **The temperature difference comes from the manufacturer file.** It states
  `muPmpReq = −0.34 %/K`, but its own parameters (`muGamma = −0.0001`) give
  about −0.29 %/K. Our file reproduces the stated datasheet coefficient
  (−0.337 %/K from CEC). PVsyst allows a mismatch between the required and the
  modelled coefficient, and this manufacturer did not tune it away. In hot
  climates the manufacturer file therefore predicts about 1 % more annual energy.
- The input data differs slightly too: CEC lists the Isc coefficient as
  5.8 mA/K, the manufacturer file has 7.28 mA/K. NCelP (1 vs 2) is cosmetic.

## Inverter

| Field | Manufacturer | Ours | Why |
|---|---|---|---|
| PNomConv / PMaxOUT | 250 / 250 kW | 275 / 275 kW | CEC `Paco` is the maximum continuous AC power (275 kVA unit) |
| MPPT window | 500–1500 V | 900–1300 V | CEC lists the full-power MPPT window; the file states the operating window |
| Efficiency, low / nominal / high V | see below | within 0.45 pp at 20–100 % load | both come from lab measurements |

Efficiency at 20–100 % load (ours minus manufacturer, percentage points):
−0.12 to +0.14 at low voltage, −0.21 to −0.39 at nominal voltage, and +0.09
to +0.13 at high voltage.

**What this means for users of the inverter library:** the curves are good.
But `PNomConv` is the CEC maximum continuous power, which can be above the
datasheet's *nominal* power, and the MPPT window is the narrower full-power
window. Both affect PVsyst sizing checks (DC/AC ratio, string voltage
window). Check them against the datasheet for the inverter you use.

## Limits of this validation

There are only two products, because manufacturers' PVsyst files are rarely
public. The CEC-to-PAN/OND pipeline is also cross-checked against NREL's
independent CEC 6-parameter model for 400 modules (`docs/cec-import.md`).
Import into PVsyst, or another tool that reads PAN/OND, remains open.
