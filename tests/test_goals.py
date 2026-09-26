from evie.goals import GoalCommand, GoalStore, format_goal, parse_goal_command


def test_create_and_get_roundtrip(tmp_path):
    s = GoalStore(path=tmp_path / "goals.json")
    g = s.create("get the NOI regional qualification", deadline="December", next_action="finish the mock set")
    assert s.get(g.id).outcome == "get the NOI regional qualification"
    assert g.status == "active" and g.deadline == "December"


def test_state_survives_a_restart(tmp_path):
    p = tmp_path / "goals.json"
    s1 = GoalStore(path=p)
    g = s1.create("ship the cricket win predictor v2")
    s2 = GoalStore(path=p)
    assert s2.get(g.id).outcome == "ship the cricket win predictor v2"


def test_list_filters_by_status(tmp_path):
    s = GoalStore(path=tmp_path / "goals.json")
    a = s.create("goal a")
    b = s.create("goal b")
    s.set_status(b.id, "paused")
    assert [g.id for g in s.list_goals("active")] == [a.id]
    assert [g.id for g in s.list_goals("paused")] == [b.id]
    assert len(s.list_goals()) == 2


def test_find_by_text_fuzzy_matches_outcome(tmp_path):
    s = GoalStore(path=tmp_path / "goals.json")
    s.create("get the NOI regional qualification")
    s.create("ship the cricket win predictor v2")
    found = s.find_by_text("noi regional qualification")
    assert found is not None and "NOI" in found.outcome


def test_find_by_text_returns_none_when_nothing_close(tmp_path):
    s = GoalStore(path=tmp_path / "goals.json")
    s.create("get the NOI regional qualification")
    assert s.find_by_text("what's the weather like") is None


def test_find_by_text_ignores_done_goals_by_default(tmp_path):
    s = GoalStore(path=tmp_path / "goals.json")
    g = s.create("get the NOI regional qualification")
    s.set_status(g.id, "done")
    assert s.find_by_text("noi regional qualification") is None


def test_record_progress_updates_timestamp_and_next_action(tmp_path):
    s = GoalStore(path=tmp_path / "goals.json", clock=lambda: 100.0)
    g = s.create("ship v2")
    s.record_progress(g.id, "finished the backtest", next_action="write up results")
    got = s.get(g.id)
    assert got.last_progress_at == 100.0 and got.last_progress_note == "finished the backtest"
    assert got.next_action == "write up results"


def test_resume_sets_status_active(tmp_path):
    s = GoalStore(path=tmp_path / "goals.json")
    g = s.create("goal a")
    s.set_status(g.id, "paused")
    s.resume(g.id)
    assert s.get(g.id).status == "active"


def test_milestones_add_and_complete_by_fuzzy_text(tmp_path):
    s = GoalStore(path=tmp_path / "goals.json")
    g = s.create("get the NOI regional qualification")
    s.add_milestone(g.id, "finish the mock set")
    s.add_milestone(g.id, "qualify for regionals")
    s.complete_milestone(g.id, "finish mock set")
    got = s.get(g.id)
    assert got.milestones[0]["done"] is True and got.milestones[1]["done"] is False


def test_stale_lists_active_goals_with_no_recent_progress(tmp_path):
    now = [1_000_000.0]
    s = GoalStore(path=tmp_path / "goals.json", clock=lambda: now[0])
    fresh = s.create("goal a")
    stale = s.create("goal b")
    now[0] += 20 * 86400
    s.record_progress(fresh.id, "still going")
    assert [g.id for g in s.stale(days=10)] == [stale.id]


def test_stale_excludes_paused_and_done_goals(tmp_path):
    now = [1_000_000.0]
    s = GoalStore(path=tmp_path / "goals.json", clock=lambda: now[0])
    g = s.create("goal a")
    s.set_status(g.id, "paused")
    now[0] += 20 * 86400
    assert s.stale(days=10) == []


def test_growth_cap_only_removes_done_or_dropped_goals(tmp_path):
    s = GoalStore(path=tmp_path / "goals.json", max_goals=2)
    live = s.create("still working on this")
    s.set_status(live.id, "active")
    done = s.create("finished this one")
    s.set_status(done.id, "done")
    s.create("a brand new one, also active")  # pushes the store over the cap
    ids = {g.id for g in s.list_goals()}
    assert live.id in ids and done.id not in ids and len(ids) == 2


def test_unknown_id_operations_return_none(tmp_path):
    s = GoalStore(path=tmp_path / "goals.json")
    assert s.set_status("nope", "done") is None
    assert s.record_progress("nope", "x") is None
    assert s.add_milestone("nope", "x") is None
    assert s.complete_milestone("nope", "x") is None


def test_format_goal_reads_naturally(tmp_path):
    s = GoalStore(path=tmp_path / "goals.json")
    g = s.create("get the NOI regional qualification", deadline="December", next_action="finish the mock set")
    s.add_milestone(g.id, "mocks")
    assert format_goal(g) == ("get the NOI regional qualification (active), due December, "
                              "next: finish the mock set, 0/1 milestones")


# -- command parsing: plain code, never Jev ----------------------------------------------------

def test_parse_new_goal():
    assert parse_goal_command("new goal: get the NOI regional qualification by December") == GoalCommand(
        "new", "get the NOI regional qualification by December")


def test_parse_new_goal_alternate_phrasing():
    assert parse_goal_command("start a goal to ship the cricket predictor") == GoalCommand(
        "new", "ship the cricket predictor")


def test_parse_progress_update():
    assert parse_goal_command("update my goal on NOI: finished the mock set") == GoalCommand(
        "progress", "NOI: finished the mock set")


def test_parse_resume():
    assert parse_goal_command("resume my goal on the cricket predictor") == GoalCommand(
        "resume", "the cricket predictor")


def test_parse_pause():
    assert parse_goal_command("pause my goal on the wearable device") == GoalCommand(
        "pause", "the wearable device")


def test_parse_done():
    assert parse_goal_command("mark the goal on NOI as done") == GoalCommand("done", "NOI")


def test_parse_status():
    assert parse_goal_command("how's my goal on NOI going") == GoalCommand("status", "NOI going")
    assert parse_goal_command("what's next on the cricket predictor goal") == GoalCommand(
        "status", "cricket predictor")


def test_parse_status_accepts_the_uncontracted_phrasing():
    """Real-Mac testing (2026-09-26): Isaac's own "how IS my goal on X going" fell through to a
    plain answer, missing the goal entirely, because how'?s only ever matched the contraction."""
    assert parse_goal_command("how is my goal on phase six going") == GoalCommand("status", "phase six going")
    assert parse_goal_command("how are my goals on NOI going") == GoalCommand("status", "NOI going")
    assert parse_goal_command("how is the weather today") is None


def test_parse_list():
    assert parse_goal_command("what are my goals") == GoalCommand("list")
    assert parse_goal_command("list my goals") == GoalCommand("list")


def test_parse_returns_none_for_unrelated_speech():
    assert parse_goal_command("what's on my calendar today") is None
    assert parse_goal_command("play some lofi") is None
    assert parse_goal_command("remind me to buy milk") is None


def test_parse_never_shadows_existing_verbs_that_mean_something_else():
    """"pause"/"resume"/"mark done"/"how's X" are real, already-evaluated commands (music
    control, task_done, plain questions). Only the literal word "goal" should ever divert them."""
    assert parse_goal_command("pause the music") is None
    assert parse_goal_command("pause") is None
    assert parse_goal_command("resume the music") is None
    assert parse_goal_command("resume") is None
    assert parse_goal_command("mark that done") is None
    assert parse_goal_command("mark my chemistry homework done") is None
    assert parse_goal_command("how's the weather today") is None
    assert parse_goal_command("what's next on my calendar") is None
