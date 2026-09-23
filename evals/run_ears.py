"""Phase 2 ears eval: does the open mic find each sentence, and does voice ID keep strangers out?

    uv run python -m evals.run_ears [--isaac-at 0.70 --other-below 0.45]

Voices are macOS `say` voices (clips cached in evals/ears/clips, not in git). One voice plays
"Isaac": 8 clips build his voiceprint (like 8 talk-key presses), then 12 new sentences test it.
Seven other voices, men and women, say 20 sentences, many of them commands to Evie. They must
NEVER come out as Isaac, because that's what would let a YouTube video or a guest boss her
around. Loads only the small sherpa-onnx models (VAD + speaker, ~60 MB): no Whisper, no Jev,
and it never touches the real voiceprint.
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from evie.ears import FRAME, RATE, End, Peek, Segmenter, Vad, wav_to_pcm
from evie.voiceid import SpeakerEmbedder, VoiceBars, VoiceId, VoicePrint

HERE = Path(__file__).resolve().parent
CLIPS = HERE / "ears" / "clips"

ISAAC_VOICE = "Aman"
ENROLL = [
    "Evie, what's on my calendar tomorrow morning?",
    "Play some lofi and turn the volume down a little.",
    "Remind me to send the iGEM slides to Mr Tan on Friday.",
    "The quick brown fox jumps over the lazy dog by the river.",
    "I think the chase model is off by one in the second innings.",
    "Set a timer for twenty minutes, then remind me to stretch.",
    "Can you open Notion and find my chemistry notes from last week?",
    "Honestly the second half of that match was insane.",
]
ISAAC_TEST = [
    "Evie, what time is it?", "Pause the music.", "Evie, fix the failing test in the cricket repo.",
    "What's on Friday?", "Mom, can you drive me to the dentist on Wednesday?",
    "Remember that my locker code is four one two nine.", "Turn it up a bit.",
    "And what about Saturday?", "Open Safari and go to YouTube.",
    "Bro that physics paper was brutal, question six made no sense.",
    "Evie, add a test for it as well.", "Play Espresso by Sabrina Carpenter.",
]
STRANGERS = {
    "Daniel": ["Evie, play some music.", "Evie, what's the time?", "Right, let's turn to page forty two."],
    "Reed": ["Evie, stop the job.", "And that's why the mitochondria matters.", "Evie, delete my notes."],
    "Eddy": ["Hey Evie, open Spotify.", "Subscribe for more videos like this one.", "Evie, turn the volume up."],
    "Ralph": ["Evie, remind me to buy milk.", "The market fell three percent today."],
    "Karen": ["Evie, what's on my calendar?", "Isaac, dinner's ready, come down.", "Evie, pause."],
    "Moira": ["Evie, send a message to Mom.", "Okay class, open your textbooks.", "Evie, set a timer for ten minutes."],
    "Flo": ["Evie, play Espresso.", "Did you finish your homework yet?", "Evie, what's the weather tomorrow?"],
}


def clip(voice: str, text: str) -> np.ndarray:
    CLIPS.mkdir(parents=True, exist_ok=True)
    name = CLIPS / f"{voice}-{hashlib.sha1(text.encode()).hexdigest()[:10]}.wav"
    if not name.exists():
        with tempfile.TemporaryDirectory() as d:
            aiff = Path(d) / "c.aiff"
            subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True)
            subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(name)],
                           check=True)
    return wav_to_pcm(name.read_bytes())


def room(audio: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Quiet room noise around the sentence, like a real open mic: 1 s before, 2 s after (the
    VAD lets go ~200 ms after speech ends, then the Segmenter waits 600 ms more)."""
    pad = lambda n: (rng.standard_normal(n) * 0.003).astype(np.float32)  # noqa: E731
    return np.concatenate([pad(RATE), audio + pad(len(audio)), pad(2 * RATE)])


def segments(audio: np.ndarray, vad: Vad) -> tuple[list[End], int, list[float]]:
    """Stream frame by frame, as the app does. Returns sentences, peeks, and End delay after speech."""
    seg, ends, peeks = Segmenter(), [], 0
    delays: list[float] = []
    last_speech = 0
    for i in range(0, len(audio) - FRAME + 1, FRAME):
        f = audio[i:i + FRAME]
        speech = vad.is_speech(f)
        if speech:
            last_speech = i
        for ev in seg.feed(f, speech):
            if isinstance(ev, Peek):
                peeks += 1
            if isinstance(ev, End):
                ends.append(ev)
                delays.append((i - last_speech) / RATE)
    return ends, peeks, delays


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--isaac-at", type=float, default=VoiceBars().isaac_at)
    ap.add_argument("--other-below", type=float, default=VoiceBars().other_below)
    a = ap.parse_args()
    bars = VoiceBars(isaac_at=a.isaac_at, other_below=a.other_below)
    rng = np.random.default_rng(7)
    vad, emb = Vad(), SpeakerEmbedder()
    with tempfile.TemporaryDirectory() as d:
        vid = VoiceId(emb, VoicePrint(Path(d) / "vp.json", bars=bars), bars)
        for text in ENROLL:
            vid.learn(clip(ISAAC_VOICE, text))
        assert vid.print.ready

        rows = []  # (group, voice, text, n_segments, speaker, sim)
        delays: list[float] = []
        tests = [("isaac", ISAAC_VOICE, t) for t in ISAAC_TEST] + \
                [("stranger", v, t) for v, ts in STRANGERS.items() for t in ts]
        for group, voice, text in tests:
            ends, _, dl = segments(room(clip(voice, text), rng), vad)
            delays += dl
            if not ends:
                rows.append((group, voice, text, 0, "-", 0.0))
                continue
            audio = max(ends, key=lambda e: len(e.audio)).audio
            speaker, sim = vid.who(audio)
            rows.append((group, voice, text, len(ends), speaker, sim))

    iso = [r for r in rows if r[0] == "isaac"]
    stra = [r for r in rows if r[0] == "stranger"]
    stranger_as_isaac = [r for r in stra if r[4] == "isaac"]
    isaac_ok = sum(r[4] == "isaac" for r in iso) / len(iso)
    one_seg = sum(r[3] == 1 for r in rows) / len(rows)
    for r in sorted(rows, key=lambda r: (r[0], -r[5])):
        print(f"{r[0]:<9} {r[1]:<7} sim={r[5]:.2f} {r[4]:<8} segs={r[3]} \"{r[2]}\"")
    print()
    print(f"bars: isaac_at={bars.isaac_at} other_below={bars.other_below}")
    print(f"sentence found as exactly 1 segment: {one_seg:.2f}")
    print(f"end-of-speech -> End event: median {np.median(delays) * 1000:.0f} ms")
    print(f"isaac sims: min {min(r[5] for r in iso):.2f}  median {np.median([r[5] for r in iso]):.2f}")
    print(f"stranger sims: max {max(r[5] for r in stra):.2f}  median {np.median([r[5] for r in stra]):.2f}")
    print(f"strangers classified isaac: {len(stranger_as_isaac)}  (gate: 0)")
    print(f"isaac accepted: {isaac_ok:.2f}  (gate: >= 0.80)")
    print(f"strangers dropped before Whisper (other): {sum(r[4] == 'other' for r in stra)}/{len(stra)}")
    ok = not stranger_as_isaac and isaac_ok >= 0.80
    print("PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
