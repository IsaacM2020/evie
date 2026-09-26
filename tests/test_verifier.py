"""P2-E design (the other half, alongside recovery.py): named, typed completion checks (spec §12:
"url, element, text, app_front, window_on_display, file_exists, media_playing; polled"). Today
Planner._expect_problem only supports two kinds (url_contains, element) hardcoded into one method.
This module defines the check TYPES and their predicate logic as free functions over data the
caller already has (a Screen, a World, a filesystem path) -- window_on_display and media_playing
need live ComputerState/media data this environment can't produce, so their predicates take that
data as a plain parameter rather than reaching for it themselves, and are tested with hand-built
fixtures shaped like what a real read would produce.

Does not touch Planner._expect_problem or the `expect` step's polling loop (planner.py) -- wiring
these in as additional `expect` step kinds is deferred to the Phase 2 follow-up plan."""
from pathlib import Path

from evie.computer.observe import Screen
from evie.computer.verifier import (CheckResult, check_app_front, check_element, check_file_exists,
                                    check_media_playing, check_url_contains, check_window_on_display)


def test_check_url_contains_passes_when_the_url_has_the_substring():
    screen = Screen(snapshot="s1", app="Safari", kind="web", url="https://www.gmail.com/inbox")
    assert check_url_contains(screen, "gmail") == CheckResult(True, "")


def test_check_url_contains_fails_with_a_useful_message():
    screen = Screen(snapshot="s1", app="Safari", kind="web", url="https://example.com/404")
    r = check_url_contains(screen, "gmail")
    assert r.ok is False and "gmail" in r.detail and "example.com/404" in r.detail


def test_check_element_passes_when_a_matching_label_is_on_screen():
    screen = Screen(snapshot="s1", app="Settings", kind="app",
                    elements=[{"id": "e1", "label": "Bluetooth", "role": "text"}])
    assert check_element(screen, "Bluetooth") == CheckResult(True, "")


def test_check_element_fails_when_nothing_matches():
    screen = Screen(snapshot="s1", app="Settings", kind="app", elements=[])
    r = check_element(screen, "Bluetooth")
    assert r.ok is False and "Bluetooth" in r.detail


def test_check_app_front_passes_when_the_named_app_is_frontmost():
    assert check_app_front("Safari", front_app="Safari") == CheckResult(True, "")


def test_check_app_front_fails_with_what_is_actually_front():
    r = check_app_front("Safari", front_app="Notes")
    assert r.ok is False and "Notes" in r.detail and "Safari" in r.detail


def test_check_file_exists_passes_for_a_real_file(tmp_path):
    f = tmp_path / "report.pdf"
    f.write_text("x")
    assert check_file_exists(f) == CheckResult(True, "")


def test_check_file_exists_fails_for_a_missing_file(tmp_path):
    r = check_file_exists(tmp_path / "nope.pdf")
    assert r.ok is False and "nope.pdf" in r.detail


def test_check_result_is_a_plain_comparable_record():
    assert CheckResult(True, "") == CheckResult(True, "")
    assert CheckResult(False, "x") != CheckResult(True, "")


def test_check_window_on_display_passes_when_the_apps_window_is_on_the_expected_display():
    windows = [{"app": "Safari", "display": "builtin"}, {"app": "Xcode", "display": "external-1"}]
    assert check_window_on_display("Safari", "builtin", windows) == CheckResult(True, "")


def test_check_window_on_display_fails_when_its_on_a_different_display():
    windows = [{"app": "Safari", "display": "external-1"}]
    r = check_window_on_display("Safari", "builtin", windows)
    assert r.ok is False and "builtin" in r.detail and "external-1" in r.detail


def test_check_window_on_display_fails_when_the_app_has_no_window_at_all():
    r = check_window_on_display("Safari", "builtin", [])
    assert r.ok is False and "Safari" in r.detail


def test_check_media_playing_passes_when_told_it_is_playing():
    assert check_media_playing(True, "The Mentalist") == CheckResult(True, "")


def test_check_media_playing_fails_when_told_it_is_not():
    r = check_media_playing(False, "The Mentalist")
    assert r.ok is False and "The Mentalist" in r.detail
