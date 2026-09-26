"""P2-F design: the computer-task registry (spec §22). brain.py's self._computer_task is
currently a bare asyncio.Task | None -- when a plan is running there is no way to address it by
id, so "actually use the other file" or "stop that" can only ever mean "the one task, if any,
running right now." This module gives a computer task a real identity (task_id, objective, state,
owner, workspace, started_at, current_step, matching the spec's own field list) and a registry to
start/find/stop/amend one -- so a later wiring session can let conversational input target the
correct task even once more than one can exist (background computer work, not just background
Claude Code jobs, which JobRunner in jobs.py already supports this way).

This is the data model and registry logic only. It does not replace brain.py's self._computer_task
or asyncio.Task usage -- that is a live behavior change to code currently running Isaac's
assistant and needs its own review once this module has landed on its own (see the plan's
Deferred Work).
"""
import uuid
from dataclasses import dataclass
from typing import Callable


@dataclass
class ComputerTask:
    task_id: str
    objective: str
    state: str  # running | stopped | done
    owner: str = "isaac"
    workspace: str = "evie_private"
    started_at: float = 0.0
    current_step: str = ""


class TaskRegistry:
    def __init__(self, clock: Callable[[], float]):
        self._clock = clock
        self._tasks: dict[str, ComputerTask] = {}
        self._current_id: str | None = None

    def start(self, objective: str, owner: str = "isaac", workspace: str = "evie_private") -> ComputerTask:
        """Starting a new task stops whatever was running before -- an auditable state change,
        not a silent drop (spec §22: conversational input can modify or stop the correct task)."""
        if self._current_id is not None:
            prev = self._tasks.get(self._current_id)
            if prev is not None and prev.state == "running":
                prev.state = "stopped"
        task = ComputerTask(task_id=uuid.uuid4().hex[:8], objective=objective, state="running",
                            owner=owner, workspace=workspace, started_at=self._clock())
        self._tasks[task.task_id] = task
        self._current_id = task.task_id
        return task

    def current(self) -> ComputerTask | None:
        if self._current_id is None:
            return None
        t = self._tasks.get(self._current_id)
        return t if t is not None and t.state == "running" else None

    def get(self, task_id: str) -> ComputerTask | None:
        return self._tasks.get(task_id)

    def stop(self, task_id: str) -> bool:
        t = self._tasks.get(task_id)
        if t is None:
            return False
        t.state = "stopped"
        if self._current_id == task_id:
            self._current_id = None
        return True

    def amend(self, task_id: str, new_objective: str) -> bool:
        """"Actually, use the other file" -- updates the SAME task's objective in place rather
        than starting an unrelated one."""
        t = self._tasks.get(task_id)
        if t is None:
            return False
        t.objective = new_objective
        return True

    def set_step(self, task_id: str, step: str) -> bool:
        t = self._tasks.get(task_id)
        if t is None:
            return False
        t.current_step = step
        return True

    def mark_done(self, task_id: str) -> bool:
        t = self._tasks.get(task_id)
        if t is None:
            return False
        t.state = "done"
        if self._current_id == task_id:
            self._current_id = None
        return True
