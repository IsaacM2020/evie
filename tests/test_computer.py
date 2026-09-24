import json

from evie.computer.observe import Screen
from evie.computer.recipes import Recipes
from evie.computer.safety import is_risky, risky_words
from evie.hands import HandsResult
from evie.jev import JevResult

YT = [{"id": "w1", "role": "input:text", "label": "Search", "typeable": True, "region": "header"},
      {"id": "w5", "role": "link", "label": "I Spent 7 Days Buried Alive", "href": "https://www.youtube.com/watch?v=abc",
       "region": "main", "onscreen": True},
      {"id": "w6", "role": "link", "label": "MrBeast", "href": "https://www.youtube.com/@MrBeast", "region": "main"}]


def obs(elements, url="https://www.youtube.com/results?search_query=mrbeast", app="Safari", kind="web", snap="s1"):
    return HandsResult(True, "", {"snapshot": snap, "app": app, "kind": kind, "url": url, "window": "YouTube",
                                  "elements": json.dumps(elements)})


class FakeHands:
    """Scripted: each op returns the next result queued for it (or ok)."""

    def __init__(self, script=None):
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.calls = []

    async def do(self, op, timeout=5.0, **args):
        self.calls.append((op, args))
        q = self.script.get(op)
        if q:
            return q.pop(0) if len(q) > 1 else q[0]
        return HandsResult(True, "ok", {})


def test_screen_compact_lists_real_ids_only():
    s = Screen.from_data(obs(YT).data)
    text = s.compact()
    assert s.ids == {"w1", "w5", "w6"}
    assert 'w5 link "I Spent 7 Days Buried Alive" (main) -> www.youtube.com/watch' in text
    assert text.startswith("App: Safari | Page: YouTube")


def test_safety_rail_catches_send_like_steps():
    assert risky_words("Send") and risky_words("Place order") and risky_words("Delete chat")
    assert not risky_words("Search") and not risky_words("Play")
    assert is_risky("press", {"label": "Send"})
    assert is_risky("set_text", {"label": "Type a message"}, "on my way")
    assert not is_risky("press", {"label": "Next video"})
    assert is_risky("press", {"label": "Next"}, flagged=True)


class FakeJev:
    def __init__(self, choice, conf=0.9):
        self.choice, self.conf = choice, conf

    async def ask(self, state, questions):
        key = next(iter(questions))
        return JevResult({key: {"choice": self.choice, "confidence": self.conf}}, 200.0, 0.0)


class FakeTalker:
    def __init__(self, out):
        self.out = out

    async def extract(self, instructions, text):
        return self.out


def test_compact_screen_shows_card_details_and_selected_tabs():
    els = [{"id": "w1", "role": "tab", "label": "Videos", "selected": True, "region": "main"},
           {"id": "w2", "role": "link", "label": "I hacked my own network", "href": "https://www.youtube.com/watch?v=n1",
            "meta": "412K views 2 days ago", "group": "c1", "region": "main"}]
    text = Screen.from_data(obs(els).data).compact()
    assert 'w1 tab "Videos" (main) [selected]' in text
    assert "412K views 2 days ago" in text


async def test_new_tab_is_instant_with_no_model_at_all():
    hands = FakeHands()
    r = await Recipes(hands, None, None, planner=None).run("Evie, open a new tab")
    assert hands.calls == [("activate", {"app": "Safari"}), ("key", {"combo": "cmd+t", "app": "Safari"})]
    assert r.said == "New tab's open."


async def test_messages_go_to_the_message_sender_and_everything_else_to_the_planner():
    from evie.computer.planner import Outcome

    class P:
        async def run(self, goal, app=None):
            return Outcome(True, f"planned: {goal}")

    class M:
        async def send(self, text, a):
            return Outcome(True, f"sent to {a['contact']}")

    rec = Recipes(FakeHands(), None, FakeTalker({"contact": "Mom", "body": "hi"}), planner=P(), messages=M())
    assert (await rec.run("message mom hi", skill="message_send")).said == "sent to Mom"
    assert (await rec.run("make a new note", skill="computer")).said == "planned: make a new note"
