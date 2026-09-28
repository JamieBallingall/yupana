"""The ``.yup`` reader: every rule in ``SPEC.md`` has a failing example."""

import pytest
from yupana.result import Err, Ok
from yupana.yup import (
    PREAMBLE,
    Blank,
    Border,
    Cell,
    Column,
    Format,
    Formula,
    LineStyle,
    Logical,
    Number,
    Row,
    Text,
    Underline,
    Yup,
    read_yup,
)

HEAD = PREAMBLE


def yup(*lines: str) -> str:
    return HEAD + "".join(line + "\n" for line in lines)


def line(
    kind: str = "#",
    cell: str = "1",
    fmt: str = "",
    sheet: str = "Model",
    row: str = "1",
    col: str = "1",
) -> str:
    return f"{sheet}\t{row}\t{col}\t{kind}\t{cell}\t{fmt}"


def column(col: str = "1", fmt: str = "columnwidth=5", sheet: str = "Model") -> str:
    return line("|", "", fmt, sheet, "*", col)


def row_line(row: str = "1", fmt: str = "rowheight=20", sheet: str = "Model") -> str:
    return line("-", "", fmt, sheet, row, "*")


def errors(text: str) -> list[str]:
    match read_yup(text):
        case Err(found):
            return [str(error) for error in found]
        case Ok():
            return []


def test_a_file_with_several_problems_reports_them_all() -> None:
    text = yup(
        column(fmt="columnwidth=20"),
        line("#", "01", col="2"),
        line("#", "5", "indent=300", row="0", col="2"),
        line("?", "maybe", row="2"),
        line("$", "ok", sheet="Bad:Name", row="3"),
        line("#", "1", sheet="model", row="4"),
        line("%", "x", row="5"),
    )
    assert errors(text) == [
        'line 4: a number is written in JSON\'s grammar, not "01"',
        'line 5: row must be a whole number from 1 to 1048576, not "0"',
        'line 5: indent must be a whole number from 0 to 250, not "300"',
        'line 6: a logical is TRUE or FALSE, not "maybe"',
        'line 7: sheet name "Bad:Name" contains ":"',
        'line 8: sheet "model" is spelled "Model" earlier',
        'line 9: type is one of = # $ ? . | -, not "%"',
    ]


def test_a_realistic_file_reads_to_exactly_its_lines() -> None:
    text = yup(
        "Model\t*\t1\t|\t\tcolumnwidth=34",
        "Model\t*\t*\t|\t\tcolumnwidth=10",
        "Model\t1\t1\t$\tYear\t",
        "Model\t1\t2\t#\t2026\tnumberformat=0",
        "Model\t1\t3\t=\tB1+1\tnumberformat=0",
        "Model\t2\t1\t$\tRevenue\t",
        "Model\t2\t2\t#\t1000\tnumberformat=#,##0.0;(#,##0.0)",
        "Inputs\t1\t1\t$\tGrowth\tindent=1",
        "Inputs\t1\t2\t#\t0.08\tnumberformat=0.0%",
        "Model\t2\t3\t=\tB2*(1+Inputs!B1)\tnumberformat=#,##0.0;(#,##0.0)",
        "Model\t3\t*\t-\t\trowheight=6",
        "Model\t4\t1\t$\tPositive\tindent=2",
        "Model\t4\t2\t?\tTRUE\t",
        "Model\t4\t3\t=\tNA()\t",
        "Model\t5\t2\t.\t\tnumberformat=0.0",
        "Inputs\t*\t*\t-\t\trowheight=18.75",
    )
    money = "#,##0.0;(#,##0.0)"
    assert read_yup(text) == Ok(
        Yup(
            sheets=("Model", "Inputs"),
            cells=(
                Cell(5, "Model", 1, 1, Text("Year"), Format()),
                Cell(6, "Model", 1, 2, Number(2026.0), Format(number_format="0")),
                Cell(7, "Model", 1, 3, Formula("=B1+1"), Format(number_format="0")),
                Cell(8, "Model", 2, 1, Text("Revenue"), Format()),
                Cell(9, "Model", 2, 2, Number(1000.0), Format(number_format=money)),
                Cell(10, "Inputs", 1, 1, Text("Growth"), Format(indent=1)),
                Cell(11, "Inputs", 1, 2, Number(0.08), Format(number_format="0.0%")),
                Cell(
                    12,
                    "Model",
                    2,
                    3,
                    Formula("=B2*(1+Inputs!B1)"),
                    Format(number_format=money),
                ),
                Cell(14, "Model", 4, 1, Text("Positive"), Format(indent=2)),
                Cell(15, "Model", 4, 2, Logical(True), Format()),
                Cell(16, "Model", 4, 3, Formula("=NA()"), Format()),
            ),
            blanks=(Blank(17, "Model", 5, 2, Format(number_format="0.0")),),
            columns=(Column(3, "Model", 1, 34.0), Column(4, "Model", None, 10.0)),
            rows=(Row(13, "Model", 3, 6.0), Row(18, "Inputs", None, 18.75)),
        )
    )


def test_the_lines_come_back_in_file_order() -> None:
    read = read_yup(yup(column(), line("$", "a"), row_line("2"))).unwrap()
    assert [type(x).__name__ for x in read.lines()] == ["Column", "Cell", "Row"]


# The file and its lines.


def test_a_byte_order_mark_is_refused() -> None:
    assert errors("﻿" + yup(line())) == [
        "line 1: the file starts with a byte-order mark"
    ]


def test_a_cr_is_refused_once_and_the_rest_is_still_checked() -> None:
    text = yup(line(), line("#", "x", row="2")).replace("\n", "\r\n")
    assert errors(text) == [
        "line 1: a CR, where lines end with LF alone",
        'line 4: a number is written in JSON\'s grammar, not "x"',
    ]


def test_a_cr_inside_a_line_is_refused_on_that_line() -> None:
    assert errors(yup(line(), line("$", "a\rb", row="2"))) == [
        "line 4: a CR, where lines end with LF alone",
        "line 4: a text cannot contain the character U+000D",
    ]


def test_the_last_line_ends_with_lf() -> None:
    assert errors(yup(line())[:-1]) == ["line 3: the last line does not end with LF"]


def test_an_empty_file_is_refused() -> None:
    assert errors("") == ["line 1: the file is empty"]


def test_the_version_line_is_exact() -> None:
    assert errors(yup(line()).replace("Format", "format", 1)) == [
        'line 1: the first line must be "yup 0.0.2 Yupana Straight Line Spreadsheet Format"'
    ]


def test_a_file_of_another_version_is_named_as_such() -> None:
    assert errors(yup(line()).replace("0.0.2", "0.0.1", 1)) == [
        'line 1: this reader reads version 0.0.2, not "0.0.1"'
    ]


NOT_THE_HEADER = 'line 2: the second line must be the header "sheet\\trow\\tcol\\ttype\\tcell\\tformat"'


def test_a_file_without_a_version_line_is_refused() -> None:
    assert errors(yup(line()).split("\n", 1)[1]) == [
        'line 1: the first line must be "yup 0.0.2 Yupana Straight Line Spreadsheet Format"',
        NOT_THE_HEADER,
        "line 2: there is no line after the header",
    ]


def test_the_header_is_exact() -> None:
    assert errors(yup(line()).replace("\tformat", "\tFormat", 1)) == [NOT_THE_HEADER]


def test_a_file_needs_a_line_after_the_header() -> None:
    assert errors(HEAD) == ["line 2: there is no line after the header"]


def test_an_empty_line_is_refused() -> None:
    assert errors(yup(line(), "", line(row="2"))) == ["line 4: an empty line"]


@pytest.mark.parametrize(
    "text",
    [
        "Model\t1\t1\t#\t1",
        "Model\t1\t1\t#\t1\t\t",
        "Model\t1\t1\t#1\t",
        "Model 1 1 # 1",
    ],
)
def test_a_line_has_six_fields(text: str) -> None:
    [error] = errors(yup(text))
    assert error.startswith("line 3: a line has 6 tab-separated fields, not ")


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
    assert error.startswith("line 3: ")
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


def test_a_bad_sheet_name_is_refused_on_every_type_of_line() -> None:
    text = yup(column(sheet="a:b"), row_line(sheet="a:b"), line(sheet="a:b"))
    assert errors(text) == [
        f'line {n}: sheet name "a:b" contains ":"' for n in (3, 4, 5)
    ]


# Rows and columns.


@pytest.mark.parametrize(
    "row", ["0", "01", "+1", "-1", "1.0", "", " 1", "*", "1048577", "9" * 5000]
)
def test_a_row_is_refused(row: str) -> None:
    [error] = errors(yup(line(row=row)))
    assert error.startswith(
        "line 3: row must be a whole number from 1 to 1048576, not "
    )


@pytest.mark.parametrize("col", ["0", "16385", "A", "*"])
def test_a_col_is_refused(col: str) -> None:
    [error] = errors(yup(line(col=col)))
    assert error.startswith("line 3: col must be a whole number from 1 to 16384, not ")


def test_the_largest_row_and_col_are_accepted() -> None:
    cell = read_yup(yup(line(row="1048576", col="16384"))).unwrap().cells[0]
    assert (cell.row, cell.col) == (1_048_576, 16_384)


# Types and cell contents.


@pytest.mark.parametrize("kind", ["", "%", "==", "#$", "*", "!", "a", " #"])
def test_a_type_is_refused(kind: str) -> None:
    [error] = errors(yup(line(kind)))
    assert error.startswith("line 3: type is one of = # $ ? . | -, not ")


@pytest.mark.parametrize(
    ("kind", "cell", "message"),
    [
        ("=", "", "a formula cannot be empty"),
        ("=", "A1&\x01", "a formula cannot contain the character U+0001"),
        ("=", "1" * 8192, "a formula is at most 8191 characters long, not 8192"),
        ("#", "01", "JSON's grammar"),
        ("#", "+1", "JSON's grammar"),
        ("#", ".5", "JSON's grammar"),
        ("#", "1.", "JSON's grammar"),
        ("#", "1_000", "JSON's grammar"),
        ("#", "inf", "JSON's grammar"),
        ("#", "nan", "JSON's grammar"),
        ("#", "0x10", "JSON's grammar"),
        ("#", "1e", "JSON's grammar"),
        ("#", " 1", "JSON's grammar"),
        ("#", "", "JSON's grammar"),
        ("#", "#1", "JSON's grammar"),
        ("#", "1e999", "the number 1e999 is too large to be a double"),
        ("#", "-1e309", "too large to be a double"),
        ("#", "1e-400", "the number 1e-400 is too small, and would be stored as zero"),
        ("#", "1e-310", "the number 1e-310 is subnormal, and would be stored as zero"),
        ("$", "", "a text cannot be empty"),
        ("$", "a" * 32768, "a text is at most 32767 characters long, not 32768"),
        ("$", "\N{GRINNING FACE}" * 16384, "not 32768"),
        ("$", "a\x00", "a text cannot contain the character U+0000"),
        ("$", "a￾", "a text cannot contain the character U+FFFE"),
        ("$", "a\ud800", "a text cannot contain the character U+D800"),
        ("?", "true", 'a logical is TRUE or FALSE, not "true"'),
        ("?", "1", 'a logical is TRUE or FALSE, not "1"'),
        ("?", "", 'a logical is TRUE or FALSE, not ""'),
        ("?", "?TRUE", 'a logical is TRUE or FALSE, not "?TRUE"'),
    ],
)
def test_a_cell_is_refused(kind: str, cell: str, message: str) -> None:
    [error] = errors(yup(line(kind, cell)))
    assert error.startswith("line 3: ")
    assert message in error


@pytest.mark.parametrize(
    ("kind", "cell", "content"),
    [
        ("#", "0", Number(0.0)),
        ("#", "-0", Number(-0.0)),
        ("#", "-1.5E+3", Number(-1500.0)),
        ("#", "9.99999999999999e307", Number(9.99999999999999e307)),
        ("#", "-1e308", Number(-1e308)),
        ("#", "1.7976931348623157e308", Number(1.7976931348623157e308)),
        ("#", "2.2250738585072014e-308", Number(2.2250738585072014e-308)),
        ("#", "0.000", Number(0.0)),
        ("#", "0e-999", Number(0.0)),
        ("$", "=not a formula", Text("=not a formula")),
        ("$", "#N/A", Text("#N/A")),
        ("$", "$5", Text("$5")),
        ("$", " padded ", Text(" padded ")),
        ("$", "a" * 32767, Text("a" * 32767)),
        ("?", "TRUE", Logical(True)),
        ("?", "FALSE", Logical(False)),
        ("=", "NA()", Formula("=NA()")),
        ("=", "=1", Formula("==1")),
        ("=", "1" * 8191, Formula("=" + "1" * 8191)),
    ],
)
def test_a_cell_is_accepted(kind: str, cell: str, content: object) -> None:
    [read] = read_yup(yup(line(kind, cell))).unwrap().cells
    assert read.content == content
    if isinstance(content, Number):
        assert repr(read.content) == repr(content)


def test_a_blank_cell_is_listed_for_its_format() -> None:
    read = read_yup(yup(line(".", "", "indent=1", row="2", col="3"))).unwrap()
    assert read.cells == ()
    assert read.blanks == (Blank(3, "Model", 2, 3, Format(indent=1)),)
    assert read.sheets == ("Model",)


def test_a_blank_cell_needs_a_format_and_nothing_in_it() -> None:
    assert errors(yup(line(".", ""), line(".", "x", "indent=1", row="2"))) == [
        "line 3: a . line needs a format: a blank cell without one is not listed",
        'line 4: the cell field of a . line is empty, not "x"',
    ]


# Formats on a cell.

A_COLOUR = "is a colour: six hexadecimal digits in upper case, such as FF0000"
UNDERLINES = "single, double, singleaccounting or doubleaccounting"
LINES = "thin, medium, thick, double, dotted or dashed"


@pytest.mark.parametrize(
    ("fmt", "message"),
    [
        ("indent=1|indent=2", "the format key indent appears twice"),
        ("Indent=1", 'unknown format key "Indent"'),
        ("width=3", 'unknown format key "width"'),
        ("indent", 'a format pair is key=value, not "indent"'),
        ("indent=", 'a format pair is key=value, not "indent="'),
        ("indent=1|", 'a format pair is key=value, not ""'),
        ("numberformat=", 'not "numberformat="'),
        ("indent=251", 'from 0 to 250, not "251"'),
        ("indent=01", 'not "01"'),
        ("indent=-1", 'not "-1"'),
        ("indent=1.0", 'not "1.0"'),
        ("numberformat=0\x01", "cannot contain the character U+0001"),
        ("columnwidth=5", "the format key columnwidth is for a | line, not a # line"),
        ("rowheight=5", "the format key rowheight is for a - line, not a # line"),
        ("bold=True", 'bold is true or false, not "True"'),
        ("italic=1", 'italic is true or false, not "1"'),
        ("underline=Double", f'underline is {UNDERLINES}, not "Double"'),
        ("underline=none", 'not "none"'),
        ("fontcolor=ff0000", f'fontcolor {A_COLOUR}, not "ff0000"'),
        ("fontcolor=#FF0000", 'not "#FF0000"'),
        ("fill=FF000", f'fill {A_COLOUR}, not "FF000"'),
        ("fill=FF00000", 'not "FF00000"'),
        ("bordertop=hair", f'bordertop is {LINES}, not "hair"'),
        ("borderleft=Thin", f'borderleft is {LINES}, not "Thin"'),
        ("bordertopcolor=FF0000", "bordertopcolor needs bordertop, the line it colours"),
        ("borderright=thin|borderrightcolor=red", f'borderrightcolor {A_COLOUR}, not "red"'),
        ("borderbottom=thin|borderbottom=thick", "the format key borderbottom appears twice"),
        ("bordercolor=FF0000", 'unknown format key "bordercolor"'),
        ("fill=GG0000", 'not "GG0000"'),
    ],
)  # fmt: skip
def test_a_format_is_refused(fmt: str, message: str) -> None:
    [error] = errors(yup(line(fmt=fmt)))
    assert error.startswith("line 3: ")
    assert message in error


def test_every_bad_value_on_a_line_is_reported() -> None:
    assert errors(yup(line(fmt="bold=yes|fill=red|indent=-1"))) == [
        'line 3: indent must be a whole number from 0 to 250, not "-1"',
        'line 3: bold is true or false, not "yes"',
        f'line 3: fill {A_COLOUR}, not "red"',
    ]


@pytest.mark.parametrize(
    ("fmt", "expected"),
    [
        ("", Format()),
        ("indent=0", Format(indent=0)),
        ("indent=250", Format(indent=250)),
        (
            "indent=0|numberformat=[>=100]0;0",
            Format(number_format="[>=100]0;0", indent=0),
        ),
        ("numberformat=a=b", Format(number_format="a=b")),
        ("bold=true", Format(bold=True)),
        ("bold=false|italic=true", Format(bold=False, italic=True)),
        ("underline=single", Format(underline=Underline.SINGLE)),
        ("underline=double", Format(underline=Underline.DOUBLE)),
        ("underline=singleaccounting", Format(underline=Underline.SINGLE_ACCOUNTING)),
        ("underline=doubleaccounting", Format(underline=Underline.DOUBLE_ACCOUNTING)),
        ("fontcolor=0070C0|fill=DDEBF7", Format(font_color="0070C0", fill="DDEBF7")),
        ("fill=000000", Format(fill="000000")),
        ("bordertop=thin", Format(border_top=Border(LineStyle.THIN))),
        (
            "borderbottom=double|borderbottomcolor=0070C0",
            Format(border_bottom=Border(LineStyle.DOUBLE, "0070C0")),
        ),
        (
            "borderleft=dotted|borderright=dashed|bordertop=medium|borderbottom=thick",
            Format(
                border_top=Border(LineStyle.MEDIUM),
                border_bottom=Border(LineStyle.THICK),
                border_left=Border(LineStyle.DOTTED),
                border_right=Border(LineStyle.DASHED),
            ),
        ),
    ],
)
def test_a_format_is_accepted(fmt: str, expected: Format) -> None:
    [cell] = read_yup(yup(line(fmt=fmt))).unwrap().cells
    assert cell.format == expected


# Columns and rows.


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (column("3", "columnwidth=0"), Column(3, "Model", 3, 0.0)),
        (column("3", "columnwidth=-0"), Column(3, "Model", 3, 0.0)),
        (column("3", "columnwidth=255"), Column(3, "Model", 3, 255.0)),
        (column("3", "columnwidth=1e1"), Column(3, "Model", 3, 10.0)),
        (column("16384", "columnwidth=12.5"), Column(3, "Model", 16384, 12.5)),
        (column("*", "columnwidth=0.5"), Column(3, "Model", None, 0.5)),
    ],
)
def test_a_column_is_accepted(text: str, expected: Column) -> None:
    assert read_yup(yup(text)).unwrap().columns == (expected,)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (row_line("3", "rowheight=0"), Row(3, "Model", 3, 0.0)),
        (row_line("3", "rowheight=409"), Row(3, "Model", 3, 409.0)),
        (row_line("1048576", "rowheight=6"), Row(3, "Model", 1_048_576, 6.0)),
        (row_line("*", "rowheight=18.75"), Row(3, "Model", None, 18.75)),
    ],
)
def test_a_row_line_is_accepted(text: str, expected: Row) -> None:
    assert read_yup(yup(text)).unwrap().rows == (expected,)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (column(fmt="columnwidth=256"), 'columnwidth must be a number from 0 to 255, not "256"'),
        (column(fmt="columnwidth=-1"), 'columnwidth must be a number from 0 to 255, not "-1"'),
        (column(fmt="columnwidth=default"), 'columnwidth must be a number from 0 to 255, not "default"'),
        (column(fmt="columnwidth=5."), 'columnwidth must be a number from 0 to 255, not "5."'),
        (column("*", "columnwidth=0"), "columnwidth cannot be 0 for every col"),
        (column(fmt=""), "a | line needs columnwidth"),
        (column(fmt="indent=1"), "the format key indent is for a cell, not a | line"),
        (column(fmt="rowheight=5"), "the format key rowheight is for a - line, not a | line"),
        (column("0"), 'col must be * or a whole number from 1 to 16384, not "0"'),
        (line("|", "", "columnwidth=5", row="2"), 'row is * on a | line, not "2"'),
        (line("|", "x", "columnwidth=5", row="*"), 'the cell field of a | line is empty, not "x"'),
        (row_line(fmt="rowheight=410"), 'rowheight must be a number from 0 to 409, not "410"'),
        (row_line("*", "rowheight=0"), "rowheight cannot be 0 for every row"),
        (row_line(fmt=""), "a - line needs rowheight"),
        (row_line(fmt="columnwidth=5"), "the format key columnwidth is for a | line, not a - line"),
        (row_line("x"), 'row must be * or a whole number from 1 to 1048576, not "x"'),
        (line("-", "", "rowheight=5", col="2"), 'col is * on a - line, not "2"'),
    ],
)  # fmt: skip
def test_a_column_or_row_line_is_refused(text: str, message: str) -> None:
    [error] = errors(yup(text))
    assert error == f"line 3: {message}"


def test_a_sheet_can_hold_only_sizes() -> None:
    read = read_yup(yup(column(), row_line(sheet="Other"))).unwrap()
    assert read.sheets == ("Model", "Other")
    assert read.cells == ()


# Rules across lines.


def test_no_two_lines_name_the_same_cell() -> None:
    assert errors(yup(line(), line("#", "2"))) == [
        'line 4: sheet "Model", row 1, col 1 is already on line 3'
    ]


def test_a_blank_cell_and_a_cell_with_contents_cannot_share_a_place() -> None:
    assert errors(yup(line(), line(".", "", "indent=1"))) == [
        'line 4: sheet "Model", row 1, col 1 is already on line 3'
    ]


def test_a_cell_is_the_same_whatever_the_spelling_of_its_sheet() -> None:
    assert errors(yup(line(), line("#", "2", sheet="MODEL"))) == [
        'line 4: sheet "MODEL" is spelled "Model" earlier',
        'line 4: sheet "Model", row 1, col 1 is already on line 3',
    ]


def test_no_two_lines_size_the_same_column_or_row() -> None:
    text = yup(
        column("2"),
        column("2", "columnwidth=7"),
        column("*"),
        column("*", "columnwidth=7"),
        row_line("2"),
        row_line("2"),
        row_line("*"),
        row_line("*"),
    )
    assert errors(text) == [
        'line 4: the width of sheet "Model", col 2, is already on line 3',
        'line 6: the width of sheet "Model", every col, is already on line 5',
        'line 8: the height of sheet "Model", row 2, is already on line 7',
        'line 10: the height of sheet "Model", every row, is already on line 9',
    ]


def test_sizes_belong_to_each_sheet_and_do_not_clash_with_cells() -> None:
    text = yup(
        column("1"),
        column("1", sheet="Other"),
        row_line("1"),
        line(),
        column("*"),
        row_line("*"),
    )
    read = read_yup(text).unwrap()
    assert [c.sheet for c in read.columns] == ["Model", "Other", "Model"]
    assert len(read.rows) == 2


def test_a_line_with_a_bad_cell_still_counts_for_the_rules_across_lines() -> None:
    assert errors(yup(line("#", "x"), line())) == [
        'line 3: a number is written in JSON\'s grammar, not "x"',
        'line 4: sheet "Model", row 1, col 1 is already on line 3',
    ]


def test_sheets_are_in_order_of_first_appearance_on_any_line() -> None:
    text = yup(
        line(sheet="B"), column(sheet="C"), line(sheet="A"), line(sheet="B", row="2")
    )
    assert read_yup(text).unwrap().sheets == ("B", "C", "A")
