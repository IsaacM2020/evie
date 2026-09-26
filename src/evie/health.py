"""Phase 6 P2: component health from signals that already exist — the keep-warm pings (Jev,
Groq, Whisper), the WebSocket's own connection count, a running job's own event stream — instead
of inventing new checks. record() reports back a transition ("degraded" / "recovered") only when
one actually happened, so the caller can publish it once on the existing event bus rather than
spamming a health line every 20 s; evie.world_model already subscribes to that bus, so its
system_health view updates itself with no new wiring on that side.
"""
import time
from dataclasses import dataclass, field
from typing import Callable

STUCK_JOB_S = 300.0  # a running job with no new event this long is worth a look


@dataclass
class ComponentHealth:
    ok: bool = True
    consecutive_failures: int = 0
    total_checks: int = 0
    total_failures: int = 0
    last_ok_at: float | None = None
    last_check_at: float = 0.0
    last_detail: str = ""
    latencies_ms: list[float] = field(default_factory=list)  # a short rolling window

    def p50_ms(self) -> float | None:
        if not self.latencies_ms:
            return None
        return sorted(self.latencies_ms)[len(self.latencies_ms) // 2]


class HealthMonitor:
    def __init__(self, clock: Callable[[], float] = time.time, latency_window: int = 20,
                 degraded_after: int = 3):
        self._clock, self._window, self._degraded_after = clock, latency_window, degraded_after
        self._components: dict[str, ComponentHealth] = {}
        self._job_last_event: dict[str, float] = {}

    def degraded(self, component: str) -> bool:
        c = self._components.get(component)
        return bool(c and c.consecutive_failures >= self._degraded_after)

    def record(self, component: str, ok: bool, latency_ms: float | None = None,
              detail: str = "") -> tuple[ComponentHealth, str | None]:
        """Returns the component's health plus "degraded"/"recovered" if this call crossed that
        line, or None if nothing changed worth telling anyone about."""
        was_degraded = self.degraded(component)
        c = self._components.setdefault(component, ComponentHealth())
        now = self._clock()
        c.total_checks += 1
        c.last_check_at, c.last_detail, c.ok = now, detail, ok
        if ok:
            c.consecutive_failures, c.last_ok_at = 0, now
        else:
            c.consecutive_failures += 1
            c.total_failures += 1
        if latency_ms is not None:
            c.latencies_ms = (c.latencies_ms + [latency_ms])[-self._window:]
        now_degraded = self.degraded(component)
        event = "degraded" if now_degraded and not was_degraded else \
                "recovered" if was_degraded and not now_degraded else None
        return c, event

    def overall_ok(self) -> bool:
        return not any(self.degraded(name) for name in self._components)

    def get(self, component: str) -> ComponentHealth | None:
        return self._components.get(component)

    def snapshot(self) -> dict:
        return {"ok": self.overall_ok(), "components": {
            name: {"ok": c.ok, "degraded": self.degraded(name), "consecutive_failures": c.consecutive_failures,
                  "total_checks": c.total_checks, "total_failures": c.total_failures,
                  "last_ok_at": c.last_ok_at, "last_detail": c.last_detail, "p50_ms": c.p50_ms()}
            for name, c in self._components.items()}}

    # -- stuck-job detection: fed by the same on_event callback jobs.py already calls -----------
    def job_event(self, job_id: str) -> None:
        self._job_last_event[job_id] = self._clock()

    def job_ended(self, job_id: str) -> None:
        self._job_last_event.pop(job_id, None)

    def stuck_jobs(self, running_ids: list[str], stuck_after: float = STUCK_JOB_S) -> list[str]:
        now = self._clock()
        return [jid for jid in running_ids if now - self._job_last_event.get(jid, now) >= stuck_after]
