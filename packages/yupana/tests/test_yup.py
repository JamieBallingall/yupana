"""The ``.yup`` reader: every rule in ``SPEC.md`` has a failing example."""

import pytest
from yupana.result import Err, Ok
from yupana.yup import (
    Cell,
    Default,
    Format,
    Formula,
    Logical,
    Number,
    Text,
    Yup,
    read_yup,
)

HEAD = "sheet\trow\tcol\tcell\tformat\n"


def yup(*lines: str) -> str:
    return HEAD + "".join(line + "\n" for line in lines)


def line(
    cell: str = "#1",
    fmt: str = "columnwidth=5",
    sheet: str = "Model",
    row: str = "1",
    col: str = "1",
) -> str:
    return f"{sheet}\t{row}\t{col}\t{cell}\t{fmt}"


def errors(text: str) -> list[str]:
    match read_yup(text):
        case Err(found):
            return [str(error) for error in found]
        case Ok():
            return []


def test_a_file_with_several_problems_reports_them_all() -> None:
    text = yup(
        line("$Revenue", "columnwidth=20"),
        line("#01", "", col="2"),
        line("#5", "indent=300", row="0", col="2"),
        line("?maybe", "", row="2"),
        line("$ok", "", sheet="Bad:Name", row="3"),
        line("#1", "", sheet="model", row="4"),
    )
    assert errors(text) == [
        'line 3: a number is written in JSON\'s grammar, not "01"',
        'line 3: the first line for sheet "Model", col 2, must carry columnwidth',
        'line 4: row must be a whole number from 1 to 1048576, not "0"',
        'line 4: indent must be a whole number from 0 to 250, not "300"',
        'line 5: a logical is TRUE or FALSE, not "maybe"',
        'line 6: sheet name "Bad:Name" contains ":"',
        'line 6: the first line for sheet "Bad:Name", col 1, must carry columnwidth',
        'line 7: sheet "model" is spelled "Model" earlier',
    ]


def test_a_realistic_file_reads_to_exactly_its_cells() -> None:
    text = yup(
        "Model\t1\t1\t$Year\tcolumnwidth=34",
        "Model\t1\t2\t#2026\tcolumnwidth=10|numberformat=0",
        "Model\t1\t3\t=B1+1\tcolumnwidth=default|numberformat=0",
        "Model\t2\t1\t$Revenue\t",
        "Model\t2\t2\t#1000\tnumberformat=#,##0.0;(#,##0.0)",
        "Inputs\t1\t1\t$Growth\tcolumnwidth=12.5|indent=1",
        "Inputs\t1\t2\t#0.08\tcolumnwidth=default|numberformat=0.0%",
        "Model\t2\t3\t=B2*(1+Inputs!B1)\tnumberformat=#,##0.0;(#,##0.0)",
        "Model\t3\t1\t$Positive\tindent=2",
        "Model\t3\t2\t?TRUE\t",
        "Model\t3\t3\t=NA()\t",
    )
    money = "#,##0.0;(#,##0.0)"
    assert read_yup(text) == Ok(
        Yup(
            ("Model", "Inputs"),
            (
                Cell(2, "Model", 1, 1, Text("Year"), Format(column_width=34.0)),
                Cell(
                    3,
                    "Model",
                    1,
                    2,
                    Number(2026.0),
                    Format(number_format="0", column_width=10.0),
                ),
                Cell(
                    4,
                    "Model",
                    1,
                    3,
                    Formula("=B1+1"),
                    Format(number_format="0", column_width=Default()),
                ),
                Cell(5, "Model", 2, 1, Text("Revenue"), Format()),
                Cell(6, "Model", 2, 2, Number(1000.0), Format(number_format=money)),
                Cell(
                    7,
                    "Inputs",
                    1,
                    1,
                    Text("Growth"),
                    Format(indent=1, column_width=12.5),
                ),
                Cell(
                    8,
                    "Inputs",
                    1,
                    2,
                    Number(0.08),
                    Format(number_format="0.0%", column_width=Default()),
                ),
                Cell(
                    9,
                    "Model",
                    2,
                    3,
                    Formula("=B2*(1+Inputs!B1)"),
                    Format(number_format=money),
                ),
                Cell(10, "Model", 3, 1, Text("Positive"), Format(indent=2)),
                Cell(11, "Model", 3, 2, Logical(True), Format()),
                Cell(12, "Model", 3, 3, Formula("=NA()"), Format()),
            ),
        )
    )


# The file and its lines.


def test_a_byte_order_mark_is_refused() -> None:
    assert errors("﻿" + yup(line())) == [
        "line 1: the file starts with a byte-order mark"
    ]


def test_a_cr_is_refused_once_and_the_rest_is_still_checked() -> None:
    text = yup(line(), line("#x", "", row="2")).replace("\n", "\r\n")
    assert errors(text) == [
        "line 1: a CR, where lines end with LF alone",
        'line 3: a number is written in JSON\'s grammar, not "x"',
    ]


def test_a_cr_inside_a_line_is_refused_on_that_line() -> None:
    assert errors(yup(line(), line("$a\rb", "", row="2"))) == [
        "line 3: a CR, where lines end with LF alone",
        "line 3: a text cannot contain the character U+000D",
    ]


def test_the_last_line_ends_with_lf() -> None:
    assert errors(yup(line())[:-1]) == ["line 2: the last line does not end with LF"]


def test_an_empty_file_is_refused() -> None:
    assert errors("") == ["line 1: the file is empty"]


def test_the_header_is_exact() -> None:
    assert errors(yup(line()).replace("format", "Format", 1)) == [
        'line 1: the first line must be the header "sheet\\trow\\tcol\\tcell\\tformat"'
    ]


def test_a_file_needs_a_cell() -> None:
    assert errors(HEAD) == ["line 1: there are no cells"]


def test_an_empty_line_is_refused() -> None:
    assert errors(yup(line(), "", line(row="2", fmt=""))) == ["line 3: an empty line"]


@pytest.mark.parametrize(
    "text", ["Model\t1\t1\t#1", "Model\t1\t1\t#1\tcolumnwidth=5\t", "Model 1 1 #1"]
)
def test_a_line_has_five_fields(text: str) -> None:
    [error] = errors(yup(text))
    assert error.startswith("line 2: a line has 5 tab-separated fields, not ")


# Sheet names.


@pytest.mark.parametrize(
    ("sheet", "message"),
    [
        ("", 'a sheet name is 1 to 31 characters long, and "" is 0'),
        ("a" * 32, "is 32"),
        ("\N{GRINNING FACE}" * 16, "is 32"),
        ("a:b", 'contains ":"'),
        ("a\\b", 'contains "\\\\"'),
        ("a/b", 'contains "/"'),
        ("a?b", 'contains "?"'),
        ("a*b", 'contains "*"'),
        ("a[b]", 'contains "[" "]"'),
        ("'a", "starts or ends with an apostrophe"),
        ("a'", "starts or ends with an apostrophe"),
        ("History", "is reserved by the spreadsheet app"),
        ("HISTORY", "is reserved by the spreadsheet app"),
        ("a\x01", "contains the character U+0001"),
        ("a\x7f", "contains the character U+007F"),
        ("a￿", "contains the character U+FFFF"),
    ],
)
def test_a_sheet_name_is_refused(sheet: str, message: str) -> None:
    [error] = [e for e in errors(yup(line(sheet=sheet))) if "sheet name" in e]
    assert error.startswith("line 2: ")
    assert message in error


@pytest.mark.parametrize(
    "sheet",
    [
        "a'b",
        "a" * 31,
        "\N{GRINNING FACE}" * 15,
        " padded ",
        "Histories",
        "max_x",
        "_x41_",
        "a_x0041_b",
        "_x00fF_",
    ],
)
def test_a_sheet_name_is_accepted(sheet: str) -> None:
    assert read_yup(yup(line(sheet=sheet))).unwrap().sheets == (sheet,)


# Rows and columns.


@pytest.mark.parametrize(
    "row", ["0", "01", "+1", "-1", "1.0", "", " 1", "1048577", "9" * 5000]
)
def test_a_row_is_refused(row: str) -> None:
    [error] = errors(yup(line(row=row)))
    assert error.startswith(
        "line 2: row must be a whole number from 1 to 1048576, not "
    )


@pytest.mark.parametrize("col", ["0", "16385", "A"])
def test_a_col_is_refused(col: str) -> None:
    [error] = errors(yup(line(col=col)))
    assert error.startswith("line 2: col must be a whole number from 1 to 16384, not ")


def test_the_largest_row_and_col_are_accepted() -> None:
    cell = read_yup(yup(line(row="1048576", col="16384"))).unwrap().cells[0]
    assert (cell.row, cell.col) == (1_048_576, 16_384)


# Cell contents.


@pytest.mark.parametrize(
    ("cell", "message"),
    [
        ("", "the cell is empty: a blank cell is not listed"),
        ("%x", 'a cell starts with =, #, $ or ?, not "%"'),
        ("=", "a formula needs something after the ="),
        ("=A1&\x01", "a formula cannot contain the character U+0001"),
        ("=" + "1" * 8192, "a formula is at most 8192 characters long, not 8193"),
        ("#01", "JSON's grammar"),
        ("#+1", "JSON's grammar"),
        ("#.5", "JSON's grammar"),
        ("#1.", "JSON's grammar"),
        ("#1_000", "JSON's grammar"),
        ("#inf", "JSON's grammar"),
        ("#nan", "JSON's grammar"),
        ("#0x10", "JSON's grammar"),
        ("#1e", "JSON's grammar"),
        ("# 1", "JSON's grammar"),
        ("#", "JSON's grammar"),
        ("#1e999", "the number 1e999 is too large to be a double"),
        ("#-1e309", "too large to be a double"),
        ("#1e-400", "the number 1e-400 is too small, and would be stored as zero"),
        ("#1e-310", "the number 1e-310 is subnormal, and would be stored as zero"),
        ("$", "a text cannot be empty: a blank cell is not listed"),
        ("$" + "a" * 32768, "a text is at most 32767 characters long, not 32768"),
        ("$" + "\N{GRINNING FACE}" * 16384, "not 32768"),
        ("$a\x00", "a text cannot contain the character U+0000"),
        ("$a￾", "a text cannot contain the character U+FFFE"),
        ("$a\ud800", "a text cannot contain the character U+D800"),
        ("?true", 'a logical is TRUE or FALSE, not "true"'),
        ("?1", 'a logical is TRUE or FALSE, not "1"'),
        ("?", 'a logical is TRUE or FALSE, not ""'),
    ],
)
def test_a_cell_is_refused(cell: str, message: str) -> None:
    [error] = errors(yup(line(cell)))
    assert error.startswith("line 2: ")
    assert message in error


@pytest.mark.parametrize(
    ("cell", "content"),
    [
        ("#0", Number(0.0)),
        ("#-0", Number(-0.0)),
        ("#-1.5E+3", Number(-1500.0)),
        ("#9.99999999999999e307", Number(9.99999999999999e307)),
        ("#-1e308", Number(-1e308)),
        ("#1.7976931348623157e308", Number(1.7976931348623157e308)),
        ("#2.2250738585072014e-308", Number(2.2250738585072014e-308)),
        ("#0.000", Number(0.0)),
        ("#0e-999", Number(0.0)),
        ("$=not a formula", Text("=not a formula")),
        ("$#N/A", Text("#N/A")),
        ("$ padded ", Text(" padded ")),
        ("$" + "a" * 32767, Text("a" * 32767)),
        ("?TRUE", Logical(True)),
        ("?FALSE", Logical(False)),
        ("=NA()", Formula("=NA()")),
        ("=" + "1" * 8191, Formula("=" + "1" * 8191)),
    ],
)
def test_a_cell_is_accepted(cell: str, content: object) -> None:
    [read] = read_yup(yup(line(cell))).unwrap().cells
    assert read.content == content
    if isinstance(content, Number):
        assert repr(read.content) == repr(content)


# Formats.


@pytest.mark.parametrize(
    ("fmt", "message"),
    [
        ("columnwidth=5|indent=1|indent=2", "the format key indent appears twice"),
        ("columnwidth=5|Indent=1", 'unknown format key "Indent"'),
        ("columnwidth=5|width=3", 'unknown format key "width"'),
        ("columnwidth=5|indent", 'a format pair is key=value, not "indent"'),
        ("columnwidth=5|indent=", 'a format pair is key=value, not "indent="'),
        ("columnwidth=5|", 'a format pair is key=value, not ""'),
        ("columnwidth=5|numberformat=", 'not "numberformat="'),
        ("columnwidth=5|indent=251", 'from 0 to 250, not "251"'),
        ("columnwidth=5|indent=01", 'not "01"'),
        ("columnwidth=5|indent=-1", 'not "-1"'),
        ("columnwidth=5|indent=1.0", 'not "1.0"'),
        (
            "columnwidth=256",
            'columnwidth must be default or a number from 0 to 255, not "256"',
        ),
        ("columnwidth=-1", 'not "-1"'),
        ("columnwidth=Default", 'not "Default"'),
        ("columnwidth=5.", 'not "5."'),
        ("columnwidth=5|numberformat=0\x01", "cannot contain the character U+0001"),
    ],
)
def test_a_format_is_refused(fmt: str, message: str) -> None:
    [error] = errors(yup(line(fmt=fmt)))
    assert error.startswith("line 2: ")
    assert message in error


@pytest.mark.parametrize(
    ("fmt", "expected"),
    [
        ("columnwidth=default", Format(column_width=Default())),
        ("columnwidth=0", Format(column_width=0.0)),
        ("columnwidth=-0", Format(column_width=0.0)),
        ("columnwidth=255", Format(column_width=255.0)),
        ("columnwidth=1e1", Format(column_width=10.0)),
        (
            "indent=0|columnwidth=12.5|numberformat=[>=100]0;0",
            Format(number_format="[>=100]0;0", indent=0, column_width=12.5),
        ),
        ("columnwidth=5|indent=250", Format(indent=250, column_width=5.0)),
        (
            "columnwidth=5|numberformat=a=b",
            Format(number_format="a=b", column_width=5.0),
        ),
    ],
)
def test_a_format_is_accepted(fmt: str, expected: Format) -> None:
    [cell] = read_yup(yup(line(fmt=fmt))).unwrap().cells
    assert cell.format == expected


# Rules across lines.


def test_no_two_lines_name_the_same_cell() -> None:
    assert errors(yup(line(), line("#2", ""))) == [
        'line 3: sheet "Model", row 1, col 1 is already on line 2'
    ]


def test_a_cell_is_the_same_whatever_the_spelling_of_its_sheet() -> None:
    assert errors(yup(line(), line("#2", "", sheet="MODEL"))) == [
        'line 3: sheet "MODEL" is spelled "Model" earlier',
        'line 3: sheet "Model", row 1, col 1 is already on line 2',
    ]


def test_the_first_line_for_a_column_carries_its_width() -> None:
    assert errors(yup(line(fmt=""))) == [
        'line 2: the first line for sheet "Model", col 1, must carry columnwidth'
    ]


def test_no_later_line_for_a_column_carries_a_width() -> None:
    text = yup(line(), line(row="2", fmt="columnwidth=default"))
    assert errors(text) == [
        'line 3: only the first line for sheet "Model", col 1, may carry columnwidth'
    ]


def test_widths_belong_to_each_sheet_and_column() -> None:
    text = yup(
        line(),
        line(col="2", fmt="columnwidth=7"),
        line(sheet="Other"),
        line(sheet="Other", row="2", fmt=""),
    )
    assert read_yup(text).unwrap().sheets == ("Model", "Other")


def test_a_line_with_a_bad_cell_still_counts_for_the_rules_across_lines() -> None:
    assert errors(yup(line("#x"), line(fmt="columnwidth=3"))) == [
        'line 2: a number is written in JSON\'s grammar, not "x"',
        'line 3: sheet "Model", row 1, col 1 is already on line 2',
        'line 3: only the first line for sheet "Model", col 1, may carry columnwidth',
    ]


def test_sheets_are_in_order_of_first_appearance() -> None:
    text = yup(line(sheet="B"), line(sheet="A"), line(sheet="B", row="2", fmt=""))
    assert read_yup(text).unwrap().sheets == ("B", "A")
