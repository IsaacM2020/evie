"""Evie's open-mic ears: cut the endless mic stream into sentences.

A VAD says, for each 32 ms frame, "someone is talking" or not. The Segmenter turns that into
sentences, and it cheats time: after 250 ms of silence it emits a Peek (the sentence so far)
so Whisper and voice ID can start early. If Isaac keeps talking it emits Resume and the early
work is thrown away. At 600 ms of silence the sentence Ends; if nothing was said after the
peek, the early result is simply reused, which saves ~350 ms on every turn.
"""
import io
import math
import re
import wave
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

RATE = 16000
FRAME = 512  # samples per frame (32 ms): Silero VAD's window size at 16 kHz
MODELS = Path.home() / "Library/Application Support/Evie/models"


@dataclass
class Start:
    pass


@dataclass
class Peek:
    audio: np.ndarray = field(repr=False)


@dataclass
class Resume:
    pass


@dataclass
class End:
    audio: np.ndarray = field(repr=False)
    same_as_peek: bool = False  # no speech since the last Peek: its early result still holds
    forced: bool = False  # hit max length rather than a pause


@dataclass
class Drop:
    pass  # too little speech to be a sentence (a cough, a click, a door)


Ev = Start | Peek | Resume | End | Drop


class Segmenter:
    """Pure state machine: frames + speech flags in, sentence events out. No audio libraries."""

    def __init__(self, frame_ms: float = 32, start_frames: int = 3, peek_ms: float = 250,
                 end_ms: float = 600, preroll_ms: float = 500, max_s: float = 15, min_speech_s: float = 0.4):
        self._start_frames = start_frames
        self._peek = math.ceil(peek_ms / frame_ms)
        self._frame_ms = frame_ms
        self._end_default = self._end = math.ceil(end_ms / frame_ms)
        self._max = math.ceil(max_s * 1000 / frame_ms)
        self._min_speech = math.ceil(min_speech_s * 1000 / frame_ms)
        self._pre: deque[np.ndarray] = deque(maxlen=math.ceil(preroll_ms / frame_ms) + start_frames)
        self.reset()

    def reset(self) -> None:
        self._pre.clear()
        self._run = 0
        self._buf: list[np.ndarray] | None = None
        self._speech = self._silence = 0
        self._peeked = False
        self._end = self._end_default

    def extend(self, end_ms: float) -> None:
        """Wait longer before ending THIS sentence (the words so far sound unfinished)."""
        if self._buf is not None:
            self._end = max(self._end, math.ceil(end_ms / self._frame_ms))

    @property
    def active(self) -> bool:
        return self._buf is not None

    def feed(self, frame: np.ndarray, speech: bool) -> list[Ev]:
        if self._buf is None:
            return self._idle(frame, speech)
        self._buf.append(frame)
        out: list[Ev] = []
        if speech:
            if self._peeked and self._silence:
                out.append(Resume())
                self._peeked = False
            self._silence = 0
            self._speech += 1
        else:
            self._silence += 1
        if not self._peeked and self._silence == self._peek and self._speech >= self._min_speech:
            self._peeked = True
            out.append(Peek(np.concatenate(self._buf)))
        if self._silence >= self._end:
            out.append(End(np.concatenate(self._buf), same_as_peek=self._peeked)
                       if self._speech >= self._min_speech else Drop())
            self.reset()
        elif len(self._buf) >= self._max:
            out.append(End(np.concatenate(self._buf), forced=True))
            self.reset()
        return out

    def _idle(self, frame: np.ndarray, speech: bool) -> list[Ev]:
        self._pre.append(frame)
        self._run = self._run + 1 if speech else 0
        if self._run < self._start_frames:
            return []
        self._buf = list(self._pre)
        self._speech, self._silence, self._peeked = self._run, 0, False
        self._pre.clear()
        return [Start()]


_TRAILING = re.compile(r"(?:\b(?:and|or|but|so|to|the|a|an|of|for|with|by|um|uh|like|my|your|is|was|that|if|because)|,)\s*$",
                       re.IGNORECASE)


def sounds_unfinished(text: str) -> bool:
    """ "Remind me to", "play the one by", "so the thing is,": he's mid-sentence, just pausing."""
    t = text.strip()
    if not t:
        return False
    return bool(_TRAILING.search(t.rstrip(".…"))) and not t.endswith(("?", "!"))


def pcm_to_wav(audio: np.ndarray) -> bytes:
    """float32 samples in [-1, 1] at 16 kHz -> 16-bit mono WAV bytes (what Whisper reads)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


def wav_to_pcm(data: bytes) -> np.ndarray:
    """16-bit mono WAV bytes -> float32 samples. Anything unreadable gives an empty array."""
    try:
        with wave.open(io.BytesIO(data)) as w:
            raw = w.readframes(w.getnframes())
    except (wave.Error, EOFError):
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768


class Vad:
    """Silero VAD through sherpa-onnx (~2 MB model, well under 1 ms per frame on CPU)."""

    def __init__(self, model: Path = MODELS / "silero_vad.onnx", threshold: float = 0.5):
        import sherpa_onnx as so
        cfg = so.VadModelConfig()
        cfg.silero_vad.model = str(model)
        cfg.silero_vad.threshold = threshold
        # The Segmenter does the timing, so the VAD itself should flip fast.
        cfg.silero_vad.min_silence_duration = 0.1
        cfg.silero_vad.min_speech_duration = 0.05
        cfg.sample_rate = RATE
        self._vad = so.VoiceActivityDetector(cfg, buffer_size_in_seconds=30)

    def is_speech(self, frame: np.ndarray) -> bool:
        self._vad.accept_waveform(frame)
        speech = self._vad.is_speech_detected()
        while not self._vad.empty():  # we keep our own buffer; don't let its queue grow
            self._vad.pop()
        return speech
