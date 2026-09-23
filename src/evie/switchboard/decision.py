"""Jev's raw answers turned into one typed object the rest of Evie can trust."""
from dataclasses import dataclass

from evie.switchboard.questions import REMEMBER_TO, ROUTES, SKILLS


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
    skill: str | None = None
    skill_conf: float = 0.0
    remember_to: str | None = None


def _choice(answers: dict, key: str, allowed: dict) -> tuple[str | None, float]:
    a = answers.get(key) or {}
    c = a.get("choice")
    if c not in allowed:
        return None, 0.0
    return c, float(a.get("confidence", (a.get("probabilities") or {}).get(c, 0.0)))


def parse_decision(answers: dict, latency_ms: float = 0.0, cost_usd: float = 0.0) -> Decision:
    r = answers["route"]
    route = r.get("choice")
    if route not in ROUTES:
        raise ValueError(f"unknown route {route!r}")
    probs = {k: float(v) for k, v in r.get("probabilities", {}).items()}
    skill, skill_conf = _choice(answers, "skill", SKILLS)
    return Decision(
        for_evie=float(answers["for_evie"]["noul"]),
        route=route,
        route_confidence=float(r.get("confidence", probs.get(route, 0.0))),
        route_probs=probs,
        complete=float(answers["complete"]["noul"]),
        has_event=float(answers["has_event"]["noul"]),
        latency_ms=latency_ms,
        cost_usd=cost_usd,
        skill=skill,
        skill_conf=skill_conf,
        remember_to=_choice(answers, "remember_to", REMEMBER_TO)[0],
    )
