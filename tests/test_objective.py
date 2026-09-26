"""P2-D design: the Objective model (spec §2). "The first layer translates Isaac's
natural-language request into an objective" -- desired outcome, current target, constraints, risk,
preferred workspace, interaction mode, whether Isaac should see the work, acceptable completion
condition. This composes the modules already shipped rather than reinventing their logic:
workspace.default_policy for preferred workspace, safety.classify for risk (given the action a
plan is about to take), world.Target for current target (reused, not duplicated).

Building an Objective from a goal string is NOT the same plan call the spec's §11 Planner
description says it should eventually come from ("from the SAME plan call" -- the Phase 2
follow-up plan's own header notes this needs Planner.run() wiring, deferred). This module gives
the Objective type and a construction function usable with data the caller already has (a goal
string, an optional Target, an optional element+op for risk classification) -- not a new model
call."""
from evie.computer.objective import InteractionMode, Objective, RiskLevel, from_goal
from evie.computer.workspace import DisplayPolicy
from evie.computer.world import Target


def test_a_plain_goal_with_no_target_yet_defaults_to_low_risk_and_evie_private():
    obj = from_goal("open netflix and play the mentalist")
    assert obj.desired_outcome == "open netflix and play the mentalist"
    assert obj.risk == RiskLevel.LOW
    assert obj.preferred_workspace == DisplayPolicy.EVIE_PRIVATE
    assert obj.isaac_should_see is False


def test_show_me_in_the_goal_sets_isaac_visible_and_show_me():
    obj = from_goal("open netflix and show me the mentalist")
    assert obj.preferred_workspace == DisplayPolicy.ISAAC_VISIBLE
    assert obj.isaac_should_see is True


def test_a_goal_naming_a_risky_action_is_high_risk():
    """desired outcome alone can't always tell risk -- but a goal that names the risky action
    directly (spec's own risky-word vocabulary, safety.classify) should be reflected."""
    obj = from_goal("send the message to mom")
    assert obj.risk == RiskLevel.CONFIRM


def test_a_current_target_is_carried_through_unchanged():
    t = Target("app", "Safari", why="he named Safari")
    obj = from_goal("switch to safari", target=t)
    assert obj.current_target is t


def test_interaction_mode_defaults_to_autonomous_for_a_low_risk_goal():
    obj = from_goal("clean up my desktop")
    assert obj.interaction_mode == InteractionMode.AUTONOMOUS


def test_interaction_mode_is_confirm_for_a_risky_goal():
    obj = from_goal("delete these files")
    assert obj.interaction_mode == InteractionMode.CONFIRM


def test_constraints_always_include_the_two_standing_rules():
    """spec's own example objective lists these two constraints for every task, not just some --
    "do not ask unnecessary questions" and "do not disturb isaac's primary workspace" are Evie's
    standing behavior, not goal-specific."""
    obj = from_goal("open netflix")
    assert "don't ask unnecessary questions" in obj.constraints
    assert "don't disturb isaac's primary workspace unless he asked to see this" in obj.constraints
