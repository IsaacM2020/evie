from datetime import date, datetime

import pytest

from evie.calendar_store import TZ, CalendarStore, CalEvent
from evie.context_packs import Packs, ProjectIndex, named_days, rails
from evie.remember import Task

TODAY = date(2026, 9, 24)  # a Thursday


@pytest.mark.parametrize("text,want", [
    ("am i free at 5", {"calendar"}),
    ("what's on friday", {"calendar"}),
    ("what do i have tomorrow", {"calendar"}),
    ("what's on my to do list", {"tasks"}),
    ("anything due today", {"calendar", "tasks"}),
    ("summarise this page", {"screen"}),
    ("what's the weather like tomorrow", {"calendar", "web"}),
    ("who won the ipl final", {"web"}),
    ("tell me a joke", set()),
])
def test_rails_force_the_obvious_packs(text, want):
    assert rails(text) == want


@pytest.mark.parametrize("text,days", [
    ("what's on the 14th of october", [date(2026, 10, 14)]),
    ("anything on october 14", [date(2026, 10, 14)]),
    ("what about 2 nov", [date(2026, 11, 2)]),
    ("what's on the 30th", [date(2026, 9, 30)]),
    ("what's on the 3rd", [date(2026, 10, 3)]),  # the 3rd already passed this month
    ("what's on friday", [date(2026, 9, 25)]),
    ("what's on next week monday", [date(2026, 9, 28)]),
    ("anything in january on the 5th", [date(2027, 1, 5)]),
    ("how are you", []),
])
def test_named_days(text, days):
    assert named_days(text, TODAY) == days


def at(d, h, m=0):
    return datetime(d.year, d.month, d.day, h, m, tzinfo=TZ)


class FakeHands:
    def __init__(self):
        self.calls = []

    async def do(self, op, timeout=5.0, **args):
        from evie.hands import HandsResult
        self.calls.append((op, args))
        return HandsResult(True, "", {"events": '[{"id":"x","title":"Chem test","start":"2026-10-14T01:00:00Z",'
                                                '"end":"2026-10-14T02:00:00Z","all_day":false,"calendar":"Isaac"}]'})


class FakeTodoist:
    async def list(self, query="today | overdue"):
        return [Task("1", "Email bio teacher", "today"), Task("2", "Maths practice", None)]


class FakeWeb:
    def __init__(self):
        self.asked = []

    async def search(self, question):
        self.asked.append(question)
        return "RCB won the 2026 IPL final on 31 May."


def packs(tmp_path, **kw):
    cal = CalendarStore()
    cal.update([CalEvent("School", at(TODAY, 8), at(TODAY, 15, 30), False, "Isaac")], at=at(TODAY, 7))
    idx = ProjectIndex(tmp_path)
    return Packs(cal, FakeHands(), FakeTodoist(), projects=idx, web=kw.get("web") or FakeWeb(),
                 screen=lambda: {"front_app": "Safari", "window": "IB Physics - Kinematics notes"},
                 today=lambda: TODAY)


async def test_far_named_day_is_looked_up(tmp_path):
    p = packs(tmp_path)
    facts = await p.gather({"calendar"}, "what's on the 14th of october")
    assert facts["calendar_wednesday_14_oct"] == "Wednesday 14 Oct: 9:00-10:00 Chem test"


async def test_tasks_pack(tmp_path):
    facts = await packs(tmp_path).gather({"tasks"}, "what's due")
    assert facts["todoist"] == "Email bio teacher (due today); Maths practice"


async def test_screen_pack(tmp_path):
    facts = await packs(tmp_path).gather({"screen"}, "what am i looking at")
    assert facts["screen"] == "Front app: Safari. Window: IB Physics - Kinematics notes"


async def test_web_pack_asks_the_search_model(tmp_path):
    web = FakeWeb()
    facts = await packs(tmp_path, web=web).gather({"web"}, "who won the ipl final")
    assert web.asked == ["who won the ipl final"] and facts["web"].startswith("RCB")


async def test_projects_pack_has_the_brief_and_matching_snippets(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# Isaac\nIsaac is 16 and builds AI tools.\n")
    (tmp_path / "projects" / "cricket").mkdir(parents=True)
    (tmp_path / "projects" / "cricket" / "roadmap.md").write_text(
        "# Roadmap\n\nWeek 3: build the chase win predictor with ball-by-ball data.\n\nOther stuff here.\n")
    (tmp_path / "projects" / "session-log.md").write_text("2026-09-23 | Build | Evie calendar fixes\n")
    facts = await packs(tmp_path).gather({"projects"}, "how's my chase win predictor going")
    assert "builds AI tools" in facts["isaac_brief"] and "Evie calendar fixes" in facts["isaac_brief"]
    assert "chase win predictor" in facts["isaac_files"] and "cricket/roadmap.md" in facts["isaac_files"]


async def test_a_broken_pack_never_breaks_the_answer(tmp_path):
    class Boom:
        async def list(self, query=""):
            raise RuntimeError("todoist down")

    p = packs(tmp_path)
    p._todoist = Boom()
    facts = await p.gather({"tasks"}, "what's due")
    assert facts == {"todoist": "couldn't check Todoist just now"}
