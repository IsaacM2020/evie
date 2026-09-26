"""Phase 6 P0: a persistent, structured view of what's going on, kept beside the event bus
(evie.events), not inside it. events.py stays a bare pub/sub with in-RAM history only; this
module subscribes to it like any other reader (the WebSocket is another one) and folds what
happens into state that survives a core restart.

Only genuinely new state is persisted here: current app/window, system health, a short list of
recent bus events, and the "active thread" (what's being talked about right now). Calendar,
tasks, projects and people already have their own authoritative stores elsewhere; this module
reads them through injected callables instead of copying them, so there is only ever one place
each fact actually lives (no raw-data pollution).

Deterministic only: apply() is a pattern match over already-structured event dicts. No model
call, ever. Overheard chatter is never stored as text: "heard" events already only reach the bus
when Evie acted or Isaac addressed her (see brain.py), so mirroring them here is as safe as the
panel that already shows them.
"""
import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

log = logging.getLogger("evie.world")

WORLD_FILE = Path.home() / "Library/Application Support/Evie/world.json"
ACTIONS_LOG = Path.home() / "Library/Logs/Evie/actions.jsonl"
RECENT_EVENTS_KEEP = 100
RECENT_ACTIONS_SHOW = 20


@dataclass
class HealthCheck:
    ok: bool = True
    detail: str = ""
    at: float = 0.0
    consecutive_failures: int = 0


@dataclass
class ActiveThread:
    text: str = ""
    route: str | None = None
    speaker: str = ""
    at: float = 0.0


@dataclass
class WorldState:
    """The persisted part. Everything else in a snapshot() is read live from its own store."""
    updated_at: float = 0.0
    current_app: str = ""
    front_window: str = ""
    in_call: bool = False
    active_thread: ActiveThread = field(default_factory=ActiveThread)
    active_jobs: dict[str, dict] = field(default_factory=dict)  # id -> {goal, status, started, tier}
    last_job: dict | None = None  # most recent job, kept a while after it finishes too
    system_health: dict[str, HealthCheck] = field(default_factory=dict)
    recent_events: list[dict] = field(default_factory=list)  # capped, oldest first


def _load(path: Path) -> WorldState:
    try:
        raw = json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return WorldState()
    st = WorldState(updated_at=raw.get("updated_at", 0.0), current_app=raw.get("current_app", ""),
                    front_window=raw.get("front_window", ""), in_call=bool(raw.get("in_call", False)),
                    last_job=raw.get("last_job"), recent_events=list(raw.get("recent_events", [])))
    at = raw.get("active_thread") or {}
    st.active_thread = ActiveThread(text=at.get("text", ""), route=at.get("route"),
                                    speaker=at.get("speaker", ""), at=at.get("at", 0.0))
    st.active_jobs = dict(raw.get("active_jobs", {}))
    st.system_health = {k: HealthCheck(**v) for k, v in raw.get("system_health", {}).items()}
    return st


class WorldStore:
    """Owns the WorldState, applies bus events to it, and answers typed views of it. One per
    core process; server.py wires it beside the EventBus the same way it wires everything else."""

    def __init__(self, path: Path = WORLD_FILE, clock: Callable[[], float] = time.time,
                 calendar_view: Callable[[], dict] | None = None,
                 tasks_view: Callable[[], list] | None = None,
                 projects_view: Callable[[], list] | None = None,
                 people_view: Callable[[], dict] | None = None,
                 commitments_view: Callable[[], list] | None = None,
                 actions_log: Path = ACTIONS_LOG):
        self._path, self._clock = path, clock
        self._calendar, self._tasks, self._projects = calendar_view, tasks_view, projects_view
        self._people, self._commitments = people_view, commitments_view
        self._actions_log = actions_log
        self.state = _load(path)

    # -- persistence ------------------------------------------------------------------------
    def save(self) -> None:
        self.state.updated_at = self._clock()
        d = asdict(self.state)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(d))
        except OSError as e:
            log.warning("couldn't save world state: %r", e)

    # -- the event-bus side: a pure subscriber, events.py is untouched ----------------------
    def apply(self, ev: dict) -> None:
        kind = ev.get("kind")
        handler = getattr(self, f"_on_{kind}", None)
        if handler is not None:
            handler(ev)
        self.state.recent_events.append({"kind": kind, "t": ev.get("t", self._clock())})
        self.state.recent_events = self.state.recent_events[-RECENT_EVENTS_KEEP:]
        self.save()

    def _on_heard(self, ev: dict) -> None:
        text = ev.get("text") or ""
        if text:  # already filtered upstream: only addressed or acted-on speech reaches "heard"
            self.state.active_thread = ActiveThread(text=text, speaker=ev.get("speaker", "isaac"), at=self._clock())

    def _on_verdict(self, ev: dict) -> None:
        self.state.active_thread.route = ev.get("route")

    def _on_job_started(self, ev: dict) -> None:
        jid = ev.get("id")
        if jid:
            self.state.active_jobs[jid] = {"id": jid, "goal": ev.get("goal", ""), "status": "running",
                                           "tier": ev.get("tier", ""), "started": self._clock()}

    def _on_job_progress(self, ev: dict) -> None:
        job = self.state.active_jobs.get(ev.get("id"))
        if job:
            job["step"] = ev.get("step", "")
            job["progress"] = f"{ev.get('done', 0)}/{ev.get('total', 0)}"

    def _on_job_done(self, ev: dict) -> None:
        jid = ev.get("id")
        job = self.state.active_jobs.pop(jid, None) or {"id": jid}
        job["status"] = ev.get("status", "done")
        job["summary"] = ev.get("summary", "")
        job["ended"] = self._clock()
        self.state.last_job = job

    def _on_health(self, ev: dict) -> None:
        """evie.health publishes this only on a degraded/recovered transition; apply() still logs
        every event kind to recent_events regardless, same as any other."""
        self.set_health(ev.get("component", ""), bool(ev.get("ok")), ev.get("detail", ""))

    def _on_state(self, ev: dict) -> None:
        s = ev.get("state")
        if s in ("idle", "working", "listening", "thinking", "speaking"):
            pass  # transient UI state, not worth persisting; front_app/in_call come from scene updates below

    def scene_changed(self, front_app: str, in_call: bool, window_title: str = "") -> None:
        """Brain.scene() changes aren't bus events today; called directly on each turn instead
        of adding a new publish() to a hot path that doesn't need one."""
        self.state.current_app, self.state.in_call, self.state.front_window = front_app, in_call, window_title
        self.save()

    # -- self-monitoring feeds this directly (P2), not just through events ------------------
    def set_health(self, component: str, ok: bool, detail: str = "") -> None:
        prev = self.state.system_health.get(component)
        streak = (prev.consecutive_failures + 1) if (prev and not ok) else (0 if ok else 1)
        self.state.system_health[component] = HealthCheck(ok=ok, detail=detail, at=self._clock(),
                                                           consecutive_failures=streak)
        self.save()

    # -- typed views, never a whole-world prompt --------------------------------------------
    def recent_actions(self, n: int = RECENT_ACTIONS_SHOW) -> list[dict]:
        if not self._actions_log.exists():
            return []
        try:
            lines = self._actions_log.read_text().splitlines()[-n:]
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def snapshot(self) -> dict:
        """Everything at once, for a debug endpoint. Callers that want to hand this to a model
        should use brief() with the packs they actually need instead."""
        return {
            "updated_at": self.state.updated_at,
            "current_app": self.state.current_app, "front_window": self.state.front_window,
            "in_call": self.state.in_call,
            "active_thread": asdict(self.state.active_thread),
            "active_jobs": list(self.state.active_jobs.values()),
            "last_job": self.state.last_job,
            "recent_actions": self.recent_actions(),
            "commitments": self._commitments() if self._commitments else [],
            "calendar": self._calendar() if self._calendar else {},
            "tasks": self._tasks() if self._tasks else [],
            "projects": self._projects() if self._projects else [],
            "people": self._people() if self._people else {},
            "system_health": {k: asdict(v) for k, v in self.state.system_health.items()},
        }

    def brief(self, fields: set[str]) -> dict:
        """Only the requested slices (evie.context_packs does the same for calendar/tasks/etc.)."""
        full = self.snapshot()
        return {k: v for k, v in full.items() if k in fields}


async def run(bus, store: WorldStore) -> None:
    """Subscribe to the bus and fold events into the store until cancelled. events.py needs no
    changes: subscribe()/unsubscribe() already exist for exactly this (the WebSocket uses them)."""
    q = bus.subscribe()
    try:
        while True:
            store.apply(await q.get())
    finally:
        bus.unsubscribe(q)
