"""Evie's mouth: Kokoro turns text into audio, and Mouth plays it one line at a time.

Replies jump ahead of job narrations, old narrations get dropped instead of said late, and
stop() (Isaac pressing the talk key) cuts her off mid-sentence.
"""
import asyncio
import logging
import tempfile
import time
import wave
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal, Protocol

log = logging.getLogger("evie.voice")

KOKORO_DIR = Path.home() / "Library/Application Support/Evie/kokoro"
ACKS = {"on_it": "On it.", "for_me": "Was that for me?", "not_yet": "Can't do that one yet."}


class Synth:
    def __init__(self, model_dir: Path = KOKORO_DIR, voice: str = "af_heart", speed: float = 1.1):
        from kokoro_onnx import Kokoro  # heavy import, only when a real voice is needed
        self._k = Kokoro(str(model_dir / "kokoro-v1.0.onnx"), str(model_dir / "voices-v1.0.bin"))
        self._voice, self._speed = voice, speed
        self._dir = Path(tempfile.mkdtemp(prefix="evie-voice-"))
        self._n = 0

    def render(self, text: str) -> Path:
        import numpy as np
        samples, rate = self._k.create(text, voice=self._voice, speed=self._speed, lang="en-us")
        self._n += 1
        path = self._dir / f"say-{self._n}.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
        return path

    def prepare_clips(self) -> dict[str, Path]:
        return {name: self.render(text) for name, text in ACKS.items()}


class Player(Protocol):
    async def play(self, path: Path) -> None: ...
    def kill(self) -> None: ...


class AfPlayer:
    """Plays a file with macOS's afplay, in a process we can kill for barge-in."""

    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None

    async def play(self, path: Path) -> None:
        self._proc = await asyncio.create_subprocess_exec("afplay", str(path))
        await self._proc.wait()
        self._proc = None

    def kill(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()


@dataclass
class _Line:
    text: str
    kind: Literal["reply", "narration"]
    created: float
    ttl_s: float | None = None
    clip: Path | None = field(default=None)


class Mouth:
    def __init__(self, synth, player: Player, clock: Callable[[], float] = time.monotonic,
                 on_say: Callable[[str], None] | None = None, clips: dict[str, Path] | None = None,
                 on_quiet: Callable[[], None] | None = None):
        self._synth, self._player, self._clock = synth, player, clock
        self._on_say, self._on_quiet = on_say, on_quiet
        self._clips = clips or {}
        self._queue: deque[_Line] = deque()
        self._wake = asyncio.Event()
        self._gen = 0  # bumped by stop(), so a line rendered before a stop never plays after it
        self._task: asyncio.Task | None = None
        self.speaking = False
        self._spoke = False

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    def say(self, text: str, kind: Literal["reply", "narration"] = "reply", ttl_s: float | None = None,
            clip: Path | None = None) -> None:
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
        self._gen += 1
        self._queue.clear()
        self._player.kill()

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
            gen = self._gen
            try:
                path = line.clip or await asyncio.to_thread(self._synth.render, line.text)
            except Exception:
                log.exception("couldn't render %r", line.text)
                continue
            if gen != self._gen:
                continue
            if self._on_say:
                self._on_say(line.text)
            self.speaking = self._spoke = True
            try:
                await self._player.play(path)
            finally:
                self.speaking = False
                if line.clip is None:
                    path.unlink(missing_ok=True)

    async def aclose(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
