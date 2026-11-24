# Specs from makers' datasheets

Each folder holds one maker's specs, read from its own datasheets. Waaree (`waaree/`) has its own scripts. Every other
maker has a `sheets.yaml` (its datasheets' figures as printed), from which `build_specs.py` writes the specs and
`sources.csv`. How to add a sheet, and the rules: [`../docs/datasheets.md`](../docs/datasheets.md).

## Ground-mount pilot (2026-11-20)

The first sheets for SolarLayout Desktop's equipment catalogue: utility-scale modules and inverters sold in India.
These are 18 datasheets: 11 module sheets from 11 makers and 7 inverter sheets from 5 makers. They give 81 module power
ratings and 14 inverter models.

| Folder | Sheet | Specs |
|---|---|---:|
| `adani-solar` | Elan Shine TOPCon Gen-II, 550–580 W | 7 |
| `avaada` | Enlume AVN66G12G glass-glass, 700–720 W | 5 |
| `ja-solar` | DeepBlue 4.0 JAM66D45 LB, 615–640 W | 6 |
| `jinkosolar` | Tiger Neo 3.0 JKM-N-66QL6-BDV, 650–670 W | 5 |
| `longi` | Hi-MO 9 LR8-66HYD (India), 625–660 W | 8 |
| `premier-energies` | PE-132 G12R TOPCon, 600–630 W | 7 |
| `rayzon-solar` | RS132TGC TOPCon 210R (domestic), 610–625 W | 4 |
| `renew` | RPS3MH66BD G12R TOPCon, 615–640 W | 6 |
| `saatvik` | Tej Plus N-TOPCon G12R, 600–640 W | 9 |
| `trina-solar` | Vertex TSM-NEG19RC.20, 625–650 W | 6 |
| `vikram-solar` | Hypersol G12R, 620–650 W | 7 |
| `sungrow` | SG320HX string; SG3300UD, SG4400UD central | 3 |
| `huawei` | SUN2000-330KTL H1 and H2 string | 2 |
| `sineng` | SP-350K-INHB string; EP-3125-HC-UD, EP-3400-HA-UD central | 3 |
| `solis` | S6-GU3P 275K and 350K string | 2 |
| `sma` | Sunny Central 4000, 4200, 4400 and 4600 UP | 4 |

### Samples for SolarLayout Desktop

SolarLayout Desktop offers a few of these as **samples** for draft layouts, named by what they are and never by
the maker ("Sample 630 Wp bifacial module", "Sample 320 kW string inverter"). The manufacturers' own PAN and OND
files will replace them when they arrive.

- `samples.yaml` names the spec each sample's values come from.
- `build_samples.py` writes `samples/`: one PAN or OND file per sample, plus `index.json` (id, kind, file, SHA-256).
  It replaces the manufacturer with "Sample", the model with the sample's name and the data source with "Typical
  values from published datasheets", and drops the sheet's notes.
- The app copies `samples/` as it is. Its tests fail if a maker's name, a model code or a URL appears in a sample.

There are 8 modules (550 to 720 Wp in three sizes, all bifacial) and 9 inverters: 4 string inverters (275 to 350 kW)
and 5 central inverters (3,125 to 4,600 kW). No two samples share a power, kind and size. Which datasheet a sample
comes from (`samples.yaml`) is for the team only; it never ships.

### How they were checked

Every figure is checked against its PDF by `build_specs.py --check`:

- **Unchanged file.** The PDF's SHA-256 matches the one in `sheets.yaml`.
- **Tables and figures.** Each bin column or per-model list appears in order on a line of the PDF's text, and every single figure appears in the text.
- **Plausible bins.** Power rises from bin to bin, and Vmp × Imp is within 0.6 % of rated power.
- **Valid specs and clean fits.** Every spec passes the spec's own rules, and every file fits with `--strict`, with no warnings.

Figures that exist only as an image were read twice, by two readers working independently. The two readings agreed on every figure. These are the whole of `premier-energies` and `saatvik`, and ReNew's Vmp row, where the text fuses a figure with a drawing dimension. The sheets mark them with `unchecked` or `read_by_eye`.

SolarLayout Desktop's own PAN and OND readers then read all 95 files. Each one gives back the spec's power, voltages,
currents, size, temperature coefficients, maximum voltages, MPPT window and efficiencies.

### Choices made where a sheet is silent or gives several figures

Each choice is written into the file's Remarks; the sheets' `notes` say which applies where.

- **Half cells.** Avaada and LONGi print 132 cells without saying they are half cells. They are taken as 66 × 2, because
  the Voc could not come from 132 cells in series. Jinko prints 264 (66 × 4): quarter-cut cells.
- **Technology.** JA Solar, Premier and Vikram say "n-type" without naming the cell structure, so TOPCon is recorded.
  It is the same PVsyst technology (crystalline mono) as any non-HJT cell.
- **ReNew prints no maximum system voltage.** Its files give none; the app then uses the system voltage.
- **Vikram's bifaciality** is printed as 75 ± 5 % for Pmax and as "up to 80 %" in its feature list; 0.75 is used.
- **Inverter AC power.** Each model's nominal power is the rating it is named after, at the sheet's ambient
  temperature for that rating, taken as kW at cos φ = 1 when printed in kVA:
  - Sungrow SG320HX: 320 kW at 40 °C.
  - Sineng SP-350K-INHB: 352 kW at 30 °C.
  - Solis: 275 kW at 40 °C and 350 kW at 30 °C.
  - Sungrow central: 3300 and 4400 kW at 45 °C.
  - Sineng central: 3125 kW at 50 °C and 3400 kW at 45 °C.
  - SMA: 35 °C.
  - Huawei H1: its nominal 300 kW (the 330 is its maximum).
- **SMA:**
  - One MPPT, which the sheet does not state.
  - The MPPT window is the one printed for 25 °C.
  - The CEC efficiency is left out: it is measured on a different basis from the maximum and European figures.

### Open questions for review

1. **Inverter rating at Indian site temperatures.** Sheets rate an inverter at several ambient temperatures; Sineng's "350 kW", for example, is 352 kW at 30 °C but 305 kW at 50 °C. Should the catalogue use the named rating, as here, or the rating at a hotter ambient?
2. **MPPT window.** Some sheets print a full MPPT range and a narrower "range for nominal power": Sineng SP-350K-INHB gives 500–1500 V and 880–1300 V. The full range is used, and Sineng's central sheet prints only the narrower one. Which should string sizing use?
3. **The choices above.** Do the half-cell readings, Vikram's bifaciality and SMA's single MPPT stand?
