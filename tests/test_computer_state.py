from evie.computer.state import ComputerState, Display, Window


def test_from_data_parses_two_displays_and_their_windows():
    raw = {
        "version": 41, "ts": 1234.5,
        "displays": [
            {"id": "builtin", "builtin": True, "frame": [0, 0, 1470, 956]},
            {"id": "external-1", "builtin": False, "frame": [1470, 0, 2560, 1440]},
        ],
        "windows": [
            {"app": "Safari", "title": "Evie repo", "frame": [100, 100, 900, 700], "display": "builtin", "focused": True},
            {"app": "Xcode", "title": "main.swift", "frame": [1500, 50, 2000, 1200], "display": "external-1", "focused": False},
        ],
        "front_app": "Safari",
        "front_element": {"role": "button", "label": "Reload"},
        "tabs": [{"window": 1, "order": 1, "index": 1, "current": True, "title": "Evie repo", "url": "https://github.com/x"}],
    }
    s = ComputerState.from_data(raw)
    assert s.version == 41 and s.ts == 1234.5
    assert len(s.displays) == 2 and s.displays[0].builtin is True
    assert s.displays[1].frame == (1470, 0, 2560, 1440)
    assert len(s.windows) == 2 and s.windows[0].focused is True
    assert s.front_app == "Safari"
    assert s.front_element == {"role": "button", "label": "Reload"}
    assert len(s.tabs) == 1 and s.tabs[0].title == "Evie repo"


def test_from_data_handles_missing_fields_gracefully():
    """A partial/malformed read (Swift side not yet answering some field) must not crash --
    matches World.from_data's existing tolerance for missing keys."""
    s = ComputerState.from_data({})
    assert s.version == 0 and s.displays == [] and s.windows == [] and s.front_app == ""
    assert s.front_element is None and s.tabs == []


def test_from_data_ignores_malformed_display_and_window_entries():
    raw = {"displays": ["not a dict", {"id": "ok", "builtin": True, "frame": [0, 0, 100, 100]}],
           "windows": [None, {"app": "Notes", "title": "", "frame": [0, 0, 10, 10], "display": "ok", "focused": False}]}
    s = ComputerState.from_data(raw)
    assert len(s.displays) == 1 and s.displays[0].id == "ok"
    assert len(s.windows) == 1 and s.windows[0].app == "Notes"
