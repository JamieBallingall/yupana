# The `.yup` format and the values CSV

Version 1. **Not yet stable:** a later version may change anything here.

A `.yup` file lists the cells of a workbook, one per line: what is in each cell and how it is
formatted. It is a straight-line program laid out on a grid. A values CSV lists, for every cell of
a `.yup` file, the value computed for it.

In the examples below, `→` stands for a tab. Real tabs do not survive copying from rendered text.

## The `.yup` file

### Lines and fields

- UTF-8, with **no byte-order mark**.
- Lines end with LF. A CR anywhere is an error. The last line ends with LF too. There are no empty
  lines.
- Fields are separated by a tab. No field can contain a tab, a CR or an LF, and there is no quoting
  (the IANA `text/tab-separated-values` rule).
- The first line is exactly the header `sheet→row→col→cell→format`.
- Every other line has exactly five fields, in that order, and there is at least one such line.
- A line whose format is empty still has five fields, so it ends with a tab.
- **Lengths are counted in UTF-16 code units**: a character outside the Basic Multilingual Plane
  counts as two.

```text
sheet→row→col→cell→format
Model→1→1→$Year→columnwidth=34
Model→1→2→#2026→columnwidth=10|numberformat=0
Model→2→1→$Revenue→
Model→2→2→#1000→numberformat=#,##0.0;(#,##0.0)
Model→3→1→$Growth→indent=1
Model→3→2→#0.08→numberformat=0.0%
Model→4→1→$Next year→
Model→4→2→=B2*(1+B3)→numberformat=#,##0.0;(#,##0.0)
```

### Characters

Every sheet name, text, formula and number format:
- contains no C0 control character (U+0000 to U+001F), and not U+007F;
- contains neither U+FFFE nor U+FFFF, which XML 1.0 cannot carry;
- contains no surrogate code point (U+D800 to U+DFFF), which UTF-8 cannot encode.

### `sheet`

The sheet's name.
- 1 to 31 UTF-16 code units.
- None of `:` `\` `/` `?` `*` `[` `]`.
- Does not start or end with an apostrophe. One inside is fine.
- Not `History`, in any case: the spreadsheet app reserves it.
- Does not contain `_x` (with a lowercase `x`) followed by four hexadecimal digits and `_`. The xlsx format decodes that
  shape in text, so it is refused in a sheet name rather than risk its being decoded.
- Sheet names are compared **case-insensitively**, by Unicode case folding: `Data` and `data` name
  the same sheet, so a file that spells one sheet two ways is an error. Case folding equates more
  than some apps do (`Straße` and `STRASSE`), which refuses more rather than less.
- Leading and trailing spaces are part of the name.
- Sheets appear in the workbook in the order of their first appearance in the file.

### `row` and `col`

1-based positions, written as plain decimal integers with no sign and no leading zeros. `row` is 1
to 1,048,576 and `col` is 1 to 16,384.

### `cell`

What is in the cell. Its first character says what kind of thing it is.

| First character | Kind | The rest |
|---|---|---|
| `=` | formula | A1-notation formula text, such as `=B2*(1+B3)`. At least one character after the `=`, and at most 8,192 UTF-16 code units in all |
| `#` | number | A number in JSON's grammar: `-?(0\|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?` |
| `$` | text | The text itself: at least 1 and at most 32,767 UTF-16 code units |
| `?` | logical | Exactly `TRUE` or `FALSE` |

Anything else is an error. Further:
- A number is **finite** once parsed: `#1e999` is an error.
- A number that is not zero, but so small it reads as zero, is an error: `#1e-400`.
- A number that is not zero but smaller in magnitude than 2.2250738585072014e-308 (a subnormal) is
  an error, since the spreadsheet app stores it as zero.
- A number larger in magnitude than 9.99999999999999e307 is an error. That is the largest the
  spreadsheet app accepts.
- So `+1`, `.5`, `1.`, `1_000`, `inf`, `nan` and `0x10` are all errors.
- A text may begin with any character: `$=not a formula` is the text `=not a formula`.
- There are **no error constants**: `#N/A` reads as a malformed number. A cell that must hold an
  error holds a formula, such as `=NA()`.
- There are no blank cells. A blank cell is simply not listed.

### `format`

Empty, or `key=value` pairs separated by `|`.
- Each pair splits at its **first** `=`, since a number format can contain `=`, as in `[>=100]`.
- Keys are lowercase and exact. Each appears at most once on a line. An unknown key is an error.
  A value is never empty.

| Key | Value | Means |
|---|---|---|
| `numberformat` | a format code, containing no `\|` | The cell's number format. The reader does not judge the code; the xlsx writer does |
| `indent` | an integer from 0 to 250, written as `row` is (or `0`) | The indent level of the cell's contents |
| `columnwidth` | `default`, or a number from 0 to 255 in JSON's grammar | The width of the cell's **column**, in characters as the spreadsheet app's interface shows them. `default` is the app's standard width, and `0` hides the column |

**Column widths follow one rule.** For each sheet and column, the **first** line in the file that
mentions that column carries `columnwidth`, and **no later line** for that column does. A width
therefore belongs to exactly one line, and a file's widths never conflict.

### Rules across lines

- No two lines name the same cell: the same sheet, row and col.
- The column-width rule above.
- Each sheet is spelled one way throughout.

### Rules for writers

The reader accepts any formula text after the `=`. A writer must still produce formulas that the
spreadsheet app and the xlsx writer both handle:

- **A1 notation, spelled as the app stores it**: function names and cell references in upper case,
  no spaces inside a reference (`SUM(B2:F2)`, not `sum( b2 : f2 )`), and `TRUE` and `FALSE` in
  upper case. Text inside double quotes is kept exactly, with `""` for a quote.
- **Only functions stored without a name prefix**: those in ECMA-376 Part 1's predefined function
  list (clause 18.17.7). `SUM`, `MIN`, `MAX` and `NA` are all in it.
- No array formulas, and nothing that spills.
- A reference to another sheet writes the sheet name in single quotes, doubling any apostrophe in
  it: `'My Sheet'!A1`.
- **Defined before use.** Every cell a formula refers to, including every cell of a range, is on an
  earlier line. So there are no circular references, and a file computes from top to bottom.
- **A number written inside a formula may lose precision:** the spreadsheet app keeps about 15
  significant digits of it, while a number cell keeps the full double. A writer that needs an exact
  constant puts it in a cell of its own.

## The values CSV

The value computed for every cell of a `.yup` file.

- CSV with RFC 4180 quoting, UTF-8 with no byte-order mark, and LF line endings.
- The header is `sheet,row,col,type,value`.
- One line per cell of the `.yup` file, in the same order.
- `type` is the code of the spreadsheet function `TYPE`, and `value` is written accordingly:

| `type` | Means | `value` |
|---|---|---|
| `1` | number | The shortest text that reads back as the same double, as Python's `repr` writes it: `23.0`, `0.1`, `1e-300`, `-0.0`. Never `nan` or `inf` |
| `2` | text | The text itself, exactly. CSV quoting carries commas, quotes, tabs and newlines |
| `4` | logical | `TRUE` or `FALSE` |
| `16` | error | The error as the app shows it: `#N/A`, `#DIV/0!`, `#VALUE!`, `#REF!`, `#NAME?`, `#NUM!`, `#NULL!`. Any text starting with `#` is accepted, since apps have more |

There is no blank: every listed cell was written, and a formula that returns empty text has type
`2` and an empty value.

The values CSV for the example `.yup` file above:

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

A reader of a values CSV checks the header, that every line has five fields, that `row` and `col`
are written as in `.yup`, that `type` is one of the four codes, and that each `value` suits its
type: a number in JSON's grammar that is finite, `TRUE` or `FALSE`, or text starting with `#`.
