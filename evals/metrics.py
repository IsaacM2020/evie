"""Scores a run. The headline number is false_action: Evie acting on speech not meant for her.

That must be 0. One wrong WhatsApp sent during Spanish class and Isaac turns Evie off forever.
"""

TARGETS = {
    "false_action": ("<=", 0),
    "false_clarify_rate": ("<=", 0.10),
    "command_recall": (">=", 0.95),
    "route_accuracy": (">=", 0.90),
    "complete_accuracy": (">=", 0.85),
    "event_accuracy": (">=", 0.90),
    "latency_p95_ms": ("<=", 900),
    "skill_accuracy": (">=", 0.90),
    "remember_to_accuracy": (">=", 0.90),
    "multi_accuracy": (">=", 0.85),
    "pack_accuracy": (">=", 0.85),
}
COSTLY_PACKS = {"web", "projects"}  # slow or big: picking them when not needed costs real time


_NUM = {w: str(i) for i, w in enumerate("zero one two three four five six seven eight nine".split())}


def _words(s: str) -> list[str]:
    import re
    s = re.sub(r"\b([ap])\.\s?m\.?", r"\1m", s.lower())  # "5 p.m." and "5pm" are the same words
    s = re.sub(r"(\d)\s+([ap]m)\b", r"\1\2", s)
    words = [_NUM.get(w, w) for w in re.sub(r"[^a-z0-9' ]", " ", s).split()]
    out: list[str] = []  # "four one two nine" and "4129" are the same number
    for w in words:
        if w.isdigit() and out and out[-1].isdigit() and len(w) == 1:
            out[-1] += w
        else:
            out.append(w)
    return out


def wer(ref: str, hyp: str) -> float:
    """Word error rate: word edits (swap, missing, extra) needed to turn hyp into ref, per ref word."""
    r, h = _words(ref), _words(hyp)
    if not r:
        return 0.0 if not h else 1.0
    row = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        prev, row[0] = row[0], i
        for j, hw in enumerate(h, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (rw != hw))
    return row[len(h)] / len(r)


def _ratio(a: int, b: int) -> float | None:
    return round(a / b, 3) if b else None


def should_act(r: dict) -> bool:
    return bool(r["expect"]["for_evie"]) and r.get("speaker") != "other"


def score(results: list[dict]) -> tuple[dict, dict[str, list[str]]]:
    judged = [r for r in results if r["decision"] is not None]
    neg = [r for r in results if not should_act(r)]
    pos = [r for r in results if should_act(r)]
    fails: dict[str, list[str]] = {k: [] for k in
                                   ("false_action", "false_clarify", "missed_command", "route", "complete", "event",
                                    "skill", "remember_to", "packs", "multi")}
    for r in neg:
        if r["action"] == "act":
            fails["false_action"].append(r["id"])
        elif r["action"] == "clarify":
            fails["false_clarify"].append(r["id"])
    for r in pos:
        if r["action"] == "ignore":
            fails["missed_command"].append(r["id"])
    n_complete = n_event = n_skill = n_rem = n_pack = n_multi = 0
    for r in judged:
        d, e = r["decision"], r["expect"]
        if e.get("skill"):
            n_skill += 1
            if d.get("skill") not in (e["skill"] if isinstance(e["skill"], list) else [e["skill"]]):
                fails["skill"].append(r["id"])
        if e.get("multi") is not None:
            n_multi += 1
            if (float(d.get("multi") or 0.0) >= 0.7) != bool(e["multi"]):
                fails["multi"].append(r["id"])
        if e.get("remember_to"):
            n_rem += 1
            ok = e["remember_to"] if isinstance(e["remember_to"], list) else [e["remember_to"]]
            if d.get("remember_to") not in ok:
                fails["remember_to"].append(r["id"])
        if e.get("packs") is not None:
            from evie.context_packs import rails
            n_pack += 1
            got, want = set(d.get("packs") or ()) | rails(r.get("utterance", "")), set(e["packs"])
            if not want <= got or (got - want) & COSTLY_PACKS - set(e.get("packs_ok_extra", [])):
                fails["packs"].append(r["id"])
        if d["route"] not in (e["route"] if isinstance(e["route"], list) else [e["route"]]):
            fails["route"].append(r["id"])
        if e.get("complete") is not None:
            n_complete += 1
            if (d["complete"] >= 0.5) != e["complete"]:
                fails["complete"].append(r["id"])
        if e.get("has_event") is not None:
            n_event += 1
            if (d["has_event"] >= 0.5) != e["has_event"]:
                fails["event"].append(r["id"])
    lat = sorted(r["decision"]["latency_ms"] for r in judged)
    metrics = {
        "n": len(results),
        "jev_failures": len(results) - len(judged),
        "false_action": len(fails["false_action"]),
        "false_clarify_rate": _ratio(len(fails["false_clarify"]), len(neg)),
        "command_recall": _ratio(len(pos) - len(fails["missed_command"]), len(pos)),
        "route_accuracy": _ratio(len(judged) - len(fails["route"]), len(judged)),
        "complete_accuracy": _ratio(n_complete - len(fails["complete"]), n_complete),
        "event_accuracy": _ratio(n_event - len(fails["event"]), n_event),
        "latency_p50_ms": round(lat[len(lat) // 2]) if lat else None,
        "latency_p95_ms": round(lat[min(len(lat) - 1, int(len(lat) * 0.95))]) if lat else None,
        "skill_accuracy": _ratio(n_skill - len(fails["skill"]), n_skill),
        "remember_to_accuracy": _ratio(n_rem - len(fails["remember_to"]), n_rem),
        "pack_accuracy": _ratio(n_pack - len(fails["packs"]), n_pack),
        "multi_accuracy": _ratio(n_multi - len(fails["multi"]), n_multi),
        "cost_usd": round(sum(r["decision"]["cost_usd"] for r in judged), 6),
    }
    return metrics, fails


def check_targets(metrics: dict) -> dict[str, bool]:
    out = {}
    for k, (op, v) in TARGETS.items():
        x = metrics.get(k)
        out[k] = x is not None and (x <= v if op == "<=" else x >= v)
    return out
