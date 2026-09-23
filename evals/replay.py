"""Replay eval for the ears: how many words does each transcriber get wrong, and how fast?

    uv run python -m evals.replay --synthetic --backend local groq
    uv run python -m evals.replay --recorded  --backend groq      # Isaac's own recorded sentences

--synthetic: the ears-eval "Isaac" sentences (macOS `say`, known text), so WER is exact.
--recorded:  sentences the debug recorder kept (~/Library/Application Support/Evie/recordings).
             Labels come from evals/replay/labels.jsonl ({"name": ..., "text": ..., "echo": bool});
             unlabelled ones are only timed. Also counts likely split sentences (a new sentence
             starting under 1 s after the last one ended) and segments heard while Evie spoke.
Loads Whisper only when --backend local is asked for (a one-off eval process, not the core).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

import numpy as np

from evals.metrics import wer
from evie.ears import pcm_to_wav, wav_to_pcm
from evie.recorder import REC_DIR

HERE = Path(__file__).resolve().parent
LABELS = HERE / "replay" / "labels.jsonl"


def synthetic() -> list[tuple[str, np.ndarray, str | None]]:
    from evals.run_ears import ISAAC_TEST, ISAAC_VOICE, clip
    pad = np.zeros(int(0.3 * 16000), dtype=np.float32)  # like the open mic's pre-roll
    return [(f"{ISAAC_VOICE}-{i}", np.concatenate([pad, clip(ISAAC_VOICE, t), pad]), t)
            for i, t in enumerate(ISAAC_TEST)]


def recorded(folder: Path = REC_DIR) -> tuple[list[tuple[str, np.ndarray, str | None]], dict]:
    from evie.recorder import SegmentRecorder
    rows = SegmentRecorder(folder).rows()
    labels = {}
    if LABELS.exists():
        for line in LABELS.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                labels[r["name"]] = r
    items, prev_end, splits, while_evie = [], None, 0, 0
    for r in sorted(rows, key=lambda r: r["t"]):
        wav = folder / f"{r['name']}.wav"
        if not wav.exists():
            continue
        start = r["t"] - r["seconds"]
        if prev_end is not None and 0 <= start - prev_end < 1.0:
            splits += 1
        prev_end = r["t"]
        while_evie += bool(r.get("evie_speaking"))
        lab = labels.get(r["name"])
        items.append((r["name"], wav_to_pcm(wav.read_bytes()), lab["text"] if lab else None))
    echo = sum(1 for lab in labels.values() if lab.get("echo"))
    return items, {"segments": len(items), "likely_splits": splits, "heard_while_evie_spoke": while_evie,
                   "labelled_echo": echo}


async def run(items, backend: str) -> dict:
    from evie.config import load_settings
    from evie.stt import Transcriber
    stt = Transcriber(load_settings(), backend=backend)
    await stt.warm()
    wers, ms, worst = [], [], []
    for name, audio, ref in items:
        t0 = time.perf_counter()
        hyp = await stt.transcribe(pcm_to_wav(audio))
        ms.append((time.perf_counter() - t0) * 1000)
        if ref is not None:
            w = wer(ref, hyp)
            wers.append(w)
            if w > 0:
                worst.append((round(w, 2), ref, hyp))
    await stt.aclose()
    ms.sort()
    return {"backend": backend, "n": len(items), "wer": round(statistics.mean(wers), 3) if wers else None,
            "ms_p50": round(ms[len(ms) // 2]) if ms else None,
            "ms_p95": round(ms[min(len(ms) - 1, int(len(ms) * 0.95))]) if ms else None,
            "mistakes": sorted(worst, reverse=True)[:8]}


def main() -> None:
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--synthetic", action="store_true")
    src.add_argument("--recorded", action="store_true")
    ap.add_argument("--backend", nargs="+", default=["groq"], choices=["local", "groq"])
    a = ap.parse_args()
    if a.synthetic:
        items, extra = synthetic(), {}
    else:
        items, extra = recorded()
    if extra:
        print(json.dumps(extra))
    for b in a.backend:
        print(json.dumps(asyncio.run(run(items, b))))


if __name__ == "__main__":
    main()
