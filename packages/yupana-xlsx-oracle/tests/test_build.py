"""``build``: the app computes a ``.yup`` file, cell by cell, as written."""

import re
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from yupana.result import Err, Ok
from yupana.values import Type, Value
from yupana.yup import read_yup
from yupana_xlsx_oracle.errors import Rejected
from yupana_xlsx_oracle.session import Session
from yupana_xlsx_oracle.workbook import build

TARGET = Path(__file__).resolve().parents[3] / "target" / "oracle-tests"
HEAD = "sheet\trow\tcol\tcell\tformat\n"


@pytest.fixture(scope="module")
def session() -> Iterator[Session]:
    with Session() as opened:
        yield opened


def yup(*lines: str) -> str:
    return HEAD + "".join(line + "\n" for line in lines)


def values_of(session: Session, text: str) -> list[tuple[int, str]]:
    computed = build(session, read_yup(text).unwrap()).unwrap()
    return [(int(v.type), v.value) for v in computed]


@pytest.mark.app
def test_numbers_logicals_and_formulas(session: Session) -> None:
    text = yup(
        "Model\t1\t1\t#2.5\tcolumnwidth=default",
        "Model\t2\t1\t#-0\t",
        "Model\t3\t1\t#1e-300\t",
        "Model\t4\t1\t?TRUE\t",
        "Model\t5\t1\t?FALSE\t",
        "Model\t6\t1\t=A1*4\t",
        "Model\t7\t1\t=0.1+0.2\t",
        "Model\t8\t1\t=AND(A4,A5)\t",
        "Model\t9\t1\t=SUM(A1:A3)+MIN(A1,A6)+MAX(A1,A6)\t",
    )
    assert values_of(session, text) == [
        (1, "2.5"),
        (1, "0.0"),
        (1, "1e-300"),
        (4, "TRUE"),
        (4, "FALSE"),
        (1, "10.0"),
        (1, "0.30000000000000004"),
        (4, "FALSE"),
        (1, "15.0"),
    ]


AWKWARD = [
    "001", "1/2", "TRUE", "=1+1", "#N/A", "  padded  ", "50%", "1e5", "$5", "'quoted",
    "''two", "'", "-1", "(1)", "12:30", "Jan 1", "@home", "_x0041_", "\N{GRINNING FACE}",
    "c" * 32767,
]  # fmt: skip


@pytest.mark.app
def test_every_awkward_text_is_kept_exactly(session: Session) -> None:
    lines = [f"Model\t{r}\t1\t${t}\t" for r, t in enumerate(AWKWARD, start=1)]
    lines[0] += "columnwidth=default"
    assert values_of(session, yup(*lines)) == [(2, t) for t in AWKWARD]


@pytest.mark.app
def test_a_text_the_app_cannot_hold_is_rejected_not_truncated(session: Session) -> None:
    long = "'" + "c" * 32766
    text = yup("Model\t1\t1\t#1\tcolumnwidth=default", f"Model\t2\t1\t${long}\t")
    [rejected] = build(session, read_yup(text).unwrap()).unwrap_err()
    assert isinstance(rejected, Rejected)
    assert (rejected.line, rejected.row) == (3, 2)
    assert "starts with an apostrophe" in rejected.message


@pytest.mark.app
def test_every_error_reads_back_as_the_app_shows_it(session: Session) -> None:
    formulas = [
        ("=SUM(A1:A2 B1:B2)", "#NULL!"),
        ("=1/0", "#DIV/0!"),
        ('="a"+1', "#VALUE!"),
        ("=INDEX(A1:A2,3)", "#REF!"),
        ("=NOSUCHNAME", "#NAME?"),
        ("=SQRT(-1)", "#NUM!"),
        ("=NA()", "#N/A"),
    ]
    lines = [f"Model\t{r}\t3\t{f}\t" for r, (f, _) in enumerate(formulas, start=1)]
    lines[0] += "columnwidth=default"
    assert values_of(session, yup(*lines)) == [(16, e) for _, e in formulas]


@pytest.mark.app
def test_sheets_are_named_and_ordered_and_refer_to_each_other(session: Session) -> None:
    text = yup(
        "Inputs\t1\t1\t#40\tcolumnwidth=default",
        "My Model\t1\t1\t=Inputs!A1+2\tcolumnwidth=default",
        "It's\t1\t1\t='My Model'!A1/2\tcolumnwidth=default",
    )
    assert build(session, read_yup(text).unwrap()) == Ok(
        (
            Value("Inputs", 1, 1, Type.NUMBER, "40.0"),
            Value("My Model", 1, 1, Type.NUMBER, "42.0"),
            Value("It's", 1, 1, Type.NUMBER, "21.0"),
        )
    )


@pytest.mark.app
def test_formats_are_applied_and_saved_with_the_workbook(session: Session) -> None:
    text = yup(
        "Model\t1\t1\t$Label\tcolumnwidth=20|indent=2",
        "Model\t1\t2\t#1234.5\tcolumnwidth=10|numberformat=#,##0.0;(#,##0.0)",
        "Model\t1\t3\t$001\tcolumnwidth=0|numberformat=0.0%",
        "Model\t1\t4\t#1\tcolumnwidth=default",
    )
    path = TARGET / "formats.xlsx"
    computed = build(session, read_yup(text).unwrap(), path).unwrap()
    assert [v.value for v in computed] == ["Label", "1234.5", "001", "1.0"]
    with zipfile.ZipFile(path) as archive:
        styles = archive.read("xl/styles.xml").decode()
        sheet = archive.read("xl/worksheets/sheet1.xml").decode()
    assert 'formatCode="#,##0.0;\\(#,##0.0\\)"' in styles
    assert 'indent="2"' in styles
    cols = re.findall(r"<col [^>]*>", sheet)
    assert any('min="1" max="1" width="20.7109375"' in c for c in cols)
    assert any('min="2" max="2" width="10.7109375"' in c for c in cols)
    assert any('min="3" max="3"' in c and 'hidden="1"' in c for c in cols)


@pytest.mark.app
def test_a_number_format_the_app_rejects_is_an_error_on_that_cell(
    session: Session,
) -> None:
    text = yup(
        "Model\t1\t1\t#1\tcolumnwidth=default|numberformat=0.00e+00",
        "Model\t2\t1\t#2\tnumberformat=0.0",
    )
    match build(session, read_yup(text).unwrap()):
        case Err((Rejected(line=2, row=1, col=1, message=message),)):
            assert "the number format '0.00e+00'" in message
        case other:
            raise AssertionError(f"expected one rejected cell, not {other}")


@pytest.mark.app
def test_the_session_is_still_usable_after_a_rejection(session: Session) -> None:
    assert values_of(session, yup("S\t1\t1\t=1+1\tcolumnwidth=default")) == [(1, "2.0")]
