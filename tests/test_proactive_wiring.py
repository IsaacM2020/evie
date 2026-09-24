"""Phase 4 T7/T8: the Brain's side of proactive follow-ups."""
import asyncio

from evie.proactive.queue import FollowUp
from evie.switchboard import Outcome
from evie.switchboard.decision import Decision
from evie.switchboard.policy import Action, Verdict
from tests.test_brain import FakeSwitchboard, SeqSwitchboard, brain_c


class OverheardSB(FakeSwitchboard):
    async def handle(self, ctx):
        self.contexts.append(ctx)
        d = Decision(0.02, "not_for_evie", 0.9, {"not_for_evie": 0.9}, 0.3, 0.95, 300.0, 0.0)
        return Outcome(ctx, d, Verdict(Action.IGNORE, "not for Evie", followup=True))


class Sources:
    def __init__(self):
        self.heard = []

    async def overheard(self, text):
        self.heard.append(text)


class Engine:
    def __init__(self):
        self.answers = []

    async def answer(self, fid, action, text=None):
        self.answers.append((fid, action, text))
        return True


async def test_an_overheard_plan_goes_to_the_follow_up_sources():
    b, p = brain_c(OverheardSB())
    b.proactive = src = Sources()
    await b.hear("mom ive got the dentist on wednesday", addressed=False)
    await asyncio.sleep(0.01)
    assert src.heard == ["mom ive got the dentist on wednesday"]
    await b.hear("mom ive got the dentist on wednesday", speaker="other", addressed=False)
    await asyncio.sleep(0.01)
    assert len(src.heard) == 1  # someone else's plans aren't his


async def test_a_spoken_what_time_question_is_answered_like_any_question():
    sb = SeqSwitchboard(("act", "remember", "remember"))
    b, p = brain_c(sb)
    b.engine = Engine()
    b.expect_followup(FollowUp("overheard", "Heard you've got the dentist on Wednesday. What time?", "k", ask=True,
                               request="remember I have the dentist on Wednesday"))
    await b.hear("4pm")
    assert sb.contexts[0].utterance == ('remember I have the dentist on Wednesday. Evie asked "Heard you\'ve got the '
                                        'dentist on Wednesday. What time?", Isaac answered "4pm".')
    assert sb.contexts[0].answered


async def test_yes_to_her_offer_runs_it_and_no_drops_it():
    b, p = brain_c(FakeSwitchboard())
    b.engine = eng = Engine()
    offer = FollowUp("tasks", "Your bio email is due today. Want help?", "k", on_yes={"do": "job", "goal": "x"})
    b.expect_followup(offer)
    out = await b.hear("yeah sure")
    assert eng.answers == [(offer.id, "yes", None)] and out["reason"] == "follow-up"
    b.expect_followup(offer)
    await b.hear("nah")
    assert eng.answers[-1] == (offer.id, "no", None)


async def test_doing_a_follow_up():
    b, p = brain_c(SeqSwitchboard(("act", "remember", "remember")))
    said = []
    b._say = lambda t: said.append(t) or t
    await b.do_followup(FollowUp("job", "done?", "k", on_yes={"do": "say", "text": "Fixed: a missing env var."}))
    assert said == ["Fixed: a missing env var."]
    await b.do_followup(FollowUp("overheard", "x", "k2", on_yes={"do": "turn", "text": "remember I have a test friday at 9"}))
    assert b._sb.contexts[-1].utterance == "remember I have a test friday at 9" and b._sb.contexts[-1].answered
    started = []

    async def fake_job(text, **kw):
        started.append(text)
        return "ok"
    b._start_job = fake_job
    await b.do_followup(FollowUp("tasks", "x", "k3", on_yes={"do": "job", "goal": "help me with the bio email"}))
    assert started == ["help me with the bio email"]


async def test_idle_seconds_counts_speech_near_her():
    from tests.test_brain import Clock
    clock = Clock()
    b, p = brain_c(FakeSwitchboard("ignore", "not for Evie", "not_for_evie"), clock=clock)
    clock.t += 500
    assert b.idle_s() >= 500
    await b.hear("so anyway", addressed=False)
    clock.t += 10
    assert 9 <= b.idle_s() <= 11


async def test_answering_her_spoken_question_clears_its_chip():
    """Self-review: the chip for "What time?" stayed on the orb for 8 h after he answered by voice."""
    from evie.proactive.engine import Engine
    from evie.proactive.queue import FollowUps
    import tempfile
    from pathlib import Path
    sb = SeqSwitchboard(("act", "remember", "remember"))
    b, p = brain_c(sb)
    q = p["bus"].subscribe()
    eng = Engine(FollowUps(Path(tempfile.mkdtemp()) / "f.json"), p["mouth"], p["bus"], None, act=None,
                 idle_s=lambda: 999, text_mode=lambda: False, in_call=lambda: False)
    b.engine = eng
    it = FollowUp("overheard", "Heard you've got the dentist on Wednesday. What time?", "k", ask=True,
                  request="remember I have the dentist on Wednesday")
    eng.add(it)
    b.expect_followup(it)
    await b.hear("4pm")
    assert eng.queue.get(it.id) is None
    kinds = []
    while not q.empty():
        kinds.append(q.get_nowait())
    assert any(e["kind"] == "followup_done" and e["id"] == it.id for e in kinds)
