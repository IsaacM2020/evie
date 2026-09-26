"""Phase 6 P1: procedural memory for screen tasks. On repeated success, remember the exact steps
that worked and prefer them over asking a model to plan again.

"Never run one without checking current state" isn't re-implemented here: find() only ever hands
a cached plan back to computer.recipes.Recipes, which passes it through Planner.run(), and that
runs the SAME `expect` checks against the live screen a fresh plan would. A stale procedure fails
its expect check exactly like a wrong fresh guess does today, and Planner's own replan-with-a-
fresh-plan-call recovery takes over — this module just decides when to skip straight to a plan
that has already worked more than once, and retires one that stops working.
"""
import json
import logging
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable

log = logging.getLogger("evie.procedures")

PROCEDURES_FILE = Path.home() / "Library/Application Support/Evie/procedures.json"
MATCH_MIN = 0.6
REUSE_AFTER = 2  # successes needed before a procedure is trusted enough to replace planning
RETIRE_AFTER = 2  # consecutive failures since its last success before it's retired
MAX_PROCEDURES = 200  # a growth cap (Phase 6 P2 memory policy): least-recently-used drop first

_ON_OFF = re.compile(r"\b(on|off)\b")
_NUMBER = re.compile(r"\b\d+(?:\.\d+)?\b")


def _entities(goal: str) -> frozenset[str]:
    """The parts of a goal that must match EXACTLY for a procedure to be reused: on/off state and
    numbers. Word-shape similarity alone conflates 'wifi on' with 'wifi off' (0.88 by
    SequenceMatcher) -- this catches that class of mismatch regardless of overall phrasing
    similarity, without needing capitalization (goals arrive lowercased from speech-to-text, so a
    proper-noun heuristic keyed on capital letters would never fire on real input)."""
    g = goal.lower()
    return frozenset(_ON_OFF.findall(g)) | frozenset(_NUMBER.findall(g))


@dataclass
class Procedure:
    goal_pattern: str
    steps: list[dict] = field(default_factory=list)
    id: str = field(default_factory=lambda: f"p{uuid.uuid4().hex[:8]}")
    version: int = 1
    status: str = "learning"  # learning (not yet trusted) | active (reused) | retired (stopped trying)
    success_count: int = 0
    failure_count: int = 0
    consecutive_failures: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0
    last_used_at: float | None = None
    prerequisites: str = "The goal it was learned from must still describe what's wanted."
    verification: str = "Planner's own `expect` steps re-check the screen before this is trusted."
    failure_recovery: str = "record_failure() retires it after repeated misses; a fresh plan is learned again."


class ProcedureStore:
    def __init__(self, path: Path = PROCEDURES_FILE, clock: Callable[[], float] = time.time,
                 max_procedures: int = MAX_PROCEDURES):
        self._path, self._clock, self._max = path, clock, max_procedures
        self._procs: dict[str, Procedure] = {}
        try:
            raw = json.loads(path.read_text())
            for p in raw.get("procedures", []):
                proc = Procedure(**p)
                self._procs[proc.id] = proc
        except FileNotFoundError:
            pass
        except (ValueError, TypeError) as e:
            log.warning("procedures file unreadable, starting fresh: %r", e)

    def save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps({"procedures": [asdict(p) for p in self._procs.values()]}))
        except OSError as e:
            log.warning("couldn't save procedures: %r", e)

    @staticmethod
    def _score(goal: str, proc: Procedure) -> float:
        return SequenceMatcher(None, goal.lower(), proc.goal_pattern.lower()).ratio()

    def find_any(self, goal: str) -> Procedure | None:
        """The closest record for this goal, whatever its status. learn() uses this to decide
        whether a fresh success reinforces an existing record or starts a new one."""
        cands = [p for p in self._procs.values() if p.status != "retired"
                 and _entities(goal) == _entities(p.goal_pattern)]
        if not cands:
            return None
        best = max(cands, key=lambda p: self._score(goal, p))
        return best if self._score(goal, best) >= MATCH_MIN else None

    def find(self, goal: str) -> Procedure | None:
        """A procedure proven enough (REUSE_AFTER successes) to run instead of planning fresh."""
        proc = self.find_any(goal)
        return proc if proc and proc.status == "active" else None

    def learn(self, goal: str, steps: list[dict]) -> Procedure:
        """A fresh plan (not a cached one) just succeeded: reinforce a matching record or start one."""
        proc, now = self.find_any(goal), self._clock()
        if proc is None:
            proc = Procedure(goal_pattern=goal, steps=steps, created_at=now, updated_at=now,
                             success_count=1, last_used_at=now)
            self._procs[proc.id] = proc
            self._enforce_cap()
        else:
            proc.steps, proc.success_count = steps, proc.success_count + 1
            proc.consecutive_failures, proc.updated_at, proc.last_used_at = 0, now, now
            proc.version += 1
            if proc.success_count >= REUSE_AFTER:
                proc.status = "active"
        self.save()
        return proc

    def record_success(self, proc_id: str) -> None:
        proc = self._procs.get(proc_id)
        if proc is None:
            return
        proc.success_count += 1
        proc.consecutive_failures = 0
        proc.last_used_at = proc.updated_at = self._clock()
        self.save()

    def record_failure(self, proc_id: str) -> None:
        proc = self._procs.get(proc_id)
        if proc is None:
            return
        proc.failure_count += 1
        proc.consecutive_failures += 1
        proc.updated_at = self._clock()
        if proc.consecutive_failures >= RETIRE_AFTER:
            proc.status = "retired"
        self.save()

    def _enforce_cap(self) -> None:
        over = len(self._procs) - self._max
        if over <= 0:
            return
        oldest = sorted(self._procs.values(), key=lambda p: p.last_used_at or p.created_at)[:over]
        for proc in oldest:
            del self._procs[proc.id]

    def get(self, proc_id: str) -> Procedure | None:
        return self._procs.get(proc_id)

    def list_procedures(self) -> list[Procedure]:
        return list(self._procs.values())
