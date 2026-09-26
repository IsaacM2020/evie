"""Phase 6 P2: a visual fallback for when the Accessibility tree / DOM (Planner's normal, primary
way of seeing the screen) comes back with nothing — Isaac's case: he's on a PDF, Accessibility
exposes no elements, and Evie needs to see the screen some other way.

Only after structured methods have already failed, and only once (see the one call site in
Planner.run(): right where it's about to give up and hand off to Claude Code, not on every look).
No screenshot is ever the default path.

Honest limit of this file: it asks the app for a screenshot and expects a plain-text description
back. Taking the screenshot needs Screen Recording permission and a Swift-side handler for a
`screenshot` `do` op, and turning it into a description needs a vision-capable model call — Evie's
existing models (Jev, Groq) are text-only. Neither exists yet; this module is the decision logic
and the plumbing, wired so that whenever a future session adds that Swift handler and a real
describe step, Planner already knows when to reach for it. Until then hands.do("screenshot", ...)
simply fails and describe() returns None, exactly like any other hands op nothing implements yet.
"""
import logging

from evie.computer.observe import Screen

log = logging.getLogger("evie.computer")


def needs_visual_fallback(screen: Screen | None) -> bool:
    """True only when structured reading has already run and genuinely found nothing to act on."""
    return screen is not None and not screen.ids


class VisionFallback:
    def __init__(self, hands, describe_image=None):
        self._hands = hands
        # Swapped in once a real vision model is wired; None keeps this a safe no-op until then.
        self._describe_image = describe_image

    async def describe(self, app: str) -> str | None:
        try:
            r = await self._hands.do("screenshot", app=app, timeout=8.0)
        except Exception:
            log.info("visual fallback: screenshot request failed")
            return None
        if not r.ok:
            log.info("visual fallback: no screenshot available (%s)", r.detail)
            return None
        image = r.data.get("image")
        if not image:
            return None
        if self._describe_image is None:
            log.info("visual fallback: got a screenshot but no vision model is wired to read it")
            return None
        try:
            return await self._describe_image(image)
        except Exception:
            log.exception("visual fallback: describing the screenshot failed")
            return None
