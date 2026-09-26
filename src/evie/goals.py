"""Phase 6 P1: persistent goals and initiatives Isaac is tracking with Evie — a fridge project
("get the NOI regional qualification"), not a to-do ("buy milk") or calendar event.

Command parsing is plain, deterministic code, exactly like is_stop()/is_other() in brain.py:
this never asks Jev, so it can't shift the switchboard's carefully tuned thresholds (see
evals/TUNING.md) and it can't be talked out of matching "new goal" by anything upstream. Only
free text a parser can't read (the outcome/deadline in a new goal) goes to Groq, the same rule
skills/parse.py already follows for everything else.
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

log = logging.getLogger("evie.goals")

GOALS_FILE = Path.home() / "Library/Application Support/Evie/goals.json"
MATCH_MIN = 0.55
STALE_DAYS = 10.0
MAX_GOALS = 300  # a growth cap (Memory V2): only ever drops finished/dropped goals, never live ones
# Isaac says a short nickname ("NOI", "the cricket predictor"); the stored outcome is a full
# sentence. Plain SequenceMatcher.ratio() over the whole strings scores that low even when the
# nickname is an exact, unambiguous substring, so word overlap (ignoring filler words) gets a say
# too, and the better of the two wins.
_STOP_WORDS = {"the", "a", "an", "my", "on", "of", "for", "goal", "going", "is", "it", "to", "s"}
_WORD = re.compile(r"[a-z0-9]+")


def _match_score(ref: str, target: str) -> float:
    seq = SequenceMatcher(None, ref.lower(), target.lower()).ratio()
    ref_words = set(_WORD.findall(ref.lower())) - _STOP_WORDS
    if not ref_words:
        return seq
    target_words = set(_WORD.findall(target.lower())) - _STOP_WORDS
    overlap = len(ref_words & target_words) / len(ref_words)
    return max(seq, overlap)

NEW_Q = ('Isaac wants Evie to track a new personal goal. Return {"outcome": what he wants to achieve, in his own '
         'words, short and clear, "deadline": a date or time frame if he gave one as he said it (e.g. '
         '"December", "before IB starts"), or "" if none}.')
PROGRESS_Q = ('Isaac is giving a progress update on one of his tracked goals. Return {"goal": which goal he means, '
              'in a few of his own words, "note": what progress he made, in his own words, "next_action": what he '
              'said he\'ll do next, or "" if he didn\'t say}.')


@dataclass
class Goal:
    outcome: str
    deadline: str = ""
    status: str = "active"  # active | paused | done | dropped
    milestones: list[dict] = field(default_factory=list)  # [{"text": ..., "done": False}]
    next_action: str = ""
    blockers: list[str] = field(default_factory=list)
    depends_on: tuple[str, ...] = ()
    id: str = field(default_factory=lambda: f"g{uuid.uuid4().hex[:8]}")
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_progress_at: float | None = None
    last_progress_note: str = ""


class GoalStore:
    def __init__(self, path: Path = GOALS_FILE, clock: Callable[[], float] = time.time,
                 max_goals: int = MAX_GOALS):
        self._path, self._clock, self._max = path, clock, max_goals
        self._goals: dict[str, Goal] = {}
        try:
            raw = json.loads(path.read_text())
            for g in raw.get("goals", []):
                g["depends_on"] = tuple(g.get("depends_on", ()))
                goal = Goal(**g)
                self._goals[goal.id] = goal
        except FileNotFoundError:
            pass
        except (ValueError, TypeError) as e:
            log.warning("goals file unreadable, starting fresh: %r", e)

    def save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps({"goals": [asdict(g) for g in self._goals.values()]}))
        except OSError as e:
            log.warning("couldn't save goals: %r", e)

    def create(self, outcome: str, deadline: str = "", next_action: str = "") -> Goal:
        now = self._clock()  # not the dataclass's own default_factory: that can't see our injected clock
        g = Goal(outcome=outcome.strip(), deadline=deadline.strip(), next_action=next_action.strip(),
                created_at=now, updated_at=now)
        self._goals[g.id] = g
        self._enforce_cap()
        self.save()
        return g

    def _enforce_cap(self) -> None:
        """A growth cap only ever removes finished business (done/dropped), oldest first — an
        active or paused goal is a live commitment, never silently deleted."""
        over = len(self._goals) - self._max
        if over <= 0:
            return
        removable = sorted((g for g in self._goals.values() if g.status in ("done", "dropped")),
                          key=lambda g: g.updated_at)
        for g in removable[:over]:
            del self._goals[g.id]

    def get(self, gid: str) -> Goal | None:
        return self._goals.get(gid)

    def list_goals(self, status: str | None = None) -> list[Goal]:
        vals = list(self._goals.values())
        return [g for g in vals if g.status == status] if status else vals

    def find_by_text(self, text: str, statuses: tuple[str, ...] = ("active", "paused")) -> Goal | None:
        """The goal whose outcome sounds most like what he said, or None if nothing is close."""
        cands = [g for g in self._goals.values() if g.status in statuses]
        if not cands:
            return None
        scored = [(_match_score(text, g.outcome), g) for g in cands]
        best_score, best = max(scored, key=lambda sg: sg[0])
        return best if best_score >= MATCH_MIN else None

    def _touch(self, g: Goal) -> None:
        g.updated_at = self._clock()
        self.save()

    def set_status(self, gid: str, status: str) -> Goal | None:
        g = self._goals.get(gid)
        if g is None:
            return None
        g.status = status
        self._touch(g)
        return g

    def resume(self, gid: str) -> Goal | None:
        return self.set_status(gid, "active")

    def record_progress(self, gid: str, note: str, next_action: str = "") -> Goal | None:
        g = self._goals.get(gid)
        if g is None:
            return None
        g.last_progress_at, g.last_progress_note = self._clock(), note.strip()
        if next_action:
            g.next_action = next_action.strip()
        self._touch(g)
        return g

    def add_milestone(self, gid: str, text: str) -> Goal | None:
        g = self._goals.get(gid)
        if g is None:
            return None
        g.milestones.append({"text": text.strip(), "done": False})
        self._touch(g)
        return g

    def complete_milestone(self, gid: str, text: str) -> Goal | None:
        g = self._goals.get(gid)
        if g is None or not g.milestones:
            return None
        scored = [(_match_score(text, m["text"]), m) for m in g.milestones]
        score, m = max(scored, key=lambda sm: sm[0])
        if score >= MATCH_MIN:
            m["done"] = True
            self._touch(g)
        return g

    def stale(self, days: float = STALE_DAYS) -> list[Goal]:
        """Active goals nobody has touched in a while: proactive.Sources can nudge on these."""
        cutoff = self._clock() - days * 86400
        return [g for g in self._goals.values() if g.status == "active"
               and (g.last_progress_at or g.created_at) < cutoff]


# -- plain-code command parsing, exactly like brain.py's is_stop()/is_other() -------------------

@dataclass
class GoalCommand:
    kind: str  # new | progress | resume | pause | done | status | list
    text: str = ""  # the free text after the trigger phrase (for "new" and "progress")


_NEW = re.compile(r"^(?:evie[,:]?\s+)?(?:new goal[,:]?|start(?: tracking)? a goal(?: to)?[,:]?|"
                  r"add a goal[,:]?|track a goal[,:]?)\s+(.+)$", re.I)
_PROGRESS = re.compile(r"^(?:evie[,:]?\s+)?(?:goal update|update (?:my|the) goal(?: on)?)[,:]?\s+(.+)$", re.I)
_LIST = re.compile(r"^(?:evie[,:]?\s+)?(?:what are my goals|list my goals|show my goals)\??$", re.I)

# resume/pause/done/status share one trap: their verbs already mean something else on their own
# ("pause" is Spotify, "mark that done" is a to-do). They only fire when the word "goal" actually
# appears, so "pause the music" and "mark that done" keep going to their existing routes untouched.
_VERB = {"resume": r"(?:resume|pick up|unpause|restart)",
         "pause": r"pause",
         "done": r"(?:mark|finish|complete|close)",
         # "how's" only matches the contraction (2026-09-26: Isaac's own "how IS my goal on X
         # going" fell through to a plain answer instead, missing the goal entirely -- caught by
         # real-Mac testing, not the offline suite, since its own fixture always said "how's").
         "status": r"(?:how'?s|how is|how are|how're|what'?s the status (?:on|of)|status (?:on|of)|"
                   r"what'?s next on)"}
_HAS_GOAL = re.compile(r"\bgoals?\b", re.I)
_LEAD_MY_THE = re.compile(r"^(?:my|the)\s+", re.I)
_LEAD_GOAL = re.compile(r"^goals?\b\s*(?:on|of|for)?\s*", re.I)
_TRAIL_GOAL = re.compile(r"\s+goals?$", re.I)
_TRAIL_DONE = re.compile(r"\s+(?:as\s+done|done)$", re.I)
_TRAIL_Q = re.compile(r"\?+$")


def _clean_ref(s: str) -> str:
    s = _LEAD_MY_THE.sub("", s.strip())
    s = _LEAD_GOAL.sub("", s)
    s = _TRAIL_GOAL.sub("", s)
    return s.strip()


def _verb_command(kind: str, text: str) -> GoalCommand | None:
    m = re.match(rf"^(?:evie[,:]?\s+)?{_VERB[kind]}\s+(.+)$", text, re.I)
    if not m or not _HAS_GOAL.search(m.group(1)):
        return None
    rest = _TRAIL_Q.sub("", m.group(1))
    if kind == "done":
        rest = _TRAIL_DONE.sub("", rest)
    ref = _clean_ref(rest)
    return GoalCommand(kind, ref) if ref else None


def parse_goal_command(text: str) -> GoalCommand | None:
    t = text.strip()
    if _LIST.match(t):
        return GoalCommand("list")
    m = _NEW.match(t)
    if m:
        return GoalCommand("new", m.group(1).strip())
    m = _PROGRESS.match(t)
    if m:
        return GoalCommand("progress", m.group(1).strip())
    for kind in ("resume", "pause", "done", "status"):
        cmd = _verb_command(kind, t)
        if cmd:
            return cmd
    return None


def format_goal(g: Goal) -> str:
    bits = [f"{g.outcome} ({g.status})"]
    if g.deadline:
        bits.append(f"due {g.deadline}")
    if g.next_action:
        bits.append(f"next: {g.next_action}")
    done = sum(1 for m in g.milestones if m["done"])
    if g.milestones:
        bits.append(f"{done}/{len(g.milestones)} milestones")
    return ", ".join(bits)
