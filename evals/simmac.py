"""Task 8 (Phase 2 follow-up plan, spec §27): a stateful pretend Mac for eval outcomes
evals/sim.py's SimHands can't express. SimHands is deliberately stateless and per-task-fresh --
fine for the 36 existing hands-eval cases, but §27 also asks for: a stale element id rejected the
way the real Swift press()/set_text() reject one (Eyes.swift:367/390), place_window (added to the
real Swift side in Task 4 of this plan but never given a SimHands handler), a native dialog that
blocks interaction with the app underneath it until dismissed, and filesystem state that persists
across calls within one task (real Finder ops run as applescript against the real disk; SimHands's
script_out is a fixed string, not state).

SimMac extends SimHands rather than replacing it: every existing SimHands behavior (used by the 36
cases already in evals/computer/tasks.py) is unchanged, since real-app behavior for ops SimMac
doesn't override still goes through SimHands.do()."""
from evie.hands import HandsResult
from evals.sim import SimHands


class SimMac(SimHands):
    def __init__(self, *a, files: set[str] | None = None, **kw):
        super().__init__(*a, **kw)
        self.files = files if files is not None else set()
        self._dialog: str | None = None
        self._delay: dict[str, tuple[int, list[dict]]] = {}  # app -> (calls left holding, final elements)
        self._delay_seen: dict[str, int] = {}

    def show_dialog(self, text: str) -> None:
        self._dialog = text

    def dismiss_dialog(self) -> None:
        self._dialog = None

    def delay_screens(self, app: str, n: int, final_elements: list[dict]) -> None:
        """The first n observe()/find() calls against `app` see an empty screen; from the n+1th
        call on, they see `final_elements`. Models a slow-loading dialog or pane."""
        self._delay[app] = (n, final_elements)
        self._delay_seen[app] = 0

    async def do(self, op, timeout=5.0, **a):
        if op == "observe":
            app = a.get("app")
            if app in self._delay:
                n, final = self._delay[app]
                seen = self._delay_seen[app]
                self._delay_seen[app] = seen + 1
                if seen < n:
                    self.calls.append((op, a))
                    self.focus = app if app != "Safari" else "web"
                    self.snap += 1
                    return HandsResult(True, "ok", {"snapshot": f"s{self.snap}", "app": app, "kind": "app",
                                                    "window": app, "elements": "[]"})
                self.apps[app] = final
        if op in ("press", "set_text"):
            self.calls.append((op, a))
            if self._dialog is not None:
                return HandsResult(False, f"a dialog is in the way: {self._dialog!r}")
            if a.get("snapshot") != self._el_snapshot():
                return HandsResult(False, "the screen changed, look again")
            return await self._do_inner(op, **a)
        if op == "place_window":
            self.calls.append((op, a))
            return HandsResult(True, "moved", {"app": a.get("app"), "display_id": a.get("display_id")})
        if op == "applescript":
            self._apply_filesystem_effect(a.get("source", ""))
            return await super().do(op, timeout=timeout, **a)
        return await super().do(op, timeout=timeout, **a)

    def _el_snapshot(self) -> str:
        """The snapshot id the LAST observe() call handed out -- press/set_text must be called
        with exactly that id, matching Eyes.swift's `guard snap == snapshot`."""
        return f"s{self.snap}"

    async def _do_inner(self, op, **a):
        """Runs the real SimHands press/set_text logic once snapshot+dialog checks pass, without
        re-appending to self.calls (already appended above so the dialog/stale-snapshot rejection
        itself is visible in the call log too)."""
        self.calls.pop()  # SimHands.do() will append its own copy; avoid double-logging
        return await super().do(op, **a)

    def _apply_filesystem_effect(self, source: str) -> None:
        """A tiny best-effort reading of the fixed AppleScript templates cards.py's finder_*
        actions render (see src/evie/computer/cards.py ACTIONS) -- not a general AppleScript
        interpreter, just enough for finder_trash/finder_new_folder to have a visible effect on
        self.files so a later finder_find in the same task sees it."""
        if "delete" in source and "POSIX file" in source:
            for f in list(self.files):
                if f in source:
                    self.files.discard(f)
        if "make new folder" in source:
            import re
            m = re.search(r'name:"([^"]+)"', source)
            if m:
                self.files.add(m.group(1))
