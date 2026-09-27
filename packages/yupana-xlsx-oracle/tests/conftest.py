"""Tests marked ``app`` run only on Windows with pywin32 and the spreadsheet app, set to
en-US's number and date conventions."""

import importlib.util
import sys

import pytest
from yupana_xlsx_oracle.session import Session, app_installed


def _app_available() -> bool:
    return (
        sys.platform == "win32"
        and importlib.util.find_spec("win32com") is not None
        and app_installed()
    )


def _why_not(items: list[pytest.Item]) -> str | None:
    """Why the tests marked ``app`` cannot run, or ``None`` if they can.

    The app is started only when such a test was collected.
    """
    if not any("app" in item.keywords for item in items):
        return None
    if not _app_available():
        return "needs Windows, pywin32 and the spreadsheet app"
    with Session() as session:
        unlike = session.unlike_en_us()
    if unlike:
        return "needs the app set to en-US's conventions: " + "; ".join(unlike)
    return None


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    reason = _why_not(items)
    if reason is None:
        return
    skip = pytest.mark.skip(reason=reason)
    for item in items:
        if "app" in item.keywords:
            item.add_marker(skip)


def pytest_make_parametrize_id(
    config: pytest.Config, val: object, argname: str
) -> str | None:
    text = repr(val)
    return text if len(text) <= 40 else f"{text[:30]}...({len(text)} chars)"
