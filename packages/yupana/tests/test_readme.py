"""The README's example file: the committed copy is a good file, and the README shows it."""

import re
from pathlib import Path

from yupana import read_yup, write_xlsx

README = Path(__file__).resolve().parents[3] / "README.md"
EXAMPLE = Path(__file__).resolve().parent / "examples" / "readme.yup"


def as_file(shown: str) -> str:
    """The file a docs example shows: each ``→``, with the spaces around it, is a tab.

    The spaces line the columns up for reading. A field that starts or ends with a space
    cannot be shown this way, and the comparison with the committed file catches one.
    """
    return re.sub(r" *→ *", "\t", shown)


def test_the_readme_example_is_a_good_yup_file() -> None:
    yup = read_yup(EXAMPLE.read_bytes().decode()).unwrap()
    assert len(yup.cells) == 4
    assert write_xlsx(yup).is_ok()


def test_the_readme_shows_the_example_file() -> None:
    text = README.read_bytes().decode()
    block = re.search(r"```text\n(yup .*?)```", text, re.DOTALL)
    assert block is not None
    assert as_file(block.group(1)) == EXAMPLE.read_bytes().decode()


def test_an_empty_format_still_ends_with_a_tab() -> None:
    assert (
        as_file("Model →  2  →  1  → $Revenue     →\n") == "Model\t2\t1\t$Revenue\t\n"
    )
