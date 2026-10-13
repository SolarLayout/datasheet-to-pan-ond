# PVsyst PAN / OND file format notes

PVsyst SA publishes **no specification** for `.PAN` (module) and `.OND`
(inverter) files. Everything here is inferred from real PVsyst exports,
kept unmodified in `tests/data/reference/`. When in doubt, the reference
files win.

## Encoding and line endings

| Producer | Encoding | Line ending |
|----------|----------|-------------|
| PVsyst 6.81 (reference OND) | UTF-8 with BOM | CRLF |
| PVsyst 7.2 (reference PAN, as stored in pvlib's git) | ASCII | LF (probably normalised by git) |

We write **UTF-8 with BOM and CRLF** by default (`--encoding`, `--lf` to
change). Files from PVsyst before v6 used a different, non-text layout and
are not supported.

## Grammar

```
file     := block
block    := header NL { item NL } footer
item     := key "=" value | block | ""        (blank lines allowed)
indent   := two spaces per nesting level
```

Block header/footer pairs:

| Header | Footer |
|--------|--------|
| `PVObject_=pvModule` / `PVObject_=pvGInverter` | `End of PVObject pvModule` / `... pvGInverter` |
| `PVObject_Commercial=pvCommercial` | `End of PVObject pvCommercial` |
| `PVObject_IAM=pvIAM` | `End of PVObject pvIAM` |
| `Converter=TConverter` | `End of TConverter` |
| `<Name>=TCubicProfile` | `End of TCubicProfile` |
| `Remarks, Count=N` | `End of Remarks` |

Values:

- Numbers use `.` as decimal separator and no thousands separator.
- Lists are comma separated. Voltage lists in OND files have a trailing
  comma (`VNomEff=880.0,1174.0,1300.0,`).
- `$XXXX` values (`Flags`, `LastCompile`) are hexadecimal bit fields with
  unknown meaning. We copy the values PVsyst writes.
- Some keys contain a space before `=`: `PInEffMax =`, `IsolSurvey =`.
  We reproduce these exactly.
- The OND top-level `Comment` is `Manufacturer<TAB>Model<TAB>DataSource`.
- `PriceDate` is `dd/mm/yy hh:mm`.

## Cubic profiles

```
ProfilPIO=TCubicProfile
  NPtsMax=11          # slots
  NPtsEff=9           # used points
  LastCompile=$8085
  Mode=1              # 1 for efficiency curves, 3 for IAM
  Point_1=1250,0      # x,y
  ...
  Point_10=0,0        # unused slots padded with 0,0
End of TCubicProfile
```

For `ProfilPIO*`, x = DC input power [W] and y = AC output power [W]. The
first point is the threshold (output 0). For `IAMProfile`, x = angle [deg]
and y = IAM.

## Units (PVsyst internal)

| Key | Unit |
|-----|------|
| `muISC` | mA/K |
| `muVocSpec` | mV/K |
| `muPmpReq` | %/K |
| `muGamma` | 1/K |
| `RShunt`, `Rp_0`, `RSerie` | Ω |
| `PNom` (PAN) | W |
| `PNomConv`, `PMaxOUT`, `PNomDC`, `PMaxDC`, `PLim1`, `PLimAbs` (OND) | kW |
| `PSeuil`, `PThreshEff`, `PInEffMax`, `Night_Loss` (OND) | W |
| `Width`, `Height`, `Depth` | m |
| `Weight` | kg |
| `CellArea` | cm² |

## Keys written only when the datasheet gives them

Beyond the reference exports, a few keys appear only when the input has a
value for them; a structure test allows each one by name
(`tests/test_writers.py`, `OPTIONAL_CONVERTER`).

| Key | From | Where | Format |
|-----|------|-------|--------|
| `VmppNom` (OND converter) | `v_mpp_nom`: the datasheet's rated or nominal DC input voltage | between `VMPPMax` and `VAbsMax` | V, one decimal (`VmppNom=620.0`) |

`VmppNom` is not in the reference OND (CPS 275 kW, from pvlib's tests). Its
place and format are those of makers' own exports: in 338 of 388 manufacturer
OND files written by PVsyst 6.4 to 8.1 (SolarLayout's internal inverter
library, not redistributed here) it follows `VMPPMax`, and in 274 of them
`VAbsMax` follows it (the others put `Vmpp_PMax` between them, a key this
writer does not produce). Example: FIMER PVM-75-TL, PVsyst 8.1.5,
`VMPPMax=1000`, `VmppNom=620.0`, `VAbsMax=1100`. Readers use it as the
inverter's nominal string voltage (SolarLayout Rooftop sizes its nominal
string length to it).

## Reading

`pvlib.iotools.read_panond(path, encoding="utf-8-sig")` parses both file
types into nested dicts. `ds2panond inspect FILE` wraps it.

## Open questions (verify against PVsyst)

- [ ] Does PVsyst 7.x/8.x accept the files without warnings? (Import test.)
- [ ] Is a `Remarks` block valid inside a PAN `pvCommercial` (the reference
      PAN has none; the reference OND does)?
- [ ] Do `Flags` values need to change for optional features (bifacial,
      voltage-dependent curves)?
- [ ] Does PVsyst recompute `LastCompile` or use it to validate profiles?
