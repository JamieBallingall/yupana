"""Number formats against the app's own answers, in ``data/numfmt-canonical.csv``.

Each row is a code sent to the app, and what the app saved for it: a built-in id, a
custom code in its canonical spelling, or a rejection. The writer must write the same,
or refuse. It refuses only where the app rejects the code, or on purpose where the app's
rule is not established well enough to follow; those are listed below with the reason.
"""

import csv
import io
from pathlib import Path

import pytest
from yupana.numfmt import (
    BUILT_IN,
    FIRST_CUSTOM_ID,
    canonical,
    grouped,
    number_format_id,
)
from yupana.result import Err, Ok

DATA = Path(__file__).parent / "data" / "numfmt-canonical.csv"
ROWS = list(csv.DictReader(io.StringIO(DATA.read_bytes().decode("utf-8"), newline="")))

# Codes the app accepts but the writer refuses, since the rule behind the app's answer
# is not established: a guess that happens to fit one example is not a rule.
REFUSED_ON_PURPOSE = {
    "0.0x": "an unquoted letter, which the app escapes; the writer asks for quotes",
    "e": "a letter with a meaning in some calendars",
    "B1yyyy": "a calendar prefix",
    "[$-en-US]0.00": "a locale by name, which the app maps to an id",
    "0.0,0": "a comma among the decimals, which the app drops",
}


def written(code: str) -> tuple[int, str] | str:
    """What the writer stores for a code: (id, custom spelling or ""), or its refusal."""
    custom: dict[str, int] = {}
    match number_format_id(code, custom):
        case Err(reason):
            return reason
        case Ok(number) if number >= FIRST_CUSTOM_ID:
            return number, next(c for c, n in custom.items() if n == number)
        case Ok(number):
            return number, ""


def test_the_data_is_all_there() -> None:
    assert len(ROWS) > 200
    assert {row["outcome"] for row in ROWS} == {"builtin", "custom", "rejected"}


@pytest.mark.parametrize("row", ROWS, ids=[f"{r['group']}: {r['sent']}" for r in ROWS])
def test_the_writer_stores_what_the_app_stores(row: dict[str, str]) -> None:
    sent, stored, outcome = row["sent"], row["stored"], row["outcome"]
    result = written(sent)
    if sent in REFUSED_ON_PURPOSE:
        assert isinstance(result, str), (
            f"{sent!r} is listed as refused, but was written"
        )
        return
    match outcome:
        case "rejected":
            assert isinstance(result, str), (
                f"the app rejects {sent!r}, the writer wrote {result}"
            )
        case "builtin" if not stored:
            assert result == (int(row["numFmtId"]), "")
        case _:
            # A custom code, or one of the currency built-ins, which the app writes out in
            # full and the writer writes as a custom code of the same spelling.
            assert isinstance(result, tuple), f"{sent!r} refused: {result}"
            number, spelling = result
            assert spelling == stored
            assert number >= FIRST_CUSTOM_ID


def test_every_listed_refusal_is_in_the_data() -> None:
    assert set(REFUSED_ON_PURPOSE) <= {row["sent"] for row in ROWS}


@pytest.mark.parametrize("code", list(BUILT_IN))
def test_a_built_in_is_matched_in_any_case(code: str) -> None:
    assert number_format_id(code.upper(), {}) == Ok(BUILT_IN[code])
    assert number_format_id(code.lower(), {}) == Ok(BUILT_IN[code]) or "E+" in code


def test_equal_spellings_share_one_id_in_order_of_first_use() -> None:
    custom: dict[str, int] = {}
    ids = [
        number_format_id(code, custom).unwrap()
        for code in ["0.0%", "$0.00", '"$"0.00', "0.0%", "yyyy-mm-dd", "YYYY-MM-DD"]
    ]
    assert ids == [164, 165, 165, 164, 166, 166]
    assert custom == {"0.0%": 164, '"$"0.00': 165, "yyyy\\-mm\\-dd": 166}


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        ("", "an empty number format"),
        ("0.00e+00", "a lowercase e in an exponent"),
        ("yyyy0", "a date or time and a digit placeholder in the same section"),
        ("0 yyyy", "a date or time and a digit placeholder"),
        ("dd0", "a date or time and a digit placeholder"),
        ("hh:mm am/pm", "AM/PM or A/P in a spelling"),
        ("[$-en-US]0", "names a locale"),
        ("0.0x", "the character 'x'"),
        ("0:0", "a colon outside a time"),
        ('0"', "an unclosed double quote"),
        ("[Red0", "an unclosed ["),
        ("0\\", "at the end, with nothing after it"),
        ("0;0;0;0;0", "more than 4 sections"),
        ("[Colour10]0", "is not one the writer knows"),
        ("?,?0", "fewer than four ? placeholders"),
        ("#######,##0", "not grouped by threes"),
        ("0é", "the character"),
    ],
)
def test_a_code_the_writer_cannot_spell_is_refused_with_a_reason(
    code: str, reason: str
) -> None:
    assert reason in canonical(code).unwrap_err()


@pytest.mark.parametrize(
    ("code", "spelled"),
    [
        ("mm:ss.0;0.0", "mm:ss.0;0.0"),
        ("[s].00", "[s].00"),
        ("0.0,,", "0.0,,"),
        ('#,##0.0,"K"', '#,##0.0,"K"'),
        ("[>100]0", "[>100]0;General"),
        ("[Blue][<0]0.0;0", "[Blue][<0]0.0;0"),
        ("€ 0", "\\€\\ 0"),
    ],
)
def test_more_spellings(code: str, spelled: str) -> None:
    assert canonical(code) == Ok(spelled)


def test_an_accounting_format_is_spelled_as_the_app_spells_it() -> None:
    """A common format for financial statements, asked of the app on 2026-09-27."""
    code = "_(#,##0.0_);(#,##0.0);_(-_)"
    assert canonical(code) == Ok("_(#,##0.0_);\\(#,##0.0\\);_(\\-_)")


def test_grouping_pads_to_four_and_groups_by_threes() -> None:
    assert [grouped(p) for p in ("#", "0", "##0", "0000", "#####0", "######0")] == [
        "#,###",
        "#,##0",
        "#,##0",
        "0,000",
        "###,##0",
        "#,###,##0",
    ]


LEGALITY_DATA = Path(__file__).parent / "data" / "numfmt-legality.csv"
LEGALITY = list(
    csv.DictReader(io.StringIO(LEGALITY_DATA.read_bytes().decode("utf-8"), newline=""))
)


def test_every_file_the_writer_wrote_opened_in_the_app() -> None:
    written_rows = [row for row in LEGALITY if row["writer"] == "writes"]
    assert len(written_rows) > 100
    assert all(row["opens"] == "yes" for row in written_rows)


@pytest.mark.parametrize("row", LEGALITY, ids=[r["sent"] for r in LEGALITY])
def test_the_writer_still_writes_what_was_checked_in_the_app(
    row: dict[str, str],
) -> None:
    """If the writer changes what it writes for a code, the app must be asked again."""
    result = written(row["sent"])
    if row["writer"] == "writes":
        assert isinstance(result, tuple)
        assert result[1] == row["file_code"]
    else:
        assert isinstance(result, str)
