"""Jev's raw answers turned into one typed object the rest of Evie can trust."""
from dataclasses import dataclass

from evie.switchboard.questions import ROUTES


@dataclass(frozen=True)
class Decision:
    for_evie: float
    route: str
    route_confidence: float
    route_probs: dict[str, float]
    complete: float
    has_event: float
    latency_ms: float
    cost_usd: float


def parse_decision(answers: dict, latency_ms: float = 0.0, cost_usd: float = 0.0) -> Decision:
    r = answers["route"]
    route = r.get("choice")
    if route not in ROUTES:
        raise ValueError(f"unknown route {route!r}")
    probs = {k: float(v) for k, v in r.get("probabilities", {}).items()}
    return Decision(
        for_evie=float(answers["for_evie"]["noul"]),
        route=route,
        route_confidence=float(r.get("confidence", probs.get(route, 0.0))),
        route_probs=probs,
        complete=float(answers["complete"]["noul"]),
        has_event=float(answers["has_event"]["noul"]),
        latency_ms=latency_ms,
        cost_usd=cost_usd,
    )
