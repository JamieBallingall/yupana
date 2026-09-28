"""The xlsx writer against the app: every workbook it writes opens, computes to the
values the app computed for the same ``.yup`` file, and shows its formats as the file
gives them."""

import io
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from yupana.result import Err
from yupana.values import read_values
from yupana.xlsx import write_xlsx, write_xlsx_file
from yupana.yup import PREAMBLE, read_yup
from yupana_xlsx_oracle.compare import compare
from yupana_xlsx_oracle.errors import Refused
from yupana_xlsx_oracle.session import Session
from yupana_xlsx_oracle.workbook import bgr, build, read

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


def styles_shown(sheet: Any) -> dict[str, object]:
    """How the app shows each style in the ``styles`` fixture, by what it shows."""
    font = {row: sheet.Cells(row, 1).Font for row in range(1, 14)}

    def line(row: int, edge: int) -> tuple[int, int]:
        border = sheet.Cells(row, 2).Borders(edge)
        return border.LineStyle, border.Weight

    def aligned(row: int, col: int) -> tuple[int, int, bool, int]:
        cell = sheet.Cells(row, col)
        return (
            cell.HorizontalAlignment,
            cell.VerticalAlignment,
            cell.WrapText,
            cell.IndentLevel,
        )

    top, bottom, left, right = 8, 9, 7, 10
    return {
        "unaligned": aligned(1, 1),
        "left, centred and right": [aligned(row, 1)[0] for row in (29, 30, 31)],
        "a number left and centred": [aligned(row, 2)[0] for row in (29, 30)],
        "indented from the right": aligned(32, 1),
        "top, middle and bottom": [aligned(33, col)[1] for col in (1, 2, 3)],
        "wrapped": aligned(34, 1),
        "thin top": line(15, top),
        "medium bottom": line(17, bottom),
        "thick left": line(19, left),
        "double right": line(21, right),
        "dotted top, dashed bottom": (line(23, top), line(23, bottom)),
        "line colours": (
            sheet.Cells(25, 2).Borders(top).Color,
            sheet.Cells(25, 2).Borders(bottom).Color,
        ),
        "no line": sheet.Cells(15, 2).Borders(left).LineStyle,
        "bold": font[1].Bold,
        "italic": font[2].Italic,
        "bold and italic": (font[3].Bold, font[3].Italic),
        "underlines": [font[row].Underline for row in (4, 5, 6, 7)],
        "font colour": font[8].Color,
        "fill": sheet.Cells(9, 1).Interior.Color,
        "a filled blank cell": sheet.Cells(12, 2).Interior.Color,
        "plain": (font[13].Bold, font[13].Italic, font[13].Color),
        "unfilled": sheet.Cells(13, 1).Interior.ColorIndex,
    }


@pytest.mark.app
def test_the_app_shows_each_style_as_the_writer_says(session: Session) -> None:
    path = TARGET / "styles-read-back.xlsx"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_xlsx_file(read_yup(fixture("styles")).unwrap(), path).unwrap()
    workbook = session.app.Workbooks.Open(str(path), UpdateLinks=0, ReadOnly=True)
    found = styles_shown(workbook.Worksheets(1))
    workbook.Close(SaveChanges=False)
    # Underlines are the app's constants: single, double, and the two accounting ones.
    # A colour is an integer with red lowest, and an unfilled cell has no colour index.
    # A line is the app's style and weight: continuous (1) thin (2), medium (-4138) or
    # thick (4); double (-4119); dotted (-4118); dashed (-4115); none (-4142).
    # Alignments are too: general (1), left (-4131), centre (-4108), right (-4152); top
    # (-4160), centre (-4108), bottom (-4107).
    assert found == {
        "unaligned": (1, -4107, False, 0),
        "left, centred and right": [-4131, -4108, -4152],
        "a number left and centred": [-4131, -4108],
        "indented from the right": (-4152, -4107, False, 2),
        "top, middle and bottom": [-4160, -4108, -4107],
        "wrapped": (1, -4160, True, 0),
        "thin top": (1, 2),
        "medium bottom": (1, -4138),
        "thick left": (1, 4),
        "double right": (-4119, 4),
        "dotted top, dashed bottom": ((-4118, 2), (-4115, 2)),
        "line colours": (bgr("FF0000"), bgr("0070C0")),
        "no line": -4142,
        "bold": True,
        "italic": True,
        "bold and italic": (True, True),
        "underlines": [2, -4119, 4, 5],
        "font colour": bgr("0070C0"),
        "fill": bgr("DDEBF7"),
        "a filled blank cell": bgr("DDEBF7"),
        "plain": (False, False, 0),
        "unfilled": -4142,
    }


def views_shown(workbook: Any) -> list[tuple[bool, int, bool, int, int, object]]:
    """How the app shows each sheet: gridlines, zoom, whether panes are frozen, the
    rows and columns frozen, and the tab's colour. The window shows the active sheet."""
    shown = []
    for sheet in workbook.Worksheets:
        sheet.Activate()
        window = workbook.Windows(1)
        shown.append(
            (
                window.DisplayGridlines,
                window.Zoom,
                window.FreezePanes,
                window.SplitRow,
                window.SplitColumn,
                sheet.Tab.Color,
            )
        )
    return shown


@pytest.mark.app
def test_the_app_shows_each_sheet_as_the_writer_says(session: Session) -> None:
    found = []
    for name in ("styles", "sizes", "model"):
        path = TARGET / f"{name}-views.xlsx"
        path.parent.mkdir(parents=True, exist_ok=True)
        write_xlsx_file(read_yup(fixture(name)).unwrap(), path).unwrap()
        workbook = session.app.Workbooks.Open(str(path), UpdateLinks=0, ReadOnly=True)
        found += views_shown(workbook)
        workbook.Close(SaveChanges=False)
    # A tab without a colour has none: False.
    assert found == [
        (False, 85, True, 1, 1, bgr("0070C0")),
        (True, 100, True, 2, 0, False),
        (True, 120, True, 0, 1, bgr("00B050")),
        (True, 100, False, 0, 0, False),
    ]


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
