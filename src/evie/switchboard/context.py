"""What Evie knows at the moment a sentence arrives, and how that is written up for Jev.

Jev only sees text, and it gets worse when the text is padded, so the state is short and
every line earns its place.
"""
from dataclasses import dataclass
from typing import Literal

Speaker = Literal["isaac", "other", "unknown"]

MAX_UTTERANCE_CHARS = 600
MAX_RECENT = 3
MAX_RECENT_CHARS = 200

SPEAKER_DESC = {
    "isaac": "Isaac (voice match)",
    "other": "someone else, not Isaac",
    "unknown": "unknown",
}

# Things Whisper "hears" in silence or music. Exact matches only.
WHISPER_JUNK = {
    "you", "bye", "thank you", "thanks for watching", "thank you for watching",
    "subtitles by the amaraorg community",
}


@dataclass(frozen=True)
class Context:
    utterance: str
    speaker: Speaker = "unknown"  # real voice ID arrives in Phase 2
    in_call: bool = False
    front_app: str = ""
    recent: tuple[str, ...] = ()
    active_jobs: tuple[str, ...] = ()
    addressed: bool = False  # Isaac held the talk key or typed to Evie: definitely for her
    followup_s: float | None = None  # open mic: seconds since Evie last answered Isaac


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else "…" + text[-(limit - 1):]


def render_state(ctx: Context) -> str:
    lines = [
        "Isaac is 16 and uses a voice assistant called Evie on his MacBook. The microphone is "
        "always on, so it also hears Isaac talking to other people, online classes, calls and videos.",
        f"Speaker of the latest speech: {SPEAKER_DESC[ctx.speaker]}",
        f"Isaac is in a video call or online class: {'yes' if ctx.in_call else 'no'}",
    ]
    if ctx.addressed:
        lines.append("Isaac held Evie's talk key while saying this, so it is meant for Evie.")
    elif ctx.followup_s is not None:
        lines.append(f"Evie answered Isaac {ctx.followup_s:.0f} seconds ago, so a short follow-up "
                     "question may be for her.")
    if ctx.front_app:
        lines.append(f"App in front: {ctx.front_app}")
    if ctx.active_jobs:
        jobs = [_clip(j, MAX_RECENT_CHARS) for j in ctx.active_jobs[-MAX_RECENT:]]
        lines.append("Evie is currently working on: " + "; ".join(jobs))
    else:
        lines.append("Evie is not working on anything right now.")
    if ctx.recent:
        shown = [_clip(r, MAX_RECENT_CHARS) for r in ctx.recent[-MAX_RECENT:]]
        lines.append("Speech just before: " + " | ".join(shown))
    lines.append(
        'Latest speech (raw transcript, may contain mishearings; "evie" is often heard as '
        f'"eve", "evey" or "ivy"): "{_clip(ctx.utterance, MAX_UTTERANCE_CHARS)}"'
    )
    return "\n".join(lines)


def is_noise(utterance: str) -> bool:
    t = "".join(ch for ch in utterance.lower() if ch.isalnum() or ch == " ")
    t = " ".join(t.split())
    return len(t) < 2 or t in WHISPER_JUNK
