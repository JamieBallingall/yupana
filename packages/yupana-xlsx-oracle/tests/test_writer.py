"""The xlsx writer against the app: every workbook it writes opens, and computes to the
values the app computed for the same ``.yup`` file."""

import io
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from yupana.result import Err
from yupana.values import read_values
from yupana.xlsx import write_xlsx, write_xlsx_file
from yupana.yup import PREAMBLE, read_yup
from yupana_xlsx_oracle.compare import compare
from yupana_xlsx_oracle.errors import Refused
from yupana_xlsx_oracle.session import Session
from yupana_xlsx_oracle.workbook import build, read

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "fixtures"
TARGET = ROOT / "target" / "oracle-tests" / "writer"
NAMES = sorted(path.stem for path in FIXTURES.glob("*.yup"))


@pytest.fixture(scope="module")
def session() -> Iterator[Session]:
    with Session() as opened:
        yield opened


def fixture(name: str) -> str:
    return (FIXTURES / f"{name}.yup").read_bytes().decode()


@pytest.mark.app
@pytest.mark.parametrize("name", NAMES)
def test_the_writers_workbook_opens_and_computes_to_the_fixtures_values(
    session: Session, name: str
) -> None:
    yup = read_yup(fixture(name)).unwrap()
    expected = read_values((FIXTURES / f"{name}.values.csv").read_bytes().decode())
    path = TARGET / f"{name}.xlsx"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_xlsx_file(yup, path).unwrap()
    computed = read(session, path, yup).unwrap()
    assert compare(computed, expected.unwrap()) == ()


@pytest.mark.app
def test_the_writers_workbook_with_one_cell_written_twice_is_refused(
    session: Session,
) -> None:
    yup = read_yup(fixture("model")).unwrap()
    source = zipfile.ZipFile(io.BytesIO(write_xlsx(yup).unwrap()))
    faulty = io.BytesIO()
    with zipfile.ZipFile(faulty, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            content = source.read(info)
            if info.filename == "xl/worksheets/sheet1.xml":
                first = content.index(b'<c r="A1"')
                end = content.index(b"</c>", first) + len(b"</c>")
                content = content[:end] + content[first:end] + content[end:]
            target.writestr(info, content)
    path = TARGET / "cell-twice.xlsx"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(faulty.getvalue())
    match read(session, path, yup):
        case Err((Refused(),)):
            pass
        case other:
            raise AssertionError(f"expected a refusal, not {other}")
    assert session.stalled is None


@pytest.mark.app
def test_an_accounting_format_shows_as_the_app_shows_it(session: Session) -> None:
    code = "_(#,##0.0_);(#,##0.0);_(-_)"
    text = (
        PREAMBLE + "S\t*\t1\t|\t\tcolumnwidth=14\n"
        f"S\t1\t1\t#\t1234.5\tnumberformat={code}\n"
        f"S\t2\t1\t#\t-1234.5\tnumberformat={code}\n"
        f"S\t3\t1\t#\t0\tnumberformat={code}\n"
    )
    path = TARGET / "accounting.xlsx"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_xlsx_file(read_yup(text).unwrap(), path).unwrap()
    workbook = session.app.Workbooks.Open(str(path), UpdateLinks=0, ReadOnly=True)
    found = [workbook.Worksheets(1).Cells(row, 1).Text for row in (1, 2, 3)]
    workbook.Close(SaveChanges=False)
    assert found == [" 1,234.5 ", "(1,234.5)", " - "]


@pytest.mark.app
def test_the_app_sizes_columns_and_rows_as_the_writer_says(session: Session) -> None:
    path = TARGET / "sizes-read-back.xlsx"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_xlsx_file(read_yup(fixture("sizes")).unwrap(), path).unwrap()
    workbook = session.app.Workbooks.Open(str(path), UpdateLinks=0, ReadOnly=True)
    sizes, only = workbook.Worksheets(1), workbook.Worksheets(2)
    found = {
        "every column": sizes.StandardWidth,
        "column A": sizes.Columns(1).ColumnWidth,
        "column C hidden": sizes.Columns(3).Hidden,
        "column E": sizes.Columns(5).ColumnWidth,
        "every row": sizes.StandardHeight,
        "row 1": sizes.Rows(1).RowHeight,
        "row 3": sizes.Rows(3).RowHeight,
        "row 5 hidden": sizes.Rows(5).Hidden,
        "row 7": sizes.Rows(7).RowHeight,
        "row 100": sizes.Rows(100).RowHeight,
        "other sheet, column B": only.Columns(2).ColumnWidth,
        "other sheet, row 2": only.Rows(2).RowHeight,
    }
    workbook.Close(SaveChanges=False)
    # 4.5 characters is not a whole number of pixels, so the app shows the nearest.
    assert found == {
        "every column": 12.0,
        "column A": 30.0,
        "column C hidden": True,
        "column E": 12.0,
        "every row": 18.0,
        "row 1": 18.0,
        "row 3": 6.0,
        "row 5 hidden": True,
        "row 7": 409.0,
        "row 100": 18.0,
        "other sheet, column B": pytest.approx(4.5, abs=1 / 7),
        "other sheet, row 2": 30.0,
    }


@pytest.mark.app
def test_side_by_side_workbooks_for_a_human_to_compare(session: Session) -> None:
    """Both renderings of every fixture, for comparing by eye in the app:
    ``target/side-by-side/<name>.writer.xlsx`` and ``<name>.app.xlsx``."""
    folder = ROOT / "target" / "side-by-side"
    folder.mkdir(parents=True, exist_ok=True)
    for name in NAMES:
        yup = read_yup(fixture(name)).unwrap()
        write_xlsx_file(yup, folder / f"{name}.writer.xlsx").unwrap()
        build(session, yup, folder / f"{name}.app.xlsx").unwrap()
        for rendering in ("writer", "app"):
            assert sheet_names(session, folder / f"{name}.{rendering}.xlsx") == list(
                yup.sheets
            ), f"{name}.{rendering}.xlsx"
    assert len(list(folder.glob("*.xlsx"))) == 2 * len(NAMES)


def sheet_names(session: Session, path: Path) -> list[str]:
    """The sheets of a workbook as the app lists them, left to right."""
    workbook = session.app.Workbooks.Open(str(path), UpdateLinks=0, ReadOnly=True)
    names = [sheet.Name for sheet in workbook.Worksheets]
    workbook.Close(SaveChanges=False)
    return names
