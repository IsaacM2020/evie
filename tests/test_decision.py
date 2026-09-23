import pytest

from evie.switchboard.decision import parse_decision
from evie.switchboard.questions import QUESTIONS, ROUTES
from tests.helpers import make_answers


def test_question_set_shape():
    assert set(QUESTIONS) == {"for_evie", "route", "complete", "has_event"}
    assert QUESTIONS["route"]["type"] == "choice"
    assert QUESTIONS["route"]["criteria"] is ROUTES
    assert set(ROUTES) == {"not_for_evie", "quick_action", "answer", "deep_job", "job_control", "remember"}
    for k in ("for_evie", "complete", "has_event"):
        assert QUESTIONS[k]["type"] == "noul"


def test_parse_real_shaped_answers():
    # Shape copied from the live 2026-09-23 run ("evie play that song")
    raw = {
        "for_evie": {"type": "noul", "noul": 0.8},
        "route": {"type": "choice", "choice": "quick_action",
                  "probabilities": {"quick_action": 1, "deep_job": 0, "remember": 0, "answer": 0, "not_for_evie": 0},
                  "confidence": 1},
        "complete": {"type": "noul", "noul": 0.05},
        "has_event": {"type": "noul", "noul": 0.02},
    }
    d = parse_decision(raw, latency_ms=332.0, cost_usd=0.000023)
    assert d.for_evie == 0.8 and d.route == "quick_action" and d.route_confidence == 1.0
    assert d.route_probs["quick_action"] == 1.0
    assert d.complete == 0.05 and d.has_event == 0.02
    assert d.latency_ms == 332.0 and d.cost_usd == 0.000023


def test_confidence_falls_back_to_probability():
    raw = make_answers()
    del raw["route"]["confidence"]
    assert parse_decision(raw).route_confidence == 1.0


def test_unknown_route_raises():
    raw = make_answers(route="launch_rockets")
    with pytest.raises(ValueError):
        parse_decision(raw)


def test_missing_field_raises():
    raw = make_answers()
    del raw["complete"]
    with pytest.raises(KeyError):
        parse_decision(raw)
