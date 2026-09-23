import asyncio
import threading

import numpy as np
import pytest

from evie.voice import ACKS, Mouth


class FakeVoice:
    """Stands in for Pocket TTS: 'audio' is just the text, one chunk per word."""

    def __init__(self, fail_on=()):
        self.fail_on = set(fail_on)
        self.generated: list[str] = []

    def chunks(self, text):
        if text in self.fail_on:
            raise RuntimeError("pocket blew up")
        self.generated.append(text)
        for w in text.split():
            yield w


class FakeOut:
    def __init__(self, hold=False, crash_first=False):
        self.played: list[str] = []
        self.hold = hold
        self.crash_first = crash_first
        self.release = threading.Event()

    def play(self, chunks, cancel, on_start=None):
        items = ["<clip>"] if isinstance(chunks, np.ndarray) else list(chunks)
        if self.crash_first:
            self.crash_first = False
            raise OSError("no audio device")
        if on_start:
            on_start()
        self.played.append(" ".join(items))
        if self.hold:
            while not cancel.is_set() and not self.release.is_set():
                cancel.wait(0.01)
        return not cancel.is_set()


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


async def settle(n=40):
    for _ in range(n):
        await asyncio.sleep(0.005)


def mouth(voice=None, out=None, clock=None, said=None, clips=None, **kw):
    return Mouth(voice or FakeVoice(), out or FakeOut(), clock=clock or Clock(),
                 on_say=(said.append if said is not None else None), clips=clips or {}, **kw)


async def test_plays_in_order_and_reports_what_was_said():
    said, out = [], FakeOut()
    m = mouth(out=out, said=said)
    m.say("one")
    m.say("two")
    m.start()
    await settle()
    assert out.played == ["one", "two"] and said == ["one", "two"]
    await m.aclose()


async def test_reply_jumps_ahead_of_queued_narrations():
    out = FakeOut()
    m = mouth(out=out)
    m.say("n1", kind="narration")
    m.say("n2", kind="narration")
    m.say("r1", kind="reply")
    m.start()
    await settle()
    assert out.played == ["r1", "n1", "n2"]
    await m.aclose()


async def test_stale_narration_is_dropped():
    out, clock = FakeOut(), Clock()
    m = mouth(out=out, clock=clock)
    m.say("old news", kind="narration", ttl_s=15)
    clock.t += 16
    m.say("fresh", kind="narration", ttl_s=15)
    m.start()
    await settle()
    assert out.played == ["fresh"]
    await m.aclose()


async def test_stop_cuts_her_off_and_clears_queue():
    out = FakeOut(hold=True)
    m = mouth(out=out)
    m.say("long answer")
    m.say("next thing")
    m.start()
    await settle()
    assert m.speaking and out.played == ["long answer"]
    m.stop()
    await settle()
    assert out.played == ["long answer"] and not m.speaking
    await m.aclose()


async def test_voice_failure_is_skipped_and_queue_continues():
    out = FakeOut()
    m = mouth(voice=FakeVoice(fail_on={"bad"}), out=out)
    m.say("bad")
    m.say("good")
    m.start()
    await settle()
    assert out.played == ["good"]
    await m.aclose()


async def test_audio_device_crash_does_not_kill_the_mouth():
    out = FakeOut(crash_first=True)
    m = mouth(out=out)
    m.say("first")
    m.say("second")
    m.start()
    await settle()
    assert out.played == ["second"] and not m.speaking
    await m.aclose()


async def test_play_clip_uses_cached_audio_without_generating():
    voice, out = FakeVoice(), FakeOut()
    m = mouth(voice=voice, out=out, clips={"on_it": np.zeros(10, dtype=np.float32)})
    m.play_clip("on_it")
    m.start()
    await settle()
    assert out.played == ["<clip>"] and voice.generated == []
    await m.aclose()


async def test_play_clip_without_cache_speaks_the_ack_text():
    voice = FakeVoice()
    m = mouth(voice=voice)
    m.play_clip("for_me")
    m.start()
    await settle()
    assert voice.generated == [ACKS["for_me"]]
    await m.aclose()


async def test_on_quiet_fires_once_when_queue_drains():
    quiet = []
    m = mouth(on_quiet=lambda: quiet.append(1))
    m.say("one")
    m.say("two")
    m.start()
    await settle()
    assert quiet == [1]
    await m.aclose()


async def test_first_audio_callback_fires_per_line():
    started = []
    m = mouth(on_audio=lambda text: started.append(text))
    m.say("one")
    m.start()
    await settle()
    assert started == ["one"]
    await m.aclose()


@pytest.mark.live
def test_live_pocket_first_chunk_is_fast():
    import time

    from evie.voice import PocketVoice
    v = PocketVoice()
    t = time.perf_counter()
    first = next(iter(v.chunks("Tomorrow you have school at eight.")))
    assert (time.perf_counter() - t) < 0.3 and len(first) > 0


async def test_mouth_remembers_what_it_last_said_and_when_it_went_quiet():
    clock = Clock()
    m = mouth(clock=clock)
    assert m.current_text == "" and m.quiet_at < clock.t
    m.say("hello there")
    m.start()
    await settle()
    assert m.current_text == "hello there" and m.quiet_at == clock.t and not m.speaking
    await m.aclose()
