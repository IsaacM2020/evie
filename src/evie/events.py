"""A tiny in-process event bus. The core publishes what's happening (heard, verdict, say,
job updates) and the menu bar app's WebSocket reads it live."""
import asyncio
import time
from collections import deque


class EventBus:
    def __init__(self, maxsize: int = 200, keep: int = 50):
        self._subs: set[asyncio.Queue] = set()
        self._maxsize = maxsize
        self._history: deque[dict] = deque(maxlen=keep)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._maxsize)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def publish(self, kind: str, **data) -> None:
        ev = {"kind": kind, "t": time.time(), **data}
        self._history.append(ev)
        for q in self._subs:
            if q.full():  # a slow reader loses the oldest event, never blocks Evie
                q.get_nowait()
            q.put_nowait(ev)

    def history(self) -> list[dict]:
        return list(self._history)
