import asyncio

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock

from evie.jobs import ALLOWED_MODELS, Busy, JobRunner, TIERS, bash_hook, describe, guard_bash, make_client


@pytest.mark.parametrize("cmd,blocked", [
    ("rm -rf build", True),
    ("rm a.txt", True),
    ("ls && rm a", True),
    ("cd x; rm -r y", True),
    ("find . -name '*.pyc' | xargs rm", True),
    ("find . -name '*.pyc' -delete", True),
    ("/bin/rm a", True),
    ("sudo make install", True),
    ("git push --force origin main", True),
    ("git push -f", True),
    ("git reset --hard HEAD~3", True),
    ("\\rm a.txt", True),
    ("command rm a.txt", True),
    ("env rm -rf build", True),
    ("FOO=1 nohup rm a", True),
    ("bash -c \"rm -rf build\"", True),
    ("sh -c 'cd x && rm y'", True),
    ("bash -c 'echo hi'", False),
    ("trash a.txt", False),
    ("git push origin main", False),
    ("echo rm is blocked", False),
    ("uv run pytest -q", False),
    ("grep -rn 'rm ' src", False),
])
def test_guard_bash(cmd, blocked):
    assert (guard_bash(cmd) is not None) == blocked


async def test_bash_hook_denies_rm_with_reason():
    out = await bash_hook({"tool_name": "Bash", "tool_input": {"command": "rm -rf x"}}, "t1", None)
    hso = out["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny" and "trash" in hso["permissionDecisionReason"]


async def test_bash_hook_allows_safe_commands():
    assert await bash_hook({"tool_name": "Bash", "tool_input": {"command": "ls"}}, "t1", None) == {}


async def test_bash_hook_denies_a_task_call_that_requests_opus():
    """Review finding C1: the Bash|Task|Agent matcher (Task 5) is useless without the hook itself
    checking a non-Bash tool's input -- an Agent/Task call carries no 'command' key, so guard_bash
    was always a no-op for it. A sub-agent spawned with model: 'opus' must be denied here, not
    silently allowed because bash_hook only ever looked at tool_input['command']."""
    out = await bash_hook({"tool_name": "Task", "tool_input": {"subagent_type": "general-purpose",
                                                                "model": "opus", "prompt": "do x"}}, "t1", None)
    hso = out["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny" and "opus" in hso["permissionDecisionReason"].lower()


async def test_bash_hook_denies_an_agent_call_with_a_full_opus_model_id():
    out = await bash_hook({"tool_name": "Agent", "tool_input": {"model": "claude-opus-5-5"}}, "t1", None)
    hso = out["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny"


async def test_bash_hook_allows_a_task_call_with_an_allowed_model():
    out = await bash_hook({"tool_name": "Task", "tool_input": {"subagent_type": "general-purpose",
                                                                "model": "sonnet", "prompt": "do x"}}, "t1", None)
    assert out == {}


async def test_bash_hook_allows_a_task_call_with_no_model_specified():
    out = await bash_hook({"tool_name": "Task", "tool_input": {"subagent_type": "general-purpose",
                                                                "prompt": "do x"}}, "t1", None)
    assert out == {}


@pytest.mark.parametrize("block,line", [
    (ToolUseBlock("1", "Read", {"file_path": "/Users/isaac/x/model.py"}), "Read model.py"),
    (ToolUseBlock("1", "Edit", {"file_path": "/a/chase.py"}), "Edited chase.py"),
    (ToolUseBlock("1", "Write", {"file_path": "/a/test_chase.py"}), "Wrote test_chase.py"),
    (ToolUseBlock("1", "Bash", {"command": "uv run pytest -q\necho done"}), "Ran: uv run pytest -q"),
    (ToolUseBlock("1", "Grep", {"pattern": "chase"}), "Searched for 'chase'"),
    (ToolUseBlock("1", "TodoWrite", {"todos": []}), None),
    (ToolUseBlock("1", "WebSearch", {"query": "ipl 2026"}), "Searched the web for 'ipl 2026'"),
    (TextBlock("  Found the bug: the chase target is off by one.  "),
     "Found the bug: the chase target is off by one."),
    (TextBlock("   "), None),
])
def test_describe(block, line):
    assert describe(block) == line


def text(t):
    return AssistantMessage(content=[TextBlock(t)], model="m")


def tool(name, inp):
    return AssistantMessage(content=[ToolUseBlock("id", name, inp)], model="m")


def result(t, is_error=False):
    return ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=is_error,
                         num_turns=1, session_id="s", result=t)


class FakeClient:
    def __init__(self, turns, hang=False, boom=None):
        self.turns = list(turns)
        self.hang = hang
        self.boom = boom
        self.queries: list[str] = []
        self.closed = False
        self.first_turn_started = asyncio.Event()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True

    async def query(self, prompt):
        self.queries.append(prompt)

    async def receive_response(self):
        turn = self.turns.pop(0) if self.turns else [result("")]
        self.first_turn_started.set()
        for m in turn:
            await asyncio.sleep(0)
            if self.boom and m == "boom":
                raise self.boom
            yield m
        if self.hang:
            await asyncio.Event().wait()


class Recorder:
    def __init__(self):
        self.events = []
        self.done = []

    async def on_event(self, job, line):
        self.events.append(line)

    async def on_done(self, job):
        self.done.append(job)


def runner(client, rec):
    return JobRunner(rec.on_event, rec.on_done, client_factory=lambda: client)


async def test_events_flow_and_result_is_kept():
    rec = Recorder()
    c = FakeClient([[tool("Read", {"file_path": "/a/model.py"}), text("Found it."), result("Fixed the chase bug.")]])
    r = runner(c, rec)
    job = await r.start("fix the chase bug")
    await r.wait()
    assert c.queries == ["fix the chase bug"]
    assert rec.events == ["Read model.py", "Found it."]
    assert job.status == "done" and job.result == "Fixed the chase bug."
    assert rec.done == [job] and c.closed and r.current is None


async def test_error_result_marks_failed():
    rec = Recorder()
    r = runner(FakeClient([[result("hit max turns", is_error=True)]]), rec)
    job = await r.start("x")
    await r.wait()
    assert job.status == "failed" and rec.done == [job]


async def test_exception_marks_failed_and_still_reports_done():
    rec = Recorder()
    r = runner(FakeClient([[text("hi"), "boom"]], boom=RuntimeError("cli died")), rec)
    job = await r.start("x")
    await r.wait()
    assert job.status == "failed" and "cli died" in job.result and rec.done == [job]


async def test_second_start_while_running_is_busy():
    rec = Recorder()
    c = FakeClient([[text("working")]], hang=True)
    r = runner(c, rec)
    await r.start("first")
    await c.first_turn_started.wait()
    with pytest.raises(Busy):
        await r.start("second")
    await r.stop()


async def test_stop_marks_stopped_closes_client_and_skips_done():
    rec = Recorder()
    c = FakeClient([[text("working")]], hang=True)
    r = runner(c, rec)
    job = await r.start("x")
    await c.first_turn_started.wait()
    await r.stop()
    assert job.status == "stopped" and c.closed and rec.done == [] and r.current is None


async def test_shutdown_stops_running_job():
    rec = Recorder()
    c = FakeClient([[text("working")]], hang=True)
    r = runner(c, rec)
    job = await r.start("x")
    await c.first_turn_started.wait()
    await r.shutdown()
    assert job.status == "stopped" and c.closed


async def test_instruction_is_sent_as_next_turn():
    rec = Recorder()
    c = FakeClient([[text("fixing"), result("fixed")], [text("adding test"), result("fixed + tested")]])
    r = runner(c, rec)
    job = await r.start("fix the bug")
    assert await r.add_instruction("also add a test for it")
    await r.wait()
    assert c.queries == ["fix the bug", "also add a test for it"]
    assert job.result == "fixed + tested" and rec.done == [job]


async def test_add_instruction_with_no_job_returns_false():
    assert await runner(FakeClient([]), Recorder()).add_instruction("hi") is False


async def test_status_line():
    rec = Recorder()
    c = FakeClient([[tool("Bash", {"command": "pytest"})]], hang=True)
    r = runner(c, rec)
    assert r.status_line() == "Nothing running right now."
    await r.start("fix the chase bug")
    await c.first_turn_started.wait()
    await asyncio.sleep(0.01)
    assert r.status_line().startswith("I'm ") and "fix the chase bug" in r.status_line()
    assert "Ran: pytest" in r.status_line()
    await r.stop()


@pytest.mark.live
async def test_live_claude_code_tiny_job(tmp_path):
    from evie.jobs import make_client
    rec = Recorder()
    r = JobRunner(rec.on_event, rec.on_done, client_factory=lambda: make_client(cwd=tmp_path, max_turns=3))
    job = await r.start("Reply with just the word ok. Don't use any tools.")
    await asyncio.wait_for(r.wait(), timeout=120)
    assert job.status == "done" and "ok" in job.result.lower()


@pytest.mark.live
async def test_live_guard_blocks_rm_inside_claude_code(tmp_path):
    from evie.jobs import make_client
    (tmp_path / "keep.txt").write_text("x")
    rec = Recorder()
    r = JobRunner(rec.on_event, rec.on_done, client_factory=lambda: make_client(cwd=tmp_path, max_turns=4))
    await r.start("Run exactly this Bash command and nothing else: rm keep.txt . Then tell me what happened.")
    await asyncio.wait_for(r.wait(), timeout=120)
    assert (tmp_path / "keep.txt").exists()


class SlowCloseClient(FakeClient):
    def __init__(self, turns):
        super().__init__(turns)
        self.closing = asyncio.Event()
        self.release = asyncio.Event()

    async def __aexit__(self, *exc):
        self.closing.set()
        await self.release.wait()
        self.closed = True


async def test_instruction_during_wrap_up_is_refused_not_lost():
    rec = Recorder()
    c = SlowCloseClient([[text("done"), result("all done")]])
    r = runner(c, rec)
    job = await r.start("x")
    await c.closing.wait()
    assert await r.add_instruction("also add a test") is False
    c.release.set()
    await r.wait()
    assert job.status == "done" and c.queries == ["x"]


async def test_queued_goal_starts_when_the_current_job_finishes():
    started = []

    async def on_start(job):
        started.append(job.goal)

    done = []

    async def on_done(job):
        done.append(job.goal)

    r = JobRunner(lambda j, l: asyncio.sleep(0), on_done, client_factory=lambda: FakeClient([[result("ok")]]),
                  on_start=on_start)
    await r.start("first")
    r.enqueue("second")
    assert r.queued == ["second"]
    await r.wait()
    for _ in range(50):
        if r.current is None and started:
            break
        await asyncio.sleep(0.01)
    await r.wait()
    assert done == ["first", "second"] and started == ["second"] and r.queued == []


async def test_stopping_drops_the_queue_too():
    r = JobRunner(lambda j, l: asyncio.sleep(0), lambda j: asyncio.sleep(0),
                  client_factory=lambda: FakeClient([[result("x")]], hang=True))
    await r.start("first")
    r.enqueue("second")
    dropped = await r.stop()
    assert dropped == ["second"] and r.queued == []


TODOS = {"todos": [
    {"content": "Read the build log", "status": "completed", "activeForm": "Reading the build log"},
    {"content": "Find the missing env var", "status": "in_progress", "activeForm": "Finding the missing env var"},
    {"content": "Fix vercel.json", "status": "pending", "activeForm": "Fixing vercel.json"},
    {"content": "Run the build", "status": "pending", "activeForm": "Running the build"}]}


async def test_claude_codes_own_todo_list_is_real_progress():
    """Isaac (2026-09-24): during a long job 'I don't know the progress of it'."""
    rec = Recorder()
    c = FakeClient([[tool("TodoWrite", TODOS), text("Found it: VERCEL_TOKEN isn't set.")]], hang=True)
    r = runner(c, rec)
    job = await r.start("check why my website deploy failed")
    await c.first_turn_started.wait()
    await asyncio.sleep(0.01)
    assert job.progress() == (2, 4, "Finding the missing env var")
    assert "Step 2 of 4: Finding the missing env var" in rec.events
    assert r.status_line() == ("I'm on step 2 of 4 of check why my website deploy failed: finding the missing env var. "
                               "Latest: Found it: VERCEL_TOKEN isn't set.")
    await r.stop()


def test_the_worker_is_told_to_keep_a_plan_and_skip_memory_notices():
    from evie.jobs import WORKER_NOTE
    assert "TodoWrite" in WORKER_NOTE and "memory" in WORKER_NOTE


# -- Jev picks the model (Isaac, 2026-09-24: "Sonnet medium or high for most, Haiku for really simple", never Opus)
async def test_the_picked_tier_sets_model_and_effort():
    from evie.jobs import TIERS
    rec, made = Recorder(), []

    def factory(**kw):
        made.append(kw)
        return FakeClient([[result("ok")]])

    async def pick(goal):
        return "quick" if "open" in goal else "hard"

    r = JobRunner(rec.on_event, rec.on_done, client_factory=factory, pick_tier=pick)
    job = await r.start("open netflix and play the mentalist")
    await asyncio.sleep(0.05)
    assert job.tier == "quick" and made[-1] == {"model": TIERS["quick"][0], "effort": TIERS["quick"][1]}
    job = await r.start("fix the failing chase test in my cricket model")
    await asyncio.sleep(0.05)
    assert made[-1] == {"model": "claude-sonnet-5", "effort": "high"}


def test_opus_is_never_used():
    assert all("opus" not in m for m, _ in TIERS.values())
    with pytest.raises(ValueError):
        make_client(model="claude-opus-5-5")


async def test_jev_failing_means_the_normal_tier():
    from evie.jev import JevError
    from evie.jobs import pick_tier

    class Down:
        async def ask(self, state, q):
            raise JevError("down")

    assert await pick_tier(Down(), "research MIT's early action deadline") == "normal"


def test_allowlist_rejects_any_model_not_explicitly_listed():
    with pytest.raises(ValueError, match="never runs"):
        make_client(model="some-future-model-nobody-vetted")


def test_allowlist_still_rejects_opus_explicitly():
    with pytest.raises(ValueError, match="never runs"):
        make_client(model="claude-opus-5-5")


def test_none_model_resolves_to_the_normal_tier_default_not_claude_codes_own_setting():
    client = make_client(model=None)
    assert client.options.model == TIERS["normal"][0]


def test_hook_matcher_covers_bash_task_and_agent():
    client = make_client()
    matcher = client.options.hooks["PreToolUse"][0].matcher
    assert "Bash" in matcher and "Task" in matcher and "Agent" in matcher


def test_allowed_models_set_matches_the_three_named_in_the_spec():
    assert ALLOWED_MODELS == frozenset({"claude-haiku-4-5-20251001", "claude-sonnet-5", "claude-fable-5-1"})


def test_make_client_passes_mcp_servers_through_when_given():
    """P3 wiring: make_client(mcp_servers=...) reaches ClaudeAgentOptions.mcp_servers -- this is
    how brain.py's stuck-computer-task handoff will give Claude Code bridge.build_evie_hands_server()
    so it drives Evie's own hands instead of reinventing osascript."""
    fake_server = {"type": "sdk", "name": "evie_hands", "instance": object()}
    client = make_client(mcp_servers={"evie_hands": fake_server})
    assert client.options.mcp_servers == {"evie_hands": fake_server}


def test_make_client_omits_mcp_servers_when_not_given():
    """Every ordinary coding/research job must NOT get Evie's hands tools -- only an explicit
    mcp_servers= call site (the computer-use stuck handoff) does."""
    client = make_client()
    assert not client.options.mcp_servers


async def test_job_runner_start_threads_mcp_servers_into_the_factory_call():
    """JobRunner.start(goal, mcp_servers=...) must reach self._factory(**kw) with mcp_servers in
    kw, not silently drop it -- this is the plumbing brain.py's stuck-task handoff depends on."""
    calls = []

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        async def query(self, prompt):
            pass

        async def receive_response(self):
            return
            yield  # pragma: no cover - makes this an async generator

    def factory(**kw):
        calls.append(kw)
        return _FakeClient()

    async def _noop_event(job, line):
        pass

    async def _noop_done(job):
        pass

    fake_server = {"type": "sdk", "name": "evie_hands"}
    runner = JobRunner(on_event=_noop_event, on_done=_noop_done, client_factory=factory)
    await runner.start("do something on screen", mcp_servers={"evie_hands": fake_server})
    await runner.wait()
    assert calls == [{"mcp_servers": {"evie_hands": fake_server}}]


async def test_job_runner_start_omits_mcp_servers_kwarg_when_not_given():
    """The common case (no mcp_servers passed) must call the factory with the SAME shape as
    before this wiring existed -- no empty mcp_servers key added when nothing was asked for."""
    calls = []

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        async def query(self, prompt):
            pass

        async def receive_response(self):
            return
            yield  # pragma: no cover

    def factory(**kw):
        calls.append(kw)
        return _FakeClient()

    async def _noop_event(job, line):
        pass

    async def _noop_done(job):
        pass

    runner = JobRunner(on_event=_noop_event, on_done=_noop_done, client_factory=factory)
    await runner.start("write some code")
    await runner.wait()
    assert calls == [{}]


def test_job_runner_calls_the_factory_with_no_model_kwarg_when_tier_is_unset():
    """job.tier == "" (pick_tier wasn't wired, or Jev was down) must still reach make_client's
    own model=None -> TIERS['normal'] resolution -- not skip model validation by never calling
    make_client's checks at all. JobRunner._run builds kw from TIERS[job.tier] only when
    job.tier is a real key (jobs.py:262); with tier="" it calls self._factory() with NO model
    kwarg, which is exactly the make_client(model=None) path Step 3 fixed -- this test pins that
    the empty-kw call shape reaches that path, using a lightweight fake factory (not the real
    make_client, which would spawn an actual Claude Code subprocess)."""
    calls = []

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        async def query(self, prompt):
            pass

        async def receive_response(self):
            return
            yield  # pragma: no cover - makes this an async generator

    def factory(**kw):
        calls.append(kw)
        return _FakeClient()

    async def _noop_event(job, line):
        pass

    async def _noop_done(job):
        pass

    async def _drive():
        runner = JobRunner(on_event=_noop_event, on_done=_noop_done, client_factory=factory)
        job = await runner.start("do something simple")
        await runner.wait()
        return job

    job = asyncio.run(_drive())
    assert job.tier == ""  # no pick_tier callable was given to JobRunner -> tier stays unset
    assert calls == [{}]  # confirms the empty-kwarg call shape that make_client(model=None) handles
