"""Phase 6 P1: the job supervisor built on top of jobs.py's foreground JobRunner — background
jobs, priority, dependencies, timeouts, retries and pause/resume (a real session resume)."""
import asyncio

import pytest
from claude_agent_sdk import ResultMessage, TextBlock

from evie.jobs import JobRunner


def result(t, is_error=False):
    return ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=is_error,
                         num_turns=1, session_id="s", result=t)


def text(t):
    from claude_agent_sdk import AssistantMessage
    return AssistantMessage(content=[TextBlock(t)], model="m")


class FakeClient:
    """Like test_jobs.py's, but the factory that builds it can be asserted on afterwards."""
    def __init__(self, turns, hang=False, boom=None):
        self.turns = list(turns)
        self.hang = hang
        self.boom = boom
        self.queries: list[str] = []
        self.closed = False
        self.started = asyncio.Event()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True

    async def query(self, prompt):
        self.queries.append(prompt)

    async def receive_response(self):
        turn = self.turns.pop(0) if self.turns else [result("")]
        self.started.set()
        for m in turn:
            await asyncio.sleep(0)
            if self.boom and m == "boom":
                raise self.boom
            yield m
        if self.hang:
            await asyncio.Event().wait()


class Recorder:
    def __init__(self):
        self.events: list[str] = []
        self.done: list = []

    async def on_event(self, job, line):
        self.events.append(line)

    async def on_done(self, job):
        self.done.append(job)


def factory_of(clients: list[FakeClient]):
    """Returns each client in order, recording the kwargs it was built with."""
    calls: list[dict] = []

    def factory(**kw):
        calls.append(kw)
        return clients[len(calls) - 1]
    factory.calls = calls
    return factory


async def _settle(cond, tries=200):
    for _ in range(tries):
        if cond():
            return True
        await asyncio.sleep(0.005)
    return False


# -- background jobs run alongside the foreground one -----------------------------------------

async def test_background_job_runs_while_foreground_is_busy():
    rec = Recorder()
    fg = FakeClient([[text("working")]], hang=True)
    bg = FakeClient([[result("done research")]])
    r = JobRunner(rec.on_event, rec.on_done, client_factory=factory_of([fg, bg]))
    await r.start("watch this space")
    await fg.started.wait()
    job = await r.start_background("research MIT deadlines")
    assert await _settle(lambda: job in rec.done)
    assert job.status == "done" and r.current is not None  # the foreground job is untouched
    await r.stop()


def test_background_property_lists_jobs_regardless_of_status():
    r = JobRunner(Recorder().on_event, Recorder().on_done, client_factory=factory_of([]))
    assert r.background == []


async def test_all_jobs_includes_foreground_and_background():
    rec = Recorder()
    fg = FakeClient([[text("x")]], hang=True)
    bg = FakeClient([[result("ok")]], hang=True)
    r = JobRunner(rec.on_event, rec.on_done, client_factory=factory_of([fg, bg]))
    await r.start("fg job")
    await fg.started.wait()
    job = await r.start_background("bg job")
    await bg.started.wait()
    ids = {j.id for j in r.all_jobs()}
    assert r.current.id in ids and job.id in ids and len(ids) == 2
    await r.stop()
    await r.cancel(job.id)


# -- resource limit: at most max_background run at once ---------------------------------------

async def test_max_background_queues_extra_jobs_instead_of_running_them():
    rec = Recorder()
    slow = FakeClient([[text("still going")]], hang=True)
    other = FakeClient([[result("ok")]], hang=True)
    r = JobRunner(rec.on_event, rec.on_done, client_factory=factory_of([slow, other]), max_background=1)
    j1 = await r.start_background("first background job")
    await slow.started.wait()
    j2 = await r.start_background("second background job")
    await asyncio.sleep(0.02)
    assert j1.status == "running" and j2.status == "queued"
    await r.cancel(j1.id)
    await r.cancel(j2.id)


# -- priority ------------------------------------------------------------------------------

async def test_priority_reorders_the_foreground_backlog():
    r = JobRunner(lambda j, l: asyncio.sleep(0), lambda j: asyncio.sleep(0),
                 client_factory=lambda **kw: FakeClient([[result("x")]], hang=True))
    await r.start("first")
    r.enqueue("low priority", priority=0)
    r.enqueue("urgent fix", priority=10)
    assert r.queued == ["urgent fix", "low priority"]
    assert r.drop_next() == "urgent fix"
    assert r.queued == ["low priority"]
    await r.stop()


# -- dependencies ----------------------------------------------------------------------------

async def test_dependent_background_job_waits_for_its_dependency():
    rec = Recorder()
    a = FakeClient([[result("a done")]])
    b = FakeClient([[result("b done")]])
    r = JobRunner(rec.on_event, rec.on_done, client_factory=factory_of([a, b]))
    job_a = await r.start_background("step one")
    job_b = await r.start_background("step two", depends_on=(job_a.id,))
    assert job_b.status == "blocked"
    assert await _settle(lambda: job_b in rec.done)
    assert job_a.status == "done" and job_b.status == "done"
    assert b.queries == ["step two"]  # only queried once dependency cleared


async def test_dependent_job_fails_without_running_when_dependency_fails():
    rec = Recorder()
    a = FakeClient([[result("boom", is_error=True)]])
    calls = []

    def factory(**kw):
        calls.append(kw)
        return a
    r = JobRunner(rec.on_event, rec.on_done, client_factory=factory)
    job_a = await r.start_background("will fail")
    job_b = await r.start_background("depends on a", depends_on=(job_a.id,))
    assert await _settle(lambda: job_b in rec.done)
    assert job_a.status == "failed" and job_b.status == "failed"
    assert "will fail" not in " ".join(str(c) for c in calls[1:])  # b's client was never built
    assert len(calls) == 1


async def test_unknown_dependency_id_raises_immediately():
    r = JobRunner(Recorder().on_event, Recorder().on_done, client_factory=factory_of([]))
    with pytest.raises(ValueError):
        await r.start_background("x", depends_on=("nope",))


# -- timeouts --------------------------------------------------------------------------------

async def test_background_job_times_out():
    rec = Recorder()
    forever = FakeClient([[text("thinking")]], hang=True)
    r = JobRunner(rec.on_event, rec.on_done, client_factory=factory_of([forever]))
    job = await r.start_background("never finishes", timeout_s=0.05)
    assert await _settle(lambda: job in rec.done)
    assert job.status == "timeout"


# -- retries -----------------------------------------------------------------------------------

async def test_background_job_retries_then_succeeds():
    rec = Recorder()
    fail1 = FakeClient([[result("first crash", is_error=True)]])
    fail2 = FakeClient([[result("second crash", is_error=True)]])
    ok = FakeClient([[result("finally ok")]])
    r = JobRunner(rec.on_event, rec.on_done, client_factory=factory_of([fail1, fail2, ok]),
                 retry_backoff_s=lambda n: 0)
    job = await r.start_background("flaky task", max_retries=2)
    assert await _settle(lambda: job in rec.done)
    assert job.status == "done" and job.retries_done == 2
    assert rec.done == [job]  # on_done fires once, not per attempt


async def test_background_job_gives_up_after_max_retries():
    rec = Recorder()
    always_fails = [FakeClient([[result("nope", is_error=True)]]) for _ in range(3)]
    r = JobRunner(rec.on_event, rec.on_done, client_factory=factory_of(always_fails),
                 retry_backoff_s=lambda n: 0)
    job = await r.start_background("doomed task", max_retries=2)
    assert await _settle(lambda: job in rec.done)
    assert job.status == "failed" and job.retries_done == 2 and rec.done == [job]


# -- pause / resume: a real Claude Code session resume, not a restart -------------------------

async def test_pause_cancels_and_resume_continues_the_same_session():
    rec = Recorder()
    first = FakeClient([[text("halfway")]], hang=True)
    after_resume = FakeClient([[result("finished after resuming")]])
    factory = factory_of([first, after_resume])
    r = JobRunner(rec.on_event, rec.on_done, client_factory=factory)
    job = await r.start_background("a long task")
    await first.started.wait()
    assert await r.pause(job.id)
    assert job.status == "paused" and first.closed
    assert job not in rec.done  # paused isn't done

    resumed = await r.resume_job(job.id)
    assert resumed is job
    assert await _settle(lambda: job in rec.done)
    assert job.status == "done"
    assert factory.calls[0].get("session_id") == job.session_id
    assert factory.calls[1].get("resume") == job.session_id
    assert after_resume.queries == ["Continue where you left off; you don't need to start over."]


async def test_pause_unknown_job_returns_false():
    r = JobRunner(Recorder().on_event, Recorder().on_done, client_factory=factory_of([]))
    assert await r.pause("nope") is False


async def test_resume_non_paused_job_returns_none():
    rec = Recorder()
    c = FakeClient([[result("ok")]])
    r = JobRunner(rec.on_event, rec.on_done, client_factory=factory_of([c]))
    job = await r.start_background("quick one")
    assert await _settle(lambda: job in rec.done)
    assert await r.resume_job(job.id) is None


# -- cancel: unlike pause, it's for good, and mirrors stop()'s "no on_done" convention ---------

async def test_cancel_stops_a_background_job_for_good_without_firing_on_done():
    rec = Recorder()
    forever = FakeClient([[text("working")]], hang=True)
    r = JobRunner(rec.on_event, rec.on_done, client_factory=factory_of([forever]))
    job = await r.start_background("cancel me")
    await forever.started.wait()
    assert await r.cancel(job.id)
    assert job.status == "stopped" and forever.closed and rec.done == []


async def test_cancel_unknown_job_returns_false():
    r = JobRunner(Recorder().on_event, Recorder().on_done, client_factory=factory_of([]))
    assert await r.cancel("nope") is False


async def test_shutdown_cancels_background_jobs_too():
    rec = Recorder()
    fg = FakeClient([[text("x")]], hang=True)
    bg = FakeClient([[text("y")]], hang=True)
    r = JobRunner(rec.on_event, rec.on_done, client_factory=factory_of([fg, bg]))
    await r.start("fg")
    await fg.started.wait()
    job = await r.start_background("bg")
    await bg.started.wait()
    await r.shutdown()
    assert fg.closed and bg.closed and job.status == "stopped"
