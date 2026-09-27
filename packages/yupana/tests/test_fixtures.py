"""Every fixture reads cleanly, and its values CSV lists its cells in order."""

from pathlib import Path

import pytest
from yupana.values import read_values
from yupana.yup import read_yup

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"
NAMES = sorted(path.stem for path in FIXTURES.glob("*.yup"))


def test_there_are_fixtures() -> None:
    assert len(NAMES) >= 8


@pytest.mark.parametrize("name", NAMES)
def test_a_fixture_and_its_values_read_and_line_up(name: str) -> None:
    yup = read_yup((FIXTURES / f"{name}.yup").read_bytes().decode()).unwrap()
    values_text = (FIXTURES / f"{name}.values.csv").read_bytes().decode()
    values = read_values(values_text).unwrap()
    assert [(c.sheet, c.row, c.col) for c in yup.cells] == [
        (v.sheet, v.row, v.col) for v in values
    ]
