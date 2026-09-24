import pytest

from evals.cassette import Cassette, CachedGroq, CachedJev
from evie.jev import JevError, JevResult


class CountingJev:
    def __init__(self, error=None):
        self.calls, self.error = 0, error

    async def ask(self, state, questions):
        self.calls += 1
        if self.error:
            raise self.error
        return JevResult({"q": {"noul": 0.7}}, 250.0, 0.00002)

    async def aclose(self):
        pass


class CountingGroq:
    def __init__(self):
        self.calls = 0

    async def chat(self, system, user, max_tokens=400, json_mode=False, model=None, reasoning=None, fallbacks=None):
        self.calls += 1
        return f"reply {self.calls}"


async def test_a_second_identical_question_is_replayed_not_asked(tmp_path):
    inner = CountingJev()
    jev = CachedJev(inner, Cassette(tmp_path / "jev.jsonl"))
    a = await jev.ask("state", {"q": {"type": "noul"}})
    b = await jev.ask("state", {"q": {"type": "noul"}})
    assert inner.calls == 1 and a == b and isinstance(b, JevResult)


async def test_a_changed_question_goes_live(tmp_path):
    inner = CountingJev()
    jev = CachedJev(inner, Cassette(tmp_path / "jev.jsonl"))
    await jev.ask("state", {"q": {"type": "noul", "instructions": "old wording"}})
    await jev.ask("state", {"q": {"type": "noul", "instructions": "new wording"}})
    assert inner.calls == 2


async def test_answers_survive_between_runs(tmp_path):
    await CachedJev(CountingJev(), Cassette(tmp_path / "jev.jsonl")).ask("s", {"q": {}})
    inner = CountingJev()
    await CachedJev(inner, Cassette(tmp_path / "jev.jsonl")).ask("s", {"q": {}})
    assert inner.calls == 0


async def test_live_mode_asks_again_and_records_the_fresh_answer(tmp_path):
    await CachedJev(CountingJev(), Cassette(tmp_path / "jev.jsonl")).ask("s", {"q": {}})
    inner = CountingJev()
    await CachedJev(inner, Cassette(tmp_path / "jev.jsonl", live=True)).ask("s", {"q": {}})
    assert inner.calls == 1


async def test_errors_are_never_recorded(tmp_path):
    cas = Cassette(tmp_path / "jev.jsonl")
    with pytest.raises(JevError):
        await CachedJev(CountingJev(error=JevError("down")), cas).ask("s", {"q": {}})
    inner = CountingJev()
    await CachedJev(inner, cas).ask("s", {"q": {}})
    assert inner.calls == 1


async def test_groq_calls_are_cached_by_everything_that_changes_the_answer(tmp_path):
    inner = CountingGroq()
    g = CachedGroq(inner, Cassette(tmp_path / "groq.jsonl"))
    assert await g.chat("sys", "hi") == await g.chat("sys", "hi")
    await g.chat("sys", "hi", model="openai/gpt-oss-120b")
    assert inner.calls == 2
    assert g.cassette.hits == 1 and g.cassette.misses == 2
