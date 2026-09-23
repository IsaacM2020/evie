"""Today's conversation with Isaac, so "move it to 5" and "what about Friday?" make sense.

Only turns meant for Evie are kept (what he said, what she said, what she did). Overheard
speech never comes in here. A day runs 4am to 4am; each day is one JSON-lines file in App
Support, so a core restart doesn't wipe it. Answers see the last 12 turns word for word plus a
short summary of anything earlier, which Groq refreshes every 10 turns off the hot path.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from evie.calendar_store import TZ

CONV_DIR = Path.home() / "Library/Application Support/Evie"
VERBATIM = 12
SUMMARY_EVERY = 10


class Conversation:
    def __init__(self, folder: Path = CONV_DIR, now: Callable[[], datetime] = lambda: datetime.now(TZ)):
        self._dir, self._now = folder, now
        self._day: str | None = None
        self._turns: list[dict] = []
        self._summary = ""
        self._summarized_upto = 0  # how many turns the summary covers

    def _today(self) -> str:
        return (self._now() - timedelta(hours=4)).strftime("%Y-%m-%d")

    def _load(self) -> None:
        day = self._today()
        if day == self._day:
            return
        self._day, self._turns, self._summary, self._summarized_upto = day, [], "", 0
        p = self._dir / f"conversation-{day}.jsonl"
        if p.exists():
            for line in p.read_text().splitlines():
                try:
                    self._turns.append(json.loads(line))
                except ValueError:
                    continue
        s = self._dir / f"conversation-{day}.summary.json"
        if s.exists():
            try:
                d = json.loads(s.read_text())
                self._summary, self._summarized_upto = d.get("summary", ""), int(d.get("upto", 0))
            except ValueError:
                pass

    @property
    def summary(self) -> str:
        self._load()
        return self._summary

    def add(self, isaac: str, evie: str, did: str | None = None) -> None:
        self._load()
        row = {"t": self._now().strftime("%H:%M"), "isaac": isaac, "evie": evie, "did": did}
        self._turns.append(row)
        self._dir.mkdir(parents=True, exist_ok=True)
        with (self._dir / f"conversation-{self._day}.jsonl").open("a") as f:
            f.write(json.dumps(row) + "\n")

    def lines(self, n: int = VERBATIM) -> list[str]:
        self._load()
        return [f'{t["t"]} Isaac: "{t["isaac"]}" / Evie: "{t["evie"]}"' for t in self._turns[-n:]]

    def older(self) -> list[dict]:
        self._load()
        return self._turns[:-VERBATIM] if len(self._turns) > VERBATIM else []

    def needs_summary(self) -> bool:
        return len(self.older()) - self._summarized_upto >= SUMMARY_EVERY

    def set_summary(self, text: str) -> None:
        self._load()
        self._summary, self._summarized_upto = text, len(self.older())
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / f"conversation-{self._day}.summary.json").write_text(
            json.dumps({"summary": text, "upto": self._summarized_upto}))
