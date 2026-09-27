"""Comparing values, in both modes, and checking that a values CSV lines up. Runs anywhere."""

import pytest
from yupana.values import Type, Value
from yupana.yup import read_yup
from yupana_xlsx_oracle.compare import compare, misaligned

N, T, L, E = Type.NUMBER, Type.TEXT, Type.LOGICAL, Type.ERROR


def one(kind: Type, value: str) -> list[Value]:
    return [Value("S", 1, 1, kind, value)]


@pytest.mark.parametrize(
    ("got", "want"),
    [
        ((N, "1.0"), (N, "1.0")),
        ((T, "a b"), (T, "a b")),
        ((L, "TRUE"), (L, "TRUE")),
        ((E, "#N/A"), (E, "#N/A")),
    ],
)
def test_identical_values_agree_in_both_modes(got: tuple, want: tuple) -> None:
    assert compare(one(*got), one(*want)) == ()
    assert compare(one(*got), one(*want), tolerance=0.0) == ()


@pytest.mark.parametrize(
    ("got", "want", "message"),
    [
        (
            (N, "0.0"),
            (N, "-0.0"),
            "expected the number -0.0, the app computed the number 0.0",
        ),
        ((N, "1.0"), (N, "1.0000000000000002"), "the number 1.0000000000000002"),
        (
            (E, "#N/A"),
            (E, "#DIV/0!"),
            "expected the error #DIV/0!, the app computed the error #N/A",
        ),
        ((T, "1"), (N, "1.0"), 'the app computed the text "1"'),
        ((L, "TRUE"), (N, "1.0"), "the app computed the logical TRUE"),
        ((T, "a"), (T, "A"), 'expected the text "A"'),
    ],
)
def test_exact_mode_compares_type_and_text(
    got: tuple, want: tuple, message: str
) -> None:
    [disagreement] = compare(one(*got), one(*want))
    assert message in str(disagreement)
    assert str(disagreement).startswith('sheet "S", row 1, col 1: ')


@pytest.mark.parametrize(
    ("got", "want", "agree"),
    [
        ((N, "1.0"), (N, "1.0000000001"), True),
        ((N, "1.0"), (N, "1.00000001"), False),
        ((N, "0.0"), (N, "-0.0"), True),
        ((N, "1e300"), (N, "-1e300"), False),
        ((E, "#N/A"), (E, "#DIV/0!"), True),
        ((E, "#N/A"), (N, "1.0"), False),
        ((N, "1.0"), (E, "#N/A"), False),
        ((T, "a"), (T, "a"), True),
        ((T, "a"), (T, "b"), False),
        ((T, "1"), (N, "1.0"), False),
        ((L, "TRUE"), (L, "FALSE"), False),
    ],
)
def test_the_tolerance_applies_to_numbers_and_any_error_matches_any(
    got: tuple, want: tuple, agree: bool
) -> None:
    assert (compare(one(*got), one(*want), tolerance=1e-9) == ()) is agree


def test_every_disagreement_is_reported() -> None:
    got = [Value("S", r, 1, N, "1.0") for r in range(1, 4)]
    want = [Value("S", r, 1, N, v) for r, v in enumerate(["1.0", "2.0", "3.0"], 1)]
    assert [d.row for d in compare(got, want)] == [2, 3]


YUP = read_yup(
    "sheet\trow\tcol\tcell\tformat\nS\t1\t1\t#1\tcolumnwidth=default\nS\t2\t1\t#2\t\n"
).unwrap()


def test_values_that_line_up_are_not_misaligned() -> None:
    values = [Value("S", 1, 1, N, "1.0"), Value("S", 2, 1, N, "2.0")]
    assert misaligned(YUP, values, "x.csv") == ()


def test_a_missing_cell_is_misaligned() -> None:
    [problem] = misaligned(YUP, [Value("S", 1, 1, N, "1.0")], "x.csv")
    assert str(problem) == "x.csv: lists 1 cells, and the .yup file 2"


def test_cells_out_of_order_are_misaligned_at_the_first() -> None:
    values = [Value("S", 2, 1, N, "2.0"), Value("S", 1, 1, N, "1.0")]
    [problem] = misaligned(YUP, values, "x.csv")
    assert str(problem) == (
        'x.csv: record 1 is sheet "S", row 2, col 1, '
        'where line 2 of the .yup file is sheet "S", row 1, col 1'
    )
