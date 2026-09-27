"""The xlsx writer: a checked ``.yup`` file as an xlsx workbook of live formulas.

**The app must open every file this writes without complaint.** So the writer produces a
small subset of the format, reliably, and refuses what it cannot write with confidence.
Formula cells carry no cached value: the app computes everything when it opens the file.

The output is deterministic: the same ``.yup`` gives the same bytes.

>>> from yupana.yup import read_yup
>>> yup = read_yup("sheet\\trow\\tcol\\tcell\\tformat\\nModel\\t1\\t1\\t#1\\tcolumnwidth=10\\n")
>>> data = write_xlsx(yup.unwrap()).unwrap()
>>> data[:2], data == write_xlsx(yup.unwrap()).unwrap()
(b'PK', True)
"""

import io
import math
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from yupana.numfmt import number_format_id
from yupana.result import Err, Ok, Result
from yupana.yup import (
    ESCAPE_SHAPE,
    Cell,
    Default,
    Formula,
    Logical,
    Number,
    Text,
    Yup,
    quoted,
)

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
RELATIONSHIPS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"
_SHEETML = "application/vnd.openxmlformats-officedocument.spreadsheetml"
_DECLARATION = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
_EPOCH = (1980, 1, 1, 0, 0, 0)

# The maximum digit width, in pixels, of the default font at 100% display scaling.
MAX_DIGIT_WIDTH = 7
DEFAULT_ROW_HEIGHT = "15"


@dataclass(frozen=True, slots=True)
class WriteError:
    """A cell the writer refuses to write, or a file it could not write."""

    line: int | None
    message: str

    def __str__(self) -> str:
        return (
            self.message if self.line is None else f"line {self.line}: {self.message}"
        )


def column_letters(col: int) -> str:
    """A column's letters, in bijective base 26.

    >>> column_letters(1), column_letters(26), column_letters(27), column_letters(16384)
    ('A', 'Z', 'AA', 'XFD')
    """
    assert col >= 1, f"no column {col}"
    letters = ""
    while col:
        col, remainder = divmod(col - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def address(row: int, col: int) -> str:
    """A cell's A1 address.

    >>> address(2, 28)
    'AB2'
    """
    return f"{column_letters(col)}{row}"


def escape_text(text: str) -> str:
    """Text as element content: the ``_xHHHH_`` rule, then XML escaping.

    xlsx decodes ``_x`` followed by four hexadecimal digits and ``_`` as a character, so
    that exact shape, and only it, has its ``_x`` written as ``_x005F_x``. The app
    decodes it in shared strings and in formula text alike.

    >>> escape_text("_x000D_ & max_x < a_xb")
    '_x005F_x000D_ &amp; max_x &lt; a_xb'
    """
    unshaped = ESCAPE_SHAPE.sub(lambda shape: "_x005F" + shape.group(), text)
    return unshaped.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def escape_attribute(text: str) -> str:
    """Text as an attribute value in double quotes: as element content, and ``"`` too.

    The app decodes the ``_xHHHH_`` shape in a sheet name and in a number format code as
    well, so the same rule applies.

    >>> print(escape_attribute('It\\'s "here" & <there> _x0041_'))
    It's &quot;here&quot; &amp; &lt;there&gt; _x005F_x0041_
    """
    return escape_text(text).replace('"', "&quot;")


def stored_width(width: float) -> float:
    """The width a file stores for a column ``width`` characters wide, as the app's
    interface shows it: padding added, snapped to whole pixels.

    >>> [stored_width(w) for w in (0.5, 1, 1.5, 2, 5, 10, 12.5, 20, 255)]
    [0.85546875, 1.7109375, 2.28515625, 2.7109375, 5.7109375, 10.7109375, \
13.28515625, 20.7109375, 255.7109375]
    """
    if width >= 1:
        pixels = math.trunc(width * MAX_DIGIT_WIDTH + 0.5) + 5
    else:
        pixels = math.trunc(width * (MAX_DIGIT_WIDTH + 5) + 0.5)
    return math.trunc(pixels * 256 / MAX_DIGIT_WIDTH) / 256


def _where(cell: Cell) -> str:
    return f"sheet {quoted(cell.sheet)}, row {cell.row}, col {cell.col}"


@dataclass(frozen=True, slots=True)
class _Styles:
    """The number formats and cell styles a workbook uses, in order of first use."""

    custom: dict[str, int]
    keys: dict[tuple[int, int], int]


def _style(cell: Cell, styles: _Styles) -> Result[int, str]:
    """A cell's index in ``cellXfs``, allocating its number format and style if new."""
    number_format = 0
    if cell.format.number_format is not None:
        match number_format_id(cell.format.number_format, styles.custom):
            case Err(reason):
                return Err(reason)
            case Ok(found):
                number_format = found
    key = (number_format, cell.format.indent or 0)
    return Ok(styles.keys.setdefault(key, len(styles.keys)))


def _cell(cell: Cell, style: int, strings: dict[str, int]) -> str:
    attributes = f'r="{address(cell.row, cell.col)}"'
    if style:
        attributes += f' s="{style}"'
    match cell.content:
        case Number(value):
            return f"<c {attributes}><v>{value!r}</v></c>"
        case Logical(value):
            return f'<c {attributes} t="b"><v>{int(value)}</v></c>'
        case Text(value):
            index = strings.setdefault(value, len(strings))
            return f'<c {attributes} t="s"><v>{index}</v></c>'
        case Formula(text):
            return f"<c {attributes}><f>{escape_text(text[1:])}</f></c>"


def _cols(widths: dict[int, float]) -> str:
    """The ``<cols>`` element, adjacent columns of the same width merged, as the app does."""
    runs: list[tuple[int, int, float]] = []
    for col in sorted(widths):
        stored = stored_width(widths[col]) if widths[col] else 0.0
        if runs and runs[-1][1] == col - 1 and runs[-1][2] == stored:
            runs[-1] = (runs[-1][0], col, stored)
        else:
            runs.append((col, col, stored))
    if not runs:
        return ""
    items = "".join(
        f'<col min="{first}" max="{last}" width="0" hidden="1" customWidth="1"/>'
        if stored == 0
        else f'<col min="{first}" max="{last}" width="{stored!r}" customWidth="1"/>'
        for first, last, stored in runs
    )
    return f"<cols>{items}</cols>"


def _worksheet(
    cells: list[tuple[Cell, str]], widths: dict[int, float], first: bool
) -> str:
    rows: dict[int, list[tuple[int, str]]] = {}
    for cell, xml in cells:
        rows.setdefault(cell.row, []).append((cell.col, xml))
    cols = [cell.col for cell, _ in cells]
    top_left = address(min(rows), min(cols))
    bottom_right = address(max(rows), max(cols))
    dimension = top_left if top_left == bottom_right else f"{top_left}:{bottom_right}"
    data = "".join(
        f'<row r="{row}">' + "".join(xml for _, xml in sorted(rows[row])) + "</row>"
        for row in sorted(rows)
    )
    selected = ' tabSelected="1"' if first else ""
    return (
        f'<worksheet xmlns="{MAIN}" xmlns:r="{RELATIONSHIPS}">'
        f'<dimension ref="{dimension}"/>'
        f'<sheetViews><sheetView{selected} workbookViewId="0"/></sheetViews>'
        f'<sheetFormatPr defaultRowHeight="{DEFAULT_ROW_HEIGHT}"/>'
        f"{_cols(widths)}<sheetData>{data}</sheetData>"
        '<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" '
        'header="0.3" footer="0.3"/></worksheet>'
    )


def _styles_part(styles: _Styles) -> str:
    num_fmts = ""
    if styles.custom:
        entries = "".join(
            f'<numFmt numFmtId="{number}" formatCode="{escape_attribute(code)}"/>'
            for code, number in styles.custom.items()
        )
        num_fmts = f'<numFmts count="{len(styles.custom)}">{entries}</numFmts>'
    xfs = []
    for number_format, indent in styles.keys:
        attributes = (
            f'numFmtId="{number_format}" fontId="0" fillId="0" borderId="0" xfId="0"'
        )
        if number_format:
            attributes += ' applyNumberFormat="1"'
        if indent:
            xfs.append(
                f'<xf {attributes} applyAlignment="1">'
                f'<alignment horizontal="left" indent="{indent}"/></xf>'
            )
        else:
            xfs.append(f"<xf {attributes}/>")
    return (
        f'<styleSheet xmlns="{MAIN}">{num_fmts}'
        '<fonts count="1"><font><sz val="11"/><color rgb="FF000000"/>'
        '<name val="Aptos Narrow"/><family val="2"/></font></fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/>'
        "</border></borders>"
        '<cellStyleXfs count="1">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        f'<cellXfs count="{len(xfs)}">{"".join(xfs)}</cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/>'
        "</cellStyles></styleSheet>"
    )


def _shared_strings(strings: Iterable[str], count: int) -> str:
    def item(text: str) -> str:
        space = (
            ' xml:space="preserve"' if text[:1].isspace() or text[-1:].isspace() else ""
        )
        return f"<si><t{space}>{escape_text(text)}</t></si>"

    items = [item(text) for text in strings]
    return (
        f'<sst xmlns="{MAIN}" count="{count}" uniqueCount="{len(items)}">'
        + "".join(items)
        + "</sst>"
    )


def _package(parts: list[tuple[str, str]]) -> bytes:
    """The parts as a ZIP, in the order given, with nothing that varies between runs."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, xml in parts:
            info = zipfile.ZipInfo(name, date_time=_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 0
            info.external_attr = 0
            archive.writestr(info, (_DECLARATION + xml).encode("utf-8"))
    return out.getvalue()


def write_xlsx(yup: Yup) -> Result[bytes, tuple[WriteError, ...]]:
    """An xlsx workbook holding a checked ``.yup`` file's cells, or every cell refused.

    The writer relies on the reader's checks, so it takes a ``Yup`` that came from
    ``read_yup``. Anything that can only go wrong through a bug here raises.
    """
    styles = _Styles(custom={}, keys={(0, 0): 0})
    strings: dict[str, int] = {}
    per_sheet: dict[str, list[tuple[Cell, str]]] = {name: [] for name in yup.sheets}
    widths: dict[str, dict[int, float]] = {name: {} for name in yup.sheets}
    errors: list[WriteError] = []
    text_cells = 0
    for cell in yup.cells:
        match _style(cell, styles):
            case Err(reason):
                errors.append(WriteError(cell.line, f"{_where(cell)}: {reason}"))
                continue
            case Ok(style):
                pass
        match cell.format.column_width:
            case float() as width:
                widths[cell.sheet][cell.col] = width
            case Default() | None:
                pass
        text_cells += isinstance(cell.content, Text)
        per_sheet[cell.sheet].append((cell, _cell(cell, style, strings)))
    if errors:
        return Err(tuple(errors))

    count = len(yup.sheets)
    overrides = [("/xl/workbook.xml", f"{_SHEETML}.sheet.main+xml")]
    overrides += [
        (f"/xl/worksheets/sheet{i}.xml", f"{_SHEETML}.worksheet+xml")
        for i in range(1, count + 1)
    ]
    overrides.append(("/xl/styles.xml", f"{_SHEETML}.styles+xml"))
    targets = [("worksheet", f"worksheets/sheet{i}.xml") for i in range(1, count + 1)]
    targets.append(("styles", "styles.xml"))
    if strings:
        overrides.append(("/xl/sharedStrings.xml", f"{_SHEETML}.sharedStrings+xml"))
        targets.append(("sharedStrings", "sharedStrings.xml"))
    content_types = (
        f'<Types xmlns="{CONTENT_TYPES}">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        + "".join(
            f'<Override PartName="{name}" ContentType="{kind}"/>'
            for name, kind in overrides
        )
        + "</Types>"
    )
    package_relationships = (
        f'<Relationships xmlns="{PACKAGE}">'
        f'<Relationship Id="rId1" Type="{RELATIONSHIPS}/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>'
    )
    workbook = (
        f'<workbook xmlns="{MAIN}" xmlns:r="{RELATIONSHIPS}"><sheets>'
        + "".join(
            f'<sheet name="{escape_attribute(name)}" sheetId="{i}" r:id="rId{i}"/>'
            for i, name in enumerate(yup.sheets, start=1)
        )
        + "</sheets></workbook>"
    )
    workbook_relationships = (
        f'<Relationships xmlns="{PACKAGE}">'
        + "".join(
            f'<Relationship Id="rId{i}" Type="{RELATIONSHIPS}/{kind}" Target="{target}"/>'
            for i, (kind, target) in enumerate(targets, start=1)
        )
        + "</Relationships>"
    )
    parts = [
        ("[Content_Types].xml", content_types),
        ("_rels/.rels", package_relationships),
        ("xl/workbook.xml", workbook),
        ("xl/_rels/workbook.xml.rels", workbook_relationships),
    ]
    parts += [
        (
            f"xl/worksheets/sheet{i}.xml",
            _worksheet(per_sheet[name], widths[name], first=i == 1),
        )
        for i, name in enumerate(yup.sheets, start=1)
    ]
    parts.append(("xl/styles.xml", _styles_part(styles)))
    if strings:
        parts.append(("xl/sharedStrings.xml", _shared_strings(strings, text_cells)))
    return Ok(_package(parts))


def write_xlsx_file(yup: Yup, path: Path) -> Result[None, tuple[WriteError, ...]]:
    """Write the workbook to ``path``, or report every cell refused or the file unwritable."""
    match write_xlsx(yup):
        case Err() as refused:
            return refused
        case Ok(data):
            pass
    try:
        path.write_bytes(data)
    except OSError as error:
        return Err((WriteError(None, f"{path}: could not write: {error.strerror}"),))
    return Ok(None)
