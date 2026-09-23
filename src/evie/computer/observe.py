"""A snapshot of one app's screen, as the app's eyes reported it, and a compact text version
for the planner: one line per element, each with an id that only exists in this snapshot."""
import json
from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass
class Screen:
    snapshot: str
    app: str
    kind: str  # "web" (a Safari page) or "app" (the Accessibility tree)
    elements: list[dict] = field(default_factory=list)
    url: str = ""
    window: str = ""

    @classmethod
    def from_data(cls, data: dict) -> "Screen":
        try:
            els = json.loads(data.get("elements") or "[]")
        except ValueError:
            els = []
        return cls(snapshot=data.get("snapshot", ""), app=data.get("app", ""), kind=data.get("kind", "app"),
                   elements=[e for e in els if isinstance(e, dict) and e.get("id")],
                   url=data.get("url", ""), window=data.get("window", ""))

    @property
    def ids(self) -> set[str]:
        return {e["id"] for e in self.elements}

    def get(self, eid: str) -> dict | None:
        return next((e for e in self.elements if e["id"] == eid), None)

    def compact(self, limit: int = 160) -> str:
        """ "w5 link "I Spent 7 Days Buried Alive" (main) -> youtube.com/watch": what the planner reads."""
        head = [f"App: {self.app}" + (f" | Page: {self.window} ({self.url[:120]})" if self.url else
                                      (f" | Window: {self.window}" if self.window else ""))]
        lines = []
        for e in self.elements[:limit]:
            bits = [e["id"], e.get("role", ""), json.dumps(e.get("label", ""))]
            if e.get("value"):
                bits.append(f"value={json.dumps(e['value'][:40])}")
            if e.get("region"):
                bits.append(f"({e['region']})")
            if e.get("enabled") is False:
                bits.append("[disabled]")
            if e.get("onscreen") is False:
                bits.append("[off screen]")
            if e.get("href"):
                u = urlparse(e["href"])
                bits.append(f"-> {u.netloc}{u.path}"[:70])
            lines.append(" ".join(bits))
        return "\n".join(head + lines)
