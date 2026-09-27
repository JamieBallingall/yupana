"""The session: it starts, it stops quickly, and a stall never hangs its caller."""

import time
from types import SimpleNamespace

import pytest
from yupana.result import Err, Ok
from yupana_xlsx_oracle.session import Session, Stalled, Timeouts, app_processes


def test_conventions_unlike_en_us_are_each_named() -> None:
    session = Session()
    settings = [None] * 45
    settings[2], settings[3], settings[31] = ",", ".", 1.0
    session.app = SimpleNamespace(International=tuple(settings))
    assert session.unlike_en_us() == (
        "its decimal separator is ',', not '.'",
        "its thousands separator is '.', not ','",
        "its date order is 1.0, not 0",
    )
    settings[2], settings[3], settings[31] = ".", ",", 0.0
    session.app = SimpleNamespace(International=tuple(settings))
    assert session.unlike_en_us() == ()


@pytest.mark.app
def test_a_session_starts_and_stops_leaving_nothing_behind() -> None:
    before = app_processes()
    with Session() as session:
        assert session.stalled is None
        assert session.pid is not None
        assert session.pid not in before
        pid = session.pid
        assert session.app.Visible is False
        assert session.app.DisplayAlerts is False
        begun = time.perf_counter()
    assert time.perf_counter() - begun < 5
    assert pid not in app_processes()
    assert before <= app_processes()


@pytest.mark.app
def test_a_modal_dialog_is_a_stall_and_the_session_is_dead_after_it() -> None:
    with Session(Timeouts(close=5)) as session:
        pid = session.pid
        begun = time.perf_counter()
        stalled = session.guarded("asking", 3, lambda: session.app.InputBox("?"))
        assert stalled == Err(Stalled("asking"))
        assert time.perf_counter() - begun < 20
        assert session.stalled == Stalled("asking")
        assert session.guarded("anything", 3, lambda: 1) == Err(Stalled("anything"))
        assert session.recalculate() == Err(Stalled("recalculating"))
    assert pid not in app_processes()


@pytest.mark.app
def test_an_exception_that_is_not_a_stall_reaches_the_caller() -> None:
    with Session() as session:

        def fail() -> None:
            raise ValueError("the caller's own")

        with pytest.raises(ValueError, match="the caller's own"):
            session.guarded("failing", 30, fail)
        assert session.stalled is None
        assert session.guarded("adding", 30, lambda: 1 + 1) == Ok(2)
