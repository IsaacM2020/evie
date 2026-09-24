"""Score Jev's model pick for Claude Code jobs (jobs.pick_tier) on evals/tiers.jsonl.

    uv run python -m evals.run_tiers [--live]

Isaac, 2026-09-24: Haiku for really simple jobs, Sonnet medium or high for most, never Opus.
Gate: >= 13/15 right, and a hard job is never given the quick tier.
"""
import asyncio
import json
import sys
from pathlib import Path

from evals.cassette import CachedJev, cassette
from evie.config import load_settings
from evie.jev import JevClient
from evie.jobs import TIERS, pick_tier

CASES = Path(__file__).resolve().parent / "tiers.jsonl"


async def main() -> None:
    cases = [json.loads(l) for l in CASES.read_text().splitlines() if l.strip()]
    cas = cassette("tiers", live="--live" in sys.argv)
    jev = CachedJev(JevClient(load_settings()), cas)
    got = await asyncio.gather(*(pick_tier(jev, c["goal"]) for c in cases))
    await jev.aclose()
    print(cas.summary())
    right = sum(g == c["want"] for g, c in zip(got, cases))
    bad = [c["id"] for g, c in zip(got, cases) if c["want"] == "hard" and g == "quick"]
    for c, g in zip(cases, got):
        print(f"{'ok ' if g == c['want'] else 'XX '}{c['id']} {g:6} want {c['want']:6} {c['goal'][:60]}")
    opus = sum("opus" in TIERS[g][0] for g in got)
    ok = right >= 13 and not bad and opus == 0
    print(json.dumps({"n": len(cases), "right": right, "hard_as_quick": bad, "opus": opus}), "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
