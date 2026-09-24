"""The "say stop to cancel" window before anything Evie can't cleanly take back.

Deleting an event (and in Phase 3b sending, posting or buying) is announced first, then done
after a few seconds unless Isaac says "stop". The Brain's local stop path calls cancel().
"""
import asyncio
import logging
from typing import Awaitable, Callable

log = logging.getLogger("evie.countdown")


class Countdown:
    def __init__(self, seconds: float = 5.0, text_s: Callable[[], float] = lambda: 0.0,
                 on_start: Callable[[float], None] | None = None):
        """text_s: in text mode he has to read the line and tap Cancel, so the window is at least this
        long. on_start: the orb shows the window as a bar with Cancel."""
        self.seconds = seconds
        self._text_s, self._on_start = text_s, on_start
        self._task: asyncio.Task | None = None

    @property
    def pending(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, action: Callable[[], Awaitable[None]], seconds: float | None = None) -> None:
        self.cancel()
        wait = max(self.seconds if seconds is None else seconds, self._text_s())
        if self._on_start:
            self._on_start(wait)

        async def run() -> None:
            await asyncio.sleep(wait)
            try:
                await action()
            except Exception:
                log.exception("countdown action failed")

        self._task = asyncio.get_running_loop().create_task(run())

    async def wait(self, seconds: float | None = None) -> bool:
        """Say-stop window for something that happens inline (a 3b send): True if nobody said stop."""
        async def nothing() -> None:
            return None
        self.start(nothing, seconds)
        task = self._task
        await asyncio.wait({task})
        return not task.cancelled()

    def cancel(self) -> bool:
        """True if something was waiting and is now called off."""
        if not self.pending:
            return False
        self._task.cancel()
        self._task = None
        return True


class Countdowns:
    """Separate windows for separate things (a pending delete and a 3b send never cancel each
    other), but "stop" calls off whichever are waiting."""

    def __init__(self, *cds: Countdown):
        self._cds = cds

    @property
    def pending(self) -> bool:
        return any(c.pending for c in self._cds)

    def cancel(self) -> bool:
        return any([c.cancel() for c in self._cds])  # a list, so every one is cancelled
