"""Exact, fast paths for the things Isaac asks for most. No step-by-step model loop: a known route
that's quicker and can't wander. Jev picks the recipe from this list (or "none", which goes to
the planner); Groq only reads the free-text bits (what to search, who to message, what to say).
"""
import logging
from urllib.parse import quote_plus

from evie.computer.observe import Screen
from evie.computer.planner import Outcome
from evie.jev import JevError

log = logging.getLogger("evie.computer")

RECIPES = {
    "youtube_play": "Play or find a video on YouTube (a creator, a song video, a topic)",
    "web_search": "Search Google for something and show the results",
    "open_site": "Open a particular website or web address",
    "new_tab": "Open a new browser tab",
    "close_tab": "Close the current browser tab",
    "message_send": "Send a WhatsApp message or a text/iMessage to someone",
    "none": "Anything else: doing something inside an app or a web page",
}

ARGS_Q = ('Isaac wants something done on his Mac. Return {"query": what to search or play (string or null), '
          '"url": a full https address if he named a site (or null), "contact": who to message (or null), '
          '"body": the message text exactly as he wants it sent (or null), "via": "whatsapp" or "imessage" or null, '
          '"app": the app it happens in, like "Safari", "Notes", "WhatsApp", "Finder" (or null)}.')


def _first_video(screen: Screen) -> dict | None:
    vids = [e for e in screen.elements if "/watch?v=" in (e.get("href") or "") and e.get("label")
            and not e["label"].lower().startswith(("shorts", "ad "))]
    on = [e for e in vids if e.get("onscreen") is not False]
    return (on or vids or [None])[0]


class Recipes:
    def __init__(self, hands, jev, talker, planner, messages=None):
        self._hands, self._jev, self._talker, self._planner = hands, jev, talker, planner
        self._messages = messages  # computer/messages.py: contacts + sending

    async def pick(self, text: str) -> str:
        q = {"recipe": {"type": "choice", "instructions": "Which of these does Isaac want done on his Mac?",
                        "criteria": RECIPES}}
        try:
            a = (await self._jev.ask(f'Isaac said: "{text}"', q)).answers["recipe"]
        except (JevError, KeyError, TypeError):
            return "none"
        c = a.get("choice")
        return c if c in RECIPES and float(a.get("confidence", 0)) >= 0.5 else "none"

    async def run(self, text: str) -> Outcome:
        import asyncio
        recipe, args = await asyncio.gather(self.pick(text), self._talker.extract(ARGS_Q, text))
        args = args or {}
        log.info("computer recipe %s %s", recipe, {k: v for k, v in args.items() if k != "body"})
        fn = getattr(self, f"_{recipe}", None)
        if fn is None or recipe == "none":
            return await self._planner.run(text, app=args.get("app"))
        return await fn(text, args)

    # -- browser -----------------------------------------------------------------------------
    async def _youtube_play(self, text: str, a: dict) -> Outcome:
        query = str(a.get("query") or "").strip()
        if not query:
            return Outcome(False, "Which video?", ask=True)
        url = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
        await self._hands.do("open_url", url=url, app="Safari", front=False)  # opens behind whatever he's doing
        await self._hands.do("wait_page", timeout=10.0, app="Safari")
        seen = await self._hands.do("observe", timeout=8.0, app="Safari")
        if not seen.ok:
            return Outcome(False, f"Couldn't read YouTube: {seen.detail}.")
        screen = Screen.from_data(seen.data)
        video = _first_video(screen)
        if video is None:
            return Outcome(False, f"I couldn't find a {query} video.", stuck=True)
        pressed = await self._hands.do("press", id=video["id"], snapshot=screen.snapshot)
        if not pressed.ok:
            return Outcome(False, f"Couldn't start it: {pressed.detail}.", stuck=True)
        await self._hands.do("wait_page", timeout=10.0, app="Safari")
        await self._hands.do("activate", app="Safari")  # he asked to watch it: bring it up
        return Outcome(True, f"Playing {video['label']}.")

    async def _web_search(self, text: str, a: dict) -> Outcome:
        query = str(a.get("query") or "").strip()
        if not query:
            return Outcome(False, "Search for what?", ask=True)
        await self._hands.do("open_url", url=f"https://www.google.com/search?q={quote_plus(query)}", app="Safari",
                             front=True)
        return Outcome(True, f"Here's Google for {query}.")

    async def _open_site(self, text: str, a: dict) -> Outcome:
        url = str(a.get("url") or "").strip()
        if not url.startswith("https://"):
            return await self._planner.run(text, app="Safari")
        await self._hands.do("open_url", url=url, app="Safari", front=True)
        return Outcome(True, "Opened it.")

    async def _new_tab(self, text: str, a: dict) -> Outcome:
        await self._hands.do("activate", app="Safari")
        r = await self._hands.do("key", combo="cmd+t", app="Safari")
        return Outcome(r.ok, "New tab's open." if r.ok else f"Couldn't: {r.detail}.")

    async def _close_tab(self, text: str, a: dict) -> Outcome:
        r = await self._hands.do("key", combo="cmd+w", app="Safari")
        return Outcome(r.ok, "Closed it." if r.ok else f"Couldn't: {r.detail}.")

    # -- messages (read back + 3 s stop window inside) -----------------------------------------
    async def _message_send(self, text: str, a: dict) -> Outcome:
        if self._messages is None:
            return Outcome(False, "I can't send messages yet.")
        return await self._messages.send(text, a)
