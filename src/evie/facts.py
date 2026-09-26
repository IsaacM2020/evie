"""Things Isaac asked Evie to remember that are neither a task nor an event ("my locker code is
4129"). A small local file; the latest ones go into every answer so she can use them."""
import json
import time
from pathlib import Path

FACTS_FILE = Path.home() / "Library/Application Support/Evie/facts.jsonl"


class FactStore:
    def __init__(self, path: Path | None = FACTS_FILE, keep: int = 200):
        self._path, self._keep = path, keep
        self._facts: list[dict] = []
        if path and path.exists():
            for line in path.read_text().splitlines():
                try:
                    self._facts.append(json.loads(line))
                except ValueError:
                    continue
        self._facts = self._facts[-keep:]

    def _save(self) -> None:
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text("".join(json.dumps(f) + "\n" for f in self._facts))

    def add(self, text: str, source: str = "isaac") -> None:
        self._facts = (self._facts + [{"t": time.time(), "text": text, "source": source}])[-self._keep:]
        self._save()

    def remove_last(self) -> str | None:
        if not self._facts:
            return None
        f = self._facts.pop()
        self._save()
        return f["text"]

    def recent(self, n: int = 20) -> list[str]:
        return [f["text"] for f in self._facts[-n:]]
