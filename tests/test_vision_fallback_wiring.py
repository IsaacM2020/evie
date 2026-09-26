"""Phase 6 P2: the one call site in Planner.run() — only reached once structured reads have
already failed MAX_REPLANS times, exactly Isaac's "on a PDF, Accessibility gives nothing" case."""
import json

from evie.computer.planner import Planner
from evie.computer.vision_fallback import VisionFallback
from evie.countdown import Countdown
from evie.hands import HandsResult
from evals.sim import SimHands
from tests.test_planner import PickJev, PlanGroq

PREVIEW_WORLD = {"front_app": "Preview", "apps": ["Preview"], "windows": [{"app": "Preview", "title": "chem_ia.pdf"}],
                 "tabs": [], "selected": ""}


class ScreenshotSimHands(SimHands):
    """Neither SimHands nor the real evie.hands.Hands understands "screenshot" yet — that's the
    Swift-side gap noted in evie.computer.vision_fallback. This local stand-in is only for
    proving the Planner <-> VisionFallback wiring; the other tests here use plain SimHands, which
    correctly shows today's real, honest behaviour: a graceful no-op."""
    async def do(self, op, timeout=5.0, **a):
        if op == "screenshot":
            self.calls.append((op, a))
            return HandsResult(True, "ok", {"image": "b64-fake-screenshot"})
        return await super().do(op, timeout, **a)


def make_planner(hands, groq, vision=None):
    cd = Countdown(seconds=0.02)
    p = Planner(hands, groq, PickJev(), cd, say=lambda t: None, settle_s=0, window_s=cd.seconds, vision=vision)
    p.EXPECT_S, p._expect_poll = 0.05, 0.01
    return p


async def test_stuck_on_an_empty_screen_asks_the_vision_fallback_and_includes_it_in_tried():
    async def describe_image(img):
        return "A PDF with a Print button top right."

    bad = {"steps": [{"do": "find", "what": "the print button"}]}
    hands = ScreenshotSimHands(apps={}, world=PREVIEW_WORLD)  # Preview isn't in `apps`: empty once activated
    vision = VisionFallback(hands, describe_image=describe_image)
    p = make_planner(hands, PlanGroq(bad, bad, bad), vision=vision)
    r = await p.run("print this pdf")
    assert not r.ok and r.stuck
    assert "screen looked like: A PDF with a Print button top right." in r.tried


async def test_no_vision_wired_behaves_exactly_as_before():
    bad = {"steps": [{"do": "find", "what": "the print button"}]}
    hands = SimHands(apps={}, world=PREVIEW_WORLD)
    p = make_planner(hands, PlanGroq(bad, bad, bad), vision=None)
    r = await p.run("print this pdf")
    assert not r.ok and r.stuck and "screen looked like" not in r.tried


async def test_vision_is_not_consulted_when_the_screen_actually_has_elements():
    """The fallback only fires once structured reading has genuinely found nothing — not for an
    ordinary wrong guess, which is what evals/computer/tasks.py already covers 35 ways."""
    bad = {"steps": [{"do": "expect", "url_contains": "/nope"}]}
    hands = SimHands(pages={"https://www.google.com/": [{"id": "w1", "role": "link", "label": "something"}]},
                     world={"front_app": "Safari", "apps": ["Safari"],
                            "windows": [{"app": "Safari", "title": "Google"}],
                            "tabs": [{"window": 11, "order": 1, "index": 1, "current": True, "title": "Google",
                                     "url": "https://www.google.com/"}], "selected": ""})
    calls = []

    async def describe_image(img):
        calls.append(img)
        return "shouldn't be used"

    vision = VisionFallback(hands, describe_image=describe_image)
    p = make_planner(hands, PlanGroq(bad, bad, bad), vision=vision)
    r = await p.run("do something odd")
    assert not r.ok and r.stuck and calls == []
