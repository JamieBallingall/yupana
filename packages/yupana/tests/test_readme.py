"""The README's example file reads cleanly, and writes."""

import re
from pathlib import Path

from yupana import read_yup, write_xlsx

README = Path(__file__).resolve().parents[3] / "README.md"


def test_the_readme_example_is_a_good_yup_file() -> None:
    text = README.read_bytes().decode()
    block = re.search(r"```text\n(yup .*?)```", text, re.DOTALL)
    assert block is not None
    yup = read_yup(block.group(1).replace("→", "\t")).unwrap()
    assert len(yup.cells) == 4
    assert write_xlsx(yup).is_ok()
