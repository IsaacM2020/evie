import asyncio
import json

from evie.computer.observe import Screen
from evie.computer.planner import Planner
from evie.computer.recipes import Recipes
from evie.computer.safety import is_risky, risky_words
from evie.countdown import Countdown
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


class FakeGroq:
    def __init__(self, *steps):
        self.steps, self.prompts = list(steps), []

    async def chat(self, system, user, max_tokens=400, json_mode=False, model=None, reasoning=None):
        self.prompts.append(user)
        return json.dumps(self.steps.pop(0))


def planner(hands, groq, countdown=None, said=None):
    said = said if said is not None else []
    cd = countdown or Countdown(seconds=0.02)
    return Planner(hands, groq, cd, say=said.append, settle_s=0, window_s=cd.seconds), said


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


async def test_planner_presses_a_real_element_then_finishes():
    hands = FakeHands({"observe": [obs(YT)]})
    groq = FakeGroq({"op": "press", "id": "w5", "why": "first video"},
                    {"op": "done", "say": "Playing I Spent 7 Days Buried Alive."})
    p, said = planner(hands, groq)
    r = await p.run("play a mrbeast video", app="Safari")
    assert r.ok and r.said == "Playing I Spent 7 Days Buried Alive."
    assert ("press", {"id": "w5", "snapshot": "s1"}) in hands.calls
    assert "Goal: play a mrbeast video" in groq.prompts[0] and "w5 link" in groq.prompts[0]


async def test_planner_never_acts_on_an_id_that_isnt_on_screen():
    hands = FakeHands({"observe": [obs(YT)]})
    groq = FakeGroq({"op": "press", "id": "w99"}, {"op": "press", "id": "w42"}, {"op": "press", "id": "w77"})
    p, _ = planner(hands, groq)
    r = await p.run("do something", app="Safari")
    assert not r.ok and r.stuck and not any(op == "press" for op, _ in hands.calls)


async def test_risky_step_is_read_back_and_waits_for_stop():
    msg = [{"id": "a1", "role": "textarea", "label": "Type a message", "typeable": True},
           {"id": "a2", "role": "button", "label": "Send"}]
    hands = FakeHands({"observe": [obs(msg, url="", app="WhatsApp", kind="app")]})
    groq = FakeGroq({"op": "press", "id": "a2", "say": "Sending it"}, {"op": "done", "say": "Sent."})
    p, said = planner(hands, groq)
    r = await p.run("send it", app="WhatsApp")
    assert said[0] == "Sending it. Say stop to cancel." and r.said == "Sent."
    assert ("press", {"id": "a2", "snapshot": "s1"}) in hands.calls


async def test_stop_during_the_read_back_cancels_the_send():
    msg = [{"id": "a2", "role": "button", "label": "Send"}]
    hands = FakeHands({"observe": [obs(msg, url="", app="WhatsApp", kind="app")]})
    cd = Countdown(seconds=0.2)
    groq = FakeGroq({"op": "press", "id": "a2", "say": "Sending it"})
    p, said = planner(hands, groq, countdown=cd)
    task = asyncio.create_task(p.run("send it", app="WhatsApp"))
    await asyncio.sleep(0.05)
    assert cd.cancel() is True
    r = await task
    assert r.said == "Okay, I didn't do it." and not any(op == "press" for op, _ in hands.calls)


async def test_planner_asks_when_the_goal_is_vague():
    hands = FakeHands({"observe": [obs(YT)]})
    p, _ = planner(hands, FakeGroq({"op": "ask", "say": "Which video, the newest one?"}))
    r = await p.run("play that one", app="Safari")
    assert r.ask and r.said == "Which video, the newest one?"


async def test_planner_says_so_when_the_app_isnt_open():
    hands = FakeHands({"observe": [HandsResult(False, "Notion isn't open")]})
    p, _ = planner(hands, FakeGroq())
    r = await p.run("open my notes", app="Notion")
    assert not r.ok and r.said == "Couldn't do that: Notion isn't open."


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


async def test_youtube_recipe_opens_results_in_the_background_and_plays_the_first_video():
    watch = obs(YT, url="https://www.youtube.com/watch?v=abc", snap="s2")
    hands = FakeHands({"observe": [obs(YT), watch], "wait_page": [HandsResult(True, "loaded")]})
    rec = Recipes(hands, FakeJev("youtube_play"), FakeTalker({"query": "mrbeast"}), planner=None)
    r = await rec.run("play a video by mrbeast")
    ops = [op for op, _ in hands.calls]
    assert hands.calls[0] == ("open_url", {"url": "https://www.youtube.com/results?search_query=mrbeast",
                                           "app": "Safari", "front": False})
    assert ("press", {"id": "w5", "snapshot": "s1"}) in hands.calls
    assert ops[-1] == "activate" and r.ok and r.said == "Playing I Spent 7 Days Buried Alive."


async def test_new_tab_recipe():
    hands = FakeHands()
    r = await Recipes(hands, FakeJev("new_tab"), FakeTalker({}), planner=None).run("open a new tab")
    assert hands.calls == [("activate", {"app": "Safari"}), ("key", {"combo": "cmd+t", "app": "Safari"})]
    assert r.said == "New tab's open."


async def test_no_recipe_goes_to_the_planner():
    class P:
        async def run(self, goal, app=None):
            from evie.computer.planner import Outcome
            return Outcome(True, f"planned: {goal} in {app}")

    r = await Recipes(FakeHands(), FakeJev("none"), FakeTalker({"app": "Notes"}), planner=P()).run("make a new note")
    assert r.said == "planned: make a new note in Notes"
