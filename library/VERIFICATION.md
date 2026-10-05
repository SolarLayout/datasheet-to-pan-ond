# Verification report

**PASSED** - `ds2panond library verify`, datasheet-to-pan-ond 0.1.0, 2026-10-05.

| Kind | Files verified |
|---|---:|
| modules | 19,832 |
| inverters | 2,343 |

## Checks

Per file (independent of the fitting code; files re-read from disk with pvlib):

- bytes: UTF-8 with BOM, CRLF line endings only, correct closing line, no NaN/inf;
- all keys PVsyst needs are present, numeric values finite;
- PAN: 0 < Imp < Isc, 0 < Vmp < Voc, plausible Voc per cell, Rp_0 > RShunt > 0,
  RSerie >= 0, Gamma 0.5-2.5; the PVsyst one-diode model rebuilt from the file
  reproduces Isc and Voc (0.2 %), passes through the datasheet MPP (0.5 %),
  gives Pmax within 0..+1 % of Vmp*Imp and the stated Pmax temperature
  coefficient (0.02 %/K);
- OND: VMppMin < VMPPMax <= VAbsMax, PMaxOUT >= PNomConv, every efficiency
  profile strictly increasing in P_in and P_out with efficiency 0-100 % (> 50 %
  above 10 % load) reaching nominal power, zero padding; EfficMax, EfficEuro,
  PNomDC and PSeuil consistent with the profile; voltage-dependent profiles valid;
- key values (model, cells, Isc/Voc/Imp/Vmp/Pnom; Pnom AC, MPPT and max DC
  voltage) match the YAML spec, which must validate against the input schema.

Library level: every file listed in `SHA256SUMS` with a matching checksum and vice versa; every file in
`index.csv` and linked from its folder README; every folder has a README; no
unexpected or temporary files.
