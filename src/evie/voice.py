"""Evie's mouth: Pocket TTS streams audio straight to the speakers while it's still being made.

First sound ~30 ms after a line starts (Pocket makes speech ~8x faster than real time on the
M4), so nothing is rendered to a file first. Replies jump ahead of job narrations, stale
narrations get dropped, and stop() (Isaac pressing the talk key) cuts her off mid-word.
"""
import asyncio
import contextvars
import logging
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator, Literal, Protocol

import numpy as np

log = logging.getLogger("evie.voice")

# Which of Isaac's turns a line answers. The Brain sets it per turn; lines said from that turn's
# code carry it, so a turn that's been replaced (he rephrased, or a fragment got merged) can have
# its unspoken replies dropped instead of talking over the new answer.
TURN: contextvars.ContextVar[int | None] = contextvars.ContextVar("evie_turn", default=None)

ACKS = {"on_it": "On it.", "on_it_long": "On it, this might take a minute.", "for_me": "Was that for me?",
        "not_yet": "Can't do that one yet."}
VOICE = "eve"  # Pocket TTS predefined voice (Isaac picked it by ear, 2026-09-24; was alba)


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


class AppOut:
    """Plays Evie's voice through the menu bar app, inside the same audio engine as the open mic.

    Apple's echo canceller only removes sound it plays itself: with Evie's voice going out
    through the app, the mic hears Isaac and not her (2026-09-23: her own words kept turning up
    in his transcripts). The link is the app's /ws/mouth socket; the app says "done" when the
    last sample has played, so `speaking` stays true for exactly as long as she's audible."""

    def __init__(self, link, rate: int, grace_s: float = 1.5):
        self._link, self.rate, self._grace = link, rate, grace_s

    def play(self, chunks, cancel, on_start=None) -> bool:
        if isinstance(chunks, np.ndarray):
            chunks = [chunks]
        lid = uuid.uuid4().hex[:10]
        done = self._link.waiter(lid)
        self._link.send_json({"kind": "start", "id": lid, "rate": self.rate})
        samples = 0
        for chunk in chunks:
            if cancel.is_set():
                self._link.send_json({"kind": "stop", "id": lid})
                return False
            self._link.send_bytes(np.ascontiguousarray(chunk, dtype=np.float32).tobytes())
            if samples == 0 and on_start:
                on_start()
            samples += len(chunk)
        self._link.send_json({"kind": "end", "id": lid})
        # Wait for the app to finish playing; never longer than the audio itself plus a margin.
        deadline = time.monotonic() + samples / self.rate + self._grace
        while not done.wait(0.05):
            if cancel.is_set():
                self._link.send_json({"kind": "stop", "id": lid})
                return False
            if time.monotonic() > deadline:
                log.warning("app never said the line finished; carrying on")
                break
        return True


class RoutedOut:
    """The app's echo-cancelled speaker while it's listening, the Mac's speakers otherwise."""

    def __init__(self, link, app: Out, speakers: Out):
        self._link, self._app, self._speakers = link, app, speakers

    def play(self, chunks, cancel, on_start=None) -> bool:
        out = self._app if self._link.connected else self._speakers
        return out.play(chunks, cancel, on_start)


@dataclass
class _Line:
    text: str
    kind: Literal["reply", "narration"]
    created: float
    ttl_s: float | None = None
    clip: np.ndarray | None = field(default=None, repr=False)
    turn: int | None = None


class Mouth:
    def __init__(self, voice, out: Out, clock: Callable[[], float] = time.monotonic,
                 on_say: Callable[[str], None] | None = None, clips: dict[str, np.ndarray] | None = None,
                 on_quiet: Callable[[], None] | None = None, on_audio: Callable[[str], None] | None = None,
                 trace: Callable[[dict], None] | None = None, text_only: Callable[[], bool] = lambda: False,
                 on_text: Callable[[str, str], None] | None = None):
        self._voice, self._out, self._clock = voice, out, clock
        # Text mode (evie.quiet): the ONE gate every line passes, replies, read-backs, narration,
        # countdown lines and clips alike. In text mode nothing is synthesised; the words go to the orb.
        self._text_only, self._on_text = text_only, on_text
        self._trace = trace  # speech.jsonl: when each line started and ended (overlap hunting)
        self._on_say, self._on_quiet, self._on_audio = on_say, on_quiet, on_audio
        self._clips = clips or {}
        self._queue: deque[_Line] = deque()
        self._wake = asyncio.Event()
        self._cancel = threading.Event()  # the current line's; stop() sets it
        self._task: asyncio.Task | None = None
        self.speaking = False
        self._spoke = False
        self.current_text = ""  # the open mic compares what it hears to this (echo guard)
        self.quiet_at = clock() - 60.0  # when she last stopped making sound

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    def say(self, text: str, kind: Literal["reply", "narration"] = "reply", ttl_s: float | None = None,
            clip: np.ndarray | None = None) -> None:
        line = _Line(text, kind, self._clock(), ttl_s, clip, TURN.get())
        if self._text_only():
            self._log("text", line)
            if self._on_text:
                self._on_text(text, kind)
            return
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

    def drop_turn(self, turn: int) -> None:
        """A newer turn replaced this one: its replies not yet spoken go. The line already playing
        finishes (cutting a word in half sounds worse than one extra sentence)."""
        keep = deque()
        for q in self._queue:
            if q.turn == turn and q.kind == "reply":
                self._log("dropped", q)
            else:
                keep.append(q)
        self._queue = keep

    def _log(self, ev: str, line: _Line, **extra) -> None:
        if self._trace:
            try:
                self._trace({"t": time.time(), "ev": ev, "text": line.text, "kind": line.kind, "turn": line.turn}
                            | extra)
            except Exception:  # a log write must never stop her talking
                log.exception("speech trace failed")

    def _play(self, line: _Line, cancel: threading.Event) -> bool:
        audio = line.clip if line.clip is not None else self._voice.chunks(line.text)
        on_start = (lambda: self._on_audio(line.text)) if self._on_audio else None
        return self._out.play(audio, cancel, on_start)

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
            self.current_text = line.text
            self.speaking = self._spoke = True
            self._log("start", line)
            ok = False
            try:
                ok = bool(await asyncio.to_thread(self._play, line, cancel))
            except Exception:  # a voice or audio-device failure must never leave Evie mute for good
                log.exception("couldn't speak %r", line.text)
            finally:
                self.speaking = False
                self.quiet_at = self._clock()
                self._log("end", line, ok=ok)

    async def aclose(self) -> None:
        self._cancel.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
