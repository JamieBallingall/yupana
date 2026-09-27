"""The values CSV: exact round trips, and a failing example for each rule."""

import pytest
from yupana.result import Err, Ok
from yupana.values import Type, Value, number_text, read_values, write_values

HEAD = "sheet,row,col,type,value\n"


def errors(text: str) -> list[str]:
    match read_values(text):
        case Err(found):
            return [str(error) for error in found]
        case Ok():
            return []


@pytest.mark.parametrize(
    "number",
    [
        0.0,
        -0.0,
        23.0,
        0.1,
        0.1 + 0.2,
        1e-300,
        5e-324,
        1.7976931348623157e308,
        -1e16,
        123456789.123456789,
    ],
)
def test_numbers_round_trip_exactly(number: float) -> None:
    value = Value("S", 1, 1, Type.NUMBER, number_text(number))
    [back] = read_values(write_values([value])).unwrap()
    assert back == value
    assert repr(float(back.value)) == repr(number)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "plain",
        "a,b",
        'say "hi"',
        "tab\there",
        "two\nlines",
        "a\rb",
        "a\r\nb",
        "  padded  ",
        "=1+1",
        "#N/A",
        "\N{GRINNING FACE}",
    ],
)
def test_texts_round_trip_exactly(text: str) -> None:
    value = Value("S", 1, 1, Type.TEXT, text)
    assert read_values(write_values([value])) == Ok((value,))


def test_every_type_round_trips_in_order() -> None:
    values = (
        Value("Model", 1, 1, Type.TEXT, "Year"),
        Value("Model", 1, 2, Type.NUMBER, "2026.0"),
        Value("Model", 2, 1, Type.LOGICAL, "TRUE"),
        Value("Other sheet", 2, 2, Type.ERROR, "#DIV/0!"),
        Value("Model", 1048576, 16384, Type.LOGICAL, "FALSE"),
    )
    assert read_values(write_values(values)) == Ok(values)


def test_the_writer_writes_lf_and_the_header() -> None:
    text = write_values([Value("S", 1, 1, Type.TEXT, "a\nb")])
    assert text == 'sheet,row,col,type,value\nS,1,1,2,"a\nb"\n'


@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")])
def test_a_number_that_is_not_finite_is_never_written(number: float) -> None:
    with pytest.raises(AssertionError):
        number_text(number)


def test_the_writer_refuses_a_value_that_does_not_suit_its_type() -> None:
    with pytest.raises(AssertionError):
        write_values([Value("S", 1, 1, Type.LOGICAL, "yes")])


def test_a_record_spanning_lines_is_reported_where_it_starts() -> None:
    text = HEAD + 'S,1,1,2,"one\ntwo"\nS,2,1,4,maybe\n'
    assert errors(text) == ['line 4: a logical is TRUE or FALSE, not "maybe"']


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("﻿" + HEAD + "S,1,1,1,1.0\n", "line 1: the file starts with a byte-order mark"),
        (HEAD.replace("\n", "\r\n") + "S,1,1,1,1.0\r\n", "line 1: a CR"),
        (HEAD + "S,1,1,2,a\rb\n", "line 2: a CR"),
        ("", "line 1: the file is empty"),
        (HEAD + "S,1,1,1,1.0", "line 2: the last line does not end with LF"),
        ("sheet,row,col,kind,value\nS,1,1,1,1.0\n", "line 1: the first line must be"),
        (HEAD, "line 1: there are no records"),
        (HEAD + "S,1,1,1,1.0\n\nS,2,1,1,1.0\n", "line 3: an empty line"),
        (HEAD + "S,1,1,1\n", "line 2: a record has 5 fields, not 4"),
        (HEAD + "S,1,1,1,1.0,x\n", "line 2: a record has 5 fields, not 6"),
        (
            HEAD + "S,0,1,1,1.0\n",
            'line 2: row must be a whole number from 1 to 1048576, not "0"',
        ),
        (
            HEAD + "S,1,A,1,1.0\n",
            'line 2: col must be a whole number from 1 to 16384, not "A"',
        ),
        (HEAD + ",1,1,1,1.0\n", "line 2: a sheet name is 1 to 31 characters long"),
        (HEAD + "S,1,1,3,1.0\n", 'line 2: type is 1, 2, 4 or 16, not "3"'),
        (HEAD + "S,1,1,01,1.0\n", 'line 2: type is 1, 2, 4 or 16, not "01"'),
        (
            HEAD + "S,1,1,1,nan\n",
            'line 2: a number is finite and in JSON\'s grammar, not "nan"',
        ),
        (HEAD + "S,1,1,1,inf\n", 'not "inf"'),
        (HEAD + "S,1,1,1,1e999\n", 'not "1e999"'),
        (HEAD + "S,1,1,1,1.\n", 'not "1."'),
        (HEAD + "S,1,1,1,\n", 'not ""'),
        (HEAD + "S,1,1,4,true\n", 'line 2: a logical is TRUE or FALSE, not "true"'),
        (HEAD + "S,1,1,16,N/A\n", 'line 2: an error starts with #, not "N/A"'),
        (HEAD + 'S,1,1,2,"a"b\n', "line 2: malformed CSV"),
    ],
)
def test_a_values_csv_is_refused(text: str, expected: str) -> None:
    assert any(
        error.startswith(expected) or expected in error for error in errors(text)
    )
