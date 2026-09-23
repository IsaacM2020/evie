import json
from datetime import datetime, timedelta

import httpx
import pytest
import respx

from evie.calendar_store import TZ, CalendarStore, CalEvent
from evie.facts import FactStore
from evie.hands import HandsResult
from evie.remember import Remember, Todoist

NOW = datetime(2026, 9, 23, 17, 0, tzinfo=TZ)  # a Wednesday


class FakeTalker:
    def __init__(self, out):
        self.out, self.asked = out, []

    async def extract(self, instructions, text):
        self.asked.append(instructions)
        return self.out


class FakeHands:
    def __init__(self, ok=True):
        self.calls, self.ok = [], ok

    async def do(self, op, timeout=5.0, **args):
        self.calls.append((op, args))
        return HandsResult(self.ok, "added" if self.ok else "Calendar access is off", {"id": "EV1"})


class FakeTodoist:
    def __init__(self, tid="T1"):
        self.added, self.deleted, self.tid = [], [], tid

    async def add(self, content, due):
        self.added.append((content, due))
        return self.tid

    async def delete(self, tid):
        self.deleted.append(tid)
        return True


class Undo:
    def __init__(self):
        self.fns = []

    def remember_undo(self, fn):
        self.fns.append(fn)


def rem(tmp_path, out, hands=None, todoist=None, cal=None):
    undo = Undo()
    r = Remember(FakeTalker(out), hands or FakeHands(), todoist or FakeTodoist(), FactStore(tmp_path / "facts.jsonl"),
                 cal or CalendarStore(), undo, now=lambda: NOW)
    return r, undo


async def test_event_goes_to_the_calendar_via_the_app(tmp_path):
    hands = FakeHands()
    r, undo = rem(tmp_path, {"title": "Dentist", "date": "2026-09-30", "time": "16:00", "duration_min": None,
                             "all_day": False}, hands=hands)
    out = await r.run("event", "i have the dentist next wednesday at 4pm")
    op, args = hands.calls[0]
    assert op == "calendar_add" and args["title"] == "Dentist" and args["all_day"] is False
    assert args["start"] == "2026-09-30T08:00:00Z" and args["end"] == "2026-09-30T09:00:00Z"  # SGT = UTC+8
    assert out.ok and out.said == "Added Dentist, Wednesday 30 Sep at 4pm."
    assert len(undo.fns) == 1


async def test_event_prompt_knows_what_day_it_is(tmp_path):
    r, _ = rem(tmp_path, {"title": "x", "date": "2026-09-24", "time": "09:00"})
    await r.run("event", "x tomorrow at 9")
    assert "Wednesday 23 September 2026" in r._talker.asked[0]


async def test_event_this_week_is_said_by_weekday(tmp_path):
    r, _ = rem(tmp_path, {"title": "Chem test", "date": "2026-09-25", "time": "08:30"})
    assert (await r.run("event", "chem test friday 830")).said == "Added Chem test, Friday at 8:30am."


async def test_event_without_a_time_asks_for_one(tmp_path):
    hands = FakeHands()
    r, _ = rem(tmp_path, {"title": "Dentist", "date": "2026-09-30", "time": None, "all_day": False}, hands=hands)
    out = await r.run("event", "remember i have the dentist next wednesday")
    assert out.ask == "What time?" and hands.calls == []


async def test_all_day_event(tmp_path):
    hands = FakeHands()
    r, _ = rem(tmp_path, {"title": "Vedant's birthday", "date": "2026-09-27", "time": None, "all_day": True},
               hands=hands)
    out = await r.run("event", "vedants birthday is on sunday")
    assert hands.calls[0][1]["all_day"] is True and out.said == "Added Vedant's birthday, Sunday."


async def test_event_in_the_past_or_nonsense_is_refused(tmp_path):
    for bad in ({"title": "x", "date": "2025-01-01", "time": "10:00"}, {"title": "", "date": "2026-09-30", "time": "10:00"},
                {"title": "x", "date": "someday", "time": "10:00"}, {}):
        r, _ = rem(tmp_path, bad)
        assert not (await r.run("event", "blah")).ok, bad


async def test_clash_is_mentioned(tmp_path):
    cal = CalendarStore()
    start = datetime(2026, 9, 30, 15, 30, tzinfo=TZ)
    cal.update([CalEvent("iGEM", start, start + timedelta(hours=2), False, "School")], at=NOW)
    r, _ = rem(tmp_path, {"title": "Dentist", "date": "2026-09-30", "time": "16:00"}, cal=cal)
    assert (await r.run("event", "dentist")).said.endswith("Heads up, it overlaps iGEM.")


async def test_calendar_failure_is_plain(tmp_path):
    r, _ = rem(tmp_path, {"title": "Dentist", "date": "2026-09-30", "time": "16:00"}, hands=FakeHands(ok=False))
    out = await r.run("event", "dentist")
    assert not out.ok and out.said == "Couldn't add it: Calendar access is off."


async def test_task_goes_to_todoist_with_its_due_date(tmp_path):
    td = FakeTodoist()
    r, undo = rem(tmp_path, {"content": "Email Mr Tan", "due": "tomorrow"}, todoist=td)
    out = await r.run("task", "remind me to email mr tan tomorrow")
    assert td.added == [("Email Mr Tan", "tomorrow")] and out.said == "Added to Todoist: Email Mr Tan, tomorrow."
    assert await undo.fns[0]() == "Took Email Mr Tan off Todoist." and td.deleted == ["T1"]


async def test_todoist_down_says_so(tmp_path):
    r, _ = rem(tmp_path, {"content": "x", "due": None}, todoist=FakeTodoist(tid=None))
    assert not (await r.run("task", "remind me to x")).ok


async def test_fact_is_kept_and_recalled(tmp_path):
    r, undo = rem(tmp_path, {"fact": "Isaac's locker code is 4129."})
    out = await r.run("fact", "remember my locker code is 4129")
    assert out.said == "Got it, I'll remember that." and r.facts.recent() == ["Isaac's locker code is 4129."]
    assert await undo.fns[0]() == "Forgot it." and r.facts.recent() == []


async def test_unknown_destination_defaults_to_a_task(tmp_path):
    td = FakeTodoist()
    r, _ = rem(tmp_path, {"content": "Buy ink", "due": None}, todoist=td)
    await r.run(None, "add buy ink")
    assert td.added == [("Buy ink", None)]


def test_fact_store_keeps_the_latest(tmp_path):
    fs = FactStore(tmp_path / "f.jsonl", keep=3)
    for i in range(5):
        fs.add(f"fact {i}")
    assert FactStore(tmp_path / "f.jsonl", keep=3).recent() == ["fact 2", "fact 3", "fact 4"]


@respx.mock
async def test_todoist_client_uses_api_v1():
    route = respx.post("https://api.todoist.com/api/v1/tasks").mock(return_value=httpx.Response(200, json={"id": "99"}))
    tid = await Todoist("tok").add("Email Mr Tan", "tomorrow")
    body = json.loads(route.calls[0].request.content)
    assert tid == "99" and body == {"content": "Email Mr Tan", "due_string": "tomorrow", "due_lang": "en"}
    assert route.calls[0].request.headers["Authorization"] == "Bearer tok"


@respx.mock
async def test_todoist_client_failure_is_none():
    respx.post("https://api.todoist.com/api/v1/tasks").mock(return_value=httpx.Response(401))
    assert await Todoist("bad").add("x", None) is None
    assert await Todoist("").add("x", None) is None


@pytest.mark.live
async def test_live_todoist_add_then_delete_own_task():
    from evie.config import load_settings
    t = Todoist(load_settings().todoist_key)
    tid = await t.add("Evie test (delete me)", None)
    assert tid and await t.delete(tid)
