from datetime import date, datetime, timedelta

from fastapi.testclient import TestClient

from evie.calendar_store import TZ, CalendarStore, CalEvent
from evie.events import EventBus
from evie.server import Deps, create_app
from evie.switchboard import Switchboard
from tests.test_server import FakeJev

TODAY = date(2026, 9, 23)


def at(day: date, h: int, m: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, h, m, tzinfo=TZ)


def ev(title, start, end, all_day=False, cal="Isaac"):
    return CalEvent(title=title, start=start, end=end, all_day=all_day, calendar=cal)


def store_with(*events) -> CalendarStore:
    s = CalendarStore()
    s.update(list(events), at=at(TODAY, 8))
    return s


def test_on_puts_all_day_first_then_by_start():
    tmr = TODAY + timedelta(days=1)
    s = store_with(
        ev("iGEM", at(tmr, 14, 30), at(tmr, 16)),
        ev("Math", at(tmr, 9), at(tmr, 10)),
        ev("Mom's birthday", at(tmr, 0), at(tmr + timedelta(days=1), 0), all_day=True),
        ev("Chem", at(TODAY, 9), at(TODAY, 10)),
    )
    assert [e.title for e in s.on(tmr)] == ["Mom's birthday", "Math", "iGEM"]


def test_summary_tomorrow_wording():
    tmr = TODAY + timedelta(days=1)
    s = store_with(ev("iGEM", at(tmr, 14, 30), at(tmr, 16)), ev("Math", at(tmr, 9), at(tmr, 10)))
    assert s.summary(tmr, today=TODAY) == "Tomorrow: 9:00-10:00 Math, 14:30-16:00 iGEM"


def test_summary_today_with_all_day():
    s = store_with(ev("Sports day", at(TODAY, 0), at(TODAY, 23, 59), all_day=True),
                   ev("Chem", at(TODAY, 9), at(TODAY, 10)))
    assert s.summary(TODAY, today=TODAY) == "Today: Sports day (all day), 9:00-10:00 Chem"


def test_summary_empty_day():
    assert store_with().summary(TODAY + timedelta(days=1), today=TODAY) == "Nothing on tomorrow."


def test_summary_other_day_uses_weekday_name():
    fri = date(2026, 9, 25)
    s = store_with(ev("Physics", at(fri, 11), at(fri, 12)))
    assert s.summary(fri, today=TODAY) == "Friday: 11:00-12:00 Physics"


def test_stale_before_first_update_and_after_two_missed_pushes():
    s = CalendarStore()
    assert s.stale(at(TODAY, 8))
    s.update([], at=at(TODAY, 8))
    assert not s.stale(at(TODAY, 8, 10))
    assert s.stale(at(TODAY, 8, 31))


def client():
    return TestClient(create_app(lambda: Deps(sb=Switchboard(FakeJev()), calendar=CalendarStore(), bus=EventBus()), probe=False))


def test_calendar_endpoint_round_trip():
    body = {"events": [{"title": "Math", "start": "2026-09-24T09:00:00+08:00",
                        "end": "2026-09-24T10:00:00+08:00", "all_day": False, "calendar": "Math"}]}
    with client() as c:
        r = c.post("/calendar", json=body)
        store = c.app.state.d.calendar
    assert r.json() == {"ok": True, "count": 1}
    assert store.on(date(2026, 9, 24))[0].title == "Math"


def test_calendar_endpoint_rejects_datetime_without_timezone():
    body = {"events": [{"title": "x", "start": "2026-09-24T09:00:00", "end": "2026-09-24T10:00:00",
                        "all_day": False, "calendar": "c"}]}
    with client() as c:
        assert c.post("/calendar", json=body).status_code == 422


def test_calendar_endpoint_rejects_bad_datetime():
    body = {"events": [{"title": "x", "start": "tomorrow-ish", "end": "later",
                        "all_day": False, "calendar": "c"}]}
    with client() as c:
        assert c.post("/calendar", json=body).status_code == 422


def test_updated_at_is_none_until_first_update():
    s = CalendarStore()
    assert s.updated_at is None
    s.update([], at=at(TODAY, 8))
    assert s.updated_at == at(TODAY, 8)


# -- Phase 3.5: end times, "right now", any day ----------------------------------------------

def school_day():
    return store_with(
        ev("Vedant Bday", at(TODAY, 0), at(TODAY + timedelta(days=1), 0), all_day=True),
        ev("School", at(TODAY, 8), at(TODAY, 15, 30)),
        ev("Shipra Class", at(TODAY, 18), at(TODAY, 19)),
        ev("Chem Class", at(TODAY, 20), at(TODAY, 21)),
    )


def test_summary_has_end_times():
    assert school_day().summary(TODAY, today=TODAY) == (
        "Today: Vedant Bday (all day), 8:00-15:30 School, 18:00-19:00 Shipra Class, 20:00-21:00 Chem Class")


def test_now_line_inside_an_event():
    assert school_day().now_line(at(TODAY, 10)) == "Right now: School until 15:30. Next: Shipra Class at 18:00."


def test_now_line_between_events():
    assert school_day().now_line(at(TODAY, 16)) == "Nothing on right now. Next: Shipra Class at 18:00."


def test_now_line_at_the_exact_end_is_over():
    assert school_day().now_line(at(TODAY, 15, 30)).startswith("Nothing on right now.")


def test_now_line_at_the_exact_start_is_on():
    assert school_day().now_line(at(TODAY, 18)).startswith("Right now: Shipra Class until 19:00.")


def test_now_line_after_the_last_event_looks_at_tomorrow():
    s = school_day()
    tmr = TODAY + timedelta(days=1)
    s.update(s.on(TODAY) + [ev("Sax Class", at(tmr, 16, 45), at(tmr, 18, 15))], at=at(TODAY, 8))
    assert s.now_line(at(TODAY, 22)) == "Nothing on right now. Next: Sax Class tomorrow at 16:45."


def test_now_line_ignores_all_day_and_says_when_nothing_is_coming():
    s = store_with(ev("Holiday", at(TODAY, 0), at(TODAY + timedelta(days=1), 0), all_day=True))
    assert s.now_line(at(TODAY, 12)) == "Nothing on right now, and nothing else coming up."


def test_two_events_at_once_both_named():
    s = store_with(ev("School", at(TODAY, 8), at(TODAY, 15)), ev("iGEM call", at(TODAY, 10), at(TODAY, 11)))
    assert s.now_line(at(TODAY, 10, 30)).startswith("Right now: School until 15:00, iGEM call until 11:00.")


def test_now_line_without_a_calendar():
    assert CalendarStore().now_line(at(TODAY, 10)) == "Calendar not connected yet."


def test_stale_after_two_missed_pushes():
    s = school_day()  # pushed at 08:00
    assert not s.stale(at(TODAY, 8, 10)) and s.stale(at(TODAY, 8, 12))


def test_covers_the_pushed_fortnight_only():
    s = school_day()
    assert s.covers(TODAY + timedelta(days=13)) and not s.covers(TODAY + timedelta(days=14))
    assert not CalendarStore().covers(TODAY)


async def test_lookup_far_day_asks_the_app():
    from evie.calendar_store import lookup
    from evie.hands import HandsResult
    far = date(2026, 10, 14)

    class FakeHands:
        def __init__(self):
            self.calls = []

        async def do(self, op, timeout=5.0, **args):
            self.calls.append((op, args))
            return HandsResult(True, "1 events", {"events": '[{"id":"x","title":"Chem test","start":"2026-10-14T01:00:00Z","end":"2026-10-14T02:00:00Z","all_day":false,"calendar":"Isaac"}]'})

    h = FakeHands()
    out = await lookup(school_day(), h, far, today=TODAY)
    assert h.calls[0][0] == "calendar_query"
    assert h.calls[0][1] == {"start": "2026-10-13T16:00:00Z", "end": "2026-10-14T16:00:00Z"}
    assert out == "Wednesday 14 Oct: 9:00-10:00 Chem test"


async def test_lookup_near_day_uses_the_snapshot():
    from evie.calendar_store import lookup

    class NoHands:
        async def do(self, *a, **k):
            raise AssertionError("should not ask the app")

    assert (await lookup(school_day(), NoHands(), TODAY, today=TODAY)).startswith("Today: Vedant Bday")


async def test_lookup_when_the_app_is_offline_says_so():
    from evie.calendar_store import lookup
    from evie.hands import HandsResult

    class Down:
        async def do(self, *a, **k):
            return HandsResult(False, "can't reach my hands")

    assert await lookup(school_day(), Down(), date(2026, 11, 2), today=TODAY) == \
        "Couldn't check Monday 2 Nov: the Evie app isn't answering."


def test_calendar_endpoint_keeps_ids_and_which_calendars_are_read():
    body = {"events": [{"id": "e1", "title": "Sax", "start": "2026-09-24T16:45:00+08:00",
                        "end": "2026-09-24T18:15:00+08:00", "all_day": False, "calendar": "Isaac"}],
            "calendars": [{"id": "g", "title": "Isaac", "source": "Google", "writable": True, "used": True},
                          {"id": "i", "title": "Home", "source": "iCloud", "writable": True, "used": False}]}
    with client() as c:
        c.post("/calendar", json=body)
        dbg = c.get("/debug/calendar").json()
        store = c.app.state.d.calendar
    assert store.on(date(2026, 9, 24))[0].id == "e1"
    assert dbg["reading"] == ["Isaac (Google , 0 events)"] and dbg["ignored"] == ["Home (iCloud)"]


def test_singapore_public_holidays_are_on_the_calendar():
    s = store_with()
    assert s.summary(date(2026, 12, 25), today=TODAY) == "Friday 25 Dec: Christmas Day (public holiday, all day)"
    assert "Diwali (observed) (public holiday, all day)" in s.summary(date(2026, 11, 9), today=TODAY)
    assert s.summary(date(2026, 12, 24), today=TODAY) == "Nothing on Thursday 24 Dec."


async def test_far_lookup_includes_holidays_too():
    from evie.calendar_store import lookup
    from evie.hands import HandsResult

    class Empty:
        async def do(self, *a, **k):
            return HandsResult(True, "0 events", {"events": "[]"})

    assert await lookup(school_day(), Empty(), date(2026, 12, 25), today=TODAY) == \
        "Friday 25 Dec: Christmas Day (public holiday, all day)"
