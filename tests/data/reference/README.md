# Reference PVsyst files

These files are real PVsyst exports, copied unmodified from the
[pvlib-python](https://github.com/pvlib/pvlib-python) test suite
(`tests/data/`, BSD-3-Clause licence). They are the ground truth for the
on-disk format that this project writes, since PVsyst publishes no formal
specification.

| File | Producer | Notes |
|------|----------|-------|
| `ET-M772BH550GL.PAN` | PVsyst 7.2 | 550 W mono half-cell bifacial module, ASCII, LF |
| `CPS SCH275KTL-DO-US-800-250kW_275kVA_1.OND` | PVsyst 6.81 | 250 kW string inverter, UTF-8 with BOM, CRLF, 3 voltage-dependent efficiency profiles |

Tests use them to:

1. check that the writers emit the same key structure, and
2. check that the module fitter reproduces PVsyst's own one-diode
   parameters (`RSerie`, `Gamma`) from the STC values in the PAN file.

Do not edit these files. `.gitattributes` disables line-ending conversion
for them.
