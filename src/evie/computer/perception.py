"""P1-C design: the perception router's decision only (spec §4-5). "What is the cheapest source
of information that can resolve the current uncertainty?" -- structured reading (the existing
Screen/AX/DOM path) is preferred; vision (the existing Planner._look_for/marked_shot path) is a
capability invoked deliberately, never the default; filesystem-only goals need no screen read at
all.

This makes explicit and independently testable a decision planner.py already made implicitly
(VISION_BELOW's "fewer than 5 labelled elements on a non-web screen -> look at it instead",
planner.py:373) plus the filesystem-only short-circuit the spec calls out that nothing currently
implements. It does NOT add a new perception source, a local-OCR op, or a cloud-vision budget --
those (§4 Level 2/3, "max 2 cloud vision calls per task, logged") need the broker's actual
call-counting/budget state wired against a live Mac and are left for a follow-on session once
this routing logic is itself wired into Planner (see the plan's Deferred Work).
"""
import re
from enum import Enum

from evie.computer.observe import Screen

VISION_BELOW = 5  # matches planner.py's existing VISION_BELOW: fewer labelled elements than this on
                  # a non-web screen means structured reading has too little to work with


class PerceptionSource(Enum):
    FILESYSTEM = "filesystem"          # no screen read needed at all
    STRUCTURED = "structured"          # the existing AX/DOM Screen read
    TARGETED_VISION = "targeted_vision"  # spec §4 Level 2: answer ONE specific visual question
    VISION = "vision"                  # spec §4 Level 3: full enumeration (_look_for/marked_shot)


_FILESYSTEM_WORDS = re.compile(r"\b(move|copy|rename|delete|trash|file|folder|directory|desktop|downloads)\b",
                               re.IGNORECASE)
# A goal ASKING about visual content needs ONE targeted answer -- Level 2, never the full
# numbered-box enumeration Level 3 exists for (that's for FINDING a pressable element, not
# answering a question about what's drawn). Includes the spec's own §4 worked examples ("where is
# the Play button", "what does question 7 say", "what number is written in this diagram") --
# _find's own choose_source check only special-cases PerceptionSource.VISION exactly, so a
# TARGETED_VISION result never diverts its pressable-element candidate matching/Jev-choice/
# _look_for fallback, even for a goal phrased like "where is X".
_VISUAL_WORDS = re.compile(r"\b(diagram|graph|chart|picture|image|photo|drawing|figure|screenshot|"
                           r"what does this .*(mean|show|say)|explain this|where is the .+ (button|icon|control)|"
                           r"what does question \d+ say|what number is written)\b", re.IGNORECASE)


def choose_source(goal: str, screen: Screen | None) -> PerceptionSource:
    """The cheapest perception source that can plausibly resolve this goal, given what's already
    been read (or None, before any screen read has happened)."""
    if _VISUAL_WORDS.search(goal):
        return PerceptionSource.TARGETED_VISION
    if screen is None:
        if _FILESYSTEM_WORDS.search(goal):
            return PerceptionSource.FILESYSTEM
        return PerceptionSource.STRUCTURED
    if screen.kind != "web":
        labelled = [e for e in screen.elements if e.get("label")]
        if len(labelled) < VISION_BELOW:
            return PerceptionSource.VISION
    return PerceptionSource.STRUCTURED
