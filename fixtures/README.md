# Fixtures

Hand-written `.yup` files, each with `name.values.csv`: what the spreadsheet app computed for it,
as `yupana-xlsx-oracle build` wrote it. Both packages' tests use them: `yupana`'s readers must read
every one, the oracle recomputes each and must agree exactly, and the xlsx writer's output must
compute to the same values.

| Fixture | Covers |
|---|---|
| `contents` | every kind of cell content, and the extremes of numbers |
| `arithmetic` | operators, precedence, comparisons and concatenation |
| `functions` | `SUM`, `MIN`, `MAX` and `NA()`, over ranges and lists |
| `errors` | every error the app shows, and one passed along |
| `texts` | texts the app would otherwise take for something else, and XML's awkward characters |
| `formats` | all three format keys, with built-in and custom number formats |
| `sheets` | three sheets, quoted names, and references between them |
| `model` | a small model laid out with labels, numbers, formulas and formats |
