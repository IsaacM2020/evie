"""Phase 4 T1: "play that song" knows which song, and a question gets asked once at most.
The flows here are the real transcripts from Isaac's friends' test on 2026-09-24 (turns.jsonl 14:09)."""
import asyncio

from evie.brain import needs_resolve
from evie.switchboard import Outcome
from evie.switchboard.decision import Decision
from evie.switchboard.policy import Action, Thresholds, Verdict, decide
from tests.test_brain import FakeSkills, FakeSwitchboard, FakeTalker, SeqSwitchboard, brain_c, skill_outcome


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class ResolvingTalker(FakeTalker):
    def __init__(self, resolved="play Trance by Travis Scott on Spotify", fail=False, delay=0.0):
        super().__init__()
        self.resolved, self.fail, self.delay = resolved, fail, delay

    async def resolve(self, text, recent):
        self.calls.append(("resolve", text, tuple(recent)))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("groq down")
        return self.resolved


class ScriptSwitchboard(FakeSwitchboard):
    """Each call returns the next outcome-maker: 'ignore' (overheard chatter) or a skill name."""

    def __init__(self, *script):
        super().__init__()
        self.script = list(script)

    async def handle(self, ctx):
        self.contexts.append(ctx)
        step = self.script.pop(0)
        if step == "ignore":
            d = Decision(0.05, "not_for_evie", 0.9, {"not_for_evie": 0.9}, 0.9, 0.0, 300.0, 0.0)
            return Outcome(ctx, d, Verdict(Action.IGNORE, "not for Evie"))
        return skill_outcome(ctx, step)


def trance_brain(talker=None, clock=None):
    sb = ScriptSwitchboard("ignore", "music_play")
    b, p = brain_c(sb, clock=clock or Clock())
    b._talker = talker or ResolvingTalker()
    b._skills = FakeSkills(said="Playing Trance by Metro Boomin.")
    return b, p, sb


def test_needs_resolve_only_with_a_reference():
    for t in ("play that song on spotify", "open it", "send it to him", "play the one from before",
              "open this", "do the same again"):
        assert needs_resolve(t), t
    for t in ("play trance by travis scott", "what time is it", "is it going to rain", "it's so hot",
              "open safari"):
        assert not needs_resolve(t), t


async def test_that_song_is_resolved_from_what_was_just_said():
    b, p, sb = trance_brain()
    await b.hear("trance, travis scott", addressed=False)
    out = await b.hear("play that song on spotify")
    assert b._skills.runs == [("music_play", "play Trance by Travis Scott on Spotify")]
    resolve = [c for c in b._talker.calls if c[0] == "resolve"]
    assert resolve and any("trance, travis scott" in r for r in resolve[0][2])
    assert out["said"] == "Playing Trance by Metro Boomin."
    # Jev still judged his real words
    assert sb.contexts[1].utterance == "play that song on spotify"


async def test_nothing_to_resolve_from_after_two_minutes():
    clock = Clock()
    b, p, sb = trance_brain(clock=clock)
    await b.hear("trance, travis scott", addressed=False)
    clock.t += 121
    await b.hear("play that song on spotify")
    assert not [c for c in b._talker.calls if c[0] == "resolve"]
    assert b._skills.runs == [("music_play", "play that song on spotify")]


async def test_resolve_failing_or_slow_uses_his_words():
    for talker in (ResolvingTalker(fail=True), ResolvingTalker(delay=5.0)):
        b, p, sb = trance_brain(talker=talker)
        b.RESOLVE_WAIT_S = 0.05
        await b.hear("trance, travis scott", addressed=False)
        await b.hear("play that song on spotify")
        assert b._skills.runs == [("music_play", "play that song on spotify")]


async def test_others_voices_are_not_used_to_resolve():
    b, p, sb = trance_brain()
    await b.hear("trance, travis scott", speaker="other", addressed=False)
    await b.hear("play that song on spotify")
    assert not [c for c in b._talker.calls if c[0] == "resolve"]


async def test_one_question_per_request():
    sb = SeqSwitchboard(("clarify", "missing detail", "quick_action"), ("act", "answer", "answer"))
    b, p = brain_c(sb)
    await b.hear("evie play that song")
    await b.hear("trance")
    assert not sb.contexts[0].answered
    assert sb.contexts[1].answered  # the merged request can't be sent back for "missing detail"


def test_policy_acts_once_he_has_answered():
    d = Decision(0.9, "quick_action", 1.0, {"quick_action": 1.0}, 0.1, 0.0, 300.0, 0.0)
    assert decide(d, "isaac", Thresholds(), addressed=True).reason == "missing detail"
    v = decide(d, "isaac", Thresholds(), addressed=True, answered=True)
    assert v.action == Action.ACT and v.reason == "quick_action"


async def test_her_question_knows_the_conversation():
    sb = SeqSwitchboard(("ignore", "not for Evie", "not_for_evie"), ("clarify", "missing detail", "quick_action"))
    b, p = brain_c(sb)
    await b.hear("we were listening to trance earlier", addressed=False)
    await b.hear("evie play that song")
    clar = [c for c in p["talker"].calls if c[0] == "clarify"]
    assert clar and any("listening to trance" in r for r in clar[0][3])
