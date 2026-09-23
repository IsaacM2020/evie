from pathlib import Path

from evals.run import load_cases, to_context

CASES = Path(__file__).resolve().parents[1] / "evals" / "cases.jsonl"


def test_splits_partition_the_cases():
    tune, hold, all_ = (load_cases(CASES, s) for s in ("tune", "holdout", "all"))
    assert len(tune) + len(hold) == len(all_) >= 70
    assert all(c["id"][-1] in "37" for c in hold)
    assert not {c["id"] for c in tune} & {c["id"] for c in hold}


def test_to_context_maps_fields():
    case = {"id": "h01", "utterance": "evie hows it going", "speaker": "isaac", "in_call": True,
            "front_app": "Zoom", "recent": ["a"], "jobs": ["fixing x"]}
    ctx = to_context(case)
    assert ctx.speaker == "isaac" and ctx.in_call and ctx.front_app == "Zoom"
    assert ctx.recent == ("a",) and ctx.active_jobs == ("fixing x",)
