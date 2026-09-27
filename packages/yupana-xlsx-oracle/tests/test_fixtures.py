"""The app computes every fixture to exactly its committed values."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from yupana.values import read_values
from yupana.yup import read_yup
from yupana_xlsx_oracle.compare import compare
from yupana_xlsx_oracle.session import Session
from yupana_xlsx_oracle.workbook import build

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"
NAMES = sorted(path.stem for path in FIXTURES.glob("*.yup"))


@pytest.fixture(scope="module")
def session() -> Iterator[Session]:
    with Session() as opened:
        yield opened


@pytest.mark.app
@pytest.mark.parametrize("name", NAMES)
def test_the_app_computes_a_fixture_to_its_values(session: Session, name: str) -> None:
    yup = read_yup((FIXTURES / f"{name}.yup").read_bytes().decode()).unwrap()
    expected = read_values((FIXTURES / f"{name}.values.csv").read_bytes().decode())
    computed = build(session, yup).unwrap()
    assert compare(computed, expected.unwrap()) == ()
