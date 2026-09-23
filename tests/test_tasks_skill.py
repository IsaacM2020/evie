from evie.jev import JevResult
from evie.remember import Task
from evie.skills.tasks import TaskSkills
from evie.skills.timers import Timer, done_line


class FakeTodoist:
    def __init__(self, tasks=None):
        self.tasks = tasks if tasks is not None else [Task("1", "Email bio teacher", "tomorrow"),
                                                       Task("2", "Maths practice", None)]
        self.closed, self.reopened = [], []

    async def list(self, query="today | overdue"):
        return self.tasks

    async def close(self, tid):
        self.closed.append(tid)
        return True

    async def reopen(self, tid):
        self.reopened.append(tid)
        return True


class FakeJev:
    def __init__(self, choice="1", conf=0.9):
        self.choice, self.conf, self.options = choice, conf, None

    async def ask(self, state, questions):
        self.options = questions["task"]["criteria"]
        return JevResult({"task": {"choice": self.choice, "confidence": self.conf}}, 200.0, 0.0)


class Undo:
    def __init__(self):
        self.fns = []

    def remember_undo(self, fn):
        self.fns.append(fn)


async def test_done_ticks_off_the_task_jev_picked_from_the_real_list():
    td, jev, u = FakeTodoist(), FakeJev(), Undo()
    done = await TaskSkills(td, jev, u).done("I emailed my bio teacher")
    assert jev.options == {"1": "Email bio teacher (due tomorrow)", "2": "Maths practice", "none": "None of these tasks"}
    assert td.closed == ["1"] and done.said == "Ticked off: Email bio teacher."
    assert await u.fns[0]() == "Put Email bio teacher back on your list." and td.reopened == ["1"]


async def test_done_with_no_match_says_so():
    td = FakeTodoist()
    done = await TaskSkills(td, FakeJev(choice="none"), Undo()).done("I did the dishes")
    assert td.closed == [] and done.said == "That's not on your Todoist for today."


async def test_done_with_an_empty_list():
    done = await TaskSkills(FakeTodoist(tasks=[]), FakeJev(), Undo()).done("finished it")
    assert done.said == "Nothing's due on your Todoist right now."


def test_timer_and_reminder_lines():
    assert done_line(Timer("a", 600, 0)) == "Your 10 minute timer's done."
    assert done_line(Timer("b", 30, 0, label="eat a banana")) == "Reminder: eat a banana."
