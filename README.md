# yupana

**A human- and LLM-friendly authoring surface for spreadsheets.**

You write a workbook as a short text file in `.yup`, and yupana turns it, with any CSV files it
reads, into an xlsx workbook: live formulas, styles and all. You say what each range is and
where it goes beside the others; yupana works out every size and every cell.

```text
// Each order's revenue, beside the orders read from orders.csv.
revenue : Formula style money revenue_header down
revenue = by_row (\order -> order.units * order.unit_price) orders

// Each region once, and the revenue of each.
regions_n : Size Maximum orders_n
regions : Formula size [regions_n, 1] summary_region_header down
regions = unique orders.region

region_revenue : Formula style money summary_revenue_header down
region_revenue = map (\key -> sum_if (orders.region == key) revenue) regions
```

That is part of [examples/orders.yup](examples/orders.yup). In the workbook, `revenue` is one
BYROW formula that spills down beside the table, and `region_revenue` one MAP over the regions.

## Why

- **The whole workbook fits in one read.** An LLM, or a person, sees all of a model's logic at
  once, instead of a few cells at a time. Bulk numbers stay in CSV files beside it.
- **Mistakes are caught where they are written.** Ranges that do not line up, text where a
  number goes, two ranges in the same cells, a formula that would show `#CALC!`: each is an error
  with its line and column, before any workbook exists.
- **Files open cleanly.** Every workbook yupana writes opens in the spreadsheet app without an
  offer to repair it. Anything it cannot write validly, it refuses.

It is not a converter from xlsx: a `.yup` is written from scratch, by an LLM or by hand.

## Try it

**In the browser,** with nothing to install: open
[jamieballingall.github.io/yupana](https://jamieballingall.github.io/yupana/), paste or open a
`.yup` file, add the CSV files it names, and download the workbook. The page works offline once
saved.

**As one JavaScript file,** for Node 18 or later, Deno, Bun or a browser: download `yup.mjs` from
the [latest release](https://github.com/JamieBallingall/yupana/releases/latest).

```bash
node yup.mjs build orders.yup -o orders.xlsx
```

Imported, it is a library, typed by `yup.d.ts` from the same release:

```js
import { build } from "./yup.mjs";

const { xlsx, warnings } = await build(source, { "orders.csv": orders });
```

**From the source,** with Rust installed:

```bash
cargo run -p yup-cli -- build examples/orders.yup -o target/orders.xlsx
```

`yup check` reports problems without writing anything, and `yup show <step>` prints what any
step makes of a file, such as `yup show ground`, which writes every size and cell into the
`.yup`.

## Learn more

- [docs/language.md](docs/language.md): the language, as the code implements it.
- [examples](examples): eight files to start from, each built by the tests.
- [docs/design.md](docs/design.md): why it is the way it is, what was decided, and what is open.
- [AGENTS.md](AGENTS.md): how the code is written and checked, for people and coding agents.

The current version is 0.3. The syntax is still provisional, and may change between versions.

## License

[Apache-2.0](LICENSE). yupana is written clean-room from the published specifications of the xlsx
format, and is not affiliated with or endorsed by the maker of any spreadsheet app.
