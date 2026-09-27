# yupana-xlsx-oracle

Has the real spreadsheet app compute a `.yup` or xlsx file, and compares what it computed with a
values CSV. A tool for checking; nothing depends on it at run time.

## The machine

The app's behaviour recorded in this package was established on one machine, set to:

- regional format: en-US;
- display scaling: 100%.

## What the app was found to do

Established with this package on 2026-09-27, and checked again by its tests marked `app`:

- It decodes `_x` followed by four hexadecimal digits and `_` as a character in shared strings,
  formula text, sheet names and number format codes alike, and writes the shape as `_x005F_x…`
  itself. So `yupana`'s writer escapes it in all four.
- It compares sheet names in a file by Unicode case folding, and refuses a workbook holding two
  that fold alike: `Straße` and `STRASSE`, `σ` and `ς`, `ﬁle` and `FILE`. (Renaming a sheet
  through COM is more lenient.)
- It counts sheet names and texts in UTF-16 code units, and refuses a file holding a longer text
  than 32,767 of them.
- It keeps every finite double in a file, up to 1.7976931348623157e308, and stores a subnormal
  as zero, and -0 as 0.
- It keeps only 15 significant digits of a number written inside a formula.
- A formula is at most 8,192 UTF-16 code units, counting its `=`.
- A result that nearly cancels is snapped to zero: `=0.1+0.2-0.3` is exactly 0.
- Negation binds more tightly than `^`: `=-2^2` is 4.
- Its default font is Aptos Narrow 11, and it writes `defaultRowHeight="15"` for it.
- It refuses a workbook with one cell written twice, and a number format mixing a date with
  digit placeholders in one section.
- Its error numbers are 2000 `#NULL!`, 2007 `#DIV/0!`, 2015 `#VALUE!`, 2023 `#REF!`, 2029
  `#NAME?`, 2036 `#NUM!`, 2042 `#N/A`, 2045 `#SPILL!` and 2050 `#CALC!`.
