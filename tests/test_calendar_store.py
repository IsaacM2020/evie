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
    assert s.summary(tmr, today=TODAY) == "Tomorrow: 9:00 Math, 14:30 iGEM"


def test_summary_today_with_all_day():
    s = store_with(ev("Sports day", at(TODAY, 0), at(TODAY, 23, 59), all_day=True),
                   ev("Chem", at(TODAY, 9), at(TODAY, 10)))
    assert s.summary(TODAY, today=TODAY) == "Today: Sports day (all day), 9:00 Chem"


def test_summary_empty_day():
    assert store_with().summary(TODAY + timedelta(days=1), today=TODAY) == "Nothing on tomorrow."


def test_summary_other_day_uses_weekday_name():
    fri = date(2026, 9, 25)
    s = store_with(ev("Physics", at(fri, 11), at(fri, 12)))
    assert s.summary(fri, today=TODAY) == "Friday: 11:00 Physics"


def test_stale_before_first_update_and_after_31_min():
    s = CalendarStore()
    assert s.stale(at(TODAY, 8))
    s.update([], at=at(TODAY, 8))
    assert not s.stale(at(TODAY, 8, 29))
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
