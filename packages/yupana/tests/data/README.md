# Test data

`numfmt-canonical.csv` holds the spreadsheet app's own answers about number formats: each code sent to it through COM (`sent`), and what it saved for that code in an xlsx file (`stored`, empty for a built-in, and `numFmtId`), or `rejected` if it refused the code outright. Custom ids start again at 164 in each of the two rounds the data was gathered in, so only built-in ids mean anything across rows. `test_numfmt.py` checks the writer against every row.

`numfmt-legality.csv` records, for each distinct custom code in the first file, whether a workbook holding it opens in the app: the writer's own spelling for a code it writes, or the raw code edited into an otherwise good workbook for a code it refuses. Every workbook the writer wrote opened. Most refused codes make the app refuse the whole file.

Gathered on 2026-09-27 with `yupana-xlsx-oracle`'s session, with the app using en-US's number and date conventions, as that package's README describes.
