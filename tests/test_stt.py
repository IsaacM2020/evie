import io
import subprocess
import time
import wave

import pytest
import respx

from evie.config import Settings
from evie.stt import Transcriber

S = Settings(openrouter_key="sk-or", groq_key="gsk-test")


def wav(seconds: float, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return buf.getvalue()


class FakeModel:
    def __init__(self, text=" what time is it"):
        self.text = text
        self.paths = []

    def __call__(self, path: str) -> str:
        self.paths.append(path)
        return self.text


async def test_tiny_audio_returns_empty_without_running_model():
    m = FakeModel()
    t = Transcriber(S, backend="local", local_fn=m)
    assert await t.transcribe(wav(0.1)) == ""
    assert m.paths == []


async def test_garbage_bytes_return_empty():
    m = FakeModel()
    assert await Transcriber(S, backend="local", local_fn=m).transcribe(b"not audio") == ""
    assert m.paths == []


async def test_local_backend_runs_model_on_a_temp_wav_and_strips():
    m = FakeModel()
    out = await Transcriber(S, backend="local", local_fn=m).transcribe(wav(1.0))
    assert out == "what time is it"
    assert m.paths[0].endswith(".wav")


@respx.mock
async def test_groq_backend_posts_audio_file():
    route = respx.post("https://api.groq.com/openai/v1/audio/transcriptions").respond(
        200, json={"text": " evie pause "})
    m = FakeModel()
    out = await Transcriber(S, backend="groq", local_fn=m).transcribe(wav(1.0))
    assert out == "evie pause" and m.paths == []
    req = route.calls[0].request
    assert req.headers["Authorization"] == "Bearer gsk-test"
    assert b"whisper-large-v3-turbo" in req.content and b"RIFF" in req.content


def test_unknown_backend_rejected():
    with pytest.raises(ValueError):
        Transcriber(S, backend="cloud-magic")


@pytest.mark.live
async def test_live_local_whisper_hears_a_spoken_sentence(tmp_path):
    aiff, out = tmp_path / "q.aiff", tmp_path / "q.wav"
    subprocess.run(["say", "-o", str(aiff), "Evie, what time is it?"], check=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(out)], check=True)
    t = Transcriber(S, backend="local")
    await t.warm()
    t0 = time.perf_counter()
    text = await t.transcribe(out.read_bytes())
    print(f"local whisper: {(time.perf_counter() - t0) * 1000:.0f} ms -> {text!r}")
    assert "time" in text.lower()


@pytest.mark.parametrize("heard,fixed", [
    ("Eevee, fix the bug", "Evie, fix the bug"),
    ("eevee fix the bug in the Eevee repo", "Evie fix the bug in the Evie repo"),
])
async def test_known_mishearings_of_evie_are_fixed(heard, fixed):
    assert await Transcriber(S, backend="local", local_fn=FakeModel(heard)).transcribe(wav(1.0)) == fixed


async def test_other_words_are_left_alone():
    out = await Transcriber(S, backend="local", local_fn=FakeModel("the eve of the match")).transcribe(wav(1.0))
    assert out == "the eve of the match"


async def test_transcribe_pcm_wraps_audio_as_wav():
    import numpy as np
    m = FakeModel(" hi there")
    t = Transcriber(S, backend="local", local_fn=m)
    assert await t.transcribe_pcm(np.zeros(16000, dtype=np.float32)) == "hi there"
    assert len(m.paths) == 1


# -- Phase 3.5: cloud ears first, local only as a fallback ------------------------------------
GROQ_STT = "https://api.groq.com/openai/v1/audio/transcriptions"


def test_groq_is_the_default_backend():
    assert Settings(openrouter_key="x").stt_backend == "groq"


@respx.mock
async def test_groq_request_carries_isaacs_vocabulary_as_a_prompt():
    route = respx.post(GROQ_STT).respond(200, json={"text": "open todoist"})
    await Transcriber(S, backend="groq", local_fn=FakeModel()).transcribe(wav(1.0))
    body = route.calls[0].request.content
    assert b'name="prompt"' in body and b"Todoist" in body and b"IsaacOS" in body


@respx.mock
async def test_groq_down_falls_back_to_local_and_says_so():
    import httpx
    respx.post(GROQ_STT).mock(side_effect=httpx.ConnectError("offline"))
    m = FakeModel(" pause the music")
    t = Transcriber(S, backend="groq", local_fn=m)
    assert await t.transcribe(wav(1.0)) == "pause the music"
    assert len(m.paths) == 1 and t.offline is True


@respx.mock
async def test_groq_error_status_falls_back_too():
    respx.post(GROQ_STT).respond(503)
    m = FakeModel(" hi")
    t = Transcriber(S, backend="groq", local_fn=m)
    assert await t.transcribe(wav(1.0)) == "hi" and t.offline is True


@respx.mock
async def test_back_online_clears_the_offline_flag():
    import httpx
    respx.post(GROQ_STT).mock(side_effect=[httpx.ConnectError("x"), httpx.Response(200, json={"text": "ok"})])
    t = Transcriber(S, backend="groq", local_fn=FakeModel(" x"))
    await t.transcribe(wav(1.0))
    await t.transcribe(wav(1.0))
    assert t.offline is False


async def test_groq_backend_warm_never_loads_local_whisper():
    m = FakeModel()
    t = Transcriber(S, backend="groq", local_fn=m)
    await t.warm()
    assert m.paths == []


async def test_local_model_is_unloaded_after_ten_idle_minutes():
    unloaded = []
    clock = [1000.0]
    t = Transcriber(S, backend="local", local_fn=FakeModel(), unload_fn=lambda: unloaded.append(1),
                    clock=lambda: clock[0])
    await t.transcribe(wav(1.0))
    clock[0] += 599
    assert t.maybe_unload() is False
    clock[0] += 2
    assert t.maybe_unload() is True and unloaded == [1]
    assert t.maybe_unload() is False  # already gone
