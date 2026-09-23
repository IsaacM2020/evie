"""Evie's hands: the core asks the menu bar app to do things only a signed app may do.

macOS gives permissions (control Spotify, write to Calendar) to apps, and a launchd Python
process can't reliably get them. So the core publishes a `do` command on the event WebSocket,
the app runs it and POSTs the result to /hands/result. Every command has an id (so results
can't get mixed up) and an expiry (so a reconnecting app never runs a stale one).
Phase 3b (Fluid) uses the same channel for screen control.
"""
import asyncio
import time
import uuid
from dataclasses import dataclass, field

from evie.events import EventBus

UNREACHABLE = "can't reach my hands, is the Evie app running?"


@dataclass
class HandsResult:
    ok: bool
    detail: str = ""
    data: dict = field(default_factory=dict)


class Hands:
    def __init__(self, bus: EventBus):
        self._bus = bus
        self._waiting: dict[str, asyncio.Future] = {}

    async def do(self, op: str, timeout: float = 5.0, **args) -> HandsResult:
        if not self._bus.subscribers:
            return HandsResult(False, UNREACHABLE)
        cid = uuid.uuid4().hex[:12]
        fut = asyncio.get_running_loop().create_future()
        self._waiting[cid] = fut
        self._bus.publish("do", id=cid, op=op, args=args, expires=time.time() + timeout)
        try:
            return await asyncio.wait_for(fut, timeout)
        except TimeoutError:
            return HandsResult(False, UNREACHABLE)
        finally:
            self._waiting.pop(cid, None)

    def result(self, cid: str, ok: bool, detail: str = "", data: dict | None = None) -> bool:
        """The app reporting back. False if nobody is waiting (too late, or unknown id)."""
        fut = self._waiting.get(cid)
        if fut is None or fut.done():
            return False
        fut.set_result(HandsResult(ok, detail, data or {}))
        return True
