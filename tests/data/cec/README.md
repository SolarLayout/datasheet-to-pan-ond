# CEC / SAM library fixtures

Small subsets of NREL System Advisor Model library files
(`deploy/libraries/CEC Modules.csv` and `CEC Inverters.csv`, SAM library
2026.7.3, [github.com/NREL/SAM](https://github.com/NREL/SAM), BSD-3-Clause).
The three header rows (names, units, SAM variable ids) are kept as-is.

Each row was chosen to cover one importer case:

| File | Record | Case |
|------|--------|------|
| Modules | Ablytek 6MN6A270 | clean 60-cell module |
| Modules | Jinko JKM640N-66HL4M-BDV | wrong N_s (22); cell count from model name |
| Modules | EMMVEE E530HCBG144 | N_s = 144 total half-cells |
| Modules | Trina TSM-295PE06H | wrong N_s (40), no name hint; statistical estimate |
| Modules | Advanced Solar Power ASP-S1-80 | technology "Thin Film" (unsupported) |
| Modules | First Solar FS-6385 | CdTe |
| Modules | Boviet BVM7612M-590-H-HC-BF-DG | Vmp·Imp inconsistent with STC (skipped) |
| Inverters | ABB PVI-30-OUTD-S-US-A {208V} | 208 V, three-phase by assumption |
| Inverters | SMA STP 20-US-50 {480V} | 480 V three-phase |
| Inverters | SMA SB70-1SP-US-41 {240V} | 240 V single-phase |
| Inverters | Guangzhou Sanjing H2-114K-LS4-US {208V} | missing Pnt |
