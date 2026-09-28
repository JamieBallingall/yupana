"""The ``.yup`` format: its types, and a reader that checks a file against ``SPEC.md``.

>>> text = (
...     PREAMBLE
...     + "Model\\t*\\t1\\t|\\t\\tcolumnwidth=20\\n"
...     "Model\\t1\\t1\\t$\\tRevenue\\t\\n"
...     "Model\\t1\\t2\\t#\\t1000\\tnumberformat=#,##0\\n"
...     "Model\\t2\\t2\\t=\\tB1*2\\t\\n"
...     "Model\\t3\\t2\\t.\\t\\tindent=1\\n"
... )
>>> yup = read_yup(text).unwrap()
>>> yup.sheets
('Model',)
>>> [cell.content for cell in yup.cells]
[Text(value='Revenue'), Number(value=1000.0), Formula(text='=B1*2')]
>>> yup.cells[1].format
Format(number_format='#,##0', indent=None)
>>> yup.columns, yup.blanks[0].format
((Column(line=3, sheet='Model', col=1, width=20.0),), Format(number_format=None, indent=1))

A file with problems is refused with every one of them, each with its line:

>>> broken = text.replace("\\t1000", "\\t01").replace("\\t2\\t2\\t", "\\t0\\t2\\t")
>>> for error in read_yup(broken).unwrap_err():
...     print(error)
line 5: a number is written in JSON's grammar, not "01"
line 6: row must be a whole number from 1 to 1048576, not "0"
"""

import json
import math
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from yupana.result import Err, Ok, Result

VERSION = "0.0.2"
# The first line names the format and its version, so that a file says what it is, and a
# reader of a later version can tell which rules a file was written to.
VERSION_LINE = f"yup {VERSION} Yupana Straight Line Spreadsheet Format"
HEADER = "sheet\trow\tcol\ttype\tcell\tformat"
PREAMBLE = f"{VERSION_LINE}\n{HEADER}\n"
"""The two lines every ``.yup`` file starts with, for a writer to begin with."""
EVERY = "*"
"""What ``row`` or ``col`` holds for every row or column, where the type allows it."""
MAX_ROW = 1_048_576
MAX_COL = 16_384
MAX_SHEET_NAME = 31
MAX_TEXT = 32_767
MAX_FORMULA = 8_192
"""The app's limit on a formula's length, counting its ``=``."""
MAX_INDENT = 250
MAX_COLUMN_WIDTH = 255
MAX_ROW_HEIGHT = 409
SMALLEST_NORMAL = 2.2250738585072014e-308

_INTEGER = re.compile(r"[1-9][0-9]*")
JSON_NUMBER = re.compile(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?")
ESCAPE_SHAPE = re.compile(r"_x[0-9A-Fa-f]{4}_")
_NOT_IN_SHEET_NAME = ":\\/?*[]"
_CELL_TYPES = ("=", "#", "$", "?", ".")
_TYPES = (*_CELL_TYPES, "|", "-")
_CELL_KEYS = ("numberformat", "indent")
_KEYS = {"|": ("columnwidth",), "-": ("rowheight",)}
_HOME = {key: "a cell" for key in _CELL_KEYS} | {
    key: f"a {kind} line" for kind, keys in _KEYS.items() for key in keys
}
"""Every format key, and the type of line it is for."""


@dataclass(frozen=True, slots=True)
class Formula:
    """A formula as it is typed into a cell, so its text starts with ``=``. A ``.yup``
    file writes it without, since its type already says it is a formula."""

    text: str


@dataclass(frozen=True, slots=True)
class Number:
    value: float


@dataclass(frozen=True, slots=True)
class Text:
    value: str


@dataclass(frozen=True, slots=True)
class Logical:
    value: bool


type Content = Formula | Number | Text | Logical


@dataclass(frozen=True, slots=True)
class Format:
    """A cell's formats. ``None`` means the key is absent."""

    number_format: str | None = None
    indent: int | None = None


@dataclass(frozen=True, slots=True)
class Cell:
    """A cell with contents: a line of type ``=``, ``#``, ``$`` or ``?``. ``line`` is its
    1-based line number in the file."""

    line: int
    sheet: str
    row: int
    col: int
    content: Content
    format: Format


@dataclass(frozen=True, slots=True)
class Blank:
    """A blank cell, listed for its format: a line of type ``.``."""

    line: int
    sheet: str
    row: int
    col: int
    format: Format


@dataclass(frozen=True, slots=True)
class Column:
    """A column's width: a line of type ``|``. ``col`` is ``None`` for every column."""

    line: int
    sheet: str
    col: int | None
    width: float


@dataclass(frozen=True, slots=True)
class Row:
    """A row's height: a line of type ``-``. ``row`` is ``None`` for every row."""

    line: int
    sheet: str
    row: int | None
    height: float


type Line = Cell | Blank | Column | Row


@dataclass(frozen=True, slots=True)
class Yup:
    """A checked ``.yup`` file.

    Its sheets, in order of first appearance; its cells with contents, in file order, one
    for each line of the values CSV; and the lines that only format: blank cells,
    columns and rows, each in file order.
    """

    sheets: tuple[str, ...]
    cells: tuple[Cell, ...]
    blanks: tuple[Blank, ...] = ()
    columns: tuple[Column, ...] = ()
    rows: tuple[Row, ...] = ()

    def lines(self) -> tuple[Line, ...]:
        """Every line after the header, in file order."""
        every = (*self.cells, *self.blanks, *self.columns, *self.rows)
        return tuple(sorted(every, key=lambda line: line.line))


@dataclass(frozen=True, slots=True)
class YupError:
    """One problem with a ``.yup`` file, on one line of it."""

    line: int
    message: str

    def __str__(self) -> str:
        return f"line {self.line}: {self.message}"


def utf16_length(text: str) -> int:
    """The length of a text in UTF-16 code units, as the spreadsheet app counts it.

    >>> len("\\N{GRINNING FACE}"), utf16_length("\\N{GRINNING FACE}")
    (1, 2)
    """
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def bad_character(text: str) -> str | None:
    """The first character no field may hold, written ``U+XXXX``, or ``None``.

    Those are the C0 controls, U+007F, U+FFFE and U+FFFF, which XML cannot carry or the
    app would not keep, and the surrogates, which UTF-8 cannot encode.

    >>> bad_character("fine"), bad_character("bell\\a"), bad_character("\\ufffe")
    (None, 'U+0007', 'U+FFFE')
    """
    for character in text:
        point = ord(character)
        if (
            point < 0x20
            or point == 0x7F
            or point in (0xFFFE, 0xFFFF)
            or 0xD800 <= point <= 0xDFFF
        ):
            return f"U+{point:04X}"
    return None


def quoted(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def sheet_name_problems(name: str) -> tuple[str, ...]:
    """Every rule a sheet name breaks."""
    problems: list[str] = []
    length = utf16_length(name)
    if not 1 <= length <= MAX_SHEET_NAME:
        problems.append(
            f"a sheet name is 1 to {MAX_SHEET_NAME} characters long, "
            f"and {quoted(name)} is {length}"
        )
    forbidden = [c for c in _NOT_IN_SHEET_NAME if c in name]
    if forbidden:
        listed = " ".join(quoted(c) for c in forbidden)
        problems.append(f"sheet name {quoted(name)} contains {listed}")
    if name.startswith("'") or name.endswith("'"):
        problems.append(f"sheet name {quoted(name)} starts or ends with an apostrophe")
    if name.casefold() == "history":
        problems.append(f"sheet name {quoted(name)} is reserved by the spreadsheet app")
    if bad := bad_character(name):
        problems.append(f"sheet name {quoted(name)} contains the character {bad}")
    return tuple(problems)


def position(field: str, text: str, largest: int) -> Result[int, str]:
    """A row or col: a whole number from 1 to ``largest``, with no sign or leading zero.

    >>> position("row", "12", 100), position("row", "012", 100)
    (Ok(value=12), Err(error='row must be a whole number from 1 to 100, not "012"'))
    """
    if (
        _INTEGER.fullmatch(text)
        and len(text) <= len(str(largest))
        and int(text) <= largest
    ):
        return Ok(int(text))
    return Err(
        f"{field} must be a whole number from 1 to {largest}, not {quoted(text)}"
    )


def _position_or_every(field: str, text: str, largest: int) -> Result[int | None, str]:
    """A row or col, or ``None`` for every one (``*``)."""
    if text == EVERY:
        return Ok(None)
    match position(field, text, largest):
        case Ok(value):
            return Ok(value)
        case Err():
            return Err(
                f"{field} must be * or a whole number from 1 to {largest}, "
                f"not {quoted(text)}"
            )


def _every(field: str, text: str, kind: str) -> Result[None, str]:
    if text == EVERY:
        return Ok(None)
    return Err(f"{field} is * on a {kind} line, not {quoted(text)}")


def _number(text: str) -> Result[float, str]:
    if not JSON_NUMBER.fullmatch(text):
        return Err(f"a number is written in JSON's grammar, not {quoted(text)}")
    value = float(text)
    mantissa = re.split("[eE]", text)[0]
    if not math.isfinite(value):
        return Err(f"the number {text} is too large to be a double")
    if value == 0 and any(digit in mantissa for digit in "123456789"):
        return Err(f"the number {text} is too small, and would be stored as zero")
    if value != 0 and abs(value) < SMALLEST_NORMAL:
        return Err(f"the number {text} is subnormal, and would be stored as zero")
    return Ok(value)


def _content(kind: str, cell: str) -> Result[Content, str]:
    """A cell's contents, read as its type says."""
    match kind:
        case "=":
            if not cell:
                return Err("a formula cannot be empty")
            if bad := bad_character(cell):
                return Err(f"a formula cannot contain the character {bad}")
            if (length := utf16_length(cell)) >= MAX_FORMULA:
                return Err(
                    f"a formula is at most {MAX_FORMULA - 1} characters long, "
                    f"not {length}"
                )
            return Ok(Formula("=" + cell))
        case "#":
            match _number(cell):
                case Ok(value):
                    return Ok(Number(value))
                case Err() as refused:
                    return refused
        case "$":
            if not cell:
                return Err("a text cannot be empty")
            if (length := utf16_length(cell)) > MAX_TEXT:
                return Err(
                    f"a text is at most {MAX_TEXT} characters long, not {length}"
                )
            if bad := bad_character(cell):
                return Err(f"a text cannot contain the character {bad}")
            return Ok(Text(cell))
        case "?":
            if cell in ("TRUE", "FALSE"):
                return Ok(Logical(cell == "TRUE"))
            return Err(f"a logical is TRUE or FALSE, not {quoted(cell)}")
        case _:
            raise AssertionError(f"no contents for a {kind} line")


def _number_format(text: str) -> Result[str, str]:
    if bad := bad_character(text):
        return Err(f"a number format cannot contain the character {bad}")
    return Ok(text)


def _indent(text: str) -> Result[int, str]:
    if (text == "0" or (_INTEGER.fullmatch(text) and len(text) <= 3)) and int(
        text
    ) <= MAX_INDENT:
        return Ok(int(text))
    return Err(
        f"indent must be a whole number from 0 to {MAX_INDENT}, not {quoted(text)}"
    )


def _size(key: str, text: str, largest: int) -> Result[float, str]:
    """A width or height: a number from 0 to ``largest``, in JSON's grammar."""
    if JSON_NUMBER.fullmatch(text) and 0 <= (size := float(text)) <= largest:
        return Ok(size + 0.0)
    return Err(f"{key} must be a number from 0 to {largest}, not {quoted(text)}")


def _pairs(text: str, kind: str) -> tuple[dict[str, str], list[str]]:
    """A format's values by key, and every problem with its shape or its keys."""
    allowed = _CELL_KEYS if kind in _CELL_TYPES else _KEYS[kind]
    pairs: dict[str, str] = {}
    problems: list[str] = []
    if not text:
        return pairs, problems
    for pair in text.split("|"):
        key, equals, value = pair.partition("=")
        if not equals or not value:
            problems.append(f"a format pair is key=value, not {quoted(pair)}")
        elif key not in _HOME:
            problems.append(f"unknown format key {quoted(key)}")
        elif key not in allowed:
            problems.append(
                f"the format key {key} is for {_HOME[key]}, not a {kind} line"
            )
        elif key in pairs:
            problems.append(f"the format key {key} appears twice")
        else:
            pairs[key] = value
    return pairs, problems


def _cell_format(pairs: dict[str, str]) -> Result[Format, list[str]]:
    """A cell's formats, or every problem with their values."""
    problems: list[str] = []

    def parsed[T](key: str, parse: Callable[[str], Result[T, str]]) -> T | None:
        text = pairs.get(key)
        if text is None:
            return None
        match parse(text):
            case Ok(value):
                return value
            case Err(problem):
                problems.append(problem)
                return None

    number_format = parsed("numberformat", _number_format)
    indent = parsed("indent", _indent)
    if problems:
        return Err(problems)
    return Ok(Format(number_format=number_format, indent=indent))


@dataclass(frozen=True, slots=True)
class _Placed:
    """What a line names, as far as it could be read, for the rules across lines.

    ``kind`` is ``cell``, ``width`` or ``height``, and ``slot`` which one of those on the
    sheet the line names, such as ``row 2, col 3``, or ``None`` if it could not be read.
    """

    line: int
    sheet: str
    kind: str
    slot: str | None


def _across_lines(placed: Iterable[_Placed]) -> list[YupError]:
    """The rules that no single line can break: spelling, and one line per cell, column
    and row."""
    errors: list[YupError] = []
    spelling: dict[str, str] = {}
    first_line: dict[tuple[str, str, str], int] = {}
    for p in placed:
        key = p.sheet.casefold()
        first = spelling.setdefault(key, p.sheet)
        if first != p.sheet:
            errors.append(
                YupError(
                    p.line,
                    f"sheet {quoted(p.sheet)} is spelled {quoted(first)} earlier",
                )
            )
        if p.slot is None:
            continue
        named = (key, p.kind, p.slot)
        if named not in first_line:
            first_line[named] = p.line
            continue
        where = f"sheet {quoted(first)}, {p.slot}"
        earlier = first_line[named]
        message = (
            f"{where} is already on line {earlier}"
            if p.kind == "cell"
            else f"the {p.kind} of {where}, is already on line {earlier}"
        )
        errors.append(YupError(p.line, message))
    return errors


def _line(number: int, fields: list[str]) -> tuple[Line | None, list[str], _Placed]:
    """One line after the header: what it describes, if it is good; every problem with
    it; and what it names, for the rules across lines."""
    sheet, row_text, col_text, kind, cell_text, format_text = fields
    problems = [*sheet_name_problems(sheet)]

    def read[T](result: Result[T, str]) -> T | None:
        match result:
            case Ok(value):
                return value
            case Err(problem):
                problems.append(problem)
                return None

    def empty_cell() -> None:
        if cell_text:
            problems.append(
                f"the cell field of a {kind} line is empty, not {quoted(cell_text)}"
            )

    if kind not in _TYPES:
        problems.append(f"type is one of {' '.join(_TYPES)}, not {quoted(kind)}")
        return None, problems, _Placed(number, sheet, "cell", None)

    if kind in _CELL_TYPES:
        row = read(position("row", row_text, MAX_ROW))
        col = read(position("col", col_text, MAX_COL))
        content = None
        if kind == ".":
            empty_cell()
            if not format_text:
                problems.append(
                    "a . line needs a format: a blank cell without one is not listed"
                )
        else:
            content = read(_content(kind, cell_text))
        pairs, format_problems = _pairs(format_text, kind)
        problems += format_problems
        cell_format = None
        match _cell_format(pairs):
            case Ok(read_format):
                cell_format = read_format
            case Err(value_problems):
                problems += value_problems
        slot = None if row is None or col is None else f"row {row}, col {col}"
        placed = _Placed(number, sheet, "cell", slot)
        if problems or row is None or col is None or cell_format is None:
            return None, problems, placed
        if content is None:
            return Blank(number, sheet, row, col, cell_format), problems, placed
        return Cell(number, sheet, row, col, content, cell_format), problems, placed

    # A column or a row: one of row and col says which, and the other is *.
    column = kind == "|"
    if column:
        read(_every("row", row_text, kind))
        at = read(_position_or_every("col", col_text, MAX_COL))
        readable = col_text == EVERY or at is not None
        what, key, largest = "col", "columnwidth", MAX_COLUMN_WIDTH
    else:
        at = read(_position_or_every("row", row_text, MAX_ROW))
        read(_every("col", col_text, kind))
        readable = row_text == EVERY or at is not None
        what, key, largest = "row", "rowheight", MAX_ROW_HEIGHT
    empty_cell()
    pairs, format_problems = _pairs(format_text, kind)
    problems += format_problems
    size = None
    if key in pairs:
        size = read(_size(key, pairs[key], largest))
        if size == 0 and at is None and readable:
            problems.append(f"{key} cannot be 0 for every {what}")
    elif not format_problems:
        problems.append(f"a {kind} line needs {key}")
    slot = (f"every {what}" if at is None else f"{what} {at}") if readable else None
    placed = _Placed(number, sheet, "width" if column else "height", slot)
    if problems or size is None:
        return None, problems, placed
    if column:
        return Column(number, sheet, at, size), problems, placed
    return Row(number, sheet, at, size), problems, placed


def _not_the_version_line(line: str) -> str:
    """Why a first line is not this version's, naming the version it claims, if any."""
    match line.split(" ", 2):
        case ["yup", version, *_] if version != VERSION:
            return f"this reader reads version {VERSION}, not {quoted(version)}"
        case _:
            return f"the first line must be {quoted(VERSION_LINE)}"


def read_yup(text: str) -> Result[Yup, tuple[YupError, ...]]:
    """A ``.yup`` file's lines, checked, or every problem with it, each with its line.

    Reading the file from disk is the caller's business, and so is decoding it as UTF-8.
    """
    errors: list[YupError] = []
    if text.startswith("﻿"):
        errors.append(YupError(1, "the file starts with a byte-order mark"))
        text = text[1:]
    if "\r" in text:
        line = text.count("\n", 0, text.index("\r")) + 1
        errors.append(YupError(line, "a CR, where lines end with LF alone"))
        text = text.replace("\r\n", "\n")
    if not text:
        errors.append(YupError(1, "the file is empty"))
        return Err(tuple(errors))
    if not text.endswith("\n"):
        errors.append(
            YupError(text.count("\n") + 1, "the last line does not end with LF")
        )
        text += "\n"
    lines = text.split("\n")[:-1]
    if lines[0] != VERSION_LINE:
        errors.append(YupError(1, _not_the_version_line(lines[0])))
    if len(lines) > 1 and lines[1] != HEADER:
        errors.append(
            YupError(2, f"the second line must be the header {quoted(HEADER)}")
        )
    if len(lines) < 3:
        errors.append(YupError(len(lines), "there is no line after the header"))

    read: list[Line] = []
    placed: list[_Placed] = []
    for number, line in enumerate(lines[2:], start=3):
        if not line:
            errors.append(YupError(number, "an empty line"))
            continue
        fields = line.split("\t")
        if len(fields) != 6:
            errors.append(
                YupError(
                    number, f"a line has 6 tab-separated fields, not {len(fields)}"
                )
            )
            continue
        good, problems, named = _line(number, fields)
        errors.extend(YupError(number, problem) for problem in problems)
        placed.append(named)
        if good is not None:
            read.append(good)
    errors.extend(_across_lines(placed))
    if errors:
        return Err(tuple(sorted(errors, key=lambda error: error.line)))
    return Ok(
        Yup(
            sheets=tuple(dict.fromkeys(line.sheet for line in read)),
            cells=tuple(line for line in read if isinstance(line, Cell)),
            blanks=tuple(line for line in read if isinstance(line, Blank)),
            columns=tuple(line for line in read if isinstance(line, Column)),
            rows=tuple(line for line in read if isinstance(line, Row)),
        )
    )
