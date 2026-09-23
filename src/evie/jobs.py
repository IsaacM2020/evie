"""Deep work: run Claude Code in the background (via the Agent SDK) and report what it does.

One job at a time. Claude Code runs in ~/IsaacOS with Isaac's CLAUDE.md, skills and memory,
full permissions, and one guard: no rm / sudo / force-push / hard reset (deletes go to Trash).
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

log = logging.getLogger("evie.jobs")

WORKER_NOTE = (
    "You were started by voice through Evie, Isaac's assistant. Isaac isn't watching this terminal, "
    "so don't ask questions: make sensible choices and say what you assumed. Delete files with "
    "`trash`, never `rm`. End with 2-4 plain sentences on what you did; they get read out loud, so "
    "leave out bookkeeping like session logs or memory notes."
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


async def bash_hook(input_data: dict, tool_use_id: str | None, context) -> dict:
    reason = guard_bash(input_data.get("tool_input", {}).get("command", ""))
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


def make_client(cwd: Path | str = Path.home() / "IsaacOS", max_turns: int = 60) -> ClaudeSDKClient:
    return ClaudeSDKClient(ClaudeAgentOptions(
        cwd=str(cwd),
        permission_mode="bypassPermissions",
        setting_sources=["user", "project"],
        max_turns=max_turns,
        system_prompt={"type": "preset", "preset": "claude_code", "append": WORKER_NOTE},
        hooks={"PreToolUse": [HookMatcher(matcher="Bash", hooks=[bash_hook])]},
    ))


class Busy(Exception):
    pass


@dataclass
class Job:
    goal: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    status: str = "running"  # running | done | failed | stopped
    started: float = field(default_factory=time.time)
    events: list[str] = field(default_factory=list)
    result: str = ""


class JobRunner:
    def __init__(self, on_event: Callable[[Job, str], Awaitable[None]],
                 on_done: Callable[[Job], Awaitable[None]],
                 client_factory: Callable[[], ClaudeSDKClient] = make_client,
                 on_start: Callable[[Job], Awaitable[None]] | None = None):
        self._on_event, self._on_done, self._factory = on_event, on_done, client_factory
        self._on_start = on_start  # a queued job starting by itself (Evie says so)
        self._job: Job | None = None
        self._task: asyncio.Task | None = None
        self._queued: list[str] = []
        self._backlog: list[str] = []  # whole jobs waiting their turn ("do this after")

    @property
    def queued(self) -> list[str]:
        return list(self._backlog)

    def enqueue(self, goal: str) -> int:
        """Another job while one runs: it waits its turn instead of being refused. Returns its place."""
        self._backlog.append(goal)
        return len(self._backlog)

    def drop_next(self) -> str | None:
        return self._backlog.pop(0) if self._backlog else None

    @property
    def current(self) -> Job | None:
        return self._job if self._job and self._job.status == "running" else None

    async def start(self, goal: str) -> Job:
        if self.current:
            raise Busy(self.current.goal)
        self._job = Job(goal=goal)
        self._queued = []
        self._task = asyncio.create_task(self._run(self._job))
        return self._job

    async def _run(self, job: Job) -> None:
        try:
            async with self._factory() as client:
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
        await self._on_done(job)
        if self._backlog and job.status != "stopped":
            nxt = await self.start(self._backlog.pop(0))
            if self._on_start:
                try:
                    await self._on_start(nxt)
                except Exception:
                    log.exception("on_start failed")

    async def _handle(self, job: Job, m) -> None:
        if isinstance(m, AssistantMessage):
            for block in m.content:
                line = describe(block)
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
        dropped, self._backlog = self._backlog, []
        if self._task and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        return dropped

    def status_line(self) -> str:
        job = self.current
        if not job:
            return "Nothing running right now."
        mins = int((time.time() - job.started) // 60)
        last = job.events[-1] if job.events else "getting started"
        return f"Working on: {job.goal} ({mins} min, last: {last})"

    async def shutdown(self) -> None:
        await self.stop()
