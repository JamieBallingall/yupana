"""Short test ids, since pytest puts each id in an environment variable of limited length."""

import pytest


def pytest_make_parametrize_id(
    config: pytest.Config, val: object, argname: str
) -> str | None:
    text = repr(val)
    return text if len(text) <= 40 else f"{text[:30]}...({len(text)} chars)"
