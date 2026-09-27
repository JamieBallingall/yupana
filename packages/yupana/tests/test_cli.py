"""The ``yupana`` command: check and xlsx."""

import io
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from yupana.cli import main

GOOD = "sheet\trow\tcol\tcell\tformat\nModel\t1\t1\t$Year\tcolumnwidth=10\n"


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    status = main(list(argv), out, err)
    return status, out.getvalue(), err.getvalue()


def written(tmp_path: Path, text: str, name: str = "model.yup") -> Path:
    path = tmp_path / name
    path.write_bytes(text.encode("utf-8"))
    return path


def test_check_reports_a_good_file(tmp_path: Path) -> None:
    path = written(tmp_path, GOOD)
    assert run("check", str(path)) == (0, f"{path}: 1 cell on 1 sheet\n", "")


def test_check_reports_every_problem(tmp_path: Path) -> None:
    path = written(tmp_path, GOOD + "Model\t0\t1\t#01\t\n")
    status, _, err = run("check", str(path))
    assert status == 1
    assert err.splitlines() == [
        f'{path}: line 3: row must be a whole number from 1 to 1048576, not "0"',
        f'{path}: line 3: a number is written in JSON\'s grammar, not "01"',
    ]


def test_a_file_that_is_missing_or_not_utf8_is_reported(tmp_path: Path) -> None:
    assert "could not read" in run("check", str(tmp_path / "missing.yup"))[2]
    path = tmp_path / "latin.yup"
    path.write_bytes(GOOD.replace("Year", "Ann\u00e9e").encode("latin-1"))
    assert "not UTF-8" in run("check", str(path))[2]


def test_xlsx_writes_a_workbook(tmp_path: Path) -> None:
    source = written(tmp_path, GOOD)
    target = tmp_path / "model.xlsx"
    assert run("xlsx", str(source), str(target)) == (0, "", "")
    assert zipfile.ZipFile(target).namelist()[0] == "[Content_Types].xml"


def test_xlsx_reports_every_refused_cell_and_writes_nothing(tmp_path: Path) -> None:
    source = written(
        tmp_path, GOOD.replace("columnwidth=10", "columnwidth=10|numberformat=0x")
    )
    target = tmp_path / "model.xlsx"
    status, _, err = run("xlsx", str(source), str(target))
    assert status == 1
    assert "line 2: " in err
    assert "cannot be written" in err
    assert not target.exists()


def test_a_malformed_command_exits_with_2() -> None:
    with pytest.raises(SystemExit) as exited:
        main(["xlsx", "only-one.yup"], io.StringIO(), io.StringIO())
    assert exited.value.code == 2


def test_the_module_runs_as_the_command(tmp_path: Path) -> None:
    path = written(tmp_path, GOOD.replace("Year", "\N{GRINNING FACE}"))
    finished = subprocess.run(
        [sys.executable, "-m", "yupana", "check", str(path)],
        capture_output=True,
        check=False,
    )
    assert finished.returncode == 0
    assert finished.stdout.endswith(b": 1 cell on 1 sheet\n")
