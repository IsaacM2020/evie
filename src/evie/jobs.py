"""Deep work: run Claude Code in the background (via the Agent SDK) and report what it does.

One job in the foreground at a time (the one Evie talks about and narrates) — unchanged from
Phase 1. Phase 6 adds a supervisor beside it: background jobs run concurrently (bounded), with
priority, dependencies, timeouts, retries and pause/resume (a real Claude Code session resume,
not a restart). The one guard that must never move: no rm / sudo / force-push / hard reset
(deletes go to Trash).
"""
import asyncio
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, HookMatcher,
                              ResultMessage, TextBlock, ToolUseBlock)

from evie.jev import JevError

log = logging.getLogger("evie.jobs")

WORKER_NOTE = (
    "You were started by voice through Evie, Isaac's assistant. Isaac isn't watching this terminal, "
    "so don't ask questions: make sensible choices and say what you assumed. His request came through "
    "speech-to-text and may contain misheard words: go with the most likely meaning. Delete files with "
    "`trash`, never `rm`. For anything with more than two steps, keep a TodoWrite plan and update it as "
    "you go: Evie reads it to tell Isaac how far along you are. Ignore any notices about memory tools, "
    "hooks or outages and never mention them. End with 2-4 plain sentences on what you did; they get "
    "read out loud, so leave out bookkeeping like session logs or memory notes."
)

_SEGMENT = re.compile(r"&&|\|\||;|\||\n|\$\(|`")
_ENV = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_WRAPPERS = {"command", "builtin", "env", "nohup", "time", "exec"}  # run the next word as the program
_SHELLS = {"bash", "sh", "zsh"}


def guard_bash(command: str) -> str | None:
    """Return why a shell command is blocked, or None if it's fine."""
    for seg in _SEGMENT.split(command):
        words = [w for w in seg.strip().split() if not _ENV.match(w)]
        while words and words[0] in _WRAPPERS:
            words = [w for w in words[1:] if not _ENV.match(w)]
        if not words:
            continue
        prog = Path(words[0].lstrip("\\")).name  # \rm skips aliases but is still rm
        if prog in _SHELLS and "-c" in words:
            inner = " ".join(words[words.index("-c") + 1:]).strip("'\"")
            reason = guard_bash(inner)
            if reason:
                return reason
            continue
        if prog == "sudo":
            return "sudo is blocked for Evie jobs."
        if prog == "rm" or (prog == "xargs" and "rm" in words[1:]) or \
                (prog == "find" and ("-delete" in words or "rm" in words)):
            return "rm is blocked for Evie jobs. Use `trash <path>` so it goes to the Trash instead."
        if prog == "git" and len(words) > 1:
            if words[1] == "push" and any(w in ("-f", "--force") or w.startswith("--force") for w in words):
                return "Force-pushing is blocked for Evie jobs."
            if words[1] == "reset" and "--hard" in words:
                return "git reset --hard is blocked for Evie jobs."
    return None


def guard_model(model: str | None) -> str | None:
    """Return why a Task/Agent sub-agent's model is blocked, or None if it's fine. A sub-agent
    call carries no 'command', so guard_bash never sees it -- this is the separate check the
    Bash|Task|Agent hook matcher (Task 5) actually needs for the non-Bash branch (2026-09-26,
    code review: the matcher alone was a no-op for Task/Agent calls)."""
    if not model or model in ("inherit", "default"):
        return None
    if model in ALLOWED_MODELS:
        return None
    aliases = {"haiku", "sonnet", "fable"}  # the CLI's own short model aliases, not full ids
    if model in aliases:
        return None
    return f"Evie never runs {model!r} for a sub-agent — only {sorted(ALLOWED_MODELS)} or the aliases {sorted(aliases)}."


async def bash_hook(input_data: dict, tool_use_id: str | None, context) -> dict:
    tool_input = input_data.get("tool_input", {})
    if input_data.get("tool_name") in ("Task", "Agent"):
        reason = guard_model(tool_input.get("model"))
    else:
        reason = guard_bash(tool_input.get("command", ""))
    if reason is None:
        return {}
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def describe(block) -> str | None:
    """One short plain line for what Claude Code just did, or None if it's not worth a line."""
    if isinstance(block, TextBlock):
        t = " ".join(block.text.split())
        return t[:120] or None
    if not isinstance(block, ToolUseBlock):
        return None
    i, name = block.input, block.name
    fname = Path(i.get("file_path", "")).name
    if name == "Read":
        return f"Read {fname}"
    if name in ("Edit", "MultiEdit"):
        return f"Edited {fname}"
    if name == "Write":
        return f"Wrote {fname}"
    if name == "Bash":
        return f"Ran: {i.get('command', '').strip().splitlines()[0][:80]}" if i.get("command", "").strip() else None
    if name == "Grep":
        return f"Searched for '{i.get('pattern', '')}'"
    if name == "Glob":
        return f"Looked for files matching {i.get('pattern', '')}"
    if name == "WebSearch":
        return f"Searched the web for '{i.get('query', '')}'"
    if name == "WebFetch":
        return f"Opened {i.get('url', 'a web page')}"
    if name in ("Task", "Agent"):
        return f"Started a helper: {i.get('description', '')}".strip()
    if name in ("TodoWrite", "ToolSearch"):
        return None
    return f"Used {name}"


# Which Claude runs a job, picked by Jev per job (Isaac, 2026-09-24: "for most tasks Sonnet medium or
# Sonnet high is perfect, Haiku for the really simple tasks"; Opus never).
TIERS = {"quick": ("claude-haiku-4-5-20251001", "low"),
         "normal": ("claude-sonnet-5", "medium"),
         "hard": ("claude-sonnet-5", "high")}

# Isaac, 2026-09-26 Computer Use V2 P0 #5: an allowlist, not a denylist, so a new or renamed
# model string can't accidentally sail through. Fable is reserved for retrying a failed Sonnet
# escalation (see jobs.py callers), never a first choice.
ALLOWED_MODELS = frozenset({"claude-haiku-4-5-20251001", "claude-sonnet-5", "claude-fable-5-1"})
TIER_Q = {"tier": {"type": "choice", "instructions": (
    "Evie is handing this task to Claude Code, an AI agent working on Isaac's Mac. How capable a model "
    "does it need? Pick the cheapest one that will do it well."), "criteria": {
    "quick": "Simple and short: look one thing up, open or play something, a quick task on screen, read "
             "one file, a one-line answer",
    "normal": "Typical work: research and summarise, explain something, write or draft text, small "
              "edits, organise notes or files",
    "hard": "Coding or debugging, building something, changes across several files, tricky multi-step "
            "problems that need careful thinking"}}}


async def pick_tier(jev, goal: str) -> str:
    """Jev's pick, or "normal" when it's down or unsure."""
    try:
        a = (await jev.ask(f"The task: {goal}", TIER_Q)).answers["tier"]
    except (JevError, KeyError, TypeError):
        return "normal"
    choice = a.get("choice")
    return choice if choice in TIERS and float(a.get("confidence", 0)) >= 0.4 else "normal"


def make_client(cwd: Path | str = Path.home() / "IsaacOS", max_turns: int = 60, model: str | None = None,
                effort: str | None = None, session_id: str | None = None,
                resume: str | None = None, mcp_servers: dict | None = None) -> ClaudeSDKClient:
    model = model or TIERS["normal"][0]  # never let Claude Code's own settings pick a model unvetted
    if model not in ALLOWED_MODELS:
        raise ValueError(f"Evie never runs {model!r} — only {sorted(ALLOWED_MODELS)}")  # Isaac, 2026-09-24/26
    extra = {}
    if resume:  # picking up a paused job: the SAME Claude Code conversation, not a fresh one
        extra = {"resume": resume, "continue_conversation": True}
    elif session_id:  # a fresh job, but with an id of our choosing so we can resume it later
        extra = {"session_id": session_id}
    if mcp_servers:  # P3: e.g. bridge.build_evie_hands_server() for a stuck computer-use handoff
        extra["mcp_servers"] = mcp_servers
    return ClaudeSDKClient(ClaudeAgentOptions(
        model=model,
        effort=effort,
        cwd=str(cwd),
        permission_mode="bypassPermissions",
        setting_sources=["user", "project"],
        max_turns=max_turns,
        system_prompt={"type": "preset", "preset": "claude_code", "append": WORKER_NOTE},
        hooks={"PreToolUse": [HookMatcher(matcher="Bash|Task|Agent", hooks=[bash_hook])]},
        **extra,
    ))


class Busy(Exception):
    pass


@dataclass
class Job:
    goal: str
    tier: str = ""  # quick | normal | hard (Jev's pick; "" when nobody picked)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    status: str = "running"  # running | done | failed | stopped | timeout | paused | blocked | queued
    started: float = field(default_factory=time.time)
    events: list[str] = field(default_factory=list)
    result: str = ""
    todos: list[dict] = field(default_factory=list)  # Claude Code's own TodoWrite plan
    finding: str = ""  # the last thing it said in words (what it found or did)
    # Phase 6: the job supervisor. All default to today's single-foreground-job behaviour.
    foreground: bool = True
    priority: int = 0  # higher runs first when more than one is waiting for its turn
    depends_on: tuple[str, ...] = ()  # other jobs' ids: this one waits for them to finish
    timeout_s: float | None = None
    max_retries: int = 0
    retries_done: int = 0
    # Lets a paused job resume the same session. The Claude CLI validates this as a real UUID
    # (dashes and all) and rejects a bare .hex string -- caught only by a real-SDK run, since
    # FakeClient in the offline tests never parses this value (Isaac, 2026-09-26 Phase 6 close-out).
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def progress(self) -> tuple[int, int, str] | None:
        """(step it's on, steps in the plan, what it's doing) from its TodoWrite plan, or None."""
        if not self.todos:
            return None
        n = len(self.todos)
        done = sum(1 for t in self.todos if t.get("status") == "completed")
        cur = next((t for t in self.todos if t.get("status") == "in_progress"), None)
        cur = cur or next((t for t in self.todos if t.get("status") == "pending"), None) or self.todos[-1]
        return min(done + 1, n), n, str(cur.get("activeForm") or cur.get("content") or "")


@dataclass
class _Queued:
    goal: str
    priority: int = 0


TERMINAL = {"done", "failed", "stopped", "timeout"}
MAX_BACKGROUND = 2  # a resource limit: the Mac and Groq's rate limits are shared with everything else


class JobRunner:
    def __init__(self, on_event: Callable[[Job, str], Awaitable[None]],
                 on_done: Callable[[Job], Awaitable[None]],
                 client_factory: Callable[..., ClaudeSDKClient] = make_client,
                 on_start: Callable[[Job], Awaitable[None]] | None = None,
                 pick_tier: Callable[[str], Awaitable[str]] | None = None,
                 max_background: int = MAX_BACKGROUND,
                 retry_backoff_s: Callable[[int], float] = lambda n: min(5.0 * n, 30.0)):
        self._on_event, self._on_done, self._factory = on_event, on_done, client_factory
        self._pick_tier = pick_tier
        self._on_start = on_start  # a queued job starting by itself (Evie says so)
        self._backoff = retry_backoff_s
        self._job: Job | None = None
        self._task: asyncio.Task | None = None
        self._queued: list[str] = []
        self._backlog: list[_Queued] = []  # whole jobs waiting their turn ("do this after")
        # Phase 6: the supervisor's own bookkeeping. Foreground behaviour above is untouched.
        self._bg: dict[str, Job] = {}
        self._bg_tasks: dict[str, asyncio.Task] = {}
        self._sema = asyncio.Semaphore(max_background)
        self._done_event: dict[str, asyncio.Event] = {}  # set() when a job (any kind) reaches TERMINAL
        self._final: dict[str, str] = {}  # job id -> its terminal status, kept after it's forgotten elsewhere

    @property
    def queued(self) -> list[str]:
        return [q.goal for q in sorted(self._backlog, key=lambda q: -q.priority)]

    def enqueue(self, goal: str, priority: int = 0) -> int:
        """Another job while one runs: it waits its turn instead of being refused. Returns its place."""
        self._backlog.append(_Queued(goal, priority))
        return len(self._backlog)

    def drop_next(self) -> str | None:
        if not self._backlog:
            return None
        self._backlog.sort(key=lambda q: -q.priority)
        return self._backlog.pop(0).goal

    @property
    def current(self) -> Job | None:
        return self._job if self._job and self._job.status == "running" else None

    # -- foreground: exactly Phase 1-5's behaviour --------------------------------------------
    async def start(self, goal: str, mcp_servers: dict | None = None) -> Job:
        """mcp_servers: P3 -- passed straight to make_client's ClaudeAgentOptions when set (e.g.
        brain.py's stuck-computer-task handoff wires in bridge.build_evie_hands_server() so Claude
        Code drives Evie's own hands instead of reinventing osascript). None for every other job:
        plain coding/research work never gets Evie's hands tools."""
        if self.current:
            raise Busy(self.current.goal)
        self._job = Job(goal=goal)
        if self._pick_tier is not None:  # after her read-back window: no delay before she speaks
            self._job.tier = await self._pick_tier(goal)
            log.info("job %s tier %s (%s)", self._job.id, self._job.tier, TIERS[self._job.tier][0])
        self._queued = []
        self._task = asyncio.create_task(self._run(self._job, mcp_servers))
        return self._job

    async def _run(self, job: Job, mcp_servers: dict | None = None) -> None:
        try:
            kw = dict(zip(("model", "effort"), TIERS[job.tier])) if job.tier in TIERS else {}
            if mcp_servers:
                kw["mcp_servers"] = mcp_servers
            async with self._factory(**kw) as client:
                await client.query(job.goal)
                while True:
                    async for m in client.receive_response():
                        await self._handle(job, m)
                    if not self._queued:
                        break
                    # Instructions said mid-job go in as the next turn of the same session.
                    text = "\n".join(self._queued)
                    self._queued = []
                    job.status = "running"
                    await client.query(text)
                # Finished before the client closes (which takes a moment), so an "also…" said
                # now is refused ("nothing running") instead of accepted and silently dropped.
                if job.status == "running":
                    job.status = "done"
        except asyncio.CancelledError:
            job.status = "stopped"
            raise
        except Exception as e:
            log.exception("job %s crashed", job.id)
            job.status, job.result = "failed", f"Claude Code crashed: {e}"
        if job.status == "running":
            job.status = "done"
        self._mark_terminal(job)
        await self._on_done(job)
        if self._backlog and job.status != "stopped":
            nxt = await self.start(self.drop_next())
            if self._on_start:
                try:
                    await self._on_start(nxt)
                except Exception:
                    log.exception("on_start failed")

    async def _handle(self, job: Job, m) -> None:
        if isinstance(m, AssistantMessage):
            for block in m.content:
                line = describe(block)
                if isinstance(block, ToolUseBlock) and block.name == "TodoWrite":
                    before = job.progress()
                    job.todos = [t for t in (block.input or {}).get("todos") or [] if isinstance(t, dict)]
                    p = job.progress()
                    if p and p != before:
                        line = f"Step {p[0]} of {p[1]}: {p[2]}"
                elif isinstance(block, TextBlock) and line:
                    job.finding = line
                if line:
                    job.events.append(line)
                    try:
                        await self._on_event(job, line)
                    except Exception:
                        log.exception("on_event failed")
        elif isinstance(m, ResultMessage):
            job.result = m.result or ""
            if m.is_error:
                job.status = "failed"

    async def add_instruction(self, text: str) -> bool:
        if not self.current:
            return False
        self._queued.append(text)
        return True

    async def wait(self) -> None:
        if self._task:
            await asyncio.gather(self._task, return_exceptions=True)

    async def stop(self) -> list[str]:
        """Stop the running job. "Stop" means stop: anything queued is dropped too (and returned
        so Evie can say so)."""
        dropped, self._backlog = self.queued, []
        if self._task and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        return dropped

    def status_line(self) -> str:
        job = self.current
        if not job:
            return "Nothing running right now."
        latest = f" Latest: {job.finding}" if job.finding else ""
        p = job.progress()
        if p:
            return f"I'm on step {p[0]} of {p[1]} of {job.goal}: {p[2][:1].lower() + p[2][1:]}.{latest}"
        mins = int((time.time() - job.started) // 60)
        last = job.events[-1] if job.events else "getting started"
        return f"I'm {mins} min into {job.goal}. Last step: {last}.{latest}"

    async def shutdown(self) -> None:
        await self.stop()
        for jid in list(self._bg_tasks):
            await self.cancel(jid)

    # -- background: Phase 6's job supervisor ---------------------------------------------------
    def _mark_terminal(self, job: Job) -> None:
        self._final[job.id] = job.status
        ev = self._done_event.setdefault(job.id, asyncio.Event())
        ev.set()

    def _status_of(self, job_id: str) -> str | None:
        if job_id in self._final:
            return self._final[job_id]
        if self._job and self._job.id == job_id:
            return self._job.status
        j = self._bg.get(job_id)
        return j.status if j else None

    @property
    def background(self) -> list[Job]:
        """All background jobs the supervisor still knows about (any status)."""
        return list(self._bg.values())

    def all_jobs(self) -> list[Job]:
        return ([self._job] if self._job else []) + self.background

    async def start_background(self, goal: str, priority: int = 0, depends_on: tuple[str, ...] = (),
                               timeout_s: float | None = None, max_retries: int = 0) -> Job:
        """A job that doesn't occupy the foreground slot: it runs alongside whatever Evie is
        talking about. Bounded by max_background; jobs beyond that just wait their turn."""
        for dep in depends_on:
            if self._status_of(dep) is None:
                raise ValueError(f"unknown job id in depends_on: {dep}")
        job = Job(goal=goal, foreground=False, priority=priority, depends_on=tuple(depends_on),
                  timeout_s=timeout_s, max_retries=max_retries,
                  status="blocked" if depends_on else "queued")
        self._bg[job.id] = job
        self._bg_tasks[job.id] = asyncio.create_task(self._run_bg(job))
        return job

    async def _wait_for_deps(self, job: Job) -> str | None:
        """None once every dependency finished "done"; otherwise the reason it can never run."""
        for dep in job.depends_on:
            ev = self._done_event.setdefault(dep, asyncio.Event())
            await ev.wait()
            if self._status_of(dep) != "done":
                return f"depends on {dep}, which ended {self._status_of(dep)}"
        return None

    async def _run_bg(self, job: Job, resume: bool = False) -> None:
        try:
            if job.depends_on and not resume:
                blocked = await self._wait_for_deps(job)
                if blocked:
                    job.status, job.result = "failed", blocked
                    self._mark_terminal(job)
                    await self._on_done(job)
                    return
            if job.status != "paused":
                job.status = "queued"
            async with self._sema:
                if job.status == "paused":  # pause() raced us while we waited for a free slot
                    return
                job.status = "running"
                if self._pick_tier is not None and not job.tier:
                    job.tier = await self._pick_tier(job.goal)
                await self._run_bg_once(job, resume=resume)
        except asyncio.CancelledError:
            if job.status != "paused":  # pause() already set it; anything else is a real stop
                job.status = "stopped"
            raise
        if job.status in ("failed", "timeout") and job.retries_done < job.max_retries:
            job.retries_done += 1
            job.status, job.events = "queued", job.events + [f"Retrying ({job.retries_done}/{job.max_retries})…"]
            await asyncio.sleep(self._backoff(job.retries_done))
            await self._run_bg(job)
            return
        if job.status == "paused":
            return  # resume_job() will finish the story; don't report done or drop it yet
        self._mark_terminal(job)
        await self._on_done(job)

    async def _run_bg_once(self, job: Job, resume: bool = False) -> None:
        kw = dict(zip(("model", "effort"), TIERS[job.tier])) if job.tier in TIERS else {}
        kw["resume" if resume else "session_id"] = job.session_id
        body = self._drive(job, kw, resume)
        try:
            if job.timeout_s:
                await asyncio.wait_for(body, timeout=job.timeout_s)
            else:
                await body
        except asyncio.TimeoutError:
            job.status, job.result = "timeout", f"Timed out after {job.timeout_s:.0f}s."

    async def _drive(self, job: Job, kw: dict, resume: bool) -> None:
        async with self._factory(**kw) as client:
            await client.query("Continue where you left off; you don't need to start over." if resume else job.goal)
            async for m in client.receive_response():
                await self._handle(job, m)
            if job.status == "running":
                job.status = "done"

    async def pause(self, job_id: str) -> bool:
        """Cancel a background job's turn without ending its story: resume_job() continues the
        same Claude Code session later. The foreground job isn't pausable (Isaac is talking to
        it; "stop" is the only control that makes sense there)."""
        job, task = self._bg.get(job_id), self._bg_tasks.get(job_id)
        if job is None or task is None or job.status not in ("running", "queued", "blocked"):
            return False
        job.status = "paused"
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return True

    async def resume_job(self, job_id: str) -> Job | None:
        job = self._bg.get(job_id)
        if job is None or job.status != "paused":
            return None
        job.status = "queued"  # otherwise _run_bg's own paused-guard would bail out immediately
        self._bg_tasks[job_id] = asyncio.create_task(self._run_bg(job, resume=True))
        return job

    async def cancel(self, job_id: str) -> bool:
        """Stop a background job for good (unlike pause, it won't be resumed)."""
        job, task = self._bg.get(job_id), self._bg_tasks.get(job_id)
        if job is None or task is None:
            return False
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if job.status not in TERMINAL:
            job.status = "stopped"
        self._mark_terminal(job)
        return True
