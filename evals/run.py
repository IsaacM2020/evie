"""Run the switchboard over evals/cases.jsonl against LIVE Jev and score it.

    uv run python -m evals.run --split tune --label baseline

Costs about $0.002 for all 70 cases.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from evals.metrics import TARGETS, check_targets, score
from evie.config import load_settings
from evals.cassette import CachedJev, cassette
from evie.jev import JevClient
from evie.switchboard import Switchboard
from evie.switchboard.context import Context

HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs"


def load_cases(path: Path, split: str) -> list[dict]:
    cases = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    if split == "all":
        return cases
    hold = [c for c in cases if c["id"][-1] in "37"]
    return hold if split == "holdout" else [c for c in cases if c["id"][-1] not in "37"]


def to_context(c: dict) -> Context:
    return Context(
        utterance=c["utterance"],
        speaker=c.get("speaker", "unknown"),
        in_call=c.get("in_call", False),
        front_app=c.get("front_app", ""),
        recent=tuple(c.get("recent", [])),
        active_jobs=tuple(c.get("jobs", [])),
        addressed=c.get("addressed", False),
        followup_s=c.get("followup_s"),
    )


async def run_cases(sb: Switchboard, cases: list[dict], concurrency: int = 8) -> list[dict]:
    sem = asyncio.Semaphore(concurrency)

    async def one(c: dict) -> dict:
        async with sem:
            o = await sb.handle(to_context(c))
        return {"id": c["id"], "cat": c["cat"], "utterance": c["utterance"],
                "speaker": c.get("speaker", "unknown"), "expect": c["expect"], **o.to_dict()}

    return list(await asyncio.gather(*(one(c) for c in cases)))


def previous_run(split: str) -> dict | None:
    runs = sorted(RUNS.glob(f"*-{split}-*.json"))
    return json.loads(runs[-1].read_text()) if runs else None


def report(metrics: dict, fails: dict, results: list[dict], prev: dict | None) -> str:
    ok = check_targets(metrics)
    lines = [f"{'metric':<22} {'value':<10} {'target':<10}"]
    for k, v in metrics.items():
        tgt = TARGETS.get(k)
        t = "" if tgt is None else f"{tgt[0]} {tgt[1]}"
        mark = "" if tgt is None else ("PASS" if ok[k] else "FAIL")
        was = f"(was {prev['metrics'].get(k)})" if prev else ""
        lines.append(f"{k:<22} {str(v):<10} {t:<10} {mark} {was}")
    by_id = {r["id"]: r for r in results}
    for kind, ids in fails.items():
        for i in ids:
            r, d = by_id[i], by_id[i]["decision"] or {}
            lines.append(f"  {kind:<15} {i} \"{r['utterance']}\" -> {r['action']} ({r['reason']}) "
                         f"for_evie={d.get('for_evie')} route={d.get('route')} "
                         f"complete={d.get('complete')} event={d.get('has_event')}")
    if prev:
        old = {r["id"]: r["action"] for r in prev["results"]}
        flips = [r["id"] for r in results if r["id"] in old and old[r["id"]] != r["action"]]
        lines.append(f"flips vs previous run: {len(flips)} {flips}")
    return "\n".join(lines)


async def amain(args: argparse.Namespace) -> int:
    cases = load_cases(HERE / "cases.jsonl", args.split)
    cas = cassette("switchboard", live=args.live)
    sb = Switchboard(CachedJev(JevClient(load_settings()), cas))
    try:
        results = await run_cases(sb, cases)
    finally:
        await sb.aclose()
    print(cas.summary())
    metrics, fails = score(results)
    prev = previous_run(args.split)
    print(report(metrics, fails, results, prev))
    RUNS.mkdir(exist_ok=True)
    out = RUNS / f"{time.strftime('%Y%m%d-%H%M%S')}-{args.split}-{args.label}.json"
    out.write_text(json.dumps({"label": args.label, "split": args.split, "metrics": metrics,
                               "fails": fails, "results": results}, indent=1))
    print(f"saved {out.relative_to(HERE.parent)}")
    return 0 if all(check_targets(metrics).values()) else 1


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--split", choices=["tune", "holdout", "all"], default="tune")
    p.add_argument("--label", default="run")
    p.add_argument("--live", action="store_true", help="ask Jev again instead of replaying recorded answers")
    raise SystemExit(asyncio.run(amain(p.parse_args())))


if __name__ == "__main__":
    main()
