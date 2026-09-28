"""The command line: inputs are checked before the app starts, then each command runs."""

import io
import subprocess
import sys
from pathlib import Path

import pytest
from yupana.yup import PREAMBLE
from yupana_xlsx_oracle.cli import main

TARGET = Path(__file__).resolve().parents[3] / "target" / "oracle-tests" / "cli"
YUP = (
    PREAMBLE + "Model\t1\t1\t$\tRevenue\t\n"
    "Model\t1\t2\t#\t1000\tnumberformat=#,##0\n"
    "Model\t2\t1\t$\tNext\t\n"
    "Model\t2\t2\t=\tB1*(1+0.08)\tnumberformat=#,##0\n"
    "Model\t*\t1\t|\t\tcolumnwidth=20\n"
    "Model\t*\t2\t|\t\tcolumnwidth=10\n"
)
VALUES = (
    "sheet,row,col,type,value\n"
    "Model,1,1,2,Revenue\n"
    "Model,1,2,1,1000.0\n"
    "Model,2,1,2,Next\n"
    "Model,2,2,1,1080.0\n"
)


def file(name: str, text: str) -> Path:
    path = TARGET / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    status = main(list(argv), out, err)
    return status, out.getvalue(), err.getvalue()


def test_a_malformed_command_exits_with_2() -> None:
    with pytest.raises(SystemExit) as exited:
        main(["build"], io.StringIO(), io.StringIO())
    assert exited.value.code == 2


def test_a_negative_tolerance_is_a_malformed_command() -> None:
    with pytest.raises(SystemExit) as exited:
        main(["compare", "a.yup", "b.csv", "--tolerance", "-1"])
    assert exited.value.code == 2


def test_a_missing_yup_file_is_reported_without_starting_the_app() -> None:
    status, _, err = run("build", str(TARGET / "missing.yup"))
    assert status == 1
    assert "missing.yup: could not read" in err


def test_a_yup_file_that_does_not_read_is_reported_line_by_line() -> None:
    broken = YUP.replace("\t1000\t", "\t01\t").replace("\tB1*(1+0.08)\t", "\t\t")
    path = file("broken.yup", broken)
    status, _, err = run("build", str(path))
    assert status == 1
    assert err.splitlines() == [
        f'{path}: line 4: a number is written in JSON\'s grammar, not "01"',
        f"{path}: line 6: a formula cannot be empty",
    ]


def test_a_file_that_is_not_utf8_is_reported() -> None:
    path = TARGET / "latin1.yup"
    path.write_bytes(YUP.replace("Revenue", "Revenüe").encode("latin-1"))
    status, _, err = run("build", str(path))
    assert status == 1
    assert "not UTF-8" in err


def test_expected_values_that_do_not_line_up_are_reported_before_computing() -> None:
    yup = file("model.yup", YUP)
    short = file("short.csv", VALUES.rsplit("Model,2,2", 1)[0])
    status, _, err = run("compare", str(yup), str(short))
    assert status == 1
    assert "lists 3 cells, and the .yup file 4" in err


@pytest.mark.app
def test_build_writes_the_values_and_the_workbook() -> None:
    yup = file("model.yup", YUP)
    values, xlsx = TARGET / "out" / "model.csv", TARGET / "out" / "model.xlsx"
    status, _, err = run(
        "build", str(yup), "--values", str(values), "--xlsx", str(xlsx)
    )
    assert (status, err) == (0, "")
    assert values.read_bytes().decode() == VALUES
    assert xlsx.read_bytes()[:2] == b"PK"


@pytest.mark.app
def test_build_without_values_prints_them() -> None:
    status, out, _ = run("build", str(file("model.yup", YUP)))
    assert (status, out) == (0, VALUES)


@pytest.mark.app
def test_read_computes_a_saved_workbook() -> None:
    yup = file("model.yup", YUP)
    xlsx = TARGET / "read" / "model.xlsx"
    assert run("build", str(yup), "--xlsx", str(xlsx))[0] == 0
    assert run("read", str(xlsx), str(yup)) == (0, VALUES, "")


@pytest.mark.app
def test_compare_agrees_and_disagrees() -> None:
    yup = file("model.yup", YUP)
    assert run("compare", str(yup), str(file("good.csv", VALUES))) == (
        0,
        "all 4 cells agree\n",
        "",
    )
    off = file("off.csv", VALUES.replace("1080.0", "1080.0000001"))
    status, _, err = run("compare", str(yup), str(off))
    assert status == 1
    assert err == (
        'sheet "Model", row 2, col 2: expected the number 1080.0000001, '
        "the app computed the number 1080.0\n"
    )
    assert run("compare", str(yup), str(off), "--tolerance", "1e-6")[0] == 0


@pytest.mark.app
def test_the_installed_command_writes_utf8_and_lf() -> None:
    yup = file("unicode.yup", YUP.replace("Revenue", "Rev\N{GRINNING FACE}"))
    finished = subprocess.run(
        [sys.executable, "-m", "yupana_xlsx_oracle", "build", str(yup)],
        capture_output=True,
        check=False,
    )
    assert finished.returncode == 0, finished.stderr
    assert finished.stdout == VALUES.replace("Revenue", "Rev\N{GRINNING FACE}").encode()
