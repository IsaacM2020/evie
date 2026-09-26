"""P2-D design: the Objective model (spec §2). "The first layer translates Isaac's natural-
language request into an objective": desired outcome, current target, constraints, risk level,
preferred workspace, interaction mode, whether Isaac should see the work, acceptable completion
condition -- deliberately different from a brittle step-by-step macro.

This composes modules already shipped rather than reinventing their logic: workspace.default_policy
decides preferred_workspace/isaac_should_see from an explicit "show me" signal in the goal text;
safety.risky_words gives a goal-level risk signal (an element-level classify() isn't available yet
at objective-construction time, before any screen has been read); world.Target is reused by
reference for current_target, never duplicated.

from_goal() is a plain function over a goal string -- NOT the "same plan call" the spec's §11
Planner section says an objective should eventually come from (that needs Planner.run() wiring
against a live model call, deferred to the Phase 2 follow-up plan alongside every other module's
wiring). This gives the type and a usable construction path today; a later session can replace
from_goal()'s regex-based risk/workspace detection with fields the SAME plan call already produces,
without changing Objective's shape.
"""
import re
from dataclasses import dataclass, field
from enum import Enum

from evie.computer.safety import risky_words
from evie.computer.workspace import DisplayPolicy, default_policy
from evie.computer.world import Target


class RiskLevel(Enum):
    LOW = "low"
    CONFIRM = "confirm"


class InteractionMode(Enum):
    AUTONOMOUS = "autonomous"
    CONFIRM = "confirm"


_SHOW_ME = re.compile(r"\b(show me|put (it|this) on my screen|let me see)\b", re.IGNORECASE)

_STANDING_CONSTRAINTS = ("don't ask unnecessary questions",
                        "don't disturb isaac's primary workspace unless he asked to see this")


@dataclass
class Objective:
    desired_outcome: str
    current_target: Target | None = None
    constraints: tuple[str, ...] = _STANDING_CONSTRAINTS
    risk: RiskLevel = RiskLevel.LOW
    preferred_workspace: DisplayPolicy = DisplayPolicy.EVIE_PRIVATE
    interaction_mode: InteractionMode = InteractionMode.AUTONOMOUS
    isaac_should_see: bool = False
    completion_condition: str = ""


def from_goal(goal: str, target: Target | None = None) -> Objective:
    """Builds an Objective from Isaac's raw goal text (and an already-resolved Target, if one
    exists) using the same signals the rest of the shipped modules already use -- not a new
    natural-language layer."""
    show_me = bool(_SHOW_ME.search(goal))
    policy = default_policy(explicit_show_me=show_me, explicit_observe=False)
    risky = risky_words(goal)
    return Objective(desired_outcome=goal, current_target=target,
                     risk=RiskLevel.CONFIRM if risky else RiskLevel.LOW,
                     preferred_workspace=policy, isaac_should_see=show_me,
                     interaction_mode=InteractionMode.CONFIRM if risky else InteractionMode.AUTONOMOUS)
