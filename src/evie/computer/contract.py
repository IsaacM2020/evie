"""Typed action contracts (spec §9). Every meaningful computer action (press, set_text) gets a
structured contract instead of an implicit "dict in, HandsResult out" call: operation, target,
the perception snapshot it was resolved against, preconditions, an expected postcondition,
effect/risk classification, and whether it's reversible.

This is additive normalization over what _act already does, not a new step schema -- planner.py's
`steps` (what a plan call produces) are untouched; build_contract composes the exact same
safety.classify() call _act already makes, and validate_precondition mirrors _act's own staleness
concern (Screen.snapshot). The executor shape becomes:

    fresh state -> contract validation (precondition) -> safety -> act -> postcondition -> recover

postcondition is a plain string, either empty (no named check: HandsResult.ok is the only proof
available for this class of action) or "element:<label>" (checked against a screen read AFTER the
action, via the same find-then-substring logic verifier.check_element already uses) -- deliberately
the smallest useful postcondition language, not a new DSL. A step wanting a real completion check
already has `expect` for that (verifier.py); this is the per-action guard, not a replacement.
"""
from dataclasses import dataclass

from evie.computer.find import find_in_code
from evie.computer.observe import Screen
from evie.computer.safety import EffectClass, classify, is_risky
from evie.hands import HandsResult


@dataclass(frozen=True)
class ActionContract:
    operation: str
    target: dict
    snapshot: str
    preconditions: tuple[str, ...]
    postcondition: str
    effect: EffectClass
    reversible: bool


def build_contract(op: str, el: dict, screen: Screen, text: str = "", flagged: bool = False,
                   screen_has_password: bool = False, postcondition: str = "") -> ActionContract:
    """Builds a contract from exactly what _act already has at the point it's about to act --
    no new perception, no new model call."""
    effect = classify(op, el, text, flagged=flagged, screen_has_password=screen_has_password)
    reversible = effect in (EffectClass.READ, EffectClass.REVERSIBLE)
    precond = (f"{el.get('id')!r} ({el.get('label', '')!r}) is on screen at snapshot {screen.snapshot!r}",)
    return ActionContract(operation=op, target=dict(el), snapshot=screen.snapshot, preconditions=precond,
                          postcondition=postcondition, effect=effect, reversible=reversible)


def validate_precondition(contract: ActionContract, screen: Screen) -> tuple[bool, str]:
    """A target resolved against an OLD snapshot must never be blindly used (spec Law 4/§3: "A
    button discovered in snapshot S41 cannot be blindly clicked after the interface has changed to
    S42"). Checked two ways: the snapshot id itself, and (belt and suspenders) that the target's id
    still resolves to a real element on the screen being validated against."""
    eid = contract.target.get("id")
    if screen.snapshot != contract.snapshot:
        return False, (f"stale snapshot: contract was built against {contract.snapshot!r}, "
                       f"the screen is now {screen.snapshot!r} -- refresh and re-resolve the target")
    if screen.get(eid) is None:
        return False, f"{eid!r} isn't on the current screen any more"
    return True, ""


def validate_postcondition(contract: ActionContract, result: HandsResult, screen: Screen) -> tuple[bool, str]:
    """A successful click is not evidence (spec §12/§9) -- HandsResult.ok is necessary but never
    sufficient on its own when the contract names a postcondition. No named postcondition means no
    stronger claim is being made than "the OS acknowledged the action", which is exactly what
    result.ok already is."""
    if not result.ok:
        return False, result.detail
    if contract.postcondition.startswith("element:"):
        label = contract.postcondition.removeprefix("element:")
        el, _ = find_in_code(screen, label)
        if el is None and not any(label.lower() in (e.get("label") or "").lower() for e in screen.elements):
            return False, f"expected to see {label!r} after {contract.operation}, it isn't there"
    return True, ""
