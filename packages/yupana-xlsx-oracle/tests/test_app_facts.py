"""Facts about the app that the spec and the writer rely on, established in plan step 28.

Each writes a workbook with the xlsx writer, edits one thing into it where the writer
would not write it, and has the app open it.
"""

import io
import re
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from yupana.xlsx import write_xlsx
from yupana.yup import read_yup
from yupana_xlsx_oracle.session import Session, com_error

TARGET = Path(__file__).resolve().parents[3] / "target" / "oracle-tests" / "facts"
HEAD = "sheet\trow\tcol\tcell\tformat\n"


@pytest.fixture(scope="module")
def session() -> Iterator[Session]:
    with Session() as opened:
        yield opened


def edited(text: str, part: str = "", old: str = "", new: str = "") -> bytes:
    """The writer's workbook for ``text``, with ``old`` replaced by ``new`` in ``part``."""
    source = zipfile.ZipFile(io.BytesIO(write_xlsx(read_yup(text).unwrap()).unwrap()))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            content = source.read(info).decode()
            if info.filename == part:
                assert old in content
                content = content.replace(old, new)
            target.writestr(info, content.encode())
    return out.getvalue()


def opened(session: Session, name: str, data: bytes) -> Any:
    """The workbook the app opened, recalculated, or ``None`` if it refused the file."""
    path = TARGET / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    try:
        workbook = session.app.Workbooks.Open(
            str(path), UpdateLinks=0, ReadOnly=True, CorruptLoad=0
        )
    except com_error():
        return None
    session.recalculate().unwrap()
    return workbook


@pytest.mark.app
@pytest.mark.parametrize(
    ("part", "old", "new", "read", "expected"),
    [
        ("xl/workbook.xml", 'name="S"', 'name="a_x0041_b"', "name", "aAb"),
        ("xl/sharedStrings.xml", "QQQQ", "_x0041_", "value", "A"),
        ("xl/worksheets/sheet1.xml", "QQQQ", "_x0041_", "formula value", "Ab"),
        ("xl/styles.xml", "QQQQ", "_x0041_", "shown", "5A"),
    ],
)
def test_the_app_decodes_the_x_shape_everywhere_the_writer_escapes_it(
    session: Session, part: str, old: str, new: str, read: str, expected: str
) -> None:
    text = HEAD + (
        "S\t1\t1\t$QQQQ\tcolumnwidth=20\n"
        'S\t2\t1\t="QQQQ"&"b"\t\n'
        'S\t3\t1\t#5\tnumberformat=0"QQQQ"\n'
    )
    workbook = opened(session, f"x-{read}.xlsx", edited(text, part, old, new))
    sheet = workbook.Worksheets(1)
    found = {
        "name": lambda: sheet.Name,
        "value": lambda: sheet.Cells(1, 1).Value2,
        "formula value": lambda: sheet.Cells(2, 1).Value2,
        "shown": lambda: sheet.Cells(3, 1).Text,
    }[read]()
    workbook.Close(SaveChanges=False)
    assert found == expected


@pytest.mark.app
@pytest.mark.parametrize(
    ("first", "second", "opens"),
    [
        ("Straße", "Other", True),
        ("Straße", "STRASSE", False),
        ("σ", "ς", False),
        ("ﬁle", "FILE", False),
        ("ı", "I", True),
    ],
)
def test_the_app_refuses_sheet_names_equal_under_case_folding(
    session: Session, first: str, second: str, opens: bool
) -> None:
    text = (
        HEAD
        + "One\t1\t1\t#1\tcolumnwidth=default\nTwo\t1\t1\t#2\tcolumnwidth=default\n"
    )
    data = edited(text, "xl/workbook.xml", 'name="One"', f'name="{first}"')
    source = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            content = source.read(info).decode()
            content = content.replace('name="Two"', f'name="{second}"')
            target.writestr(info, content.encode())
    assert (first.casefold() == second.casefold()) is not opens
    workbook = opened(session, "case.xlsx", out.getvalue())
    assert (workbook is not None) is opens
    if workbook is not None:
        workbook.Close(SaveChanges=False)


@pytest.mark.app
def test_a_text_is_at_most_32767_utf16_units_in_a_file(session: Session) -> None:
    face = "\N{GRINNING FACE}"
    text = HEAD + f"S\t1\t1\t${face * 16383}a\tcolumnwidth=default\n"
    workbook = opened(session, "longest.xlsx", edited(text))
    assert workbook.Worksheets(1).Cells(1, 1).Value2 == face * 16383 + "a"
    workbook.Close(SaveChanges=False)
    too_long = edited(text, "xl/sharedStrings.xml", f"{face}a<", f"{face}{face}<")
    assert opened(session, "too-long.xlsx", too_long) is None


@pytest.mark.app
def test_every_finite_double_is_kept_and_a_subnormal_is_not(session: Session) -> None:
    text = HEAD + (
        "S\t1\t1\t#1.7976931348623157e308\tcolumnwidth=default\n"
        "S\t2\t1\t=A1/2\t\n"
        "S\t3\t1\t#1\t\n"
    )
    data = edited(text, "xl/worksheets/sheet1.xml", "<v>1.0</v>", "<v>5e-324</v>")
    workbook = opened(session, "doubles.xlsx", data)
    sheet = workbook.Worksheets(1)
    values = [sheet.Cells(r, 1).Value2 for r in (1, 2, 3)]
    workbook.Close(SaveChanges=False)
    assert values == [1.7976931348623157e308, 8.988465674311579e307, 0.0]


@pytest.mark.app
def test_a_number_inside_a_formula_keeps_15_significant_digits(
    session: Session,
) -> None:
    text = HEAD + "S\t1\t1\t=0.3333333333333333*3\tcolumnwidth=default\n"
    workbook = opened(session, "digits.xlsx", edited(text))
    value = workbook.Worksheets(1).Cells(1, 1).Value2
    workbook.Close(SaveChanges=False)
    assert value == 0.333333333333333 * 3 != 0.3333333333333333 * 3


@pytest.mark.app
def test_the_app_writes_the_defaults_the_writer_assumes(session: Session) -> None:
    workbook = session.app.Workbooks.Add()
    workbook.Worksheets(1).Cells(1, 1).Value2 = 1.0
    path = TARGET / "defaults.xlsx"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    workbook.SaveAs(str(path), FileFormat=51)
    workbook.Close(SaveChanges=False)
    with zipfile.ZipFile(path) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml").decode()
        styles = archive.read("xl/styles.xml").decode()
    assert re.search(r'<sheetFormatPr defaultRowHeight="15"', sheet)
    assert re.search(r'<font><sz val="11"/>.*?<name val="Aptos Narrow"/>', styles)
