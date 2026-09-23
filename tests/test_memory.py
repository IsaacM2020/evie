import json
from datetime import datetime

from evie.calendar_store import TZ
from evie.memory import Conversation


def at(h, m=0, day=24):
    return datetime(2026, 9, day, h, m, tzinfo=TZ)


class Now:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def test_keeps_todays_turns_in_order(tmp_path):
    c = Conversation(tmp_path, now=Now(at(10)))
    c.add("what's on friday", "Sax at 4:45.")
    c.add("move it to 5", "Moved Sax Class to Friday at 5pm.")
    assert c.lines() == ['10:00 Isaac: "what\'s on friday" / Evie: "Sax at 4:45."',
                         '10:00 Isaac: "move it to 5" / Evie: "Moved Sax Class to Friday at 5pm."']


def test_only_the_last_twelve_verbatim(tmp_path):
    c = Conversation(tmp_path, now=Now(at(10)))
    for i in range(20):
        c.add(f"q{i}", f"a{i}")
    lines = c.lines()
    assert len(lines) == 12 and '"q8"' in lines[0] and '"q19"' in lines[-1]
    assert [t["isaac"] for t in c.older()] == [f"q{i}" for i in range(8)]


def test_survives_a_core_restart(tmp_path):
    Conversation(tmp_path, now=Now(at(10))).add("hi", "Hey.")
    assert len(Conversation(tmp_path, now=Now(at(11))).lines()) == 1


def test_a_new_day_starts_at_4am(tmp_path):
    now = Now(at(23, day=23))
    c = Conversation(tmp_path, now=now)
    c.add("late one", "Night.")
    now.t = at(3, 30, day=24)  # still "yesterday" until 4am
    assert len(c.lines()) == 1
    now.t = at(4, 1, day=24)
    assert c.lines() == []


def test_summary_is_kept_and_saved(tmp_path):
    c = Conversation(tmp_path, now=Now(at(10)))
    c.set_summary("Isaac asked about Friday and moved sax to 5.")
    assert Conversation(tmp_path, now=Now(at(12))).summary == "Isaac asked about Friday and moved sax to 5."


def test_needs_summary_every_ten_turns_past_the_window(tmp_path):
    c = Conversation(tmp_path, now=Now(at(10)))
    for i in range(12):
        c.add(f"q{i}", "a")
    assert not c.needs_summary()
    for i in range(10):
        c.add(f"r{i}", "a")
    assert c.needs_summary()
    c.set_summary("x")
    assert not c.needs_summary()


def test_file_is_one_json_line_per_turn(tmp_path):
    c = Conversation(tmp_path, now=Now(at(10)))
    c.add("hi", "Hey.", did="answer")
    rows = [json.loads(l) for l in (tmp_path / "conversation-2026-09-24.jsonl").read_text().splitlines()]
    assert rows[0]["isaac"] == "hi" and rows[0]["evie"] == "Hey." and rows[0]["did"] == "answer"
