"""Phase Computer Use V2, P1-B: which display a computer-use task works on, and whether Isaac's
own workspace may be touched. Spec §6-7: the MacBook display is normally Evie's own workspace
(she can research, sort files, open apps there without disturbing Isaac); the external monitor
is normally his. This is a policy, not a hardware assumption -- with only one display connected,
there's no split at all (assign_display returns None: single-display mode).

Deliberately just the decision logic here. The Swift-side window move/resize/focus that would
let Evie actually put a window on the display this picks needs a new Swift op and a real
multi-display Mac to build and test against -- left for a follow-on session (see
docs/superpowers/plans/2026-09-26-computer-use-v2.md, Deferred Work).
"""
from enum import Enum

from evie.computer.state import Display


class DisplayPolicy(Enum):
    EVIE_PRIVATE = "evie_private"      # default for autonomous work: her own display
    ISAAC_VISIBLE = "isaac_visible"    # "show me this" / "put this on my screen"
    OBSERVE_ISAAC = "observe_isaac"    # may read Isaac's display; never implies permission to act on it
    SHARED = "shared"                  # genuinely working together


def assign_display(policy: DisplayPolicy, displays: list[Display]) -> str | None:
    """The Display.id a task with this policy should use, or None when there's only one display
    (single-display mode: no Evie/Isaac split to make)."""
    if len(displays) < 2:
        return None
    builtin = next((d for d in displays if d.builtin), None)
    external = next((d for d in displays if not d.builtin), None)
    if policy == DisplayPolicy.EVIE_PRIVATE:
        return builtin.id if builtin else None
    if policy in (DisplayPolicy.ISAAC_VISIBLE, DisplayPolicy.OBSERVE_ISAAC):
        return external.id if external else None
    return None  # SHARED: no single display to hand back


def default_policy(explicit_show_me: bool, explicit_observe: bool) -> DisplayPolicy:
    """Isaac, 2026-09-26: 'show me' always wins if both signals somehow fire at once -- an
    explicit ask to see something outranks a passive observation need."""
    if explicit_show_me:
        return DisplayPolicy.ISAAC_VISIBLE
    if explicit_observe:
        return DisplayPolicy.OBSERVE_ISAAC
    return DisplayPolicy.EVIE_PRIVATE
