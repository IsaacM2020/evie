"""Evie's words. Jev decides what happens; Groq only writes the sentence she says out loud."""
import ast
import asyncio
import json
import math
import operator
import re

import logging
from datetime import datetime

import httpx

from evie import capabilities
from evie.config import Settings

log = logging.getLogger("evie.talk")

FALLBACK = "My brain's lagging, try again."
BIG_MODEL = "openai/gpt-oss-120b"  # hard questions only: slower (~1-2 s) but it actually reasons

PERSONA = (
    "You are Evie, Isaac's voice assistant on his MacBook. Isaac is 16 and lives in Singapore. "
    "Everything you write is spoken out loud, so use plain words: no markdown, no lists, no emoji, "
    "no em dashes. Be casual and warm, like a sharp friend. Two short sentences at most. "
    "Never do arithmetic in your head: write the expression inside double brackets and it will be "
    "replaced with the exact result, for example \"That's [[0.18*240]].\" You can use sqrt, log (base 10), "
    "ln, factorial, pi, e, and sin/cos/tan which take DEGREES (sinr/cosr/tanr take radians); if it's "
    "unclear whether he means degrees or radians, use degrees and say so."
)
CANT_COMPUTE = "I couldn't work that one out exactly, sorry."


# Plain-English labels: with a bare "now:" key the model didn't realise it knew the time.
FACT_LABELS = {
    "now": "Current date and time",
    "calendar_now": "Isaac's calendar right now (worked out exactly, trust it)",
    "calendar_today": "Isaac's calendar today",
    "calendar_tomorrow": "Isaac's calendar tomorrow",
    "calendar_week": "Isaac's calendar for the rest of the week",
    "job": "Evie's background job",
    "conversation_earlier": "Earlier today with Isaac, in short",
    "conversation": "What you and Isaac said today, oldest first (use it for follow-ups like 'move it to 5' or 'what about Friday')",
    "things_isaac_told_evie": "Things Isaac asked Evie to remember",
    "todoist": "Isaac's Todoist, due today or overdue",
    "isaac_brief": "About Isaac and what he's been working on this week",
    "isaac_files": "Matching bits of Isaac's own notes (IsaacOS)",
    "screen": "What's on Isaac's screen",
    "web": "Web search result (fresh)",
}


class TalkError(Exception):
    pass


class RateLimited(TalkError):
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
                   model: str | None = None, reasoning: str | None = None, fallbacks: list[str] | None = None) -> str:
        """fallbacks: models to try, in order, when the one before is rate limited (each Groq model has
        its own tokens-per-minute budget). Default: the everyday model."""
        model = model or self._s.groq_model
        chain = [model] + [m for m in (fallbacks if fallbacks is not None else [self._s.groq_model]) if m != model]
        for i, m in enumerate(chain):
            try:
                return await self._hedged(self._body(system, user, max_tokens, json_mode, m, reasoning if i == 0 else None))
            except RateLimited:
                if i == len(chain) - 1:
                    raise TalkError("rate limited")
                log.warning("%s rate limited, falling back to %s", m, chain[i + 1])
        raise TalkError("rate limited")

    def _body(self, system: str, user: str, max_tokens: int, json_mode: bool, model: str, reasoning: str | None) -> dict:
        return {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": max_tokens,
            **({"response_format": {"type": "json_object"}} if json_mode else {}),
            # Evie's lines are short: no thinking. gpt-oss can't switch it off, so hide it.
            **({"reasoning_effort": reasoning or "low", "include_reasoning": False}
               if model.startswith("openai/gpt-oss") else {"reasoning_effort": "none"}),
        }

    async def look(self, prompt: str, png_b64: str, max_tokens: int = 60) -> str:
        """Qwen with a screenshot (tested 2026-09-24: Groq's qwen3.8-27b reads images). Only the
        planner's last resort for apps whose buttons can't be read as text; JSON out."""
        body = {"model": self._s.groq_model, "max_tokens": max_tokens, "reasoning_effort": "none",
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png_b64}"}}]}]}
        return await self._hedged(body)

    async def _hedged(self, body: dict) -> str:
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

    async def search(self, question: str) -> str:
        """A web-grounded answer: gpt-oss-120b with Groq's built-in browser search (~4 s)."""
        body = {"model": "openai/gpt-oss-120b", "reasoning_effort": "low", "include_reasoning": False,
                "max_tokens": 700, "tools": [{"type": "browser_search"}], "tool_choice": "auto",
                "messages": [{"role": "user", "content": (
                    f"Search the web and answer in two or three plain sentences with the key facts and "
                    f"dates (Isaac is in Singapore; today is {datetime.now().strftime('%A %-d %B %Y')}): {question}")}]}
        headers = {"Authorization": f"Bearer {self._s.groq_key}"}
        try:
            r = await self._http.post(f"{self._s.groq_url}/chat/completions", json=body, headers=headers, timeout=15.0)
        except httpx.HTTPError as e:
            raise TalkError(f"search: {e!r}") from e
        if r.status_code != 200:
            raise TalkError(f"search http {r.status_code}")
        text = (r.json()["choices"][0]["message"].get("content") or "").strip()
        if not text:
            raise TalkError("search: empty")
        return text

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
            if r.status_code == 429:
                raise RateLimited(r.text[:200])
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


def _factorial(n):
    if n != int(n) or n < 0 or n > 170:
        raise ValueError("factorial out of range")
    return math.factorial(int(n))


# Trig takes DEGREES by default (school maths: "cos 60" means 60 degrees); sinr/cosr/tanr take radians.
_FUNCS = {
    "sin": lambda x: math.sin(math.radians(x)), "cos": lambda x: math.cos(math.radians(x)),
    "tan": lambda x: math.tan(math.radians(x)),
    "sinr": math.sin, "cosr": math.cos, "tanr": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan,  # radians: wrap in degrees() for an angle
    "degrees": math.degrees, "radians": math.radians,
    "sqrt": math.sqrt, "log": math.log10, "ln": math.log, "log2": math.log2, "exp": math.exp,
    "abs": abs, "round": round, "factorial": _factorial, "floor": math.floor, "ceil": math.ceil,
}
_CONSTS = {"pi": math.pi, "e": math.e}


class MathError(ValueError):
    pass


def _calc(node):
    """Numbers, + - * / // % **, a few named functions and pi/e. Anything else raises."""
    if isinstance(node, ast.Expression):
        return _calc(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.Name) and node.id in _CONSTS:
        return _CONSTS[node.id]
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_calc(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _calc(node.left), _calc(node.right)
        if isinstance(node.op, ast.Pow) and (abs(right) > 100 or abs(left) > 1e6):
            raise ValueError("too big")
        return _OPS[type(node.op)](left, right)
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS
            and not node.keywords and 1 <= len(node.args) <= 2):
        return _FUNCS[node.func.id](*[_calc(a) for a in node.args])
    raise ValueError(f"not arithmetic: {ast.dump(node)[:40]}")


def _fmt(x) -> str:
    if isinstance(x, float) and not x.is_integer():
        r = round(x, 2)
        return f"{r:g}" if r != 0 or x == 0 else f"{x:.2g}"
    return f"{int(x):,}".replace(",", "") if abs(x) < 1e15 else f"{x:g}"


_BARE_CALL = re.compile(r"\b(" + "|".join(sorted(_FUNCS, key=len, reverse=True)) + r")\s+(-?[\d.]+|pi|e)\b")


def _tidy(expr: str) -> str:
    """How models actually write maths: "cos 60", "sin 30°", "2^10"."""
    expr = expr.replace("°", "").replace("^", "**").replace("×", "*").replace("÷", "/")
    return _BARE_CALL.sub(r"\1(\2)", expr).strip()


def fill_math(text: str) -> str:
    """Replace [[expression]] with its exact value, computed by code, not by the model.
    Raises MathError if any expression can't be computed (she then says so, never "that")."""
    def one(m):
        try:
            return _fmt(_calc(ast.parse(_tidy(m.group(1)), mode="eval")))
        except (ValueError, SyntaxError, ZeroDivisionError, OverflowError, TypeError) as e:
            raise MathError(m.group(1)) from e
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

    async def _say(self, user: str, model: str | None = None, reasoning: str | None = None) -> str:
        try:
            raw = await self._groq.chat(PERSONA + "\n\n" + capabilities.sheet(), user, model=model, reasoning=reasoning)
        except TalkError:
            return FALLBACK
        try:
            return clean(fill_math(raw)) or FALLBACK
        except MathError:
            return CANT_COMPUTE

    async def reply(self, utterance: str, facts: dict, hard: bool = False) -> str:
        """hard: a question that needs real reasoning goes to the bigger model (gpt-oss-120b)."""
        lines = "\n".join(f"- {FACT_LABELS.get(k, k)}: {v}" for k, v in facts.items() if v)
        return await self._say(
            f"What you know right now:\n{lines or '- nothing extra'}\n\n"
            f'Isaac said: "{utterance}"\n'
            "Answer him. If what you know doesn't cover it, say so briefly. Never make up events or facts."
            + (" Think it through carefully, then give the answer in plain spoken words." if hard else ""),
            model=BIG_MODEL if hard else None, reasoning="medium" if hard else None,
        )

    async def sum_up(self, summary: str, turns: list[dict]) -> str:
        """Three plain lines covering the older part of today's conversation (not spoken)."""
        convo = "\n".join(f'Isaac: {t["isaac"]} / Evie: {t["evie"]}' for t in turns[-60:])
        try:
            out = await self._groq.chat(
                "Summarise a conversation between Isaac and his assistant Evie in at most three short "
                "plain sentences: what he asked for, what got done, anything still open.",
                f"Summary so far: {summary or 'none'}\n\nConversation:\n{convo}", max_tokens=200)
        except TalkError:
            return summary
        return " ".join(out.split())

    async def clarify(self, utterance: str, reason: str) -> str:
        return await self._say(
            f'Isaac said: "{utterance}". You can\'t do it yet because of this: {reason}. '
            "Ask him one short question to get what you need."
        )

    async def readback(self, text: str) -> dict:
        """Before a long job: what Evie understood, as a short spoken line ("Checking why your website
        deploy failed"), or, when the words look misheard or a key detail is missing, one question.
        {"line": str, "unsure": bool, "question": str}. Falls back to the plain words on failure."""
        system = ("Isaac asked his assistant Evie, by voice, to do a job that takes a while. His words came through "
                  "speech-to-text and may contain misheard words. Return JSON: {\"line\": what Evie is about to do, "
                  "as she'd say it, 4-12 words starting with an -ing verb, using the most likely meaning of his words, "
                  "\"unsure\": true only if the words don't make sense or it's unclear WHICH thing he means, "
                  "\"question\": if unsure, one short question to ask him}. No em dashes.")
        try:
            out = json.loads(await self._groq.chat(system, f'Isaac said: "{text}"', max_tokens=120, json_mode=True))
        except (TalkError, ValueError) as e:
            log.warning("readback failed: %s", str(e)[:120])
            return {"line": "", "unsure": False, "question": ""}
        if not isinstance(out, dict):
            return {"line": "", "unsure": False, "question": ""}
        return {"line": str(out.get("line") or "").strip().rstrip("."), "unsure": bool(out.get("unsure")),
                "question": str(out.get("question") or "").strip()}

    async def narrate(self, goal: str, event: str, last: str = "") -> str:
        before = f' Your last update to him was: "{last}". Carry on from it, don\'t repeat it.' if last else ""
        return await self._say(
            f"You're working in the background on: {goal}. Latest progress: {event}.{before} "
            "Tell Isaac in one short sentence, like a teammate giving a quick update."
        )

    async def summarize(self, goal: str, result: str) -> str:
        return await self._say(
            f"You just finished working on: {goal}. The worker's report:\n{result[:3000]}\n\n"
            "Tell Isaac how it went, in two short sentences at most."
        )

    async def aclose(self) -> None:
        await self._groq.aclose()
