# Test data

`numfmt-canonical.csv` holds the spreadsheet app's own answers about number formats: each code
sent to it through COM (`sent`), and what it saved for that code in an xlsx file (`stored`, empty
for a built-in, and `numFmtId`), or `rejected` if it refused the code outright. Custom ids start
again at 164 in each of the two rounds the data was gathered in, so only built-in ids mean
anything across rows. `test_numfmt.py` checks the writer against every row.

Gathered on 2026-09-27 with `yupana-xlsx-oracle`'s session, on a machine set to regional format
en-US and display scaling 100%.
