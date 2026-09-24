"""Open mic: Evie hears Isaac without the talk key.

Every sentence the Segmenter cuts goes through, in order:
  1. voice ID  (~50 ms, on the Mac): someone else? Dropped right here, so other people's words
     never reach Whisper, let alone Jev in the cloud.
  2. Whisper   (~300 ms, on the Mac): speech to text. Started early at the 250 ms Peek.
  3. guards: the talk key was held (that turn belongs to push-to-talk), or it's Evie hearing
     her own voice through the speakers.
  4. the Brain, marked addressed=False: Jev and the policy decide whether it was for her.
In shadow mode the Brain only decides and logs what she WOULD have done.
"""
import asyncio
import difflib
import json
import logging
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import numpy as np

from evie.stt import is_hallucination
from evie.ears import End, Drop, Peek, Resume, Segmenter, Start, sounds_unfinished

log = logging.getLogger("evie.open_mic")

MODES = ("off", "shadow", "live")
EARS_FILE = Path.home() / "Library/Application Support/Evie/ears.json"
ECHO_TAIL_S = 0.4
UNFINISHED_END_MS = 1000  # room echo and buffered audio keep arriving just after she stops
# A sentence that ends this soon after the previous one, while that turn is still being worked
# out, is the rest of the same sentence (2026-09-24: chopped sentences got two answers).
MERGE_S = 1.2
# Isaac saying "Evie, ..." in a voice that already clearly matches is certainly him, and this is
# the open mic's own (echo-cancelled) audio: it teaches the live voiceprint (evie/voiceid.py).
LEARN_LIVE_AT = 0.70
_NAMED = re.compile(r"^\s*(hey\s+)?(evie|eve|evey|ivy)\b", re.IGNORECASE)


def _words(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", s.lower()).split())


def is_echo(heard: str, said: str) -> bool:
    """Does what the mic heard look like what Evie is (or just was) saying?"""
    h, s = _words(heard), _words(said)
    if not h or not s:
        return False
    return h in s or difflib.SequenceMatcher(None, h, s).ratio() >= 0.6


class ModeStore:
    def __init__(self, path: Path | None = EARS_FILE):
        self._path = path
        self.mode = "off"
        if path and path.exists():
            try:
                m = json.loads(path.read_text()).get("mode")
                self.mode = m if m in MODES else "off"
            except ValueError:
                pass

    def set(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self.mode = mode
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps({"mode": mode}))


class OpenMic:
    def __init__(self, segmenter: Segmenter, vad: Callable[[np.ndarray], bool], voiceid, stt, brain, mouth,
                 bus, modes: ModeStore, clock: Callable[[], float] = time.monotonic):
        self._seg, self._vad, self._vid, self._stt = segmenter, vad, voiceid, stt
        self._brain, self._mouth, self._bus, self.modes, self._clock = brain, mouth, bus, modes, clock
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="voiceid")
        self._lock = asyncio.Lock()
        self._spec: asyncio.Task | None = None
        self._ptt = False
        self._tainted = False  # this sentence overlapped the talk key
        self._echo_start = False  # Evie was talking when this sentence began
        self.context = {"front_app": "", "in_call": False}
        self.stats: Counter = Counter()  # segments by speaker, echo drops: for tuning, never words
        self.recorder = None  # debug recorder (evie.recorder), off unless Isaac turns it on
        self._inflight: tuple[asyncio.Task, str, str, float] | None = None  # (turn, text, speaker, ended at)

    # -- the talk key owns its own turns --------------------------------------------------
    def ptt_start(self) -> None:
        self._ptt = True
        if self._seg.active:
            self._tainted = True

    def ptt_end(self) -> None:
        self._ptt = False

    # -- frames in ------------------------------------------------------------------------
    def feed(self, frame: np.ndarray) -> None:
        if self.modes.mode == "off":
            if self._seg.active:
                self._seg.reset()
                self._cancel_spec()
            return
        for ev in self._seg.feed(frame, self._vad(frame)):
            if isinstance(ev, Start):
                self._tainted, self._echo_start = self._ptt, bool(self._mouth.speaking)
            elif isinstance(ev, Peek):
                self._cancel_spec()
                self._spec = asyncio.get_running_loop().create_task(self._understand(ev.audio))
                self._spec.add_done_callback(self._maybe_wait_longer)
            elif isinstance(ev, Resume | Drop):
                self._cancel_spec()
            elif isinstance(ev, End):
                if ev.same_as_peek and self._spec:
                    task, self._spec = self._spec, None
                else:
                    self._cancel_spec()
                    task = asyncio.get_running_loop().create_task(self._understand(ev.audio))
                asyncio.get_running_loop().create_task(
                    self._finish(task, self._tainted, self._echo_start, ev.audio))

    def _maybe_wait_longer(self, task: asyncio.Task) -> None:
        """The early transcript sounds mid-sentence ("remind me to"): give him up to a second
        to carry on instead of cutting the sentence in half."""
        if task.cancelled() or task.exception() is not None or task is not self._spec:
            return
        if sounds_unfinished(task.result()[2]):
            self._seg.extend(UNFINISHED_END_MS)

    def _cancel_spec(self) -> None:
        if self._spec:
            self._spec.cancel()
            self._spec = None

    async def _understand(self, audio: np.ndarray) -> tuple[str, float, str]:
        speaker, sim = await asyncio.get_running_loop().run_in_executor(self._pool, self._vid.who, audio)
        if speaker == "other":
            return speaker, sim, ""
        if hasattr(self._stt, "transcribe_pcm_detail"):
            heard = await self._stt.transcribe_pcm_detail(audio)
            if heard.noise:  # words Whisper wrote over silence or room noise
                log.info("open mic dropped noise (no_speech %.2f)", heard.no_speech)
                return speaker, sim, ""
            return speaker, sim, heard.text.strip()
        return speaker, sim, (await self._stt.transcribe_pcm(audio)).strip()

    async def _finish(self, task: asyncio.Task, tainted: bool, echo_start: bool, audio=None) -> None:
        try:
            speaker, sim, text = await task
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("open mic couldn't understand a sentence")
            return
        self.stats["segments"] += 1
        self.stats[speaker] += 1
        if self.recorder is not None and audio is not None:
            try:
                self.recorder.save(audio, speaker, sim, text, evie_speaking=echo_start or bool(self._mouth.speaking))
            except OSError:
                log.exception("debug recorder couldn't save")
        if tainted or self._ptt or self.modes.mode == "off":
            return
        if speaker == "other":
            self._bus.publish("overheard", speaker="other", sim=round(sim, 2))
            return
        if not text or is_hallucination(text):
            return
        log.info("open mic heard %s (sim %.2f, %d chars)", speaker, sim, len(text))  # for voice-ID tuning
        m = self._mouth
        if echo_start or m.speaking or self._clock() - m.quiet_at < ECHO_TAIL_S:
            if speaker != "isaac" or is_echo(text, m.current_text):
                log.info("dropped echo/unsure speech while Evie talked (%s %.2f)", speaker, sim)
                self.stats["echo_dropped"] += 1
                return
            if m.speaking:
                m.stop()  # Isaac talked over her: she stops, like a person would
        # (after the echo check: her own voice must never teach the print)
        if speaker == "isaac" and sim >= LEARN_LIVE_AT and _NAMED.match(text) and audio is not None \
                and hasattr(self._vid, "learn"):
            self._pool.submit(self._vid.learn, audio, "live")
        now = self._clock()
        prev = self._inflight
        if prev and not prev[0].done() and now - prev[3] <= MERGE_S:
            prev[0].cancel()  # the first half's turn: one sentence gets one answer
            text = f"{prev[1]} {text}"
            speaker = "isaac" if "isaac" in (prev[2], speaker) else speaker
            log.info("merged a fragment into the sentence before it")
        task = asyncio.get_running_loop().create_task(self._hear(text, speaker))
        self._inflight = (task, text, speaker, now)
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _hear(self, text: str, speaker: str) -> None:
        async with self._lock:
            try:
                await self._brain.hear(text, speaker, addressed=False, shadow=self.modes.mode == "shadow")
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("open mic turn failed")

    def close(self) -> None:
        self._cancel_spec()
        self._pool.shutdown(wait=False)
