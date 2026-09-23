"""Timers: plain asyncio in the core. Saved to disk so a core restart doesn't lose one; a timer
that ran out while the core was down fires as soon as it's back."""
import asyncio
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger("evie.timers")

TIMERS_FILE = Path.home() / "Library/Application Support/Evie/timers.json"


@dataclass
class Timer:
    id: str
    seconds: float
    ends_at: float
    label: str = ""


def done_line(t: Timer) -> str:
    """What Evie says when it goes off: a reminder says what it was for."""
    if t.label:
        return f"Reminder: {t.label}."
    from evie.skills.parse import say_duration
    return f"Your {say_duration(int(t.seconds))} timer's done."


class Timers:
    def __init__(self, on_done: Callable[[Timer], None], path: Path | None = TIMERS_FILE,
                 clock: Callable[[], float] = time.time):
        self._on_done, self._path, self._clock = on_done, path, clock
        self._timers: dict[str, Timer] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    def start(self, seconds: float, label: str = "") -> Timer:
        t = Timer(uuid.uuid4().hex[:8], seconds, self._clock() + seconds, label)
        self._schedule(t)
        self._save()
        return t

    def cancel(self, tid: str | None = None) -> Timer | None:
        """Cancel one timer (the most recent if no id). None if there was nothing to cancel."""
        if not self._timers:
            return None
        tid = tid or list(self._timers)[-1]
        t = self._timers.pop(tid, None)
        task = self._tasks.pop(tid, None)
        if task:
            task.cancel()
        self._save()
        return t

    def active(self) -> list[Timer]:
        return list(self._timers.values())

    def restore(self) -> None:
        if not self._path or not self._path.exists():
            return
        try:
            saved = [Timer(**d) for d in json.loads(self._path.read_text())]
        except (ValueError, TypeError):
            log.warning("timers file unreadable, ignoring it")
            return
        for t in saved:
            self._schedule(t)

    def _schedule(self, t: Timer) -> None:
        self._timers[t.id] = t
        self._tasks[t.id] = asyncio.get_running_loop().create_task(self._wait(t))

    async def _wait(self, t: Timer) -> None:
        await asyncio.sleep(max(0.0, t.ends_at - self._clock()))
        self._timers.pop(t.id, None)
        self._tasks.pop(t.id, None)
        self._save()
        try:
            self._on_done(t)
        except Exception:
            log.exception("timer callback failed")

    def _save(self) -> None:
        if not self._path:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps([asdict(t) for t in self._timers.values()]))

    def close(self) -> None:
        """Stop the in-process waits but keep the file, so the next core picks them up."""
        for task in self._tasks.values():
            task.cancel()
        self._tasks.clear()
