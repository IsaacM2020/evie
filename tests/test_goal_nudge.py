"""Phase 6 P1: the proactive engine reasons from goals too, not just reminders."""
from evie.calendar_store import CalendarStore
from evie.goals import GoalStore
from evie.proactive.sources import Sources
from tests.test_sources import Engine, Talker, Todoist


def wire(tmp_path, clock):
    goals = GoalStore(path=tmp_path / "goals.json", clock=clock)
    engine = Engine()
    src = Sources(engine, CalendarStore(), Todoist(), Talker(), clock=clock, goals=goals)
    return src, engine, goals


async def test_stale_goal_gets_nudged_with_its_next_action(tmp_path):
    now = [1_000_000.0]
    src, engine, goals = wire(tmp_path, lambda: now[0])
    goals.create("get the NOI regional qualification", next_action="finish the mock set")
    now[0] += 20 * 86400
    await src.collect()
    assert len(engine.items) == 1
    assert "NOI regional qualification" in engine.items[0].line and "finish the mock set" in engine.items[0].line
    assert engine.items[0].on_yes == {"do": "say", "text": "finish the mock set"}


async def test_fresh_goal_is_not_nudged(tmp_path):
    now = [1_000_000.0]
    src, engine, goals = wire(tmp_path, lambda: now[0])
    goals.create("ship v2")
    await src.collect()
    assert engine.items == []


async def test_no_goals_store_wired_is_a_silent_no_op(tmp_path):
    engine = Engine()
    src = Sources(engine, CalendarStore(), Todoist(), Talker(), goals=None)
    await src.collect()  # must not raise
    assert engine.items == []


async def test_second_collect_within_the_radar_window_does_not_rescan(tmp_path):
    now = [1_000_000.0]
    src, engine, goals = wire(tmp_path, lambda: now[0])
    g = goals.create("ship v2")
    now[0] += 20 * 86400
    await src.collect()
    assert len(engine.items) == 1
    goals.record_progress(g.id, "nope, still stale, just checking the radar gate")
    goals.set_status(g.id, "active")  # record_progress doesn't change status; stays active/stale
    await src.collect()  # radar hasn't cooled down yet: no second look, no duplicate/updated item
    assert len(engine.items) == 1


async def test_paused_goal_is_never_nudged(tmp_path):
    now = [1_000_000.0]
    src, engine, goals = wire(tmp_path, lambda: now[0])
    g = goals.create("wearable device")
    goals.set_status(g.id, "paused")
    now[0] += 20 * 86400
    await src.collect()
    assert engine.items == []
