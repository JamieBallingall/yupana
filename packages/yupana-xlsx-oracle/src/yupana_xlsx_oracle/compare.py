"""Comparing computed values with expected ones, cell by cell.

Two modes:

- **exact** (``tolerance`` is ``None``): a cell's type and value text must be identical.
  This is how the writer is checked, since both computations happen in the same app from
  the same formulas, so any difference is a finding.
- **with a tolerance** ``t``: for a model's own results. Numbers agree when
  ``|a - b| <= t``; an error agrees with an error of any kind; text and logicals must be
  identical; any other mismatch of type is a disagreement.

>>> from yupana.values import Type, Value
>>> app = [Value("S", 1, 1, Type.NUMBER, "0.30000000000000004")]
>>> mine = [Value("S", 1, 1, Type.NUMBER, "0.3")]
>>> for d in compare(app, mine):
...     print(d)
sheet "S", row 1, col 1: expected the number 0.3, the app computed the number 0.30000000000000004
>>> compare(app, mine, tolerance=1e-9)
()
"""

from collections.abc import Sequence

from yupana.values import Type, Value
from yupana.yup import Yup, quoted

from yupana_xlsx_oracle.errors import Disagreement, FileProblem, where


def _shown(value: Value) -> str:
    match value.type:
        case Type.NUMBER:
            return f"the number {value.value}"
        case Type.TEXT:
            return f"the text {quoted(value.value)}"
        case Type.LOGICAL:
            return f"the logical {value.value}"
        case Type.ERROR:
            return f"the error {value.value}"


def _agree(computed: Value, expected: Value, tolerance: float | None) -> bool:
    same = computed.type == expected.type and computed.value == expected.value
    if tolerance is None or same:
        return same
    match (computed.type, expected.type):
        case (Type.NUMBER, Type.NUMBER):
            return abs(float(computed.value) - float(expected.value)) <= tolerance
        case (Type.ERROR, Type.ERROR):
            return True
        case _:
            return False


def misaligned(
    yup: Yup, expected: Sequence[Value], path: str
) -> tuple[FileProblem, ...]:
    """Whether a values CSV lists exactly the ``.yup`` file's cells, in its order.

    Only the first misplaced cell is named, since every later one would follow from it.
    """
    problems: list[FileProblem] = []
    if len(expected) != len(yup.cells):
        problems.append(
            FileProblem(
                path,
                f"lists {len(expected)} cells, and the .yup file {len(yup.cells)}",
            )
        )
    for number, (cell, value) in enumerate(zip(yup.cells, expected, strict=False), 1):
        if (cell.sheet, cell.row, cell.col) != (value.sheet, value.row, value.col):
            problems.append(
                FileProblem(
                    path,
                    f"record {number} is {where(value.sheet, value.row, value.col)}, "
                    f"where line {cell.line} of the .yup file is "
                    f"{where(cell.sheet, cell.row, cell.col)}",
                )
            )
            break
    return tuple(problems)


def compare(
    computed: Sequence[Value],
    expected: Sequence[Value],
    tolerance: float | None = None,
) -> tuple[Disagreement, ...]:
    """Every cell whose computed value disagrees with the expected one.

    Both lists name the same cells in the same order: ``misaligned`` checks that first.
    """
    assert len(computed) == len(expected), "compare needs aligned values"
    disagreements: list[Disagreement] = []
    for got, want in zip(computed, expected, strict=True):
        assert (got.sheet, got.row, got.col) == (want.sheet, want.row, want.col)
        if not _agree(got, want, tolerance):
            disagreements.append(
                Disagreement(
                    want.sheet,
                    want.row,
                    want.col,
                    f"expected {_shown(want)}, the app computed {_shown(got)}",
                )
            )
    return tuple(disagreements)
