import asyncio
from pathlib import Path

import pytest

from evie.voice import ACKS, Mouth


class FakeSynth:
    def __init__(self, fail_on=()):
        self.fail_on = set(fail_on)
        self.rendered = []

    def render(self, text: str) -> Path:
        if text in self.fail_on:
            raise RuntimeError("kokoro blew up")
        self.rendered.append(text)
        return Path(f"/tmp/{text}.wav")


class FakePlayer:
    def __init__(self, hold=False):
        self.played: list[str] = []
        self.killed = 0
        self.hold = hold
        self._gate = asyncio.Event()

    async def play(self, path: Path) -> None:
        self.played.append(path.stem)
        if self.hold:
            await self._gate.wait()
            self._gate.clear()

    def kill(self) -> None:
        self.killed += 1
        self._gate.set()


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


async def settle():
    for _ in range(20):
        await asyncio.sleep(0)


def mouth(synth=None, player=None, clock=None, said=None, clips=None):
    m = Mouth(synth or FakeSynth(), player or FakePlayer(), clock=clock or Clock(),
              on_say=(said.append if said is not None else None), clips=clips or {})
    return m


async def test_plays_in_order_and_reports_what_was_said():
    said, p = [], FakePlayer()
    m = mouth(player=p, said=said)
    m.say("one")
    m.say("two")
    m.start()
    await settle()
    assert p.played == ["one", "two"] and said == ["one", "two"]
    await m.aclose()


async def test_reply_jumps_ahead_of_queued_narrations():
    p = FakePlayer()
    m = mouth(player=p)
    m.say("n1", kind="narration")
    m.say("n2", kind="narration")
    m.say("r1", kind="reply")
    m.start()
    await settle()
    assert p.played == ["r1", "n1", "n2"]
    await m.aclose()


async def test_stale_narration_is_dropped():
    p, clock = FakePlayer(), Clock()
    m = mouth(player=p, clock=clock)
    m.say("old news", kind="narration", ttl_s=15)
    clock.t += 16
    m.say("fresh", kind="narration", ttl_s=15)
    m.start()
    await settle()
    assert p.played == ["fresh"]
    await m.aclose()


async def test_stop_kills_current_and_clears_queue():
    p = FakePlayer(hold=True)
    m = mouth(player=p)
    m.say("long answer")
    m.say("next thing")
    m.start()
    await settle()
    assert m.speaking and p.played == ["long answer"]
    m.stop()
    await settle()
    assert p.killed == 1 and p.played == ["long answer"] and not m.speaking
    await m.aclose()


async def test_synth_failure_is_skipped_and_queue_continues():
    p = FakePlayer()
    m = mouth(synth=FakeSynth(fail_on={"bad"}), player=p)
    m.say("bad")
    m.say("good")
    m.start()
    await settle()
    assert p.played == ["good"]
    await m.aclose()


async def test_play_clip_uses_cached_file_without_rendering():
    synth, p = FakeSynth(), FakePlayer()
    m = mouth(synth=synth, player=p, clips={"on_it": Path("/tmp/on_it_cached.wav")})
    m.play_clip("on_it")
    m.start()
    await settle()
    assert p.played == ["on_it_cached"] and synth.rendered == []
    await m.aclose()


async def test_play_clip_without_cache_renders_the_ack_text():
    synth = FakeSynth()
    m = mouth(synth=synth)
    m.play_clip("for_me")
    m.start()
    await settle()
    assert synth.rendered == [ACKS["for_me"]]
    await m.aclose()


@pytest.mark.live
def test_live_kokoro_renders_audio():
    import wave

    from evie.voice import Synth
    path = Synth().render("hello Isaac")
    with wave.open(str(path)) as w:
        assert w.getnframes() / w.getframerate() > 0.3
