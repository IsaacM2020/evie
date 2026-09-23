import asyncio

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock

from evie.jobs import Busy, JobRunner, bash_hook, describe, guard_bash


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
    assert r.status_line().startswith("Working on: fix the chase bug (")
    assert "last: Ran: pytest" in r.status_line()
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
