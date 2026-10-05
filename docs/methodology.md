# Methodology

This document explains how datasheet values are turned into PVsyst model
parameters, which values are assumed, and how good the result can be.

A PAN/OND file built from a datasheet is an **estimate**. It is suitable for
early design, screening and comparing products. It is not a substitute for
a PAN file derived from laboratory measurements (IEC 61853-1 matrix, low
irradiance tests), which lenders and independent engineers usually require
for bankable energy yield assessments.

---

## 1. Modules (`.PAN`)

### 1.1 The model

PVsyst uses a one-diode model with irradiance-dependent shunt resistance and
temperature-dependent diode factor:

```
I = IL - I0 * (exp((V + I*Rs) / (gamma * Ns * k*T/q)) - 1) - (V + I*Rs) / Rsh

Rsh(G)    = Rsh_ref + (Rsh_0 - Rsh_ref) * exp(-Rsh_exp * G / G_ref)
gamma(T)  = gamma_ref + mu_gamma * (T - T_ref)
IL(G, T)  = G/G_ref * (IL_ref + alpha_sc * (T - T_ref))
I0(T)     = I0_ref * (T/T_ref)^3 * exp(q*Eg/(k*gamma) * (1/T_ref - 1/T))
```

We evaluate it with `pvlib.pvsystem.calcparams_pvsyst` and
`pvlib.pvsystem.singlediode`, which implement PVsyst's equations.

| PAN key  | Symbol      | Source |
|----------|-------------|--------|
| `Isc`, `Voc`, `Imp`, `Vmp`, `PNom` | | datasheet |
| `muISC` [mA/K] | alpha_sc | datasheet (%/K × Isc) |
| `muVocSpec` [mV/K] | | datasheet (informational in PVsyst) |
| `muPmpReq` [%/K] | | datasheet; target for `muGamma` |
| `RShunt` | Rsh_ref | **assumed** (heuristic) or user value |
| `Rp_0`, `Rp_Exp` | Rsh_0, Rsh_exp | **assumed** defaults (4 × RShunt, 5.5) |
| `RSerie` | Rs | **fitted** to low-light efficiency |
| `Gamma` | gamma_ref | **fitted** (STC curve) |
| `muGamma` | mu_gamma | **fitted** to `muPmpReq` |

### 1.2 Procedure

1. **Shunt resistance.** `RShunt = 100 × Vmp/Imp` unless supplied
   (`fit.r_sh_ref`). For a modern 550 W c-Si module that gives ~320 Ω, close to
   the ~300 Ω used in manufacturer PAN files. `Rp_0 = 4 × RShunt`,
   `Rp_Exp = 5.5`.
2. **STC curve.** For a given `RSerie`, `IL`, `I0` and `Gamma` are solved so
   the I-V curve passes exactly through `(0, Isc)`, `(Voc, 0)` and
   `(Vmp, Imp)`. `I0` and `IL` follow in closed form from the Isc/Voc
   conditions; `Gamma` is a 1-D root find on the MPP condition.
3. **Series resistance.** With `RShunt` fixed, `RSerie` mostly controls the
   efficiency at low irradiance: higher `RSerie` gives a better relative
   efficiency at 200 W/m². `RSerie` is solved so that
   `eta(200 W/m², 25 °C) / eta(STC) − 1` matches:
   - the datasheet's `low_light_rel_eff_pct`, if given, or
   - a technology default (−2.5 % for mono c-Si, −3 % poly, −2 % HIT,
     −1 % CdTe).

   `fit.r_s` fixes `RSerie` directly instead.
4. **Temperature behaviour.** `muGamma` is solved so the model's relative
   Pmax temperature coefficient
   (`pvlib.ivtools.sdm.pvsyst_temperature_coeff`) equals `muPmpReq`.

### 1.3 Why not also force dP/dV = 0 at the datasheet MPP?

STC gives four conditions (Isc, Voc, the MPP point and a zero slope of power
at the MPP), so in principle Rs could be fitted from STC values alone.
In practice the datasheet's Vmp and Imp are rounded to 0.01 V / 0.01 A,
and that makes the four-condition solution very sensitive to rounding. For the reference
ET Solar 550 W PAN it gives `Gamma ≈ 1.28`, `RSerie ≈ 0.08 Ω` and an
unrealistic −7.7 % relative efficiency at 200 W/m².

PVsyst itself only forces the datasheet MPP to lie **on** the curve.
`tests/test_module_fit.py` checks this: given the reference file's
`RShunt`/`RSerie`, our STC solve reproduces its `Gamma` (0.979 vs 0.980).
Given that file's own low-light efficiency, the low-light fit recovers its
`RSerie` to within 1e-4 Ω.

As a consequence, the model's true Pmax is typically 0.1–0.2 % above
`Vmp × Imp`, and its MPP voltage sits a few tenths of a volt lower. The same
happens with manufacturer PAN files. The fit report shows these deviations.

### 1.4 Known limitations

- **Low-light performance is the dominant uncertainty.** If the datasheet has
  no 200 W/m² figure, the technology default is used. Each 1 % of relative
  efficiency at low light moves annual yield by roughly 0.3–0.6 %, depending
  on climate.
- `muVocSpec` is written as stated on the datasheet. The model's own Voc
  coefficient (shown in the report) is usually slightly more negative. PVsyst
  behaves the same way.
- Bifacial modules: only `BifacialityFactor` is written. Rear-side
  parameters beyond that are not modelled.
- Reverse-bias, spectral and IAM defaults are PVsyst defaults
  (`writers/pan.py::PVSYST_DEFAULTS`). If no IAM profile is given, PVsyst
  uses its own default IAM.
- Thin-film-specific PVsyst corrections (e.g. recombination term
  `d²/µτ` for a-Si/CdTe) are not written.

---

## 2. Inverters (`.OND`)

### 2.1 Loss model

The efficiency curve uses the three-term loss model (Schmidt & Sauer),
in per-unit of nominal AC power:

```
p_in = p_out + l0 + l1 * p_out + l2 * p_out^2
eta  = p_out / p_in
eta_max = 1 / (1 + l1 + 2*sqrt(l0*l2))   at   p_out = sqrt(l0/l2)
```

`l0` (self-consumption) and `l2` (ohmic) are positive. `l1` may be slightly
negative, as in empirical fits, so that the model can follow the flat top
of modern inverter curves.

### 2.2 Fit

The coefficients are fitted by least squares to the datasheet's max,
European and CEC efficiencies:

| Weighting | Load points (fraction of nominal output) and weights |
|-----------|------------------------------------------------------|
| European  | 5 % (0.03), 10 % (0.06), 20 % (0.13), 30 % (0.10), 50 % (0.48), 100 % (0.20) |
| CEC       | 10 % (0.04), 20 % (0.05), 30 % (0.12), 50 % (0.21), 75 % (0.53), 100 % (0.05) |

- **Three figures**: fitted (near) exactly.
- **Two figures** (max + Euro or max + CEC): one degree of freedom is left,
  so the location of the efficiency peak is pulled towards 50 % load. That is
  where peaks fall in real PVsyst curves (e.g. ~60 % in the reference OND).
- Residuals above 0.02 pp produce a warning. Residuals above 0.3 pp are an
  error (the figures contradict each other).

Check against the reference CPS 250 kW OND: fitting only its max (99.01 %)
and Euro (98.49 %) figures reproduces its measured curve within 0.5 pp at
10–110 % load (`tests/test_inverter_fit.py`).

### 2.2a Supplied curves

If the spec has an `efficiency_curve` (and/or per-voltage `curve`), the OND
profile is written from those points as given, with `p_threshold_w` as the
first point. The loss model is then only fitted to the points for the
report. The CEC importer uses this path with curves evaluated from the
Sandia inverter model (see `docs/cec-import.md`).

### 2.3 What is written

- `ProfilPIO`: threshold point, then output at 5, 10, 20, 30, 50, 75 and
  100 % (plus `PMaxOUT` if larger), padded to 11 points as PVsyst does.
- If `efficiency_by_voltage` is given (3 voltages), also `VNomEff`,
  `EfficMaxV`, `EfficEuroV` and `ProfilPIOV1..3`.
- `PSeuil`/`PThreshEff` = `l0 × PNom`. `PNomDC` = input power at nominal
  output. `PInEffMax` = input power at the efficiency peak.
- AC currents are derived from power and voltage if not given.
  `TanPhiMin/Max` come from `power_factor_min`.

### 2.4 Known limitations

- Below ~10 % load the quadratic model is less accurate than a measured
  curve. The real curve rises faster, so energy at very low irradiance is
  slightly under-estimated.
- Without `efficiency_by_voltage`, one curve applies at all DC voltages.
- Power derating at high temperature is written only if supplied. Derating
  against DC voltage and altitude is not modelled.
