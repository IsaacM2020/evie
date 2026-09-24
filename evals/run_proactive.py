"""Phase 4/5 gate: a simulated day of Evie's proactive side (no models, no network, runs in a second).

    uv run python -m evals.run_proactive

His real Thursday: School 8:00-15:30, Sax 16:45-18:15, Chem 20:00-21:00, plus talking to people, a call,
tasks due today and in two days, a job that finishes mid-conversation, a stuck error, a break. The
real Engine + Sources + Quiet run on a fake clock, one tick every 20 s from 06:00 to 23:30.

Gates: 0 lines spoken in class, in a call or while someone is talking; <= 3 spoken in any hour;
nothing spoken in quiet hours; every follow-up is brought up (spoken or as a chip) exactly once.
"""
import asyncio
import json
import sys
import tempfile
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from evie.calendar_store import TZ, CalendarStore, CalEvent
from evie.jev import JevResult
from evie.proactive.engine import Engine
from evie.proactive.queue import FollowUps
from evie.proactive.sources import Sources
from evie.quiet import Quiet
from evie.remember import Task

DAY = datetime(2026, 9, 24, tzinfo=TZ)


def at(h, m=0, day=0):
    return (DAY + timedelta(days=day)).replace(hour=h, minute=m)


CALENDAR = [CalEvent("School", at(8), at(15, 30), False, "Isaac", "school"),
            CalEvent("Sax Class", at(16, 45), at(18, 15), False, "Isaac", "sax"),
            CalEvent("Dinner with family", at(19, 30), at(20), False, "Isaac", "dinner"),
            CalEvent("Chem Class", at(20), at(21), False, "Isaac", "chem"),
            CalEvent("Vedant Bday", DAY, DAY + timedelta(days=1), True, "Isaac", "bday"),
            CalEvent("Physics worksheet", at(23, 59, day=2), at(23, 59, day=2), False, "Grade 11IB _Physics_2026-27", "pw")]
TALKING = [(at(7, 10), at(7, 25)), (at(15, 40), at(16, 5)), (at(18, 20), at(18, 40)), (at(21, 30), at(21, 50))]
CALL = (at(18, 50), at(19, 20))
OVERHEARD = [(at(15, 50), {"what": "the dentist", "day": "Wednesday", "time": ""}),
             (at(18, 30), {"what": "a physics test", "day": "Friday", "time": "9am"})]
JOB_DONE = at(15, 45)  # mid-conversation: must wait
STUCK = (at(21, 55), at(22, 20))  # the same error on screen while he's there (then quiet hours)
AWAY = (at(10, 0), at(10, 40))  # (in school: "where was I?" must be a chip)
DINNER = (at(19, 30), at(20))


class Clock:
    def __init__(self):
        self.t = at(6)

    def now(self):
        return self.t

    def ts(self):
        return self.t.timestamp()


class Mouth:
    def __init__(self, clock):
        self.clock, self.spoken, self.speaking = clock, [], False

    def say(self, text, kind="reply", ttl_s=None, clip=None):
        self.spoken.append((self.clock.now(), text))


class Bus:
    def __init__(self, clock):
        self.clock, self.chips = clock, []

    def publish(self, kind, **d):
        if kind == "followup" and not d.get("spoken"):
            self.chips.append((self.clock.now(), d["line"]))


class Jev:
    async def ask(self, state, questions):
        return JevResult({"now": {"type": "noul", "noul": 0.8}}, 1.0, 0.0)


class Talker:
    def __init__(self, clock):
        self.clock = clock

    async def extract(self, instructions, text):
        return next(q for t, q in OVERHEARD if t == self.clock.now())

    async def brief(self, facts):
        return "Morning. School till 3:30, sax at 4:45, chem at 8. It's Vedant's birthday."


class Todoist:
    async def list(self, query="today | overdue"):
        if "3 days" in query:
            return [Task("9", "IA draft", "Sep 26", date="2026-09-26")]
        return [Task("1", "Email bio teacher", "today", date="2026-09-24")]


class Hands:
    def __init__(self):
        self.worlds = [{"front_app": "Safari", "tabs": [{"url": "https://notes/chem", "title": "Chem notes", "current": True}]},
                       {"front_app": "Finder", "tabs": []}]

    async def do(self, op, timeout=5.0, **a):
        from evie.hands import HandsResult
        return HandsResult(True, "ok", {"world": json.dumps(self.worlds.pop(0) if self.worlds else {})})


def within(t, span):
    return span[0] <= t < span[1]


async def main() -> None:
    clock = Clock()
    cal = CalendarStore()
    cal.update(CALENDAR, at(6))
    last_talk = {"t": at(5)}
    quiet = Quiet(cal, in_call=lambda: within(clock.now(), CALL), now=clock.now)
    mouth, bus = Mouth(clock), Bus(clock)
    tmp = Path(tempfile.mkdtemp())
    idle = lambda: (clock.now() - last_talk["t"]).total_seconds()  # noqa: E731
    engine = Engine(FollowUps(tmp / "f.json", clock=clock.ts), mouth, bus, Jev(), act=None, idle_s=idle,
                    text_mode=lambda: quiet.mode() == "text", in_call=lambda: within(clock.now(), CALL),
                    hour=lambda: clock.now().hour + clock.now().minute / 60, clock=clock.ts)
    src = Sources(engine, cal, Todoist(), Talker(clock), hands=Hands(), now=clock.now, clock=clock.ts,
                  text_mode=lambda: quiet.mode() == "text")
    engine.present = lambda: src.present
    engine.busy_event = lambda: quiet.busy_event() is not None
    src.present = False  # asleep until he opens the laptop at 6:45
    added = Counter()
    real_add = engine.add

    def counting_add(it):
        ok = real_add(it)
        if ok:
            added[it.source_key] += 1
        return ok
    engine.add = counting_add
    err = "E   AssertionError\nFAILED tests/test_chase.py::test_target - AssertionError"
    was_away = False
    while clock.now() < at(23, 30):
        t = clock.now()
        if any(within(t, s) for s in TALKING):
            last_talk["t"] = t
        if t == at(6, 45):
            await src.activity("active")
        for when, _ in OVERHEARD:
            if t == when:
                last_talk["t"] = t
                await src.overheard("(a sentence to someone else)")
        if t == JOB_DONE and not src.job_done("fix the chase bug", "Fixed: the chase target was off by one."):
            mouth.say("Fixed: the chase target was off by one.")  # the narrator would have said it
        if within(t, STUCK):
            src.activity_now()
            src.screen_text("Terminal", err)
        away = within(t, AWAY)
        if away != was_away:
            await src.activity("idle" if away else "back")
            was_away = away
        await src.collect()
        await engine.tick()
        clock.t += timedelta(seconds=20)

    fails = []
    for when, text in mouth.spoken:
        if quiet.current_class() is None:
            clock.t = when
        clock.t = when
        cls = quiet.current_class()
        if cls:
            fails.append(f"spoke in {cls.title} at {when:%H:%M}: {text}")
        if within(when, CALL):
            fails.append(f"spoke in a call at {when:%H:%M}: {text}")
        if within(when, DINNER) and "in 15 minutes" not in text:
            fails.append(f"spoke at dinner at {when:%H:%M}: {text}")
        if any(within(when, s) for s in TALKING) and text != "Fixed: the chase target was off by one.":
            fails.append(f"spoke over a conversation at {when:%H:%M}: {text}")
        if (when.hour, when.minute) >= (22, 30) or when.hour < 6:
            fails.append(f"spoke in quiet hours at {when:%H:%M}: {text}")
    per_hour = Counter(w.strftime("%H") for w, t in mouth.spoken if "in 15 minutes" not in t)
    fails += [f"{n} spoken in hour {h}" for h, n in per_hour.items() if n > 3]
    offered = Counter(line for _, line in mouth.spoken) + Counter(line for _, line in bus.chips)
    fails += [f"offered {n}x: {line}" for line, n in offered.items() if n > 1]
    waiting = [i.line for i in engine.queue.due()]
    fails += [f"never brought up: {w}" for w in waiting]

    print("SPOKEN")
    for w, text in mouth.spoken:
        print(f"  {w:%H:%M}  {text}")
    print("CHIPS")
    for w, text in bus.chips:
        print(f"  {w:%H:%M}  {text}")
    print(json.dumps({"spoken": len(mouth.spoken), "chips": len(bus.chips), "queued": sum(added.values()),
                      "max_per_hour": max(per_hour.values(), default=0)}), "PASS" if not fails else "FAIL")
    for f in fails:
        print("  FAIL", f)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    asyncio.run(main())
