"""The documents' examples: the README's is a good file and the README shows it, and the
specification's is a good file too."""

import re
from pathlib import Path

from yupana import read_yup, write_xlsx

ROOT = Path(__file__).resolve().parents[3]
README = ROOT / "README.md"
SPEC = ROOT / "SPEC.md"
EXAMPLE = Path(__file__).resolve().parent / "examples" / "readme.yup"


def as_file(shown: str) -> str:
    """The file a docs example shows: each ``→``, with the spaces around it, is a tab.

    The spaces line the columns up for reading. A field that starts or ends with a space
    cannot be shown this way, and the comparison with the committed file catches one.
    """
    return re.sub(r" *→ *", "\t", shown)


def shown(document: Path) -> str:
    """The first ``.yup`` file a document shows, as the file itself."""
    text = document.read_bytes().decode()
    block = re.search(r"```text\n(yup .*?)```", text, re.DOTALL)
    assert block is not None
    return as_file(block.group(1))


def test_the_readme_example_is_a_good_yup_file() -> None:
    yup = read_yup(EXAMPLE.read_bytes().decode()).unwrap()
    assert (len(yup.cells), len(yup.columns)) == (4, 2)
    assert write_xlsx(yup).is_ok()


def test_the_readme_shows_the_example_file() -> None:
    assert shown(README) == EXAMPLE.read_bytes().decode()


def test_the_specifications_example_is_a_good_yup_file() -> None:
    yup = read_yup(shown(SPEC)).unwrap()
    assert (len(yup.cells), len(yup.columns), len(yup.rows)) == (8, 2, 1)
    assert write_xlsx(yup).is_ok()


def test_an_empty_field_is_still_a_field() -> None:
    assert as_file("Model →  2  →  1  →  $   → Revenue →\n") == (
        "Model\t2\t1\t$\tRevenue\t\n"
    )
    assert as_file("Model →  *  →  1  →  |   →         → columnwidth=20\n") == (
        "Model\t*\t1\t|\t\tcolumnwidth=20\n"
    )
