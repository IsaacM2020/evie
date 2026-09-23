"""Evie's words. Jev decides what happens; Groq only writes the sentence she says out loud."""
import ast
import asyncio
import json
import operator
import re

import logging

import httpx

from evie.config import Settings

log = logging.getLogger("evie.talk")

FALLBACK = "My brain's lagging, try again."

PERSONA = (
    "You are Evie, Isaac's voice assistant on his MacBook. Isaac is 16 and lives in Singapore. "
    "Everything you write is spoken out loud, so use plain words: no markdown, no lists, no emoji, "
    "no em dashes. Be casual and warm, like a sharp friend. Two short sentences at most. "
    "Never do arithmetic in your head: write the expression inside double brackets and it will be "
    "replaced with the exact result, for example \"That's [[0.18*240]].\""
)


# Plain-English labels: with a bare "now:" key the model didn't realise it knew the time.
FACT_LABELS = {
    "now": "Current date and time",
    "calendar_now": "Isaac's calendar right now (worked out exactly, trust it)",
    "calendar_today": "Isaac's calendar today",
    "calendar_tomorrow": "Isaac's calendar tomorrow",
    "calendar_week": "Isaac's calendar for the rest of the week",
    "job": "Evie's background job",
    "things_isaac_told_evie": "Things Isaac asked Evie to remember",
}


class TalkError(Exception):
    pass


class GroqClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None, hedge_after_s: float = 1.2):
        self._s = settings
        self._http = http or httpx.AsyncClient(timeout=settings.groq_timeout_s)
        # Groq answers in ~250 ms, but about 1 call in 35 stalls for 4 s+ (2026-09-23: "I didn't
        # catch what to remember" was a stall, not a bad answer). If there's no answer by this
        # point, a second identical request goes out and whichever lands first wins.
        self._hedge = hedge_after_s

    async def chat(self, system: str, user: str, max_tokens: int = 400, json_mode: bool = False,
                   model: str | None = None, reasoning: str | None = None) -> str:
        model = model or self._s.groq_model
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": max_tokens,
            **({"response_format": {"type": "json_object"}} if json_mode else {}),
            # Evie's lines are short: no thinking. gpt-oss can't switch it off, so hide it.
            **({"reasoning_effort": reasoning or "low", "include_reasoning": False}
               if model.startswith("openai/gpt-oss") else {"reasoning_effort": reasoning or "none"}),
        }
        first = asyncio.create_task(self._once(body))
        done, _ = await asyncio.wait({first}, timeout=self._hedge)
        if done:
            return first.result()
        second = asyncio.create_task(self._once(body))
        last: TalkError | None = None
        pending = {first, second}
        try:
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for t in done:
                    if t.exception() is None:
                        return t.result()
                    last = t.exception()
            raise last
        finally:
            for t in pending:
                t.cancel()

    async def _once(self, body: dict) -> str:
        headers = {"Authorization": f"Bearer {self._s.groq_key}"}
        last: TalkError | None = None
        for _ in range(2):
            try:
                r = await self._http.post(f"{self._s.groq_url}/chat/completions", json=body, headers=headers)
            except httpx.TimeoutException as e:  # already waited the full budget: give up
                raise TalkError(f"timeout: {e!r}") from e
            except httpx.TransportError as e:  # a quick blip (e.g. connect failed): one retry
                last = TalkError(f"network: {e!r}")
                continue
            if r.status_code >= 500:
                last = TalkError(f"server {r.status_code}")
                continue
            if r.status_code != 200:
                raise TalkError(f"http {r.status_code}: {r.text[:200]}")
            text = (r.json()["choices"][0]["message"].get("content") or "").strip()
            if not text:
                raise TalkError("empty reply")
            return text
        raise last

    async def warm(self) -> None:
        """Keep the TLS connection open so Evie's first answer isn't slow. Never raises."""
        try:
            await self._http.head(f"{self._s.groq_url}/models")
        except httpx.HTTPError:
            pass

    async def aclose(self) -> None:
        await self._http.aclose()


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
        ast.USub: operator.neg, ast.UAdd: operator.pos}
_MATH = re.compile(r"\[\[(.+?)\]\]")


def _calc(node):
    """Numbers and + - * / // % ** only. Anything else (names, calls) raises."""
    if isinstance(node, ast.Expression):
        return _calc(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_calc(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _calc(node.left), _calc(node.right)
        if isinstance(node.op, ast.Pow) and (abs(right) > 100 or abs(left) > 1e6):
            raise ValueError("too big")
        return _OPS[type(node.op)](left, right)
    raise ValueError(f"not arithmetic: {ast.dump(node)[:40]}")


def _fmt(x) -> str:
    if isinstance(x, float) and not x.is_integer():
        return f"{round(x, 2):g}"
    return f"{int(x):,}".replace(",", "") if abs(x) < 1e15 else f"{x:g}"


def fill_math(text: str) -> str:
    """Replace [[expression]] with its exact value, computed by code, not by the model."""
    def one(m):
        try:
            return _fmt(_calc(ast.parse(m.group(1).strip(), mode="eval")))
        except (ValueError, SyntaxError, ZeroDivisionError, OverflowError, TypeError):
            return "that"
    return _MATH.sub(one, text)


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def clean(text: str) -> str:
    """Make LLM text safe to speak: no markdown, no dashes, two sentences max."""
    text = re.sub(r"[*`#]", "", text)
    text = re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"\1", text)  # _emphasis_, but keep test_brain.py
    text = re.sub(r"\s*[—–]\s*", ", ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return " ".join(_SENTENCE_END.split(text)[:2])


EXTRACT = ("You read one request that Isaac said out loud to his voice assistant (a raw transcript, "
           "may contain mishearings) and pull out details as JSON. Reply with one JSON object only. ")


class Talker:
    def __init__(self, groq: GroqClient):
        self._groq = groq

    async def extract(self, instructions: str, text: str) -> dict:
        """Free-text details (a song, a web address, an event time) as JSON. {} if anything fails:
        the caller then says it couldn't, rather than guessing."""
        try:
            out = json.loads(await self._groq.chat(EXTRACT + instructions, f'Request: "{text}"',
                                                   max_tokens=200, json_mode=True))
        except (TalkError, ValueError) as e:
            log.warning("extract failed: %s", str(e)[:120])
            return {}
        return out if isinstance(out, dict) else {}

    async def _say(self, user: str) -> str:
        try:
            return clean(fill_math(await self._groq.chat(PERSONA, user))) or FALLBACK
        except TalkError:
            return FALLBACK

    async def reply(self, utterance: str, facts: dict) -> str:
        lines = "\n".join(f"- {FACT_LABELS.get(k, k)}: {v}" for k, v in facts.items() if v)
        return await self._say(
            f"What you know right now:\n{lines or '- nothing extra'}\n\n"
            f'Isaac said: "{utterance}"\n'
            "Answer him. If what you know doesn't cover it, say so briefly. Never make up events or facts."
        )

    async def clarify(self, utterance: str, reason: str) -> str:
        return await self._say(
            f'Isaac said: "{utterance}". You can\'t do it yet because of this: {reason}. '
            "Ask him one short question to get what you need."
        )

    async def narrate(self, goal: str, event: str) -> str:
        return await self._say(
            f"You're working in the background on: {goal}. Latest progress: {event}. "
            "Tell Isaac in one short sentence, like a teammate giving a quick update."
        )

    async def summarize(self, goal: str, result: str) -> str:
        return await self._say(
            f"You just finished working on: {goal}. The worker's report:\n{result[:3000]}\n\n"
            "Tell Isaac how it went, in two short sentences at most."
        )

    async def aclose(self) -> None:
        await self._groq.aclose()
