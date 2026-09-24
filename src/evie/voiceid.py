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


CHANNELS = ("raw", "live")  # raw: talk-key recordings; live: the open mic's echo-cancelled audio


class VoicePrint:
    """Two prints of the same voice. Apple's voice processing (the open mic's echo cancel) changes
    how Isaac sounds, so comparing open-mic audio to a print learned from raw talk-key clips put
    him at 0.52-0.73 (2026-09-24). The live print is learned from open-mic-path audio that is
    certainly him, and takes over once it has enough clips."""

    def __init__(self, path: Path | None = VOICEPRINT, keep: int = 40, bars: VoiceBars = VoiceBars()):
        self._path, self._keep, self._bars = path, keep, bars
        self._embs: dict[str, list[list[float]]] = {c: [] for c in CHANNELS}
        self._secs: dict[str, list[float]] = {c: [] for c in CHANNELS}
        self._load()

    def _load(self) -> None:
        if not self._path or not self._path.exists():
            return
        try:
            d = json.loads(self._path.read_text())
            self._embs["raw"], self._secs["raw"] = d["embs"][-self._keep:], d["secs"][-self._keep:]
            self._embs["live"] = d.get("live_embs", [])[-self._keep:]
            self._secs["live"] = d.get("live_secs", [])[-self._keep:]
        except (ValueError, KeyError, TypeError):
            log.warning("voiceprint file unreadable, starting fresh")
            self._embs, self._secs = {c: [] for c in CHANNELS}, {c: [] for c in CHANNELS}

    def _save(self) -> None:
        if not self._path:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"embs": self._embs["raw"], "secs": self._secs["raw"],
                                   "live_embs": self._embs["live"], "live_secs": self._secs["live"]}))
        tmp.replace(self._path)

    def add(self, emb: np.ndarray, seconds: float, channel: str = "raw") -> None:
        e = np.asarray(emb, dtype=np.float32)
        self._embs[channel] = (self._embs[channel] + [(e / np.linalg.norm(e)).tolist()])[-self._keep:]
        self._secs[channel] = (self._secs[channel] + [round(float(seconds), 2)])[-self._keep:]
        self._save()

    def clear(self) -> None:
        self._embs, self._secs = {c: [] for c in CHANNELS}, {c: [] for c in CHANNELS}
        self._save()

    def mean(self, channel: str = "raw") -> np.ndarray | None:
        if not self._embs[channel]:
            return None
        m = np.mean(np.array(self._embs[channel], dtype=np.float32), axis=0)
        return m / np.linalg.norm(m)

    def ready_for(self, channel: str) -> bool:
        if channel == "live":  # only takes over with clips, never on a few long ones
            return len(self._embs["live"]) >= self._bars.ready_clips
        return len(self._embs[channel]) >= self._bars.ready_clips or sum(self._secs[channel]) >= self._bars.ready_seconds

    @property
    def ready(self) -> bool:
        return self.ready_for("raw") or self.ready_for("live")

    def status(self) -> dict:
        return {"clips": len(self._embs["raw"]), "seconds": round(sum(self._secs["raw"]), 1), "ready": self.ready,
                "live_clips": len(self._embs["live"])}


class VoiceId:
    def __init__(self, embed_fn: Callable[[np.ndarray], np.ndarray], voiceprint: VoicePrint,
                 bars: VoiceBars = VoiceBars()):
        self._embed, self.print, self.bars = embed_fn, voiceprint, bars
        self._lock = threading.Lock()  # the open mic and the talk key can both call in

    def who(self, audio: np.ndarray) -> tuple[str, float]:
        """(speaker, similarity). Blocking (~50 ms): call it from a worker thread."""
        channel = "live" if self.print.ready_for("live") else "raw"
        ref = self.print.mean(channel)
        if ref is None or not self.print.ready_for(channel) or len(audio) / RATE < self.bars.min_seconds:
            return "unknown", 0.0
        with self._lock:
            e = np.asarray(self._embed(audio), dtype=np.float32)
        sim = float(ref @ (e / np.linalg.norm(e)))
        if sim >= self.bars.isaac_at:
            return "isaac", sim
        if sim < self.bars.other_below:
            return "other", sim
        return "unknown", sim

    def learn(self, audio: np.ndarray, channel: str = "raw") -> bool:
        """Add a clip that is certainly Isaac (he held the talk key, or said "Evie" in a voice that
        already matched). Blocking."""
        seconds = len(audio) / RATE
        if seconds < self.bars.learn_min_seconds:
            return False
        with self._lock:
            self.print.add(self._embed(audio), seconds, channel)
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
