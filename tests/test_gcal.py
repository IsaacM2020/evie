from datetime import datetime

from evie.gcal import TZ, parse_events


def test_parse_timed_all_day_cancelled_and_untitled():
    items = [
        {"summary": "iGEM call", "start": {"dateTime": "2026-09-24T18:00:00+08:00"},
         "end": {"dateTime": "2026-09-24T19:00:00+08:00"}},
        {"summary": "Chem IA due", "start": {"date": "2026-09-24"}, "end": {"date": "2026-09-25"}},
        {"summary": "old", "status": "cancelled", "start": {"date": "2026-09-24"}, "end": {"date": "2026-09-25"}},
        {"start": {"dateTime": "2026-09-24T01:00:00Z"}, "end": {"dateTime": "2026-09-24T02:00:00Z"}},
    ]
    ev = parse_events(items)
    assert [e.title for e in ev] == ["Chem IA due", "(no title)", "iGEM call"]
    assert ev[0].all_day and not ev[2].all_day
    assert ev[1].start == datetime(2026, 9, 24, 9, 0, tzinfo=TZ)   # 01:00Z is 09:00 in Singapore
    assert ev[2].start.hour == 18 and ev[2].start.tzinfo == TZ
