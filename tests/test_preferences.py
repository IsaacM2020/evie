from evie.preferences import Preferences


def test_set_and_get_roundtrip(tmp_path):
    p = Preferences(path=tmp_path / "prefs.json")
    p.set("theme", "dark")
    assert p.get("theme") == "dark"


def test_get_missing_key_returns_default(tmp_path):
    p = Preferences(path=tmp_path / "prefs.json")
    assert p.get("nope") is None
    assert p.get("nope", "fallback") == "fallback"


def test_records_source_and_timestamp(tmp_path):
    p = Preferences(path=tmp_path / "prefs.json", clock=lambda: 42.0)
    p.set("output_mode", "text", source="quiet.py")
    e = p.entry("output_mode")
    assert e.source == "quiet.py" and e.set_at == 42.0


def test_state_survives_a_restart(tmp_path):
    path = tmp_path / "prefs.json"
    Preferences(path=path).set("theme", "dark")
    assert Preferences(path=path).get("theme") == "dark"


def test_all_returns_plain_values(tmp_path):
    p = Preferences(path=tmp_path / "prefs.json")
    p.set("a", 1)
    p.set("b", "two")
    assert p.all() == {"a": 1, "b": "two"}


def test_growth_cap_drops_the_oldest_set_key(tmp_path):
    now = [0.0]
    p = Preferences(path=tmp_path / "prefs.json", clock=lambda: now[0], max_keys=2)
    p.set("a", 1)
    now[0] += 1
    p.set("b", 2)
    now[0] += 1
    p.set("c", 3)
    assert set(p.all()) == {"b", "c"}
