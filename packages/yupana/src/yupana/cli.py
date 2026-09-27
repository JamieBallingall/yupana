"""The ``yupana`` command.

::

    yupana check model.yup
    yupana xlsx  model.yup model.xlsx

``check`` reports every problem with a ``.yup`` file; ``xlsx`` writes it as a workbook, or
reports every cell it refuses. Problems go to standard error, one per line. The exit
status is 0 when all is well, 1 when anything was wrong, and 2 for a malformed command.
"""

import argparse
import io
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from yupana.result import Err, Ok, Result
from yupana.xlsx import write_xlsx_file
from yupana.yup import Yup, read_yup


def _read(path: Path) -> Result[Yup, list[str]]:
    try:
        text = path.read_bytes().decode("utf-8")
    except OSError as error:
        return Err([f"{path}: could not read: {error.strerror}"])
    except UnicodeDecodeError as error:
        return Err([f"{path}: not UTF-8: {error.reason} at byte {error.start}"])
    return read_yup(text).map_err(lambda errors: [f"{path}: {e}" for e in errors])


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yupana", description="Check a .yup file, or write it as xlsx."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="report every problem with a .yup file")
    check.add_argument("yup", type=Path)
    xlsx = commands.add_parser("xlsx", help="write a .yup file as an xlsx workbook")
    xlsx.add_argument("yup", type=Path)
    xlsx.add_argument("xlsx", type=Path)
    return parser


def main(
    argv: Sequence[str] | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """Run the command, and return its exit status."""
    out = out or sys.stdout
    err = err or sys.stderr
    arguments = _parser().parse_args(argv)
    match _read(arguments.yup):
        case Err(problems):
            err.writelines(f"{problem}\n" for problem in problems)
            return 1
        case Ok(yup):
            pass
    if arguments.command == "check":
        cells, sheets = len(yup.cells), len(yup.sheets)
        out.write(
            f"{arguments.yup}: {cells} {'cell' if cells == 1 else 'cells'} "
            f"on {sheets} {'sheet' if sheets == 1 else 'sheets'}\n"
        )
        return 0
    match write_xlsx_file(yup, arguments.xlsx):
        case Err(refused):
            err.writelines(f"{arguments.yup}: {error}\n" for error in refused)
            return 1
        case Ok():
            return 0


def run() -> None:
    """The console entry point: UTF-8 and LF on every stream, whatever the console."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(
                encoding="utf-8", errors="backslashreplace", newline="\n"
            )
    sys.exit(main())
