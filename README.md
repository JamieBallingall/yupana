# yupana

**A spreadsheet as text.** A `.yup` file lists the cells of a workbook, one per line: where each cell is, what is in it, and how it is formatted. Other lines size its columns and rows. `yupana` reads a `.yup` file, checks it against [the specification](SPEC.md), reporting every problem at once, and writes it as an xlsx workbook of live formulas.

**Not stable yet.** The format is at version 0.0.2, and it, the API and the names may all change.

## Why

A workbook built by a program is easiest to check, diff and review as text. A `.yup` file is that text: a straight-line program laid out on a grid, where every formula refers only to cells written before it. A program writes it; `yupana` turns it into a workbook the spreadsheet app opens without complaint.

## A `.yup` file

A line naming the format and its version, then tab-separated lines with a header. Here each `→`, with the spaces around it, stands for a tab, so that the columns line up for reading. The file itself, with real tabs, is [`packages/yupana/tests/examples/readme.yup`](packages/yupana/tests/examples/readme.yup):

```text
yup 0.0.2 Yupana Straight Line Spreadsheet Format
sheet → row → col → type → cell        → format
Model →  *  →  1  →  |   →             → columnwidth=20
Model →  *  →  2  →  |   →             → columnwidth=10
Model →  1  →  1  →  $   → Revenue     →
Model →  1  →  2  →  #   → 1000        → numberformat=#,##0.0
Model →  2  →  1  →  $   → Next year   → indent=1
Model →  2  →  2  →  =   → B1*(1+0.08) → numberformat=#,##0.0
```

The type says what a line is: `=` a formula, `#` a number, `$` a text, `?` `TRUE` or `FALSE`, and `.` a blank cell, listed only for its format. A cell's format holds its number format, indent, font and fill. A `|` line sets a column's width and a `-` line a row's height, where `*` stands for every row or column. [`SPEC.md`](SPEC.md) has every rule.

## Use

```bash
uv run yupana check model.yup
uv run yupana xlsx model.yup model.xlsx
```

```python
from pathlib import Path

from yupana import read_yup, write_xlsx_file

yup = read_yup(Path("model.yup").read_bytes().decode()).unwrap()
write_xlsx_file(yup, Path("model.xlsx")).unwrap()
```

Every expected failure is a value, a `Result` from `yupana.result`: `read_yup` returns every problem with the file, each with its line, and the writer every cell it refuses.

## What the writer writes

- Numbers, text, `TRUE` and `FALSE`, and formulas, on as many sheets as the file names.
- Number formats and indents, on cells with contents or on blank cells.
- Bold, italic, four kinds of underline, font colours, and solid fills.
- Column widths and row heights, for single columns and rows or for every one, and hidden columns and rows.
- **Formula cells carry no cached value.** The spreadsheet app computes every formula when it opens the file, but a program that reads the file as data sees no value in them.
- The same `.yup` file always gives the same bytes.

It refuses, naming the cell and its line, a number format whose spelling in the file it cannot be sure of. Everything else that could make the app refuse a file, the reader has already refused.

## The two packages

This repository is a uv workspace:

- **`yupana`** (`packages/yupana`): the reader, the values CSV and the xlsx writer. Standard library only; runs anywhere.
- **`yupana-xlsx-oracle`** (`packages/yupana-xlsx-oracle`): has the spreadsheet app itself compute a `.yup` or xlsx file, and compares what it computed with a values CSV. It needs Windows and the app, and is used only for checking: nothing depends on it.

The oracle is how the writer is checked: for every file in [`fixtures`](fixtures), the app's values for the writer's workbook must equal its values for the `.yup` file, exactly.

## Install from source

```bash
git clone https://github.com/JamieBallingall/yupana.git && cd yupana
uv sync
uv run pytest
```

On a machine without the app, the tests that need it are skipped.

## The name

A yupana is an Inca counting board: a grid of cells for calculating. The word is Quechua, from *yupay*, "to count". See [Yupana](https://en.wikipedia.org/wiki/Yupana).
