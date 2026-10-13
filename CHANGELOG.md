# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `ds2panond cec search|export`: import modules and inverters from the CEC
  equipment lists (NREL SAM library CSVs, downloaded and cached), with bulk
  export, optional parallel PAN/OND build and an `_import_report.csv`.
- Cell-count sanity checks and inference for CEC module records (half-cell
  totals, model-name hints, statistical estimate flagged `VERIFY`).
- Inverter efficiency curves from Sandia model parameters (CEC test data),
  including voltage-dependent curves and start-up power.
- `efficiency_curve` / `p_threshold_w` inputs for inverters (measured or
  digitised curves are written to the OND directly).
- `provenance_notes` on module and inverter specs, carried into the file Remarks.
- `ds2panond library build|verify`: curated, resumable library build
  (technology / inverter-class / manufacturer folders, README index at every
  level, `index.csv`, `excluded.csv`, `SHA256SUMS`) and an independent
  verifier (pvlib re-read, physics and consistency checks, checksums, links).
- Committed `library/`: 19,832 PAN and 2,343 OND files from SAM library
  2026.7.3, all verified.

- `datasheets/waaree/`: 159 module specs transcribed from Waaree's 22 current
  datasheets (TOPCon, HJT, PERC), their sources, the script that writes them from
  the datasheets' text, and `tests/test_datasheets.py` (every spec fits without
  warnings and names a listed datasheet).
- `datasheets/waaree/inverter-specs/`: 38 on-grid inverter specs from Waaree's
  inverter catalogue (single-phase 0.7–6 kW, three-phase 3–136 kW), transcribed
  in `make_inverter_specs.py`; `sources.csv` gains a `kind` column.

- Validation against manufacturer PVsyst files for the same products
  (`docs/validation.md`, `tests/test_against_manufacturer.py`).

### Changed

- Python 3.12 is the minimum (was 3.10, which reaches end of life in
  October 2026); CI tests 3.12 and 3.13. `Technology` is a `StrEnum`; the
  written files are unchanged.

### Fixed

- OND MPPT voltages are rounded inwards to integers (they were rounded to
  nearest, which could widen the window); VNomEff is clamped into it.
- CEC inverter AC power is rounded to 1 W so `PNomConv` and the profile agree.
- Non-positive Sandia start-up power at extreme voltages falls back to `Pso`.
- PAN/OND and YAML files are written atomically.

## [0.1.0] - 2026-10-05

### Added

- Module datasheet → PVsyst one-diode fit (`fit_module`): RSerie fitted to
  low-light efficiency, Gamma/IL/I0 to the STC curve, muGamma to the Pmax
  temperature coefficient.
- Inverter datasheet → efficiency curve(s) via the 3-term loss model
  (`fit_inverter`), with optional voltage-dependent curves.
- PAN and OND writers that match the layout of PVsyst exports.
- `ds2panond` CLI with `module`, `inverter` and `inspect` commands and
  text/JSON fit reports.
- Test suite validated against real PVsyst PAN/OND reference files.
