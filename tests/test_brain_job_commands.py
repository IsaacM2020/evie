"""Phase 6 gap-fill: voice control over background jobs (pause/resume/stop), matched and acted on
in brain.py without ever reaching the switchboard (Jev) — see evie.job_commands, and
tests/test_brain_goals.py for the identical pattern this mirrors."""
from evie.jobs import Job
from tests.test_brain import FakeRunner, brain


def bg(id, goal, status="running"):
    return Job(goal=goal, id=id, status=status, foreground=False)


async def test_pause_that_job_with_one_background_job():
    runner = FakeRunner(background=[bg("j1", "deploy the site")])
    b, parts = brain(runner=runner)
    out = await b.hear("pause that job")
    assert out["action"] == "act" and out["reason"] == "job_command" and out["route"] == "pause"
    assert runner.paused == ["j1"] and "Paused deploy the site" in parts["mouth"].said[-1]


async def test_pause_the_background_job_phrasing():
    runner = FakeRunner(background=[bg("j1", "deploy the site")])
    b, parts = brain(runner=runner)
    await b.hear("pause the background job")
    assert runner.paused == ["j1"]


async def test_pause_job_two_by_ordinal():
    runner = FakeRunner(background=[bg("j1", "deploy the site"), bg("j2", "write the report")])
    b, parts = brain(runner=runner)
    await b.hear("pause job two")
    assert runner.paused == ["j2"] and "write the report" in parts["mouth"].said[-1]


async def test_pause_job_number_out_of_range():
    runner = FakeRunner(background=[bg("j1", "deploy the site")])
    b, parts = brain(runner=runner)
    await b.hear("pause job five")
    assert runner.paused == [] and "don't have a job number 5" in parts["mouth"].said[-1]


async def test_resume_it_bare_after_a_pause_round_trip():
    runner = FakeRunner(background=[bg("j1", "deploy the site")])
    b, parts = brain(runner=runner)
    await b.hear("pause that job")
    out = await b.hear("resume it")
    assert out is not None and out["route"] == "resume"
    assert runner.resumed == ["j1"] and "Resuming deploy the site" in parts["mouth"].said[-1]


async def test_continue_that_job_explicit_phrasing():
    j = bg("j1", "deploy the site", status="paused")
    runner = FakeRunner(background=[j])
    b, parts = brain(runner=runner)
    await b.hear("continue that job")
    assert runner.resumed == ["j1"]


async def test_resume_it_bare_with_nothing_paused_falls_through_to_switchboard():
    runner = FakeRunner(background=[bg("j1", "deploy the site")])  # running, not paused
    b, parts = brain(runner=runner)
    await b.hear("resume it")
    assert runner.resumed == []
    assert len(parts["sb"].contexts) == 1  # not confidently a job command: ordinary speech to Jev


async def test_stop_that_job_with_only_a_background_job_running():
    runner = FakeRunner(background=[bg("j1", "deploy the site")])
    b, parts = brain(runner=runner)
    await b.hear("stop that job")
    assert runner.cancelled == ["j1"] and "Stopped deploy the site" in parts["mouth"].said[-1]


async def test_stop_that_job_defers_to_the_foreground_job_control_when_one_is_running():
    """Generic "stop that job" with the foreground job running and no background jobs is ambiguous
    with the existing, unchanged _job_control/JOB_OP_Q voice surface: it must fall through to it,
    not get hijacked by the new background-job parser."""
    runner = FakeRunner(running="write the essay")
    b, parts = brain(runner=runner)
    await b.hear("stop that job")
    assert runner.cancelled == []
    assert len(parts["sb"].contexts) == 1


async def test_stop_the_background_job_still_works_even_with_a_foreground_job_running():
    runner = FakeRunner(running="write the essay", background=[bg("j1", "deploy the site")])
    b, parts = brain(runner=runner)
    await b.hear("stop the background job")
    assert runner.cancelled == ["j1"]


async def test_ambiguous_pause_asks_one_clarification_then_resolves():
    runner = FakeRunner(background=[bg("j1", "deploy the site"), bg("j2", "write the report")])
    b, parts = brain(runner=runner)
    out = await b.hear("pause that job")
    assert out["route"] == "pause" and "job 1" in parts["mouth"].said[-1] and "job 2" in parts["mouth"].said[-1]
    assert runner.paused == []
    out2 = await b.hear("job two")
    assert out2["action"] == "act" and runner.paused == ["j2"]


async def test_ambiguous_clarification_answered_by_ordinal_word():
    runner = FakeRunner(background=[bg("j1", "deploy the site"), bg("j2", "write the report")])
    b, parts = brain(runner=runner)
    await b.hear("pause that job")
    await b.hear("the second one")
    assert runner.paused == ["j2"]


async def test_no_jobs_at_all_says_so():
    runner = FakeRunner()
    b, parts = brain(runner=runner)
    await b.hear("pause that job")
    assert "don't have a job to pause" in parts["mouth"].said[-1]


async def test_pause_while_only_foreground_running_explains_it_cant():
    runner = FakeRunner(running="write the essay")
    b, parts = brain(runner=runner)
    await b.hear("pause that job")
    assert "say stop instead" in parts["mouth"].said[-1]


async def test_pause_the_music_is_not_hijacked():
    runner = FakeRunner(background=[bg("j1", "deploy the site")])
    b, parts = brain(runner=runner)
    await b.hear("pause the music")
    assert runner.paused == []
    assert len(parts["sb"].contexts) == 1


# -- the security rail: an unmatched voice can't touch a job -------------------------------------

async def test_unknown_speaker_cannot_pause_a_job():
    runner = FakeRunner(background=[bg("j1", "deploy the site")])
    b, parts = brain(runner=runner)
    await b.hear("pause that job", speaker="unknown", addressed=False)
    assert runner.paused == []
    assert len(parts["sb"].contexts) == 1  # fell through to the normal switchboard instead


# -- job commands never reach Jev; everything else still does ------------------------------------

async def test_job_command_never_reaches_the_switchboard():
    runner = FakeRunner(background=[bg("j1", "deploy the site")])
    b, parts = brain(runner=runner)
    await b.hear("pause that job")
    assert parts["sb"].contexts == []


async def test_ordinary_speech_still_reaches_the_switchboard_with_background_jobs_running():
    runner = FakeRunner(background=[bg("j1", "deploy the site")])
    b, parts = brain(runner=runner)
    await b.hear("what time is it")
    assert len(parts["sb"].contexts) == 1
