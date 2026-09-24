"""Phase 4 T7: the proactive engine. She brings things up herself, but never mid-conversation, in a call,
in class (text chips instead), more than 3 times an hour, or two at once."""
import json

from evie.jev import JevResult
from evie.proactive.engine import Engine
from evie.proactive.queue import FollowUp, FollowUps


class Clock:
    def __init__(self, t=1_790_300_000.0):
        self.t = t

    def __call__(self):
        return self.t


class Mouth:
    def __init__(self):
        self.said, self.speaking, self.quiet_at = [], False, 0.0

    def say(self, text, kind="reply", ttl_s=None, clip=None):
        self.said.append(text)


class Bus:
    def __init__(self):
        self.events = []

    def publish(self, kind, **d):
        self.events.append((kind, d))

    def of(self, kind):
        return [d for k, d in self.events if k == kind]


class Jev:
    def __init__(self, p=0.9):
        self.p, self.calls = p, 0

    async def ask(self, state, questions):
        self.calls += 1
        return JevResult({"now": {"type": "noul", "noul": self.p}}, 200.0, 0.0)


def engine(tmp_path, clock=None, text=False, call=False, jev=None, busy_s=999.0, hour=15):
    clock = clock or Clock()
    state = {"text": text, "call": call, "busy": busy_s, "hour": hour}
    done = []

    async def act(item):
        done.append(item)

    e = Engine(FollowUps(tmp_path / "f.json", clock=clock), Mouth(), Bus(), jev or Jev(), act=act,
               idle_s=lambda: state["busy"], text_mode=lambda: state["text"], in_call=lambda: state["call"],
               hour=lambda: state["hour"], clock=clock)
    return e, state, done


def item(key="k1", importance="normal", **kw):
    return FollowUp(kind=kw.pop("kind", "task"), line=kw.pop("line", "Your bio email is due today. Want help?"),
                    source_key=key, importance=importance, **kw)


async def test_she_speaks_when_he_is_free(tmp_path):
    e, s, _ = engine(tmp_path)
    assert e.add(item())
    got = await e.tick()
    assert got and e.mouth.said == ["Your bio email is due today. Want help?"]


async def test_never_mid_conversation_or_in_a_call(tmp_path):
    e, s, _ = engine(tmp_path, busy_s=20)
    e.add(item())
    assert await e.tick() is None and e.mouth.said == []
    s["busy"], s["call"] = 999, True
    assert await e.tick() is None and e.mouth.said == []
    s["call"] = False
    e.mouth.speaking = True
    assert await e.tick() is None
    e.mouth.speaking = False
    assert await e.tick() is not None


async def test_in_class_it_is_a_chip_not_a_voice(tmp_path):
    e, s, _ = engine(tmp_path, text=True)
    e.add(item())
    await e.tick()
    assert e.mouth.said == [] and e.bus.of("followup")[0]["line"].startswith("Your bio email")


async def test_quiet_hours_hold_everything_until_morning(tmp_path):
    """Sim day: a chip at 6:00 is used up while he sleeps. It waits instead (quiet hours 22:30-06:00)."""
    e, s, _ = engine(tmp_path, hour=23)
    e.add(item())
    assert await e.tick() is None and e.mouth.said == [] and e.bus.of("followup") == []
    s["hour"] = 6.5
    assert await e.tick() is not None and e.mouth.said


async def test_at_most_three_spoken_an_hour_and_one_at_a_time(tmp_path):
    clock = Clock()
    e, s, _ = engine(tmp_path, clock=clock)
    for i in range(5):
        e.add(item(key=f"k{i}", line=f"thing {i}"))
    await e.tick()
    clock.t += 120
    assert await e.tick() is None  # one at a time: 5 min between things (sim day: 3 in 4 min was a barrage)
    for _ in range(4):
        clock.t += 300
        await e.tick()
    assert e.mouth.said == ["thing 0", "thing 1", "thing 2"]
    assert [c["line"] for c in e.bus.of("followup") if c.get("line")][-2:] == ["thing 3", "thing 4"]  # capped: chips


async def test_time_critical_things_skip_the_cap_and_the_long_gap(tmp_path):
    """Sim day: "Sax Class in 15 minutes" became a chip because three nudges had used up the hour."""
    clock = Clock()
    e, s, _ = engine(tmp_path, clock=clock)
    for i in range(3):
        e.add(item(key=f"k{i}", line=f"thing {i}"))
        await e.tick()
        clock.t += 301
    clock.t += 61 - 301  # a minute after the last one, inside the long gap, hour already capped
    e.add(item(key="sax", importance="high", line="Sax Class in 15 minutes."))
    await e.tick()
    assert e.mouth.said[-1] == "Sax Class in 15 minutes."


async def test_nothing_while_he_is_away(tmp_path):
    e, s, _ = engine(tmp_path)
    here = {"on": False}
    e.present = lambda: here["on"]
    e.add(item(importance="high"))
    assert await e.tick() is None and e.bus.of("followup") == []
    here["on"] = True
    assert await e.tick() is not None


async def test_during_dinner_only_time_critical_things_are_said(tmp_path):
    """Sim day: a task nudge was spoken at family dinner."""
    e, s, _ = engine(tmp_path)
    e.busy_event = lambda: True
    e.add(item())
    assert await e.tick() is None and e.mouth.said == []
    e.add(item(key="chem", importance="high", line="Chem Class in 15 minutes."))
    await e.tick()
    assert e.mouth.said == ["Chem Class in 15 minutes."]


async def test_the_same_thing_is_only_ever_offered_once(tmp_path):
    e, s, _ = engine(tmp_path)
    assert e.add(item()) and not e.add(item())
    await e.tick()
    e2 = Engine(FollowUps(tmp_path / "f.json"), Mouth(), Bus(), Jev(), act=None, idle_s=lambda: 999,
                text_mode=lambda: False, in_call=lambda: False)
    assert not e2.add(item())  # remembered across restarts


async def test_jev_can_hold_a_normal_thing_but_not_an_urgent_one(tmp_path):
    jev = Jev(p=0.1)
    e, s, _ = engine(tmp_path, jev=jev)
    e.add(item())
    assert await e.tick() is None and jev.calls == 1  # not now: later
    e.add(item(key="class", importance="high", line="Sax class in 15."))
    await e.tick()
    assert e.mouth.said == ["Sax class in 15."] and jev.calls == 1


async def test_expired_things_are_dropped(tmp_path):
    clock = Clock()
    e, s, _ = engine(tmp_path, clock=clock)
    e.add(item(expires=clock.t + 10))
    clock.t += 11
    assert await e.tick() is None and e.mouth.said == []


async def test_answers_yes_no_later(tmp_path):
    clock = Clock()
    e, s, done = engine(tmp_path, clock=clock, text=True)
    a = item(on_yes={"do": "job", "goal": "email my bio teacher"})
    e.add(a)
    await e.tick()
    await e.answer(a.id, "yes")
    assert done and done[0].on_yes["goal"] == "email my bio teacher"
    b = item(key="k2")
    e.add(b)
    await e.tick()
    await e.answer(b.id, "later")
    assert e.queue.get(b.id).due >= clock.t + 1500 and not e.queue.get(b.id).offered
    await e.answer(b.id, "no")
    assert e.queue.get(b.id) is None


async def test_overheard_raw_words_are_never_written(tmp_path):
    e, s, _ = engine(tmp_path)
    e.add(item(kind="overheard", line="Heard you've got the dentist Wednesday. What time?",
               request="remember I have the dentist on Wednesday"))
    stored = json.loads((tmp_path / "f.json").read_text())
    assert "mom" not in json.dumps(stored).lower()  # only what was extracted, never the sentence to mom


async def test_nothing_is_brought_up_before_the_calendar_has_arrived(tmp_path):
    """2026-09-24 16:57: right after a restart the calendar was empty, so class looked like free time and
    she nearly spoke a task nudge in Sax class (Jev happened to say 'not now')."""
    e, s, _ = engine(tmp_path)
    known = {"cal": False}
    e.ready = lambda: known["cal"]
    e.add(item())
    assert await e.tick() is None and e.mouth.said == [] and e.bus.of("followup") == []
    assert e.queue.due()[0].postponed == 0  # not held back by Jev, just waiting
    known["cal"] = True
    assert await e.tick() is not None
