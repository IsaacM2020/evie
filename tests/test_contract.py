"""P2/completion pass: typed action contracts (spec §9). Every meaningful computer action gets a
structured contract (operation, target, originating snapshot, preconditions, postcondition, effect,
reversibility) instead of an implicit dict-in, HandsResult-out call. build_contract composes the
same predicates _act already calls (safety.classify) rather than re-deriving them; validate_precondition
mirrors _act's own staleness concern (a target from snapshot S41 must never be pressed against S42);
validate_postcondition is the executor's own "a click succeeding isn't proof" check, satisfied by
HandsResult.ok plus an optional named verifier check the step names.
"""
from evie.computer.contract import (ActionContract, build_contract, validate_postcondition,
                                    validate_precondition)
from evie.computer.observe import Screen
from evie.computer.safety import EffectClass
from evie.hands import HandsResult

SCREEN = Screen(snapshot="s1", app="Notion", kind="app",
                elements=[{"id": "a1", "role": "button", "label": "New"},
                         {"id": "a2", "role": "button", "label": "Delete"}])


def test_build_contract_captures_operation_target_and_snapshot():
    c = build_contract("press", SCREEN.get("a1"), SCREEN)
    assert c.operation == "press"
    assert c.target["id"] == "a1"
    assert c.snapshot == "s1"


def test_build_contract_classifies_effect_and_reversibility():
    c = build_contract("press", SCREEN.get("a2"), SCREEN)  # "Delete"
    assert c.effect == EffectClass.DESTRUCTIVE
    assert c.reversible is False


def test_build_contract_an_unclassified_press_is_still_marked_reversible():
    """safety.classify() has no positive signal for a plain press with no risk-shaped word in its
    label (unlike set_text, which defaults to REVERSIBLE) -- it falls to READ, which
    build_contract's own reversible mapping (READ or REVERSIBLE) still treats as safe/reversible."""
    c = build_contract("press", SCREEN.get("a1"), SCREEN)  # "New" -- no destructive/send/financial word
    assert c.effect == EffectClass.READ
    assert c.reversible is True


def test_build_contract_records_a_precondition_and_no_postcondition_by_default():
    c = build_contract("press", SCREEN.get("a1"), SCREEN)
    assert any("on screen" in p for p in c.preconditions)
    assert c.postcondition == ""


def test_build_contract_can_carry_an_explicit_postcondition():
    c = build_contract("press", SCREEN.get("a1"), SCREEN, postcondition="element 'Created' appears")
    assert c.postcondition == "element 'Created' appears"


def test_precondition_passes_when_target_is_on_the_matching_snapshot():
    c = build_contract("press", SCREEN.get("a1"), SCREEN)
    ok, why = validate_precondition(c, SCREEN)
    assert ok and why == ""


def test_precondition_rejects_a_stale_snapshot():
    """The contract was built from s1; the live screen has moved to s2 -- a target from an old
    snapshot must never be blindly used (spec Law 4/§3)."""
    c = build_contract("press", SCREEN.get("a1"), SCREEN)
    fresher = Screen(snapshot="s2", app="Notion", kind="app", elements=SCREEN.elements)
    ok, why = validate_precondition(c, fresher)
    assert not ok and "snapshot" in why.lower()


def test_precondition_rejects_a_target_that_vanished_even_on_the_same_snapshot():
    """A defensive second check: even if the snapshot id somehow matches, the id must still
    resolve to a real element right now (belt and suspenders against a caller reusing a contract
    after mutating the screen object in place)."""
    c = build_contract("press", SCREEN.get("a1"), SCREEN)
    gone = Screen(snapshot="s1", app="Notion", kind="app", elements=[SCREEN.get("a2")])
    ok, why = validate_precondition(c, gone)
    assert not ok and "a1" in why


def test_postcondition_passes_on_a_successful_result_with_no_named_check():
    c = build_contract("press", SCREEN.get("a1"), SCREEN)
    ok, why = validate_postcondition(c, HandsResult(True, "pressed"), SCREEN)
    assert ok and why == ""


def test_postcondition_fails_when_the_op_itself_failed():
    c = build_contract("press", SCREEN.get("a1"), SCREEN)
    ok, why = validate_postcondition(c, HandsResult(False, "it didn't respond"), SCREEN)
    assert not ok and "didn't respond" in why


def test_postcondition_with_a_named_element_check_is_validated_against_the_new_screen():
    c = build_contract("press", SCREEN.get("a1"), SCREEN, postcondition="element:Created")
    after = Screen(snapshot="s2", app="Notion", kind="app",
                   elements=[{"id": "b1", "role": "text", "label": "Created"}])
    ok, why = validate_postcondition(c, HandsResult(True, "pressed"), after)
    assert ok and why == ""


def test_postcondition_with_a_named_element_check_fails_when_absent():
    c = build_contract("press", SCREEN.get("a1"), SCREEN, postcondition="element:Created")
    after = Screen(snapshot="s2", app="Notion", kind="app", elements=[{"id": "b1", "role": "text", "label": "Nope"}])
    ok, why = validate_postcondition(c, HandsResult(True, "pressed"), after)
    assert not ok and "created" in why.lower()


def test_contract_is_a_plain_dataclass_with_the_spec_fields():
    c = ActionContract(operation="press", target={"id": "a1"}, snapshot="s1",
                       preconditions=("a1 on screen s1",), postcondition="", effect=EffectClass.REVERSIBLE,
                       reversible=True)
    assert c.operation == "press" and c.reversible is True
