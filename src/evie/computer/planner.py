"""Planner v2 (Phase 3c): plan once, find in code, pick with Jev, replan only when a check fails.

  1. world   one read of what's on screen (evie/computer/world.py) -> WHERE to work
  2. plan    ONE Groq call (gpt-oss-120b, low reasoning) sees the goal, what's open, the app's card
             (evie/computer/cards.py) and the current screen, and writes the whole route as steps
  3. run     code runs the steps: direct ones (open_url, key, menu, action) straight away; `find`
             by plain code when one element clearly matches, else Jev chooses among the real ids;
             `pick` (which video? which article?) is always Jev choosing among the real rows
  4. check   `expect` steps look at the screen again; a miss means ONE replan with that screen
             (at most 2), then the Brain hands the goal to Claude Code.

Every press is an id from the screen just read. Risky steps (send, post, buy, delete) are read
back with 3 s to say stop, and in an app Evie has no card for, she asks first.
"""
import asyncio
import time
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Callable

from evie.computer.cards import ACTIONS, CARDS, card_for, render_action
from evie.computer.find import _BADGE, candidates, find_in_code, pick_pool
from evie.computer.observe import Screen
from evie.computer.safety import is_risky
from evie.computer.world import Target, World
from evie.countdown import Countdown
from evie.jev import JevError
from evie.talk import TalkError

log = logging.getLogger("evie.computer")

MODEL = "openai/gpt-oss-120b"
# Each Groq model has its own 8k tokens/minute (on-demand tier, 2026-09-24): a rate-limited plan
# moves down this list instead of failing.
FALLBACKS = ["openai/gpt-oss-20b", "qwen/qwen3.8-27b"]
MAX_REPLANS = 2
MAX_STEPS = 20
LONG_STEPS = 3  # plans with this many doing steps take long enough to mention "say stop"
VISION_BELOW = 5  # an app showing fewer labelled elements than this gets looked at

SYSTEM = """You plan tasks on Isaac's Mac for his assistant Evie. Write the WHOLE route to the goal as steps, in one go,
using what's open, the app guide and the screen shown. Reply with one JSON object:
{"understood": "what you're about to do, as Evie would say it out loud, 3-9 words starting with a verb like "Opening" or "Finding"",
 "steps": [ ... ]}.
Steps (each an object with "do"):
- {"do":"open_url","url":"https://...","same_tab":false}   open a page (a new tab unless same_tab)
- {"do":"find","what":"words on the element","role":"tab|button|link|input|...","href":"part of its link",
   "typeable":true, "then":"press|set_text", "text":"what to type", "submit":true, "risky":false, "say":"read-back"}
- {"do":"pick","among":"videos|articles|channels|results|rows","want":"what Isaac wants","then":"press|read","ask":false}
- {"do":"key","combo":"cmd+t"}   {"do":"menu","path":"File > New"}   {"do":"activate","app":"Notes"}
- {"do":"action","name":"<action from the app guide>","args":{...}}
- {"do":"read","what":"what to find out or summarise"}     reads the page/window and answers Isaac
- {"do":"expect","url_contains":"..."} or {"do":"expect","element":"words on something that must now be there"}
- {"do":"message","to":"who","body":"exact words","via":"whatsapp|imessage"}   sending always uses this
- {"do":"ask","say":"a short question"}   only when Isaac's goal is truly unclear
- {"do":"done","say":"..."}   Evie's short closing line about what she did ("Playing {picked}." for a video,
   "Wi-Fi's off.", "Your Downloads are open."); {picked} becomes the name of what was picked
Rules: when the app guide has an action for the task, use that one step, never clicks. open_url already opens a
new tab (never key cmd+t before it). Use read only when Isaac wants to know or hear something. A message step does the
whole send by itself (finds the person, opens the chat, reads it back): use it alone. Otherwise prefer direct
addresses over clicking. After a page change put an expect that proves it worked (never after an action or message). "Newest" on a channel's Videos page is the first video. When several could match, use pick and choose the
best: don't ask. But when Isaac names a creator, site or list and NOT which item ("a mrbeast video", "a video by
parrot", "something on netflix"), open the list and set "ask": true on the pick: Evie shows him the top ones and asks
there. With newest, latest, most interesting, about X or any other hint, choose (ask false). Mark "risky": true on any step that sends, posts, buys, deletes or submits for Isaac, with a "say"
read-back. Never type passwords or pay. Isaac's words came from speech-to-text and may contain misheard words:
read them for what he most likely meant. End with done."""


_SEND_KEYS = {"return", "enter", "cmd+return", "cmd+enter", "shift+cmd+d", "cmd+shift+d"}
MESSAGING_APPS = {"WhatsApp", "Messages", "Mail", "Slack", "Discord", "Telegram", "Microsoft Teams", "Signal"}
_DOING = {"open_url", "find", "pick", "key", "menu", "action", "message", "activate"}

# "a mrbeast video", "a video by networkchuck", "a bbc article": a creator or site but not WHICH one. She opens the
# list and asks (Isaac, 2026-09-24). Any hint ("newest", "about solar", "that explains...") means she picks instead.
_VAGUE_ITEM = re.compile(r"\b(a|an|any|some)\s+(\S+\s+){0,2}(videos?|vids?|articles?|stor(y|ies)|episodes?|posts?)\b",
                         re.I)
_HINT = re.compile(r"\b(newest|latest|recent|new one|last|first|most|best|top|popular|funniest|about|called|named|"
                   r"titled|that|which|where|explain\w*|on how|how to|from (yesterday|today|last))\b", re.I)


_ROW_WORDS = {"newest": 0, "latest": 0, "most recent": 0, "first": 0, "1st": 0, "top": 0, "1": 0,
              "second": 1, "2nd": 1, "2": 1, "middle": 1, "third": 2, "3rd": 2, "3": 2}
_FILLER = {"the", "please", "evie", "play", "open", "that", "video", "article", "it", "uh", "um"}


def ordinal_row(answer: str, n: int) -> int | None:
    """"the newest one" / "the second one" / "number 3" -> a row index, worked out without a model.
    "the last one" is left to Jev (last in the list, or latest?)."""
    a = re.sub(r"[^a-z0-9 ]", " ", answer.lower())
    for word, digit in (("one", "1"), ("two", "2"), ("three", "3")):
        a = re.sub(rf"\bnumber {word}\b", digit, a)
    a = re.sub(r"\bnumber\b", " ", a)
    words = [w for w in a.split() if w not in _FILLER]
    if len(words) > 1 and words[-1] == "one":
        words = words[:-1]
    i = _ROW_WORDS.get(" ".join(words))
    return i if i is not None and i < n else None


def vague_pick(goal: str) -> bool:
    m = _VAGUE_ITEM.search(goal)
    return bool(m) and not _HINT.search(goal[m.start():])


# Isaac asked to KNOW something (so a read step's answer is what she says).
_WANTS_ANSWER = re.compile(r"\?|\b(what|what's|whats|who|which|when|where|how|why|any|anything|summari[sz]e|read|"
                           r"tell me|is there|are there|do i|did|does|explain|check)\b", re.I)


@dataclass
class Outcome:
    ok: bool
    said: str
    ask: bool = False
    stuck: bool = False  # couldn't do it on screen: the Brain may hand it to Claude Code
    options: list[dict] = field(default_factory=list)  # "Which one?": the rows shown to Isaac
    pick: dict | None = None  # what Planner.choose needs to finish once he answers
    tried: str = ""  # stuck: the steps it took and what failed, so Claude Code doesn't start blind


class _Fail(Exception):
    """A step didn't work: replan with what the screen shows now."""


class _Ask(Exception):
    def __init__(self, say: str):
        super().__init__(say)
        self.say = say


class _AskPick(Exception):
    """The list is open; Isaac says which one (Planner.choose finishes it)."""

    def __init__(self, say: str, options: list[dict], pick: dict):
        super().__init__(say)
        self.say, self.options, self.pick = say, options, pick


ASK_ROWS = 3


def _which_line(rows: list[dict]) -> str:
    names = [_short(r.get("label", "")) for r in rows]
    listed = ", ".join(names[:-1]) + f", or {names[-1]}" if len(names) > 2 else " or ".join(names)
    return f"Which one? {listed}."


def _short(label: str, n: int = 48) -> str:
    label = " ".join(label.split())
    return label if len(label) <= n else label[: n - 1].rsplit(" ", 1)[0] + "…"


class Planner:
    EXPECT_S = 6.0  # a page still loading gets this long before an expect step fails
    _expect_poll = 0.4
    WINDOW_S = 8.0  # a just-launched app gets this long to open its window
    _window_poll = 0.5

    def __init__(self, hands, groq, jev, countdown: Countdown, say: Callable[[str], None], settle_s: float = 0.5,
                 window_s: float = 3.0, show_work: Callable[[], bool] = lambda: True,
                 progress: Callable[[str], None] | None = None, messages=None, talker=None):
        self._hands, self._groq, self._jev, self._countdown, self._say = hands, groq, jev, countdown, say
        self._settle, self._window, self._show_work = settle_s, window_s, show_work
        self._progress = progress or (lambda _t: None)
        self._messages, self._talker = messages, talker
        self._rate_wait = 8.0  # seconds to wait when every planner model hit its per-minute limit

    # -- the run --------------------------------------------------------------------------------
    async def run(self, goal: str, app: str | None = None) -> Outcome:
        self._goal, self._picked, self._history = goal, "", []
        self._did, self._last_say = False, ""  # something was actually done (across replans)
        self._typed = False
        self._screen: Screen | None = None
        self._new_tab_done = False
        self._alt: dict | None = None  # after she picks by herself: her runners-up, for "no, the other one"
        world = await self._world()
        target = world.resolve(goal)
        if app and target.kind in ("app", "new_tab") and app != target.app and target.kind == "app":
            target = Target("app", app, running=app in world.apps, bring_front=True, why="asked for")
        if target.kind == "choose":
            target = await self._choose_tab(goal, target)
        self._target, self._world_now = target, world
        await self._go_to(target)
        self._understood = ""
        steps = await self._plan(first=True)
        if self._understood:  # what she's about to do, said as soon as she knows (a long one can be stopped)
            long = sum(1 for st in steps if st.get("do") not in ("expect", "done")) >= LONG_STEPS
            self._say(f"{self._understood}. Say stop if that's wrong." if long else f"{self._understood}.")
        replans = 0
        while True:
            try:
                return await self._run_steps(steps)
            except _AskPick as a:
                return Outcome(False, a.say, ask=True, options=a.options, pick=a.pick)
            except _Ask as a:
                return Outcome(False, a.say, ask=True)
            except _Fail as f:
                self._history.append(f"FAILED: {f}")
                log.info("computer step failed (%s), replan %d", f, replans + 1)
                if replans >= MAX_REPLANS:
                    log.info("computer goal stuck: %s | %s", goal, " / ".join(self._history[-6:]))
                    return Outcome(False, "I got stuck doing that on screen.", stuck=True,
                                   tried=" / ".join(self._history[-8:]))
                replans += 1
                await self._look()
                steps = await self._plan(first=False)

    async def _run_steps(self, steps: list[dict]) -> Outcome:
        if not steps:
            raise _Fail("the plan was empty")
        prev, did = None, False
        self._steps = steps
        for st in steps[:MAX_STEPS]:
            do = st.get("do")
            if do == "read" and not _WANTS_ANSWER.search(self._goal):
                continue  # he asked to open or do something, not to hear about it
            if do == "expect" and prev == "action":
                prev = do  # a fixed script reports its own success; what it changed may not be on this screen
                continue
            prev = do
            self._progress(_describe(st))
            if do == "done":
                if not did and not self._did:
                    raise _Fail("the plan stopped before doing anything")
                say = str(st.get("say") or "")
                if "spoken sentence" in say or "the label of" in say:  # the model copied the prompt's example
                    say = ""
                return Outcome(True, (say or self._closing()).replace("{picked}", self._picked), pick=self._alt)
            if do == "ask":
                raise _Ask(str(st.get("say") or "What exactly should I do?"))
            said = await self._step(do, st)
            did = did or do in _DOING
            if said is not None:  # read / action results end the task with what she found
                return Outcome(True, said)
        if not did and not self._did:
            raise _Fail("the plan stopped before doing anything")
        return Outcome(True, self._closing(), pick=self._alt)

    def _closing(self) -> str:
        if self._last_say:
            return self._last_say
        return f"Opened {self._picked}." if self._picked else "Done."

    # -- one step ---------------------------------------------------------------------------------
    async def _step(self, do: str, st: dict) -> str | None:
        if do == "open_url":
            url = str(st.get("url") or "")
            if url.startswith("x-apple.systempreferences:"):  # a Settings pane: the safe, fixed action
                return await self._action({"name": "settings_open",
                                           "args": {"pane": url.removeprefix("x-apple.systempreferences:")}})
            if not url.startswith(("https://", "http://")):
                raise _Fail(f"not a web address: {url!r}")
            new_tab = not st.get("same_tab") and not self._new_tab_done and self._target.kind != "tab"
            r = await self._hands.do("open_url", url=url, app="Safari", new_tab=new_tab, window=self._target.window,
                                     front=self._front())
            self._new_tab_done = True
            self._screen = None
            self._check(r, f"open {url}")
            self._did = True
            await self._wait_page("")
            self._history.append(f"opened {url}")
        elif do == "expect":
            # A page that's still loading isn't a wrong page: look again for up to EXPECT_S before failing
            # (2026-09-24 18:30: a new tab read about:blank, 3 replans in 3 s, then Claude Code for 2 min).
            deadline = time.monotonic() + self.EXPECT_S
            while True:
                await self._look()
                problem = self._expect_problem(st)
                if problem is None:
                    break
                if time.monotonic() >= deadline:
                    raise _Fail(problem)
                await asyncio.sleep(self._expect_poll)
            self._history.append("checked: ok")
        elif do == "find":
            await self._fresh()
            el = await self._find(st)
            await self._act(st, el)
        elif do == "pick":
            await self._fresh()
            if st.get("ask") or vague_pick(self._goal):
                self._ask_which(st)
            el = await self._pick(st)
            if st.get("then", "press") == "read" and _WANTS_ANSWER.search(self._goal):
                return await self._read(f"{st.get('want')}: {el.get('label')}")
            await self._act({**st, "then": "press"}, el)
        elif do == "key":
            combo = str(st.get("combo") or "").lower().replace(" ", "")
            if combo in _SEND_KEYS and (self._typed or self._app() in MESSAGING_APPS or st.get("risky")):
                # Return after typing into something sends it: same read-back and 3 s as a Send button.
                line = str(st.get("say") or "Sending what I typed").strip()
                self._say(f"{line.rstrip('.')}. Say stop to cancel.")
                if not await self._countdown.wait(self._window):
                    raise _Ask("Okay, I didn't do it.")
            self._screen = None
            r = await self._hands.do("key", combo=str(st.get("combo") or ""), app=self._app())
            self._check(r, f"key {st.get('combo')}")
            await self._settle_now()
        elif do == "menu":
            self._screen = None
            r = await self._hands.do("menu", path=str(st.get("path") or ""), app=self._app())
            self._check(r, f"menu {st.get('path')}")
            await self._settle_now()
        elif do == "activate":
            app = str(st.get("app") or self._app())
            self._screen = None
            self._check(await self._hands.do("activate", app=app), f"open {app}")
            self._target = Target("app", app, bring_front=True, why="activated")
            await self._settle_now()
        elif do == "action":
            return await self._action(st)
        elif do == "read":
            return await self._read(str(st.get("what") or "what's here"))
        elif do == "message":
            if self._messages is None:
                raise _Ask("I can't send messages from here yet.")
            out = await self._messages.send(self._goal, {"contact": st.get("to"), "body": st.get("body"),
                                                         "via": st.get("via")})
            return out.said
        elif do == "wait":
            await asyncio.sleep(min(3.0, float(st.get("s") or 1)))
        else:
            raise _Fail(f"unknown step {do!r}")
        return None

    # -- finding and choosing ----------------------------------------------------------------------
    async def _find(self, st: dict) -> dict:
        what = str(st.get("what") or "")
        typeable = True if st.get("typeable") or st.get("then") == "set_text" else None
        el, cands = find_in_code(self._screen, what, role=st.get("role"), href=st.get("href"), typeable=typeable)
        if el is not None:
            self._history.append(f"found {el.get('label')!r} by its name")
            return el
        labelled = [e for e in self._screen.elements if e.get("label")]
        if not self._web() and len(labelled) < VISION_BELOW:
            return await self._look_for(what)  # almost nothing readable: look at it instead
        if not cands:
            cands = candidates(self._screen, what)
        if not cands:
            raise _Fail(f"nothing on screen looks like {what!r}")
        try:
            return await self._jev_choose(f"Which of these on-screen items is: {what}?", cands, f"find {what!r}")
        except _Fail:
            if self._web():
                raise
            return await self._look_for(what)

    async def _look_for(self, what: str) -> dict:
        """The last resort before Claude Code: a screenshot with a numbered box over every element
        the app reported, and Qwen says which number. The number maps back to a real id."""
        r = await self._hands.do("marked_shot", timeout=8.0, app=self._app())
        if not r.ok or not hasattr(self._groq, "look"):
            raise _Fail(f"couldn't see {what!r} ({r.detail})")
        marks = json.loads(r.data.get("marks") or "{}")
        try:
            out = json.loads(await self._groq.look(
                f'Isaac asked: "{self._goal}". In this screenshot of {self._app()}, which numbered box is {what}? '
                'Answer in JSON: {"n": the number} or {"n": null} if none is.', str(r.data.get("png", ""))))
        except Exception as e:  # noqa: BLE001
            raise _Fail(f"couldn't look for {what!r}: {e}")
        eid = marks.get(str(out.get("n")))
        if not eid or eid not in self._screen.ids:
            raise _Fail(f"{what!r} isn't in the screenshot")
        self._history.append(f"saw {what!r} in the screenshot")
        return self._screen.get(eid)

    async def _pick(self, st: dict) -> dict:
        pool = pick_pool(self._screen, str(st.get("among") or ""))
        if not pool:
            raise _Fail(f"no {st.get('among')} on this page")
        el = await self._jev_choose(
            f"Isaac asked: \"{self._goal}\". Which one is {st.get('want')}? They're listed in page order "
            "(first = top of the page). Pick the best match.", pool[:12], f"pick {st.get('want')!r}")
        self._picked = el.get("label", "")
        others = [{k: r[k] for k in ("id", "label", "meta", "href") if r.get(k)} for r in pool[:4] if r["id"] != el["id"]]
        self._alt = self._pick_state({**st, "then": "press"}, others[:3]) if others else None
        return el

    def _ask_which(self, st: dict) -> None:
        """He didn't say which one: the list is on screen now, so ask with the top rows."""
        rows = pick_pool(self._screen, str(st.get("among") or ""))[:ASK_ROWS]
        if len(rows) < 2:
            return  # only one: nothing to ask
        shown = [{k: r[k] for k in ("id", "label", "meta", "href") if r.get(k)} for r in rows]
        done = next((s.get("say") for s in self._steps if s.get("do") == "done" and s.get("say")
                     and "spoken sentence" not in str(s.get("say"))), "")
        raise _AskPick(_which_line(rows), shown, self._pick_state(st, shown, done))

    def _pick_state(self, st: dict, rows: list[dict], done: str = "") -> dict:
        return {"goal": self._goal, "among": st.get("among"), "then": st.get("then", "press"), "rows": rows,
                "done": done, "target": self._target, "world": self._world_now, "url": self._screen.url}

    async def choose(self, pick: dict, answer: str | None, eid: str | None = None) -> Outcome:
        """Finish a "Which one?": his answer ("the latest one", "the island one") or a tap on a row.
        Only the rows he was shown count, found again on a fresh read by their link (the page may
        have re-rendered with new ids); no new plan."""
        self._goal = pick["goal"] + (f' (Isaac was shown some and chose: "{answer}")' if answer else "")
        self._picked, self._history, self._did, self._last_say, self._typed = "", [], True, "", False
        self._target, self._world_now, self._new_tab_done, self._steps = pick["target"], pick["world"], True, []
        try:
            await self._look()
            live = self._find_rows(pick["rows"])
            if not any(el for _, el in live) and pick.get("url") and self._screen.url != pick["url"]:
                # "no, the other one" from the video he's now watching: back to the list first
                r = await self._hands.do("open_url", url=pick["url"], app="Safari", new_tab=False,
                                         window=self._target.window, front=self._front())
                self._check(r, "go back to the list")
                await self._wait_page("")
                await self._look()
                live = self._find_rows(pick["rows"])
            found = [(row, el) for row, el in live if el is not None]
            if eid is not None:
                el = next((el for row, el in found if row["id"] == eid), None)
                if el is None:
                    raise _Fail("that one isn't on screen any more")
            elif found and answer and (i := ordinal_row(answer, len(pick["rows"]))) is not None \
                    and live[i][1] is not None:
                el = live[i][1]  # "the newest one", "the second one": no model needed
                self._history.append(f"row {i + 1} for {answer!r}")
            else:
                cands = [el for _, el in found] or pick_pool(self._screen, str(pick.get("among") or ""))[:12]
                if not cands:
                    raise _Fail(f"no {pick.get('among')} on this page")
                el = await self._jev_choose(
                    f'Evie showed Isaac these and asked which one. He answered: "{answer}". Which one does he '
                    'mean? They\'re in page order: "the latest", "the newest" or "the first" is #1.', cands,
                    f"choose {answer!r}")
            self._picked = el.get("label", "")
            if pick.get("then") == "read" and _WANTS_ANSWER.search(pick["goal"]):
                return Outcome(True, await self._read(f"{pick['goal']}: {self._picked}"))
            await self._act({"then": "press"}, el)
        except _Ask as a:
            return Outcome(False, a.say, ask=True)
        except _Fail as f:
            log.info("choosing %r failed: %s", answer or eid, f)
            return Outcome(False, "I couldn't open that one.", stuck=True)
        done = str(pick.get("done") or "")
        if "{picked}" not in done:  # say WHICH one: he chose it, so he should hear it was the right one
            video = re.search(r"video|vid|song|episode", str(pick.get("among") or "") + " " + pick["goal"], re.I)
            done = "Playing {picked}." if video else "Opened {picked}."
        return Outcome(True, done.replace("{picked}", self._picked))

    def _find_rows(self, rows: list[dict]) -> list[tuple[dict, dict | None]]:
        """The rows he was shown, on the screen just read: by link first (ids change on re-render)."""
        out = []
        for row in rows:
            same = [e for e in self._screen.elements if row.get("href") and e.get("href") == row["href"]]
            # a YouTube row has two links to one video: the thumbnail ("14:13 Now playing") and the title
            el = next((e for e in same if e.get("label") == row.get("label")), None) \
                or next((e for e in same if not _BADGE.match((e.get("label") or "").strip())), None) \
                or (same[0] if same else None) \
                or next((e for e in self._screen.elements if e.get("label") == row.get("label")), None)
            out.append((row, el))
        return out

    async def _jev_choose(self, instructions: str, cands: list[dict], what: str) -> dict:
        criteria = {c["id"]: (f"#{i + 1} " + (c.get("label") or "") + (f" ({c['meta']})" if c.get("meta") else ""))[:160]
                    for i, c in enumerate(cands)}
        try:
            res = await self._jev.ask(f"Goal: {self._goal}", {"el": {"type": "choice", "instructions": instructions,
                                                                     "criteria": criteria}})
            a = res.answers["el"]
        except (JevError, KeyError, TypeError) as e:
            raise _Fail(f"couldn't choose for {what}: {e}")
        cid = a.get("choice")
        if cid not in criteria or cid not in self._screen.ids:  # never anything that isn't on screen
            raise _Fail(f"{what}: the choice {cid!r} isn't on screen")
        if float(a.get("confidence", 0)) < 0.3:
            raise _Fail(f"{what}: not sure which one")
        el = self._screen.get(cid)
        self._history.append(f"chose {el.get('label')!r} for {what}")
        return el

    # -- acting ------------------------------------------------------------------------------------
    async def _act(self, st: dict, el: dict) -> None:
        then = st.get("then", "press")
        text = str(st.get("text") or "")
        op = "set_text" if then == "set_text" else "press"
        known = self._app() in CARDS
        if is_risky(op, el, text, flagged=bool(st.get("risky"))):
            if not known:
                what = el.get("label") or "that"
                raise _Ask(f"That's {what} in {self._app()}, and I can't undo it. Should I go ahead?")
            line = str(st.get("say") or f"About to press {el.get('label', '')}").strip()
            self._say(f"{line.rstrip('.')}. Say stop to cancel.")
            if not await self._countdown.wait(self._window):
                raise _Ask("Okay, I didn't do it.")
        before = self._screen.url if self._screen else ""
        args = {"id": el["id"], "snapshot": self._screen.snapshot}
        if op == "set_text":
            args |= {"text": text, "submit": bool(st.get("submit"))}
            # A Return after typing into a message box sends it (a search box is fine).
            self._typed = self._typed or self._app() in MESSAGING_APPS or is_risky("set_text", el, text)
        r = await self._hands.do(op, **args)
        self._check(r, f"{op} {el.get('label')!r}")
        self._did = True
        self._history.append(f"{op} {el.get('label')!r}")
        self._screen = None  # the screen changed: look again before the next step
        if self._web():
            await self._wait_page(before)
        else:
            await self._settle_now()

    async def _action(self, st: dict) -> str | None:
        try:
            a = render_action(str(st.get("name")), dict(st.get("args") or {}))
        except KeyError:
            raise _Fail(f"there's no action {st.get('name')!r}; the actions are: {', '.join(ACTIONS)}")
        except ValueError as e:
            raise _Fail(f"action {st.get('name')!r}: {e}")
        if a.risky:
            self._say(f"{(st.get('say') or a.say or 'About to do that').rstrip('.')}. Say stop to cancel.")
            if not await self._countdown.wait(self._window):
                raise _Ask("Okay, I didn't do it.")
        self._screen = None
        r = await self._hands.do("applescript", timeout=12.0, source=a.script)
        self._check(r, f"action {a.name}")
        self._history.append(f"did {a.name}")
        self._did, self._last_say = True, a.say
        if a.opens:  # the next steps happen in the app this brought up
            self._target = Target("app", a.opens, why=f"{a.name} opened it")
            await self._settle_now()
        if a.returns:
            return await self._answer(str(r.data.get("out", "")), f"the result of {a.name}")
        return None

    async def _read(self, what: str) -> str:
        if self._web():
            r = await self._hands.do("screen_info", page=True)
            text = str(r.data.get("page_text", "")) if r.ok else ""
        else:
            await self._look()
            text = "\n".join(f"{e.get('role')}: {e.get('label')} {e.get('value') or ''}" for e in self._screen.elements)
        return await self._answer(text, what)

    async def _answer(self, text: str, what: str) -> str:
        user = (f"Isaac asked: \"{self._goal}\". Find out: {what}. Answer in at most 2 short spoken sentences, "
                f"no markdown.\n\nWhat's on screen:\n{text[:6000]}")
        try:
            return (await self._groq.chat("You answer Isaac out loud from what's on his screen.", user, max_tokens=200)).strip()
        except Exception as e:  # noqa: BLE001 - a failed read is a failed step, never a crash
            raise _Fail(f"couldn't read it: {e}")

    # -- plumbing ----------------------------------------------------------------------------------
    async def _world(self) -> World:
        r = await self._hands.do("world", timeout=6.0)
        try:
            return World.from_data(json.loads(r.data.get("world") or "{}") if r.ok else {})
        except ValueError:
            return World.from_data({})

    async def _choose_tab(self, goal: str, target: Target) -> Target:
        crit = {f"t{i}": f"{t.title} ({t.host})" for i, t in enumerate(target.choices)}
        try:
            res = await self._jev.ask(f'Isaac said: "{goal}"', {"tab": {
                "type": "choice", "instructions": "Which of Isaac's open tabs does he mean?", "criteria": crit}})
            t = target.choices[int(res.answers["tab"]["choice"][1:])]
            return Target("tab", "Safari", t.window, t.index, bring_front=True, why=f"the tab '{t.title}'")
        except (JevError, KeyError, TypeError, ValueError, IndexError):
            t = target.choices[0]
            return Target("tab", "Safari", t.window, t.index, bring_front=True, why="best guess tab")

    async def _go_to(self, t: Target) -> None:
        front = self._front() or t.bring_front
        if t.kind == "tab":
            self._check(await self._hands.do("use_tab", window=t.window, index=t.index, front=front), "use that tab")
            if t.why.startswith("the tab"):  # he named this tab: switching to it can be the whole job
                self._did = True
            await self._look()
        elif t.kind == "app" and t.app:
            if not t.running or front:
                r = await self._hands.do("activate", app=t.app)
                if r.ok:
                    await self._settle_now()
            await self._look()

    def _expect_problem(self, st: dict) -> str | None:
        if st.get("url_contains") and st["url_contains"] not in (self._screen.url or ""):
            return f"expected the address to contain {st['url_contains']!r}, it's {self._screen.url!r}"
        if st.get("element"):
            el, _ = find_in_code(self._screen, str(st["element"]))
            if el is None and not any(str(st["element"]).lower() in (e.get("label") or "").lower()
                                      for e in self._screen.elements):
                return f"expected to see {st['element']!r}"
        return None

    async def _fresh(self) -> None:
        """Read the screen only if something changed it since the last read."""
        if self._screen is None:
            await self._look()

    async def _look(self) -> None:
        app = "Safari" if self._web() else self._app()
        seen = await self._hands.do("observe", timeout=8.0, app=app)
        deadline = time.monotonic() + self.WINDOW_S
        # An app that's still opening has no window for a moment (Notion, 2026-09-24 18:28:15): wait for it.
        while not seen.ok and "no window" in (seen.detail or "") and time.monotonic() < deadline:
            await asyncio.sleep(self._window_poll)
            seen = await self._hands.do("observe", timeout=8.0, app=app)
        if not seen.ok:
            raise _Fail(f"couldn't read {app}: {seen.detail}")
        self._screen = Screen.from_data(seen.data)

    async def _plan(self, first: bool) -> list[dict]:
        t = self._target
        where = {"tab": "his Safari tab", "new_tab": "a new Safari tab", "app": t.app}.get(t.kind, t.app)
        screen = self._screen.compact(limit=120) if self._screen else "(nothing yet: start by opening what's needed)"
        user = (f"Goal (Isaac's words, from speech-to-text, may contain misheard words): {self._goal}\n"
                f"Work in: {where} ({t.why})\n\nWhat's open:\n{self._world_now.summary()}\n\n"
                f"{card_for('Safari' if t.kind in ('tab', 'new_tab') else t.app, self._goal)}\n\n")
        if not first:
            user += "Steps so far: " + " / ".join(self._history[-10:]) + "\nThe plan went wrong. Plan the rest again.\n\n"
        user += f"Screen now:\n{screen}"
        out = None
        for attempt in range(2):
            try:
                out = json.loads(await self._groq.chat(SYSTEM, user, max_tokens=700, json_mode=True, model=MODEL,
                                                       reasoning="low", fallbacks=FALLBACKS))
                break
            except TalkError as e:
                if "rate limited" in str(e) and attempt == 0:
                    log.info("every planner model is rate limited: waiting %.0f s", self._rate_wait)
                    await asyncio.sleep(self._rate_wait)  # the per-minute budget refills
                    continue
                log.warning("plan call failed: %s", str(e)[:120])
                return []
            except Exception as e:  # noqa: BLE001 - a bad plan is a failed plan, never a crash
                log.warning("plan call failed: %s", str(e)[:120])
                return []
        if first and isinstance(out, dict) and out.get("understood"):
            u = re.sub(r"^[^A-Za-z]+", "", str(out["understood"]).strip()).rstrip(".")  # "-opening" (18:24:02)
            self._understood = u[:1].upper() + u[1:]
        steps = out.get("steps") if isinstance(out, dict) else None
        steps = [s for s in steps if isinstance(s, dict)] if isinstance(steps, list) else []
        log.info("plan (%s): %s", "first" if first else "replan", json.dumps(steps)[:900])
        return steps

    async def _wait_page(self, before: str) -> None:
        await self._hands.do("wait_page", timeout=10.0, from_url=before or "")

    async def _settle_now(self) -> None:
        if self._settle:
            await asyncio.sleep(self._settle)

    def _check(self, r, what: str) -> None:
        if not r.ok:
            raise _Fail(f"{what}: {r.detail}")

    def _web(self) -> bool:
        return self._target.kind in ("tab", "new_tab") or self._target.app == "Safari"

    def _app(self) -> str:
        return "Safari" if self._web() else (self._target.app or "")

    def _front(self) -> bool:
        return bool(self._show_work())


def _describe(st: dict) -> str:
    """The orb's line for a step."""
    do = st.get("do")
    return {"open_url": f"Opening {str(st.get('url', ''))[:60]}", "find": f"Finding {st.get('what', '')}",
            "pick": f"Choosing {st.get('want', '')}", "read": "Reading it", "expect": "Checking",
            "action": f"{str(st.get('name', '')).replace('_', ' ')}", "key": f"Pressing {st.get('combo', '')}",
            "message": "Messaging"}.get(do, "")
