"""The general loop for anything no recipe covers: observe -> choose ONE step -> act -> look again.

A fast model (Groq gpt-oss-120b, low reasoning) reads the goal, the steps so far and the screen as
a numbered list, and answers with one JSON step. Code checks it before anything happens: the id
must be on the screen it just saw, the op must be one of the allowed ones, and risky steps get a
read-back and 3 s to say "stop". Two failed steps in a row, or 15 steps, and she stops and says so
(the Brain can hand the goal to Claude Code, which may use a screenshot as the true last resort).
"""
import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Callable

from evie.computer.observe import Screen
from evie.computer.safety import is_risky
from evie.countdown import Countdown

log = logging.getLogger("evie.computer")

MODEL = "openai/gpt-oss-120b"
MAX_STEPS = 15
OPS = {"press", "set_text", "key", "open_url", "menu", "wait", "done", "ask"}

SYSTEM = """You operate apps on Isaac's Mac for him. Each turn you see ONE app's screen as a list of
elements: id, role, label (and value, region, link). Choose exactly ONE next step and reply with one JSON object:
{"op": "press" | "set_text" | "key" | "open_url" | "menu" | "wait" | "done" | "ask",
 "id": the element id for press/set_text (it MUST be one of the listed ids),
 "text": what to type for set_text, "submit": true to press Enter after typing,
 "combo": for key, like "cmd+t", "cmd+l", "return", "escape",
 "url": for open_url (https only), "path": for menu, like "File > New Tab",
 "risky": true if this step sends, posts, buys, deletes or submits something on Isaac's behalf,
 "say": for done, one short spoken sentence saying what you did; for ask, the question; for a risky step, a
        read-back like "Sending 'on my way' to Mom",
 "why": a few words}
Rules: take the most direct path. Only use listed ids. When the goal is achieved, op=done. If it's unclear what
Isaac wants (which video? which chat?), op=ask. Never type passwords, log in or pay: op=ask instead.
Elements marked [off screen] can still be pressed."""


@dataclass
class Outcome:
    ok: bool
    said: str
    ask: bool = False
    stuck: bool = False  # couldn't do it on screen: the Brain may hand it to Claude Code


class Planner:
    def __init__(self, hands, groq, countdown: Countdown, say: Callable[[str], None],
                 settle_s: float = 0.5, max_steps: int = MAX_STEPS, window_s: float = 3.0):
        self._hands, self._groq, self._countdown, self._say = hands, groq, countdown, say
        self._settle, self._max, self._window = settle_s, max_steps, window_s

    async def run(self, goal: str, app: str | None = None) -> Outcome:
        history: list[str] = []
        fails = 0
        for _ in range(self._max):
            seen = await self._hands.do("observe", timeout=8.0, **({"app": app} if app else {}))
            if not seen.ok:
                return Outcome(False, f"Couldn't do that: {seen.detail}.")
            screen = Screen.from_data(seen.data)
            step = await self._next(goal, history, screen)
            op = step.get("op")
            if op not in OPS:
                fails += 1
                history.append(f"(invalid step {step!r:.80})")
            elif op == "done":
                return Outcome(True, str(step.get("say") or "Done."))
            elif op == "ask":
                return Outcome(False, str(step.get("say") or "What exactly should I do?"), ask=True)
            elif op == "wait":
                await asyncio.sleep(1.0)
                history.append("waited")
                continue
            elif op in ("press", "set_text") and step.get("id") not in screen.ids:
                fails += 1
                history.append(f"(tried {step.get('id')}, which isn't on screen)")
            else:
                el = screen.get(step.get("id", "")) if step.get("id") else None
                if is_risky(op, el, str(step.get("text") or ""), flagged=bool(step.get("risky"))):
                    line = str(step.get("say") or f"About to {op} {el.get('label') if el else ''}".strip())
                    self._say(f"{line.rstrip('.')}. Say stop to cancel.")
                    if not await self._countdown.wait(self._window):
                        return Outcome(False, "Okay, I didn't do it.")
                r = await self._hands.do(op, **self._args(op, step, screen))
                label = (el or {}).get("label", "")
                history.append(f"{op} {label or step.get('combo') or step.get('url') or step.get('path') or ''}"
                               f"{' text=' + json.dumps(step.get('text')) if op == 'set_text' else ''} -> "
                               f"{'ok' if r.ok else 'failed: ' + r.detail}")
                if r.ok:
                    fails = 0
                    if screen.kind == "web" and op in ("press", "set_text", "open_url"):
                        await self._hands.do("wait_page", timeout=10.0, app=screen.app)
                    elif self._settle:
                        await asyncio.sleep(self._settle)
                    continue
                fails += 1
            if fails >= 2:
                break
        log.info("computer goal stuck: %s | %s", goal, " / ".join(history[-5:]))
        return Outcome(False, "I got stuck doing that on screen.", stuck=True)

    @staticmethod
    def _args(op: str, step: dict, screen: Screen) -> dict:
        if op == "press":
            return {"id": step["id"], "snapshot": screen.snapshot}
        if op == "set_text":
            return {"id": step["id"], "snapshot": screen.snapshot, "text": str(step.get("text") or ""),
                    "submit": bool(step.get("submit"))}
        if op == "key":
            return {"combo": str(step.get("combo") or ""), "app": screen.app}
        if op == "open_url":
            return {"url": str(step.get("url") or ""), "app": screen.app, "front": False}
        return {"path": str(step.get("path") or ""), "app": screen.app}

    async def _next(self, goal: str, history: list[str], screen: Screen) -> dict:
        user = (f"Goal: {goal}\nSteps so far: {' / '.join(history[-8:]) or 'none'}\n\n"
                f"Screen:\n{screen.compact()}")
        try:
            out = json.loads(await self._groq.chat(SYSTEM, user, max_tokens=500, json_mode=True, model=MODEL,
                                                   reasoning="low"))
            return out if isinstance(out, dict) else {}
        except Exception as e:  # a bad or failed reply counts as a failed step, never a crash
            log.warning("planner step failed: %s", str(e)[:120])
            return {}
