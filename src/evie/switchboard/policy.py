"""Plain code that turns Jev's probabilities into what Evie does. No model in here.

Keeping this separate from Jev means the safety rules (only Isaac commands, unsure means ask)
can't be talked out of by any input, and every threshold is one number the evals can tune.
"""
from dataclasses import dataclass
from enum import Enum

from evie.switchboard.decision import Decision


class Action(str, Enum):
    ACT = "act"
    CLARIFY = "clarify"
    IGNORE = "ignore"


@dataclass(frozen=True)
class Thresholds:
    ignore_below: float = 0.50
    act_at: float = 0.70
    answer_act_at: float = 0.60
    unknown_speaker_penalty: float = 0.10
    route_conf_min: float = 0.60
    incomplete_below: float = 0.40
    event_at: float = 0.80


@dataclass(frozen=True)
class Verdict:
    action: Action
    reason: str
    followup: bool = False


NEEDS_DETAIL = {"quick_action", "deep_job", "remember"}


def decide(d: Decision, speaker: str, t: Thresholds = Thresholds()) -> Verdict:
    followup = d.has_event >= t.event_at
    if speaker == "other":
        return Verdict(Action.IGNORE, "not Isaac's voice", followup)
    if d.route == "not_for_evie" or d.for_evie < t.ignore_below:
        return Verdict(Action.IGNORE, "not for Evie", followup)
    bar = t.answer_act_at if d.route == "answer" else t.act_at
    if speaker == "unknown":
        bar += t.unknown_speaker_penalty
    if d.for_evie < bar or d.route_confidence < t.route_conf_min:
        return Verdict(Action.CLARIFY, "unsure it was for me")
    if d.route in NEEDS_DETAIL and d.complete < t.incomplete_below:
        return Verdict(Action.CLARIFY, "missing detail")
    return Verdict(Action.ACT, d.route)
