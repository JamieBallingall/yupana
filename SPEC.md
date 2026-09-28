# The `.yup` format

Version 0.0.2. **Not yet stable:** a later version may change anything, or everything, here.

A `.yup` file lists the cells of a workbook, one per line: what is in each cell and how it is formatted. It is a [straight-line program](https://en.wikipedia.org/wiki/Straight-line_program) intended to be laid out on a grid. Other lines size its columns and rows.

In the examples below, `→` stands for a tab. Real tabs do not survive copying from rendered text.

## The `.yup` file

### Lines and fields

- UTF-8, with **no byte-order mark**.
- Lines end with LF. A CR anywhere is an error. The last line ends with LF too. There are no empty lines.
- Fields are separated by a tab. No field can contain a tab, a CR or an LF, and there is no quoting (the IANA `text/tab-separated-values` rule).
- The first line is exactly `yup 0.0.2 Yupana Straight Line Spreadsheet Format`: the **version line**. It says what the file is, and which version of this specification it follows. A reader refuses a version it does not know.
- The second line is exactly the header `sheet→row→col→type→cell→format`.
- Every later line has exactly six fields, in that order, and there is at least one such line.
- An empty field still counts: a line whose format is empty ends with a tab.
- **Lengths are counted in UTF-16 code units**: a character outside the Basic Multilingual Plane counts as two.

Here the spaces around each `→` line the columns up for reading; they are not in the file:

```text
yup 0.0.2 Yupana Straight Line Spreadsheet Format
sheet → row → col → type → cell      → format
Model →  *  →  1  →  |   →           → columnwidth=34
Model →  *  →  2  →  |   →           → columnwidth=10
Model →  1  →  1  →  $   → Year      →
Model →  1  →  2  →  #   → 2026      → numberformat=0
Model →  2  →  1  →  $   → Revenue   →
Model →  2  →  2  →  #   → 1000      → numberformat=#,##0.0;(#,##0.0)
Model →  3  →  1  →  $   → Growth    → indent=1
Model →  3  →  2  →  #   → 0.08      → numberformat=0.0%
Model →  4  →  *  →  -   →           → rowheight=6
Model →  5  →  1  →  $   → Next year →
Model →  5  →  2  →  =   → B2*(1+B3) → numberformat=#,##0.0;(#,##0.0)
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
- Sheet names are compared **case-insensitively**, by Unicode case folding: `Data` and `data` name the same sheet, so a file that spells one sheet two ways is an error. This is how the spreadsheet app compares them when it opens a file: it refuses a workbook holding both `Straße` and `STRASSE`, or both `σ` and `ς`.
- Leading and trailing spaces are part of the name.
- Sheets appear in the workbook in the order of their first appearance in the file, on a line of any type.

### `row` and `col`

1-based positions, written as plain decimal integers with no sign and no leading zeros. `row` is 1 to 1,048,576 and `col` is 1 to 16,384. Where the type allows it, `*` stands for every row or every column.

### `type`

What the line describes, in one character:

| `type` | The line is | `row` | `col` | `cell` | `format` |
|---|---|---|---|---|---|
| `=` | a formula | a row | a col | the formula, without its `=` | cell keys, or empty |
| `#` | a number | a row | a col | the number | cell keys, or empty |
| `$` | a text | a row | a col | the text | cell keys, or empty |
| `?` | a logical | a row | a col | `TRUE` or `FALSE` | cell keys, or empty |
| `.` | a blank cell, listed for its format | a row | a col | empty | cell keys, at least one |
| `\|` | a column | `*` | a col, or `*` for every column | empty | `columnwidth` |
| `-` | a row | a row, or `*` for every row | `*` | empty | `rowheight` |

Anything else is an error. Lines of the first five types are the **cells**. A blank cell with no format is not listed.

### `cell`

What is in the cell. Its type says how to read it:

- **A formula** is A1-notation formula text without its `=`, such as `B2*(1+B3)`: at least one character, and at most 8,191 UTF-16 code units, since the spreadsheet app's limit of 8,192 counts the `=`.
- **A number** is written in JSON's grammar: `-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?`.
  - It is **finite** once parsed: `1e999` is an error.
  - A number that is not zero, but so small it reads as zero, is an error: `1e-400`.
  - A number that is not zero but smaller in magnitude than 2.2250738585072014e-308 (a subnormal) is an error, since the spreadsheet app stores it as zero.
  - A number too large to be a double is an error: `1e309`. Every finite double is fine, up to 1.7976931348623157e308.
  - So `+1`, `.5`, `1.`, `1_000`, `inf`, `nan` and `0x10` are all errors.
- **A text** is the text itself: at least 1 and at most 32,767 UTF-16 code units. It may begin with any character: the text `=not a formula` is just that text.
- **A logical** is exactly `TRUE` or `FALSE`.
- There are **no error constants**. A cell that must hold an error holds a formula, such as `NA()`.
- Every other type has an empty `cell`.

### `format`

Empty, or `key=value` pairs separated by `|`.
- Each pair splits at its **first** `=`, since a number format can contain `=`, as in `[>=100]`.
- Keys are lowercase and exact. Each appears at most once on a line. An unknown key is an error, and so is a key on a type of line it is not for. A value is never empty.

On a cell:

| Key | Value | Means |
|---|---|---|
| `numberformat` | a format code, containing no `\|` | The cell's number format. The reader does not judge the code; the xlsx writer does |
| `indent` | an integer from 0 to 250, written as `row` is (or `0`) | The indent level of the cell's contents |
| `bold` | `true` or `false` | Whether the text is bold |
| `italic` | `true` or `false` | Whether the text is italic |
| `underline` | `single`, `double`, `singleaccounting` or `doubleaccounting` | How the text is underlined. The accounting underlines sit lower, and run the width of the cell for a text |
| `fontcolor` | a colour | The colour of the text |
| `fill` | a colour | The cell's background, filled solid |

A **colour** is six hexadecimal digits in upper case, `RRGGBB`: `FF0000` is red, and `DDEBF7` a pale blue. A cell without `fontcolor` has black text, and one without `fill` no fill.

On a column (`|`) or a row (`-`):

| Key | Value | Means |
|---|---|---|
| `columnwidth` | a number from 0 to 255 in JSON's grammar | The column's width, in characters as the spreadsheet app's interface shows them. `0` hides the column |
| `rowheight` | a number from 0 to 409 in JSON's grammar | The row's height, in points. `0` hides the row |

The line for every column (col `*`) sets the width of each column that has no line of its own, and the line for every row (row `*`) the height of each such row. Neither can be `0`. A column or row with neither keeps the spreadsheet app's standard size.

### Rules across lines

- No two cells name the same sheet, row and col.
- No two columns name the same sheet and col, and no two rows the same sheet and row, counting `*` as one more col or row.
- Each sheet is spelled one way throughout.

### Rules for writers

The reader accepts any formula text. A writer must still produce formulas that the spreadsheet app and the xlsx writer both handle:

- **A1 notation, spelled as the app stores it**: function names and cell references in upper case, no spaces inside a reference (`SUM(B2:F2)`, not `sum( b2 : f2 )`), and `TRUE` and `FALSE` in upper case. Text inside double quotes is kept exactly, with `""` for a quote.
- **Only functions stored without a name prefix**: those in ECMA-376 Part 1's predefined function list (clause 18.17.7). `SUM`, `MIN`, `MAX` and `NA` are all in it.
- No array formulas, and nothing that spills.
- A reference to another sheet writes the sheet name in single quotes, doubling any apostrophe in it: `'My Sheet'!A1`.
- **Defined before use.** Every cell a formula refers to, including every cell of a range, is on an earlier line, and is not a blank cell. So there are no circular references, and a file computes from top to bottom.
- **A number written inside a formula loses precision:** the spreadsheet app keeps only 15 significant digits of it (`0.3333333333333333*3` computes as `0.333333333333333*3`), while a number cell keeps the full double. A writer that needs an exact constant puts it in a cell of its own.
