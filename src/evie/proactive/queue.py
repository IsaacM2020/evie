"""The follow-up queue. Each thing is offered once (deduped by source_key, remembered for 14 days,
across restarts). Only what was extracted is stored: never the raw words she overheard."""
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

log = logging.getLogger("evie.proactive")

FOLLOWUPS = Path.home() / "Library/Application Support/Evie/followups.json"
SEEN_DAYS = 14
RANK = {"high": 0, "normal": 1, "low": 2}


@dataclass
class FollowUp:
    kind: str  # overheard | heads_up | tasks | job | brief | deadline | stuck | resume
    line: str  # what she says (or the chip shows)
    source_key: str  # the same thing is only ever brought up once
    importance: str = "normal"  # high: time-critical, no "is now a good moment?" check
    ask: bool = False  # a question whose answer completes `request` ("What time?")
    request: str = ""  # e.g. "remember I have the dentist on Wednesday"
    on_yes: dict | None = None  # {"do": "job"|"turn"|"say"|"reopen", ...}
    options: list[str] = field(default_factory=list)  # the orb's quick answers
    chip_only: bool = False  # never spoken (the stuck detector)
    due: float = 0.0  # not before (0 = now)
    expires: float = 0.0  # gone after (0 = 8 h after it was added)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    offered: bool = False
    postponed: int = 0


class FollowUps:
    def __init__(self, path: Path = FOLLOWUPS, clock: Callable[[], float] = time.time):
        self._path, self._clock = path, clock
        self._items: list[FollowUp] = []
        self._seen: dict[str, float] = {}
        try:
            raw = json.loads(path.read_text())
            self._items = [FollowUp(**i) for i in raw.get("items", [])]
            self._seen = dict(raw.get("seen", {}))
        except FileNotFoundError:
            pass
        except (ValueError, TypeError) as e:
            log.warning("follow-ups file unreadable, starting fresh: %r", e)

    def add(self, it: FollowUp) -> bool:
        now = self._clock()
        if it.source_key in self._seen:
            return False
        it.due = it.due or now
        it.expires = it.expires or now + 8 * 3600
        self._seen[it.source_key] = now
        self._items.append(it)
        self.save()
        return True

    def get(self, fid: str) -> FollowUp | None:
        return next((i for i in self._items if i.id == fid), None)

    def remove(self, fid: str) -> FollowUp | None:
        it = self.get(fid)
        if it:
            self._items.remove(it)
            self.save()
        return it

    def due(self) -> list[FollowUp]:
        now = self._clock()
        self._items = [i for i in self._items if i.expires > now]
        ready = [i for i in self._items if not i.offered and i.due <= now]
        return sorted(ready, key=lambda i: (RANK.get(i.importance, 1), i.due))

    def waiting(self) -> list[FollowUp]:
        """Offered as a chip and not answered yet (the orb's badge)."""
        now = self._clock()
        return [i for i in self._items if i.offered and i.expires > now]

    def save(self) -> None:
        now = self._clock()
        self._seen = {k: t for k, t in self._seen.items() if now - t < SEEN_DAYS * 86400}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps({"items": [asdict(i) for i in self._items], "seen": self._seen}))
        except OSError as e:
            log.warning("couldn't save follow-ups: %r", e)
