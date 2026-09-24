"""Phase 4 T4: text-only mode. Isaac, 2026-09-24: "when I'm in school I don't want it to give answers out
loud"; "depending on the calendar ... if I have school or a chem or like Spanish ... and a toggle as well"."""
import asyncio
from datetime import datetime, timedelta

import numpy as np

from evie.calendar_store import TZ, CalendarStore, CalEvent
from evie.countdown import Countdown
from evie.quiet import Quiet
from evie.voice import Mouth

DAY = datetime(2026, 9, 24, tzinfo=TZ)


def at(h, m=0):
    return DAY.replace(hour=h, minute=m)


def cal():
    c = CalendarStore()
    c.update([CalEvent("School", at(8), at(15, 30), False, "Isaac", "school"),
              CalEvent("Sax Class", at(16, 45), at(18, 15), False, "Isaac", "sax"),
              CalEvent("Chem Class", at(20), at(21), False, "Isaac", "chem"),
              CalEvent("Vedant Bday", DAY, DAY + timedelta(days=1), True, "Isaac", "bday"),
              CalEvent("Dinner with family", at(19), at(19, 45), False, "Isaac", "dinner")], at(7))
    return c


class Now:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def test_classes_on_the_calendar_mean_text():
    now = Now(at(9))
    q = Quiet(cal(), now=now)
    assert q.mode() == "text" and q.why() == "School"
    now.t = at(15, 45)
    assert q.mode() == "voice"
    now.t = at(17)
    assert q.mode() == "text" and q.why() == "Sax Class"
    now.t = at(19, 10)  # dinner isn't a class, the birthday is all day
    assert q.mode() == "voice"


def test_a_call_means_text():
    q = Quiet(cal(), now=Now(at(16)), in_call=lambda: True)
    assert q.mode() == "text" and q.why() == "a call"


def test_switching_by_hand_lasts_until_the_class_ends():
    now = Now(at(20, 10))
    q = Quiet(cal(), now=now)
    q.set("voice")
    assert q.mode() == "voice"
    now.t = at(20, 50)
    assert q.mode() == "voice"
    now.t = at(21, 5)  # Chem ended: back to auto
    assert q.state()["setting"] == "auto"
    now.t = at(8, 30)
    assert q.mode() == "text"


def test_switching_to_text_with_no_class_lasts_until_switched_back():
    now = Now(at(16))
    q = Quiet(cal(), now=now)
    q.set("text")
    now.t = at(23)
    assert q.mode() == "text" and q.why() == "you switched it"
    q.set("auto")
    assert q.mode() == "voice"


class Voice:
    rate = 24000

    def chunks(self, text):
        yield np.zeros(10, dtype=np.float32)


class Out:
    def __init__(self):
        self.played = []

    def play(self, chunks, cancel, on_start=None):
        self.played.append(list(chunks))
        return True


async def test_text_mode_is_silent_on_every_path():
    out, texts = Out(), []
    quiet = {"on": True}
    m = Mouth(Voice(), out, text_only=lambda: quiet["on"], on_text=lambda t, k: texts.append((t, k)),
              clips={"on_it": np.zeros(5, dtype=np.float32)})
    m.start()
    m.say("It's 6.022 times ten to the 23.")
    m.say("Step 2 of 3, testing the fix.", kind="narration")
    m.say("Checking why the deploy failed. Say stop if that's wrong.", ttl_s=5)
    m.play_clip("on_it")
    await asyncio.sleep(0.05)
    assert out.played == [] and [t for t, _ in texts] == [
        "It's 6.022 times ten to the 23.", "Step 2 of 3, testing the fix.",
        "Checking why the deploy failed. Say stop if that's wrong.", "On it."]
    quiet["on"] = False
    m.say("Back to talking.")
    await asyncio.sleep(0.05)
    assert len(out.played) == 1
    await m.aclose()


async def test_countdowns_are_longer_in_text_mode_because_he_has_to_tap_cancel():
    quiet = {"on": True}
    starts = []
    cd = Countdown(seconds=0.01, text_s=lambda: 0.05 if quiet["on"] else 0, on_start=starts.append)
    t0 = asyncio.get_running_loop().time()
    assert await cd.wait()
    assert asyncio.get_running_loop().time() - t0 >= 0.045 and starts == [0.05]
    quiet["on"] = False
    assert await cd.wait() and starts[-1] == 0.01


def test_the_open_mic_pauses_in_class():
    from evie.open_mic import ModeStore, OpenMic

    class Seg:
        active = False

        def __init__(self):
            self.fed = 0

        def feed(self, frame, speech):
            self.fed += 1
            return []

        def reset(self):
            pass

    class Modes(ModeStore):
        def __init__(self):
            self.mode = "live"

    class Bus:
        def __init__(self):
            self.events = []

        def publish(self, kind, **d):
            self.events.append((kind, d))

    seg, bus = Seg(), Bus()
    paused = {"why": "Chem Class"}
    mic = OpenMic(seg, lambda f: True, None, None, None, None, bus, Modes())
    mic.paused = lambda: paused["why"]
    mic.feed(np.zeros(512, dtype=np.float32))
    mic.feed(np.zeros(512, dtype=np.float32))
    assert seg.fed == 0 and [e for e in bus.events if e[0] == "mic_paused"] == [("mic_paused", {"why": "Chem Class"})]
    paused["why"] = None
    mic.feed(np.zeros(512, dtype=np.float32))
    assert seg.fed == 1 and ("mic_paused", {"why": None}) in bus.events


def test_text_mode_lines_reach_the_orb_through_the_real_bus():
    """The first version passed kind= to EventBus.publish, whose own first argument is `kind`:
    every reply in text mode would have raised TypeError."""
    from evie.events import EventBus
    from evie.server import text_to_orb
    bus = EventBus()
    q = bus.subscribe()
    text_to_orb(bus)("Avogadro's constant is 6.022e23.", "reply")
    ev = q.get_nowait()
    assert ev["kind"] == "say" and ev["text_only"] and ev["line_kind"] == "reply"


def test_busy_with_something_that_isnt_a_class():
    now = Now(at(19, 10))
    q = Quiet(cal(), now=now)
    assert q.busy_event() == "Dinner with family" and q.mode() == "voice"
    now.t = at(17)
    assert q.busy_event() is None  # a class is text mode, not "busy"
    now.t = at(22)
    assert q.busy_event() is None


def test_in_text_mode_say_stop_becomes_tap_cancel():
    from evie.events import EventBus
    from evie.server import text_to_orb
    bus = EventBus()
    q = bus.subscribe()
    text_to_orb(bus)("Sending hi to Mom. Say stop to cancel.", "reply")
    text_to_orb(bus)("Checking why the deploy failed. Say stop if that's wrong.", "reply")
    assert q.get_nowait()["text"] == "Sending hi to Mom. Tap Cancel to stop it."
    assert q.get_nowait()["text"] == "Checking why the deploy failed. Tap Cancel if that's wrong."
