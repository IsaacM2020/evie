import json
from pathlib import Path

from evie.switchboard.questions import ROUTES

CASES = Path(__file__).resolve().parents[1] / "evals" / "cases.jsonl"
CATS = {"direct", "bare", "room", "other", "call", "vague", "job", "mention", "openmic"}


def load():
    return [json.loads(l) for l in CASES.read_text().splitlines() if l.strip()]


def test_cases_are_valid():
    cases = load()
    assert len(cases) >= 70  # grows: every verdict that felt wrong becomes a case
    assert len({c["id"] for c in cases}) == len(cases)
    for c in cases:
        assert c["cat"] in CATS, c["id"]
        assert c["speaker"] in {"isaac", "other", "unknown"}, c["id"]
        e = c["expect"]
        assert e["route"] in ROUTES, c["id"]
        assert isinstance(e["for_evie"], bool), c["id"]
        assert (e["route"] == "not_for_evie") == (not e["for_evie"]), c["id"]
        assert e["complete"] in (True, False, None) and e["has_event"] in (True, False, None), c["id"]
        assert c["utterance"] == c["utterance"].lower(), c["id"]


def test_holdout_is_about_a_fifth():
    hold = [c for c in load() if c["id"][-1] in "37"]
    assert 0.15 <= len(hold) / len(load()) <= 0.25
