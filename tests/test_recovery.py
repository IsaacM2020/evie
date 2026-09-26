"""P2-E design: failure classification + recovery strategy (spec §13). Today every _Fail carries
only a free-text message and Planner.run()'s recovery is uniform regardless of cause (replan, up
to MAX_REPLANS, then stuck). This module classifies a failure's text into one of the spec's named
classes and looks up the strategy the spec assigns each one -- it does NOT change Planner.run()'s
actual recovery loop yet (that's live-code wiring, deferred to the Phase 2 follow-up plan, same as
perception.py/task.py/safety.py before it)."""
from evie.computer.recovery import FailureClass, RecoveryStrategy, classify_failure, strategy_for


def test_an_empty_plan_is_unknown():
    assert classify_failure("the plan was empty") == FailureClass.UNKNOWN


def test_a_page_still_loading_is_loading():
    assert classify_failure("expected the address to contain 'gmail', it's 'about:blank'") == FailureClass.LOADING


def test_a_missing_target_on_screen_is_missing_target():
    assert classify_failure("none of these are 'Daryl'") == FailureClass.MISSING_TARGET
    assert classify_failure("nothing on screen looks like 'Search'") == FailureClass.MISSING_TARGET


def test_a_stale_snapshot_press_is_stale_state():
    assert classify_failure("that one isn't on screen any more") == FailureClass.STALE_STATE


def test_an_ambiguous_choice_is_ambiguity():
    assert classify_failure("find Search: not sure which one") == FailureClass.AMBIGUITY


def test_a_hands_unreachable_failure_is_unreachable():
    assert classify_failure("can't reach my hands, is the Evie app running?") == FailureClass.UNREACHABLE


def test_an_unrecognized_message_is_unknown():
    assert classify_failure("something completely unexpected happened") == FailureClass.UNKNOWN


def test_loading_strategy_is_wait_then_verify():
    assert strategy_for(FailureClass.LOADING) == RecoveryStrategy.WAIT_AND_VERIFY


def test_missing_target_strategy_is_refresh_and_ask_not_replan_forever():
    """spec §13: MISSING_TARGET isn't in the example table, but the P0 #1 fix already means "none
    of these" should surface as a question, not endless replanning -- refresh perception once,
    then ask, matching the ACCESSIBILITY_EMPTY pattern's shape (alternate source, then escalate)."""
    assert strategy_for(FailureClass.MISSING_TARGET) == RecoveryStrategy.REFRESH_THEN_ASK


def test_stale_state_strategy_is_refresh_then_retry():
    assert strategy_for(FailureClass.STALE_STATE) == RecoveryStrategy.REFRESH_AND_RETRY


def test_unreachable_strategy_is_wait_then_verify_not_replan():
    """hands.UNREACHABLE ("is the Evie app running?") is never fixed by asking a model to plan
    again -- replanning burns a model call for a problem no plan can solve. Wait and retry the
    same action once the app reconnects."""
    assert strategy_for(FailureClass.UNREACHABLE) == RecoveryStrategy.WAIT_AND_VERIFY


def test_unknown_strategy_is_stronger_perception_then_replan():
    assert strategy_for(FailureClass.UNKNOWN) == RecoveryStrategy.STRONGER_PERCEPTION_THEN_REPLAN


def test_every_failure_class_has_a_strategy():
    """No silent gap: every class the spec names (or this module adds) must resolve to some
    strategy, never None or a lookup error."""
    for cls in FailureClass:
        assert strategy_for(cls) is not None
