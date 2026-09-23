"""Fast skills: the quick things Evie does herself in well under a second.

Jev already picked the skill (in the same call that decided the route). This file reads the
details with plain code where it can (volume numbers, timer lengths, app names), asks Groq only
for free text (a song, a website), does the thing, checks it worked, and logs it. Anything that
isn't a fast skill returns said=None and goes to Claude Code, the general hands.
"""
import json
import logging
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urlparse

from evie.jev import JevError
from evie.skills.parse import match_app, normalize_url, parse_duration, parse_volume, say_duration

log = logging.getLogger("evie.skills")

ACTIONS_LOG = Path.home() / "Library/Logs/Evie/actions.jsonl"

# What could go wrong if Evie acts on the wrong sentence. Everything in Phase 3 is read-only or
# undoable; sends (3b), deletes and money will need Isaac's own voice (see allowed()).
RISK = {
    "music_play": "reversible", "music_pause": "reversible", "music_resume": "reversible",
    "music_next": "reversible", "music_previous": "reversible", "now_playing": "read_only",
    "volume": "reversible", "open_app": "reversible", "open_website": "reversible",
    "timer_set": "reversible", "timer_cancel": "reversible", "undo": "reversible", "other": "unknown",
    "event_move": "reversible", "event_delete": "deletes", "task_done": "reversible",
}

MUSIC_Q = ('What music does Isaac want played? Return {"query": string, "kind": "track" | "artist" | '
           '"album" | "playlist"}. query is the song, artist, album or playlist name as it would be '
           'searched on Spotify, or "" if he named nothing specific (like "play some music").')
WEB_Q = ('Which web page does Isaac want opened? Return {"url": string}: a full https address. For a '
         'search on YouTube use https://www.youtube.com/results?search_query=WORDS, otherwise for a '
         'search use https://www.google.com/search?q=WORDS (words joined with +).')
TIMER_Q = 'How long is the timer? Return {"seconds": integer}, or {"seconds": 0} if no length was said.'


def allowed(risk: str, speaker: str, addressed: bool) -> bool:
    """Undoable things can come from the open mic; sending, deleting or spending needs Isaac's
    matched voice or the talk key."""
    if risk in ("read_only", "reversible"):
        return True
    return speaker == "isaac" or addressed


@dataclass
class Done:
    said: str | None  # None: not a fast skill, hand it to Claude Code
    ok: bool = True
    verified: bool | None = None
    detail: str = ""


def failed(detail: str) -> Done:
    return Done(f"Couldn't do that: {detail}.", ok=False, detail=detail)


class Skills:
    def __init__(self, hands, talker, jev, system, spotify, timers, apps: Callable[[], list[str]],
                 log: Path | None = ACTIONS_LOG):
        self._hands, self._talker, self._jev = hands, talker, jev
        self._sys, self._search, self.timers = system, spotify, timers
        self._apps, self._log = apps, log
        self.events = None  # EventSkills (move / delete calendar events), wired in by the server
        self.tasks = None  # TaskSkills (tick off a Todoist task)
        self._undo_stack: deque[Callable[[], Awaitable[str]]] = deque(maxlen=10)

    def remember_undo(self, fn: Callable[[], Awaitable[str]]) -> None:
        """Other hands (remember: a new task or event) can register how to take it back."""
        self._undo_stack.append(fn)

    async def run(self, skill: str, text: str) -> Done:
        fn = getattr(self, f"_{skill}", None) if skill in RISK and skill != "other" else None
        if fn is None:
            return Done(None)
        try:
            done = await fn(text)
        except Exception as e:  # a broken skill must never hang or crash a turn
            log.exception("skill %s failed", skill)
            done = failed("something broke")
            done.detail = repr(e)
        self._write_log(skill, done)
        return done

    # -- music (the Evie app drives Spotify) -------------------------------------------------
    async def _spotify_op(self, op: str, said: Callable[[dict], str], **args) -> Done:
        r = await self._hands.do(op, timeout=10.0 if op == "spotify_play" else 5.0, **args)
        if not r.ok:
            return failed(r.detail)
        return Done(said(r.data), verified=r.data.get("state") in ("playing", "paused") or None, detail=r.detail)

    async def _music_play(self, text: str) -> Done:
        q = await self._talker.extract(MUSIC_Q, text)
        query = str(q.get("query") or "").strip()
        if not query:
            return await self._music_resume(text)
        found = await self._search.find(query, str(q.get("kind") or "track"))
        if not found:
            return Done(f"Couldn't find {query} on Spotify.", ok=False, detail="not found")
        uri, label = found
        r = await self._hands.do("spotify_play", timeout=10.0, uri=uri)  # may have to launch Spotify
        if not r.ok:
            return failed(r.detail)
        # Worked means: for a song, Spotify's current track IS that song; otherwise it's playing.
        verified = r.data.get("uri") == uri if uri.startswith("spotify:track:") else r.data.get("state") == "playing"
        return Done(f"Playing {label}.", verified=verified)

    async def _music_pause(self, text: str) -> Done:
        return await self._spotify_op("spotify_pause", lambda d: "Paused.")

    async def _music_resume(self, text: str) -> Done:
        return await self._spotify_op("spotify_resume", lambda d: f"Playing {d['name']}." if d.get("name") else "Playing.")

    async def _music_next(self, text: str) -> Done:
        return await self._spotify_op("spotify_next", lambda d: f"Next up, {d['name']} by {d['artist']}."
                                   if d.get("name") else "Skipped.")

    async def _music_previous(self, text: str) -> Done:
        return await self._spotify_op("spotify_previous", lambda d: f"Back to {d['name']}." if d.get("name") else "Gone back.")

    async def _now_playing(self, text: str) -> Done:
        r = await self._hands.do("spotify_state")
        if not r.ok:
            return Done("Nothing's playing, Spotify isn't open.", ok=False, detail=r.detail)
        d = r.data
        if d.get("state") == "playing":
            return Done(f"This is {d['name']} by {d['artist']}.")
        return Done("Nothing's playing right now.")

    # -- volume (osascript in the core, no permission needed) ---------------------------------
    async def _volume(self, text: str) -> Done:
        p = parse_volume(text)
        if p is None:
            return Done("How loud? Say a number, like volume 30.", ok=False, detail="no level")
        kind, n = p
        if kind in ("mute", "unmute"):
            on = kind == "mute"
            if not await self._sys.set_muted(on):
                return failed("the Mac didn't let me")
            self._undo_stack.append(lambda: self._undo_mute(not on))
            return Done("Muted." if on else "Unmuted.", verified=True)
        before = await self._sys.get_volume()
        base = before if before is not None else 50
        target = n if kind == "set" else max(0, min(100, base + (n if kind == "up" else -n)))
        if not await self._sys.set_volume(target):
            return failed("the Mac didn't let me")
        after = await self._sys.get_volume()
        if before is not None:
            self._undo_stack.append(lambda: self._undo_volume(before))
        return Done(f"Volume {target}.", verified=after is not None and abs(after - target) <= 2)

    async def _undo_volume(self, level: int) -> str:
        await self._sys.set_volume(level)
        return f"Volume's back to {level}."

    async def _undo_mute(self, on: bool) -> str:
        await self._sys.set_muted(on)
        return "Muted again." if on else "Unmuted."

    # -- apps and websites ------------------------------------------------------------------
    async def _open_app(self, text: str) -> Done:
        apps = self._apps()
        name = match_app(text, apps) or await self._pick_app(text, apps)
        if not name:
            return Done("I couldn't find that app on your Mac.", ok=False, detail="no such app")
        if not await self._sys.open_app(name):
            return failed(f"{name} wouldn't open")
        return Done(f"Opening {name}.", verified=await self._sys.app_running(name))

    async def _pick_app(self, text: str, apps: list[str]) -> str | None:
        """Jev picks from the real list of installed apps, so it can't make one up."""
        options = {a: a for a in apps[:250]} | {"none": "None of these apps"}
        q = {"app": {"type": "choice", "instructions": "Which app on Isaac's Mac does he want opened?",
                     "criteria": options}}
        try:
            res = await self._jev.ask(f'Isaac said: "{text}"', q)
            a = res.answers["app"]
        except (JevError, KeyError, TypeError):
            return None
        choice = a.get("choice")
        return choice if choice in apps and float(a.get("confidence", 0)) >= 0.5 else None

    async def _open_website(self, text: str) -> Done:
        url = normalize_url(str((await self._talker.extract(WEB_Q, text)).get("url") or ""))
        if not url:
            return Done("I couldn't work out which site.", ok=False, detail="no url")
        if not await self._sys.open_url(url):
            return failed("the browser didn't open")
        host = (urlparse(url).hostname or url).removeprefix("www.")
        return Done(f"Opening {host}.")

    # -- timers ---------------------------------------------------------------------------
    async def _timer_set(self, text: str) -> Done:
        secs = parse_duration(text)
        if secs is None:
            try:
                secs = int((await self._talker.extract(TIMER_Q, text)).get("seconds") or 0)
            except (TypeError, ValueError):
                secs = 0
        if not secs or secs <= 0 or secs > 24 * 3600:
            return Done("How long should the timer be?", ok=False, detail="no length")
        t = self.timers.start(secs)
        self._undo_stack.append(lambda: self._undo_timer(t.id))
        return Done(f"{say_duration(secs).capitalize()} timer, starting now.", verified=True)

    async def _undo_timer(self, tid: str) -> str:
        return "Timer cancelled." if self.timers.cancel(tid) else "That timer already finished."

    async def _timer_cancel(self, text: str) -> Done:
        return Done("Timer cancelled.") if self.timers.cancel() else Done("No timer running.", ok=False)

    # -- calendar events (skills/events.py) -------------------------------------------------
    async def _event_move(self, text: str) -> Done:
        return await self.events.move(text) if self.events else Done(None)

    async def _event_delete(self, text: str) -> Done:
        return await self.events.delete(text) if self.events else Done(None)

    async def _task_done(self, text: str) -> Done:
        return await self.tasks.done(text) if self.tasks else Done(None)

    async def _undo(self, text: str) -> Done:
        if not self._undo_stack:
            return Done("Nothing to undo.", ok=False)
        return Done(await self._undo_stack.pop()())

    # -- log ------------------------------------------------------------------------------
    def _write_log(self, skill: str, done: Done) -> None:
        if not self._log:
            return
        try:
            self._log.parent.mkdir(parents=True, exist_ok=True)
            with self._log.open("a") as f:
                f.write(json.dumps({"t": time.time(), "skill": skill, "risk": RISK.get(skill), "ok": done.ok,
                                    "verified": done.verified, "said": done.said, "detail": done.detail}) + "\n")
        except OSError:
            log.exception("couldn't write actions log")
