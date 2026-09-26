"""Phase 6 gap-fill: natural voice control over the job supervisor's background jobs (pause,
resume, stop a specific one), parsed the same deterministic way as evie.goals — plain code, never
Jev, so the tuned switchboard is untouched (see evals/TUNING.md). The single foreground job
already has its own voice surface (brain._job_control, Jev's JOB_OP_Q); this is additive, for the
background jobs that surface can't reach.

Parsing (this file) never looks at which jobs exist; resolving a reference ("it", "job two") to
an actual Job is brain.py's job, exactly like evie.goals splits parse_goal_command from
GoalStore.find_by_text.
"""
import re
from dataclasses import dataclass

PAUSABLE = {"running", "queued", "blocked"}
RESUMABLE = {"paused"}
STOPPABLE = PAUSABLE | RESUMABLE  # every non-terminal Job.status

# pause/stop always need an explicit "job"/"task" word, exactly how evie.goals guards its verbs
# with _HAS_GOAL — "pause the music" and the existing is_stop() phrases must never be touched.
# resume/continue also accept a bare "it"/"that": nothing else in Evie's fast skills means
# "resume" or "continue" on its own, and brain.py only acts on a bare one when it's unambiguous.
_JOB_VERB = {"pause": r"pause",
            "stop": r"(?:stop|cancel)",
            "resume": r"(?:resume|continue|unpause)"}
_HAS_JOB = re.compile(r"\b(?:job|task)\b", re.I)
_BARE_ONLY = {"it", "that", "this", "that one", "this one"}
_ORDINAL_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                  "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}
_ORDINAL_RE = re.compile(r"\bjob\s+(?:number\s+)?(\d+|" + "|".join(_ORDINAL_WORDS) + r")\b", re.I)
_NUM_ANY = re.compile(r"\b(\d+|" + "|".join(_ORDINAL_WORDS) + r")\b", re.I)
_TRAIL_Q = re.compile(r"\?+$")


@dataclass
class JobCommand:
    kind: str  # pause | resume | stop
    ordinal: int | None = None  # "job two" -> 2, 1-indexed by when each background job started
    bare: bool = False  # "resume it" / "continue that": brain.py only acts on this when unambiguous
    text: str = ""  # the full wake-stripped utterance (brain.py checks it for "background")


def _ordinal_from(raw: str) -> int | None:
    return int(raw) if raw.isdigit() else _ORDINAL_WORDS.get(raw)


def extract_ordinal(text: str) -> int | None:
    """For answering Evie's own "which one?" clarification: the first number or number-word."""
    m = _NUM_ANY.search(text.lower())
    return _ordinal_from(m.group(1)) if m else None


def _job_verb_command(kind: str, text: str) -> JobCommand | None:
    text = _TRAIL_Q.sub("", text.strip())
    m = re.match(rf"^(?:evie[,:]?\s+)?{_JOB_VERB[kind]}\s+(.+)$", text, re.I)
    if not m:
        return None
    rest = m.group(1).strip()
    if _HAS_JOB.search(rest):
        om = _ORDINAL_RE.search(rest)
        return JobCommand(kind, ordinal=_ordinal_from(om.group(1)) if om else None, text=text)
    if kind == "resume" and rest.lower() in _BARE_ONLY:
        return JobCommand(kind, bare=True, text=text)
    return None  # "pause the music", "stop talking", "cancel that": someone else's job


def parse_job_command(text: str) -> JobCommand | None:
    t = text.strip()
    for kind in ("pause", "stop", "resume"):
        cmd = _job_verb_command(kind, t)
        if cmd:
            return cmd
    return None
