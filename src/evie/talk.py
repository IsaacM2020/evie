"""Evie's words. Jev decides what happens; Groq only writes the sentence she says out loud."""
import re

import httpx

from evie.config import Settings

FALLBACK = "My brain's lagging, try again."

PERSONA = (
    "You are Evie, Isaac's voice assistant on his MacBook. Isaac is 16 and lives in Singapore. "
    "Everything you write is spoken out loud, so use plain words: no markdown, no lists, no emoji, "
    "no em dashes. Be casual and warm, like a sharp friend. Two short sentences at most."
)


class TalkError(Exception):
    pass


class GroqClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None):
        self._s = settings
        self._http = http or httpx.AsyncClient(timeout=settings.groq_timeout_s)

    async def chat(self, system: str, user: str, max_tokens: int = 400) -> str:
        body = {
            "model": self._s.groq_model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": max_tokens,
            "reasoning_effort": "low",
            "include_reasoning": False,
        }
        headers = {"Authorization": f"Bearer {self._s.groq_key}"}
        last: TalkError | None = None
        for _ in range(2):
            try:
                r = await self._http.post(f"{self._s.groq_url}/chat/completions", json=body, headers=headers)
            except httpx.TransportError as e:  # includes timeouts
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

    async def aclose(self) -> None:
        await self._http.aclose()


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def clean(text: str) -> str:
    """Make LLM text safe to speak: no markdown, no dashes, two sentences max."""
    text = re.sub(r"[*_`#]", "", text)
    text = re.sub(r"\s*[—–]\s*", ", ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return " ".join(_SENTENCE_END.split(text)[:2])


class Talker:
    def __init__(self, groq: GroqClient):
        self._groq = groq

    async def _say(self, user: str) -> str:
        try:
            return clean(await self._groq.chat(PERSONA, user)) or FALLBACK
        except TalkError:
            return FALLBACK

    async def reply(self, utterance: str, facts: dict) -> str:
        lines = "\n".join(f"- {k}: {v}" for k, v in facts.items() if v)
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
