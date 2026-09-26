"""Phase 6 P2: the "preference" slice of Memory V2 — small settings that should survive a
restart (a theme, an output mode, a default), each with who/what set it and when. Isaac's actual
day-to-day settings already live where they're used (evie.quiet, the app's `ui` dict) because
that's simpler for things read on every turn; this store is for the smaller, growing set of named
preferences that don't have an obvious home of their own yet, so they don't just get written into
whichever file happens to need one first.
"""
import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger("evie.preferences")

PREFS_FILE = Path.home() / "Library/Application Support/Evie/preferences.json"
MAX_KEYS = 100  # a growth cap: this is meant for a few dozen named settings, not an event log


@dataclass
class Preference:
    key: str
    value: object
    source: str = "isaac"  # who/what set it: isaac, a skill name, "default"
    set_at: float = 0.0


class Preferences:
    def __init__(self, path: Path = PREFS_FILE, clock: Callable[[], float] = time.time,
                 max_keys: int = MAX_KEYS):
        self._path, self._clock, self._max = path, clock, max_keys
        self._prefs: dict[str, Preference] = {}
        try:
            raw = json.loads(path.read_text())
            for k, v in raw.get("prefs", {}).items():
                self._prefs[k] = Preference(**v)
        except FileNotFoundError:
            pass
        except (ValueError, TypeError) as e:
            log.warning("preferences file unreadable, starting fresh: %r", e)

    def save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps({"prefs": {k: asdict(p) for k, p in self._prefs.items()}}))
        except OSError as e:
            log.warning("couldn't save preferences: %r", e)

    def set(self, key: str, value: object, source: str = "isaac") -> None:
        self._prefs[key] = Preference(key, value, source, self._clock())
        if len(self._prefs) > self._max:
            oldest = min(self._prefs.values(), key=lambda p: p.set_at)
            del self._prefs[oldest.key]
        self.save()

    def get(self, key: str, default: object = None) -> object:
        p = self._prefs.get(key)
        return p.value if p is not None else default

    def entry(self, key: str) -> Preference | None:
        return self._prefs.get(key)

    def all(self) -> dict[str, object]:
        return {k: p.value for k, p in self._prefs.items()}
