# Inverters

2,343 PVsyst `.OND` files generated from the CEC inverter list (NREL SAM library 2026.7.3, built 2026-10-05). Each `.OND` has a `.yaml` spec next to it with the input values and `provenance_notes` listing every inferred or assumed value.

| Class | Description | Files | Manufacturers |
|---|---|---:|---:|
| [micro](micro/README.md) | Microinverters (<= 2 kW AC, or <= 120 V max DC) | 611 | 31 |
| [string](string/README.md) | String inverters, 2-100 kW AC | 1,392 | 117 |
| [string-utility](string-utility/README.md) | Utility-scale string inverters, 100-500 kW AC | 143 | 29 |
| [central](central/README.md) | Central inverters, >= 500 kW AC | 197 | 21 |

- Machine-readable index of all files: [`index.csv`](index.csv)
- Records needing review (inferred values marked VERIFY): 0 (`needs_review = yes` in the index)
- Excluded records: 0 - see [`excluded.csv`](excluded.csv)

## Notes

- Efficiency curves (main and at Mppt_low / Vdco / Mppt_high) are evaluated from the
  Sandia model parameters that SAM fitted to CEC laboratory measurements.
- Not in the CEC data and assumed: number of MPPTs (1), phase count (from AC
  voltage; 208 V may be single- or three-phase), power factor range (unity).
- `hybrid = yes`: CEC lists the unit as battery-capable (hybrid / PCS).
- Classes are by rated AC power; CEC does not classify inverters.
