"""Phase Computer Use V2, P1-A: one versioned snapshot of the whole Mac -- displays, windows
(with which display and whether focused), the front app/element, and Safari's tabs (reused from
world.py, not duplicated). Built from a single Swift `state` op read (not yet implemented on the
Swift side as of this file: see docs/superpowers/plans/2026-09-26-computer-use-v2.md, Deferred
Work). world.py's `resolve()` keeps using the existing `world` op for now -- this module is the
data model later work targets, not a replacement for World yet.

Every read carries a version + timestamp so a caller can tell a stale snapshot from a fresh one
(spec §3: "A button discovered in snapshot S41 cannot be blindly clicked after the interface has
changed to S42.").
"""
from dataclasses import dataclass, field

from evie.computer.world import Tab


@dataclass(frozen=True)
class Display:
    id: str
    builtin: bool
    frame: tuple[int, int, int, int]  # (x, y, width, height) in global screen coordinates


@dataclass(frozen=True)
class Window:
    app: str
    title: str
    frame: tuple[int, int, int, int]
    display: str  # a Display.id
    focused: bool


@dataclass
class ComputerState:
    displays: list[Display] = field(default_factory=list)
    windows: list[Window] = field(default_factory=list)
    front_app: str = ""
    front_element: dict | None = None
    tabs: list[Tab] = field(default_factory=list)
    version: int = 0
    ts: float = 0.0

    @classmethod
    def from_data(cls, d: dict) -> "ComputerState":
        displays = [Display(str(x.get("id", "")), bool(x.get("builtin")), tuple(x.get("frame", [0, 0, 0, 0])))
                    for x in d.get("displays") or [] if isinstance(x, dict)]
        windows = [Window(str(w.get("app", "")), str(w.get("title", "")), tuple(w.get("frame", [0, 0, 0, 0])),
                          str(w.get("display", "")), bool(w.get("focused")))
                   for w in d.get("windows") or [] if isinstance(w, dict)]
        tabs = [Tab(int(t.get("window", 0)), int(t.get("order", 1)), int(t.get("index", 1)), bool(t.get("current")),
                    str(t.get("title", "")), str(t.get("url", ""))) for t in d.get("tabs") or [] if isinstance(t, dict)]
        fe = d.get("front_element")
        return cls(displays=displays, windows=windows, front_app=str(d.get("front_app", "")),
                   front_element=fe if isinstance(fe, dict) else None, tabs=tabs,
                   version=int(d.get("version", 0)), ts=float(d.get("ts", 0.0)))
