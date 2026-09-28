"""What can go wrong when the spreadsheet app computes a file, each as a value."""

from dataclasses import dataclass

from yupana.yup import quoted

from yupana_xlsx_oracle.session import Stalled


def where(sheet: str, row: int | None, col: int | None) -> str:
    """A cell's position, or a row's or a column's, named as a ``.yup`` file names it.

    >>> where("Model", 2, 3), where("Model", None, 3)
    ('sheet "Model", row 2, col 3', 'sheet "Model", col 3')
    """
    named = [f"sheet {quoted(sheet)}"]
    if row is not None:
        named.append(f"row {row}")
    if col is not None:
        named.append(f"col {col}")
    return ", ".join(named)


@dataclass(frozen=True, slots=True)
class Refused:
    """The app refused to open a file. For a file the writer wrote, the worst failure."""

    path: str
    message: str

    def __str__(self) -> str:
        return f"the spreadsheet app refused to open {self.path}: {self.message}"


@dataclass(frozen=True, slots=True)
class Rejected:
    """The app rejected one line of a ``.yup`` file, or holds no usable value for a cell.

    A line for a column has no row, and a line for a row no col; the line for every
    column, or every row, has neither.
    """

    line: int
    sheet: str
    row: int | None
    col: int | None
    message: str

    def __str__(self) -> str:
        return f"line {self.line} ({where(self.sheet, self.row, self.col)}): {self.message}"


@dataclass(frozen=True, slots=True)
class FileProblem:
    """A file the oracle could not read, decode or write, or a sheet it could not find."""

    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


@dataclass(frozen=True, slots=True)
class Disagreement:
    """A cell whose computed value is not the one expected."""

    sheet: str
    row: int
    col: int
    message: str

    def __str__(self) -> str:
        return f"{where(self.sheet, self.row, self.col)}: {self.message}"


type OracleError = Refused | Rejected | FileProblem | Disagreement | Stalled


def describe(error: BaseException) -> str:
    """The app's own description of an exception it raised, on one line."""
    args = getattr(error, "args", ())
    detail = args[2] if len(args) > 2 else None
    if isinstance(detail, tuple) and len(detail) > 2 and detail[2]:
        text = str(detail[2])
    elif len(args) > 1 and args[1]:
        text = str(args[1])
    else:
        text = str(error)
    return " ".join(text.split())
