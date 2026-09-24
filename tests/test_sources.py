"""Phase 4 T8/T9: where the follow-ups come from."""
from datetime import datetime, timedelta

from evie.calendar_store import TZ, CalendarStore, CalEvent
from evie.proactive.sources import Sources
from evie.remember import Task

DAY = datetime(2026, 9, 24, tzinfo=TZ)


def at(h, m=0, day=0):
    return (DAY + timedelta(days=day)).replace(hour=h, minute=m)


class Engine:
    def __init__(self, free=True):
        self.items, self._free = [], free

    def add(self, it):
        if any(i.source_key == it.source_key for i in self.items):
            return False
        self.items.append(it)
        return True

    def free(self):
        return self._free


class Talker:
    def __init__(self, out=None, brief="Morning. School till 3:30, then sax. It's Vedant's birthday."):
        self.out, self.brief_line, self.calls = out or {}, brief, []

    async def extract(self, instructions, text):
        self.calls.append(("extract", text))
        return self.out

    async def brief(self, facts):
        self.calls.append(("brief", facts))
        return self.brief_line


class Todoist:
    def __init__(self, tasks=()):
        self.tasks, self.queries = list(tasks), []

    async def list(self, query="today | overdue"):
        self.queries.append(query)
        return self.tasks


class Hands:
    def __init__(self, worlds):
        self.worlds = list(worlds)

    async def do(self, op, timeout=5.0, **a):
        from evie.hands import HandsResult
        import json
        return HandsResult(True, "ok", {"world": json.dumps(self.worlds.pop(0))})


class Now:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def cal():
    c = CalendarStore()
    c.update([CalEvent("School", at(8), at(15, 30), False, "Isaac", "school"),
              CalEvent("Sax Class", at(16, 45), at(18, 15), False, "Isaac", "sax"),
              CalEvent("Vedant Bday", DAY, DAY + timedelta(days=1), True, "Isaac", "bday"),
              CalEvent("Physics worksheet", at(23, 59, day=2), at(23, 59, day=2), False, "Grade 11IB _Physics_2026-27",
                       "pw")], at(7))
    return c


def sources(now, engine=None, talker=None, todoist=None, hands=None, calendar=None):
    e = engine or Engine()
    s = Sources(e, calendar or cal(), todoist or Todoist(), talker or Talker(), hands=hands,
                now=now, clock=lambda: now().timestamp())
    return s, e


async def test_overheard_dentist_becomes_a_what_time_question():
    t = Talker({"what": "the dentist", "day": "Wednesday", "time": ""})
    s, e = sources(Now(at(16)), talker=t)
    await s.overheard("mom i've got the dentist on wednesday can you drive me")
    it = e.items[0]
    assert it.kind == "overheard" and it.ask and it.line == "Heard you've got the dentist on Wednesday. What time?"
    assert it.request == "remember I have the dentist on Wednesday"
    assert "mom" not in it.line + it.request + str(it.on_yes)  # the sentence itself is never kept


async def test_overheard_with_a_time_offers_the_calendar():
    t = Talker({"what": "a physics test", "day": "Friday", "time": "9am"})
    s, e = sources(Now(at(16)), talker=t)
    await s.overheard("i have a physics test on friday at 9am so i cant")
    it = e.items[0]
    assert not it.ask and it.on_yes == {"do": "turn", "text": "remember I have a physics test on Friday at 9am"}


async def test_overheard_nothing_real_adds_nothing():
    s, e = sources(Now(at(16)), talker=Talker({"what": "", "day": "", "time": ""}))
    await s.overheard("yeah it was fine")
    assert e.items == []


async def test_heads_up_fifteen_minutes_before_not_for_school():
    now = Now(at(7, 50))
    s, e = sources(now)
    await s.collect()
    ups = lambda: [i for i in e.items if i.kind == "heads_up"]  # noqa: E731
    assert ups() == []  # School: no heads-up for the thing he does every day
    now.t = at(16, 32)
    await s.collect()
    assert [i.line for i in ups()] == ["Sax Class in 13 minutes."] and ups()[0].importance == "high"


async def test_task_nudge_after_school_and_in_the_evening():
    todo = Todoist([Task("1", "Email bio teacher", "today"), Task("2", "Physics revision", "today")])
    now = Now(at(14))
    s, e = sources(now, todoist=todo)
    await s.collect()
    assert not [i for i in e.items if i.kind == "tasks"]
    now.t = at(15, 50)
    await s.collect()
    await s.collect()
    nudges = [i for i in e.items if i.kind == "tasks"]
    assert len(nudges) == 1 and nudges[0].line == "Email bio teacher is due today, plus 1 more. Want help getting it done?"
    assert nudges[0].on_yes == {"do": "job", "goal": "help me get this done: Email bio teacher"}
    now.t = at(19, 40)
    await s.collect()
    assert len([i for i in e.items if i.kind == "tasks"]) == 1  # the same two tasks aren't offered again


async def test_deadline_radar_flags_things_due_in_two_days_once():
    todo = Todoist([Task("9", "IA draft", "Sep 26", date="2026-09-26"), Task("8", "Today thing", "today",
                                                                          date="2026-09-24")])
    now = Now(at(16))
    s, e = sources(now, todoist=todo)
    await s.collect()
    lines = sorted(i.line for i in e.items if i.kind == "deadline")
    assert lines == ["IA draft is due Saturday. Want me to help you start it?",
                     "Physics worksheet is due Saturday. Want me to help you start it?"]
    await s.collect()
    assert len([i for i in e.items if i.kind == "deadline"]) == 2


async def test_morning_brief_once_on_the_first_activity_after_five():
    now = Now(at(4, 30))
    t = Talker()
    s, e = sources(now, talker=t, todoist=Todoist([Task("1", "Email bio teacher", "today")]))
    await s.activity("active")
    assert not e.items
    now.t = at(6, 45)
    await s.activity("active")
    await s.activity("active")
    briefs = [i for i in e.items if i.kind == "brief"]
    assert len(briefs) == 1 and briefs[0].importance == "high"
    facts = t.calls[-1][1]
    assert "Sax Class" in facts["today"] and "Email bio teacher" in facts["due"] and "Vedant Bday" in facts["today"]


async def test_a_job_finishing_while_he_is_busy_waits():
    s, e = sources(Now(at(16)), engine=Engine(free=False))
    assert s.job_done("fix the deploy", "Fixed it: a missing env var.") is True
    it = e.items[0]
    assert it.line == "Done: fix the deploy. Want the summary?" and it.on_yes["text"].startswith("Fixed it")
    s2, e2 = sources(Now(at(16)), engine=Engine(free=True))
    assert s2.job_done("fix the deploy", "Fixed.") is False and e2.items == []


def test_stuck_on_the_same_error_for_ten_minutes_is_a_quiet_chip():
    now = Now(at(16))
    s, e = sources(now)
    err = "$ uv run pytest\nE   AssertionError: expected 3 got 4\nFAILED tests/test_x.py::test_a - AssertionError"
    s.screen_text("Terminal", err)
    now.t += timedelta(minutes=5)
    s.activity_now()
    s.screen_text("Terminal", err.replace("3 got 4", "5 got 6"))  # the numbers change, same error
    assert not e.items
    now.t += timedelta(minutes=6)
    s.activity_now()
    s.screen_text("Terminal", err)
    it = e.items[0]
    assert it.kind == "stuck" and it.chip_only and "AssertionError" in it.on_yes["goal"]
    s.screen_text("Safari", err)  # not a dev app: ignored
    assert len(e.items) == 1


def test_stuck_needs_him_to_be_there():
    now = Now(at(16))
    s, e = sources(now)
    err = "Traceback (most recent call last):\nValueError: bad"
    s.screen_text("Terminal", err)
    now.t += timedelta(minutes=11)
    s.screen_text("Terminal", err)  # he walked away: no activity for 11 minutes
    assert not e.items


async def test_where_was_i_offers_what_he_had_open():
    before = {"front_app": "Safari", "apps": ["Safari", "Code"], "windows": [],
              "tabs": [{"window": 1, "index": 1, "current": True, "title": "IB Chem notes", "url": "https://notes/chem"},
                       {"window": 1, "index": 2, "current": False, "title": "YouTube", "url": "https://youtube.com"}]}
    after = {"front_app": "Finder", "apps": ["Finder"], "windows": [], "tabs": []}
    now = Now(at(16))
    s, e = sources(now, hands=Hands([before, after]))
    await s.activity("idle")
    now.t += timedelta(minutes=25)
    await s.activity("back")
    it = e.items[0]
    assert it.kind == "resume" and it.chip_only and it.line == "Pick up where you left off? IB Chem notes and 1 more tab."
    assert it.on_yes == {"do": "reopen", "urls": ["https://notes/chem", "https://youtube.com"], "app": "Safari"}


async def test_where_was_i_skips_a_short_break_or_nothing_closed():
    same = {"front_app": "Safari", "apps": ["Safari"], "windows": [],
            "tabs": [{"window": 1, "index": 1, "current": True, "title": "Notes", "url": "https://n"}]}
    now = Now(at(16))
    s, e = sources(now, hands=Hands([same, same, same, same]))
    await s.activity("idle")
    now.t += timedelta(minutes=5)
    await s.activity("back")
    await s.activity("idle")
    now.t += timedelta(minutes=30)
    await s.activity("back")
    assert not e.items


async def test_a_switched_off_source_adds_nothing():
    s, e = sources(Now(at(16, 32)))
    s.enabled = lambda name: name != "heads_up"
    await s.collect()
    assert not [i for i in e.items if i.kind == "heads_up"]



async def test_the_evening_nudge_skips_what_was_already_offered():
    """Sim day: "Email bio teacher is due today" was offered after school AND in the evening."""
    todo = Todoist([Task("1", "Email bio teacher", "today")])
    now = Now(at(15, 50))
    s, e = sources(now, todoist=todo)
    await s.collect()
    now.t = at(19, 40)
    await s.collect()
    assert len([i for i in e.items if i.kind == "tasks"]) == 1
    todo.tasks.append(Task("2", "Physics revision", "today"))
    now.t = at(20, 10)
    s._asked.discard(f"tasks:{at(19,40).date()}:1930")
    await s.collect()
    assert [i.line for i in e.items if i.kind == "tasks"][-1] == "Physics revision is due today. Want help getting it done?"


async def test_presence_follows_the_app():
    s, e = sources(Now(at(16)), hands=Hands([{}, {}]))
    assert s.present
    await s.activity("idle")
    assert not s.present
    await s.activity("active")
    assert s.present and s._away is None  # waking the Mac in the morning isn't "where was I?"


def test_the_done_line_names_the_job_briefly():
    """2026-09-24 18:33: "That job's done: help me get this done: Understand Jev and how it works." """
    s, e = sources(Now(at(16)), engine=Engine(free=False))
    s.job_done("help me get this done: Understand Jev and how it works", "Jev is ...")
    s.job_done("Can you go to Netflix, click on the account name Darrell and then play The Mentalist please?", "Playing.")
    assert [i.line for i in e.items] == ["Done: Understand Jev and how it works. Want the summary?",
                                        "Done: go to Netflix. Want the summary?"]


def test_a_job_that_kept_him_posted_isnt_offered_again():
    """18:35: "Want the summary?" three minutes after it had said "Enjoy the show"."""
    s, e = sources(Now(at(16)), engine=Engine(free=False))
    assert s.job_done("play The Mentalist", "Playing.", told=True) is True and e.items == []
