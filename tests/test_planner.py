"""Planner v2: plan once, find in code, pick with Jev, replan only when a check fails."""
import asyncio
import json

from evie.computer.planner import Planner
from evie.countdown import Countdown
from evie.jev import JevResult
from tests.simhands import SimHands

CH = "https://www.youtube.com/@NetworkChuck/videos"
CHANNEL_PAGE = [
    {"id": "w1", "role": "input:text", "label": "Search", "typeable": True, "region": "header"},
    {"id": "w2", "role": "tab", "label": "Videos", "href": CH, "selected": True},
    {"id": "w3", "role": "link", "label": "I hacked my own network (don't try this)",
     "href": "https://www.youtube.com/watch?v=n1", "meta": "412K views 2 days ago", "region": "main"},
    {"id": "w4", "role": "link", "label": "you need to learn Linux RIGHT NOW!!",
     "href": "https://www.youtube.com/watch?v=n2", "meta": "1.2M views 1 week ago", "region": "main"},
]
WATCH = [{"id": "w1", "role": "video", "label": "video"}]
SEARCH = "https://www.youtube.com/results?search_query=network+chuck"
SEARCH_PAGE = [{"id": "w1", "role": "link", "label": "NetworkChuck", "href": "https://www.youtube.com/@NetworkChuck"},
               {"id": "w2", "role": "link", "label": "some other video", "href": "https://www.youtube.com/watch?v=x"}]
SAFARI_FRONT = {"front_app": "Safari", "apps": ["Safari", "Notes"], "windows": [{"app": "Safari", "title": "Google"}],
                "tabs": [{"window": 11, "order": 1, "index": 1, "current": True, "title": "Google",
                          "url": "https://www.google.com/"}], "selected": ""}


class PlanGroq:
    """Answers plan calls from a script; other calls (reading a page) get `text`."""

    def __init__(self, *plans, text="It says hi."):
        self.plans, self.text, self.calls = list(plans), text, []

    async def chat(self, system, user, max_tokens=400, json_mode=False, model=None, reasoning=None):
        self.calls.append(user)
        if json_mode:
            return json.dumps(self.plans.pop(0))
        return self.text


class PickJev:
    """Picks the first option unless told which; records every question."""

    def __init__(self, choose=None, conf=0.9):
        self.choose, self.conf, self.asked = choose, conf, []

    async def ask(self, state, questions):
        key = next(iter(questions))
        opts = list(questions[key]["criteria"])
        self.asked.append((state, opts))
        c = self.choose(opts) if callable(self.choose) else (self.choose or opts[0])
        return JevResult({key: {"type": "choice", "choice": c, "confidence": self.conf}}, 200.0, 0.0)


def planner(hands, groq, jev=None, countdown=None, said=None):
    said = said if said is not None else []
    cd = countdown or Countdown(seconds=0.02)
    p = Planner(hands, groq, jev or PickJev(), cd, say=said.append, settle_s=0, window_s=cd.seconds)
    return p, said


NEWEST = {"steps": [{"do": "open_url", "url": CH}, {"do": "expect", "url_contains": "/videos"},
                    {"do": "pick", "among": "videos", "want": "the newest video", "then": "press"},
                    {"do": "done", "say": "Playing {picked}."}]}


async def test_newest_networkchuck_video_is_one_plan_call_and_one_jev_pick():
    hands = SimHands(pages={CH: CHANNEL_PAGE, "https://www.youtube.com/watch?v=n1": WATCH}, world=SAFARI_FRONT)
    groq, jev = PlanGroq(NEWEST), PickJev()
    p, _ = planner(hands, groq, jev)
    r = await p.run("play the newest networkchuck video")
    assert r.ok and r.said == "Playing I hacked my own network (don't try this)."
    assert len(groq.calls) == 1 and len(jev.asked) == 1
    assert ("open_url", {"url": CH, "app": "Safari", "new_tab": True, "window": 11, "front": True}) in hands.calls
    assert hands.url == "https://www.youtube.com/watch?v=n1"
    assert "may contain misheard words" in groq.calls[0] and "/@<Handle>/videos" in groq.calls[0]


async def test_a_clearly_named_button_is_pressed_without_asking_jev():
    plan = {"steps": [{"do": "open_url", "url": CH}, {"do": "find", "what": "Videos tab", "role": "tab", "then": "press"},
                      {"do": "done", "say": "On his videos."}]}
    hands = SimHands(pages={CH: CHANNEL_PAGE}, world=SAFARI_FRONT)
    jev = PickJev()
    p, _ = planner(hands, PlanGroq(plan), jev)
    r = await p.run("open networkchuck's videos")
    assert r.ok and jev.asked == [] and ("press", {"id": "w2", "snapshot": "s1"}) in hands.calls


async def test_a_wrong_guess_is_caught_by_the_check_and_replanned_once():
    wrong = {"steps": [{"do": "open_url", "url": "https://www.youtube.com/@NetworkChuk/videos"},
                       {"do": "expect", "element": "Videos"}, {"do": "done", "say": "x"}]}
    fixed = {"steps": [{"do": "open_url", "url": SEARCH, "same_tab": True},
                       {"do": "find", "what": "NetworkChuck channel", "href": "/@", "then": "press"},
                       {"do": "open_url", "url": CH, "same_tab": True},
                       {"do": "pick", "among": "videos", "want": "newest", "then": "press"},
                       {"do": "done", "say": "Playing {picked}."}]}
    hands = SimHands(pages={SEARCH: SEARCH_PAGE, CH: CHANNEL_PAGE, "https://www.youtube.com/@NetworkChuck": []},
                     world=SAFARI_FRONT)
    groq = PlanGroq(wrong, fixed)
    p, _ = planner(hands, groq)
    r = await p.run("play the newest network chuck video")
    assert r.ok and len(groq.calls) == 2 and "Steps so far" in groq.calls[1]
    assert "404 Not Found" in groq.calls[1]  # the replan sees the screen that failed


async def test_jev_can_never_pick_something_that_is_not_on_screen():
    hands = SimHands(pages={CH: CHANNEL_PAGE}, world=SAFARI_FRONT)
    p, _ = planner(hands, PlanGroq(NEWEST, {"steps": [{"do": "done", "say": "no"}]}, {"steps": []}),
                   PickJev(choose="w99"))
    await p.run("play the newest networkchuck video")
    assert not any(op == "press" for op, _ in hands.calls)


async def test_this_news_thing_uses_his_front_tab_even_when_safari_is_behind():
    news = "https://www.bbc.com/news"
    world = {"front_app": "Notes", "apps": ["Notes", "Safari"], "windows": [],
             "tabs": [{"window": 7, "order": 1, "index": 2, "current": True, "title": "BBC News", "url": news}]}
    page = [{"id": "w1", "role": "link", "label": "News", "href": news, "region": "nav"},
            {"id": "w2", "role": "link", "label": "Singapore unveils plan to double solar power by 2030",
             "href": "https://www.bbc.com/news/articles/c1", "region": "main"},
            {"id": "w3", "role": "link", "label": "AI model beats doctors at spotting rare diseases",
             "href": "https://www.bbc.com/news/articles/c3", "region": "main"}]
    plan = {"steps": [{"do": "pick", "among": "articles", "want": "the most interesting one for Isaac", "then": "press"},
                      {"do": "done", "say": "Opened {picked}."}]}
    hands = SimHands(pages={news: page, "https://www.bbc.com/news/articles/c3": []}, world=world)
    groq = PlanGroq(plan)
    p, _ = planner(hands, groq, PickJev(choose="w3"))
    r = await p.run("open the most interesting article in this news thing")
    assert hands.calls[1] == ("use_tab", {"window": 7, "index": 2, "front": True})
    assert "Singapore unveils plan" in groq.calls[0]  # the plan was made seeing his page
    assert r.said == "Opened AI model beats doctors at spotting rare diseases."


async def test_a_send_is_read_back_and_stop_cancels_it():
    world = {"front_app": "WhatsApp", "apps": ["WhatsApp"], "windows": [], "tabs": []}
    chat = [{"id": "a1", "role": "textarea", "label": "Type a message", "typeable": True},
            {"id": "a2", "role": "button", "label": "Send"}]
    plan = {"steps": [{"do": "find", "what": "Send button", "role": "button", "then": "press", "say": "Sending 'hi' to Mom"},
                      {"do": "done", "say": "Sent."}]}
    hands = SimHands(apps={"WhatsApp": chat}, world=world)
    cd = Countdown(seconds=0.3)
    p, said = planner(hands, PlanGroq(plan), countdown=cd)
    task = asyncio.create_task(p.run("press send in whatsapp"))
    await asyncio.sleep(0.1)
    assert said and said[-1] == "Sending 'hi' to Mom. Say stop to cancel."
    cd.cancel()
    r = await task
    assert r.said == "Okay, I didn't do it." and not any(op == "press" for op, _ in hands.calls)


async def test_named_actions_run_their_fixed_script():
    world = {"front_app": "Finder", "apps": ["Finder"], "windows": [], "tabs": []}
    plan = {"steps": [{"do": "action", "name": "notes_new", "args": {"title": "Groceries", "body": "milk"}},
                      {"do": "done", "say": "New note made."}]}
    hands = SimHands(world=world)
    p, _ = planner(hands, PlanGroq(plan))
    r = await p.run("make a note called groceries with milk in notes")
    script = next(a["source"] for op, a in hands.calls if op == "applescript")
    assert r.ok and 'make new note' in script and '"Groceries"' in script


async def test_a_risky_step_in_an_app_evie_does_not_know_is_asked_about_first():
    world = {"front_app": "Banking", "apps": ["Banking"], "windows": [], "tabs": []}
    screen = [{"id": "a1", "role": "button", "label": "Transfer"}]
    plan = {"steps": [{"do": "find", "what": "Transfer", "then": "press", "risky": True}, {"do": "done", "say": "x"}]}
    hands = SimHands(apps={"Banking": screen}, world=world)
    p, _ = planner(hands, PlanGroq(plan))
    r = await p.run("press transfer")
    assert r.ask and not any(op == "press" for op, _ in hands.calls)


async def test_two_failed_replans_mean_stuck():
    bad = {"steps": [{"do": "expect", "url_contains": "/nope"}]}
    hands = SimHands(pages={CH: CHANNEL_PAGE}, world=SAFARI_FRONT)
    p, _ = planner(hands, PlanGroq(bad, bad, bad))
    r = await p.run("do something odd")
    assert not r.ok and r.stuck


async def test_read_answers_from_the_page():
    world = dict(SAFARI_FRONT)
    plan = {"steps": [{"do": "read", "what": "a two sentence summary"}]}
    hands = SimHands(world=world, page_text={"https://www.google.com/": "Google homepage text"})
    p, _ = planner(hands, PlanGroq(plan, text="It's Google's homepage."))
    r = await p.run("summarise this page")
    assert r.ok and r.said == "It's Google's homepage."


class LookGroq(PlanGroq):
    def __init__(self, *plans, n=2):
        super().__init__(*plans)
        self.n, self.looked = n, []

    async def look(self, prompt, png_b64, max_tokens=60):
        self.looked.append(prompt)
        return json.dumps({"n": self.n})


async def test_an_app_with_barely_any_readable_buttons_is_looked_at():
    """Some apps (games, canvas apps) show almost nothing to Accessibility: a screenshot with
    numbered boxes, and Qwen picks the number, which is a real element id."""
    world = {"front_app": "Pixelmator", "apps": ["Pixelmator"], "windows": [], "tabs": []}
    screen = [{"id": "a1", "role": "group", "label": ""}, {"id": "a2", "role": "button", "label": ""}]
    plan = {"steps": [{"do": "find", "what": "the crop tool", "then": "press"}, {"do": "done", "say": "Crop tool's on."}]}
    hands = SimHands(apps={"Pixelmator": screen}, world=world)
    groq = LookGroq(plan, n=2)
    p, _ = planner(hands, groq)
    r = await p.run("pick the crop tool in pixelmator")
    assert r.ok and ("press", {"id": "a2", "snapshot": "s1"}) in hands.calls and "crop tool" in groq.looked[0]


async def test_a_number_that_is_not_a_box_presses_nothing():
    world = {"front_app": "Pixelmator", "apps": ["Pixelmator"], "windows": [], "tabs": []}
    screen = [{"id": "a1", "role": "button", "label": ""}]
    plan = {"steps": [{"do": "find", "what": "the crop tool", "then": "press"}, {"do": "done", "say": "x"}]}
    hands = SimHands(apps={"Pixelmator": screen}, world=world)
    p, _ = planner(hands, LookGroq(plan, plan, plan, n=9))
    r = await p.run("pick the crop tool in pixelmator")
    assert not r.ok and not any(op == "press" for op, _ in hands.calls)
