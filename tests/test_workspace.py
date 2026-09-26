from evie.computer.state import Display
from evie.computer.workspace import DisplayPolicy, assign_display, default_policy


def _two_displays():
    return [Display("builtin", True, (0, 0, 1470, 956)), Display("external-1", False, (1470, 0, 2560, 1440))]


def test_evie_private_uses_the_builtin_display_when_two_are_connected():
    assert assign_display(DisplayPolicy.EVIE_PRIVATE, _two_displays()) == "builtin"


def test_isaac_visible_uses_the_external_display_when_two_are_connected():
    assert assign_display(DisplayPolicy.ISAAC_VISIBLE, _two_displays()) == "external-1"


def test_single_display_mode_returns_none_for_either_policy():
    """spec §6: 'If the external monitor is disconnected: single-display mode' -- there is no
    separate Evie/Isaac split any more, so assign_display can't hand back a real second display."""
    one = [Display("builtin", True, (0, 0, 1470, 956))]
    assert assign_display(DisplayPolicy.EVIE_PRIVATE, one) is None
    assert assign_display(DisplayPolicy.ISAAC_VISIBLE, one) is None


def test_observe_isaac_targets_the_external_display_like_isaac_visible_but_is_a_distinct_policy():
    """Observation reads Isaac's display; it must never be confused with permission to act there
    (spec §7: 'observation does not imply permission to manipulate it') -- this test only pins
    which display it points at; Planner-side enforcement that OBSERVE_ISAAC never issues a write
    action is out of scope for this data-model task (see Deferred Work)."""
    assert assign_display(DisplayPolicy.OBSERVE_ISAAC, _two_displays()) == "external-1"


def test_default_policy_is_evie_private_for_ordinary_autonomous_work():
    assert default_policy(explicit_show_me=False, explicit_observe=False) == DisplayPolicy.EVIE_PRIVATE


def test_default_policy_is_isaac_visible_when_he_says_show_me():
    assert default_policy(explicit_show_me=True, explicit_observe=False) == DisplayPolicy.ISAAC_VISIBLE


def test_default_policy_is_observe_isaac_when_only_observation_is_needed():
    assert default_policy(explicit_show_me=False, explicit_observe=True) == DisplayPolicy.OBSERVE_ISAAC


def test_show_me_wins_over_observe_if_somehow_both_are_set():
    assert default_policy(explicit_show_me=True, explicit_observe=True) == DisplayPolicy.ISAAC_VISIBLE
