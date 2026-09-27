# AGENTS.md

Instructions for coding agents working in this repository. README.md is the introduction.

## The person you are working with

Read HUMAN.md at the repository root if it exists. It describes the human you are working with
and how they work, including whether and when you may commit or push. It is personal and is never
committed. If there is no HUMAN.md, ask the human before committing, pushing, or doing anything
else that is hard to undo.

## What this is

`yupana` holds the `.yup` format: its specification, a reader that checks it, and a writer that
turns it into xlsx, along with the values-CSV format. It is a uv workspace with two packages:
`yupana` (standard library only, runs anywhere) and `yupana-xlsx-oracle`, which has the
spreadsheet app compute a file and is used only for checking. `ukumari` writes `.yup` files and
depends on `yupana`; nothing in `yupana` knows about `ukumari`.

## Sibling repositories

This repository is one of two, checked out side by side: `../yupana` and `../ukumari`. You may
read the other one, but never change it from here.

## Commands

    uv sync
    uv run ruff format
    uv run ruff check
    uv run ty check
    uv run pytest

## Style

- Python 3.14, typed throughout. ty and ruff, on ruff's defaults, both clean.
- Functional in flavour, Python in form: data is `@dataclass(frozen=True, slots=True)`; a sum type
  is a union of dataclasses, taken apart with `match`; nothing is mutated after construction.
  Never push it past what reads as natural Python.
- Expected failures are values: `Result` from the family's `result` module. A bug raises an
  ordinary built-in exception (`AssertionError` for a broken invariant), and nothing catches it.
  A `try` appears only as a narrow boundary that turns one expected exception into an `Err`.
- `yupana.result` is the family's one copy of `Result`; `ukumari` imports it. Change it only
  with both projects in mind.
- Text files are written with LF line endings on every platform: open them with `newline=""`
  (or write bytes), since Python's text mode on Windows turns `\n` into `\r\n`.
- Comments and docstrings say why, in the present tense. Doctests are the examples.
- Dates are absolute, such as 2026-09-26.

## Rules

- No third-party material: no downloaded workbooks, no data from anyone else. Examples use
  made-up numbers.
- No workbook is ever committed. Generated files go to `target/`.
- Nothing personal: no names, paths or e-mail addresses.
- Describe techniques generically, never as if from inside an organisation.
- Call the file format "xlsx" and the program "the spreadsheet app". No product names in any
  identifier, module, file or command.
- Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`), one concern
  per commit.

### In this repository

- The `yupana` package uses the standard library only. Its source never names the oracle: both
  packages share one environment, so a wrong-way import would go unnoticed, and a test in
  `yupana` fails if the oracle's name appears in its source.
- The oracle depends on `yupana` and on `pywin32` behind `sys_platform == 'win32'`, and imports
  `pywin32` lazily, inside the functions that need it, so it installs and its pure tests run
  anywhere. Its tests marked `app` need Windows with the spreadsheet app installed.
- `SPEC.md` is the specification of the `.yup` format, and the document other projects cite. The
  reader enforces exactly what it states.
- `packages/yupana/values-csv.md` specifies the values CSV, an internal format for testing. It is
  kept out of `SPEC.md` so that the standard stays about `.yup` alone.
- `plan/`, when present, is the working plan. It is never committed. A step in it is one commit.
  Where the plan and the code disagree, the code wins, and the plan is updated.
