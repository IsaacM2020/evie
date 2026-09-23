import asyncio
from datetime import date, datetime, timedelta

from evie.calendar_store import TZ, CalendarStore, CalEvent
from evie.countdown import Countdown
from evie.hands import HandsResult
from evie.jev import JevResult
from evie.skills.events import EventSkills

NOW = datetime(2026, 9, 23, 21, 0, tzinfo=TZ)
THU = date(2026, 9, 24)


def at(day, h, m=0):
    return datetime(day.year, day.month, day.day, h, m, tzinfo=TZ)


def cal():
    s = CalendarStore()
    s.update([CalEvent("Sax Class", at(THU, 16, 45), at(THU, 18, 15), False, "Isaac", id="sax"),
              CalEvent("Chem Class", at(THU, 20), at(THU, 21), False, "Isaac", id="chem"),
              CalEvent("Vedant Bday", at(THU, 0), at(THU + timedelta(days=1), 0), True, "Isaac", id="bday")],
             at=NOW)
    return s


class FakeHands:
    def __init__(self, ok=True):
        self.calls, self.ok = [], ok

    async def do(self, op, timeout=5.0, **args):
        self.calls.append((op, args))
        data = {"old_start": "2026-09-24T08:45:00Z", "old_end": "2026-09-24T10:15:00Z"} if op == "calendar_move" else \
            {"title": "Sax Class", "start": "2026-09-24T08:45:00Z", "end": "2026-09-24T10:15:00Z", "all_day": "false"}
        return HandsResult(self.ok, "done" if self.ok else "that calendar is read only", data)


class FakeJev:
    def __init__(self, choice="sax", conf=0.9):
        self.choice, self.conf, self.options = choice, conf, None

    async def ask(self, state, questions):
        self.options = questions["event"]["criteria"]
        return JevResult({"event": {"choice": self.choice, "confidence": self.conf}}, 200.0, 0.0)


class FakeTalker:
    def __init__(self, out=None):
        self.out = out if out is not None else {"date": "2026-09-24", "time": "17:00"}

    async def extract(self, instructions, text):
        return self.out


class Undo:
    def __init__(self):
        self.stack = []

    def remember_undo(self, fn):
        self.stack.append(fn)


def skills(jev=None, talker=None, hands=None, countdown=None):
    u = Undo()
    s = EventSkills(hands or FakeHands(), talker or FakeTalker(), jev or FakeJev(), cal(), u,
                    countdown or Countdown(), now=lambda: NOW)
    return s, u


async def test_jev_picks_only_from_real_timed_events():
    jev = FakeJev()
    s, _ = skills(jev=jev)
    await s.move("move sax to 5")
    assert set(jev.options) == {"sax", "chem", "none"}  # all-day birthdays can't be moved to a time
    assert jev.options["sax"] == "Sax Class, Thursday 24 Sep 16:45 (tomorrow)"


async def test_move_keeps_the_length_and_can_be_undone():
    h = FakeHands()
    s, u = skills(hands=h)
    done = await s.move("move sax to 5")
    assert done.said == "Moved Sax Class to tomorrow at 5pm."
    op, args = h.calls[0]
    assert op == "calendar_move" and args == {"id": "sax", "start": "2026-09-24T09:00:00Z", "end": "2026-09-24T10:30:00Z"}
    assert await u.stack[-1]() == "Sax Class is back where it was."
    assert h.calls[-1] == ("calendar_move", {"id": "sax", "start": "2026-09-24T08:45:00Z", "end": "2026-09-24T10:15:00Z"})


async def test_unsure_which_event_asks():
    s, _ = skills(jev=FakeJev(conf=0.3))
    assert (await s.move("move it to 5")).said == "Which event?"


async def test_no_new_time_asks():
    s, _ = skills(talker=FakeTalker({"date": None, "time": None}))
    assert (await s.move("move sax")).said == "To when?"


async def test_read_only_calendar_is_said_plainly():
    s, _ = skills(hands=FakeHands(ok=False))
    assert (await s.move("move sax to 5")).said == "Couldn't move it: that calendar is read only."


async def test_delete_waits_for_stop_then_deletes():
    h, cd = FakeHands(), Countdown(seconds=0.05)
    s, u = skills(hands=h, countdown=cd)
    done = await s.delete("delete my sax class")
    assert done.said == "Deleting Sax Class, tomorrow at 4:45pm. Say stop to cancel."
    assert h.calls == []
    await asyncio.sleep(0.1)
    assert h.calls == [("calendar_delete", {"id": "sax"})]
    assert await u.stack[-1]() == "Put Sax Class back."
    assert h.calls[-1][0] == "calendar_add" and h.calls[-1][1]["title"] == "Sax Class"


async def test_stop_cancels_the_delete():
    h, cd = FakeHands(), Countdown(seconds=0.05)
    s, _ = skills(hands=h, countdown=cd)
    await s.delete("delete my sax class")
    assert cd.cancel() is True
    await asyncio.sleep(0.1)
    assert h.calls == []
    assert cd.cancel() is False  # nothing left to cancel


async def test_none_of_these_events():
    s, _ = skills(jev=FakeJev(choice="none"))
    assert (await s.delete("delete the dentist")).said == "I can't find that on your calendar."
