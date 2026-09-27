"""Mapping what the app hands back to the values CSV. Runs anywhere."""

import pytest
from yupana.result import Err, Ok
from yupana.values import Type, Value
from yupana_xlsx_oracle.app_values import ERROR_OFFSET, ERRORS, error_text, from_app


@pytest.mark.parametrize(
    ("raw", "kind", "text"),
    [
        (True, Type.LOGICAL, "TRUE"),
        (False, Type.LOGICAL, "FALSE"),
        (0.0, Type.NUMBER, "0.0"),
        (-0.0, Type.NUMBER, "-0.0"),
        (0.1 + 0.2, Type.NUMBER, "0.30000000000000004"),
        (1e-300, Type.NUMBER, "1e-300"),
        ("", Type.TEXT, ""),
        ("a,b", Type.TEXT, "a,b"),
        (-2146826246, Type.ERROR, "#N/A"),
    ],
)
def test_a_value_maps_to_its_type(raw: object, kind: Type, text: str) -> None:
    assert from_app("S", 2, 3, raw) == Ok(Value("S", 2, 3, kind, text))


def test_a_logical_is_not_mistaken_for_an_error() -> None:
    assert from_app("S", 1, 1, True).unwrap().type is Type.LOGICAL


@pytest.mark.parametrize(("number", "shown"), sorted(ERRORS.items()))
def test_every_known_error_has_its_text(number: int, shown: str) -> None:
    assert error_text(number - ERROR_OFFSET) == shown


def test_an_unknown_error_is_kept() -> None:
    assert error_text(2099 - ERROR_OFFSET) == "#ERR2099"


@pytest.mark.parametrize("raw", [None, b"bytes", 1j])
def test_anything_else_is_reported(raw: object) -> None:
    assert isinstance(from_app("S", 1, 1, raw), Err)
