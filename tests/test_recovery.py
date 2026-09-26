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


def test_the_real_swift_stale_snapshot_rejection_is_stale_state():
    """Eyes.swift's actual press/setText guard (snap == snapshot) rejects with 'the screen
    changed, look again' -- NOT 'isn't on screen any more' (that text only comes from
    planner.py's choose()/_find_rows path). Before this fix a real live stale-snapshot press
    classified as UNKNOWN, so it never got REFRESH_AND_RETRY at all."""
    assert classify_failure("press 'Delete': the screen changed, look again") == FailureClass.STALE_STATE
    assert classify_failure("set_text 'Search': the screen changed, look again") == FailureClass.STALE_STATE


def test_a_vanished_web_element_is_stale_state():
    """Eyes.swift press(): a web element that no longer exists by the time the click runs
    ('that button's gone' / 'that field's gone') is the same staleness as a rejected snapshot,
    not a target that was simply never there (MISSING_TARGET is for _find failing to match
    anything on the CURRENT screen at all)."""
    assert classify_failure("press 'Submit': that button's gone") == FailureClass.STALE_STATE
    assert classify_failure("set_text 'Email': that field's gone") == FailureClass.STALE_STATE


def test_an_action_rejected_by_the_os_is_action_rejected():
    """Eyes.swift press(): AXUIElementPerformAction failing on an element that's still there
    ('it didn't respond') is the OS refusing the action, not staleness or a missing target."""
    assert classify_failure("press 'Save': it didn't respond") == FailureClass.ACTION_REJECTED


def test_verifier_check_app_front_failure_is_wrong_window():
    """verifier.check_app_front's real live failure text ('expected X to be frontmost, it's Y') --
    wired into _expect_problem this session (P2-D) -- was previously unmatched by any pattern
    (-> UNKNOWN), so an app_front expect step never got the WRONG_WINDOW/REFOCUS_AND_VERIFY
    treatment the spec's own failure-class table names it for."""
    assert classify_failure("expected 'Mail' to be frontmost, it's 'Safari'") == FailureClass.WRONG_WINDOW


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
