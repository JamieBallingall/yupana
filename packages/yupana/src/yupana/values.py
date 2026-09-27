"""The values CSV: the value computed for every cell of a ``.yup`` file.

>>> values = (
...     Value("Model", 1, 1, Type.TEXT, "Year"),
...     Value("Model", 1, 2, Type.NUMBER, number_text(2026.0)),
...     Value("Model", 2, 2, Type.ERROR, "#N/A"),
... )
>>> text = write_values(values)
>>> print(text, end="")
sheet,row,col,type,value
Model,1,1,2,Year
Model,1,2,1,2026.0
Model,2,2,16,#N/A
>>> read_values(text) == Ok(values)
True
"""

import csv
import io
import math
from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum

from yupana.result import Err, Ok, Result
from yupana.yup import (
    JSON_NUMBER,
    MAX_COL,
    MAX_ROW,
    position,
    quoted,
    sheet_name_problems,
)

HEADER = ("sheet", "row", "col", "type", "value")


class Type(IntEnum):
    """What kind of value a cell holds, as the code of the spreadsheet function ``TYPE``."""

    NUMBER = 1
    TEXT = 2
    LOGICAL = 4
    ERROR = 16


@dataclass(frozen=True, slots=True)
class Value:
    """One cell's computed value, written as the values CSV writes it.

    ``value`` is text: a number is the shortest text that reads back as the same double,
    so two values are the same exactly when their texts are.
    """

    sheet: str
    row: int
    col: int
    type: Type
    value: str


@dataclass(frozen=True, slots=True)
class ValuesError:
    """One problem with a values CSV, in the record that starts on ``line``."""

    line: int
    message: str

    def __str__(self) -> str:
        return f"line {self.line}: {self.message}"


def number_text(value: float) -> str:
    """A number's value text: the shortest text that reads back as the same double.

    >>> number_text(23.0), number_text(0.1), number_text(-0.0), number_text(1e16)
    ('23.0', '0.1', '-0.0', '1e+16')
    """
    assert math.isfinite(value), f"a values CSV holds only finite numbers, not {value}"
    return repr(float(value))


def value_problem(kind: Type, text: str) -> str | None:
    """Why a value's text does not suit its type, or ``None`` if it does.

    >>> value_problem(Type.NUMBER, "1e-300"), value_problem(Type.NUMBER, "nan")
    (None, 'a number is finite and in JSON\\'s grammar, not "nan"')
    """
    match kind:
        case Type.NUMBER:
            if JSON_NUMBER.fullmatch(text) and math.isfinite(float(text)):
                return None
            return f"a number is finite and in JSON's grammar, not {quoted(text)}"
        case Type.TEXT:
            return None
        case Type.LOGICAL:
            if text in ("TRUE", "FALSE"):
                return None
            return f"a logical is TRUE or FALSE, not {quoted(text)}"
        case Type.ERROR:
            if text.startswith("#"):
                return None
            return f"an error starts with #, not {quoted(text)}"


def write_values(values: Iterable[Value]) -> str:
    """A values CSV: RFC 4180 quoting and LF line endings."""
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(HEADER)
    for v in values:
        problem = value_problem(v.type, v.value)
        assert problem is None, f"{v}: {problem}"
        writer.writerow((v.sheet, v.row, v.col, int(v.type), v.value))
    return out.getvalue()


def _cr_outside_quotes(text: str) -> int | None:
    """The line of the first CR that is not inside a quoted field, or ``None``."""
    inside = False
    line = 1
    for character in text:
        if character == '"':
            inside = not inside
        elif character == "\n":
            line += 1
        elif character == "\r" and not inside:
            return line
    return None


def _record(line: int, fields: list[str]) -> Result[Value, tuple[ValuesError, ...]]:
    if len(fields) != 5:
        message = f"a record has 5 fields, not {len(fields)}"
        return Err((ValuesError(line, message),))
    sheet, row_text, col_text, type_text, value = fields
    problems = list(sheet_name_problems(sheet))
    row = position("row", row_text, MAX_ROW)
    col = position("col", col_text, MAX_COL)
    for result in (row, col):
        if isinstance(result, Err):
            problems.append(result.error)
    codes = {str(int(t)): t for t in Type}
    kind = codes.get(type_text)
    if kind is None:
        problems.append(f"type is 1, 2, 4 or 16, not {quoted(type_text)}")
    elif problem := value_problem(kind, value):
        problems.append(problem)
    match (row, col, kind):
        case (Ok(r), Ok(c), Type() as k) if not problems:
            return Ok(Value(sheet, r, c, k, value))
        case _:
            return Err(tuple(ValuesError(line, problem) for problem in problems))


def read_values(text: str) -> Result[tuple[Value, ...], tuple[ValuesError, ...]]:
    """A values CSV's records, checked, or every problem with it, each with its line."""
    errors: list[ValuesError] = []
    if text.startswith("﻿"):
        errors.append(ValuesError(1, "the file starts with a byte-order mark"))
        text = text[1:]
    if (line := _cr_outside_quotes(text)) is not None:
        errors.append(ValuesError(line, "a CR, where lines end with LF alone"))
        return Err(tuple(errors))
    if not text:
        errors.append(ValuesError(1, "the file is empty"))
        return Err(tuple(errors))
    if not text.endswith("\n"):
        errors.append(
            ValuesError(text.count("\n") + 1, "the last line does not end with LF")
        )
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    values: list[Value] = []
    records = 0
    start = 1
    try:
        for fields in reader:
            if start == 1:
                if tuple(fields) != HEADER:
                    message = f"the first line must be the header {','.join(HEADER)}"
                    errors.append(ValuesError(1, message))
            elif not fields:
                errors.append(ValuesError(start, "an empty line"))
            else:
                records += 1
                match _record(start, fields):
                    case Ok(value):
                        values.append(value)
                    case Err(problems):
                        errors.extend(problems)
            start = reader.line_num + 1
    except csv.Error as error:
        errors.append(ValuesError(start, f"malformed CSV: {error}"))
    if records == 0:
        errors.append(ValuesError(1, "there are no records"))
    if errors:
        return Err(tuple(sorted(errors, key=lambda error: error.line)))
    return Ok(tuple(values))
