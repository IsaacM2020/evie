"""Score the narrator's Jev question on evals/narration.jsonl against LIVE Jev.

    uv run python -m evals.run_narration

Targets: accuracy >= 0.80, false_yes <= 0.20 (share of routine steps she'd say out loud).
"""
import sys
import asyncio
import json
from pathlib import Path

from evie.config import load_settings
from evals.cassette import CachedJev, cassette
from evie.jev import JevClient
from evie.narrator import NarrationRules, Narrator

CASES = Path(__file__).resolve().parent / "narration.jsonl"
TARGETS = {"accuracy": 0.80, "false_yes": 0.20}


def score(rows: list[dict], threshold: float) -> dict:
    said = [r["p"] >= threshold for r in rows]
    exp = [r["expect"] for r in rows]
    neg = [s for s, e in zip(said, exp) if not e]
    pos = [s for s, e in zip(said, exp) if e]
    return {"n": len(rows), "accuracy": round(sum(s == e for s, e in zip(said, exp)) / len(rows), 3),
            "false_yes": round(sum(neg) / max(len(neg), 1), 3), "recall": round(sum(pos) / max(len(pos), 1), 3)}


async def main() -> None:
    cases = [json.loads(l) for l in CASES.read_text().splitlines() if l.strip()]
    cas = cassette("narration", live="--live" in sys.argv)
    jev = CachedJev(JevClient(load_settings()), cas)
    n = Narrator(jev, None, None, None)
    ps = await asyncio.gather(*(n.worth_saying(c["goal"], c["line"], c["since_s"], c["count"]) for c in cases))
    await jev.aclose()
    print(cas.summary())
    rows = [{**c, "p": round(p, 3)} for c, p in zip(cases, ps)]
    t = NarrationRules().threshold
    for r in rows:
        mark = "ok " if (r["p"] >= t) == r["expect"] else "XX "
        print(f"{mark}{r['id']} p={r['p']:.2f} expect={'say' if r['expect'] else 'skip'}  {r['line'][:70]}")
    m = score(rows, t)
    fails = [k for k in TARGETS if (m[k] < TARGETS[k] if k == "accuracy" else m[k] > TARGETS[k])]
    print(json.dumps(m), "PASS" if not fails else f"FAIL {fails}")


if __name__ == "__main__":
    asyncio.run(main())
