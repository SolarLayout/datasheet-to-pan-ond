# Waaree modules and inverters from Waaree's datasheets

Waaree publishes datasheets for its modules but no PVsyst files. The specs here are its current framed modules, one per
power bin, transcribed from the datasheets in its download center (Product Datasheet section). They were read on
2026-10-13 and fitted to `.PAN` files with this repository's `ds2panond`. Those files are in SolarLayout Rooftop's module
library (rooftop-design-app, pull request 248, decision D115).

| Series | Technology | Datasheets | Specs | Power |
|---|---|---:|---:|---|
| Elite (BiN-01 to BiN-17) | TOPCon, bifacial 0.80 | 8 | 62 | 415–715 W |
| Elite R (BiN-21 to BiN-36) | TOPCon, bifacial 0.80 | 5 | 36 | 435–635 W |
| Plexus (BiH-11) | HJT, bifacial 0.85 | 1 | 7 | 700–730 W |
| Ahnay (Bi-53 to Bi-58) | PERC, bifacial 0.70 | 6 | 42 | 380–590 W |
| Arka (WSMD, WSMDi) | PERC, monofacial | 2 | 12 | 395–550 W |
| **Total** | | **22** | **159** | |

`sources.csv` lists each datasheet: its kind (`module` or `inverter`), its PDF, series, technology, document number (`WEL/E&PD/…`, printed at the foot of
the datasheet) and URL. Each spec's `data_source` ends with that document number, and its first line names the PDF. The
PDFs are Waaree's and are not kept here.

## What comes from the datasheet, and what does not

From the datasheet: the STC values of each bin (Pmax, Vmp, Imp, Isc, Voc), the three temperature coefficients, the
average power reduction at 200 W/m² (4.5 % on every sheet, as `low_light_rel_eff_pct`), the maximum system voltage, the
size and weight, the cell count, and the bifaciality factor. Every module is half-cut: N cells are written as
`cells_in_series: N/2`, `cells_in_parallel: 2`, `half_cell: true`.

Assumed where a datasheet is silent, and written into `provenance_notes` (the PAN file's Remarks):

- **Bifaciality of Bi-53 and Bi-57:** 0.70. Both datasheets call the module bifacial but give no factor; the other Ahnay
  PERC datasheets (Bi-54, Bi-55, Bi-56, Bi-58) state 70 %.
- **Bypass diodes on the Ahnay and Arka sheets:** 3, since these datasheets do not state the count.
- **Power tolerance:** 0, since no sheet gives one (the spec's default).

Left out: the flexible WFLW panels (5–167 W), whose datasheets give the temperature coefficients only as a picture, and
revision 10 of BiN-08 (24.06.2025), which revision 12 (26.09.2025) replaces with the same electrical figures and a 30 mm
frame.

## How well they fit

All 159 fit without warnings (`ds2panond module --strict`). `tests/test_datasheets.py` checks that every spec is valid and names a datasheet in `sources.csv`, and fits the highest bin of each datasheet. Against the datasheets, power at STC is within
0.19 %, Isc and Voc are exact, and Imp and Vmp are within 1.5 % (the method keeps the MPP on the fitted curve,
`docs/methodology.md` §1.3). Cold Voc from the fitted model differs from the datasheet's coefficient at −10 °C by −0.9 to
−0.3 % for TOPCon and HJT, and about +1 % for PERC.

## Inverters

Waaree publishes no OND files either. `inverter-specs/` holds 38 on-grid string inverters, one spec per model, from
Waaree's inverter catalogue (download center, Brochures › Inverter, "Inverter Vr 01 / Oct 2025", published
2026-02-14). The catalogue's tables give some figures once for a family and others per model, so
`make_inverter_specs.py` holds them transcribed as data, family by family, rather than parsing the text.

| Family | Phases | Specs | DC max | MPPT window | MPPTs |
|---|---|---:|---|---|---|
| 1–3.3 kW (0.7 to 3.3 kW) | single | 7 | 500 V | 50–480 V | 1 |
| 3–6 kW | single | 6 | 600 V | 80–550 V | 2 |
| 3–25 kW | three | 12 | 1100 V | 140–1000 V | 2 |
| 30–75 kW | three | 7 | 1100 V | 200–1000 V | 3–6 |
| 75–136 kW | three | 6 | 1100 V | 200–1000 V | 9–10 |
| **Total** | | **38** | | | |

- **Names.** The catalogue names a model only by its rating, and two families share a rating (3 kW, 75 kW), so a model
  is named by phases, rating and family: `3P-75K (75-136 kW)`, file `Waaree_3P-75K_75-136kW`. Waaree's shop sells them
  as `W1-<kW>K…` (single-phase) and `W3-<kW>K` (three-phase); the catalogue does not print those names, so they are not
  used.
- **Current.** The catalogue gives the maximum input current per MPPT; `i_max_dc` is that times the MPPTs, as PVsyst's
  `IMaxDC` is the whole inverter's (the convention of makers' own OND files, e.g. Solis).
- **MPPT window.** The operating range (e.g. 200–1000 V), not the narrower full-load range the catalogue also prints, as
  makers' own OND files give it.
- **Efficiency.** Each spec has the catalogue's maximum and European efficiency; the curve is the loss-model fit, and it
  meets both figures exactly.
- **Assumed** (in `provenance_notes`): night loss 1 W where the catalogue does not state it (30–75 kW); 78 kVA as the
  maximum AC power of `3P-75K (75-136 kW)`, the lower of the catalogue's "78*/82.5" (the asterisk is not explained).
- **Not written:** `VmppNom`, the nominal MPP voltage (the catalogue's "rated input voltage": 360, 600 V), since the
  writer has no such key yet. SolarLayout Rooftop then takes the longest string as the nominal one.
- **Left out:** the 350 kW model. Its 480 V MPPT minimum leaves no string length under a 600 V system voltage, and its
  800 V AC output is for a plant with its own transformer, not a rooftop. The hybrid inverters (Waaree Tech, 3–15 kW)
  are not on-grid string inverters and are not transcribed.

Checked before use: every spec fits with `ds2panond inverter --strict`, and every OND file is read by SolarLayout
Rooftop's catalog reader and run through its options on roofs sized to it, at 1000 V and at 600 V, beside makers' own
files of the same size (Solis S5-GR3P 5K and 10K), which behave the same.

## Rebuilding

```bash
# 1. The PDFs, into a folder outside the repository
mkdir -p ../waaree-pdf && tail -n +2 datasheets/waaree/sources.csv | while IFS=, read -r _ pdf _ _ _ url; do curl -sfL -o "../waaree-pdf/$pdf" "$url"; done
# 2. Their text (poppler's pdftotext)
mkdir -p ../waaree-txt && for f in ../waaree-pdf/*.pdf; do pdftotext -layout "$f" "../waaree-txt/$(basename "${f%.pdf}").txt"; done
# 3. The specs
uv run python datasheets/waaree/make_specs.py ../waaree-txt datasheets/waaree/specs
# 4. The PAN files
mkdir -p out/waaree && for s in datasheets/waaree/specs/*.yaml; do uv run ds2panond module "$s" -o "out/waaree/$(basename "${s%.yaml}").PAN" --strict; done
# 5. The inverter specs (no text needed: the catalogue's figures are in the script) and the OND files
uv run python datasheets/waaree/make_inverter_specs.py datasheets/waaree/inverter-specs
for s in datasheets/waaree/inverter-specs/*.yaml; do uv run ds2panond inverter "$s" -o "out/waaree/$(basename "${s%.yaml}").OND" --strict; done
```

`make_specs.py` refuses a datasheet whose document number differs from `sources.csv`: a new revision is read and added
to `sources.csv` on purpose, never silently. A rebuilt PAN file differs from the one made on 2026-10-13 only in
`PriceDate`.
