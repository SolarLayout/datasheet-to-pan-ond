# datasheet-to-pan-ond

Generate PVsyst **`.PAN`** (PV module) and **`.OND`** (inverter) files from
manufacturer datasheet values.

- Fits PVsyst's one-diode module model with
  [pvlib](https://pvlib-python.readthedocs.io), so it reproduces the
  `RSerie`/`Gamma` pairs found in manufacturer PAN files.
- Builds inverter efficiency curves from max / European / CEC efficiencies,
  optionally at three DC voltages, or from supplied curve points.
- Imports from the public **CEC module and inverter lists** (about 21,700
  modules and 2,300 inverters, via NREL's SAM library), so you don't have to
  type in datasheet values.
- Ships a **ready-made, verified library** in [`library/`](library/README.md):
  19,832 PAN and 2,343 OND files, organised by technology / inverter class
  and manufacturer, with README indexes, `index.csv` files and checksums.
- Holds **specs transcribed from makers' own datasheets** in
  [`datasheets/`](datasheets/): Waaree's current modules (159 power bins from
  22 datasheets) and on-grid inverters (38 models from its catalogue), each
  traced to its source, with every assumption stated.
- Writes files in the same layout as PVsyst's own exports. Every assumed
  value is recorded in the file's `Remarks` and in a fit report.

> **Status: prototype (v0.1).** The model fits are tested against real PVsyst
> files. The generated files have **not yet been import-tested in PVsyst
> itself**; see [Validation](#validation). Datasheet-derived files are
> estimates. Use measured PAN files for bankable studies.

## Quick start

Requires [uv](https://docs.astral.sh/uv/) (Python ≥ 3.12).

```bash
uv sync                                     # create .venv with dependencies
uv run ds2panond module   examples/module_example.yaml   -o out/EXS-144M-550.PAN
uv run ds2panond inverter examples/inverter_example.yaml -o out/EXP-100K-HV.OND
uv run ds2panond inspect  out/EXS-144M-550.PAN           # parse any PAN/OND to JSON
```

Each command prints a fit report: fitted parameters, target versus model
values, assumptions and warnings. Useful options:

| Option | Meaning |
|--------|---------|
| `-o PATH` | Output file (default: spec name with `.PAN`/`.OND`) |
| `--report-json PATH` | Write the fit report as JSON |
| `--strict` | Exit with code 2 and write nothing if the fit produced warnings |
| `--encoding ENC` / `--lf` | Change the output encoding (default UTF-8 with BOM) or line endings (default CRLF) |

### From the CEC equipment lists (no typing)

```bash
uv run ds2panond cec search modules jinko 640                       # find records
uv run ds2panond cec export modules jinko 640 -o specs/modules --build
uv run ds2panond cec export inverters "SMA America" STP -o specs/inverters --build
```

This writes one YAML spec per record (editable, with inferred values listed
under `provenance_notes`), the PAN/OND files, and an `_import_report.csv`
that flags records needing review. Inverter curves come from the CEC
laboratory test data (Sandia model). Module low-light performance is not in
the CEC data, so a technology default is used. Details, data-quality handling
and coverage: [`docs/cec-import.md`](docs/cec-import.md).

### The generated library

[`library/`](library/README.md) is browsable on GitHub: `library/README.md`, then
`<kind>/README.md`, then a class or technology, then a manufacturer, then the files.

```
library/modules/<mono-c-si|multi-c-si|cdte|cigs|a-si>/<maker>/<model>.PAN + .yaml
library/inverters/<micro|string|string-utility|central>/<maker>/<model>.OND + .yaml
```

Rebuild or verify it:

```bash
uv run ds2panond library build library --kind all --jobs 0   # resumable; add --time-budget N
uv run ds2panond library verify library --jobs 0             # independent checks, writes VERIFICATION.md
```

Only records that fit without warnings and pass verification are included.
Everything else is listed in `<kind>/excluded.csv` with the reason.

### From a maker's datasheets

For makers that publish datasheets but no PVsyst files, `datasheets/<maker>/`
holds one spec per power bin read from the datasheets, the list of datasheets
used (`sources.csv`, with document numbers and URLs) and the script that writes
the specs from the datasheets' text. Build the PAN files from them as from any
spec:

```bash
uv run ds2panond module datasheets/waaree/specs/Waaree_BiN-08-580.yaml -o out/Waaree_BiN-08-580.PAN --strict
```

What may go into a spec, and how to add a maker: [`docs/datasheets.md`](docs/datasheets.md).
Waaree's sources, assumptions and fit: [`datasheets/waaree/README.md`](datasheets/waaree/README.md).

### Using the Python API

```python
from datasheet_to_pan_ond import fit_module, fit_inverter
from datasheet_to_pan_ond.io import load_module
from datasheet_to_pan_ond.writers import write_pan

result = fit_module(load_module("examples/module_example.yaml"))
print(result.params, result.assumptions, result.warnings)
write_pan(result, "out/module.PAN")
```

## Input format (hand-written specs)

One YAML (or JSON) file per module power bin or inverter model. Units
follow datasheet conventions (W, V, A, %/K). The full schema with
descriptions is in
[`src/datasheet_to_pan_ond/models.py`](src/datasheet_to_pan_ond/models.py).
Unknown keys are rejected, so typos fail loudly.

**Module.** Required: `manufacturer`, `model`, `cells_in_series`, `p_nom`,
`isc`, `voc`, `imp`, `vmp`, `temp_coeff_isc_pct`, `temp_coeff_voc_pct`,
`temp_coeff_pmax_pct`. Strongly recommended: `low_light_rel_eff_pct`
(relative efficiency at 200 W/m²). Without it, a technology default is
assumed. See [`examples/module_example.yaml`](examples/module_example.yaml)
and [`examples/module_minimal.yaml`](examples/module_minimal.yaml).

For half-cut modules, a 144 half-cell module is `cells_in_series: 72`,
`cells_in_parallel: 2`, `half_cell: true`.

**Inverter.** Required: `manufacturer`, `model`, `p_nom_ac_kw`, `v_ac`,
`v_mpp_min`, `v_mpp_max`, `v_dc_max`, `i_max_dc`, `eff_max_pct`, and at least
one of `eff_euro_pct` / `eff_cec_pct`. See
[`examples/inverter_example.yaml`](examples/inverter_example.yaml) and
[`examples/inverter_minimal.yaml`](examples/inverter_minimal.yaml).

The inputs are validated: Vmp·Imp must be within 2 % of Pnom, the fill
factor must be plausible, weighted efficiencies must be below max, the MPPT
range must lie within the max DC voltage, and so on.

## How it works (short)

| | Datasheet gives | Assumed | Fitted |
|---|---|---|---|
| Module | STC values, temp. coefficients, cells | RShunt (100·Vmp/Imp), Rp_0 = 4·RShunt, Rp_Exp = 5.5, low-light efficiency if absent | RSerie → low-light efficiency; Gamma, IL, I0 → STC curve through Isc, Voc, MPP; muGamma → Pmax temp. coeff. |
| Inverter | max / Euro / CEC efficiency, voltages, currents | efficiency peak near 50 % load when only 2 figures given | 3-term loss model l0 + l1·p + l2·p² → efficiency curve(s) |

Full details and limitations: [`docs/methodology.md`](docs/methodology.md).
File format notes: [`docs/file-format.md`](docs/file-format.md).

## Validation

What the test suite checks (`uv run pytest`):

- **Against a real PVsyst PAN** (ET Solar 550 W): given its `RShunt`/`RSerie`,
  we recover its `Gamma` (0.979 vs 0.980). Given its low-light efficiency,
  we recover its `RSerie` to within 1e-4 Ω.
- **Against a real PVsyst OND** (CPS 250 kW): fitting only its max and Euro
  figures reproduces its measured efficiency curve within 0.5 pp between
  10 % and 110 % load.
- Generated files have the same key set and key order as the reference
  exports, parse with `pvlib.iotools.read_panond`, and round-trip all values.
- Fit targets (Isc, Voc, muPmp, low-light, efficiencies) are met.

- **Against manufacturer files for the same product** (ET Solar 550 W PAN, CPS
  275 kW OND): the CEC-derived files match within 0.1 % at STC and low light,
  and within 0.45 pp in inverter efficiency. The remaining differences are
  explained in [`docs/validation.md`](docs/validation.md).

**Not yet done:** importing the generated files into PVsyst (7.x/8.x) and
comparing a simulation against the same module/inverter from a
manufacturer-supplied PAN/OND. That is the next milestone; see the open
questions in [`docs/file-format.md`](docs/file-format.md).

## Development

```bash
uv sync                       # installs runtime + dev dependencies
uv run pytest                 # tests (with --cov=datasheet_to_pan_ond for coverage)
uv run ruff check . && uv run ruff format --check .
uv run mypy                   # strict type checking of src/
```

Project layout:

```
src/datasheet_to_pan_ond/
  models.py         input schemas (pydantic) + validation
  module_fit.py     PVsyst one-diode fit (pvlib)
  inverter_fit.py   inverter loss model / efficiency curves
  writers/          PVsyst text format: generic serialiser, PAN, OND
  report.py         text / JSON fit reports
  sources/cec.py    CEC/SAM database -> spec mapping (with data-quality checks)
  sources/export.py bulk export / build with import report
  library/          curated library: build (resumable), index (READMEs/CSV/sums), verify
  io.py, cli.py     YAML/JSON loading and dumping, `ds2panond` CLI
tests/              pytest suite; tests/data/reference = real PVsyst files,
                    tests/data/cec = CEC/SAM CSV subsets
examples/           example datasheet specs (fictional makers)
library/            generated, verified PAN/OND library (committed)
datasheets/<maker>/ specs transcribed from a maker's datasheets, their sources and script
docs/               methodology, file format, CEC import, validation, datasheets
```

## Roadmap

- [ ] Import-test generated files in PVsyst; fix any format issues
- [x] Import from the CEC module / inverter lists
- [ ] Datasheet PDF extraction (LLM or table parsing) into the YAML schema,
      with a human review step (done per maker, by table parsing, for Waaree:
      `datasheets/waaree/`)
- [ ] Multiple power bins from one datasheet spec
- [ ] Fit to IEC 61853-1 matrix data when available
      (`pvlib.ivtools.sdm.fit_pvsyst_iec61853_sandia_2025`)
- [ ] Bifacial and thin-film specific PAN parameters

## Acknowledgements

Reference PVsyst files in `tests/data/reference/` come from the
[pvlib-python](https://github.com/pvlib/pvlib-python) test suite
(BSD-3-Clause). CEC equipment data comes from the CEC via the
[NREL SAM](https://github.com/NREL/SAM) library (BSD-3-Clause). PVsyst is a trademark of PVsyst SA. This project is not
affiliated with PVsyst SA.
