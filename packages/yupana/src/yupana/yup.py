"""The ``.yup`` format: its types, and a reader that checks a file against ``SPEC.md``.

>>> text = (
...     PREAMBLE
...     + "Model\\t1\\t1\\t$Revenue\\tcolumnwidth=20\\n"
...     "Model\\t1\\t2\\t#1000\\tcolumnwidth=default|numberformat=#,##0\\n"
...     "Model\\t2\\t2\\t=B1*2\\t\\n"
... )
>>> yup = read_yup(text).unwrap()
>>> yup.sheets
('Model',)
>>> [cell.content for cell in yup.cells]
[Text(value='Revenue'), Number(value=1000.0), Formula(text='=B1*2')]
>>> yup.cells[1].format
Format(number_format='#,##0', indent=None, column_width=Default())

A file with problems is refused with every one of them, each with its line:

>>> broken = text.replace("#1000", "#01").replace("\\t2\\t2\\t", "\\t0\\t2\\t")
>>> for error in read_yup(broken).unwrap_err():
...     print(error)
line 4: a number is written in JSON's grammar, not "01"
line 5: row must be a whole number from 1 to 1048576, not "0"
"""

import json
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass

from yupana.result import Err, Ok, Result

VERSION = "0.0.1"
# The first line names the format and its version, so that a file says what it is, and a
# reader of a later version can tell which rules a file was written to.
VERSION_LINE = f"yup {VERSION} Yupana Straight Line Spreadsheet Format"
HEADER = "sheet\trow\tcol\tcell\tformat"
PREAMBLE = f"{VERSION_LINE}\n{HEADER}\n"
"""The two lines every ``.yup`` file starts with, for a writer to begin with."""
MAX_ROW = 1_048_576
MAX_COL = 16_384
MAX_SHEET_NAME = 31
MAX_TEXT = 32_767
MAX_FORMULA = 8_192
MAX_INDENT = 250
MAX_COLUMN_WIDTH = 255.0
SMALLEST_NORMAL = 2.2250738585072014e-308

_INTEGER = re.compile(r"[1-9][0-9]*")
JSON_NUMBER = re.compile(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?")
ESCAPE_SHAPE = re.compile(r"_x[0-9A-Fa-f]{4}_")
_NOT_IN_SHEET_NAME = ":\\/?*[]"
_KEYS = ("numberformat", "indent", "columnwidth")


@dataclass(frozen=True, slots=True)
class Formula:
    """A formula as it is typed into a cell, so its text starts with ``=``."""

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
class Default:
    """The spreadsheet app's standard column width."""


@dataclass(frozen=True, slots=True)
class Format:
    """A cell's formats. ``None`` means the key is absent."""

    number_format: str | None = None
    indent: int | None = None
    column_width: float | Default | None = None


@dataclass(frozen=True, slots=True)
class Cell:
    """One line of a ``.yup`` file. ``line`` is its 1-based line number in the file."""

    line: int
    sheet: str
    row: int
    col: int
    content: Content
    format: Format


@dataclass(frozen=True, slots=True)
class Yup:
    """A checked ``.yup`` file: its sheets in order of first appearance, and its cells."""

    sheets: tuple[str, ...]
    cells: tuple[Cell, ...]


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


def _content(cell: str) -> Result[Content, str]:
    kind, rest = cell[:1], cell[1:]
    match kind:
        case "=":
            if not rest:
                return Err("a formula needs something after the =")
            if bad := bad_character(rest):
                return Err(f"a formula cannot contain the character {bad}")
            if (length := utf16_length(cell)) > MAX_FORMULA:
                return Err(
                    f"a formula is at most {MAX_FORMULA} characters long, not {length}"
                )
            return Ok(Formula(cell))
        case "#":
            match _number(rest):
                case Ok(value):
                    return Ok(Number(value))
                case Err() as refused:
                    return refused
        case "$":
            if not rest:
                return Err("a text cannot be empty: a blank cell is not listed")
            if (length := utf16_length(rest)) > MAX_TEXT:
                return Err(
                    f"a text is at most {MAX_TEXT} characters long, not {length}"
                )
            if bad := bad_character(rest):
                return Err(f"a text cannot contain the character {bad}")
            return Ok(Text(rest))
        case "?":
            if rest in ("TRUE", "FALSE"):
                return Ok(Logical(rest == "TRUE"))
            return Err(f"a logical is TRUE or FALSE, not {quoted(rest)}")
        case "":
            return Err("the cell is empty: a blank cell is not listed")
        case _:
            return Err(f"a cell starts with =, #, $ or ?, not {quoted(kind)}")


def _indent(text: str) -> Result[int, str]:
    if (text == "0" or (_INTEGER.fullmatch(text) and len(text) <= 3)) and int(
        text
    ) <= MAX_INDENT:
        return Ok(int(text))
    return Err(
        f"indent must be a whole number from 0 to {MAX_INDENT}, not {quoted(text)}"
    )


def _column_width(text: str) -> Result[float | Default, str]:
    if text == "default":
        return Ok(Default())
    if JSON_NUMBER.fullmatch(text) and 0 <= (width := float(text)) <= MAX_COLUMN_WIDTH:
        return Ok(width + 0.0)
    return Err(
        f"columnwidth must be default or a number from 0 to 255, not {quoted(text)}"
    )


def _format(text: str) -> Result[Format, tuple[str, ...]]:
    if not text:
        return Ok(Format())
    problems: list[str] = []
    pairs: dict[str, str] = {}
    for pair in text.split("|"):
        key, equals, value = pair.partition("=")
        if not equals or not value:
            problems.append(f"a format pair is key=value, not {quoted(pair)}")
        elif key not in _KEYS:
            problems.append(f"unknown format key {quoted(key)}")
        elif key in pairs:
            problems.append(f"the format key {key} appears twice")
        else:
            pairs[key] = value
    number_format = pairs.get("numberformat")
    if number_format is not None and (bad := bad_character(number_format)):
        problems.append(f"a number format cannot contain the character {bad}")
    indent = pairs.get("indent")
    width = pairs.get("columnwidth")
    parsed_indent = None if indent is None else _indent(indent)
    parsed_width = None if width is None else _column_width(width)
    for result in (parsed_indent, parsed_width):
        if isinstance(result, Err):
            problems.append(result.error)
    if problems:
        return Err(tuple(problems))
    return Ok(
        Format(
            number_format=number_format,
            indent=None if parsed_indent is None else parsed_indent.unwrap(),
            column_width=None if parsed_width is None else parsed_width.unwrap(),
        )
    )


def _has_column_width(format_text: str) -> bool:
    return any(
        pair.partition("=")[0] == "columnwidth" for pair in format_text.split("|")
    )


@dataclass(frozen=True, slots=True)
class _Placed:
    """Where a line puts its cell, as far as it could be read, for the rules across lines."""

    line: int
    sheet: str
    row: int | None
    col: int | None
    has_column_width: bool


def _across_lines(placed: Iterable[_Placed]) -> list[YupError]:
    """The rules that no single line can break: spelling, duplicates and column widths."""
    errors: list[YupError] = []
    spelling: dict[str, str] = {}
    first_line: dict[tuple[str, int, int], int] = {}
    columns: set[tuple[str, int]] = set()
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
        if p.row is None or p.col is None:
            continue
        where = f"sheet {quoted(first)}, row {p.row}, col {p.col}"
        cell = (key, p.row, p.col)
        if cell in first_line:
            errors.append(
                YupError(p.line, f"{where} is already on line {first_line[cell]}")
            )
        else:
            first_line[cell] = p.line
        column = (key, p.col)
        if column not in columns:
            columns.add(column)
            if not p.has_column_width:
                errors.append(
                    YupError(
                        p.line,
                        f"the first line for sheet {quoted(first)}, col {p.col}, "
                        "must carry columnwidth",
                    )
                )
        elif p.has_column_width:
            errors.append(
                YupError(
                    p.line,
                    f"only the first line for sheet {quoted(first)}, col {p.col}, "
                    "may carry columnwidth",
                )
            )
    return errors


def _not_the_version_line(line: str) -> str:
    """Why a first line is not this version's, naming the version it claims, if any."""
    match line.split(" ", 2):
        case ["yup", version, *_] if version != VERSION:
            return f"this reader reads version {VERSION}, not {quoted(version)}"
        case _:
            return f"the first line must be {quoted(VERSION_LINE)}"


def read_yup(text: str) -> Result[Yup, tuple[YupError, ...]]:
    """A ``.yup`` file's cells, checked, or every problem with it, each with its line.

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
        errors.append(YupError(len(lines), "there are no cells"))

    cells: list[Cell] = []
    placed: list[_Placed] = []
    for number, line in enumerate(lines[2:], start=3):
        if not line:
            errors.append(YupError(number, "an empty line"))
            continue
        fields = line.split("\t")
        if len(fields) != 5:
            errors.append(
                YupError(
                    number, f"a line has 5 tab-separated fields, not {len(fields)}"
                )
            )
            continue
        sheet, row_text, col_text, cell_text, format_text = fields
        sheet_problems = sheet_name_problems(sheet)
        row = position("row", row_text, MAX_ROW)
        col = position("col", col_text, MAX_COL)
        content = _content(cell_text)
        cell_format = _format(format_text)
        problems = [*sheet_problems]
        for result in (row, col, content):
            if isinstance(result, Err):
                problems.append(result.error)
        if isinstance(cell_format, Err):
            problems.extend(cell_format.error)
        errors.extend(YupError(number, problem) for problem in problems)
        placed.append(
            _Placed(
                number,
                sheet,
                row.value if isinstance(row, Ok) else None,
                col.value if isinstance(col, Ok) else None,
                _has_column_width(format_text),
            )
        )
        match (row, col, content, cell_format):
            case (Ok(r), Ok(c), Ok(k), Ok(f)) if not sheet_problems:
                cells.append(Cell(number, sheet, r, c, k, f))
    errors.extend(_across_lines(placed))
    if errors:
        return Err(tuple(sorted(errors, key=lambda error: error.line)))
    return Ok(Yup(tuple(dict.fromkeys(c.sheet for c in cells)), tuple(cells)))
