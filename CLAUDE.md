# CLAUDE.md

Guidance for AI coding agents (and humans) working in this repo.

## What this is

A Python library + CLI (`ds2panond`) that turns PV module / inverter
datasheet values into PVsyst `.PAN` / `.OND` files. It also holds a generated
library from the CEC lists (`library/`) and specs transcribed from real
makers' datasheets (`datasheets/`). Repository: `SolarLayout/datasheet-to-pan-ond`
on GitHub (private). SolarLayout Rooftop's module library takes PAN files made
here (Waaree's, from `datasheets/waaree/`).

## Read next, when the task needs it

| Read | When |
|---|---|
| `README.md` | Usage of the CLI and the Python API, the input format |
| `docs/methodology.md` | Any change to a fit, an assumed value or a default |
| `docs/file-format.md` | Any change to a writer, a PVsyst key, units or encoding |
| `docs/cec-import.md` | Any change to the CEC import or its data-quality heuristics |
| `docs/validation.md` | Comparing output with manufacturer PVsyst files |
| `docs/datasheets.md` | Adding or changing specs transcribed from a maker's datasheets (`datasheets/`) |
| `library/README.md` | The generated library's layout and policy |

## Commands

Always use `uv`; never `pip install` into the system Python.

```bash
uv sync                                   # set up / update .venv
uv run pytest                             # full test suite (about 25 s)
uv run ruff check . && uv run ruff format --check .
uv run mypy                               # strict; must pass
uv run ds2panond module examples/module_example.yaml -o out/x.PAN
uv run ds2panond cec export inverters "SMA America" STP -o out/cec --build
```

Before considering a change done, all four must pass: pytest, ruff check,
ruff format --check, mypy.

## Architecture

```
models.py  ──>  module_fit.py / inverter_fit.py  ──>  writers/{pan,ond}.py  ──>  file
 (input)          (physics, pure functions)            (format only)
                         │
                         └──>  report.py (text / JSON)
cli.py, io.py: thin glue

sources/cec.py ──> spec dict (validated against models.py) ──> io.dump_spec ──> YAML
sources/export.py: bulk orchestration (+ optional build via the pipeline above)
library/build.py ──> plan (classify, paths) ──> worker: fit + write + verify ──> index.py
library/verify.py: independent checks (pvlib re-read + physics), used per file and library-wide

datasheets/<maker>/make_specs.py: datasheet text (pdftotext) ──> spec dicts ──> io.dump_spec ──> specs/*.yaml
```

- **models.py**: pydantic v2, `extra="forbid"`, frozen. Datasheet units
  (%/K, W, V, A, m, kg). Cross-field validation lives in model validators.
- **module_fit.py**: the PVsyst one-diode fit. Uses pvlib for model
  evaluation. The procedure (RSerie fitted to low-light efficiency; MPP on
  the curve, not forced to be the maximum) is deliberate. It is the method
  that reproduces real PVsyst files; see methodology §1.3. Don't switch to a
  4-equation STC fit.
- **inverter_fit.py**: 3-term loss model; `l1` may be negative by design.
- **sources/cec.py**: maps SAM-format CEC CSV rows to spec dicts. Every
  inferred or defaulted value must add a line to `notes` (it becomes
  `provenance_notes`, which ends up in the file's Remarks). Mark low-confidence
  inferences with the word `VERIFY` (sets `needs_review` in the import
  report). Data-quality heuristics (cell-count inference, phase from Vac)
  are calibrated on the full lists. Re-check accuracy numbers in
  `docs/cec-import.md` if you change them.
- **writers/pvsyst_format.py**: generic tree (`Section`, `Entry`, `Blank`)
  and renderer. `pan.py` / `ond.py` map fit results onto PVsyst keys and units.

## Invariants: do not break

1. **The reference files are ground truth.** `tests/data/reference/*` are
   unmodified PVsyst exports. Never edit them. `.gitattributes` keeps their
   bytes exact. Key names, key order, quirks (`PInEffMax =`, `IsolSurvey =`,
   trailing commas, tab-separated OND comment) and units must match them.
   The structure tests in `tests/test_writers.py` enforce this.
2. **Units are converted in the writers only.** Models and fits use datasheet
   or SI units. PVsyst units (mA/K, mV/K, kW for OND powers, W for
   thresholds) appear only in `writers/`.
3. **Every assumption is surfaced.** If a value isn't from the datasheet,
   add a line to `assumptions` (it ends up in the file's `Remarks` and the
   report). Conditions that may make the file misleading go in `warnings`
   (`--strict` refuses to write when there are any).
4. **Physics changes need a reference check.** A change to a fit must keep
   `test_stc_solve_reproduces_pvsyst_gamma`,
   `test_low_light_fit_recovers_pvsyst_rserie` and
   `test_reference_curve_shape` passing, or explain in the PR why the
   reference comparison changed.
5. **No real manufacturer branding in examples.** Examples use fictional
   names ("Example Solar", "Example Power"). Test fixtures may contain real
   CEC records (public data, provenance in `tests/data/cec/README.md`).
   `datasheets/` is the one place for real products by name, each traced to
   the maker's datasheet (`docs/datasheets.md`).
6. **No network in tests.** CEC tests use `tests/data/cec/*.csv` via
   `--source` / `cec.load(kind, path)`. Never call `cec.fetch` in tests.

## The committed `library/` folder

- It is **generated output**. Never edit files in it by hand. Change the code,
  then rebuild with `ds2panond library build library --kind all` and run
  `ds2panond library verify library`, which must print PASSED. Commit the
  result together with the code change.
- Resumable builds key every record on its spec **and a fingerprint of all
  package `.py` files**. Any code change rebuilds everything (~35 min for
  modules on 4 cores; use `--time-budget` to run in chunks). Batch your code
  changes before rebuilding.
- `library/.build/` holds resumable state and is git-ignored.
- Inclusion policy: no fit warnings and verification passes. Don't loosen it
  to include more records. Fix the cause or leave the record in `excluded.csv`.

## The committed `datasheets/` folder

- Specs read from real makers' datasheets, one folder per maker. The specs are
  **output of the maker's `make_specs.py`**: never edit them by hand. Every
  figure is the datasheet's; every assumed one is in `provenance_notes`.
- Rules, the folder's files and how to add a maker: `docs/datasheets.md`.
- `tests/test_datasheets.py` checks every spec. A change to a fit that makes
  one of them warn is a change to discuss, not a test to loosen.

## Conventions

- Python ≥ 3.10, `from __future__ import annotations`, full type hints.
- Ruff (line length 100) and mypy strict are configured in `pyproject.toml`.
- Tests: pytest, one file per module; shared fixtures in `tests/conftest.py`.
  Write output to `tmp_path`, never into the repo.
- `out/` is git-ignored scratch space for generated files.
- Keep `CHANGELOG.md` updated under "Unreleased" for user-visible changes.
- Commit messages: imperative mood, short subject line.

## GitHub

- `origin` is `git@github.com:SolarLayout/datasheet-to-pan-ond.git`, private.
- Work on a branch and open a pull request into `main`. CI
  (`.github/workflows/ci.yml`) runs the four checks on Python 3.10 and 3.12
  and verifies the committed library. Merge on green.

## Adding PVsyst keys

1. Find the key in a real PVsyst export (add a new reference file under
   `tests/data/reference/` with provenance in its README if needed).
2. Add it to the writer in the same position as in the reference.
3. If it has no datasheet basis, put the PVsyst default in the writer's
   defaults dict (e.g. `PVSYST_DEFAULTS` in `writers/pan.py`) and document it.
4. Extend the structure/round-trip tests.
