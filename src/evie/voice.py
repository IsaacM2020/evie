"""Evie's mouth: Pocket TTS streams audio straight to the speakers while it's still being made.

First sound ~30 ms after a line starts (Pocket makes speech ~8x faster than real time on the
M4), so nothing is rendered to a file first. Replies jump ahead of job narrations, stale
narrations get dropped, and stop() (Isaac pressing the talk key) cuts her off mid-word.
"""
import asyncio
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator, Literal, Protocol

import numpy as np

log = logging.getLogger("evie.voice")

ACKS = {"on_it": "On it.", "for_me": "Was that for me?", "not_yet": "Can't do that one yet."}
VOICE = "alba"  # Pocket TTS predefined voice


class PocketVoice:
    """Kyutai Pocket TTS on CPU. Not thread safe: only the Mouth's player thread calls it."""

    def __init__(self, voice: str = VOICE):
        from pocket_tts import TTSModel  # heavy import (torch), only when the real voice is needed
        self._m = TTSModel.load_model()
        self._state = self._m.get_state_for_audio_prompt(voice)
        self.rate = self._m.sample_rate

    def chunks(self, text: str) -> Iterator[np.ndarray]:
        for c in self._m.generate_audio_stream(self._state, text):
            yield c.numpy().astype(np.float32)

    def render(self, text: str) -> np.ndarray:
        return np.concatenate(list(self.chunks(text)))

    def prepare_clips(self) -> dict[str, np.ndarray]:
        return {name: self.render(text) for name, text in ACKS.items()}


class Out(Protocol):
    def play(self, chunks: Iterable[np.ndarray] | np.ndarray, cancel: threading.Event,
             on_start: Callable[[], None] | None = None) -> bool: ...


class SpeakerOut:
    """Writes float32 audio to the default output as it arrives; cancel stops it within ~50 ms."""

    SLICE = 1200  # 50 ms at 24 kHz: how often cancel is checked

    def __init__(self, rate: int):
        self.rate = rate

    def play(self, chunks, cancel, on_start=None) -> bool:
        import sounddevice as sd
        if isinstance(chunks, np.ndarray):
            chunks = [chunks]
        started = False
        with sd.OutputStream(samplerate=self.rate, channels=1, dtype="float32", latency="low") as s:
            for chunk in chunks:
                for i in range(0, len(chunk), self.SLICE):
                    if cancel.is_set():
                        s.abort()
                        return False
                    if not started:
                        started = True
                        if on_start:
                            on_start()
                    s.write(chunk[i:i + self.SLICE].reshape(-1, 1))
        return True


@dataclass
class _Line:
    text: str
    kind: Literal["reply", "narration"]
    created: float
    ttl_s: float | None = None
    clip: np.ndarray | None = field(default=None, repr=False)


class Mouth:
    def __init__(self, voice, out: Out, clock: Callable[[], float] = time.monotonic,
                 on_say: Callable[[str], None] | None = None, clips: dict[str, np.ndarray] | None = None,
                 on_quiet: Callable[[], None] | None = None, on_audio: Callable[[str], None] | None = None):
        self._voice, self._out, self._clock = voice, out, clock
        self._on_say, self._on_quiet, self._on_audio = on_say, on_quiet, on_audio
        self._clips = clips or {}
        self._queue: deque[_Line] = deque()
        self._wake = asyncio.Event()
        self._cancel = threading.Event()  # the current line's; stop() sets it
        self._task: asyncio.Task | None = None
        self.speaking = False
        self._spoke = False

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    def say(self, text: str, kind: Literal["reply", "narration"] = "reply", ttl_s: float | None = None,
            clip: np.ndarray | None = None) -> None:
        line = _Line(text, kind, self._clock(), ttl_s, clip)
        if kind == "reply":
            idx = next((i for i, q in enumerate(self._queue) if q.kind == "narration"), len(self._queue))
            self._queue.insert(idx, line)
        else:
            self._queue.append(line)
        self._wake.set()

    def play_clip(self, name: str) -> None:
        self.say(ACKS[name], clip=self._clips.get(name))

    def stop(self) -> None:
        self._queue.clear()
        self._cancel.set()

    def _play(self, line: _Line, cancel: threading.Event) -> None:
        audio = line.clip if line.clip is not None else self._voice.chunks(line.text)
        on_start = (lambda: self._on_audio(line.text)) if self._on_audio else None
        self._out.play(audio, cancel, on_start)

    async def _run(self) -> None:
        while True:
            if not self._queue:
                if self._spoke and self._on_quiet:
                    self._on_quiet()
                self._spoke = False
                self._wake.clear()
                await self._wake.wait()
                continue
            line = self._queue.popleft()
            if line.ttl_s is not None and self._clock() - line.created > line.ttl_s:
                continue
            self._cancel = cancel = threading.Event()
            if self._on_say:
                self._on_say(line.text)
            self.speaking = self._spoke = True
            try:
                await asyncio.to_thread(self._play, line, cancel)
            except Exception:  # a voice or audio-device failure must never leave Evie mute for good
                log.exception("couldn't speak %r", line.text)
            finally:
                self.speaking = False

    async def aclose(self) -> None:
        self._cancel.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
