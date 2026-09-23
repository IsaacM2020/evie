"""The "say stop to cancel" window before anything Evie can't cleanly take back.

Deleting an event (and in Phase 3b sending, posting or buying) is announced first, then done
after a few seconds unless Isaac says "stop". The Brain's local stop path calls cancel().
"""
import asyncio
import logging
from typing import Awaitable, Callable

log = logging.getLogger("evie.countdown")


class Countdown:
    def __init__(self, seconds: float = 5.0):
        self.seconds = seconds
        self._task: asyncio.Task | None = None

    @property
    def pending(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, action: Callable[[], Awaitable[None]], seconds: float | None = None) -> None:
        self.cancel()
        wait = self.seconds if seconds is None else seconds

        async def run() -> None:
            await asyncio.sleep(wait)
            try:
                await action()
            except Exception:
                log.exception("countdown action failed")

        self._task = asyncio.get_running_loop().create_task(run())

    def cancel(self) -> bool:
        """True if something was waiting and is now called off."""
        if not self.pending:
            return False
        self._task.cancel()
        self._task = None
        return True
