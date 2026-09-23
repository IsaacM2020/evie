import numpy as np
import pytest

from evie.ears import FRAME, End, Peek, Segmenter  # FRAME = 512 samples, 32 ms at 16 kHz


def frames(pattern: str):
    """'1' = a speech frame, '0' = silence. Each frame's samples carry its index so tests can
    check exactly which frames ended up in a segment."""
    return [(np.full(FRAME, i, dtype=np.float32), c == "1") for i, c in enumerate(pattern)]


def run(seg, pattern):
    out = []
    for f, speech in frames(pattern):
        for ev in seg.feed(f, speech):
            out.append(ev)
    return out


def kinds(evs):
    return [type(e).__name__ for e in evs]


def ids(audio):
    return sorted(set(int(x) for x in audio[::FRAME]))


def test_short_blip_is_dropped():
    seg = Segmenter()
    evs = run(seg, "0" * 5 + "1111" + "0" * 25)  # ~130 ms of speech: a cough, a click
    assert kinds(evs) == ["Start", "Drop"]


def test_single_speech_frames_never_start_a_segment():
    seg = Segmenter()
    assert run(seg, "0101010" * 10) == []


def test_sentence_gives_start_peek_then_end():
    seg = Segmenter()
    evs = run(seg, "0" * 5 + "1" * 30 + "0" * 25)  # ~1 s of speech then silence
    assert kinds(evs) == ["Start", "Peek", "End"]
    peek, end = evs[1], evs[2]
    assert end.same_as_peek is True
    assert ids(peek.audio)[-1] >= 34  # the peek holds all the speech


def test_peek_comes_at_250ms_and_end_at_600ms_of_silence():
    seg = Segmenter()
    pattern = "1" * 30 + "0" * 25
    t_peek = t_end = None
    for i, (f, speech) in enumerate(frames(pattern)):
        for ev in seg.feed(f, speech):
            if isinstance(ev, Peek):
                t_peek = i
            if isinstance(ev, End):
                t_end = i
    assert (t_peek - 29) * 32 == pytest.approx(256, abs=32)
    assert (t_end - 29) * 32 == pytest.approx(608, abs=32)


def test_speech_after_peek_resumes_and_end_is_not_the_peek():
    seg = Segmenter()
    evs = run(seg, "1" * 20 + "0" * 10 + "1" * 20 + "0" * 25)  # a mid-sentence pause
    assert kinds(evs) == ["Start", "Peek", "Resume", "Peek", "End"]
    assert evs[-1].same_as_peek is True  # the second peek covers everything


def test_pause_after_peek_without_new_speech_keeps_same_as_peek():
    seg = Segmenter()
    evs = run(seg, "1" * 20 + "0" * 25)
    assert evs[-1].same_as_peek is True


def test_preroll_keeps_the_first_syllable():
    seg = Segmenter()
    evs = run(seg, "0" * 20 + "1" * 30 + "0" * 25)
    got = ids(evs[-1].audio)
    assert got[0] <= 12  # ~300 ms before speech started is kept
    assert 19 in got


def test_long_speech_is_forced_to_end():
    seg = Segmenter(max_s=2.0)
    evs = run(seg, "1" * 100)
    assert "End" in kinds(evs)
    assert next(e for e in evs if isinstance(e, End)).forced is True


def test_segmenter_resets_between_sentences():
    seg = Segmenter()
    evs = run(seg, "1" * 30 + "0" * 25 + "1" * 30 + "0" * 25)
    assert kinds(evs) == ["Start", "Peek", "End", "Start", "Peek", "End"]
    first, second = evs[2], evs[5]
    assert max(ids(first.audio)) < min(ids(second.audio))


def test_reset_abandons_a_segment():
    seg = Segmenter()
    run(seg, "1" * 10)
    seg.reset()
    assert run(seg, "0" * 30) == []


@pytest.mark.live
def test_live_vad_finds_the_sentence_in_real_speech(tmp_path):
    import subprocess
    import wave

    from evie.ears import Vad
    subprocess.run(["say", "-v", "Daniel", "-o", str(tmp_path / "a.aiff"), "Evie, what time is it?"], check=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1",
                    str(tmp_path / "a.aiff"), str(tmp_path / "a.wav")], check=True)
    with wave.open(str(tmp_path / "a.wav")) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    pad = np.zeros(16000, dtype=np.float32)
    a = np.concatenate([pad, a, pad])
    vad, seg, evs = Vad(), Segmenter(), []
    for i in range(0, len(a) - FRAME, FRAME):
        f = a[i:i + FRAME]
        evs += seg.feed(f, vad.is_speech(f))
    assert kinds(evs)[:1] == ["Start"] and "End" in kinds(evs)
