# Importing from the CEC equipment lists

`ds2panond cec` turns records from the California Energy Commission (CEC)
module and inverter lists into YAML specs, and optionally into PAN/OND files.

## Data source

The CEC lists are taken from the copies NREL redistributes with the System
Advisor Model (SAM), which have a stable CSV schema:

- `CEC Modules.csv`: about 21,700 modules
- `CEC Inverters.csv`: about 2,300 inverters

Both are downloaded from the
[NREL/SAM repository](https://github.com/NREL/SAM/tree/develop/deploy/libraries)
on first use and cached in `~/.cache/datasheet-to-pan-ond/`. Use `--refresh`
to re-download, `--source FILE` to use a local copy, and the
`DS2PANOND_CACHE` environment variable to move the cache.

The CEC lists are US-centric. Products sold only in other markets, and
non-US variants of a product, may be missing.

## Usage

```bash
# find records (all words must appear in the name, case-insensitive)
uv run ds2panond cec search modules jinko 640
uv run ds2panond cec search inverters "SMA America" STP

# write YAML specs for the matches into a folder
uv run ds2panond cec export modules jinko 640 -o specs/modules

# ...and build the PAN/OND files next to them
uv run ds2panond cec export inverters "SMA America" STP -o specs/inverters --build

# the whole list, in parallel (modules take ~0.35 s each per core)
uv run ds2panond cec export modules --all -o specs/all-modules --build --jobs 0
```

Each export writes:

- `<Name>.yaml`: the spec, with a provenance header, and inferred values
  listed under `provenance_notes`. You can edit it and re-run
  `ds2panond module|inverter` on it.
- `<Name>.PAN` / `.OND` (with `--build`). Every provenance note is copied
  into the file's Remarks.
- `_import_report.csv`: one line per record with status (`exported`,
  `built`, `skipped`, `build_failed`, `build_refused`), the reason for any
  skip, fit warnings, and a `needs_review` flag.

`--strict` (with `--build`) skips writing files that have fit warnings.

## What the CEC data provides, and what is inferred

### Modules

| Spec field | From | Notes |
|---|---|---|
| STC values, `p_nom` | `I_sc_ref`, `V_oc_ref`, `I_mp_ref`, `V_mp_ref`, `STC` | |
| temperature coefficients | `alpha_sc`, `beta_oc` (converted to %/K), `gamma_pmp` | |
| technology | `Technology` | "Thin Film" records are skipped (ambiguous) |
| dimensions | `Length`, `Width` | often missing in newer records |
| `cells_in_series` | `N_s`, **checked** | see below |
| `cells_in_parallel`, `half_cell` | **inferred** | 1 unless N_s is a half-cell total |
| low-light efficiency | **not available** | technology default (−2.5 % mono) |
| bifaciality factor | **not available** | flagged in notes for bifacial modules |

**Cell count.** About 12 % of records have a listed `N_s` that can't be
right: the implied Voc per cell is outside 0.55–0.80 V (e.g. `N_s = 22` for
a 132 half-cell module). The importer resolves the count in this order:

1. listed `N_s`, if Voc per cell is plausible;
2. listed `N_s` read as a total half-cell count (144 → 72 × 2);
3. a cell count in the model name (`…-66HL4M`, `LR5-72HTH`). On records with a
   trustworthy `N_s` this agrees 99 % of the time;
4. a statistical estimate from Voc and SAM's CEC diode parameter `a_ref`,
   which is right about 92 % of the time. These records are marked
   **VERIFY** and get `needs_review = yes` in the report.

A wrong cell count mostly changes the PAN `Gamma` value, not the simulated
energy: the STC fit only depends on Gamma × NCelS, and `muGamma` is
re-fitted to the Pmax temperature coefficient anyway. It still should be
corrected for a clean file.

### Inverters

| Spec field | From | Notes |
|---|---|---|
| manufacturer, model | `Name` (`"Maker: Model {240V}"`) | voltage tag kept in the model name |
| `p_nom_ac_kw` | `Paco` | CEC max continuous AC power: can exceed the datasheet nominal (see `docs/validation.md`) |
| `v_ac` | `Vac` | |
| MPPT / max DC voltage | `Mppt_low`, `Mppt_high`, `Vdcmax` | full-power MPPT window, often narrower than the datasheet operating window |
| `i_max_dc` | `Idcmax` | from the Sandia parameter set |
| efficiency curves | Sandia parameters `Paco, Pdco, Vdco, Pso, C0–C3` | evaluated at 5–100 % load |
| voltage-dependent curves | Sandia model at `Mppt_low`, `Vdco`, `Mppt_high` | |
| start-up power | Sandia `Pso` | |
| night consumption | `Pnt` | 0 W if missing |
| `phases` | **inferred** from `Vac` | see note below |
| `n_mppt` | **not available** | 1 assumed |

The Sandia inverter model is fitted to CEC laboratory measurements at three
DC voltages. That makes these efficiency curves **better than
datasheet-derived ones**. The OND profile is written from the Sandia curve
directly (exact at the profile points), not from the loss-model fit.

Phase inference: 120, 240 and 277 V are treated as single-phase. 208, 480,
600 V and above are treated as three-phase. 208 V can be either (split from
a 208Y/120 service), so check the datasheet for 208 V single-phase
residential inverters.

## Results on the full lists (SAM library 2026.7.3)

These are the numbers for the committed library in [`library/`](../library/README.md),
built with `ds2panond library build`, which only keeps files that fit without
warnings and pass verification:

| | CEC records | In library | Excluded | Needs review |
|---|---:|---:|---|---:|
| Modules | 21,677 | 19,832 (91.5 %) | 1,845: 297 conversion (181 "Thin Film", 116 inconsistent data), 36 duplicate names, 1,512 fit warnings | 1,148 |
| Inverters | 2,343 | 2,343 (100 %) | 0 | 0 |

Almost all module fit exclusions are "Model Pmax deviates > 0.5 % from
Vmp·Imp". They mostly come from older or low-fill-factor modules, where the
default −2.5 % low-light target needs a large series resistance.

### Cross-check against NREL's CEC 6-parameter model

SAM's library carries NREL's own fit of the CEC 6-parameter (De Soto) model
for every module. It is a different model, fitted independently. For 400
random library modules, the maximum power from our PAN parameters compared
with the CEC model:

| Conditions | Median | 5th pct | 95th pct |
|---|---:|---:|---:|
| 1000 W/m², 25 °C | +0.03 % | 0.00 % | +0.29 % |
| 1000 W/m², 60 °C | −0.26 % | −0.88 % | +0.14 % |
| 600 W/m², 45 °C | −0.25 % | −1.38 % | +0.99 % |
| 200 W/m², 25 °C | −0.84 % | −3.12 % | +1.63 % |

The two models agree closely near STC and at high temperature. At low light
they differ by about 1 %, which reflects the different low-light assumptions:
neither model has measured low-light data in the CEC list.

Building all modules takes ~0.4 s per module per core (about 35 min on 4
cores). Builds can be resumed (`--time-budget`).
