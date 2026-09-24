"""Isaac, 2026-09-24: "it should ask for clarification when required, but it should also be autonomous".
She goes with the obvious best guess, says what she picked, and "no, the other one" moves to her next
guess (no model call, no question)."""
import asyncio

from evie.brain import is_other
from evie.computer.planner import Outcome as CO
from evie.skills.catalog import Done
from tests.test_brain import Clock, SkillSB, brain_c

OTHERS = [("spotify:track:q1", "Trance (Walk It Down) by Quavo"), ("spotify:track:x1", "X by Y")]


def test_the_other_one_is_spotted_in_code():
    for t in ("no, the other one", "the other one", "not that one", "wrong one", "no not that one",
              "a different one", "nah the other one evie", "evie, the other one", "different one please"):
        assert is_other(t), t
    for t in ("no thanks", "play the next song", "other than that what's the time", "no", "one more thing",
              "the other day i went out", "open the other tab"):
        assert not is_other(t), t


class MusicSkills:
    def __init__(self):
        self.runs, self.others = [], []

    async def run(self, skill, text):
        self.runs.append((skill, text))
        return Done("Playing Trance by Metro Boomin.", others=list(OTHERS))

    async def play_other(self, others):
        self.others.append(list(others))
        return Done(f"Playing {others[0][1]}.", others=list(others[1:]))


async def test_no_the_other_one_plays_her_next_song():
    b, p = brain_c(SkillSB("music_play"), clock=Clock())
    b._skills = sk = MusicSkills()
    await b.hear("evie play trance")
    out = await b.hear("no, the other one")
    assert sk.others == [OTHERS] and p["mouth"].said[-1] == "Playing Trance (Walk It Down) by Quavo."
    assert out["reason"] == "the other one"
    await b.hear("not that one either, a different one")
    assert sk.others[-1] == OTHERS[1:]
    assert len(sk.runs) == 1  # never searched again, never went to Jev's skill picker


async def test_the_other_one_is_forgotten_after_two_minutes():
    clock = Clock()
    b, p = brain_c(SkillSB("music_play"), clock=clock)
    b._skills = sk = MusicSkills()
    await b.hear("evie play trance")
    clock.t += 121
    await b.hear("no, the other one")
    assert sk.others == [] and len(sk.runs) == 2  # a normal turn


class ScreenComputer:
    def __init__(self):
        self.choices = []

    async def run(self, text, skill=None):
        return CO(True, "Opened AI model beats doctors.", pick={"rows": [{"id": "w3", "label": "Solar plan"},
                                                                          {"id": "w4", "label": "Ancient reef"}]})

    async def choose(self, pick, answer, eid=None):
        self.choices.append((answer, eid))
        return CO(True, "Opened Solar plan.")


async def test_no_the_other_one_opens_her_next_pick_on_screen():
    b, p = brain_c(SkillSB("computer"), clock=Clock())
    b._computer = c = ScreenComputer()
    await b.hear("evie open the most interesting bbc article")
    await asyncio.sleep(0.02)
    await b.hear("the other one")
    await asyncio.sleep(0.02)
    assert c.choices == [(None, "w3")] and p["mouth"].said[-1] == "Opened Solar plan."
    # and the one after that is still there
    assert b._last_pick[1]["rows"] == [{"id": "w4", "label": "Ancient reef"}]
