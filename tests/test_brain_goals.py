"""Phase 6 P1: goal commands are matched and acted on in brain.py without ever reaching the
switchboard (Jev) — see evie.goals for why, and is_stop()/is_other() in brain.py for precedent."""
from evie.goals import GoalStore
from tests.test_brain import FakeTalker, brain


class GoalTalker(FakeTalker):
    def __init__(self, extraction=None):
        super().__init__()
        self._extraction = extraction or {}

    async def extract(self, question, text):
        self.calls.append(("extract", question, text))
        return self._extraction


def wire(tmp_path, goals=None, talker=None):
    goals = goals or GoalStore(path=tmp_path / "goals.json")
    b, parts = brain(goals=goals)
    if talker is not None:
        b._talker = parts["talker"] = talker
    return b, parts, goals


async def test_new_goal_extracts_outcome_and_deadline_and_confirms(tmp_path):
    talker = GoalTalker({"outcome": "get the NOI regional qualification", "deadline": "December"})
    b, parts, goals = wire(tmp_path, talker=talker)
    out = await b.hear("new goal: get the noi regional qualification by december")
    assert out["action"] == "act" and out["reason"] == "goal" and out["route"] == "new"
    [g] = goals.list_goals()
    assert g.outcome == "get the NOI regional qualification" and g.deadline == "December"
    assert "Tracking it" in parts["mouth"].said[-1] and "December" in parts["mouth"].said[-1]


async def test_status_finds_existing_goal_by_fuzzy_speech(tmp_path):
    b, parts, goals = wire(tmp_path)
    goals.create("get the NOI regional qualification", next_action="finish the mock set")
    await b.hear("how's my goal on NOI going")
    assert "finish the mock set" in parts["mouth"].said[-1]


async def test_status_on_unknown_goal_says_it_could_not_find_one(tmp_path):
    b, parts, goals = wire(tmp_path)
    await b.hear("how's my goal on the moon landing going")
    assert "couldn't find" in parts["mouth"].said[-1]


async def test_pause_then_resume_round_trip(tmp_path):
    b, parts, goals = wire(tmp_path)
    g = goals.create("ship the cricket win predictor v2", next_action="backtest it")
    await b.hear("pause my goal on the cricket predictor")
    assert goals.get(g.id).status == "paused"
    await b.hear("resume my goal on the cricket predictor")
    assert goals.get(g.id).status == "active"
    assert "backtest it" in parts["mouth"].said[-1]


async def test_done_marks_the_goal_complete(tmp_path):
    b, parts, goals = wire(tmp_path)
    g = goals.create("get the NOI regional qualification")
    await b.hear("mark the goal on NOI as done")
    assert goals.get(g.id).status == "done"
    assert "done" in parts["mouth"].said[-1].lower()


async def test_progress_update_records_note_and_next_action(tmp_path):
    talker = GoalTalker({"goal": "NOI", "note": "finished the mock set", "next_action": "book regionals"})
    b, parts, goals = wire(tmp_path, talker=talker)
    g = goals.create("get the NOI regional qualification")
    await b.hear("update my goal on NOI: finished the mock set, next book regionals")
    got = goals.get(g.id)
    assert got.last_progress_note == "finished the mock set" and got.next_action == "book regionals"
    assert "book regionals" in parts["mouth"].said[-1]


async def test_list_with_no_goals(tmp_path):
    b, parts, goals = wire(tmp_path)
    await b.hear("what are my goals")
    assert "don't have any goals" in parts["mouth"].said[-1]


async def test_list_with_many_goals_summarises_and_publishes_a_card(tmp_path):
    b, parts, goals = wire(tmp_path)
    for outcome in ("goal one", "goal two", "goal three"):
        goals.create(outcome)
    await b.hear("list my goals")
    assert "3 goals" in parts["mouth"].said[-1]
    assert any(ev["kind"] == "list" for ev in parts["bus"].history())


# -- the security rail: an unmatched voice can look, never touch --------------------------------

async def test_unknown_speaker_can_check_status(tmp_path):
    b, parts, goals = wire(tmp_path)
    goals.create("get the NOI regional qualification", next_action="finish the mock set")
    out = await b.hear("how's my goal on NOI going", speaker="unknown", addressed=False)
    assert out["reason"] == "goal" and "finish the mock set" in parts["mouth"].said[-1]


async def test_unknown_speaker_cannot_create_a_goal(tmp_path):
    b, parts, goals = wire(tmp_path)
    await b.hear("new goal: take over the world", speaker="unknown", addressed=False)
    assert goals.list_goals() == []
    assert len(parts["sb"].contexts) == 1  # fell through to the normal switchboard instead


async def test_unknown_speaker_cannot_pause_a_goal(tmp_path):
    b, parts, goals = wire(tmp_path)
    g = goals.create("get the NOI regional qualification")
    await b.hear("pause my goal on NOI", speaker="unknown", addressed=False)
    assert goals.get(g.id).status == "active"


# -- goal commands never reach Jev; everything else still does ----------------------------------

async def test_goal_command_never_reaches_the_switchboard(tmp_path):
    b, parts, goals = wire(tmp_path)
    goals.create("ship v2")
    await b.hear("how's my goal on ship v2 going")
    assert parts["sb"].contexts == []


async def test_ordinary_speech_still_reaches_the_switchboard_when_goals_is_wired(tmp_path):
    b, parts, goals = wire(tmp_path)
    await b.hear("what time is it")
    assert len(parts["sb"].contexts) == 1


async def test_without_a_goals_store_wired_behaviour_is_unchanged():
    b, parts = brain()  # goals=None, the default
    await b.hear("new goal: get the noi regional qualification")
    assert len(parts["sb"].contexts) == 1  # no goals store: this is just ordinary speech to Jev
