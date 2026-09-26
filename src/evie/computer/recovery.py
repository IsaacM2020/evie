"""P2-E design: failure classification + recovery strategy (spec §13). "Failure should trigger
adaptation, not surrender." Today every _Fail (planner.py) carries only a free-text message, and
Planner.run()'s actual recovery is uniform regardless of cause: replan (up to MAX_REPLANS), then
stuck. This classifies a failure's text into the spec's named classes and looks up each class's
strategy -- TIMEOUT is deliberately split from UNREACHABLE (hands.py's UNREACHABLE constant: "is
the Evie app running?" is never fixed by replanning, since no plan can fix a disconnected app; a
genuine step timeout is a different, retriable problem).

This is the classification and lookup only. It does not change Planner.run()'s actual recovery
loop -- wiring a per-class strategy into the live replan/stuck decision is a behavior change to
code currently running Isaac's assistant and is left for the Phase 2 follow-up plan, same as
perception.py/task.py/safety.py before it.
"""
import re
from enum import Enum


class FailureClass(Enum):
    STALE_STATE = "stale_state"              # a snapshot/element no longer exists
    MISSING_TARGET = "missing_target"        # the named thing isn't on screen at all
    WRONG_WINDOW = "wrong_window"             # focus/front app isn't what's expected
    WRONG_DISPLAY = "wrong_display"           # right window, wrong screen
    LOADING = "loading"                       # the page/app hasn't finished appearing yet
    UNEXPECTED_DIALOG = "unexpected_dialog"   # a native alert/sheet interrupted the flow
    AMBIGUITY = "ambiguity"                   # more than one plausible match, no clear winner
    ACCESSIBILITY_EMPTY = "accessibility_empty"  # AX/DOM reported nothing usable
    VISUAL_ONLY = "visual_only"               # only a screenshot can answer this
    ACTION_REJECTED = "action_rejected"       # the app/OS refused the action
    UNREACHABLE = "unreachable"                # hands.py: the Evie app itself isn't responding
    TIMEOUT = "timeout"                       # a step ran out of time, app IS reachable
    STATE_MISMATCH = "state_mismatch"          # the world looks different from what the plan expected
    UNKNOWN = "unknown"


class RecoveryStrategy(Enum):
    WAIT_AND_VERIFY = "wait_and_verify"
    REFRESH_AND_RETRY = "refresh_and_retry"
    REFOCUS_AND_VERIFY = "refocus_and_verify"
    ALTERNATE_SOURCE_THEN_VISION = "alternate_source_then_vision"
    REFRESH_THEN_ASK = "refresh_then_ask"
    INSPECT_AND_REPLAN = "inspect_and_replan"
    STRONGER_PERCEPTION_THEN_REPLAN = "stronger_perception_then_replan"


_PATTERNS: list[tuple[re.Pattern, FailureClass]] = [
    (re.compile(r"isn't on screen any more", re.I), FailureClass.STALE_STATE),
    (re.compile(r"is the evie app running", re.I), FailureClass.UNREACHABLE),
    (re.compile(r"none of these are|nothing on screen looks like", re.I), FailureClass.MISSING_TARGET),
    (re.compile(r"not sure which one", re.I), FailureClass.AMBIGUITY),
    (re.compile(r"expected the address to contain|expected to see", re.I), FailureClass.LOADING),
    (re.compile(r"couldn't see .* \(", re.I), FailureClass.ACCESSIBILITY_EMPTY),
    (re.compile(r"isn't in the screenshot", re.I), FailureClass.VISUAL_ONLY),
]


def classify_failure(message: str) -> FailureClass:
    """The FailureClass a _Fail's message text most likely represents. Best-effort pattern
    matching over today's free-text messages (planner.py doesn't yet raise a typed failure) --
    matches least-ambiguous patterns first; falls back to UNKNOWN rather than guessing."""
    for pattern, cls in _PATTERNS:
        if pattern.search(message or ""):
            return cls
    return FailureClass.UNKNOWN


_STRATEGY: dict[FailureClass, RecoveryStrategy] = {
    FailureClass.STALE_STATE: RecoveryStrategy.REFRESH_AND_RETRY,
    FailureClass.LOADING: RecoveryStrategy.WAIT_AND_VERIFY,
    FailureClass.WRONG_WINDOW: RecoveryStrategy.REFOCUS_AND_VERIFY,
    FailureClass.WRONG_DISPLAY: RecoveryStrategy.REFOCUS_AND_VERIFY,
    FailureClass.ACCESSIBILITY_EMPTY: RecoveryStrategy.ALTERNATE_SOURCE_THEN_VISION,
    FailureClass.VISUAL_ONLY: RecoveryStrategy.ALTERNATE_SOURCE_THEN_VISION,
    FailureClass.MISSING_TARGET: RecoveryStrategy.REFRESH_THEN_ASK,
    FailureClass.AMBIGUITY: RecoveryStrategy.REFRESH_THEN_ASK,
    FailureClass.STATE_MISMATCH: RecoveryStrategy.INSPECT_AND_REPLAN,
    FailureClass.UNEXPECTED_DIALOG: RecoveryStrategy.INSPECT_AND_REPLAN,
    FailureClass.ACTION_REJECTED: RecoveryStrategy.INSPECT_AND_REPLAN,
    # A timed-out step's app IS reachable (unlike UNREACHABLE) -- worth one retry before replanning.
    FailureClass.TIMEOUT: RecoveryStrategy.WAIT_AND_VERIFY,
    # hands.UNREACHABLE: no plan fixes a disconnected app -- wait for it to come back, don't burn
    # a model call on a problem no plan can solve.
    FailureClass.UNREACHABLE: RecoveryStrategy.WAIT_AND_VERIFY,
    FailureClass.UNKNOWN: RecoveryStrategy.STRONGER_PERCEPTION_THEN_REPLAN,
}


def strategy_for(cls: FailureClass) -> RecoveryStrategy:
    return _STRATEGY[cls]
