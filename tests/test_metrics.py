from evals.metrics import check_targets, score


def R(id, action, for_evie=True, route="quick_action", speaker="isaac", d=None,
      complete=None, has_event=None):
    return {"id": id, "cat": "x", "utterance": id, "speaker": speaker, "action": action, "reason": "",
            "followup": False,
            "expect": {"for_evie": for_evie, "route": route, "complete": complete, "has_event": has_event},
            "decision": d}


def dec(route="quick_action", complete=0.9, has_event=0.1, ms=300.0):
    return {"for_evie": 0.9, "route": route, "route_confidence": 1.0, "route_probs": {}, "complete": complete,
            "has_event": has_event, "latency_ms": ms, "cost_usd": 0.00002}


def test_score_counts_the_right_things():
    results = [
        R("p1", "act", d=dec(), complete=True),                                # good command
        R("p2", "ignore", d=dec(route="answer")),                              # missed + wrong route
        R("n1", "act", for_evie=False, route="not_for_evie", d=dec(route="not_for_evie")),   # false action
        R("n2", "clarify", for_evie=False, route="not_for_evie", d=dec(route="not_for_evie", has_event=0.9),
          has_event=True),                                                       # false clarify, event right
        R("s1", "ignore", speaker="other", d=dec()),                           # sibling: correctly ignored
        R("j1", "ignore", for_evie=False, route="not_for_evie", d=None),       # jev failure
    ]
    m, fails = score(results)
    assert m["n"] == 6 and m["jev_failures"] == 1
    assert m["false_action"] == 1 and fails["false_action"] == ["n1"]
    assert fails["false_clarify"] == ["n2"] and m["false_clarify_rate"] == round(1 / 4, 3)
    assert fails["missed_command"] == ["p2"] and m["command_recall"] == 0.5
    assert fails["route"] == ["p2"] and m["route_accuracy"] == round(4 / 5, 3)
    assert m["complete_accuracy"] == 1.0 and m["event_accuracy"] == 1.0
    assert m["latency_p50_ms"] == 300 and m["cost_usd"] == 0.0001


def test_check_targets():
    good = {"false_action": 0, "false_clarify_rate": 0.05, "command_recall": 0.97, "route_accuracy": 0.93,
            "complete_accuracy": 0.9, "event_accuracy": 0.95, "latency_p95_ms": 700,
            "skill_accuracy": 0.95, "remember_to_accuracy": 0.92}
    assert all(check_targets(good).values())
    assert check_targets({**good, "false_action": 1})["false_action"] is False
    assert check_targets({**good, "event_accuracy": None})["event_accuracy"] is False


def test_skill_and_remember_to_are_scored():
    def r(i, skill=None, rem=None, got_skill=None, got_rem=None):
        e = {"for_evie": True, "route": "quick_action", **({"skill": skill} if skill else {}),
             **({"remember_to": rem} if rem else {})}
        d = {"for_evie": 0.9, "route": "quick_action", "complete": 0.9, "has_event": 0.0, "latency_ms": 300,
             "cost_usd": 0.0, "skill": got_skill, "remember_to": got_rem}
        return {"id": i, "speaker": "isaac", "expect": e, "action": "act", "decision": d}
    m, fails = score([r("a", "volume", got_skill="volume"), r("b", "open_app", got_skill="other"),
                      r("c", rem="fact", got_rem="fact")])
    assert fails["skill"] == ["b"] and m["skill_accuracy"] == 0.5 and m["remember_to_accuracy"] == 1.0
