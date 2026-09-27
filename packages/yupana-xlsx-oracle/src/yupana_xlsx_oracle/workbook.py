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
    Cell,
    Default,
    Formula,
    Logical,
    Number,
    Text,
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


def _rejected(cell: Cell, message: str) -> Rejected:
    return Rejected(cell.line, cell.sheet, cell.row, cell.col, message)


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


def _write_content(target: Any, cell: Cell) -> None:
    match cell.content:
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
    session: Session, cell: Cell, what: str, step: Callable[[], None]
) -> Rejected | None:
    """One step of writing a cell. An exception the app raises is its answer for this
    cell, unless the watchdog caused it, in which case it propagates as a stall."""
    try:
        step()
    except com_error() as error:
        if session.stalling():
            raise
        return _rejected(
            cell, f"the spreadsheet app rejected {what}: {describe(error)}"
        )
    return None


def _write_cell(session: Session, sheet: Any, cell: Cell) -> list[Rejected]:
    if isinstance(cell.content, Text) and text_to_type(cell.content.value).is_err():
        return [_rejected(cell, text_to_type(cell.content.value).unwrap_err())]
    target = sheet.Cells(cell.row, cell.col)
    steps: list[tuple[str, Callable[[], None]]] = [
        ("the cell's contents", lambda: _write_content(target, cell))
    ]
    fmt = cell.format
    if fmt.number_format is not None:
        code = fmt.number_format

        def number_format() -> None:
            target.NumberFormat = code

        steps.append((f"the number format {code!r}", number_format))
    if fmt.indent is not None:
        indent = fmt.indent

        def indent_level() -> None:
            target.IndentLevel = indent

        steps.append((f"the indent {indent}", indent_level))
    match fmt.column_width:
        case float() as width:

            def column_width() -> None:
                sheet.Columns(cell.col).ColumnWidth = width

            steps.append((f"the column width {width!r}", column_width))
        case Default() | None:
            pass
    found = (_attempt(session, cell, what, step) for what, step in steps)
    return [rejected for rejected in found if rejected is not None]


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
    """Have the app compute a ``.yup`` file: write every cell into a fresh workbook,
    recalculate in full, and read back every cell's value. With ``xlsx``, also save the
    workbook, as the app's own rendering of the file."""
    app = session.app

    def create() -> tuple[Any, dict[str, Any], list[Rejected]]:
        saved = app.SheetsInNewWorkbook
        app.SheetsInNewWorkbook = 1
        try:
            workbook = app.Workbooks.Add()
        finally:
            app.SheetsInNewWorkbook = saved
        first_line = {c.sheet: c for c in reversed(yup.cells)}
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
        return [
            r
            for cell in yup.cells
            for r in _write_cell(session, by_name[cell.sheet], cell)
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
