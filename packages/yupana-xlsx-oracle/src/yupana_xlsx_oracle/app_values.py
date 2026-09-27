"""What the app hands back for a cell, as a line of the values CSV.

>>> from_app("S", 1, 1, 2.5), from_app("S", 1, 2, True)
(Ok(value=Value(sheet='S', row=1, col=1, type=<Type.NUMBER: 1>, value='2.5')), \
Ok(value=Value(sheet='S', row=1, col=2, type=<Type.LOGICAL: 4>, value='TRUE')))
>>> from_app("S", 1, 3, -2146826281).unwrap().value
'#DIV/0!'
"""

from yupana.result import Err, Ok, Result
from yupana.values import Type, Value, number_text

# A cell error comes back as a negative integer; adding this gives the app's error number.
ERROR_OFFSET = 2_146_828_288
ERRORS = {
    2000: "#NULL!",
    2007: "#DIV/0!",
    2015: "#VALUE!",
    2023: "#REF!",
    2029: "#NAME?",
    2036: "#NUM!",
    2042: "#N/A",
    2045: "#SPILL!",
    2050: "#CALC!",
}


def error_text(code: int) -> str:
    """The error an integer from the app stands for; an unknown one is kept, not dropped.

    >>> error_text(-2146826246), error_text(-2146826000)
    ('#N/A', '#ERR2288')
    """
    number = code + ERROR_OFFSET
    return ERRORS.get(number, f"#ERR{number}")


def from_app(sheet: str, row: int, col: int, raw: object) -> Result[Value, str]:
    """A cell's ``Value2`` as a value, or why it cannot be one.

    ``bool`` is tested before ``int``, since in Python a ``bool`` is an ``int``.
    """
    match raw:
        case bool():
            return Ok(Value(sheet, row, col, Type.LOGICAL, "TRUE" if raw else "FALSE"))
        case float():
            return Ok(Value(sheet, row, col, Type.NUMBER, number_text(raw)))
        case str():
            return Ok(Value(sheet, row, col, Type.TEXT, raw))
        case int():
            return Ok(Value(sheet, row, col, Type.ERROR, error_text(raw)))
        case None:
            return Err("the spreadsheet app holds no value for this cell")
        case _:
            return Err(f"the spreadsheet app handed back a {type(raw).__name__}")
