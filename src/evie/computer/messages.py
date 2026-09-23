"""Sending messages as Isaac: WhatsApp or iMessage (moved here from Phase 3, Isaac's call).

The contact comes from his real Contacts (the app looks them up; Jev picks when there's more
than one match), so she can't invent a number. Every message is read back out loud with 3 s to
say "stop" before it goes, and only Isaac's own voice or the talk key can start one.
"""
import json
import re
from typing import Callable
from urllib.parse import quote

from evie.computer.observe import Screen
from evie.computer.planner import Outcome
from evie.countdown import Countdown
from evie.jev import JevError


def _digits(phone: str) -> str:
    return re.sub(r"\D", "", phone)


class Messages:
    def __init__(self, hands, jev, countdown: Countdown, say: Callable[[str], None], window_s: float = 3.0):
        self._hands, self._jev, self._countdown, self._say, self._window = hands, jev, countdown, say, window_s

    async def _contact(self, name: str) -> dict | None | str:
        r = await self._hands.do("contacts_find", name=name)
        if not r.ok:
            return "no access"
        try:
            found = [c for c in json.loads(r.data.get("contacts") or "[]") if c.get("phones")]
        except ValueError:
            found = []
        if not found:
            return None
        if len(found) == 1:
            return found[0]
        options = {str(i): f"{c['name']} ({c['phones'][0]})" for i, c in enumerate(found[:50])} | {"none": "None of these"}
        q = {"contact": {"type": "choice", "instructions": f'Which contact does Isaac mean by "{name}"?',
                         "criteria": options}}
        try:
            a = (await self._jev.ask(f"Isaac wants to message {name}", q)).answers["contact"]
        except (JevError, KeyError, TypeError):
            return "unsure"
        c = a.get("choice")
        if c not in options or c == "none" or float(a.get("confidence", 0)) < 0.5:
            return "unsure"
        return found[int(c)]

    async def send(self, text: str, a: dict) -> Outcome:
        name = str(a.get("contact") or "").strip()
        if not name:
            return Outcome(False, "Who should I message?", ask=True)
        who = await self._contact(name)
        if who == "no access":
            return Outcome(False, "I need access to your Contacts first. Allow Evie in Settings.")
        if who is None:
            return Outcome(False, f"I can't find {name} in your contacts.")
        if who == "unsure":
            return Outcome(False, f"Which {name}?", ask=True)
        body = str(a.get("body") or "").strip()
        if not body:
            return Outcome(False, f"What should I say to {who['name']}?", ask=True)
        via = "imessage" if a.get("via") == "imessage" else "whatsapp"
        app_name = "WhatsApp" if via == "whatsapp" else "Messages"
        self._say(f"Sending '{body}' to {who['name']} on {app_name}. Say stop to cancel.")
        if not await self._countdown.wait(self._window):
            return Outcome(False, "Okay, not sent.")
        phone = who["phones"][0]
        if via == "imessage":
            r = await self._hands.do("imessage_send", to="+" + _digits(phone), text=body)
            return Outcome(r.ok, "Sent." if r.ok else f"Messages wouldn't send it: {r.detail}.")
        # WhatsApp: its own link opens the chat with the text typed in; then press Send.
        await self._hands.do("open_url", url=f"whatsapp://send?phone={_digits(phone)}&text={quote(body)}",
                             app="WhatsApp", front=True)
        import asyncio
        for _ in range(8):
            await asyncio.sleep(0.5)
            seen = await self._hands.do("observe", timeout=6.0, app="WhatsApp")
            if not seen.ok:
                continue
            screen = Screen.from_data(seen.data)
            send = next((e for e in screen.elements if e.get("label", "").lower() == "send"), None)
            if send:
                r = await self._hands.do("press", id=send["id"], snapshot=screen.snapshot)
                return Outcome(r.ok, "Sent." if r.ok else f"WhatsApp wouldn't send it: {r.detail}.")
        return Outcome(False, "WhatsApp opened with the message typed in, but I couldn't find Send. Press it for me?")
