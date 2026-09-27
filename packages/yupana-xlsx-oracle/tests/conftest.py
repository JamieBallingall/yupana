"""Tests marked ``app`` run only on Windows with pywin32 and the spreadsheet app."""

import importlib.util
import sys

import pytest
from yupana_xlsx_oracle.session import app_installed


def _app_available() -> bool:
    return (
        sys.platform == "win32"
        and importlib.util.find_spec("win32com") is not None
        and app_installed()
    )


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if _app_available():
        return
    skip = pytest.mark.skip(reason="needs Windows, pywin32 and the spreadsheet app")
    for item in items:
        if "app" in item.keywords:
            item.add_marker(skip)


def pytest_make_parametrize_id(
    config: pytest.Config, val: object, argname: str
) -> str | None:
    text = repr(val)
    return text if len(text) <= 40 else f"{text[:30]}...({len(text)} chars)"
