"""P2-F design: the computer-task registry (spec §22). brain.py's self._computer_task today is a
bare asyncio.Task | None -- no id, no objective text, no started_at, no current-step, so "actually
use the other file" or "stop that" has nothing to address beyond "the one task, if any, running
right now." This module is the data model and registry logic only: a real ComputerTask record and
a TaskRegistry to start/find/stop/amend one by id. It does NOT replace brain.py's
self._computer_task (that's a live behavior change to code currently running Isaac's assistant,
left for a follow-on session once this registry is reviewed on its own)."""
import time

from evie.computer.task import ComputerTask, TaskRegistry


def test_starting_a_task_gives_it_an_id_and_records_when_it_started():
    reg = TaskRegistry(clock=lambda: 1000.0)
    t = reg.start("open netflix and play the mentalist")
    assert t.task_id and t.objective == "open netflix and play the mentalist"
    assert t.started_at == 1000.0 and t.state == "running" and t.current_step == ""


def test_current_returns_the_most_recently_started_running_task():
    reg = TaskRegistry(clock=time.time)
    reg.start("first task")
    second = reg.start("second task")
    assert reg.current() is not None and reg.current().task_id == second.task_id


def test_starting_a_new_task_marks_the_previous_one_stopped_not_silently_dropped():
    """spec §22: conversational input can modify or STOP the correct task -- starting a new one
    while another runs must leave an auditable trail (the old one's state), not just vanish."""
    reg = TaskRegistry(clock=time.time)
    first = reg.start("first task")
    reg.start("second task")
    assert reg.get(first.task_id).state == "stopped"


def test_stop_by_id_marks_it_stopped_and_clears_current():
    reg = TaskRegistry(clock=time.time)
    t = reg.start("do a thing")
    assert reg.stop(t.task_id) is True
    assert reg.get(t.task_id).state == "stopped"
    assert reg.current() is None


def test_stop_with_no_matching_id_returns_false():
    reg = TaskRegistry(clock=time.time)
    reg.start("do a thing")
    assert reg.stop("not-a-real-id") is False


def test_amend_updates_the_current_tasks_objective_in_place():
    """"Actually, use the other file" -- the SAME task, not a new one, so its history/workspace
    context (whatever a real Planner run tracked) survives the correction."""
    reg = TaskRegistry(clock=time.time)
    t = reg.start("merge the pdf in downloads")
    ok = reg.amend(t.task_id, "actually use the pdf in documents")
    assert ok is True
    assert reg.get(t.task_id).objective == "actually use the pdf in documents"
    assert reg.get(t.task_id).state == "running"  # amending isn't stopping


def test_amend_with_no_matching_id_returns_false():
    reg = TaskRegistry(clock=time.time)
    assert reg.amend("nope", "new objective") is False


def test_set_step_records_progress_for_narration():
    reg = TaskRegistry(clock=time.time)
    t = reg.start("do a multi-step thing")
    reg.set_step(t.task_id, "opening netflix")
    assert reg.get(t.task_id).current_step == "opening netflix"


def test_mark_done_moves_a_task_out_of_current_without_deleting_its_record():
    reg = TaskRegistry(clock=time.time)
    t = reg.start("open netflix")
    reg.mark_done(t.task_id)
    assert reg.get(t.task_id).state == "done"
    assert reg.current() is None


def test_computer_task_is_a_plain_record_matching_spec_22s_field_list():
    t = ComputerTask(task_id="abc", objective="do x", state="running", owner="isaac",
                     workspace="evie_private", started_at=1000.0, current_step="")
    assert t.task_id == "abc" and t.owner == "isaac" and t.workspace == "evie_private"
