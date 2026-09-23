from evie.switchboard.decision import Decision
from evie.switchboard.policy import Action, Thresholds, decide


def D(for_evie=0.9, route="quick_action", conf=1.0, complete=0.9, has_event=0.0):
    return Decision(for_evie, route, conf, {route: conf}, complete, has_event, 250.0, 0.0)


def test_confident_complete_command_acts():
    v = decide(D(), "isaac")
    assert v.action is Action.ACT and v.reason == "quick_action" and not v.followup


def test_other_speaker_never_acts_even_if_certain():
    v = decide(D(for_evie=0.99), "other")
    assert v.action is Action.IGNORE and v.reason == "not Isaac's voice"


def test_overheard_event_is_ignored_but_flagged_for_followup():
    v = decide(D(for_evie=0.02, route="not_for_evie", has_event=0.99), "isaac")
    assert v.action is Action.IGNORE and v.followup


def test_other_speaker_event_still_flagged():
    v = decide(D(for_evie=0.01, route="not_for_evie", has_event=0.95), "other")
    assert v.action is Action.IGNORE and v.followup


def test_below_floor_ignores():
    assert decide(D(for_evie=0.2), "isaac").action is Action.IGNORE


def test_middle_confidence_clarifies():
    v = decide(D(for_evie=0.6), "isaac")
    assert v.action is Action.CLARIFY and v.reason == "unsure it was for me"


def test_answer_has_lower_bar_and_needs_no_detail():
    v = decide(D(for_evie=0.65, route="answer", complete=0.1), "isaac")
    assert v.action is Action.ACT


def test_missing_detail_clarifies():
    v = decide(D(complete=0.1), "isaac")
    assert v.action is Action.CLARIFY and v.reason == "missing detail"


def test_unknown_speaker_needs_more_confidence():
    assert decide(D(for_evie=0.75), "isaac").action is Action.ACT
    assert decide(D(for_evie=0.75), "unknown").action is Action.CLARIFY


def test_low_route_confidence_clarifies():
    assert decide(D(conf=0.5), "isaac").action is Action.CLARIFY


def test_thresholds_are_injectable():
    strict = Thresholds(act_at=0.95)
    assert decide(D(for_evie=0.9), "isaac", strict).action is Action.CLARIFY


def Dp(for_evie, probs, complete=0.9):
    route = max(probs, key=probs.get)
    return Decision(for_evie, route, probs[route], probs, complete, 0.0, 250.0, 0.0)


def test_addressed_skips_the_is_it_for_me_gate():
    # "How's the iGEM website looking" said while holding the talk key: for_evie 0.62
    v = decide(Dp(0.62, {"answer": 0.9, "not_for_evie": 0.1}), "isaac", addressed=True)
    assert v.action is Action.ACT and v.reason == "answer"


def test_addressed_uses_best_real_route_when_jev_says_not_for_evie():
    v = decide(Dp(0.3, {"not_for_evie": 0.5, "deep_job": 0.45, "answer": 0.05}), "isaac", addressed=True)
    assert v.action is Action.ACT and v.reason == "deep_job"


def test_addressed_but_route_unclear_asks_what_he_meant():
    v = decide(Dp(0.8, {"answer": 0.4, "deep_job": 0.35, "quick_action": 0.25}), "isaac", addressed=True)
    assert v.action is Action.CLARIFY and v.reason == "unsure what you meant"


def test_addressed_still_needs_detail():
    v = decide(Dp(0.9, {"quick_action": 1.0}, complete=0.1), "isaac", addressed=True)
    assert v.action is Action.CLARIFY and v.reason == "missing detail"


def test_addressed_never_overrides_another_speaker():
    v = decide(Dp(0.9, {"deep_job": 1.0}), "other", addressed=True)
    assert v.action is Action.IGNORE


def test_open_mic_unknown_voice_can_ask_but_not_act():
    for route in ("quick_action", "remember", "deep_job", "job_control"):
        v = decide(D(for_evie=0.99, route=route), "unknown")
        assert v.action is Action.CLARIFY and v.reason == "unsure it was for me", route


def test_open_mic_unknown_voice_still_gets_answers():
    v = decide(D(for_evie=0.95, route="answer"), "unknown")
    assert v.action is Action.ACT and v.reason == "answer"


def test_unknown_voice_on_the_talk_key_is_fine():
    assert decide(D(route="quick_action"), "unknown", addressed=True).action is Action.ACT



def test_named_by_isaac_acts_on_a_lukewarm_for_evie():
    # 21:10:55: "Evie, what files are on my desktop" got "Was that for me?"
    d = D(for_evie=0.6, route="deep_job", conf=0.9)
    assert decide(d, "isaac", named=True).action == Action.ACT
    assert decide(d, "isaac").action == Action.CLARIFY


def test_named_but_clearly_about_her_is_still_ignored():
    d = D(for_evie=0.15, route="not_for_evie", conf=0.9)
    assert decide(d, "isaac", named=True).action == Action.IGNORE
