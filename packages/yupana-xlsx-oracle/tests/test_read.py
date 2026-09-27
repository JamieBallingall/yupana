"""``read``: the app opens an existing xlsx, and a file it will not open is refused."""

import io
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from yupana.result import Err
from yupana.yup import PREAMBLE, read_yup
from yupana_xlsx_oracle.errors import FileProblem, Refused
from yupana_xlsx_oracle.session import Session
from yupana_xlsx_oracle.workbook import build, read

TARGET = Path(__file__).resolve().parents[3] / "target" / "oracle-tests"
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE = "http://schemas.openxmlformats.org/package/2006/relationships"
SHEETML = "application/vnd.openxmlformats-officedocument.spreadsheetml"
YUP = f"{PREAMBLE}Model\t1\t1\t#1\tcolumnwidth=default\n"


def minimal_xlsx(cells: str) -> bytes:
    """A hand-built workbook of one sheet holding ``cells`` in its only row."""
    parts = {
        "[Content_Types].xml": (
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            f'<Override PartName="/xl/workbook.xml" ContentType="{SHEETML}.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" '
            f'ContentType="{SHEETML}.worksheet+xml"/></Types>'
        ),
        "_rels/.rels": (
            f'<Relationships xmlns="{PACKAGE}"><Relationship Id="rId1" '
            f'Type="{REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>'
        ),
        "xl/workbook.xml": (
            f'<workbook xmlns="{MAIN}" xmlns:r="{REL}"><sheets>'
            '<sheet name="Model" sheetId="1" r:id="rId1"/></sheets></workbook>'
        ),
        "xl/_rels/workbook.xml.rels": (
            f'<Relationships xmlns="{PACKAGE}"><Relationship Id="rId1" '
            f'Type="{REL}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'
        ),
        "xl/worksheets/sheet1.xml": (
            f'<worksheet xmlns="{MAIN}"><sheetData><row r="1">{cells}</row>'
            "</sheetData></worksheet>"
        ),
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, xml in parts.items():
            archive.writestr(name, '<?xml version="1.0" encoding="UTF-8"?>' + xml)
    return out.getvalue()


@pytest.fixture(scope="module")
def session() -> Iterator[Session]:
    with Session() as opened:
        yield opened


def written(name: str, content: bytes) -> Path:
    path = TARGET / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


@pytest.mark.app
def test_a_workbook_the_app_saved_reads_to_the_same_values(session: Session) -> None:
    text = (
        PREAMBLE + "Inputs\t1\t1\t#40\tcolumnwidth=default\n"
        "Model\t1\t1\t=Inputs!A1+2\tcolumnwidth=10\n"
        "Model\t2\t1\t$001\t\n"
        "Model\t3\t1\t=1/0\t\n"
    )
    yup = read_yup(text).unwrap()
    path = TARGET / "saved.xlsx"
    built = build(session, yup, path).unwrap()
    assert read(session, path, yup).unwrap() == built


@pytest.mark.app
def test_a_minimal_hand_built_workbook_opens(session: Session) -> None:
    path = written("minimal.xlsx", minimal_xlsx('<c r="A1"><v>1</v></c>'))
    [value] = read(session, path, read_yup(YUP).unwrap()).unwrap()
    assert value.value == "1.0"


@pytest.mark.app
@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("not-a-zip.xlsx", b"this is not a zip"),
        (
            "same-cell-twice.xlsx",
            minimal_xlsx('<c r="A1"><v>1</v></c><c r="A1"><v>2</v></c>'),
        ),
    ],
)
def test_a_file_the_app_will_not_open_is_refused_not_stalled(
    session: Session, name: str, content: bytes
) -> None:
    path = written(name, content)
    match read(session, path, read_yup(YUP).unwrap()):
        case Err((Refused(),)):
            pass
        case other:
            raise AssertionError(f"expected a refusal, not {other}")
    assert session.stalled is None


@pytest.mark.app
def test_a_missing_file_or_sheet_is_a_file_problem(session: Session) -> None:
    yup = read_yup(YUP.replace("Model", "Elsewhere")).unwrap()
    match read(session, TARGET / "no-such.xlsx", yup):
        case Err((FileProblem(message="no such file"),)):
            pass
        case other:
            raise AssertionError(f"expected a missing file, not {other}")
    path = written("minimal.xlsx", minimal_xlsx('<c r="A1"><v>1</v></c>'))
    match read(session, path, yup):
        case Err((FileProblem(message=message),)):
            assert "no sheet named 'Elsewhere'" in message
        case other:
            raise AssertionError(f"expected a missing sheet, not {other}")
