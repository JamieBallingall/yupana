"""The xlsx writer: each part's XML for small inputs, compared exactly."""

import io
import re
import zipfile
from pathlib import Path
from xml.dom.minidom import parseString

import pytest
from yupana.result import Err
from yupana.xlsx import (
    MAIN,
    RELATIONSHIPS,
    WriteError,
    address,
    column_letters,
    escape_attribute,
    escape_text,
    stored_width,
    write_xlsx,
    write_xlsx_file,
)
from yupana.yup import read_yup

HEAD = "sheet\trow\tcol\tcell\tformat\n"
DECLARATION = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'


def yup(*lines: str) -> str:
    return HEAD + "".join(line + "\n" for line in lines)


def parts(text: str) -> dict[str, str]:
    data = write_xlsx(read_yup(text).unwrap()).unwrap()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {i.filename: archive.read(i).decode("utf-8") for i in archive.infolist()}


def matched(pattern: str, text: str) -> str:
    """The first group of ``pattern`` in ``text``, which must be there."""
    found = re.search(pattern, text)
    assert found is not None, f"{pattern!r} not found"
    return found.group(1)


def body(xml: str) -> str:
    assert xml.startswith(DECLARATION)
    return xml[len(DECLARATION) :]


ONE_NUMBER = yup("Model\t1\t1\t#1\tcolumnwidth=default")


# Addresses and escaping.


@pytest.mark.parametrize(
    ("col", "letters"),
    [(1, "A"), (2, "B"), (26, "Z"), (27, "AA"), (52, "AZ"), (53, "BA"), (702, "ZZ"),
     (703, "AAA"), (16384, "XFD")],
)  # fmt: skip
def test_column_letters(col: int, letters: str) -> None:
    assert column_letters(col) == letters


def test_an_address_is_letters_then_row() -> None:
    assert address(1048576, 16384) == "XFD1048576"


@pytest.mark.parametrize(
    ("text", "escaped"),
    [
        ("_x0041_", "_x005F_x0041_"),
        ("_x000D_", "_x005F_x000D_"),
        ("_x00fF_", "_x005F_x00fF_"),
        ("_x005F_", "_x005F_x005F_"),
        ("a_x0041_b_x0042_c", "a_x005F_x0041_b_x005F_x0042_c"),
        ("_X0041_", "_X0041_"),
        ("_x41_", "_x41_"),
        ("_x00411_", "_x00411_"),
        ("_x004G_", "_x004G_"),
        ("max_x", "max_x"),
        ("a_xb", "a_xb"),
        ("_x0041", "_x0041"),
        ("a & b < c > d", "a &amp; b &lt; c &gt; d"),
        ('say "hi"', 'say "hi"'),
        ("_x0026_&", "_x005F_x0026_&amp;"),
    ],
)
def test_text_escaping_applies_the_x_rule_then_xml(text: str, escaped: str) -> None:
    assert escape_text(text) == escaped


def test_attribute_escaping_also_escapes_double_quotes() -> None:
    assert escape_attribute('a"b&c<d>e') == "a&quot;b&amp;c&lt;d&gt;e"


# The container.


def test_the_parts_are_exactly_these_in_this_order() -> None:
    assert list(parts(ONE_NUMBER)) == [
        "[Content_Types].xml",
        "_rels/.rels",
        "xl/workbook.xml",
        "xl/_rels/workbook.xml.rels",
        "xl/worksheets/sheet1.xml",
        "xl/styles.xml",
    ]


def test_shared_strings_are_a_part_only_when_there_is_text() -> None:
    names = list(parts(yup("Model\t1\t1\t$a\tcolumnwidth=default")))
    assert names[-1] == "xl/sharedStrings.xml"


def test_every_part_is_well_formed_and_declared() -> None:
    for xml in parts(
        yup("S\t1\t1\t$a & <b>\tcolumnwidth=5|numberformat=0.0%")
    ).values():
        parseString(xml.encode("utf-8"))
        assert xml.startswith(DECLARATION)


def test_content_types() -> None:
    sheetml = "application/vnd.openxmlformats-officedocument.spreadsheetml"
    assert body(parts(ONE_NUMBER)["[Content_Types].xml"]) == (
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f'<Override PartName="/xl/workbook.xml" ContentType="{sheetml}.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        f'ContentType="{sheetml}.worksheet+xml"/>'
        f'<Override PartName="/xl/styles.xml" ContentType="{sheetml}.styles+xml"/>'
        "</Types>"
    )


def test_relationships() -> None:
    found = parts(
        yup("A\t1\t1\t$x\tcolumnwidth=default", "B\t1\t1\t#1\tcolumnwidth=default")
    )
    package = "http://schemas.openxmlformats.org/package/2006/relationships"
    assert body(found["_rels/.rels"]) == (
        f'<Relationships xmlns="{package}"><Relationship Id="rId1" '
        f'Type="{RELATIONSHIPS}/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    assert body(found["xl/_rels/workbook.xml.rels"]) == (
        f'<Relationships xmlns="{package}">'
        f'<Relationship Id="rId1" Type="{RELATIONSHIPS}/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        f'<Relationship Id="rId2" Type="{RELATIONSHIPS}/worksheet" '
        'Target="worksheets/sheet2.xml"/>'
        f'<Relationship Id="rId3" Type="{RELATIONSHIPS}/styles" Target="styles.xml"/>'
        f'<Relationship Id="rId4" Type="{RELATIONSHIPS}/sharedStrings" '
        'Target="sharedStrings.xml"/>'
        "</Relationships>"
    )


def test_the_workbook_lists_the_sheets_in_order_with_escaped_names() -> None:
    found = parts(
        yup(
            'B & "Q"\t1\t1\t#1\tcolumnwidth=default', "A\t1\t1\t#1\tcolumnwidth=default"
        )
    )
    assert body(found["xl/workbook.xml"]) == (
        f'<workbook xmlns="{MAIN}" xmlns:r="{RELATIONSHIPS}"><sheets>'
        '<sheet name="B &amp; &quot;Q&quot;" sheetId="1" r:id="rId1"/>'
        '<sheet name="A" sheetId="2" r:id="rId2"/></sheets></workbook>'
    )


def test_one_sheet_holding_one_number() -> None:
    assert body(parts(ONE_NUMBER)["xl/worksheets/sheet1.xml"]) == (
        f'<worksheet xmlns="{MAIN}" xmlns:r="{RELATIONSHIPS}">'
        '<dimension ref="A1"/>'
        '<sheetViews><sheetView tabSelected="1" workbookViewId="0"/></sheetViews>'
        '<sheetFormatPr defaultRowHeight="15"/>'
        '<sheetData><row r="1"><c r="A1"><v>1.0</v></c></row></sheetData>'
        '<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" '
        'header="0.3" footer="0.3"/></worksheet>'
    )


def test_output_is_deterministic_with_fixed_zip_metadata() -> None:
    text = yup("S\t1\t1\t$a\tcolumnwidth=5|numberformat=0.0%", "S\t2\t1\t=A1\t")
    data = write_xlsx(read_yup(text).unwrap()).unwrap()
    assert data == write_xlsx(read_yup(text).unwrap()).unwrap()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
            assert info.create_system == 0
            assert info.compress_type == zipfile.ZIP_DEFLATED


def test_there_is_never_a_calculation_chain() -> None:
    assert "xl/calcChain.xml" not in parts(yup("S\t1\t1\t=1+1\tcolumnwidth=default"))


# Cells.


def sheet_data(text: str, number: int = 1) -> str:
    xml = parts(text)[f"xl/worksheets/sheet{number}.xml"]
    return matched(r"<sheetData>(.*)</sheetData>", xml)


def test_each_kind_of_cell() -> None:
    text = yup(
        "S\t1\t1\t#-1.5e-7\tcolumnwidth=default",
        "S\t1\t2\t?TRUE\tcolumnwidth=default",
        "S\t1\t3\t?FALSE\tcolumnwidth=default",
        "S\t1\t4\t$text\tcolumnwidth=default",
        'S\t1\t5\t=A1&"<x>"\tcolumnwidth=default',
        "S\t1\t6\t#1e16\tcolumnwidth=default",
    )
    assert sheet_data(text) == (
        '<row r="1"><c r="A1"><v>-1.5e-07</v></c>'
        '<c r="B1" t="b"><v>1</v></c>'
        '<c r="C1" t="b"><v>0</v></c>'
        '<c r="D1" t="s"><v>0</v></c>'
        '<c r="E1"><f>A1&amp;"&lt;x&gt;"</f></c>'
        '<c r="F1"><v>1e+16</v></c></row>'
    )


def test_rows_ascend_and_cells_ascend_within_each_row_whatever_the_file_order() -> None:
    text = yup(
        "S\t3\t2\t#32\tcolumnwidth=default",
        "S\t1\t3\t#13\tcolumnwidth=default",
        "S\t1\t1\t#11\tcolumnwidth=default",
        "S\t3\t1\t#31\t",
    )
    assert sheet_data(text) == (
        '<row r="1"><c r="A1"><v>11.0</v></c><c r="C1"><v>13.0</v></c></row>'
        '<row r="3"><c r="A3"><v>31.0</v></c><c r="B3"><v>32.0</v></c></row>'
    )


def test_the_dimension_is_the_used_range() -> None:
    xml = parts(
        yup("S\t3\t2\t#1\tcolumnwidth=default", "S\t7\t28\t#1\tcolumnwidth=default")
    )
    assert '<dimension ref="B3:AB7"/>' in xml["xl/worksheets/sheet1.xml"]


def test_shared_strings_are_shared_counted_and_preserved() -> None:
    text = yup(
        "S\t1\t1\t$b\tcolumnwidth=default",
        "S\t2\t1\t$ a \t",
        "S\t3\t1\t$b\t",
        "S\t4\t1\t$_x0041_ & <\t",
    )
    assert sheet_data(text).count('t="s"><v>0</v>') == 2
    assert body(parts(text)["xl/sharedStrings.xml"]) == (
        f'<sst xmlns="{MAIN}" count="4" uniqueCount="3">'
        "<si><t>b</t></si>"
        '<si><t xml:space="preserve"> a </t></si>'
        "<si><t>_x005F_x0041_ &amp; &lt;</t></si></sst>"
    )


# Styles.


def styles(text: str) -> str:
    return body(parts(text)["xl/styles.xml"])


def test_the_styles_part_with_no_formatting() -> None:
    assert styles(ONE_NUMBER) == (
        f'<styleSheet xmlns="{MAIN}">'
        '<fonts count="1"><font><sz val="11"/><color rgb="FF000000"/>'
        '<name val="Aptos Narrow"/><family val="2"/></font></fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/>'
        "</border></borders>"
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>'
        "</cellStyleXfs>"
        '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" '
        'xfId="0"/></cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/>'
        "</cellStyles></styleSheet>"
    )


def test_a_style_is_a_number_format_and_indent_allocated_in_order_of_first_use() -> (
    None
):
    text = yup(
        "S\t1\t1\t#1\tcolumnwidth=default|numberformat=#,##0.0;(#,##0.0)",
        "S\t2\t1\t$a\tindent=1",
        "S\t3\t1\t#1\tnumberformat=0.00",
        "S\t4\t1\t#1\tnumberformat=#,##0.0;(#,##0.0)",
        "S\t5\t1\t#1\tnumberformat=0.00|indent=1",
        "S\t6\t1\t#1\tindent=0|numberformat=General",
    )
    found = styles(text)
    assert (
        '<numFmts count="1"><numFmt numFmtId="164" '
        'formatCode="#,##0.0;\\(#,##0.0\\)"/></numFmts>' in found
    )
    xfs = matched(r"<cellXfs[^>]*>(.*)</cellXfs>", found)
    assert xfs == (
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" '
        'applyNumberFormat="1"/>'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" '
        'applyAlignment="1"><alignment horizontal="left" indent="1"/></xf>'
        '<xf numFmtId="2" fontId="0" fillId="0" borderId="0" xfId="0" '
        'applyNumberFormat="1"/>'
        '<xf numFmtId="2" fontId="0" fillId="0" borderId="0" xfId="0" '
        'applyNumberFormat="1" applyAlignment="1">'
        '<alignment horizontal="left" indent="1"/></xf>'
    )
    assert '<cellXfs count="5">' in found
    assert sheet_data(text) == (
        '<row r="1"><c r="A1" s="1"><v>1.0</v></c></row>'
        '<row r="2"><c r="A2" s="2" t="s"><v>0</v></c></row>'
        '<row r="3"><c r="A3" s="3"><v>1.0</v></c></row>'
        '<row r="4"><c r="A4" s="1"><v>1.0</v></c></row>'
        '<row r="5"><c r="A5" s="4"><v>1.0</v></c></row>'
        '<row r="6"><c r="A6"><v>1.0</v></c></row>'
    )


def test_a_format_code_is_escaped_as_an_attribute() -> None:
    found = styles(yup('S\t1\t1\t#1\tcolumnwidth=default|numberformat=0.0" <kg>"'))
    assert 'formatCode="0.0&quot; &lt;kg&gt;&quot;"' in found


def test_a_number_format_that_cannot_be_written_is_refused_naming_each_cell() -> None:
    text = yup(
        "S\t1\t1\t#1\tcolumnwidth=default|numberformat=0.0x",
        "S\t2\t1\t#1\tnumberformat=0.0",
        "S\t3\t1\t#1\tnumberformat=yyyy0",
    )
    match write_xlsx(read_yup(text).unwrap()):
        case Err((first, second)):
            assert first.line == 2
            assert str(first).startswith('line 2: sheet "S", row 1, col 1: ')
            assert "the number format '0.0x' cannot be written" in str(first)
            assert second.line == 4
        case other:
            raise AssertionError(f"expected two refusals, not {other}")


# Column widths.


def cols(text: str) -> str:
    xml = parts(text)["xl/worksheets/sheet1.xml"]
    found = re.search(r"<cols>.*</cols>", xml)
    return found.group() if found else ""


def test_widths_in_characters_are_stored_with_padding() -> None:
    assert [stored_width(w) for w in (0.5, 1, 1.5, 34)] == [
        0.85546875,
        1.7109375,
        2.28515625,
        34.7109375,
    ]


def test_cols_merge_adjacent_equal_widths_hide_zero_and_skip_default() -> None:
    text = yup(
        "S\t1\t1\t#1\tcolumnwidth=10",
        "S\t1\t2\t#1\tcolumnwidth=10",
        "S\t1\t3\t#1\tcolumnwidth=12",
        "S\t1\t4\t#1\tcolumnwidth=default",
        "S\t1\t5\t#1\tcolumnwidth=12",
        "S\t1\t6\t#1\tcolumnwidth=0",
        "S\t1\t7\t#1\tcolumnwidth=0",
    )
    assert cols(text) == (
        '<cols><col min="1" max="2" width="10.7109375" customWidth="1"/>'
        '<col min="3" max="3" width="12.7109375" customWidth="1"/>'
        '<col min="5" max="5" width="12.7109375" customWidth="1"/>'
        '<col min="6" max="7" width="0" hidden="1" customWidth="1"/></cols>'
    )


def test_no_cols_without_widths() -> None:
    assert cols(ONE_NUMBER) == ""


# Several sheets.


def test_only_the_first_sheet_is_selected_and_each_has_its_own_cells() -> None:
    text = yup(
        "One\t1\t1\t#1\tcolumnwidth=5",
        "Two\t1\t1\t#2\tcolumnwidth=default",
        "Three\t2\t2\t=One!A1+Two!A1\tcolumnwidth=default",
    )
    found = parts(text)
    assert 'tabSelected="1"' in found["xl/worksheets/sheet1.xml"]
    assert "tabSelected" not in found["xl/worksheets/sheet2.xml"]
    assert "tabSelected" not in found["xl/worksheets/sheet3.xml"]
    assert "<cols>" not in found["xl/worksheets/sheet2.xml"]
    assert sheet_data(text, 3) == '<row r="2"><c r="B2"><f>One!A1+Two!A1</f></c></row>'


def test_a_file_that_cannot_be_written_is_reported(tmp_path: Path) -> None:
    missing = tmp_path / "no" / "such" / "folder" / "x.xlsx"
    match write_xlsx_file(read_yup(ONE_NUMBER).unwrap(), missing):
        case Err((WriteError(line=None, message=message),)):
            assert "could not write" in message
        case other:
            raise AssertionError(f"expected an unwritable file, not {other}")
