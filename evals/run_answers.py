"""Score "is this Isaac answering Evie's question?" (Brain._answers) on evals/answers.jsonl, live Jev.

    uv run python -m evals.run_answers [--live]

2026-09-24 18:25: "What should I write to him?" -> "Tell him what's up." scored 0.43 and she asked
"Was that for me?". Target: every case right, clear of Brain's 0.5 line by MARGIN.
"""
import asyncio
import json
import sys
from pathlib import Path

from evals.cassette import CachedJev, cassette
from evie.brain import Brain, Pending
from evie.config import load_settings
from evie.jev import JevClient

MARGIN = 0.1  # a case at 0.51 is a coin flip in real use: it must clear the line by this much
CASES = Path(__file__).resolve().parent / "answers.jsonl"


async def main() -> None:
    cases = [json.loads(l) for l in CASES.read_text().splitlines() if l.strip()]
    cas = cassette("answers", live="--live" in sys.argv)
    jev = CachedJev(JevClient(load_settings()), cas)
    b = Brain.__new__(Brain)  # only _answers is used: it needs nothing but Jev
    b._jev = jev
    got = await asyncio.gather(*(b._answer_p(Pending("detail", c["request"], "isaac", 0.0, asked=c["asked"]), c["text"])
                                 for c in cases))
    await jev.aclose()
    print(cas.summary())
    right = 0
    for c, g in zip(cases, got):
        ok = g >= 0.5 + MARGIN if c["expect"] else g <= 0.5 - MARGIN
        right += ok
        print(f"{'ok ' if ok else 'XX '}{c['id']} p={g:.2f}  {c['text'][:60]}")
    print(json.dumps({"n": len(cases), "right": right}), "PASS" if right == len(cases) else "FAIL")
    sys.exit(0 if right == len(cases) else 1)


if __name__ == "__main__":
    asyncio.run(main())
