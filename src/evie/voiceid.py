"""Voice ID: "is this Isaac talking?", answered on the Mac in ~50 ms.

A speaker model (WeSpeaker ResNet34, run by sherpa-onnx) turns any clip into 256 numbers
that describe the voice, not the words. Isaac's voiceprint is the average of those numbers
over clips we KNOW are him: every time he holds the talk key, that clip is certainly Isaac.
A new sentence is compared to the voiceprint (cosine similarity, 1.0 = identical):
close enough is "isaac", far is "other", and the grey zone in between is "unknown".
Only the numbers are stored, never the audio.
"""
import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from evie.ears import MODELS, RATE

log = logging.getLogger("evie.voiceid")

VOICEPRINT = Path.home() / "Library/Application Support/Evie/voiceprint.json"


@dataclass(frozen=True)
class VoiceBars:
    # evals/run_ears.py (macOS voices, 2026-09-23): "Isaac" 0.62-0.83, strangers up to 0.62.
    # 0.65 keeps every stranger out and accepts 83% of Isaac. Synthetic voices sound more alike
    # than real people, so this gets re-tuned from Isaac's real sims (core.log). See TUNING.md.
    isaac_at: float = 0.65
    other_below: float = 0.45
    min_seconds: float = 1.0  # shorter clips don't carry enough voice to judge
    learn_min_seconds: float = 1.5
    ready_clips: int = 8
    ready_seconds: float = 30.0


class VoicePrint:
    def __init__(self, path: Path | None = VOICEPRINT, keep: int = 40, bars: VoiceBars = VoiceBars()):
        self._path, self._keep, self._bars = path, keep, bars
        self._embs: list[list[float]] = []
        self._secs: list[float] = []
        self._load()

    def _load(self) -> None:
        if not self._path or not self._path.exists():
            return
        try:
            d = json.loads(self._path.read_text())
            self._embs, self._secs = d["embs"][-self._keep:], d["secs"][-self._keep:]
        except (ValueError, KeyError, TypeError):
            log.warning("voiceprint file unreadable, starting fresh")
            self._embs, self._secs = [], []

    def _save(self) -> None:
        if not self._path:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"embs": self._embs, "secs": self._secs}))
        tmp.replace(self._path)

    def add(self, emb: np.ndarray, seconds: float) -> None:
        e = np.asarray(emb, dtype=np.float32)
        self._embs.append((e / np.linalg.norm(e)).tolist())
        self._secs.append(round(float(seconds), 2))
        self._embs, self._secs = self._embs[-self._keep:], self._secs[-self._keep:]
        self._save()

    def clear(self) -> None:
        self._embs, self._secs = [], []
        self._save()

    def mean(self) -> np.ndarray | None:
        if not self._embs:
            return None
        m = np.mean(np.array(self._embs, dtype=np.float32), axis=0)
        return m / np.linalg.norm(m)

    @property
    def ready(self) -> bool:
        return len(self._embs) >= self._bars.ready_clips or sum(self._secs) >= self._bars.ready_seconds

    def status(self) -> dict:
        return {"clips": len(self._embs), "seconds": round(sum(self._secs), 1), "ready": self.ready}


class VoiceId:
    def __init__(self, embed_fn: Callable[[np.ndarray], np.ndarray], voiceprint: VoicePrint,
                 bars: VoiceBars = VoiceBars()):
        self._embed, self.print, self.bars = embed_fn, voiceprint, bars
        self._lock = threading.Lock()  # the open mic and the talk key can both call in

    def who(self, audio: np.ndarray) -> tuple[str, float]:
        """(speaker, similarity). Blocking (~50 ms): call it from a worker thread."""
        ref = self.print.mean()
        if ref is None or not self.print.ready or len(audio) / RATE < self.bars.min_seconds:
            return "unknown", 0.0
        with self._lock:
            e = np.asarray(self._embed(audio), dtype=np.float32)
        sim = float(ref @ (e / np.linalg.norm(e)))
        if sim >= self.bars.isaac_at:
            return "isaac", sim
        if sim < self.bars.other_below:
            return "other", sim
        return "unknown", sim

    def learn(self, audio: np.ndarray) -> bool:
        """Add a clip that is certainly Isaac (he held the talk key). Blocking."""
        seconds = len(audio) / RATE
        if seconds < self.bars.learn_min_seconds:
            return False
        with self._lock:
            self.print.add(self._embed(audio), seconds)
        return True


class SpeakerEmbedder:
    """WeSpeaker ResNet34 (VoxCeleb) through sherpa-onnx: clip in, 256-number voice vector out."""

    def __init__(self, model: Path = MODELS / "speaker.onnx"):
        import sherpa_onnx as so
        self._ex = so.SpeakerEmbeddingExtractor(
            so.SpeakerEmbeddingExtractorConfig(model=str(model), num_threads=1))

    def __call__(self, audio: np.ndarray) -> np.ndarray:
        s = self._ex.create_stream()
        s.accept_waveform(RATE, np.ascontiguousarray(audio, dtype=np.float32))
        s.input_finished()
        e = np.array(self._ex.compute(s), dtype=np.float32)
        return e / np.linalg.norm(e)
