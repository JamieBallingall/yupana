"""`yupana` never names its oracle.

Both packages share one environment, so an import of the oracle from `yupana` would
work here and fail for anyone who installs `yupana` alone.
"""

import re
from pathlib import Path

PACKAGE = Path(__file__).parent.parent
ORACLE = re.compile(r"xlsx[-_]oracle")


def test_source_never_names_the_oracle() -> None:
    files = [PACKAGE / "pyproject.toml", *sorted((PACKAGE / "src").rglob("*.py"))]
    assert len(files) > 1
    naming = [f.name for f in files if ORACLE.search(f.read_text(encoding="utf-8"))]
    assert naming == []
