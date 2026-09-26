import json
from datetime import datetime, timedelta

import httpx
import pytest
import respx

from evie.calendar_store import TZ, CalendarStore, CalEvent
from evie.facts import FactStore
from evie.hands import HandsResult
from evie.remember import Remember, Todoist, TodoistCache

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
    for bad in ({"title": "x", "date": "2025-01-01", "time": "10:00"},
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


def test_fact_store_records_provenance(tmp_path):
    """Memory V2 (Phase 6 P2): who/what said to remember this, defaulting to Isaac himself."""
    import json
    path = tmp_path / "f.jsonl"
    fs = FactStore(path)
    fs.add("locker code is 4129")
    fs.add("heard from proactive.overheard", source="proactive")
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[0]["source"] == "isaac" and rows[1]["source"] == "proactive"


@respx.mock
async def test_todoist_client_uses_api_v1():
    route = respx.post("https://api.todoist.com/api/v1/tasks").mock(return_value=httpx.Response(200, json={"id": "99"}))
    tid = await Todoist("tok").add("Email Mr Tan", "tomorrow")
    body = json.loads(route.calls[0].request.content)
    assert tid.id == "99" and body == {"content": "Email Mr Tan", "due_string": "tomorrow", "due_lang": "en"}
    assert route.calls[0].request.headers["Authorization"] == "Bearer tok"


@respx.mock
async def test_todoist_client_failure_has_no_id():
    respx.post("https://api.todoist.com/api/v1/tasks").mock(return_value=httpx.Response(500))
    assert (await Todoist("bad").add("x", None)).id is None
    assert (await Todoist("").add("x", None)).id is None


@pytest.mark.live
async def test_live_todoist_add_then_delete_own_task():
    from evie.config import load_settings
    t = Todoist(load_settings().todoist_key)
    tid = await t.add("Evie test (delete me)", None)
    assert tid and await t.delete(tid)


# -- Phase 3.5: reminders, Todoist that copes, facts that never vanish -----------------------

class FakeTimers:
    def __init__(self):
        self.started = []

    def start(self, seconds, label=""):
        self.started.append((seconds, label))


def rem2(tmp_path, out, todoist=None, timers=None):
    undo = Undo()
    r = Remember(FakeTalker(out), FakeHands(), todoist or FakeTodoist(), FactStore(tmp_path / "facts.jsonl"),
                 CalendarStore(), undo, now=lambda: NOW, timers=timers)
    return r, undo


async def test_short_reminder_is_a_spoken_timer_not_todoist(tmp_path):
    # 21:19 on 2026-09-23: "remind me in 30 seconds to eat a banana" went to Todoist and got a 400.
    tm, td = FakeTimers(), FakeTodoist()
    r, _ = rem2(tmp_path, {"what": "eat a banana", "date": None, "time": None}, todoist=td, timers=tm)
    out = await r.run("reminder", "set a timer for 30 seconds please, remind me to go eat a banana")
    assert tm.started == [(30, "eat a banana")] and td.added == []
    assert out.said == "Okay, in 30 seconds I'll remind you to eat a banana."


async def test_reminder_at_a_time_today_is_a_timer(tmp_path):
    tm = FakeTimers()
    r, _ = rem2(tmp_path, {"what": "call mom", "date": None, "time": "19:30"}, timers=tm)
    out = await r.run("reminder", "remind me at 7 30 to call mom")
    assert tm.started == [(2.5 * 3600, "call mom")] and out.said == "Okay, at 7:30pm I'll remind you to call mom."


async def test_reminder_tomorrow_goes_to_todoist_with_its_time(tmp_path):
    tm, td = FakeTimers(), FakeTodoist()
    r, _ = rem2(tmp_path, {"what": "Email Mr Tan", "date": "2026-09-24", "time": "17:00", "due": "tomorrow at 5pm"},
                todoist=td, timers=tm)
    out = await r.run("reminder", "remind me tomorrow at 5 to email mr tan")
    assert tm.started == [] and td.added == [("Email Mr Tan", "tomorrow at 5pm")]
    assert out.said == "Added to Todoist: Email Mr Tan, tomorrow at 5pm."


async def test_reminder_with_no_time_is_a_plain_task(tmp_path):
    td = FakeTodoist()
    r, _ = rem2(tmp_path, {"what": "Buy milk", "date": None, "time": None}, todoist=td, timers=FakeTimers())
    await r.run("reminder", "remind me to buy milk")
    assert td.added == [("Buy milk", None)]


async def test_short_reminder_can_be_undone(tmp_path):
    class Timers(FakeTimers):
        def start(self, seconds, label=""):
            super().start(seconds, label)

            class T:
                id = "t1"
            return T()

        def cancel(self, tid=None):
            self.cancelled = tid
            return True

    tm = Timers()
    r, undo = rem2(tmp_path, {"what": "stretch", "date": None, "time": None}, timers=tm)
    await r.run("reminder", "remind me in 1 minute to stretch")
    assert await undo.fns[0]() == "Reminder cancelled." and tm.cancelled == "t1"


@respx.mock
async def test_todoist_400_retries_without_the_due_date():
    route = respx.post("https://api.todoist.com/api/v1/tasks").mock(
        side_effect=[httpx.Response(400, json={"error": "bad due"}), httpx.Response(200, json={"id": "9"})])
    t = Todoist("k")
    added = await t.add("Eat a banana", "in 30 seconds")
    assert added.id == "9" and added.due_dropped is True
    assert json.loads(route.calls[1].request.content) == {"content": "Eat a banana"}


@respx.mock
async def test_todoist_bad_key_is_named():
    respx.post("https://api.todoist.com/api/v1/tasks").respond(401)
    added = await Todoist("k").add("x", None)
    assert added.id is None and added.error == "Todoist key's wrong"


async def test_task_whose_due_date_was_dropped_says_so(tmp_path):
    from evie.remember import Added

    class TD(FakeTodoist):
        async def add(self, content, due):
            self.added.append((content, due))
            return Added("T2", due_dropped=True)

    r, _ = rem(tmp_path, {"content": "Eat a banana", "due": "in 30 seconds"}, todoist=TD())
    assert (await r.run("task", "x")).said == "Added to Todoist: Eat a banana, but I couldn't set the time."


async def test_fact_is_still_kept_when_extraction_fails(tmp_path):
    # 21:11: Groq stalled, extraction came back empty, she said "I didn't catch what to remember".
    r, _ = rem(tmp_path, {})
    out = await r.run("fact", "Remember my locker code, it is 2545.")
    assert out.said == "Got it, I'll remember that."
    assert r.facts.recent() == ["Isaac said: my locker code, it is 2545."]


async def test_event_without_a_title_asks_what_its_called(tmp_path):
    # 21:08: "Set a calendar event for next week Thursday, 4.30pm" got "I couldn't work out when that is".
    hands = FakeHands()
    r, _ = rem(tmp_path, {"title": "", "date": "2026-10-01", "time": "16:30"}, hands=hands)
    out = await r.run("event", "set a calendar event for next week thursday 4.30pm")
    assert out.ask == "What's it called?" and hands.calls == []


@respx.mock
async def test_todoist_lists_today_and_overdue_and_closes():
    respx.get("https://api.todoist.com/api/v1/tasks/filter").respond(200, json={"results": [
        {"id": "1", "content": "Email bio teacher", "due": {"date": "2026-09-24", "string": "tomorrow"}},
        {"id": "2", "content": "Maths practice", "due": None}]})
    close = respx.post("https://api.todoist.com/api/v1/tasks/1/close").respond(204)
    t = Todoist("k")
    tasks = await t.list("today | overdue")
    assert [(x.id, x.content, x.due) for x in tasks] == [("1", "Email bio teacher", "tomorrow"), ("2", "Maths practice", None)]
    assert await t.close("1") is True and close.called


# -- TodoistCache: world.tasks needs a sync view; Todoist.list() is async (Phase 6 P0 gap-fill) --

class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@respx.mock
async def test_todoist_cache_populated():
    respx.get("https://api.todoist.com/api/v1/tasks/filter").respond(200, json={"results": [
        {"id": "1", "content": "Email bio teacher", "due": {"date": "2026-09-24", "string": "tomorrow"}}]})
    cache = TodoistCache(Todoist("k"), clock=Clock())
    await cache.refresh()
    view = cache.view()
    assert view["available"] is True and view["stale"] is False
    assert view["items"] == [{"id": "1", "content": "Email bio teacher", "due": "tomorrow"}]


@respx.mock
async def test_todoist_cache_empty_is_not_the_same_as_unavailable():
    respx.get("https://api.todoist.com/api/v1/tasks/filter").respond(200, json={"results": []})
    cache = TodoistCache(Todoist("k"), clock=Clock())
    await cache.refresh()
    view = cache.view()
    assert view["items"] == [] and view["available"] is True and view["stale"] is False


async def test_todoist_cache_unavailable_when_not_configured():
    """Not configured is a deliberate choice (Todoist is optional, see config.py), not a fault:
    world.tasks correctly shows it unavailable, but it must never raise into health_tick's
    watched() -- that would flag a permanently "degraded" component for a feature nobody turned on."""
    cache = TodoistCache(Todoist(""), clock=Clock())
    await cache.refresh()  # must not raise
    view = cache.view()
    assert view["available"] is False and view["items"] == [] and view["stale"] is True


@respx.mock
async def test_todoist_cache_unavailable_on_network_failure_keeps_last_good_items():
    route = respx.get("https://api.todoist.com/api/v1/tasks/filter")
    route.mock(return_value=httpx.Response(200, json={"results": [{"id": "1", "content": "x", "due": None}]}))
    clock = Clock()
    cache = TodoistCache(Todoist("k"), clock=clock)
    await cache.refresh()
    assert cache.view()["available"] is True
    route.mock(return_value=httpx.Response(500))
    clock.t += 5.0  # a genuine failure, but only 5s later: not stale yet, just unavailable right now
    with pytest.raises(RuntimeError):  # a real failure DOES raise, so health_tick's watched() sees it
        await cache.refresh()
    view = cache.view()
    assert view["available"] is False
    assert view["items"] == [{"id": "1", "content": "x", "due": None}]  # last good list, not wiped
    assert view["stale"] is False


@respx.mock
async def test_todoist_cache_goes_stale_after_a_long_silence():
    respx.get("https://api.todoist.com/api/v1/tasks/filter").respond(200, json={"results": []})
    clock = Clock()
    cache = TodoistCache(Todoist("k"), clock=clock)
    await cache.refresh()
    assert cache.view()["stale"] is False
    clock.t += 1000.0  # past STALE_AFTER_S (900s) with no further refresh at all
    assert cache.view()["stale"] is True


async def test_todoist_cache_before_any_refresh_is_stale_with_no_items():
    view = TodoistCache(Todoist("k")).view()
    assert view["items"] == [] and view["stale"] is True


@respx.mock
async def test_todoist_cache_recovers_after_a_failure_without_raising():
    """The health_tick round trip Isaac asked to verify on the real Mac: a real outage raises (so
    watched() flags "todoist" degraded), and the very next successful refresh raises nothing (so
    watched() flags it recovered) -- exactly how jev/groq/stt already behave."""
    route = respx.get("https://api.todoist.com/api/v1/tasks/filter")
    route.mock(return_value=httpx.Response(500))
    cache = TodoistCache(Todoist("k"), clock=Clock())
    with pytest.raises(RuntimeError):
        await cache.refresh()
    assert cache.view()["available"] is False
    route.mock(return_value=httpx.Response(200, json={"results": []}))
    await cache.refresh()  # recovered: must not raise this time
    assert cache.view()["available"] is True
