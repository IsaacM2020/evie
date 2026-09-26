import asyncio
import json

from evie.events import EventBus
from evie.world_model import WorldStore, run


def store(tmp_path, **kw):
    return WorldStore(path=tmp_path / "world.json", actions_log=tmp_path / "actions.jsonl", **kw)


def test_heard_sets_active_thread(tmp_path):
    s = store(tmp_path)
    s.apply({"kind": "heard", "text": "what's on friday", "speaker": "isaac", "t": 1.0})
    assert s.state.active_thread.text == "what's on friday" and s.state.active_thread.speaker == "isaac"


def test_verdict_sets_route_on_the_existing_thread(tmp_path):
    s = store(tmp_path)
    s.apply({"kind": "heard", "text": "play trance", "speaker": "isaac"})
    s.apply({"kind": "verdict", "action": "act", "reason": "quick_action", "route": "music_play"})
    assert s.state.active_thread.route == "music_play" and s.state.active_thread.text == "play trance"


def test_job_lifecycle_tracked_and_moved_to_last_job_when_done(tmp_path):
    s = store(tmp_path)
    s.apply({"kind": "job_started", "id": "j1", "goal": "fix the bug", "tier": "hard"})
    assert s.state.active_jobs["j1"]["goal"] == "fix the bug"
    s.apply({"kind": "job_progress", "id": "j1", "done": 1, "total": 3, "step": "reading logs"})
    assert s.state.active_jobs["j1"]["progress"] == "1/3"
    s.apply({"kind": "job_done", "id": "j1", "status": "done", "summary": "Fixed it."})
    assert "j1" not in s.state.active_jobs
    assert s.state.last_job == {"id": "j1", "goal": "fix the bug", "tier": "hard",
                                "started": s.state.last_job["started"], "step": "reading logs",
                                "progress": "1/3", "status": "done", "summary": "Fixed it.",
                                "ended": s.state.last_job["ended"]}


def test_job_done_without_a_started_event_still_recorded(tmp_path):
    s = store(tmp_path)
    s.apply({"kind": "job_done", "id": "j9", "status": "stopped", "summary": "Stopped."})
    assert s.state.last_job["id"] == "j9" and s.state.last_job["status"] == "stopped"


def test_recent_events_capped_and_never_carries_the_event_payload(tmp_path):
    s = store(tmp_path)
    for i in range(150):
        s.apply({"kind": "overheard", "text": f"secret plan {i}", "reason": "not for Evie"})
    assert len(s.state.recent_events) == 100
    assert all(set(e) == {"kind", "t"} for e in s.state.recent_events)
    assert json.dumps(s.state.recent_events).find("secret plan") == -1


def test_scene_changed_updates_and_persists(tmp_path):
    p = tmp_path / "world.json"
    s = WorldStore(path=p, actions_log=tmp_path / "actions.jsonl")
    s.scene_changed("Safari", True, "BBC News")
    assert s.state.current_app == "Safari" and s.state.in_call and s.state.front_window == "BBC News"
    reloaded = WorldStore(path=p, actions_log=tmp_path / "actions.jsonl")
    assert reloaded.state.current_app == "Safari" and reloaded.state.in_call


def test_state_survives_a_restart(tmp_path):
    p = tmp_path / "world.json"
    s1 = WorldStore(path=p, actions_log=tmp_path / "actions.jsonl")
    s1.apply({"kind": "job_started", "id": "j1", "goal": "research MIT deadlines"})
    s2 = WorldStore(path=p, actions_log=tmp_path / "actions.jsonl")
    assert s2.state.active_jobs["j1"]["goal"] == "research MIT deadlines"


def test_health_event_from_the_bus_updates_system_health(tmp_path):
    s = store(tmp_path)
    s.apply({"kind": "health", "component": "jev", "event": "degraded", "ok": False, "detail": "timeout"})
    assert s.state.system_health["jev"].ok is False and s.state.system_health["jev"].detail == "timeout"


def test_health_tracks_consecutive_failures_and_resets_on_ok(tmp_path):
    s = store(tmp_path)
    s.set_health("jev", False, "timeout")
    s.set_health("jev", False, "timeout")
    assert s.state.system_health["jev"].consecutive_failures == 2
    s.set_health("jev", True)
    assert s.state.system_health["jev"].ok and s.state.system_health["jev"].consecutive_failures == 0


def test_recent_actions_tails_the_actions_log(tmp_path):
    log = tmp_path / "actions.jsonl"
    log.write_text("".join(json.dumps({"skill": "volume", "n": i}) + "\n" for i in range(30)))
    s = store(tmp_path)
    out = s.recent_actions(5)
    assert len(out) == 5 and out[-1] == {"skill": "volume", "n": 29}


def test_recent_actions_missing_file_is_empty(tmp_path):
    assert store(tmp_path).recent_actions() == []


def test_snapshot_reads_injected_views_without_copying_their_storage(tmp_path):
    s = store(tmp_path, calendar_view=lambda: {"today": "9am chem"}, tasks_view=lambda: ["essay"],
             projects_view=lambda: ["evie"], people_view=lambda: {"Dada": "father"},
             commitments_view=lambda: [{"what": "dentist"}])
    snap = s.snapshot()
    assert snap["calendar"] == {"today": "9am chem"} and snap["tasks"] == ["essay"]
    assert snap["projects"] == ["evie"] and snap["people"] == {"Dada": "father"}
    assert snap["commitments"] == [{"what": "dentist"}]


def test_brief_returns_only_requested_fields(tmp_path):
    s = store(tmp_path, calendar_view=lambda: {"today": "x"})
    out = s.brief({"calendar", "current_app"})
    assert set(out) == {"calendar", "current_app"}


async def test_run_consumes_bus_events_until_cancelled(tmp_path):
    bus = EventBus()
    s = store(tmp_path)
    task = asyncio.create_task(run(bus, s))
    await asyncio.sleep(0)
    bus.publish("heard", text="hello evie", speaker="isaac")
    for _ in range(50):
        if s.state.active_thread.text:
            break
        await asyncio.sleep(0.01)
    assert s.state.active_thread.text == "hello evie"
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert bus.subscribers == 0
