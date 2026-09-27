"""A private instance of the spreadsheet app, driven over COM, that never hangs its caller.

A modal dialog blocks a COM call forever, so every risky call runs under a watchdog: a
timer that kills this session's own process if the call has not returned in time. The
kill makes the blocked call raise, and the call is reported as stalled, which is never
confused with the app refusing a file. After a stall the session is dead, and it reports
every later request as stalled without trying it.

Only processes this session started are ever killed, never another copy of the app.
"""

import csv
import subprocess
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self

from yupana.result import Err, Ok, Result

PROG_ID = "Excel.Application"
IMAGE = "EXCEL.EXE"
_NO_WINDOW = 0x08000000
_CALCULATION_AUTOMATIC = -4105
_MACROS_NEVER_RUN = 3


@dataclass(frozen=True, slots=True)
class Timeouts:
    """Seconds each kind of call may take before the watchdog kills the app."""

    start: float = 60
    open: float = 90
    save: float = 120
    calculate: float = 120
    cells: float = 120
    close: float = 30


@dataclass(frozen=True, slots=True)
class Stalled:
    """The app did not answer in time, or the session had already stalled."""

    what: str

    def __str__(self) -> str:
        return f"the spreadsheet app stalled while {self.what}"


def com_error() -> type[Exception]:
    """The exception the app raises through COM, for a narrow ``except`` clause."""
    import pywintypes

    return pywintypes.com_error


def app_installed() -> bool:
    """Whether the spreadsheet app is registered for COM on this machine."""
    try:
        import winreg
    except ImportError:
        return False
    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, PROG_ID))
    except OSError:
        return False
    return True


def app_processes() -> frozenset[int]:
    """The process ids of every running copy of the app, this session's or not."""
    listed = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {IMAGE}", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        creationflags=_NO_WINDOW,
        check=False,
    ).stdout
    rows = csv.reader(line for line in listed.splitlines() if line.startswith('"'))
    return frozenset(int(row[1]) for row in rows if len(row) > 1)


def _kill(pids: Iterable[int]) -> None:
    for pid in pids:
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            capture_output=True,
            creationflags=_NO_WINDOW,
            check=False,
        )


class Session:
    """One private instance of the app, reused for many files.

    >>> Stalled("opening model.xlsx").__str__()
    'the spreadsheet app stalled while opening model.xlsx'
    """

    def __init__(self, timeouts: Timeouts | None = None) -> None:
        self.timeouts = timeouts or Timeouts()
        self.app: Any = None
        self.pid: int | None = None
        self._before: frozenset[int] = frozenset()
        self._stalled: Stalled | None = None
        self._fired = threading.Event()

    def __enter__(self) -> Self:
        import pythoncom

        pythoncom.CoInitialize()
        self._before = app_processes()
        try:
            self.guarded("starting", self.timeouts.start, self._start)
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def _start(self) -> None:
        import win32com.client
        import win32process

        app = win32com.client.DispatchEx(PROG_ID)
        self.app = app
        _, self.pid = win32process.GetWindowThreadProcessId(app.Hwnd)
        app.Visible = False
        app.DisplayAlerts = False
        app.AskToUpdateLinks = False
        app.EnableEvents = False
        app.ScreenUpdating = False
        app.AutomationSecurity = _MACROS_NEVER_RUN

    def _targets(self) -> frozenset[int]:
        """What the watchdog kills: this session's process, or, while it is still
        starting and has no window to ask, every copy of the app that is new since."""
        if self.pid is not None:
            return frozenset({self.pid})
        return app_processes() - self._before

    @property
    def stalled(self) -> Stalled | None:
        """The stall that killed this session, if one has."""
        return self._stalled

    def stalling(self) -> bool:
        """Whether the watchdog has fired during the current call.

        A caller that catches the app's exceptions around one step of a batch asks this
        first, so that the exception a kill causes is never mistaken for the app's answer.
        """
        return self._fired.is_set()

    def guarded[T](
        self, what: str, seconds: float, call: Callable[[], T]
    ) -> Result[T, Stalled]:
        """Run ``call`` under the watchdog.

        An exception the call raises for any other reason propagates, for the caller to
        turn into an error at its own narrow boundary.
        """
        if self._stalled is not None:
            return Err(Stalled(what))
        fired = threading.Event()
        self._fired = fired

        def kill() -> None:
            fired.set()
            _kill(self._targets())

        timer = threading.Timer(seconds, kill)
        timer.daemon = True
        timer.start()
        try:
            value = call()
        except Exception:
            if not fired.is_set():
                raise
            return self._die(what)
        finally:
            timer.cancel()
        if fired.is_set():
            return self._die(what)
        return Ok(value)

    def _die(self, what: str) -> Err[Stalled]:
        self._stalled = Stalled(what)
        return Err(self._stalled)

    def recalculate(self) -> Result[None, Stalled]:
        """Recalculate every open workbook in full, whatever mode it was saved in."""

        def calculate() -> None:
            self.app.Calculation = _CALCULATION_AUTOMATIC
            self.app.CalculateFullRebuild()

        return self.guarded("recalculating", self.timeouts.calculate, calculate)

    def _close(self) -> None:
        for workbook in list(self.app.Workbooks):
            workbook.Close(SaveChanges=False)
        self.app.Quit()

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Tear down in the one order that never stalls: close every workbook, quit, kill
        this session's process, drop the COM object, and uninitialise COM.

        ``Quit`` returns before the app has shut down, and releasing the COM object while
        it does blocks until an RPC timeout. Killing first avoids that; quitting first
        keeps the app from leaving recovery state behind.
        """
        import pythoncom

        try:
            if self.app is not None:
                self.guarded("closing", self.timeouts.close, self._close)
        finally:
            _kill(self._targets() if self.pid is None else {self.pid})
            self.app = None
            pythoncom.CoUninitialize()
