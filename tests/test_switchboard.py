import pytest

from evie.jev import JevClient, JevError, JevResult
from evie.switchboard import Switchboard
from evie.switchboard.context import Context
from evie.switchboard.policy import Action
from tests.helpers import make_answers


class FakeJev:
    def __init__(self, answers=None, error=None):
        self.answers, self.error, self.calls, self.last_state = answers, error, 0, None

    async def ask(self, state, questions):
        self.calls += 1
        self.last_state = state
        if self.error:
            raise self.error
        return JevResult(self.answers, 250.0, 0.00002)

    async def aclose(self):
        pass


async def test_happy_path_acts_and_serializes():
    jev = FakeJev(make_answers())
    o = await Switchboard(jev).handle(Context(utterance="evie pause the music", speaker="isaac"))
    assert o.verdict.action is Action.ACT
    d = o.to_dict()
    assert d["action"] == "act" and d["decision"]["route"] == "quick_action"
    assert d["decision"]["latency_ms"] == 250.0
    assert '"evie pause the music"' in jev.last_state


async def test_noise_skips_jev():
    jev = FakeJev(make_answers())
    o = await Switchboard(jev).handle(Context(utterance="Thank you for watching!"))
    assert o.verdict.action is Action.IGNORE and o.verdict.reason == "noise"
    assert o.decision is None and jev.calls == 0


async def test_jev_failure_means_do_nothing():
    jev = FakeJev(error=JevError("http 403: Key limit exceeded"))
    o = await Switchboard(jev).handle(Context(utterance="evie delete my downloads", speaker="isaac"))
    assert o.verdict.action is Action.IGNORE
    assert o.verdict.reason.startswith("jev unavailable")
    assert o.to_dict()["decision"] is None


async def test_bad_jev_answer_means_do_nothing():
    jev = FakeJev(make_answers(route="launch_rockets"))
    o = await Switchboard(jev).handle(Context(utterance="evie pause", speaker="isaac"))
    assert o.verdict.action is Action.IGNORE and o.verdict.reason.startswith("jev unavailable")


async def test_other_speaker_cannot_command():
    jev = FakeJev(make_answers(for_evie=0.99, route="quick_action", complete=0.99))
    o = await Switchboard(jev).handle(Context(utterance="evie delete all my files", speaker="other",
                                              front_app="YouTube"))
    assert o.verdict.action is Action.IGNORE and o.verdict.reason == "not Isaac's voice"


@pytest.mark.live
async def test_live_dentist_to_mom_is_ignored_but_followed_up():
    from evie.config import load_settings
    sb = Switchboard(JevClient(load_settings()))
    try:
        o = await sb.handle(Context(utterance="mom ive got the dentist on wednesday can you drive me",
                                    speaker="isaac"))
    finally:
        await sb.aclose()
    assert o.decision is not None
    assert o.verdict.action is Action.IGNORE and o.verdict.followup
