"""The ``yupana-xlsx-oracle`` command.

::

    yupana-xlsx-oracle build   model.yup  [--values model.csv] [--xlsx model.xlsx]
    yupana-xlsx-oracle read    model.xlsx model.yup [--values model.csv]
    yupana-xlsx-oracle compare model.yup  expected.csv [--xlsx model.xlsx] [--tolerance t]

Every input is read and checked before the app is started. Values go to ``--values``, or
to standard output. Every problem goes to standard error, one per line. The exit status is
0 when all is well, 1 when anything failed or disagreed, and 2 for a malformed command.
"""

import argparse
import io
import math
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import TextIO

from yupana.result import Err, Ok, Result
from yupana.values import Value, read_values, write_values
from yupana.yup import Yup, read_yup

from yupana_xlsx_oracle.compare import compare, misaligned
from yupana_xlsx_oracle.errors import FileProblem, OracleError
from yupana_xlsx_oracle.session import Session
from yupana_xlsx_oracle.workbook import build, read


def _text(path: Path) -> Result[str, tuple[OracleError, ...]]:
    try:
        data = path.read_bytes()
    except OSError as error:
        return Err((FileProblem(str(path), f"could not read: {error.strerror}"),))
    try:
        return Ok(data.decode("utf-8"))
    except UnicodeDecodeError as error:
        message = f"not UTF-8: {error.reason} at byte {error.start}"
        return Err((FileProblem(str(path), message),))


def _in_file(path: Path, errors: Iterable[object]) -> tuple[OracleError, ...]:
    """A reader's errors, each naming the file it was reading."""
    return tuple(FileProblem(str(path), str(e)) for e in errors)


def _yup(path: Path) -> Result[Yup, tuple[OracleError, ...]]:
    return _text(path).and_then(
        lambda text: read_yup(text).map_err(lambda errors: _in_file(path, errors))
    )


def _values(path: Path) -> Result[tuple[Value, ...], tuple[OracleError, ...]]:
    return _text(path).and_then(
        lambda text: read_values(text).map_err(lambda errors: _in_file(path, errors))
    )


def _write(path: Path | None, text: str, out: TextIO) -> tuple[OracleError, ...]:
    if path is None:
        out.write(text)
        return ()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    except OSError as error:
        return (FileProblem(str(path), f"could not write: {error.strerror}"),)
    return ()


def _tolerance(text: str) -> float:
    value = float(text)
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError(f"not a tolerance: {text!r}")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yupana-xlsx-oracle",
        description="Have the spreadsheet app compute a .yup or xlsx file.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    b = commands.add_parser("build", help="compute a .yup file in a fresh workbook")
    b.add_argument("yup", type=Path)
    b.add_argument("--values", type=Path, help="where to write the values CSV")
    b.add_argument("--xlsx", type=Path, help="also save the app's workbook here")
    r = commands.add_parser("read", help="compute an xlsx file")
    r.add_argument("xlsx", type=Path)
    r.add_argument("yup", type=Path, help="the .yup file listing the cells to read")
    r.add_argument("--values", type=Path, help="where to write the values CSV")
    c = commands.add_parser("compare", help="compute, then compare with a values CSV")
    c.add_argument("yup", type=Path)
    c.add_argument("expected", type=Path)
    c.add_argument("--xlsx", type=Path, help="compute this xlsx instead of the .yup")
    c.add_argument(
        "--tolerance",
        type=_tolerance,
        help="numbers agree within this; errors agree with errors (default: exact)",
    )
    return parser


def _report(errors: Sequence[OracleError], err: TextIO) -> int:
    err.writelines(f"{error}\n" for error in errors)
    return 1 if errors else 0


def main(
    argv: Sequence[str] | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """Run the command, and return its exit status."""
    out = out or sys.stdout
    err = err or sys.stderr
    arguments = _parser().parse_args(argv)
    match _yup(arguments.yup):
        case Err(errors):
            return _report(errors, err)
        case Ok(yup):
            pass
    expected: tuple[Value, ...] = ()
    if arguments.command == "compare":
        match _values(arguments.expected):
            case Err(errors):
                return _report(errors, err)
            case Ok(expected):
                pass
        if problems := misaligned(yup, expected, str(arguments.expected)):
            return _report(problems, err)
    with Session() as session:
        match arguments.command:
            case "build":
                computed = build(session, yup, arguments.xlsx)
            case "read":
                computed = read(session, arguments.xlsx, yup)
            case _:
                computed = (
                    read(session, arguments.xlsx, yup)
                    if arguments.xlsx is not None
                    else build(session, yup)
                )
    match computed:
        case Err(errors):
            return _report(errors, err)
        case Ok(values) if arguments.command == "compare":
            disagreements = compare(values, expected, arguments.tolerance)
            if not disagreements:
                out.write(f"all {len(values)} cells agree\n")
            return _report(disagreements, err)
        case Ok(values):
            return _report(_write(arguments.values, write_values(values), out), err)


def run() -> None:
    """The console entry point: UTF-8 and LF on every stream, whatever the console."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(
                encoding="utf-8", errors="backslashreplace", newline="\n"
            )
    sys.exit(main())
