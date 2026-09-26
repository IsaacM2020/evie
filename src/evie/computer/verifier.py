"""P2-E design (the other half of the verification engine, alongside recovery.py): named, typed
completion checks (spec §12: "url, element, text, app_front, window_on_display, file_exists,
media_playing; polled"). "A successful click is not evidence. A changed verified state is
evidence."

Today Planner._expect_problem (planner.py) hardcodes exactly two check kinds (url_contains,
element) into one method. This module gives each of the spec's named check kinds its own small,
independently testable predicate function, mirroring _expect_problem's existing element-matching
logic exactly (find_in_code first, falling back to a plain substring match) rather than
introducing a different one. window_on_display and media_playing need live ComputerState/media
state this environment can't produce -- their predicates take that data as a plain parameter
(caller supplies it) rather than reaching for it, so they're still fully unit-testable against
hand-built fixtures.

Does not touch Planner._expect_problem or the `expect` step's polling loop -- wiring these in as
additional `expect` step kinds is deferred to the Phase 2 follow-up plan, same as
perception.py/task.py/safety.py/recovery.py before it.
"""
from dataclasses import dataclass
from pathlib import Path

from evie.computer.find import find_in_code
from evie.computer.observe import Screen


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    detail: str  # empty when ok; a human-readable reason when not


def check_url_contains(screen: Screen, substring: str) -> CheckResult:
    if substring in (screen.url or ""):
        return CheckResult(True, "")
    return CheckResult(False, f"expected the address to contain {substring!r}, it's {screen.url!r}")


def check_element(screen: Screen, label: str) -> CheckResult:
    """Mirrors Planner._expect_problem's existing element check exactly: find_in_code first
    (a real, on-screen candidate), falling back to a plain case-insensitive substring match."""
    el, _ = find_in_code(screen, label)
    if el is not None or any(label.lower() in (e.get("label") or "").lower() for e in screen.elements):
        return CheckResult(True, "")
    return CheckResult(False, f"expected to see {label!r}")


def check_app_front(expected_app: str, front_app: str) -> CheckResult:
    if front_app == expected_app:
        return CheckResult(True, "")
    return CheckResult(False, f"expected {expected_app!r} to be frontmost, it's {front_app!r}")


def check_file_exists(path: Path) -> CheckResult:
    if path.exists():
        return CheckResult(True, "")
    return CheckResult(False, f"expected {path.name!r} to exist, it doesn't")


def check_window_on_display(app: str, expected_display_id: str, windows: list[dict]) -> CheckResult:
    """windows: [{"app": str, "display": str, ...}, ...] -- the same shape ComputerState.windows
    would give once wired in (Task 2 of the Phase 2 follow-up plan). Takes it as a plain parameter
    since this environment has no live ComputerState read to pull it from."""
    match = next((w for w in windows if w.get("app") == app), None)
    if match is None:
        return CheckResult(False, f"no window found for {app!r}")
    if match.get("display") == expected_display_id:
        return CheckResult(True, "")
    return CheckResult(False, f"expected {app!r} on display {expected_display_id!r}, it's on {match.get('display')!r}")


def check_media_playing(is_playing: bool, what: str = "the media") -> CheckResult:
    """is_playing: the caller's own answer to "is it actually playing" (e.g. Spotify's own
    playback-state check, or a video element's paused attribute once read) -- this function is
    just the named check wrapper, not a new way of asking the question."""
    if is_playing:
        return CheckResult(True, "")
    return CheckResult(False, f"expected {what} to be playing, it isn't")
