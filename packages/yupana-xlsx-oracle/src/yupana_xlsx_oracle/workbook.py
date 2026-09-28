"""Having the app compute a ``.yup`` file (``build``) or an xlsx file (``read``).

Both return the values CSV's lines for the cells the ``.yup`` file lists, in its order, or
every error: a cell the app rejected, a file it refused, a stall.

Writing a text needs care, since the app interprets a string as if it were typed, so
``001`` would become a number. A text is written with the cell's number format set to
text (``@``), and the format set back to General afterwards: the cell stays text, and the
saved workbook differs only by an ``applyNumberFormat`` flag. The app still reads a
leading apostrophe as a prefix, so a text that starts with one is written with one more,
which the saved workbook keeps as a quote prefix. The prefix counts toward the app's
limit, so a text of the maximum length that starts with an apostrophe cannot be written
unchanged, and is rejected rather than truncated.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from yupana.result import Err, Ok, Result
from yupana.values import Value
from yupana.yup import (
    MAX_TEXT,
    Blank,
    Cell,
    Column,
    Content,
    Format,
    Formula,
    HorizontalAlignment,
    Line,
    LineStyle,
    Logical,
    Number,
    Row,
    Text,
    Underline,
    VerticalAlignment,
    Yup,
    utf16_length,
)

from yupana_xlsx_oracle.app_values import from_app
from yupana_xlsx_oracle.errors import (
    FileProblem,
    OracleError,
    Refused,
    Rejected,
    describe,
)
from yupana_xlsx_oracle.session import Session, com_error

_XLSX = 51
# The app's constants for each underline.
_UNDERLINES = {
    Underline.SINGLE: 2,
    Underline.DOUBLE: -4119,
    Underline.SINGLE_ACCOUNTING: 4,
    Underline.DOUBLE_ACCOUNTING: 5,
}
# The app's constants for each alignment.
_HORIZONTAL = {
    HorizontalAlignment.LEFT: -4131,
    HorizontalAlignment.CENTER: -4108,
    HorizontalAlignment.RIGHT: -4152,
}
_VERTICAL = {
    VerticalAlignment.TOP: -4160,
    VerticalAlignment.CENTER: -4108,
    VerticalAlignment.BOTTOM: -4107,
}
# The app's index of each edge, and the style and weight it saves as each line.
_EDGES = {"left": 7, "top": 8, "bottom": 9, "right": 10}
_LINES = {
    LineStyle.THIN: (1, 2),
    LineStyle.MEDIUM: (1, -4138),
    LineStyle.THICK: (1, 4),
    LineStyle.DOUBLE: (-4119, 4),
    LineStyle.DOTTED: (-4118, 2),
    LineStyle.DASHED: (-4115, 2),
}


def _rejected(line: Line, message: str) -> Rejected:
    match line:
        case Cell() | Blank():
            return Rejected(line.line, line.sheet, line.row, line.col, message)
        case Column():
            return Rejected(line.line, line.sheet, None, line.col, message)
        case Row():
            return Rejected(line.line, line.sheet, line.row, None, message)


def text_to_type(text: str) -> Result[str, str]:
    """What to assign to a cell formatted as text so that it holds ``text`` unchanged.

    >>> text_to_type("001"), text_to_type("'quoted")
    (Ok(value='001'), Ok(value="''quoted"))
    >>> text_to_type("'" + "c" * 32766).is_err()
    True
    """
    if not text.startswith("'"):
        return Ok(text)
    if utf16_length(text) >= MAX_TEXT:
        return Err(
            "the spreadsheet app cannot hold a text of the maximum length that starts "
            "with an apostrophe"
        )
    return Ok("'" + text)


def _write_content(target: Any, content: Content) -> None:
    match content:
        case Formula(text):
            target.Formula = text
        case Number(value):
            target.Value2 = value
        case Logical(value):
            target.Value2 = value
        case Text(value):
            target.NumberFormat = "@"
            target.Value2 = text_to_type(value).unwrap()
            target.NumberFormat = "General"


def _attempt(
    session: Session, line: Line, what: str, step: Callable[[], None]
) -> Rejected | None:
    """One step of writing a line. An exception the app raises is its answer for this
    line, unless the watchdog caused it, in which case it propagates as a stall."""
    try:
        step()
    except com_error() as error:
        if session.stalling():
            raise
        return _rejected(
            line, f"the spreadsheet app rejected {what}: {describe(error)}"
        )
    return None


def bgr(color: str) -> int:
    """A colour as the app's object model takes it: an integer, with red lowest.

    >>> hex(bgr("0070C0"))
    '0xc07000'
    """
    return int(color[4:6] + color[2:4] + color[0:2], 16)


def _set(owner: Callable[[], Any], name: str, value: object) -> Callable[[], None]:
    """A step that sets one property of what ``owner`` fetches, fetched in the step, so
    that the app's answer to either comes back from the step."""

    def step() -> None:
        setattr(owner(), name, value)

    return step


def _formats(target: Any, fmt: Format) -> list[tuple[str, Callable[[], None]]]:
    """A step for each key of a cell's format, each named for a rejection."""

    def cell() -> Any:
        return target

    def font() -> Any:
        return target.Font

    def interior() -> Any:
        return target.Interior

    settings: list[tuple[str, Callable[[], Any], str, object]] = []
    if fmt.number_format is not None:
        what = f"the number format {fmt.number_format!r}"
        settings.append((what, cell, "NumberFormat", fmt.number_format))
    # Alignment before the indent: aligning a cell afresh can reset its indent.
    if fmt.halign is not None:
        what = f"the alignment {fmt.halign.value}"
        horizontal = _HORIZONTAL[fmt.halign]
        settings.append((what, cell, "HorizontalAlignment", horizontal))
    if fmt.valign is not None:
        what = f"the alignment {fmt.valign.value}"
        settings.append((what, cell, "VerticalAlignment", _VERTICAL[fmt.valign]))
    if fmt.wrap is not None:
        settings.append(("wrapping", cell, "WrapText", fmt.wrap))
    if fmt.indent is not None:
        settings.append((f"the indent {fmt.indent}", cell, "IndentLevel", fmt.indent))
    if fmt.bold is not None:
        settings.append(("bold", font, "Bold", fmt.bold))
    if fmt.italic is not None:
        settings.append(("italic", font, "Italic", fmt.italic))
    if fmt.underline is not None:
        what = f"the underline {fmt.underline.value}"
        settings.append((what, font, "Underline", _UNDERLINES[fmt.underline]))
    if fmt.font_color is not None:
        what = f"the font colour {fmt.font_color}"
        settings.append((what, font, "Color", bgr(fmt.font_color)))
    if fmt.fill is not None:
        settings.append((f"the fill {fmt.fill}", interior, "Color", bgr(fmt.fill)))
    for edge, border in (
        ("top", fmt.border_top),
        ("bottom", fmt.border_bottom),
        ("left", fmt.border_left),
        ("right", fmt.border_right),
    ):
        if border is None:
            continue
        line = _edge(target, _EDGES[edge])
        what = f"the {border.style.value} line along the {edge}"
        style, weight = _LINES[border.style]
        settings += [(what, line, "LineStyle", style), (what, line, "Weight", weight)]
        if border.color is not None:
            settings.append((f"{what}'s colour", line, "Color", bgr(border.color)))
    return [(what, _set(owner, name, value)) for what, owner, name, value in settings]


def _edge(target: Any, index: int) -> Callable[[], Any]:
    """What fetches the line along one edge of a cell."""

    def line() -> Any:
        return target.Borders(index)

    return line


def _write_cell(session: Session, sheet: Any, cell: Cell | Blank) -> list[Rejected]:
    target = sheet.Cells(cell.row, cell.col)
    steps: list[tuple[str, Callable[[], None]]] = []
    if isinstance(cell, Cell):
        content = cell.content
        if isinstance(content, Text) and text_to_type(content.value).is_err():
            return [_rejected(cell, text_to_type(content.value).unwrap_err())]
        steps.append(("the cell's contents", lambda: _write_content(target, content)))
    steps += _formats(target, cell.format)
    found = (_attempt(session, cell, what, step) for what, step in steps)
    return [rejected for rejected in found if rejected is not None]


def _write_size(session: Session, sheet: Any, line: Column | Row) -> list[Rejected]:
    """A column's width or a row's height. The line for every column or row sets them
    all, so it is written before the lines for single ones."""
    match line:
        case Column(col=col, width=width):
            what = f"the width {width!r} of {'every column' if col is None else 'it'}"

            def size() -> None:
                (sheet.Cells if col is None else sheet.Columns(col)).ColumnWidth = width

        case Row(row=row, height=height):
            what = f"the height {height!r} of {'every row' if row is None else 'it'}"

            def size() -> None:
                (sheet.Cells if row is None else sheet.Rows(row)).RowHeight = height

    rejected = _attempt(session, line, what, size)
    return [] if rejected is None else [rejected]


def _single(line: Column | Row) -> bool:
    """Whether a line sizes one column or row, rather than every one."""
    match line:
        case Column(col=col):
            return col is not None
        case Row(row=row):
            return row is not None


def _values(
    session: Session,
    sheets: dict[str, Any],
    yup: Yup,
    unwritten: frozenset[int] = frozenset(),
) -> Result[tuple[Value, ...], tuple[OracleError, ...]]:
    """The value of every cell, except those on the ``unwritten`` lines, which the app
    rejected and so could hold no value of their own."""

    def read() -> list[Result[Value, Rejected]]:
        results: list[Result[Value, Rejected]] = []
        for cell in yup.cells:
            if cell.line in unwritten:
                continue
            raw = sheets[cell.sheet].Cells(cell.row, cell.col).Value2
            results.append(
                from_app(cell.sheet, cell.row, cell.col, raw).map_err(
                    lambda message, cell=cell: _rejected(cell, message)
                )
            )
        return results

    match session.guarded("reading values", session.timeouts.calculate, read):
        case Err(stalled):
            return Err((stalled,))
        case Ok(results):
            values = [r.value for r in results if isinstance(r, Ok)]
            problems = [r.error for r in results if isinstance(r, Err)]
            return Err(tuple(problems)) if problems else Ok(tuple(values))


def _save(session: Session, workbook: Any, path: Path) -> list[OracleError]:
    target = path.resolve()

    def save() -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.unlink(missing_ok=True)
        workbook.SaveAs(str(target), FileFormat=_XLSX)

    try:
        match session.guarded(f"saving {path}", session.timeouts.save, save):
            case Err(stalled):
                return [stalled]
            case Ok():
                return []
    except (OSError, com_error()) as error:
        return [FileProblem(str(path), f"could not save: {describe(error)}")]


def _close(session: Session, workbook: Any) -> list[OracleError]:
    match session.guarded(
        "closing a workbook",
        session.timeouts.close,
        lambda: workbook.Close(SaveChanges=False),
    ):
        case Err(stalled):
            return [stalled]
        case Ok():
            return []


def build(
    session: Session, yup: Yup, xlsx: Path | None = None
) -> Result[tuple[Value, ...], tuple[OracleError, ...]]:
    """Have the app compute a ``.yup`` file: write every line into a fresh workbook,
    recalculate in full, and read back the value of every cell with contents. With
    ``xlsx``, also save the workbook, as the app's own rendering of the file."""
    app = session.app

    def create() -> tuple[Any, dict[str, Any], list[Rejected]]:
        saved = app.SheetsInNewWorkbook
        app.SheetsInNewWorkbook = 1
        try:
            workbook = app.Workbooks.Add()
        finally:
            app.SheetsInNewWorkbook = saved
        first_line: dict[str, Line] = {}
        for line in yup.lines():
            first_line.setdefault(line.sheet, line)
        rejected: list[Rejected] = []
        made: dict[str, Any] = {}
        previous = None
        for name in yup.sheets:
            # After is passed by position: named alone, it is dropped on the way through
            # COM, and every new sheet goes before the active one instead.
            sheet = (
                workbook.Worksheets(1)
                if previous is None
                else workbook.Worksheets.Add(None, previous)
            )

            def rename(sheet: Any = sheet, name: str = name) -> None:
                sheet.Name = name

            problem = _attempt(session, first_line[name], "the sheet name", rename)
            rejected.extend([] if problem is None else [problem])
            made[name] = previous = sheet
        # Adding a sheet activates it; the writer's workbooks open on the first.
        workbook.Worksheets(1).Activate()
        return workbook, made, rejected

    match session.guarded("creating a workbook", session.timeouts.open, create):
        case Err(stalled):
            return Err((stalled,))
        case Ok((workbook, by_name, rejected_sheets)):
            pass
    errors: list[OracleError] = [*rejected_sheets]
    if rejected_sheets:
        return Err(tuple(errors + _close(session, workbook)))

    def write() -> list[Rejected]:
        cells = [line for line in yup.lines() if isinstance(line, Cell | Blank)]
        sizes = sorted(
            (*yup.columns, *yup.rows),
            key=lambda line: (isinstance(line, Row), _single(line), line.line),
        )
        return [
            *(r for c in cells for r in _write_cell(session, by_name[c.sheet], c)),
            *(r for s in sizes for r in _write_size(session, by_name[s.sheet], s)),
        ]

    match session.guarded("writing cells", session.timeouts.cells, write):
        case Err(stalled):
            return Err((stalled,))
        case Ok(rejected_cells):
            errors.extend(rejected_cells)
    match session.recalculate():
        case Err(stalled):
            return Err((stalled,))
        case Ok():
            pass
    unwritten = frozenset(r.line for r in errors if isinstance(r, Rejected))
    computed = _values(session, by_name, yup, unwritten)
    if isinstance(computed, Err):
        errors.extend(computed.error)
    if xlsx is not None:
        errors.extend(_save(session, workbook, xlsx))
    errors.extend(_close(session, workbook))
    if errors:
        return Err(tuple(errors))
    return computed


def read(
    session: Session, xlsx: Path, yup: Yup
) -> Result[tuple[Value, ...], tuple[OracleError, ...]]:
    """Have the app open an xlsx file, recalculate it in full, and read the value of each
    cell the ``.yup`` file lists. A file the app will not open is reported as refused."""
    if not xlsx.is_file():
        return Err((FileProblem(str(xlsx), "no such file"),))
    target = str(xlsx.resolve())

    def open_workbook() -> Any:
        return session.app.Workbooks.Open(
            target, UpdateLinks=0, ReadOnly=True, CorruptLoad=0
        )

    try:
        opened = session.guarded(
            f"opening {xlsx}", session.timeouts.open, open_workbook
        )
    except com_error() as error:
        return Err((Refused(str(xlsx), describe(error)),))
    match opened:
        case Err(stalled):
            return Err((stalled,))
        case Ok(workbook):
            pass
    present = {sheet.Name.casefold(): sheet for sheet in workbook.Worksheets}
    missing = [name for name in yup.sheets if name.casefold() not in present]
    if missing:
        problems: list[OracleError] = [
            FileProblem(str(xlsx), f"the workbook has no sheet named {name!r}")
            for name in missing
        ]
        return Err(tuple(problems + _close(session, workbook)))
    by_name = {name: present[name.casefold()] for name in yup.sheets}
    match session.recalculate():
        case Err(stalled):
            return Err((stalled,))
        case Ok():
            pass
    computed = _values(session, by_name, yup)
    closed = _close(session, workbook)
    if closed:
        return Err((*(computed.error if isinstance(computed, Err) else ()), *closed))
    return computed
