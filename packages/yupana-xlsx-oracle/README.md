# yupana-xlsx-oracle

Has the real spreadsheet app compute a `.yup` or xlsx file, and compares what it computed with a values CSV. A tool for checking: nothing depends on it at run time.

Requires Windows and Microsoft Excel. Excel is a trademark of Microsoft Corporation; this project is not affiliated with or endorsed by Microsoft.

## Commands

```bash
yupana-xlsx-oracle build   model.yup  [--values model.csv] [--xlsx model.xlsx]
yupana-xlsx-oracle read    model.xlsx model.yup [--values model.csv]
yupana-xlsx-oracle compare model.yup  expected.csv [--xlsx model.xlsx] [--tolerance 1e-9]
```

- **`build`**: write every cell of the `.yup` file into a fresh workbook in the app, recalculate in full, and write the values CSV. With `--xlsx`, also save the app's own workbook.
- **`read`**: open an xlsx file in the app, recalculate in full, and write the values of the cells the `.yup` file lists. A file the app will not open is reported as refused.
- **`compare`**: compute as `build` does (or as `read` does, given `--xlsx`), and compare with a values CSV, cell by cell, reporting every disagreement. Exact by default: type and value must be identical. With `--tolerance t`, numbers agree within `t`, and any error agrees with any error.

Values go to `--values` or to standard output; problems go to standard error. The exit status is 0 when all is well and 1 otherwise.

The app runs as a private, hidden instance, never the one you have open, and a dialog can never hang a command: a call that does not return in time is reported as stalled.

## Regional settings

The app's behaviour recorded here was established with the app using en-US's number and date conventions: `.` for decimals, `,` for thousands, and dates month first. What the app shows for a number or a date depends on them, so the tests marked `app` check them first, and are skipped, saying why, where they differ.

## What the app was found to do

Established with this package on 2026-09-27, and checked again by its tests marked `app`:

- It decodes `_x` followed by four hexadecimal digits and `_` as a character in shared strings, formula text, sheet names and number format codes alike, and writes the shape as `_x005F_x…` itself. So `yupana`'s writer escapes it in all four.
- It compares sheet names in a file by Unicode case folding, and refuses a workbook holding two that fold alike: `Straße` and `STRASSE`, `σ` and `ς`, `ﬁle` and `FILE`. (Renaming a sheet through COM is more lenient.)
- It counts sheet names and texts in UTF-16 code units, and refuses a file holding a longer text than 32,767 of them.
- It keeps every finite double in a file, up to 1.7976931348623157e308, and stores a subnormal as zero, and -0 as 0.
- It keeps only 15 significant digits of a number written inside a formula.
- A formula is at most 8,192 UTF-16 code units, counting its `=`.
- A result that nearly cancels is snapped to zero: `=0.1+0.2-0.3` is exactly 0.
- Negation binds more tightly than `^`: `=-2^2` is 4.
- Its default font is Aptos Narrow 11, and it writes `defaultRowHeight="15"` for it.
- It refuses a workbook with one cell written twice, and a number format mixing a date with digit placeholders in one section.
- Its error numbers are 2000 `#NULL!`, 2007 `#DIV/0!`, 2015 `#VALUE!`, 2023 `#REF!`, 2029 `#NAME?`, 2036 `#NUM!`, 2042 `#N/A`, 2045 `#SPILL!` and 2050 `#CALC!`.
