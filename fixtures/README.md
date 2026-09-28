# Fixtures

Hand-written `.yup` files, each with `name.values.csv`: what the spreadsheet app computed for it, as `yupana-xlsx-oracle build` wrote it. Both packages' tests use them: `yupana`'s readers must read every one, the oracle recomputes each and must agree exactly, and the xlsx writer's output must compute to the same values.

| Fixture | Covers |
|---|---|
| `contents` | every kind of cell content, and the extremes of numbers |
| `arithmetic` | operators, precedence, comparisons and concatenation |
| `functions` | `SUM`, `MIN`, `MAX` and `NA()`, over ranges and lists |
| `errors` | every error the app shows, and one passed along |
| `texts` | texts the app would otherwise take for something else, and XML's awkward characters |
| `formats` | number formats, built in and custom, indents and column widths |
| `styles` | bold, italic, every underline, font colours, fills, every kind of line along an edge, and every alignment, on text, numbers, formulas and blank cells; and a sheet with every view key |
| `sizes` | the width of every column and the height of every row, single widths and heights, hidden columns and rows, a blank cell with a format, a sheet with no cells, and frozen rows or columns alone |
| `sheets` | three sheets, quoted names, and references between them |
| `model` | a small model laid out with labels, numbers, formulas and formats |
