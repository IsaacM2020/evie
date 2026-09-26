"""The front door for screen work. Jev's main call already said whether this is a message or
something on screen, so there's no second "which recipe?" call any more (Phase 3c):
  - "new tab" / "close this tab": instant, no model at all
  - a message: computer/messages.py (real contacts, read back, 3 s to say stop)
  - everything else: the planner (plan once, find in code, pick with Jev)
"""
import logging
import re

from evie.computer.planner import Outcome

log = logging.getLogger("evie.computer")

_NEW_TAB = re.compile(r"^\W*(open\s+)?(a\s+)?new\s+tab\W*$", re.I)
_CLOSE_TAB = re.compile(r"^\W*close\s+(this|the|that)?\s*(current\s+)?tab\W*$", re.I)

ARGS_Q = ('Isaac wants a message sent. Return {"contact": who to message (or null), '
          '"body": the message text exactly as he wants it sent (or null), "via": "whatsapp" or "imessage" or null}.')


class Recipes:
    def __init__(self, hands, jev, talker, planner, messages=None, procedures=None):
        self._hands, self._jev, self._talker, self._planner = hands, jev, talker, planner
        self._messages, self._procedures = messages, procedures

    async def run(self, text: str, skill: str | None = None) -> Outcome:
        bare = re.sub(r"^\W*(hey\s+)?evie\W*", "", text, flags=re.I)
        if _NEW_TAB.match(bare):
            await self._hands.do("activate", app="Safari")
            r = await self._hands.do("key", combo="cmd+t", app="Safari")
            return Outcome(r.ok, "New tab's open." if r.ok else f"Couldn't: {r.detail}.")
        if _CLOSE_TAB.match(bare):
            r = await self._hands.do("key", combo="cmd+w", app="Safari")
            return Outcome(r.ok, "Closed it." if r.ok else f"Couldn't: {r.detail}.")
        if skill == "message_send" and self._messages is not None:
            args = await self._talker.extract(ARGS_Q, text) or {}
            log.info("computer message via %s", args.get("via"))
            return await self._messages.send(text, args)
        if self._procedures is None:
            return await self._planner.run(text)
        # Phase 6 P1: a procedure proven twice or more replaces the planning call; Planner.run()
        # still runs its own expect checks against the live screen either way.
        proc = self._procedures.find(text)
        out = await self._planner.run(text, steps=proc.steps if proc else None)
        if proc is not None:
            (self._procedures.record_success if out.ok else self._procedures.record_failure)(proc.id)
        elif out.ok and out.verified:
            self._procedures.learn(text, self._planner.last_steps)
        return out

    async def choose(self, pick: dict, answer: str | None, eid: str | None = None) -> Outcome:
        return await self._planner.choose(pick, answer, eid=eid)
