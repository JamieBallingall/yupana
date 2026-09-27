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
@pytest.mark.parametrize(
    ("code", "shown"),
    [
        ("_(#,##0.0_);(#,##0.0);_(-_)", [" 1,234.5 ", "(1,234.5)", " - "]),
        ("_(#,##0.0);(#,##0.0);_(-_)", [" 1,234.5)", "(1,234.5)", " - "]),
    ],
)
def test_an_accounting_format_shows_as_the_app_shows_it(
    session: Session, code: str, shown: list[str]
) -> None:
    text = (
        PREAMBLE + f"S\t1\t1\t#1234.5\tcolumnwidth=14|numberformat={code}\n"
        f"S\t2\t1\t#-1234.5\tnumberformat={code}\n"
        f"S\t3\t1\t#0\tnumberformat={code}\n"
    )
    path = TARGET / "accounting.xlsx"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_xlsx_file(read_yup(text).unwrap(), path).unwrap()
    workbook = session.app.Workbooks.Open(str(path), UpdateLinks=0, ReadOnly=True)
    found = [workbook.Worksheets(1).Cells(row, 1).Text for row in (1, 2, 3)]
    workbook.Close(SaveChanges=False)
    assert found == shown


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
