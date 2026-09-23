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
