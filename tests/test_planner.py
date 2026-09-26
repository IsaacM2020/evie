"""Planner v2: plan once, find in code, pick with Jev, replan only when a check fails."""
import asyncio
import json

from evie.computer.planner import Planner
from evie.countdown import Countdown
from evie.hands import HandsResult
from evie.jev import JevResult
from evals.sim import SimHands

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

    async def chat(self, system, user, max_tokens=400, json_mode=False, model=None, reasoning=None, fallbacks=None):
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
    p.EXPECT_S, p._expect_poll = 0.05, 0.01  # the sim's pages never load slowly (SlowTab does its own)
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


NETFLIX_WRONG_PROFILE_SCREEN = [
    {"id": "w1", "role": "link", "label": "Isaac", "href": "https://www.netflix.com/browse?profile=isaac"},
    {"id": "w2", "role": "link", "label": "Dangal", "href": "https://www.netflix.com/browse?profile=dangal"},
    {"id": "w3", "role": "link", "label": "Kids", "href": "https://www.netflix.com/browse?profile=kids"},
]
NETFLIX_PLAN = {"steps": [{"do": "open_url", "url": "https://www.netflix.com/"},
                          {"do": "find", "what": "Daryl", "role": "link", "then": "press"},
                          {"do": "done", "say": "Daryl's profile opened."}]}


async def test_jev_choose_refuses_when_nothing_is_a_plausible_match():
    """2026-09-25 core.log: Evie pressed 'Dangal' when asked for the Netflix profile 'Daryl'.
    After the fix: none of Isaac, Dangal or Kids score above NO_MATCH_FLOOR against "Daryl", so
    _jev_choose offers "none" and PickJev(choose=lambda opts: "none") simulates Jev correctly
    recognizing that. The task fails gracefully (replan, then stuck) -- nothing gets pressed."""
    hands = SimHands(pages={"https://www.netflix.com/": NETFLIX_WRONG_PROFILE_SCREEN}, world=SAFARI_FRONT)
    p, _ = planner(hands, PlanGroq(NETFLIX_PLAN, NETFLIX_PLAN, NETFLIX_PLAN), PickJev(choose=lambda opts: "none"))
    r = await p.run("open netflix and click the profile daryl")
    assert not r.ok and r.stuck
    assert not any(op == "press" for op, _ in hands.calls)


async def test_pick_never_offers_none_for_a_subjective_want():
    """Code review I3: _pick's 'want' is a description ('a video', 'the newest one'), not a named
    target that might be absent -- NO_MATCH_FLOOR wrongly fired on nearly every pick (a plain "a
    video" scored 0.1 against every real title). Among real candidates there's always a best
    match, so pick must never be offered a "none" escape."""
    hands = SimHands(pages={CH: CHANNEL_PAGE, "https://www.youtube.com/watch?v=n1": WATCH}, world=SAFARI_FRONT)
    jev = PickJev()
    p, _ = planner(hands, PlanGroq(NEWEST), jev)
    r = await p.run("play the newest networkchuck video")
    assert r.ok
    assert "none" not in jev.asked[0][1]  # the criteria dict never contained a "none" key


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


async def test_a_check_right_after_a_scripted_action_is_not_held_against_it():
    """Actions are fixed scripts that report success themselves; what they change often isn't on the
    screen being read (a new note, Wi-Fi), so an expect right after one can't fail the task."""
    world = {"front_app": "Finder", "apps": ["Finder"], "windows": [], "tabs": []}
    plan = {"steps": [{"do": "action", "name": "wifi", "args": {"on": False}}, {"do": "expect", "element": "Wi-Fi off"},
                      {"do": "done", "say": "Wi-Fi's off."}]}
    p, _ = planner(SimHands(world=world), PlanGroq(plan))
    r = await p.run("turn off wifi")
    assert r.ok and r.said == "Wi-Fi's off."


async def test_the_screenshot_question_says_json_as_groq_requires():
    world = {"front_app": "Pixelmator", "apps": ["Pixelmator"], "windows": [], "tabs": []}
    plan = {"steps": [{"do": "find", "what": "crop tool", "then": "press"}, {"do": "done", "say": "ok"}]}
    groq = LookGroq(plan, n=1)
    p, _ = planner(SimHands(apps={"Pixelmator": [{"id": "a1", "role": "button", "label": ""}]}, world=world), groq)
    await p.run("pick the crop tool in pixelmator")
    assert "json" in groq.looked[0].lower()


async def test_a_settings_address_goes_through_the_settings_action():
    world = {"front_app": "Finder", "apps": ["Finder"], "windows": [], "tabs": []}
    plan = {"steps": [{"do": "open_url", "url": "x-apple.systempreferences:com.apple.Focus-Settings.extension"},
                      {"do": "done", "say": "Opened Focus."}]}
    hands = SimHands(world=world)
    p, _ = planner(hands, PlanGroq(plan))
    r = await p.run("open focus settings")
    assert r.ok and any(op == "applescript" and "Focus-Settings" in a["source"] for op, a in hands.calls)


async def test_an_unknown_action_fails_with_the_real_names_for_the_replan():
    world = {"front_app": "Finder", "apps": ["Finder"], "windows": [], "tabs": []}
    bad = {"steps": [{"do": "action", "name": "new_note", "args": {}}]}
    good = {"steps": [{"do": "action", "name": "notes_new", "args": {"title": "a", "body": "b"}}, {"do": "done", "say": "ok"}]}
    groq = PlanGroq(bad, good)
    p, _ = planner(SimHands(world=world), groq)
    assert (await p.run("new note")).ok and "notes_new" in groq.calls[1]


async def test_she_only_reads_things_out_when_isaac_asked_for_something():
    """The eval: 'open the most interesting bbc article' opened it and then read out a summary nobody asked for."""
    news = "https://www.bbc.com/news"
    page = [{"id": "w2", "role": "link", "label": "AI model beats doctors at spotting rare diseases",
             "href": "https://www.bbc.com/news/articles/c3", "region": "main"}]
    plan = {"steps": [{"do": "open_url", "url": news}, {"do": "pick", "among": "articles", "want": "most interesting",
                                                        "then": "press"},
                      {"do": "read", "what": "summarise it"}, {"do": "done", "say": "Opened {picked}."}]}
    hands = SimHands(pages={news: page, "https://www.bbc.com/news/articles/c3": []}, world=SAFARI_FRONT)
    p, _ = planner(hands, PlanGroq(plan, text="A long summary."))
    r = await p.run("open the most interesting bbc article")
    assert r.said == "Opened AI model beats doctors at spotting rare diseases."


async def test_a_plan_that_never_did_anything_is_not_reported_as_done():
    """The eval: 'delete old.txt' got a plan that only looked; she said 'Done.' having done nothing."""
    world = {"front_app": "Finder", "apps": ["Finder"], "windows": [], "tabs": []}
    looks_only = {"steps": [{"do": "read", "what": "is old.txt on the desktop"}]}
    does_it = {"steps": [{"do": "action", "name": "finder_trash", "args": {"path": "~/Desktop/old.txt"}}]}
    hands = SimHands(world=world)
    p, said = planner(hands, PlanGroq(looks_only, does_it))
    r = await p.run("delete old.txt from my desktop")
    assert r.ok and r.said == "Moved it to the Bin." and any(op == "applescript" for op, _ in hands.calls)
    assert said[0].endswith("Say stop to cancel.")


async def test_without_a_closing_line_she_says_what_she_picked():
    plan = {"steps": [{"do": "open_url", "url": CH}, {"do": "pick", "among": "videos", "want": "newest", "then": "press"}]}
    hands = SimHands(pages={CH: CHANNEL_PAGE}, world=SAFARI_FRONT)
    p, _ = planner(hands, PlanGroq(plan))
    r = await p.run("play the newest networkchuck video")
    assert r.said == "Opened I hacked my own network (don't try this)."


async def test_a_rate_limited_plan_waits_and_tries_once_more():
    from evie.talk import TalkError

    class Busy(PlanGroq):
        def __init__(self, *plans):
            super().__init__(*plans)
            self.n = 0

        async def chat(self, system, user, **k):
            self.n += 1
            if self.n == 1:
                raise TalkError("rate limited")
            return await super().chat(system, user, **k)

    world = {"front_app": "Finder", "apps": ["Finder"], "windows": [], "tabs": []}
    groq = Busy({"steps": [{"do": "action", "name": "wifi", "args": {"on": False}}, {"do": "done", "say": "Wi-Fi's off."}]})
    p, _ = planner(SimHands(world=world), groq)
    p._rate_wait = 0.01
    assert (await p.run("turn off wifi")).said == "Wi-Fi's off."


async def test_she_says_what_she_understood_as_soon_as_the_plan_is_ready():
    steps = [{"do": "open_url", "url": SEARCH}, {"do": "find", "what": "NetworkChuck channel", "href": "/@", "then": "press"},
             {"do": "open_url", "url": CH, "same_tab": True},
             {"do": "pick", "among": "videos", "want": "newest", "then": "press"}, {"do": "done", "say": "Playing {picked}."}]
    long = {"understood": "Finding NetworkChuck's newest video", "steps": steps}
    hands = SimHands(pages={SEARCH: SEARCH_PAGE, CH: CHANNEL_PAGE}, world=SAFARI_FRONT)
    p, said = planner(hands, PlanGroq(long))
    await p.run("play the newest network chuck video")
    assert said[0] == "Finding NetworkChuck's newest video. Say stop if that's wrong."
    short = {"understood": "Turning off Wi-Fi", "steps": [{"do": "action", "name": "wifi", "args": {"on": False}},
                                                          {"do": "done", "say": "Wi-Fi's off."}]}
    p, said = planner(SimHands(world={"front_app": "Finder", "apps": [], "windows": [], "tabs": []}), PlanGroq(short))
    await p.run("turn off wifi")
    assert said == ["Turning off Wi-Fi."]  # a short one: no stop window to mention
    dashed = {"understood": "-opening a Parrot video", "steps": short["steps"]}  # 2026-09-24 18:24:02
    p, said = planner(SimHands(world={"front_app": "Finder", "apps": [], "windows": [], "tabs": []}), PlanGroq(dashed))
    await p.run("turn off wifi")
    assert said == ["Opening a Parrot video."]


async def test_return_after_typing_in_a_chat_is_a_send_and_is_read_back():
    """Self-review: typing into a chat and then pressing Return sends it. Keys weren't risk-checked."""
    world = {"front_app": "WhatsApp", "apps": ["WhatsApp"], "windows": [], "tabs": []}
    chat = [{"id": "a1", "role": "textarea", "label": "Compose", "typeable": True},
            {"id": "a2", "role": "row", "label": "Mom"}]
    plan = {"steps": [{"do": "find", "what": "Compose", "then": "set_text", "text": "on my way"},
                      {"do": "key", "combo": "return", "say": "Sending 'on my way' to Mom"},
                      {"do": "done", "say": "Sent."}]}
    hands = SimHands(apps={"WhatsApp": chat}, world=world)
    cd = Countdown(seconds=0.3)
    p, said = planner(hands, PlanGroq(plan), countdown=cd)
    task = asyncio.create_task(p.run("whatsapp mom on my way"))
    await asyncio.sleep(0.1)
    cd.cancel()
    r = await task
    assert any(line.endswith("Say stop to cancel.") for line in said)
    assert not any(op == "key" for op, _ in hands.calls) and r.said == "Okay, I didn't do it."


async def test_ordinary_keys_are_not_held_up():
    world = {"front_app": "Safari", "apps": ["Safari"], "windows": [], "tabs": []}
    plan = {"steps": [{"do": "key", "combo": "cmd+t"}, {"do": "done", "say": "New tab."}]}
    hands = SimHands(world=world)
    p, said = planner(hands, PlanGroq(plan))
    await p.run("open a new tab please right now")
    assert said == [] and ("key", {"combo": "cmd+t", "app": "Safari"}) in hands.calls


async def test_switching_to_his_tab_is_the_whole_job():
    gmail = "https://mail.google.com/mail/u/0/"
    world = {"front_app": "Safari", "apps": ["Safari"], "windows": [],
             "tabs": [{"window": 11, "order": 1, "index": 1, "current": True, "title": "Google", "url": "https://www.google.com/"},
                      {"window": 11, "order": 1, "index": 2, "current": False, "title": "Inbox - Gmail", "url": gmail}]}
    p, _ = planner(SimHands(pages={gmail: []}, world=world), PlanGroq({"steps": [{"do": "done", "say": "Here's Gmail."}]}))
    r = await p.run("switch to my gmail")
    assert r.ok and r.said == "Here's Gmail."


async def test_pick_then_read_still_opens_it_when_he_only_asked_to_open_it():
    news = "https://www.bbc.com/news"
    page = [{"id": "w2", "role": "link", "label": "AI model beats doctors at spotting rare diseases",
             "href": "https://www.bbc.com/news/articles/c3", "region": "main"}]
    plan = {"steps": [{"do": "open_url", "url": news},
                      {"do": "pick", "among": "articles", "want": "most interesting", "then": "read"}]}
    hands = SimHands(pages={news: page, "https://www.bbc.com/news/articles/c3": []}, world=SAFARI_FRONT)
    p, _ = planner(hands, PlanGroq(plan, text="Some summary."))
    r = await p.run("open the most interesting bbc article")
    assert hands.url.endswith("/c3") and r.said == "Opened AI model beats doctors at spotting rare diseases."


# -- Phase 4 T2: browse first, then ask (Isaac, 2026-09-24: "open up the page and show me the videos and
# then ask me which one") ----------------------------------------------------------------------------
MB = "https://www.youtube.com/@MrBeast/videos"
MB_TITLES = ["$1 vs $1,000,000 Hotel Room", "I Survived 7 Days In An Abandoned City", "Last To Leave The Island Wins",
             "Ages 1 - 100 Fight For $500,000"]
MB_PAGE = [{"id": "m1", "role": "tab", "label": "Videos", "href": MB, "selected": True}] + [
    {"id": f"m{i + 2}", "role": "link", "label": t, "href": f"https://www.youtube.com/watch?v=b{i}",
     "meta": f"{i + 2} days ago", "region": "main"} for i, t in enumerate(MB_TITLES)]
VAGUE = {"understood": "Opening MrBeast's videos", "steps": [
    {"do": "open_url", "url": MB}, {"do": "expect", "url_contains": "/videos"},
    {"do": "pick", "among": "videos", "want": "a MrBeast video", "then": "press", "ask": True},
    {"do": "done", "say": "Playing {picked}."}]}


def mb_hands():
    pages = {MB: MB_PAGE, **{f"https://www.youtube.com/watch?v=b{i}": WATCH for i in range(4)}}
    return SimHands(pages=pages, world=SAFARI_FRONT)


async def test_a_vague_video_request_opens_the_list_then_asks_which_one():
    hands, groq, jev = mb_hands(), PlanGroq(VAGUE), PickJev()
    p, said = planner(hands, groq, jev)
    r = await p.run("open safari and open a mrbeast video")
    assert r.ask and not r.ok
    assert [o["label"] for o in r.options] == MB_TITLES[:3]
    assert r.said.startswith("Which one?") and MB_TITLES[0] in r.said
    assert hands.url == MB  # the page is open, so he can see them
    assert not [c for c in hands.calls if c[0] == "press"] and jev.asked == []


async def test_his_answer_picks_from_the_same_rows_with_no_replan():
    hands, groq = mb_hands(), PlanGroq(VAGUE)
    p, _ = planner(hands, groq, PickJev())
    r = await p.run("open safari and open a mrbeast video")
    p._jev = jev = PickJev(choose=lambda opts: opts[1])
    r2 = await p.choose(r.pick, "the abandoned city one")
    assert r2.ok and r2.said == f"Playing {MB_TITLES[1]}."
    assert len(groq.calls) == 1  # still the one plan call
    state, opts = jev.asked[0]
    assert "the abandoned city one" in state and opts == ["m2", "m3", "m4"]
    assert hands.url == "https://www.youtube.com/watch?v=b1"


async def test_a_tap_on_an_option_needs_no_model():
    hands, groq = mb_hands(), PlanGroq(VAGUE)
    p, _ = planner(hands, groq, PickJev())
    r = await p.run("open safari and open a mrbeast video")
    p._jev = jev = PickJev()
    r2 = await p.choose(r.pick, None, eid=r.options[2]["id"])
    assert r2.ok and MB_TITLES[2] in r2.said and jev.asked == []


async def test_rows_that_changed_are_found_again_by_their_link():
    hands, groq = mb_hands(), PlanGroq(VAGUE)
    p, _ = planner(hands, groq, PickJev())
    r = await p.run("open safari and open a mrbeast video")
    # YouTube re-rendered: same videos, new element ids, and a new video on top
    hands.pages[MB] = [MB_PAGE[0], {"id": "z9", "role": "link", "label": "Brand New Upload",
                                    "href": "https://www.youtube.com/watch?v=new", "meta": "1 hour ago"}] + [
        {**e, "id": "z" + e["id"]} for e in MB_PAGE[1:]]
    p._jev = jev = PickJev(choose=lambda opts: opts[0])
    r2 = await p.choose(r.pick, "the hotel one")
    assert jev.asked[0][1] == ["zm2", "zm3", "zm4"]  # the rows he was shown, not the new upload
    assert r2.ok and MB_TITLES[0] in r2.said
    # a tap on a row that's gone falls back to his shown rows too
    hands.url = MB
    r3 = await p.choose(r.pick, None, eid="m3")
    assert r3.ok and MB_TITLES[1] in r3.said


async def test_the_planner_is_told_when_to_ask():
    assert '"ask"' in __import__("evie.computer.planner", fromlist=["SYSTEM"]).SYSTEM


def test_vague_pick_is_spotted_in_code():
    from evie.computer.planner import vague_pick
    for g in ("open safari and open a mrbeast video", "play a video by networkchuck", "open a bbc article",
              "put on some mrbeast videos", "open an article on cna"):
        assert vague_pick(g), g
    for g in ("play the newest networkchuck video", "play mrbeast's latest video", "open the bbc article about solar",
              "find a video that explains how transformers work in ai", "open the most interesting bbc article",
              "play the linux video from networkchuck", "open a new tab", "open youtube"):
        assert not vague_pick(g), g


async def test_a_vague_goal_asks_even_when_the_plan_forgot_to():
    plan = {"steps": [s if s.get("do") != "pick" else {k: v for k, v in s.items() if k != "ask"} for s in VAGUE["steps"]]}
    hands, groq, jev = mb_hands(), PlanGroq(plan), PickJev()
    p, _ = planner(hands, groq, jev)
    r = await p.run("open safari and open a mrbeast video")
    assert r.ask and r.options and jev.asked == []


async def test_the_prompt_template_is_never_said_out_loud():
    plan = {"steps": [{"do": "open_url", "url": CH}, {"do": "pick", "among": "videos", "want": "the newest"},
                      {"do": "done", "say": "one short spoken sentence; {picked} = the label of what you picked"}]}
    hands = SimHands(pages={CH: CHANNEL_PAGE, "https://www.youtube.com/watch?v=n1": WATCH}, world=SAFARI_FRONT)
    p, _ = planner(hands, PlanGroq(plan))
    r = await p.run("play the newest networkchuck video")
    assert r.ok and "spoken sentence" not in r.said and "I hacked my own network" in r.said


def test_ordinal_answers_are_worked_out_in_code():
    from evie.computer.planner import ordinal_row
    assert ordinal_row("the newest one", 3) == 0 and ordinal_row("latest", 3) == 0 and ordinal_row("the first", 3) == 0
    assert ordinal_row("the top one", 3) == 0 and ordinal_row("number one", 3) == 0 and ordinal_row("1", 3) == 0
    assert ordinal_row("the second one", 3) == 1 and ordinal_row("number 2", 3) == 1 and ordinal_row("2nd", 3) == 1
    assert ordinal_row("the third one", 3) == 2 and ordinal_row("third", 3) == 2
    assert ordinal_row("the third one", 2) is None
    for a in ("the island one", "the one about the hotel", "the last one", "whichever"):
        assert ordinal_row(a, 3) is None, a


async def test_the_newest_one_needs_no_model():
    hands, groq = mb_hands(), PlanGroq(VAGUE)
    p, _ = planner(hands, groq, PickJev())
    r = await p.run("open safari and open a mrbeast video")
    p._jev = jev = PickJev(choose=lambda opts: opts[1])  # would be wrong
    r2 = await p.choose(r.pick, "the newest one")
    assert jev.asked == [] and r2.ok and MB_TITLES[0] in r2.said


async def test_choosing_opens_it_unless_he_asked_a_question():
    plan = {"steps": [{"do": "open_url", "url": MB},
                      {"do": "pick", "among": "videos", "want": "a video", "then": "read", "ask": True},
                      {"do": "done"}]}
    hands, groq = mb_hands(), PlanGroq(plan)
    p, _ = planner(hands, groq, PickJev())
    r = await p.run("open a mrbeast video")
    r2 = await p.choose(r.pick, "the second one")
    assert r2.ok and hands.url == "https://www.youtube.com/watch?v=b1"


async def test_after_choosing_she_says_the_title():
    plan = {"steps": [{"do": "open_url", "url": MB}, {"do": "pick", "among": "videos", "want": "a video", "ask": True},
                      {"do": "done", "say": "Your video is playing."}]}
    hands = mb_hands()
    p, _ = planner(hands, PlanGroq(plan), PickJev())
    r = await p.run("open a mrbeast video")
    r2 = await p.choose(r.pick, "the second one")
    assert r2.said == f"Playing {MB_TITLES[1]}."


async def test_after_she_picks_by_herself_the_runners_up_are_kept():
    hands = SimHands(pages={CH: CHANNEL_PAGE, "https://www.youtube.com/watch?v=n1": WATCH,
                            "https://www.youtube.com/watch?v=n2": WATCH}, world=SAFARI_FRONT)
    p, _ = planner(hands, PlanGroq(NEWEST), PickJev())
    r = await p.run("play the newest networkchuck video")
    assert r.ok and r.pick and [row["id"] for row in r.pick["rows"]] == ["w4"]
    assert r.pick["url"] == CH
    # "no, the other one": she goes back to the list and opens her next pick, no model
    p._jev = jev = PickJev()
    r2 = await p.choose(r.pick, None, eid="w4")
    assert r2.ok and "learn Linux" in r2.said and jev.asked == []
    assert hands.url == "https://www.youtube.com/watch?v=n2"


# -- 2026-09-24 18:30: Netflix -> "Darrell" -> The Mentalist fell back to Claude Code ------------
NFX = "https://www.netflix.com"
PROFILES = [{"id": "p1", "role": "link", "label": "Isaac", "href": NFX + "/browse?p=1"},
            {"id": "p2", "role": "link", "label": "Darryl", "href": NFX + "/browse?p=2"},
            {"id": "p3", "role": "link", "label": "Kids", "href": NFX + "/browse?p=3"}]


class SlowTab(SimHands):
    """A new tab reads about:blank for the first `blank` looks, like Safari while Netflix loads."""

    def __init__(self, blank=2, **kw):
        super().__init__(**kw)
        self.blank = blank

    def _screen(self):
        if self.focus == "web" and self.url and self.blank > 0:
            self.blank -= 1
            self.snap += 1
            return {"snapshot": f"s{self.snap}", "app": "Safari", "kind": "web", "url": "about:blank",
                    "window": "", "elements": "[]"}
        return super()._screen()


NETFLIX_PLAN = {"understood": "Opening Netflix", "steps": [
    {"do": "open_url", "url": NFX}, {"do": "expect", "url_contains": "netflix.com"},
    {"do": "find", "what": "Darrell", "role": "link", "then": "press", "say": "Clicked Darrell"},
    {"do": "done", "say": "Opened Darrell's profile."}]}


async def test_a_slow_new_tab_is_waited_for_not_replanned():
    hands = SlowTab(pages={NFX: PROFILES, NFX + "/browse?p=2": []}, world=SAFARI_FRONT)
    groq = PlanGroq(NETFLIX_PLAN)
    p, said = planner(hands, groq)
    p.EXPECT_S = 1.0
    out = await p.run("go to netflix and click on the account named darrell")
    assert out.ok and len([c for c in groq.calls]) == 1  # one plan, no replans
    assert hands.url == NFX + "/browse?p=2"  # "Darrell" is Darryl


class LaunchingApp(SimHands):
    """Notion just launched: the first looks find no window yet (2026-09-24 18:28:15, "Something broke")."""

    def __init__(self, missing=2, **kw):
        super().__init__(**kw)
        self.missing = missing

    async def do(self, op, timeout=5.0, **a):
        if op == "observe" and a.get("app") not in (None, "Safari") and self.missing > 0:
            self.missing -= 1
            self.calls.append((op, a))
            return HandsResult(False, f"{a['app']} has no window open")
        return await super().do(op, timeout, **a)


async def test_a_just_launched_app_is_waited_for():
    world = {"front_app": "Notion", "apps": ["Notion"], "windows": [], "tabs": []}
    hands = LaunchingApp(apps={"Notion": [{"id": "n1", "role": "button", "label": "New page"}]}, world=world)
    plan = {"understood": "Making a new Notion page", "steps": [
        {"do": "find", "what": "New page", "then": "press"}, {"do": "done", "say": "New page made."}]}
    p, said = planner(hands, PlanGroq(plan))
    p._window_poll = 0.01
    out = await p.run("create a new page in notion")
    assert out.ok and out.said == "New page made."


async def test_his_answer_is_matched_against_titles_not_the_thumbnail_badges():
    """Eval parrot1 (the 18:31 shape): the rows were shown by title, but choose() found them again by
    link, and the first link with that href is the thumbnail ("14:13 Now playing"), so Jev was asked to
    pick "the talking one" out of "14:13 Now playing" and "0:31"."""
    from evals.computer.tasks import PARROT_VIDEOS
    parrot = "https://www.youtube.com/@Parrot/videos"
    plan = {"understood": "Opening Parrot's videos", "steps": [
        {"do": "open_url", "url": parrot}, {"do": "pick", "among": "videos", "want": "a video", "then": "press",
                                            "ask": True}, {"do": "done", "say": "Playing {picked}."}]}
    hands = SimHands(pages={parrot: PARROT_VIDEOS, "https://www.youtube.com/watch?v=p2": WATCH}, world=SAFARI_FRONT)
    p, _ = planner(hands, PlanGroq(plan), PickJev())
    r = await p.run("play a video by parrot")
    assert r.ask and [o["label"] for o in r.options] == ["I Built A Parrot Paradise", "Parrot Learns To Talk In 30 Days"]
    p._jev = jev = PickJev(choose=lambda opts: opts[1])
    r2 = await p.choose(r.pick, "the talking one")
    state, opts = jev.asked[0]
    assert opts == ["w4", "w6"]  # the title links, never the badges
    assert r2.ok and hands.url.endswith("watch?v=p2")


async def test_never_types_into_a_password_field():
    """P0 #2 (core.log 2026-09-25 17:54): a replan once planned typing daryl@example.com and a
    password into a login form. This must be a hard code-level ban, not a confirmable risky
    action -- is_risky's 3s countdown is the wrong gate here (a missed 'stop' would type a real
    credential)."""
    plan = {"steps": [{"do": "open_url", "url": "https://example.com/login"},
                      {"do": "find", "what": "Password", "typeable": True, "then": "set_text", "text": "hunter2"},
                      {"do": "done", "say": "Logged in."}]}
    login_page = [{"id": "e1", "role": "textfield", "label": "Password", "typeable": True}]
    hands = SimHands(pages={"https://example.com/login": login_page}, world=SAFARI_FRONT)
    p, said = planner(hands, PlanGroq(plan))
    r = await p.run("log into example.com")
    assert not r.ok and r.ask
    assert not any(op == "set_text" for op, _ in hands.calls)  # never actually typed


async def test_never_types_into_an_email_field_on_a_login_screen():
    """Code review I2: the P0 #2 log case was typing daryl@example.com into an EMAIL field ("Email
    or mobile number", Netflix's real login label) -- its own label/role never contains any of
    _CREDENTIAL_WORDS, so the field-only check always missed this half. A screen is a login form
    if ANY element on it is a real password field (web inputs report role "input:password"), and
    then no field on that screen may be typed into, whatever its own label says."""
    plan = {"steps": [{"do": "open_url", "url": "https://example.com/login"},
                      {"do": "find", "what": "Email or mobile number", "typeable": True, "then": "set_text",
                       "text": "daryl@example.com"}, {"do": "done", "say": "Logged in."}]}
    login_page = [{"id": "e1", "role": "input:text", "label": "Email or mobile number", "typeable": True},
                  {"id": "e2", "role": "input:password", "label": "Password", "typeable": True}]
    hands = SimHands(pages={"https://example.com/login": login_page}, world=SAFARI_FRONT)
    p, _ = planner(hands, PlanGroq(plan))
    r = await p.run("log into example.com")
    assert not r.ok and r.ask
    assert not any(op == "set_text" for op, _ in hands.calls)


async def test_replan_still_hits_the_credential_ban():
    """The ban lives in _act, which every set_text step funnels through regardless of whether
    the plan came from the first _plan() call or a replan (planner.py:220 calls _plan(first=False)
    with a fresh model call) -- this pins that a REPLANNED step targeting a credential field is
    caught exactly the same way as a first-attempt one, not just on the happy path."""
    wrong = {"steps": [{"do": "open_url", "url": "https://example.com/login"},
                       {"do": "expect", "element": "Sign in"}, {"do": "done", "say": "x"}]}  # fails: no "Sign in" on screen
    fixed = {"steps": [{"do": "find", "what": "Email or username", "typeable": True,
                        "then": "set_text", "text": "daryl@example.com"},
                       {"do": "done", "say": "Logged in."}]}
    login_page = [{"id": "e9", "role": "textfield", "label": "Email or username", "typeable": True}]
    hands = SimHands(pages={"https://example.com/login": login_page}, world=SAFARI_FRONT)
    p, _ = planner(hands, PlanGroq(wrong, fixed))
    r = await p.run("log into example.com")
    assert not r.ok and r.ask
    assert not any(op == "set_text" for op, _ in hands.calls)


async def test_done_without_any_expect_is_not_marked_verified():
    """P0 #3: 'ok=True without evidence' -- a plan that runs a fixed action and immediately says
    done, with no expect step anywhere, must not be marked verified even though it still reports
    success. Outcome.verified distinguishes a checked completion from an assumed one; what to say
    about that distinction is a caller's decision, not baked into the spoken text here."""
    plan = {"steps": [{"do": "action", "name": "wifi", "args": {"on": "on"}}, {"do": "done", "say": "Wi-Fi's on."}]}
    hands = SimHands(world=SAFARI_FRONT)
    p, _ = planner(hands, PlanGroq(plan))
    r = await p.run("turn wifi on")
    assert r.ok is True
    assert r.verified is False


async def test_done_after_a_passed_expect_is_verified():
    plan = {"steps": [{"do": "open_url", "url": "https://example.com/settings"},
                      {"do": "expect", "element": "Bluetooth"}, {"do": "done", "say": "Done."}]}
    page = [{"id": "e1", "label": "Bluetooth", "role": "text"}]
    hands = SimHands(pages={"https://example.com/settings": page}, world=SAFARI_FRONT)
    p, _ = planner(hands, PlanGroq(plan))
    r = await p.run("open settings and check bluetooth is there")
    assert r.ok is True
    assert r.verified is True


async def test_an_unchecked_action_after_a_passed_expect_is_not_marked_verified():
    """Code review I4: 'checked' never reset once True, so an expect early in the plan kept
    verified=True forever, even for a later action (a press) whose actual result was never
    re-checked. A plan of open_url -> expect(ok) -> find+press -> done wrongly reported the FINAL
    press as verified when nothing confirmed the press actually worked."""
    plan = {"steps": [{"do": "open_url", "url": "https://example.com/settings"},
                      {"do": "expect", "element": "Bluetooth"},
                      {"do": "find", "what": "Bluetooth", "then": "press"},
                      {"do": "done", "say": "Turned it on."}]}
    page = [{"id": "e1", "label": "Bluetooth", "role": "button"}]
    hands = SimHands(pages={"https://example.com/settings": page}, world=SAFARI_FRONT)
    p, _ = planner(hands, PlanGroq(plan))
    r = await p.run("open settings and turn on bluetooth")
    assert r.ok is True
    assert r.verified is False  # the press after the expect was never itself re-checked


async def test_a_read_result_counts_as_verified_evidence():
    """Reading the screen and reporting what's actually there IS evidence -- unlike a bare
    'done' with no check, this isn't an assumption."""
    plan = {"steps": [{"do": "activate", "app": "Calculator"}, {"do": "read", "what": "the result shown"}]}
    hands = SimHands(apps={"Calculator": [{"id": "a1", "label": "42", "role": "text"}]},
                     world={"front_app": "Calculator", "apps": ["Calculator"], "windows": [], "tabs": [], "selected": ""})
    p, _ = planner(hands, PlanGroq(plan, text="It says 42."))
    r = await p.run("what does the calculator show")
    assert r.ok is True
    assert r.verified is True


async def test_state_reads_and_parses_the_new_state_op():
    """P1-A wiring: Planner._state() calls the new 'state' hands op and parses it with
    ComputerState.from_data() -- mirrors _world()'s existing pattern exactly."""
    from evie.computer.state import ComputerState
    raw = {"version": 5, "ts": 100.0, "displays": [{"id": "d1", "builtin": True, "frame": [0, 0, 100, 100]}],
           "windows": [], "front_app": "Safari", "front_element": None, "tabs": []}
    hands = SimHands(world=SAFARI_FRONT)
    hands.state_response = json.dumps(raw)
    p, _ = planner(hands, PlanGroq())
    s = await p._state()
    assert isinstance(s, ComputerState) and s.version == 5 and s.front_app == "Safari"


async def test_state_falls_back_to_empty_on_a_failed_op():
    """Same fallback-on-failure pattern as _world(): a failed state op never crashes the
    planner, it just returns an empty ComputerState."""
    from evie.computer.state import ComputerState
    hands = SimHands(world=SAFARI_FRONT)
    hands.state_ok = False
    p, _ = planner(hands, PlanGroq())
    s = await p._state()
    assert isinstance(s, ComputerState) and s.version == 0


async def test_find_uses_perception_choose_source_not_a_duplicate_threshold():
    """Once perception.py exists, _find must call it rather than keep its own inline
    VISION_BELOW check -- two independent copies of the same threshold could silently drift.
    This patches perception.choose_source and confirms _find actually calls through to it."""
    from unittest.mock import patch
    from evie.computer.perception import PerceptionSource
    hands = SimHands(apps={"Notion": [{"id": "a1", "role": "button", "label": "New"}]},
                     world={"front_app": "Notion", "apps": ["Notion"], "windows": [], "tabs": [], "selected": ""})
    plan = {"steps": [{"do": "find", "what": "Search", "then": "press"}, {"do": "done", "say": "x"}]}
    p, _ = planner(hands, PlanGroq(plan, {"steps": []}, {"steps": []}))
    with patch("evie.computer.planner.choose_source", return_value=PerceptionSource.STRUCTURED) as mock:
        await p.run("open notion and search")
        assert mock.called
        assert mock.call_args.args[0] == "open notion and search"  # the goal, not the bare target


TWO_DISPLAYS_STATE = json.dumps({
    "version": 1, "ts": 100.0, "front_element": None,
    "displays": [{"id": "display-1", "builtin": True, "frame": [2048, 35, 1512, 982]},
                {"id": "display-3", "builtin": False, "frame": [0, 0, 2048, 1152]}],
    "windows": [{"app": "Notion", "title": "", "frame": [0, 0, 1000, 800], "display": "display-3", "focused": False}],
    "front_app": "Finder", "tabs": []})


async def test_autonomous_work_moves_to_evies_own_display_when_two_displays_exist():
    """workspace.py's spec default: 'evie_private' for ordinary autonomous work -- Evie's own
    MacBook display, not wherever the window happened to open. With two displays and no
    'show me' in the goal, opening Notion should trigger a place_window call onto the builtin
    display, since Notion's window is currently on the external one."""
    hands = SimHands(apps={"Notion": [{"id": "a1", "role": "button", "label": "New"}]},
                     world={"front_app": "Finder", "apps": ["Notion"], "windows": [{"app": "Notion", "title": ""}],
                           "tabs": [], "selected": ""})
    hands.state_response = TWO_DISPLAYS_STATE
    plan = {"steps": [{"do": "done", "say": "Opened Notion."}]}
    p, _ = planner(hands, PlanGroq(plan))
    await p.run("open notion and make a note")
    place_calls = [a for op, a in hands.calls if op == "place_window"]
    assert place_calls == [{"app": "Notion", "display_id": "display-1"}]


async def test_show_me_keeps_work_on_isaacs_display_not_evies():
    """'show me' -> isaac_visible: assign_display returns the EXTERNAL display for this policy,
    so no place_window call should target the builtin display -- and since Notion's window is
    already on the external display, no move is needed at all."""
    hands = SimHands(apps={"Notion": [{"id": "a1", "role": "button", "label": "New"}]},
                     world={"front_app": "Finder", "apps": ["Notion"], "windows": [{"app": "Notion", "title": ""}],
                           "tabs": [], "selected": ""})
    hands.state_response = TWO_DISPLAYS_STATE
    plan = {"steps": [{"do": "done", "say": "Opened Notion."}]}
    p, _ = planner(hands, PlanGroq(plan))
    await p.run("open notion and show me the note")
    place_calls = [a for op, a in hands.calls if op == "place_window"]
    assert place_calls == []  # already on the external display -- no move needed


async def test_display_assignment_waits_until_after_go_to_for_a_cold_launched_app():
    """Live bug found while wiring this in: a not-yet-running app has no window in ComputerState
    yet at the point world.resolve() returns its Target -- _go_to's own activate() is what
    launches it and waits for the window. Reading state before _go_to (the plan's first draft)
    meant a cold launch's place_window always got 'no window to move', silently skipping the
    move it should have made. This proves _state() is called (for assign_workspace's decision)
    only after activate has already run, by asserting the "activate" call precedes "state" in
    the hands call log."""
    hands = SimHands(apps={"Notion": [{"id": "a1", "role": "button", "label": "New"}]},
                     world={"front_app": "Finder", "apps": [], "windows": [], "tabs": [], "selected": ""})
    hands.state_response = TWO_DISPLAYS_STATE
    plan = {"steps": [{"do": "done", "say": "Opened Notion."}]}
    p, _ = planner(hands, PlanGroq(plan))
    await p.run("open notion and make a note")
    ops = [op for op, _ in hands.calls]
    assert ops.index("activate") < ops.index("state")


async def test_single_display_never_calls_place_window():
    """assign_display returns None with only one display connected (single-display mode) --
    no place_window call should happen at all."""
    hands = SimHands(apps={"Notion": [{"id": "a1", "role": "button", "label": "New"}]},
                     world={"front_app": "Finder", "apps": ["Notion"], "windows": [{"app": "Notion", "title": ""}],
                           "tabs": [], "selected": ""})
    hands.state_response = json.dumps({"version": 1, "ts": 100.0, "front_element": None,
                                       "displays": [{"id": "display-1", "builtin": True, "frame": [0, 0, 1512, 982]}],
                                       "windows": [], "front_app": "Finder", "tabs": []})
    plan = {"steps": [{"do": "done", "say": "Opened Notion."}]}
    p, _ = planner(hands, PlanGroq(plan))
    await p.run("open notion and make a note")
    assert [a for op, a in hands.calls if op == "place_window"] == []
