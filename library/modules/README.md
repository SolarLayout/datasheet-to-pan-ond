# Modules

19,832 PVsyst `.PAN` files generated from the CEC module list (NREL SAM library 2026.7.3, built 2026-10-05). Each `.PAN` has a `.yaml` spec next to it with the input values and `provenance_notes` listing every inferred or assumed value.

| Technology | Description | Files | Manufacturers |
|---|---|---:|---:|
| [mono-c-si](mono-c-si/README.md) | Monocrystalline silicon | 16,203 | 244 |
| [multi-c-si](multi-c-si/README.md) | Multicrystalline silicon | 3,523 | 94 |
| [cdte](cdte/README.md) | Cadmium telluride (thin film) | 98 | 3 |
| [cigs](cigs/README.md) | CIGS / CIS (thin film) | 2 | 1 |
| [a-si](a-si/README.md) | Amorphous / nanocrystalline silicon (thin film) | 6 | 1 |

- Machine-readable index of all files: [`index.csv`](index.csv)
- Records needing review (inferred values marked VERIFY): 1,148 (`needs_review = yes` in the index)
- Excluded records: 1,845 - see [`excluded.csv`](excluded.csv)
  - conversion: 297
  - duplicate: 36
  - fit: 1,512

## Notes

- Not in the CEC data and assumed: low-light (200 W/m2) efficiency (technology
  default), bifaciality factor (not written), whether cells are half-cut.
- Cell counts that are implausible in the CEC data are corrected from the model
  name, or estimated and flagged `needs_review`.
- `bifacial = yes`: CEC lists the module as bifacial (front-side model only).
