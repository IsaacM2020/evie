"""Debug recorder: keeps Isaac's open-mic sentences as audio so the ears can be measured.

Off unless Isaac turns it on in the panel. Only sentences voice ID thinks are Isaac's (or unsure)
are kept, never other people's; everything is deleted after 7 days. evals/replay.py plays them
back through the ears to measure word errors and echo before and after a change.
"""
import json
import time
import uuid
from pathlib import Path
from typing import Callable

import numpy as np

from evie.ears import RATE, pcm_to_wav

REC_DIR = Path.home() / "Library/Application Support/Evie/recordings"
KEEP_S = 7 * 86400


class SegmentRecorder:
    def __init__(self, folder: Path = REC_DIR, clock: Callable[[], float] = time.time):
        self._dir, self._clock = folder, clock
        self._switch = folder / "recorder.json"
        self.enabled = False
        try:
            self.enabled = bool(json.loads(self._switch.read_text()).get("on"))
        except (OSError, ValueError):
            pass

    def set(self, on: bool) -> None:
        self.enabled = on
        self._dir.mkdir(parents=True, exist_ok=True)
        self._switch.write_text(json.dumps({"on": on}))

    def save(self, audio: np.ndarray, speaker: str, sim: float, text: str, evie_speaking: bool) -> str | None:
        if not self.enabled or speaker == "other":
            return None
        self._dir.mkdir(parents=True, exist_ok=True)
        name = f"{int(self._clock())}-{uuid.uuid4().hex[:6]}"
        (self._dir / f"{name}.wav").write_bytes(pcm_to_wav(audio))
        row = {"name": name, "t": self._clock(), "speaker": speaker, "sim": round(float(sim), 2), "text": text,
               "evie_speaking": evie_speaking, "seconds": round(len(audio) / RATE, 2)}
        with (self._dir / "segments.jsonl").open("a") as f:
            f.write(json.dumps(row) + "\n")
        return name

    def rows(self) -> list[dict]:
        p = self._dir / "segments.jsonl"
        if not p.exists():
            return []
        out = []
        for line in p.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def prune(self) -> None:
        cutoff = self._clock() - KEEP_S
        keep = []
        for r in self.rows():
            if r["t"] < cutoff:
                (self._dir / f"{r['name']}.wav").unlink(missing_ok=True)
            else:
                keep.append(r)
        if (self._dir / "segments.jsonl").exists():
            (self._dir / "segments.jsonl").write_text("".join(json.dumps(r) + "\n" for r in keep))
