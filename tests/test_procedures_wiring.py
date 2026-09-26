"""Phase 6 P1: Recipes + Planner + ProcedureStore wired together. Planner.run()'s own expect
checks are what make reuse safe; this file proves Recipes calls it with cached steps only once a
procedure has proven itself twice, and backs off the moment one starts failing."""
import time

from evie.computer.planner import Outcome
from evie.computer.recipes import Recipes
from evie.procedures import ProcedureStore

GOAL = "play a video by mrbeast"


class FakePlanner:
    def __init__(self, results=None):
        self.calls: list[tuple[str, list | None]] = []
        self._results = list(results) if results is not None else None
        self.last_steps: list[dict] = []

    async def run(self, goal, app=None, steps=None):
        self.calls.append((goal, steps))
        self.last_steps = steps if steps is not None else [{"do": "fresh", "n": len(self.calls)}]
        return self._results.pop(0) if self._results else Outcome(True, f"did: {goal}")


def rec(planner, procedures=None):
    return Recipes(hands=None, jev=None, talker=None, planner=planner, procedures=procedures)


def mem_store() -> ProcedureStore:
    """An in-memory-only ProcedureStore: same class and logic, no disk I/O in the test."""
    s = ProcedureStore.__new__(ProcedureStore)
    s._procs = {}
    s._clock = time.time
    s._max = 200
    s.save = lambda: None
    return s


async def test_first_success_plans_fresh_and_learns_but_does_not_reuse_yet():
    procs, planner = mem_store(), FakePlanner()
    out = await rec(planner, procs).run(GOAL)
    assert out.ok and planner.calls == [(GOAL, None)]
    learned = procs.find_any(GOAL)
    assert learned is not None and learned.status == "learning" and learned.success_count == 1


async def test_second_success_promotes_it_and_third_call_reuses_cached_steps():
    procs, planner = mem_store(), FakePlanner()
    r = rec(planner, procs)
    await r.run(GOAL)
    await r.run(GOAL)
    assert planner.calls == [(GOAL, None), (GOAL, None)]  # both planned fresh
    proc = procs.find(GOAL)
    assert proc is not None and proc.status == "active"
    await r.run(GOAL)
    assert planner.calls[-1] == (GOAL, proc.steps)  # third time: cached steps handed to Planner


async def test_a_failure_on_a_cached_procedure_is_recorded_but_does_not_retire_it_yet():
    procs = mem_store()
    procs.learn(GOAL, [{"do": "a"}])
    proc = procs.learn(GOAL, [{"do": "a"}])
    assert proc.status == "active"
    planner = FakePlanner(results=[Outcome(False, "stuck", stuck=True)])
    out = await rec(planner, procs).run(GOAL)
    assert not out.ok
    assert planner.calls == [(GOAL, proc.steps)]  # it WAS tried, and checked, before failing
    assert procs.get(proc.id).status == "active" and procs.get(proc.id).failure_count == 1


async def test_two_consecutive_failures_retire_it_and_the_next_call_plans_fresh_again():
    procs = mem_store()
    procs.learn(GOAL, [{"do": "a"}])
    proc = procs.learn(GOAL, [{"do": "a"}])
    planner = FakePlanner(results=[Outcome(False, "stuck", stuck=True), Outcome(False, "stuck", stuck=True)])
    r = rec(planner, procs)
    await r.run(GOAL)
    await r.run(GOAL)
    assert procs.get(proc.id).status == "retired"
    assert procs.find(GOAL) is None
    planner2 = FakePlanner()
    await rec(planner2, procs).run(GOAL)
    assert planner2.calls == [(GOAL, None)]  # retired: plans fresh, doesn't reuse the dead one


async def test_without_a_procedures_store_behaviour_is_exactly_the_old_planner_call():
    planner = FakePlanner()
    out = await rec(planner, procedures=None).run(GOAL)
    assert out.ok and planner.calls == [(GOAL, None)]
