"""Evie's ears: turn a WAV recording into text. Local MLX Whisper by default (audio never
leaves the Mac); Groq's hosted Whisper is a switch if local ever gets too slow."""
import asyncio
import io
import re
import tempfile
import wave
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import httpx

from evie.config import Settings

# small.en: ~315 ms for a 3 s sentence on the M4, vs ~1.2 s for large-v3-turbo (measured
# 2026-09-23). Evie needs the verdict fast; Jev already copes with small mishearings.
MODEL = "mlx-community/whisper-small.en-mlx"
MIN_SECONDS = 0.3
# Whisper spells her name like the Pokemon. Only exact known mishearings: "eve" stays, since
# "the eve of the match" is a real phrase (Jev is told about those in its state text instead).
_NAME_FIXES = re.compile(r"\beevee\b", re.IGNORECASE)


def _mlx_transcribe(path: str) -> str:
    import mlx_whisper  # heavy import, only when local Whisper actually runs
    return mlx_whisper.transcribe(path, path_or_hf_repo=MODEL)["text"]


def _seconds(audio: bytes) -> float:
    try:
        with wave.open(io.BytesIO(audio)) as w:
            return w.getnframes() / w.getframerate()
    except (wave.Error, EOFError):
        return 0.0


class Transcriber:
    def __init__(self, settings: Settings, backend: str = "local",
                 local_fn: Callable[[str], str] = _mlx_transcribe, http: httpx.AsyncClient | None = None):
        if backend not in ("local", "groq"):
            raise ValueError(f"unknown stt backend {backend!r}")
        self._s, self.backend, self._local = settings, backend, local_fn
        self._http = http or httpx.AsyncClient(timeout=10.0)
        # MLX isn't thread safe, so every local transcription runs on this one thread.
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper")

    async def transcribe(self, audio: bytes) -> str:
        if _seconds(audio) < MIN_SECONDS:
            return ""
        if self.backend == "groq":
            text = await self._groq(audio)
        else:
            text = await asyncio.get_running_loop().run_in_executor(self._pool, self._run_local, audio)
        return _NAME_FIXES.sub("Evie", text)

    def _run_local(self, audio: bytes) -> str:
        with tempfile.NamedTemporaryFile(suffix=".wav") as f:
            f.write(audio)
            f.flush()
            return self._local(f.name).strip()

    async def _groq(self, audio: bytes) -> str:
        r = await self._http.post(
            f"{self._s.groq_url}/audio/transcriptions",
            headers={"Authorization": f"Bearer {self._s.groq_key}"},
            data={"model": "whisper-large-v3-turbo", "language": "en", "response_format": "json"},
            files={"file": ("speech.wav", audio, "audio/wav")},
        )
        r.raise_for_status()
        return r.json()["text"].strip()

    async def warm(self) -> None:
        """Load the model before Isaac's first sentence (the first run takes a few seconds)."""
        if self.backend == "local":
            silence = io.BytesIO()
            with wave.open(silence, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(b"\x00\x00" * 16000)
            await asyncio.get_running_loop().run_in_executor(self._pool, self._run_local, silence.getvalue())

    async def aclose(self) -> None:
        await self._http.aclose()
        self._pool.shutdown(wait=False)
