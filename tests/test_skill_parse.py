import pytest

from evie.skills.parse import match_app, normalize_url, parse_duration, parse_volume, say_duration

APPS = ["Notion", "Google Chrome", "Safari", "Visual Studio Code", "Spotify", "Messages", "zoom.us", "Arc",
        "System Settings", "Microsoft Teams"]


@pytest.mark.parametrize("text,want", [
    ("evie volume 30", ("set", 30)),
    ("set the volume to 45 percent", ("set", 45)),
    ("volume to one hundred", ("set", 100)),
    ("turn it up", ("up", 10)),
    ("louder please", ("up", 10)),
    ("turn the volume down a bit", ("down", 10)),
    ("quieter", ("down", 10)),
    ("mute", ("mute", 0)),
    ("unmute the mac", ("unmute", 0)),
    ("volume 250", ("set", 100)),
    ("what's the volume", None),
])
def test_parse_volume(text, want):
    assert parse_volume(text) == want


@pytest.mark.parametrize("text,want", [
    ("set a timer for 10 minutes", 600),
    ("timer for ten minutes", 600),
    ("a minute and a half timer", 90),
    ("timer 1 hour 30 minutes", 5400),
    ("30 sec timer", 30),
    ("set a timer for an hour", 3600),
    ("half an hour timer", 1800),
    ("5 mins", 300),
    ("ninety seconds", 90),
    ("twenty five minutes", 1500),
    ("two and a half minutes", 150),
    ("set a timer", None),
])
def test_parse_duration(text, want):
    assert parse_duration(text) == want


@pytest.mark.parametrize("secs,want", [(600, "10 minute"), (90, "1 and a half minute"), (30, "30 second"),
                                       (3600, "1 hour"), (5400, "1 and a half hour"), (150, "2 and a half minute"),
                                       (75, "75 second")])
def test_say_duration(secs, want):
    assert say_duration(secs) == want


@pytest.mark.parametrize("text,want", [
    ("evie open notion", "Notion"),
    ("open chrome", "Google Chrome"),
    ("open vs code", "Visual Studio Code"),
    ("open visual studio code please", "Visual Studio Code"),
    ("open zoom", "zoom.us"),
    ("open settings", "System Settings"),
    ("open teams", "Microsoft Teams"),
    ("open arc", "Arc"),
    ("start the thing i use for notes", None),
    ("search for something", None),  # "arc" inside "search" must not count
])
def test_match_app(text, want):
    assert match_app(text, APPS) == want


@pytest.mark.parametrize("raw,want", [
    ("youtube.com", "https://youtube.com"),
    ("https://www.igem.org/teams", "https://www.igem.org/teams"),
    ("http://localhost:3000", None),
    ("javascript:alert(1)", None),
    ("file:///etc/passwd", None),
    ("not a url", None),
    ("", None),
])
def test_normalize_url(raw, want):
    assert normalize_url(raw) == want
