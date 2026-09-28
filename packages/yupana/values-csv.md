# The values CSV

**Internal.** A format for testing, not part of the `.yup` standard in [`SPEC.md`](../../SPEC.md), and it may change at any time. It carries the value computed for every cell of a `.yup` file, so that two computations of one file can be compared: a program that writes `.yup` files writes its own values beside them, and `yupana-xlsx-oracle` compares them with the spreadsheet app's. `yupana.values` reads and writes it.

## The file

- CSV with RFC 4180 quoting, UTF-8 with no byte-order mark, and LF line endings.
- The header is `sheet,row,col,type,value`.
- One line per cell of the `.yup` file that has contents (a line of type `=`, `#`, `$` or `?`), in the same order.
- `type` is the code of the spreadsheet function `TYPE`, and `value` is written accordingly:

| `type` | Means | `value` |
|---|---|---|
| `1` | number | The shortest text that reads back as the same double, as Python's `repr` writes it: `23.0`, `0.1`, `1e-300`, `-0.0`. Never `nan` or `inf` |
| `2` | text | The text itself, exactly. CSV quoting carries commas, quotes, tabs and newlines |
| `4` | logical | `TRUE` or `FALSE` |
| `16` | error | The error as the app shows it: `#N/A`, `#DIV/0!`, `#VALUE!`, `#REF!`, `#NAME?`, `#NUM!`, `#NULL!`. Any text starting with `#` is accepted, since apps have more |

There is no blank: every cell with contents was written, and a formula that returns empty text has type `2` and an empty value. A blank cell, listed only for its format, has no line.

## An example

The values CSV for the example `.yup` file in `SPEC.md`:

```text
sheet,row,col,type,value
Model,1,1,2,Year
Model,1,2,1,2026.0
Model,2,1,2,Revenue
Model,2,2,1,1000.0
Model,3,1,2,Growth
Model,3,2,1,0.08
Model,4,1,2,Next year
Model,4,2,1,1080.0
```

## What a reader checks

The header, that every line has five fields, that `row` and `col` are written as in `.yup`, that `type` is one of the four codes, and that each `value` suits its type: a number in JSON's grammar that is finite, `TRUE` or `FALSE`, or text starting with `#`.
