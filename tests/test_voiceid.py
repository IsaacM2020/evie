import numpy as np
import pytest

from evie.voiceid import VoiceBars, VoiceId, VoicePrint

RATE = 16000


def unit(*v):
    a = np.array(v, dtype=np.float32)
    return a / np.linalg.norm(a)


ISAAC = unit(1, 0, 0)


def audio(seconds, tag=0.0):
    """Fake audio: the first sample says which voice it is, so the fake embedder can tell."""
    a = np.zeros(int(seconds * RATE), dtype=np.float32)
    a[0] = tag
    return a


# tag -> embedding: 0 Isaac, 1 close to Isaac, 2 someone else, 3 in between
EMB = {0.0: ISAAC, 1.0: unit(1, 0.2, 0), 2.0: unit(0, 1, 0), 3.0: unit(1, 1.2, 0)}


def fake_embed(a):
    return EMB[float(a[0])]


def enrolled(tmp_path, n=8):
    vp = VoicePrint(tmp_path / "voiceprint.json")
    for _ in range(n):
        vp.add(ISAAC, 2.0)
    return vp


def test_not_ready_until_enough_voice(tmp_path):
    vp = VoicePrint(tmp_path / "vp.json")
    for _ in range(7):
        vp.add(ISAAC, 2.0)
    assert not vp.ready
    vp.add(ISAAC, 2.0)
    assert vp.ready and vp.status() == {"clips": 8, "seconds": 16.0, "ready": True}


def test_thirty_seconds_is_also_enough(tmp_path):
    vp = VoicePrint(tmp_path / "vp.json")
    for _ in range(3):
        vp.add(ISAAC, 10.0)
    assert vp.ready


def test_keeps_only_the_latest_clips(tmp_path):
    vp = VoicePrint(tmp_path / "vp.json", keep=5)
    for _ in range(9):
        vp.add(ISAAC, 1.5)
    assert vp.status()["clips"] == 5


def test_voiceprint_survives_a_restart(tmp_path):
    enrolled(tmp_path)
    again = VoicePrint(tmp_path / "voiceprint.json")
    assert again.ready and np.allclose(again.mean(), ISAAC, atol=1e-5)


def test_corrupt_file_starts_fresh(tmp_path):
    (tmp_path / "vp.json").write_text("{nope")
    assert VoicePrint(tmp_path / "vp.json").status()["clips"] == 0


def test_who_matches_isaac_and_rejects_others(tmp_path):
    vid = VoiceId(fake_embed, enrolled(tmp_path))
    assert vid.who(audio(2, 0.0))[0] == "isaac"
    assert vid.who(audio(2, 1.0))[0] == "isaac"
    assert vid.who(audio(2, 2.0))[0] == "other"
    speaker, sim = vid.who(audio(2, 3.0))
    assert speaker == "unknown" and 0.45 <= sim < 0.70


def test_too_short_is_unknown(tmp_path):
    vid = VoiceId(fake_embed, enrolled(tmp_path))
    assert vid.who(audio(0.6, 2.0))[0] == "unknown"


def test_not_enrolled_is_unknown(tmp_path):
    vid = VoiceId(fake_embed, VoicePrint(tmp_path / "vp.json"))
    assert vid.who(audio(3, 0.0))[0] == "unknown"


def test_learn_only_takes_long_enough_clips(tmp_path):
    vp = VoicePrint(tmp_path / "vp.json")
    vid = VoiceId(fake_embed, vp)
    assert vid.learn(audio(1.0, 0.0)) is False
    assert vid.learn(audio(2.0, 0.0)) is True
    assert vp.status()["clips"] == 1


def test_bars_are_one_tunable_place(tmp_path):
    strict = VoiceId(fake_embed, enrolled(tmp_path), VoiceBars(isaac_at=0.99))
    assert strict.who(audio(2, 1.0))[0] == "unknown"


def test_clear_forgets_the_voice(tmp_path):
    vp = enrolled(tmp_path)
    vp.clear()
    assert not vp.ready and not VoicePrint(tmp_path / "voiceprint.json").ready


@pytest.mark.live
def test_live_embedder_tells_two_voices_apart(tmp_path):
    import subprocess
    import wave

    from evie.voiceid import SpeakerEmbedder

    def clip(voice, text, name):
        subprocess.run(["say", "-v", voice, "-o", str(tmp_path / f"{name}.aiff"), text], check=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1",
                        str(tmp_path / f"{name}.aiff"), str(tmp_path / f"{name}.wav")], check=True)
        with wave.open(str(tmp_path / f"{name}.wav")) as w:
            return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768

    emb = SpeakerEmbedder()
    a = emb(clip("Daniel", "Evie, what's on my calendar tomorrow afternoon?", "a"))
    b = emb(clip("Daniel", "Play some lofi music and turn it down a bit.", "b"))
    c = emb(clip("Samantha", "Mom, can you drive me to the dentist on Wednesday?", "c"))
    assert float(a @ b) > float(a @ c) + 0.2
