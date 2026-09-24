"""Evie's ears: turn a WAV recording into text.

Groq's hosted Whisper large-v3-turbo first (Isaac, 2026-09-23): more accurate than the local
small model (WER 0.054 vs 0.084 on the replay set) for ~70 ms more on a warm connection, and it
frees ~470 MB of GPU memory. Only Isaac's own sentences get here (voice ID drops other people's
on the Mac first). If Groq is down or slow, local MLX Whisper takes over, loaded only then and
unloaded again after 10 idle minutes."""
import asyncio
import io
import json
import math
import logging
import re
import tempfile
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

from evie.config import Settings

log = logging.getLogger("evie.stt")

# small.en: ~315 ms for a 3 s sentence on the M4, vs ~1.2 s for large-v3-turbo (measured
# 2026-09-23). Evie needs the verdict fast; Jev already copes with small mishearings.
MODEL = "mlx-community/whisper-small.en-mlx"
MIN_SECONDS = 0.3
GROQ_TIMEOUT_S = 2.5
IDLE_UNLOAD_S = 600
# Words Whisper should expect: names it would otherwise mangle.
VOCAB = ("Evie, Isaac, IsaacOS, Todoist, Spotify, WhatsApp, iGEM, IB, TOK, Jev, Claude Code, Notion, "
         "Safari, Netflix, YouTube, MrBeast, Sabrina Carpenter, Mr Tan, chem IA, Singapore.")
VOCAB_FILE = Path.home() / "Library/Application Support/Evie/vocab.json"  # names he's spelled out
PROMPT_MAX = 700  # characters: Whisper only reads the last ~224 tokens of a prompt


class Vocab:
    """The words Whisper is told to expect: the fixed list, the people he talks about (people.json)
    and names he spelled out loud ("Parrot, P-A-R-R-O-T", 2026-09-24), newest first."""

    def __init__(self, path: Path = VOCAB_FILE, people: dict[str, str] | None = None, keep: int = 40):
        self._path, self._keep = path, keep
        self._people = list(dict.fromkeys((people or {}).values()))
        try:
            self._learned = [str(w) for w in json.loads(path.read_text())][:keep]
        except (OSError, ValueError, TypeError):
            self._learned = []

    def learn(self, word: str) -> None:
        if not word or word in self._learned:
            return
        self._learned = [word, *self._learned][: self._keep]
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._learned))
        except OSError:
            log.warning("couldn't save a learned word")

    def prompt(self) -> str:
        words = list(dict.fromkeys([*self._learned, *self._people, *[w.strip(" .") for w in VOCAB.split(",")]]))
        out = ""
        for w in words:
            if len(out) + len(w) + 2 > PROMPT_MAX:
                break
            out = f"{out}, {w}" if out else w
        return out + "."


# "P-A-R-R-O-T": three or more letters joined by dashes is Isaac spelling a name.
_SPELLED = re.compile(r"\b[A-Za-z](?:-[A-Za-z]){2,}\b")


def join_spelled(text: str) -> tuple[str, list[str]]:
    words: list[str] = []

    def one(m: re.Match) -> str:
        w = m.group(0).replace("-", "")
        w = w[0].upper() + w[1:].lower()
        words.append(w)
        return w
    return _SPELLED.sub(one, text), words


# Whisper spells her name like the Pokemon. Only exact known mishearings: "eve" stays, since
# "the eve of the match" is a real phrase (Jev is told about those in its state text instead).
_NAME_FIXES = re.compile(r"\beevee\b", re.IGNORECASE)


def _mlx_transcribe(path: str) -> str:
    import mlx.core as mx
    import mlx_whisper  # heavy import, only when local Whisper actually runs
    # MLX keeps GPU scratch buffers cached after each run: ~1.6 GB for small.en. Capping the
    # cache and clearing it after each sentence keeps it ~0.9 GB for ~20 ms (measured 2026-09-23).
    mx.set_cache_limit(64 * 1024 * 1024)
    try:
        return mlx_whisper.transcribe(path, path_or_hf_repo=MODEL)["text"]
    finally:
        mx.clear_cache()


def _mlx_unload() -> None:
    """Drop the local Whisper model and its GPU buffers (~470 MB)."""
    import gc

    import mlx.core as mx
    from mlx_whisper.transcribe import ModelHolder
    ModelHolder.model = None
    ModelHolder.model_path = None
    gc.collect()
    mx.clear_cache()


def _seconds(audio: bytes) -> float:
    try:
        with wave.open(io.BytesIO(audio)) as w:
            return w.getnframes() / w.getframerate()
    except (wave.Error, EOFError):
        return 0.0


@dataclass(frozen=True)
class Heard:
    """What Whisper heard, and how sure it was. confidence is exp(mean avg_logprob): ~0.8+ is a
    clear sentence, under 0.5 means mumbled or misheard words. noise is Whisper's own rule for
    words it wrote over silence (no_speech_prob > 0.6 and avg_logprob < -1)."""
    text: str
    confidence: float = 1.0
    no_speech: float = 0.0

    @property
    def noise(self) -> bool:
        return self.no_speech > 0.6 and math.log(max(self.confidence, 1e-6)) < -1.0


class Transcriber:
    def __init__(self, settings: Settings, backend: str = "groq",
                 local_fn: Callable[[str], str] = _mlx_transcribe, http: httpx.AsyncClient | None = None,
                 unload_fn: Callable[[], None] = _mlx_unload, clock: Callable[[], float] = time.monotonic,
                 vocab: Vocab | None = None):
        if backend not in ("local", "groq"):
            raise ValueError(f"unknown stt backend {backend!r}")
        self._s, self.backend, self._local, self._unload = settings, backend, local_fn, unload_fn
        self._http = http or httpx.AsyncClient(timeout=GROQ_TIMEOUT_S)
        self._clock = clock
        self._vocab = vocab
        # MLX isn't thread safe, so every local transcription runs on this one thread.
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper")
        self._local_used_at: float | None = None  # None: the local model isn't loaded
        self.offline = False  # Groq failed last time and local Whisper answered instead

    async def transcribe(self, audio: bytes) -> str:
        return (await self.transcribe_detail(audio)).text

    async def transcribe_detail(self, audio: bytes) -> Heard:
        if _seconds(audio) < MIN_SECONDS:
            return Heard("")
        heard = None
        if self.backend == "groq":
            try:
                heard = await self._groq(audio)
                self.offline = False
            except (httpx.HTTPError, KeyError, ValueError) as e:
                log.warning("groq whisper failed (%s), using local whisper", type(e).__name__)
                self.offline = True
        if heard is None:
            heard = Heard(await asyncio.get_running_loop().run_in_executor(self._pool, self._run_local, audio))
        text, spelled = join_spelled(_NAME_FIXES.sub("Evie", heard.text))
        for w in spelled:
            if self._vocab is not None:
                self._vocab.learn(w)
        return Heard(text, heard.confidence, heard.no_speech)

    async def transcribe_pcm(self, audio) -> str:
        return (await self.transcribe_pcm_detail(audio)).text

    async def transcribe_pcm_detail(self, audio) -> Heard:
        """The open mic hands over raw samples (float32, 16 kHz), not a WAV file."""
        from evie.ears import pcm_to_wav
        return await self.transcribe_detail(pcm_to_wav(audio))

    def _run_local(self, audio: bytes) -> str:
        self._local_used_at = self._clock()
        with tempfile.NamedTemporaryFile(suffix=".wav") as f:
            f.write(audio)
            f.flush()
            return self._local(f.name).strip()

    def maybe_unload(self) -> bool:
        """Called now and then: frees the local model once it's been idle for 10 minutes."""
        if self._local_used_at is None or self._clock() - self._local_used_at < IDLE_UNLOAD_S:
            return False
        self._local_used_at = None
        self._pool.submit(self._unload).result()
        log.info("local whisper unloaded after %d idle minutes", IDLE_UNLOAD_S // 60)
        return True

    async def _groq(self, audio: bytes) -> Heard:
        r = await self._http.post(
            f"{self._s.groq_url}/audio/transcriptions",
            headers={"Authorization": f"Bearer {self._s.groq_key}"},
            data={"model": "whisper-large-v3-turbo", "language": "en", "response_format": "verbose_json",
                  "temperature": "0", "prompt": self._vocab.prompt() if self._vocab else VOCAB},
            files={"file": ("speech.wav", audio, "audio/wav")},
        )
        r.raise_for_status()
        d = r.json()
        segs = [x for x in d.get("segments") or [] if isinstance(x, dict)]
        if not segs:
            return Heard(d["text"].strip())
        logp = sum(float(x.get("avg_logprob", 0.0)) for x in segs) / len(segs)
        return Heard(d["text"].strip(), round(math.exp(logp), 3),
                     max(float(x.get("no_speech_prob", 0.0)) for x in segs))

    async def warm(self) -> None:
        """Groq: open the connection now so Isaac's first sentence isn't slow. Local: load the model."""
        if self.backend == "groq":
            try:
                await self._http.head(f"{self._s.groq_url}/models")
            except httpx.HTTPError:
                pass
            return
        silence = io.BytesIO()
        with wave.open(silence, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\x00\x00" * 16000)
        await asyncio.get_running_loop().run_in_executor(self._pool, self._run_local, silence.getvalue())

    async def aclose(self) -> None:
        await self._http.aclose()
        self._pool.shutdown(wait=False)


# What Whisper writes when it's given noise, not speech. On the open mic these are dropped before
# Jev ever sees them (2026-09-24: a third of the day's segments were "Thank you.").
_HALLUCINATIONS = {"thank you", "thank you so much", "thanks for watching", "thank you for watching",
                   "thanks for watching and see you next time", "you", "bye", "subtitles by the amara org community"}


def is_hallucination(text: str) -> bool:
    words = " ".join(re.sub(r"[^a-z ]", " ", text.lower()).split())
    return not words or words in _HALLUCINATIONS
