"""The core's end of the app's /ws/mouth socket: Evie's voice goes out, "done" comes back.

The Mouth's player runs on a thread, so sends are handed to the event loop, which writes them
to the socket in order. When the app disconnects, anything still waiting is released so the
voice falls back to the Mac's own speakers instead of hanging.
"""
import asyncio
import threading


class MouthLink:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue | None = None
        self._waiters: dict[str, threading.Event] = {}
        self.connected = False

    def attach(self, loop: asyncio.AbstractEventLoop) -> asyncio.Queue:
        self._loop, self._queue = loop, asyncio.Queue()
        self.connected = True
        return self._queue

    def detach(self) -> None:
        self.connected = False
        self._queue = None
        for ev in list(self._waiters.values()):
            ev.set()
        self._waiters.clear()

    def _put(self, item) -> None:
        loop, q = self._loop, self._queue
        if loop is None or q is None:
            return
        try:
            loop.call_soon_threadsafe(q.put_nowait, item)
        except RuntimeError:  # loop closed while shutting down
            pass

    def send_json(self, msg: dict) -> None:
        self._put(("json", msg))

    def send_bytes(self, data: bytes) -> None:
        self._put(("bytes", data))

    def waiter(self, lid: str) -> threading.Event:
        ev = self._waiters.setdefault(lid, threading.Event())
        if not self.connected:
            ev.set()
        return ev

    def finished(self, lid: str) -> None:
        ev = self._waiters.pop(lid, None)
        if ev:
            ev.set()
