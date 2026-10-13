"""Generic writer for the PVsyst object text format used by PAN/OND files.

PVsyst publishes no specification. The structure below is inferred from
real PVsyst exports (see ``tests/data/reference``):

* one ``key=value`` per line, nested blocks indented by two spaces;
* blocks open with a header line and close with an explicit footer, e.g.
  ``PVObject_Commercial=pvCommercial`` ... ``End of PVObject pvCommercial``,
  ``ProfilPIO=TCubicProfile`` ... ``End of TCubicProfile``,
  ``Remarks, Count=2`` ... ``End of Remarks``;
* decimal point is ``.``; lists are comma separated;
* PVsyst 6.8+ writes UTF-8 with a BOM and CRLF line endings.

Keys are emitted verbatim (including PVsyst quirks such as ``IsolSurvey =``).
"""

from __future__ import annotations

import datetime as dt
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

INDENT = "  "


@dataclass(frozen=True)
class FileFormat:
    """Byte-level output options."""

    encoding: str = "utf-8-sig"  # UTF-8 with BOM, as PVsyst 6.8+/7 writes
    newline: str = "\r\n"


DEFAULT_FORMAT = FileFormat()


@dataclass
class Blank:
    """An empty separator line."""


@dataclass
class Section:
    header: str
    footer: str
    items: list[Item] = field(default_factory=list)

    def add(self, key: str, value: object) -> Section:
        self.items.append(Entry(key, value))
        return self

    def add_opt(self, key: str, value: object | None) -> Section:
        if value is not None:
            self.items.append(Entry(key, value))
        return self

    def extend(self, items: Iterable[Item]) -> Section:
        self.items.extend(items)
        return self


@dataclass
class Entry:
    key: str
    value: object  # pre-formatted str, or a value passed through ``str``

    def render(self) -> str:
        return f"{self.key}={self.value}"


Item = Entry | Section | Blank


def pvobject(key: str, cls: str, items: Iterable[Item] = ()) -> Section:
    """``key=cls`` ... ``End of PVObject cls``."""
    return Section(f"{key}={cls}", f"End of PVObject {cls}", list(items))


def cubic_profile(
    key: str,
    points: Sequence[tuple[str, str]],
    *,
    mode: int,
    last_compile: str,
    n_pts_max: int | None = None,
) -> Section:
    """A ``TCubicProfile`` block. ``points`` are pre-formatted (x, y) strings.

    PVsyst pads unused slots up to ``NPtsMax`` with ``0,0`` points.
    """
    n_max = n_pts_max if n_pts_max is not None else len(points)
    if n_max < len(points):
        raise ValueError("n_pts_max smaller than number of points")
    sec = Section(f"{key}=TCubicProfile", "End of TCubicProfile")
    sec.add("NPtsMax", n_max).add("NPtsEff", len(points))
    sec.add("LastCompile", last_compile).add("Mode", mode)
    padded = list(points) + [("0", "0")] * (n_max - len(points))
    for i, (x, y) in enumerate(padded, start=1):
        sec.add(f"Point_{i}", f"{x},{y}")
    return sec


def remarks(lines: Sequence[str]) -> Section:
    """``Remarks, Count=N`` block with ``Str_i`` lines."""
    sec = Section(f"Remarks, Count={len(lines)}", "End of Remarks")
    for i, text in enumerate(lines, start=1):
        # Commas are legal in PVsyst remarks but parsers (incl. pvlib) split on them.
        sec.add(f"Str_{i}", sanitize(text).replace(",", ";"))
    return sec


def sanitize(text: str) -> str:
    """Make free text safe for the line-oriented ``key=value`` format.

    Collapses whitespace/newlines and replaces ``=`` (which would be read as a
    key/value separator) with ``:``.
    """
    text = " ".join(text.split())
    return text.replace(" = ", ": ").replace("=", ":")


def num(value: float, decimals: int) -> str:
    """Fixed-point number formatting with ``.`` decimal separator."""
    if decimals == 0:
        return str(round(value))
    return f"{value:.{decimals}f}"


def price_date(now: dt.datetime | None = None) -> str:
    """PVsyst ``PriceDate`` stamp, ``dd/mm/yy hh:mm``."""
    return (now or dt.datetime.now()).strftime("%d/%m/%y %H:%M")


def render_lines(section: Section, level: int = 0) -> list[str]:
    pad = INDENT * level
    out = [pad + section.header]
    for item in section.items:
        if isinstance(item, Blank):
            out.append("")
        elif isinstance(item, Section):
            out.extend(render_lines(item, level + 1))
        else:
            out.append(INDENT * (level + 1) + item.render())
    out.append(pad + section.footer)
    return out


def render(section: Section, fmt: FileFormat = DEFAULT_FORMAT) -> str:
    return fmt.newline.join(render_lines(section)) + fmt.newline


def write(section: Section, path: str | Path, fmt: FileFormat = DEFAULT_FORMAT) -> Path:
    """Write atomically (temp file + rename), so an interrupted run leaves no partial file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    # newline="" so Python does not translate our explicit line endings.
    with tmp.open("w", encoding=fmt.encoding, newline="") as fh:
        fh.write(render(section, fmt))
    os.replace(tmp, path)
    return path
