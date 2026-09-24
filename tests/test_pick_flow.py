"""Phase 4 T2 in the Brain: "a MrBeast video" -> she opens his videos and asks which one -> Isaac's
answer (or a tap on the orb) plays it, with no second question and no new plan."""
import asyncio

from evie.computer.planner import Outcome as CO
from tests.test_brain import Clock, SkillSB, brain_c

ROWS = [{"id": "m2", "label": "$1 vs $1,000,000 Hotel Room"}, {"id": "m3", "label": "I Survived 7 Days"},
        {"id": "m4", "label": "Last To Leave The Island Wins"}]
PICK = {"goal": "open a mrbeast video", "rows": ROWS}


class PickingComputer:
    def __init__(self):
        self.goals, self.choices = [], []

    async def run(self, text, skill=None):
        self.goals.append(text)
        return CO(False, "Which one? $1 vs $1,000,000 Hotel Room, I Survived 7 Days, or Last To Leave The "
                         "Island Wins.", ask=True, options=ROWS, pick=PICK)

    async def choose(self, pick, answer, eid=None):
        self.choices.append((pick, answer, eid))
        label = next(r["label"] for r in ROWS if r["id"] == (eid or "m2"))
        return CO(True, f"Playing {label}.")


async def asked(clock=None):
    b, p = brain_c(SkillSB("computer"), clock=clock or Clock())
    b._computer = PickingComputer()
    q = p["bus"].subscribe()
    await b.hear("evie open a mrbeast video")
    await asyncio.sleep(0.02)
    return b, p, q


async def test_she_shows_the_options_and_waits():
    b, p, q = await asked()
    assert p["mouth"].said[-1].startswith("Which one?")
    assert b._pending.kind == "pick" and b._pending.data is PICK
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    opts = [e for e in events if e.get("kind") == "options"]
    assert opts and [o["label"] for o in opts[-1]["options"]] == [r["label"] for r in ROWS]


async def test_his_answer_finishes_it_with_no_new_plan():
    b, p, q = await asked()
    out = await b.hear("the latest one")
    await asyncio.sleep(0.02)
    assert b._computer.choices == [(PICK, "the latest one", None)]
    assert b._computer.goals == ["open a mrbeast video"]  # still the one run
    assert p["mouth"].said[-1] == "Playing $1 vs $1,000,000 Hotel Room."
    assert out["reason"] == "choice" and b._pending is None


async def test_saying_her_name_first_still_counts_as_the_answer():
    b, p, q = await asked()
    await b.hear("evie the island one")
    await asyncio.sleep(0.02)
    assert b._computer.choices and b._computer.choices[0][1] == "the island one"


async def test_a_tap_on_the_orb_picks_that_row():
    b, p, q = await asked()
    out = await b.choose_option("m4")
    await asyncio.sleep(0.02)
    assert b._computer.choices == [(PICK, None, "m4")]
    assert p["mouth"].said[-1] == "Playing Last To Leave The Island Wins."
    assert out["ok"] and b._pending is None


async def test_a_tap_with_nothing_waiting_does_nothing():
    b, p, q = await asked()
    b._pending = None
    out = await b.choose_option("m4")
    assert not out["ok"] and b._computer.choices == []


async def test_the_list_waits_two_minutes_not_fifteen_seconds():
    clock = Clock()
    b, p, q = await asked(clock)
    clock.t += 60
    await b.hear("the second one")
    await asyncio.sleep(0.02)
    assert b._computer.choices, "still waiting after a minute"
    b2, p2, q2 = await asked(clock)
    clock.t += 121
    await b2.hear("the second one")
    await asyncio.sleep(0.02)
    assert not b2._computer.choices
