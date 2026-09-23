"""One job: send state + typed questions to Jev and hand back its answers."""
import time
from dataclasses import dataclass

import httpx

from evie.config import Settings


class JevError(Exception):
    pass


@dataclass(frozen=True)
class JevResult:
    answers: dict
    latency_ms: float
    cost_usd: float


class JevClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None):
        self._s = settings
        self._http = http or httpx.AsyncClient(timeout=settings.jev_timeout_s)

    async def ask(self, state: str, questions: dict) -> JevResult:
        body = {"model": self._s.jev_model, "state": state, "questions": questions}
        headers = {"Authorization": f"Bearer {self._s.openrouter_key}"}
        last: JevError | None = None
        for _ in range(2):
            t0 = time.perf_counter()
            try:
                r = await self._http.post(self._s.jev_url, json=body, headers=headers)
            except httpx.TransportError as e:  # includes timeouts
                last = JevError(f"network: {e!r}")
                continue
            ms = (time.perf_counter() - t0) * 1000
            if r.status_code >= 500:
                last = JevError(f"server {r.status_code}")
                continue
            if r.status_code != 200:
                raise JevError(f"http {r.status_code}: {r.text[:200]}")
            data = r.json()
            answers = data.get("answers", {})
            missing = set(questions) - set(answers)
            if missing:
                raise JevError(f"missing answers: {sorted(missing)}")
            return JevResult(answers, ms, float(data.get("usage", {}).get("cost", 0.0)))
        raise last

    async def aclose(self) -> None:
        await self._http.aclose()
