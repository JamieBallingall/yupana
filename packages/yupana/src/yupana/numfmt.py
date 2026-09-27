"""Number formats: a format code as the id xlsx stores for it.

A code is either one of the app's **built-in** formats, matched case-insensitively and
written as its id alone, or a **custom** format, spelled out in the workbook with an id
from 164 upward. The app stores a custom code in a canonical spelling of its own, and a
file must use that spelling, so the writer produces it. Anything the writer cannot spell
with confidence is refused, with a reason, rather than guessed.

The rules below were established by sending codes to the app and reading what it saved;
``tests/data/numfmt-canonical.csv`` holds its answers, and the tests reproduce every one.

>>> custom: dict[str, int] = {}
>>> number_format_id("#,##0.00", custom), number_format_id("yyyy-mm-dd", custom)
(Ok(value=4), Ok(value=164))
>>> custom
{'yyyy\\\\-mm\\\\-dd': 164}
>>> number_format_id("0.0 kg", custom).is_err()
True
"""

import re
import unicodedata
from dataclasses import dataclass

from yupana.result import Err, Ok, Result

# The app's en-US table, as it reports it. The currency and accounting formats (5 to 8,
# 41 to 44) are left out on purpose: they render with the reader's currency symbol, so a
# code like them is written out in full instead.
BUILT_IN = {
    "General": 0,
    "0": 1,
    "0.00": 2,
    "#,##0": 3,
    "#,##0.00": 4,
    "0%": 9,
    "0.00%": 10,
    "0.00E+00": 11,
    "# ?/?": 12,
    "# ??/??": 13,
    "m/d/yyyy": 14,
    "d-mmm-yy": 15,
    "d-mmm": 16,
    "mmm-yy": 17,
    "h:mm AM/PM": 18,
    "h:mm:ss AM/PM": 19,
    "h:mm": 20,
    "h:mm:ss": 21,
    "m/d/yyyy h:mm": 22,
    "#,##0_);(#,##0)": 37,
    "#,##0_);[Red](#,##0)": 38,
    "#,##0.00_);(#,##0.00)": 39,
    "#,##0.00_);[Red](#,##0.00)": 40,
    "mm:ss": 45,
    "[h]:mm:ss": 46,
    "mm:ss.0": 47,
    "##0.0E+0": 48,
    "@": 49,
}
_BY_FOLDED = {code.casefold(): number for code, number in BUILT_IN.items()}
FIRST_CUSTOM_ID = 164
MAX_SECTIONS = 4

_COLOURS: dict[str, str] = {
    name.casefold(): name
    for name in ("Black", "Blue", "Cyan", "Green", "Magenta", "Red", "White", "Yellow")
}
_COLOUR_NUMBER = re.compile(r"(?i)color *([1-9]|[1-4][0-9]|5[0-6])")
_CONDITION = re.compile(r"(<=|>=|<>|<|>|=)-?[0-9]+(\.[0-9]+)?")
_ELAPSED = re.compile(r"(?i)h+|m+|s+")
_LOCALE = re.compile(r"\$([^-\]]*)(-[0-9A-Fa-f]{1,8})?")
_LOWERCASE_EXPONENT = re.compile(r"[0#?]e[+-]")
_DATE_LETTERS = "ymdhs"
_AM_PM = ("AM/PM", "A/P", "a/p")
_PLACEHOLDERS = "0#?"
# Printed literally, so the app writes each with a backslash before it.
_ESCAPED = "-() +^'{}<>=~&!|`"


@dataclass(frozen=True, slots=True)
class _Token:
    """One piece of a code: its kind, and its canonical spelling."""

    kind: str
    text: str


def _bracket(inner: str) -> Result[_Token, str]:
    folded = inner.casefold()
    if folded in _COLOURS:
        return Ok(_Token("colour", f"[{_COLOURS[folded]}]"))
    if match := _COLOUR_NUMBER.fullmatch(inner):
        return Ok(_Token("colour", f"[Color{match.group(1)}]"))
    if _CONDITION.fullmatch(inner):
        return Ok(_Token("condition", f"[{inner}]"))
    if _ELAPSED.fullmatch(inner):
        return Ok(_Token("elapsed", f"[{folded}]"))
    if (match := _LOCALE.fullmatch(inner)) and (match.group(1) or match.group(2)):
        return Ok(_Token("locale", f"[{inner}]"))
    if inner.startswith("$"):
        return Err(
            f"the locale tag [{inner}] names a locale, which the app rewrites; "
            "use its hexadecimal id, as in [$-409]"
        )
    return Err(f"the bracketed part [{inner}] is not one the writer knows")


def _tokens(code: str) -> Result[list[_Token], str]:
    """A code as tokens, each spelled as the app spells it."""
    tokens: list[_Token] = []
    i = 0
    while i < len(code):
        c = code[i]
        rest = code[i:]
        if c == '"':
            end = code.find('"', i + 1)
            if end < 0:
                return Err("an unclosed double quote")
            tokens.append(_Token("quoted", code[i : end + 1]))
            i = end + 1
        elif c in "\\_*":
            if i + 1 >= len(code):
                return Err(f"{c!r} at the end, with nothing after it")
            tokens.append(_Token("kept", code[i : i + 2]))
            i += 2
        elif c == "[":
            end = code.find("]", i + 1)
            if end < 0:
                return Err("an unclosed [")
            match _bracket(code[i + 1 : end]):
                case Err(reason):
                    return Err(reason)
                case Ok(token):
                    tokens.append(token)
            i = end + 1
        elif rest[:7].casefold() == "general":
            tokens.append(_Token("general", "General"))
            i += 7
        elif any(rest.startswith(spelled) for spelled in _AM_PM):
            spelled = next(s for s in _AM_PM if rest.startswith(s))
            tokens.append(_Token("date", spelled))
            i += len(spelled)
        elif rest[:5].casefold() == "am/pm" or rest[:3].casefold() == "a/p":
            return Err("AM/PM or A/P in a spelling whose canonical form is not known")
        elif c.casefold() in _DATE_LETTERS and c.isascii():
            run = len(rest) - len(rest.lstrip(c))
            tokens.append(_Token("date", c.casefold() * run))
            i += run
        elif c == "E" and code[i + 1 : i + 2] in ("+", "-"):
            tokens.append(_Token("exponent", code[i : i + 2]))
            i += 2
        elif c == "/":
            run = len(rest[1:]) - len(rest[1:].lstrip("0123456789"))
            tokens.append(_Token("slash", code[i : i + 1 + run]))
            i += 1 + run
        else:
            match c:
                case "0" | "#" | "?":
                    tokens.append(_Token("placeholder", c))
                case ",":
                    tokens.append(_Token("comma", c))
                case ".":
                    tokens.append(_Token("point", c))
                case ";":
                    tokens.append(_Token("section", c))
                case ":":
                    tokens.append(_Token("colon", c))
                case "%" | "@":
                    tokens.append(_Token("kept", c))
                case "$":
                    tokens.append(_Token("kept", '"$"'))
                case _ if c in _ESCAPED or c in "123456789":
                    tokens.append(_Token("kept", "\\" + c))
                case _ if not c.isascii() and not unicodedata.category(c).startswith(
                    "L"
                ):
                    tokens.append(_Token("kept", "\\" + c))
                case _:
                    return Err(
                        f"the character {c!r}, which the app rejects or rewrites in ways "
                        "not established; put it in double quotes"
                    )
            i += 1
    return Ok(tokens)


def grouped(placeholders: str) -> str:
    """An integer part with thousands separators, as the app spells it: at least four
    placeholders, padded with ``#`` on the left, and a comma every three from the right.

    >>> grouped("0"), grouped("00"), grouped("###0"), grouped("####0"), grouped("#####0")
    ('#,##0', '#,#00', '#,##0', '##,##0', '###,##0')
    """
    padded = "#" * (4 - len(placeholders)) + placeholders
    groups = []
    while padded:
        groups.insert(0, padded[-3:])
        padded = padded[:-3]
    return ",".join(groups)


def _is_seconds(token: _Token) -> bool:
    return token.kind in ("date", "elapsed") and token.text.rstrip("]").endswith("s")


def _section(tokens: list[_Token]) -> Result[str, str]:
    """One section of a code, in the app's spelling, or why it is refused."""
    out: list[str] = []
    has_date = any(t.kind in ("date", "elapsed") for t in tokens)
    digit = False
    i = 0
    point_seen = False
    while i < len(tokens):
        token = tokens[i]
        if token.kind == "placeholder":
            j = i
            while j < len(tokens) and tokens[j].kind in ("placeholder", "comma"):
                j += 1
            run = tokens[i:j]
            last = max(k for k, t in enumerate(run) if t.kind == "placeholder")
            body, scaling = run[: last + 1], run[last + 1 :]
            spelled = "".join(t.text for t in body)
            if any(t.kind == "comma" for t in body):
                if point_seen:
                    return Err("a comma between placeholders after the decimal point")
                placeholders = "".join(t.text for t in body if t.kind == "placeholder")
                canonical = grouped(placeholders)
                if len(placeholders) < 4 and "?" in placeholders:
                    return Err(
                        "a thousands separator with fewer than four ? placeholders"
                    )
                if len(placeholders) > 6 and canonical != spelled:
                    return Err(
                        "a thousands separator over seven or more placeholders, not "
                        "grouped by threes"
                    )
                spelled = canonical
            out.append(spelled + "," * len(scaling))
            digit = True
            i = j
            continue
        match token.kind:
            case "comma":
                out.append("\\,")
            case "point" if i and _is_seconds(tokens[i - 1]):
                # Fractions of a second: the zeros after the point are not digits.
                j = i + 1
                while j < len(tokens) and tokens[j].text == "0":
                    j += 1
                out.append("." + "0" * (j - i - 1))
                i = j
                continue
            case "point":
                point_seen = True
                out.append(".")
            case "exponent":
                digit = True
                out.append(token.text)
            case "colon":
                if not has_date:
                    return Err("a colon outside a time, which the app rejects")
                out.append(":")
            case "colour" if out and tokens[i - 1].kind == "condition":
                out.insert(len(out) - 1, token.text)
            case _:
                out.append(token.text)
        i += 1
    if has_date and digit:
        return Err("a date or time and a digit placeholder in the same section")
    return Ok("".join(out))


def canonical(code: str) -> Result[str, str]:
    """A custom format code in the app's canonical spelling, or why it is refused.

    A character printed literally is backslash-escaped; one the format grammar consumes
    is not.

    >>> canonical("#,##0.0;(#,##0.0)").unwrap()
    '#,##0.0;\\\\(#,##0.0\\\\)'
    >>> canonical("mmm d, yyyy").unwrap(), canonical("$#,##0.00").unwrap()
    ('mmm\\\\ d\\\\,\\\\ yyyy', '"$"#,##0.00')
    >>> canonical("yyyy0").unwrap_err()
    'a date or time and a digit placeholder in the same section'
    """
    if not code:
        return Err("an empty number format")
    if _LOWERCASE_EXPONENT.search(code):
        return Err("a lowercase e in an exponent, which the app rejects")
    match _tokens(code):
        case Err(reason):
            return Err(reason)
        case Ok(tokens):
            pass
    sections: list[list[_Token]] = [[]]
    for token in tokens:
        if token.kind == "section":
            sections.append([])
        else:
            sections[-1].append(token)
    if len(sections) > MAX_SECTIONS:
        return Err(f"more than {MAX_SECTIONS} sections")
    spelled: list[str] = []
    for section in sections:
        match _section(section):
            case Err(reason):
                return Err(reason)
            case Ok(text):
                spelled.append(text)
    if len(sections) == 1 and any(t.kind == "condition" for t in sections[0]):
        spelled.append("General")
    return Ok(";".join(spelled))


def number_format_id(code: str, custom: dict[str, int]) -> Result[int, str]:
    """The id to store for a format code: a built-in id, or a custom id allocated in
    ``custom`` (canonical code to id) in order of first use, shared by equal spellings."""
    if code.casefold() in _BY_FOLDED and not _LOWERCASE_EXPONENT.search(code):
        return Ok(_BY_FOLDED[code.casefold()])
    match canonical(code):
        case Err(reason):
            return Err(f"the number format {code!r} cannot be written: {reason}")
        case Ok(spelled):
            if spelled.casefold() in _BY_FOLDED:
                return Ok(_BY_FOLDED[spelled.casefold()])
            return Ok(custom.setdefault(spelled, FIRST_CUSTOM_ID + len(custom)))
