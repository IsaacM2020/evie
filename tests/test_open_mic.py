import asyncio

import numpy as np
import pytest

from evie.ears import FRAME, Segmenter
from evie.events import EventBus
from evie.open_mic import ModeStore, OpenMic, is_echo

# A fake frame's first sample is the "voice tag" of whoever is talking (0 = silence).
ISAAC, MOM, EVIE = 1.0, 2.0, 3.0


def frame(tag):
    f = np.zeros(FRAME, dtype=np.float32)
    f[0] = tag
    return f


class FakeVoiceId:
    def __init__(self):
        self.calls = 0
        self.learned = 0

    def who(self, audio):
        self.calls += 1
        tags = set(audio[::FRAME].tolist()) - {0.0}
        if tags == {ISAAC}:
            return "isaac", 0.8
        if tags == {MOM}:
            return "other", 0.1
        return "unknown", 0.5


class FakeSTT:
    def __init__(self, text="evie what time is it", delay=0.0):
        self.text, self.delay, self.calls = text, delay, 0

    async def transcribe_pcm(self, audio):
        self.calls += 1
        await asyncio.sleep(self.delay)
        return self.text


class FakeBrain:
    def __init__(self):
        self.heard = []

    async def hear(self, text, speaker="isaac", addressed=True, shadow=False):
        self.heard.append({"text": text, "speaker": speaker, "addressed": addressed, "shadow": shadow})
        return {}


class FakeMouth:
    def __init__(self, speaking=False, text=""):
        self.speaking, self.current_text, self.quiet_at, self.stops = speaking, text, -100.0, 0

    def stop(self):
        self.stops += 1
        self.speaking = False


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def mic(tmp_path, mode="live", stt=None, mouth=None, clock=None):
    modes = ModeStore(tmp_path / "ears.json")
    modes.set(mode)
    parts = dict(voiceid=FakeVoiceId(), stt=stt or FakeSTT(), brain=FakeBrain(), mouth=mouth or FakeMouth(),
                 bus=EventBus())
    m = OpenMic(Segmenter(), lambda f: f[0] != 0.0, modes=modes, clock=clock or Clock(), **parts)
    return m, parts


async def say(m, tag, speech_frames=30, silence_frames=25):
    for _ in range(speech_frames):
        m.feed(frame(tag))
    for _ in range(silence_frames):
        m.feed(frame(0.0))
    await settle()


async def settle():
    for _ in range(30):
        await asyncio.sleep(0.002)


async def test_isaac_sentence_reaches_the_brain_not_addressed(tmp_path):
    m, p = mic(tmp_path)
    await say(m, ISAAC)
    assert p["brain"].heard == [{"text": "evie what time is it", "speaker": "isaac", "addressed": False,
                                 "shadow": False}]


async def test_someone_else_never_reaches_whisper_or_jev(tmp_path):
    m, p = mic(tmp_path)
    await say(m, MOM)
    assert p["stt"].calls == 0 and p["brain"].heard == []


async def test_shadow_mode_only_decides(tmp_path):
    m, p = mic(tmp_path, mode="shadow")
    await say(m, ISAAC)
    assert p["brain"].heard[0]["shadow"] is True


async def test_off_mode_ignores_frames(tmp_path):
    m, p = mic(tmp_path, mode="off")
    await say(m, ISAAC)
    assert p["voiceid"].calls == 0 and p["brain"].heard == []


async def test_speculative_peek_result_is_reused(tmp_path):
    m, p = mic(tmp_path)
    await say(m, ISAAC)
    assert p["stt"].calls == 1 and p["voiceid"].calls == 1


async def test_speech_after_peek_throws_the_early_work_away(tmp_path):
    m, p = mic(tmp_path)
    for _ in range(20):
        m.feed(frame(ISAAC))
    for _ in range(10):  # pause long enough to peek, short of ending
        m.feed(frame(0.0))
    await settle()
    await say(m, ISAAC, speech_frames=20)
    assert len(p["brain"].heard) == 1
    assert p["stt"].calls == 2  # the peeked half, then the whole sentence


async def test_talk_key_held_means_no_double_turn(tmp_path):
    m, p = mic(tmp_path)
    m.ptt_start()
    await say(m, ISAAC)
    m.ptt_end()
    assert p["brain"].heard == []


async def test_talk_key_pressed_mid_sentence_drops_that_sentence(tmp_path):
    m, p = mic(tmp_path)
    for _ in range(15):
        m.feed(frame(ISAAC))
    m.ptt_start()
    m.ptt_end()
    await say(m, ISAAC, speech_frames=15)
    assert p["brain"].heard == []
    await say(m, ISAAC)  # the next sentence is fine again
    assert len(p["brain"].heard) == 1


async def test_evie_hearing_herself_is_dropped(tmp_path):
    mouth = FakeMouth(speaking=True, text="Tomorrow you have school at eight.")
    m, p = mic(tmp_path, mouth=mouth, stt=FakeSTT("tomorrow you have school at eight"))
    await say(m, EVIE)
    assert p["brain"].heard == [] and mouth.stops == 0


async def test_isaac_talking_over_evie_cuts_her_off(tmp_path):
    mouth = FakeMouth(speaking=True, text="Tomorrow you have school at eight.")
    m, p = mic(tmp_path, mouth=mouth, stt=FakeSTT("evie stop"))
    await say(m, ISAAC)
    assert mouth.stops == 1 and p["brain"].heard[0]["text"] == "evie stop"


async def test_isaac_repeating_her_words_while_she_talks_is_still_echo(tmp_path):
    mouth = FakeMouth(speaking=True, text="Tomorrow you have school at eight.")
    m, p = mic(tmp_path, mouth=mouth, stt=FakeSTT("tomorrow you have school at eight"))
    await say(m, ISAAC)
    assert p["brain"].heard == []


async def test_just_after_she_stops_is_still_guarded(tmp_path):
    clock = Clock()
    mouth = FakeMouth(speaking=False, text="Tomorrow you have school at eight.")
    mouth.quiet_at = clock.t - 0.2
    m, p = mic(tmp_path, mouth=mouth, clock=clock, stt=FakeSTT("whatever"))
    await say(m, EVIE)
    assert p["brain"].heard == []


async def test_unknown_voice_goes_through_when_evie_is_quiet(tmp_path):
    m, p = mic(tmp_path)
    await say(m, EVIE)  # unknown voice, Evie not talking: Jev + the policy judge it
    assert p["brain"].heard[0]["speaker"] == "unknown"


async def test_empty_transcript_is_dropped(tmp_path):
    m, p = mic(tmp_path, stt=FakeSTT(""))
    await say(m, ISAAC)
    assert p["brain"].heard == []


async def test_turns_never_overlap(tmp_path):
    m, p = mic(tmp_path, stt=FakeSTT(delay=0.01))
    await say(m, ISAAC)
    await say(m, ISAAC)
    await asyncio.sleep(0.05)
    assert len(p["brain"].heard) == 2


def test_mode_store_persists_and_rejects_junk(tmp_path):
    s = ModeStore(tmp_path / "ears.json")
    assert s.mode == "off"
    s.set("shadow")
    assert ModeStore(tmp_path / "ears.json").mode == "shadow"
    with pytest.raises(ValueError):
        s.set("loud")


@pytest.mark.parametrize("heard,said,echo", [
    ("tomorrow you have school at eight", "Tomorrow you have school at eight.", True),
    ("you have school at eight", "Tomorrow you have school at eight.", True),
    ("evie stop", "Tomorrow you have school at eight.", False),
    ("what about friday", "Tomorrow you have school at eight.", False),
    ("anything", "", False),
])
def test_is_echo(heard, said, echo):
    assert is_echo(heard, said) is echo


class BrokenBrain(FakeBrain):
    async def hear(self, *a, **k):
        raise RuntimeError("boom")


async def test_a_brain_crash_is_logged_and_the_mic_keeps_going(tmp_path, caplog):
    m, p = mic(tmp_path)
    m._brain = BrokenBrain()
    await say(m, ISAAC)
    assert "open mic turn failed" in caplog.text
    m._brain = p["brain"]
    await say(m, ISAAC)
    assert len(p["brain"].heard) == 1
