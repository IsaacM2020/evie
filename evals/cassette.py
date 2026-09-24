"""Record Jev and Groq answers once, replay them after that: re-running an eval that hasn't
changed costs nothing (Isaac, 2026-09-24: "don't be wasteful").

A question's key is a hash of EVERYTHING that could change its answer (the state text, the exact
question wording, the model, the settings). Change the wording and it's a new key, so it goes live
and gets recorded; keep it the same and it's replayed. Errors are never recorded. `live=True`
(the evals' --live flag) asks everything again and overwrites what was there.
"""
import hashlib
import json
from pathlib import Path
from typing import Any, Awaitable, Callable

from evie.jev import JevResult

CASSETTES = Path(__file__).parent / "cassettes"


class Cassette:
    def __init__(self, path: Path, live: bool = False):
        self.path, self.live = path, live
        self.hits = self.misses = 0
        self._store: dict[str, Any] = {}
        if path.exists():
            for line in path.read_text().splitlines():
                if line.strip():
                    row = json.loads(line)
                    self._store[row["key"]] = row["value"]

    @staticmethod
    def key(*parts: Any) -> str:
        return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:32]

    async def get_or(self, key: str, fetch: Callable[[], Awaitable[Any]]) -> Any:
        if not self.live and key in self._store:
            self.hits += 1
            return self._store[key]
        value = await fetch()  # an exception here propagates and nothing is recorded
        self.misses += 1
        self._store[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps({"key": key, "value": value}, ensure_ascii=False) + "\n")
        return value

    def summary(self) -> str:
        return f"cassette {self.path.name}: {self.hits} replayed, {self.misses} live"


class CachedJev:
    def __init__(self, jev, cassette: Cassette):
        self._jev, self.cassette = jev, cassette

    async def ask(self, state: str, questions: dict) -> JevResult:
        async def fetch() -> dict:
            r = await self._jev.ask(state, questions)
            return {"answers": r.answers, "latency_ms": r.latency_ms, "cost_usd": r.cost_usd}

        return JevResult(**await self.cassette.get_or(Cassette.key("jev", state, questions), fetch))

    async def aclose(self) -> None:
        await self._jev.aclose()


class CachedGroq:
    def __init__(self, groq, cassette: Cassette):
        self._groq, self.cassette = groq, cassette

    async def chat(self, system: str, user: str, max_tokens: int = 400, json_mode: bool = False,
                   model: str | None = None, reasoning: str | None = None, fallbacks: list[str] | None = None) -> str:
        k = Cassette.key("groq", system, user, max_tokens, json_mode, model, reasoning)
        return await self.cassette.get_or(k, lambda: self._groq.chat(system, user, max_tokens, json_mode, model,
                                                                      reasoning, fallbacks=fallbacks))

    def __getattr__(self, name: str) -> Any:  # anything else (aclose, settings) goes to the real client
        return getattr(self._groq, name)


def cassette(name: str, live: bool = False) -> Cassette:
    return Cassette(CASSETTES / f"{name}.jsonl", live=live)
