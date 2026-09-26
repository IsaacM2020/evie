"""P1-C design: the perception router's DECISION only (spec §4-5: "what is the cheapest source
of information that can resolve the current uncertainty?"). This does not implement a new
perception source -- structured reading (Screen) and vision (_look_for/marked_shot) already
exist and are unchanged; this module is the routing logic planner.py's VISION_BELOW check already
did implicitly, made explicit and testable, plus the filesystem-only case the spec calls out that
nothing currently short-circuits on."""
from evie.computer.observe import Screen
from evie.computer.perception import PerceptionSource, choose_source


def _screen(n_labelled: int, web: bool = False) -> Screen:
    els = [{"id": f"e{i}", "label": f"item {i}", "role": "button"} for i in range(n_labelled)]
    return Screen(snapshot="s1", app="Finder", kind="web" if web else "app", elements=els)


def test_a_filesystem_only_goal_needs_no_screen_read_at_all():
    """spec §5: "Move this file into my Physics folder." Needed: filesystem state. No screenshot."""
    assert choose_source("move this file into my physics folder", screen=None) == PerceptionSource.FILESYSTEM


def test_a_well_labelled_screen_uses_structured_reading():
    assert choose_source("find the button that submits this form", screen=_screen(20)) == PerceptionSource.STRUCTURED


def test_a_sparsely_labelled_native_app_screen_falls_back_to_vision():
    """Matches planner.py's existing VISION_BELOW=5 threshold for non-web screens -- this makes
    that same decision explicit and independently testable, not a new threshold."""
    assert choose_source("find the play button", screen=_screen(3)) == PerceptionSource.VISION


def test_a_sparsely_labelled_web_screen_still_prefers_structured():
    """planner.py's _find only falls back to vision for non-web screens (self._web() check) --
    a web page's DOM is trusted even when few elements are labelled, since Jev's choice among the
    real candidates is still cheaper than a screenshot."""
    assert choose_source("find the play button", screen=_screen(3, web=True)) == PerceptionSource.STRUCTURED


def test_an_explicitly_visual_goal_goes_straight_to_vision_even_with_a_well_labelled_screen():
    """spec §5: "What does this Physics diagram mean?" Needed: visual perception -- regardless of
    how many labelled elements happen to be on screen, understanding a diagram's content needs
    genuine visual understanding, not element-finding."""
    assert choose_source("what does this diagram mean", screen=_screen(50)) == PerceptionSource.VISION
    assert choose_source("explain this graph to me", screen=_screen(50)) == PerceptionSource.VISION


def test_no_screen_and_no_filesystem_hint_defaults_to_structured():
    """A goal with no filesystem words and no screen read yet (the very first look) can't be
    routed to FILESYSTEM or VISION on guesswork -- STRUCTURED is the safe, cheap default that a
    fresh _look() satisfies."""
    assert choose_source("open my email", screen=None) == PerceptionSource.STRUCTURED
