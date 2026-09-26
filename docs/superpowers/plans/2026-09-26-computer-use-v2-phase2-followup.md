# Evie Computer Use V2 — Phase 2 Follow-Up (Swift Wiring + Live Integration) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **This plan requires a real Mac with a real, currently-running (or startable) Evie.app — it cannot be executed in a sandboxed/headless session.** Every task that touches `mac/EvieBar` needs `swift build` to succeed against the actual toolchain, and Task 5's live verification needs Isaac physically present to confirm Evie still behaves correctly by voice.

**Goal:** Wire the four pure-Python, currently-unwired design modules from the first Computer Use V2 increment (`computer/state.py`, `computer/workspace.py`, `computer/perception.py`, `computer/task.py`) into the live system, add the one new Swift op (`state`) those modules need real data from, and extend the SimMac eval harness with the ≥15 unseen outcomes the original spec's §27 calls for. This is the second half of the original `docs/superpowers/plans/2026-09-26-computer-use-v2.md` plan — read that document first for full context on what already shipped (all 6 P0 bugs, plus the four modules this plan wires in).

**Architecture:** Add a new Swift `state` op (parallel to the existing `world` op, not replacing it — `World.resolve()` keeps working exactly as it does today) that reports display geometry and per-window frames using APIs already proven in this codebase (`CGWindowListCopyWindowInfo` + `kCGWindowBounds`, used today by `markedShot()`). Wire `ComputerState.from_data()` to that op's output. Then, one call site at a time, have `Planner`/`Recipes`/`brain.py` start reading from the already-tested `perception.choose_source`, `safety.classify`, and `task.TaskRegistry` — each wiring step is small, individually tested against the live app, and reversible if it regresses anything.

**Tech Stack:** Swift (SwiftPM, no Xcode — `mac/build.sh`), Python 3.12, pytest, the existing `evals/sim.py` SimMac-precursor harness (to be extended into a real stateful SimMac).

**Spec:** `~/Downloads/Evie Computer Use V2 — Architecture.md` sections 3 (unified state), 6-7 (two-screen architecture, display policy), 22 (task identity), §27 (eval benchmark). Also `docs/superpowers/plans/2026-09-26-computer-use-v2.md` (the first-increment plan this one continues) and its ledger for prior rulings that still apply (e.g., `_pick`/`choose()` never get a `NO_MATCH_FLOOR` target; `verified` resets on every doing-step).

## Global Constraints

- **Never run `mac/build.sh` (or any command that kills/restarts EvieBar) while Isaac is mid-conversation with Evie** — it kills the currently-running app. Confirm with Isaac before each rebuild in Task 1 and Task 4.
- No rewrite of `World`, `Planner`, `Recipes`, or `brain.py`'s existing control flow — each wiring task adds a call to an already-tested pure function/class at one specific point, verified before moving to the next.
- Zero regressions: full pytest suite must stay green (same one pre-existing `test_singapore_public_holidays_are_on_the_calendar` failure, unrelated) after every task. `run_computer.py` (35/36, unsafe=0) and `run_tiers.py` (15/15, opus=0) must not regress.
- Every wiring task ends with a **live voice test** (Isaac says a real sentence to Evie, confirms she still behaves correctly) before being considered done — a green test suite alone does not prove the live app still works, per this project's own "test in the real app" rule.
- `guard_bash`/`guard_model` in `jobs.py`, the credential ban, the `NO_MATCH_FLOOR` fix, and every other P0 fix from the first increment must remain untouched and passing throughout.

## Review Focus

- **A display disconnects mid-task** (spec §6: "if the external monitor is disconnected: single-display mode") — `assign_display()` already handles this (returns `None`), but no test yet exercises a `ComputerState` read that changes shape (2 displays → 1) between two calls in the same task. Task 2's tests must cover a mid-task display-count change.
- **The Swift `state` op fails or times out** (Screen Recording not granted, or a window closes mid-read) — `ComputerState.from_data({})` already tolerates empty/malformed input (Task 7 of the first plan), but the Python-side caller (wherever `world()` is called today) must handle a failed `state` op the same graceful way `_world()` handles a failed `world` op (falls back to `World.from_data({})`).
- **`perception.choose_source` disagrees with `_find`'s own `VISION_BELOW` check once both exist** — Task 3 must ensure there is exactly ONE place that decision gets made once `perception.py` is wired in, not two independent implementations of the same threshold that could drift apart.
- **`task.TaskRegistry` and `brain.py`'s job-control voice commands ("pause that job", "stop it") collide** — a computer task and a background Claude Code job are different things with different control vocabularies (`jobs.py`'s `JobRunner` already owns "pause/resume/stop" phrasing for jobs). Task 6 must confirm a computer-task stop command doesn't accidentally intercept or get intercepted by job-control routing.
- **The SimMac harness's ≥15 new unseen outcomes (§27) accidentally duplicate existing eval cases** rather than covering genuinely new failure classes (two displays, a stale id, a slow dialog, a file operation). Task 8 must check each new case against the existing 36 in `evals/computer/tasks.py` for actual novelty, not just a new `tid`.

---

## Task 1: New Swift `state` op — display + window geometry

**Files:**
- Modify: `mac/EvieBar/Sources/EvieBar/Eyes.swift` (add `state()` method + dispatch case)
- Modify: `mac/EvieBar/Sources/EvieBar/Hands.swift` (add `"state"` to the known-ops list, matching `"world"`'s existing entry)
- Test: manual, via the Swift `--selftest` (no Python-side test — this task is Swift-only)

**Interfaces:**
- Produces: a new `do` op `"state"` returning JSON `{"version": int, "ts": float, "displays": [{"id": str, "builtin": bool, "frame": [x,y,w,h]}], "windows": [{"app": str, "title": str, "frame": [x,y,w,h], "display": str, "focused": bool}], "front_app": str, "front_element": {...} | null}` — the exact shape `ComputerState.from_data()` (already shipped, `src/evie/computer/state.py`) already parses.
- Consumes: `NSScreen.screens` (display enumeration + frames), `CGWindowListCopyWindowInfo` + `kCGWindowBounds` (already used in `markedShot()`, `Eyes.swift:617-622` — same pattern, enumerate all windows instead of one).

Before writing any Swift, read `Eyes.swift:500-560` (`world()`) and `Eyes.swift:613-631` (`markedShot()`) in full — this task's `state()` method reuses both patterns directly (window enumeration from `world()`, per-window bounds extraction from `markedShot()`) rather than inventing a new approach.

- [ ] **Step 1: Confirm the display-enumeration API**

```bash
cd ~/Elemental/Water/evie/mac/EvieBar
swift -e 'import AppKit; for s in NSScreen.screens { print(s.frame, s.deviceDescription) }'
```

Read the output. `NSScreen.deviceDescription["NSScreenNumber"]` gives a `CGDirectDisplayID`; `NSScreen.main` (or comparing frames to `NSScreen.screens.first`) identifies which one macOS treats as primary — but "primary" is not the same as "builtin" (a MacBook's built-in display can be non-primary if Isaac's external monitor is set as primary in System Settings). Confirm which API actually reports "is this the built-in display" — likely `CGDisplayIsBuiltin(_:)` from Core Graphics, not `NSScreen` alone. Test:

```bash
swift -e 'import CoreGraphics; import AppKit; for s in NSScreen.screens { let id = s.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? CGDirectDisplayID ?? 0; print(id, CGDisplayIsBuiltin(id) != 0) }'
```

Confirm this prints `true` for exactly one display when run on Isaac's MacBook (with or without an external monitor attached) before proceeding — this is the ground truth `Display.builtin` needs.

- [ ] **Step 2: Implement `state()` in `Eyes.swift`**

Add near `world()` (after it, `Eyes.swift:560`-ish — find the exact end of `world()` first: `grep -n "private func world" -A 100 Eyes.swift | grep -n "^    }" | head -1` to locate its closing brace):

```swift
private func state() async -> HandsOutcome {
    var displays: [[String: Any]] = []
    for screen in NSScreen.screens {
        let num = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? CGDirectDisplayID ?? 0
        let f = screen.frame
        displays.append(["id": "display-\(num)", "builtin": CGDisplayIsBuiltin(num) != 0,
                         "frame": [Int(f.origin.x), Int(f.origin.y), Int(f.width), Int(f.height)]])
    }
    let list = (CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
        as? [[String: Any]]) ?? []
    var windows: [[String: Any]] = []
    let front = NSWorkspace.shared.frontmostApplication
    for w in list where (w[kCGWindowLayer as String] as? Int) == 0 {
        guard let owner = w[kCGWindowOwnerName as String] as? String, owner != "Evie", owner != "EvieBar",
              let bd = w[kCGWindowBounds as String] as? NSDictionary,
              let bounds = CGRect(dictionaryRepresentation: bd) else { continue }
        // Which display owns most of this window's area (a window can straddle two displays).
        let ownerDisplay = NSScreen.screens.max(by: { a, b in
            a.frame.intersection(bounds).width * a.frame.intersection(bounds).height <
            b.frame.intersection(bounds).width * b.frame.intersection(bounds).height
        })
        let dispId = ownerDisplay.map { s -> String in
            let n = s.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? CGDirectDisplayID ?? 0
            return "display-\(n)"
        } ?? ""
        let pid = w[kCGWindowOwnerPID as String] as? pid_t
        let isFront = pid != nil && pid == front?.processIdentifier
        let title = w[kCGWindowName as String] as? String ?? ""
        windows.append(["app": owner, "title": title,
                        "frame": [Int(bounds.origin.x), Int(bounds.origin.y), Int(bounds.width), Int(bounds.height)],
                        "display": dispId, "focused": isFront])
        if windows.count >= 20 { break }
    }
    let payload: [String: Any] = ["version": Int(Date().timeIntervalSince1970 * 1000) % 1_000_000,
                                  "ts": Date().timeIntervalSince1970, "displays": displays, "windows": windows,
                                  "front_app": front?.localizedName ?? "", "front_element": NSNull()]
    guard let data = try? JSONSerialization.data(withJSONObject: payload),
          let json = String(data: data, encoding: .utf8) else {
        return HandsOutcome(ok: false, detail: "couldn't build state JSON")
    }
    return HandsOutcome(ok: true, detail: "ok", data: ["state": json])
}
```

Add the dispatch case next to `case "world": return await world()` (`Eyes.swift:277`):

```swift
case "state": return await state()
```

- [ ] **Step 3: Add `"state"` to `Hands.swift`'s known-ops list**

```bash
grep -n '"world"' mac/EvieBar/Sources/EvieBar/Hands.swift
```

Read the line (likely the same known-ops array shown earlier this session at `Hands.swift:55`: `"screen_info", "wait_page", "world", "use_tab", "applescript", "marked_shot"`). Add `"state"` to that list.

- [ ] **Step 4: Build and self-test**

Confirm with Isaac before running this (it restarts the live Evie.app):

```bash
cd ~/Elemental/Water/evie/mac && bash build.sh 2>&1 | tail -40
```

Expected: build succeeds, `--selftest` passes at the same count as before this task (no Python or existing-Swift-behavior changes), and the final line confirms `Evie.app installed and running`.

- [ ] **Step 5: Manually verify the new op returns real data**

With Evie.app running, use whatever debug/inspection path the EXPLAINER.md describes for live `/debug/do` reads (check `docs/EXPLAINER.md` for the exact mechanism — likely a local HTTP endpoint on the core at `127.0.0.1:8765`) to send a `state` op and confirm the JSON shape matches `ComputerState.from_data()`'s expectations. If no such debug endpoint exists yet, add a minimal one following the pattern of whatever endpoint currently lets you send an ad-hoc `do` op for manual testing.

- [ ] **Step 6: Commit**

```bash
cd ~/Elemental/Water/evie
git add mac/EvieBar/Sources/EvieBar/Eyes.swift mac/EvieBar/Sources/EvieBar/Hands.swift
git commit -m "$(cat <<'EOF'
swift: add state op (display + window geometry for ComputerState)

P1-A Swift wiring: a new `state` do-op alongside the existing `world` op,
reporting NSScreen display frames (with CGDisplayIsBuiltin distinguishing the
MacBook's own display from an external monitor) and per-window frames/display-
ownership/focus via CGWindowListCopyWindowInfo + kCGWindowBounds (same pattern
markedShot() already uses for one window, extended to all on-screen windows).
Matches the JSON shape src/evie/computer/state.py's ComputerState.from_data()
already parses and is already tested against.

world() and World.resolve() are completely unchanged -- this is a new,
independent op, not a replacement.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Wire `ComputerState` reads into `Planner` (read-only, unused for decisions yet)

**Files:**
- Modify: `src/evie/computer/planner.py` (add a `_state()` method parallel to `_world()`)
- Test: `tests/test_planner.py`

**Interfaces:**
- Produces: `Planner._state() -> ComputerState` — calls the new `"state"` hands op, parses with `ComputerState.from_data()`, falls back to `ComputerState.from_data({})` on any failure (matching `_world()`'s existing fallback pattern exactly, `planner.py`'s current `_world()` method).
- Consumes: `evie.computer.state.ComputerState` (already shipped), the new `"state"` hands op (Task 1).

This task ONLY adds the ability to read a `ComputerState` — it is not yet consulted for any decision (that's Task 3+). This keeps the risk of this step to near-zero: nothing calls `_state()` yet, so nothing can regress from adding it.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_planner.py
async def test_state_reads_and_parses_the_new_state_op():
    """P1-A wiring: Planner._state() calls the new 'state' hands op and parses it with
    ComputerState.from_data() -- mirrors _world()'s existing pattern exactly."""
    from evie.computer.state import ComputerState
    import json
    raw = {"version": 5, "ts": 100.0, "displays": [{"id": "d1", "builtin": True, "frame": [0, 0, 100, 100]}],
           "windows": [], "front_app": "Safari", "front_element": None, "tabs": []}
    hands = SimHands(world=SAFARI_FRONT)
    hands.state_response = json.dumps(raw)  # SimHands needs a "state" op handler -- see Step 1b
    p, _ = planner(hands, PlanGroq())
    s = await p._state()
    assert isinstance(s, ComputerState) and s.version == 5 and s.front_app == "Safari"
```

- [ ] **Step 1b: Add a `"state"` op handler to `SimHands` (`evals/sim.py`)**

Check `SimHands.do()`'s existing `"world"` handler (`grep -n '"world"' evals/sim.py`) and add a parallel one:

```python
# evals/sim.py -- in SimHands.__init__, add:
self.state_response = "{}"

# in SimHands.do(), alongside the "world" branch:
if op == "state":
    return HandsResult(True, "ok", {"state": self.state_response})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python -m pytest tests/test_planner.py -v -k test_state_reads`
Expected: FAIL — `Planner` has no `_state` method yet (`AttributeError`), and `SimHands` has no `state_response`/`"state"` handler yet.

- [ ] **Step 3: Implement `_state()` in `planner.py`**

Find `_world()`'s exact current implementation first (`grep -n "async def _world" -A 8 src/evie/computer/planner.py`) and add a parallel method right after it:

```python
    async def _state(self) -> ComputerState:
        r = await self._hands.do("state", timeout=6.0)
        try:
            return ComputerState.from_data(json.loads(r.data.get("state") or "{}") if r.ok else {})
        except ValueError:
            return ComputerState.from_data({})
```

Add the import at the top of `planner.py`:

```python
from evie.computer.state import ComputerState
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python -m pytest tests/test_planner.py -v -k test_state_reads`
Expected: PASS

- [ ] **Step 5: Run the full test suite and both evals**

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python -m pytest -q && PYTHONPATH=. python evals/run_computer.py && PYTHONPATH=. python evals/run_tiers.py`
Expected: same counts as before this task (`_state()` is never called by existing code paths yet).

- [ ] **Step 6: Commit**

```bash
cd ~/Elemental/Water/evie
git add src/evie/computer/planner.py tests/test_planner.py evals/sim.py
git commit -m "$(cat <<'EOF'
computer: Planner._state() reads the new state op (unused for decisions yet)

P1-A wiring, step 1 of 2: Planner can now read a ComputerState the same way
it already reads a World (_world()) -- same fallback-on-failure pattern. Not
yet consulted by any decision (World.resolve() and the existing VISION_BELOW
check are unchanged); this is purely additive so it carries near-zero
regression risk on its own. The next task decides where display/window
geometry actually changes behavior.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Wire `perception.choose_source` into `Planner._find` (replace the inline `VISION_BELOW` check)

**Files:**
- Modify: `src/evie/computer/planner.py` (the `_find` method's inline vision-fallback check)
- Test: `tests/test_planner.py`

**Interfaces:**
- Consumes: `evie.computer.perception.choose_source`, `PerceptionSource` (already shipped and tested, `src/evie/computer/perception.py`).
- Removes: the inline `if not self._web() and len(labelled) < VISION_BELOW:` check in `_find` (`planner.py`), replaced by a single call to `choose_source`.

This closes the Review Focus risk named in this plan's own header: once `perception.py` exists, there must be exactly ONE place the vision-fallback threshold is decided, not two independent copies that could drift.

- [ ] **Step 1: Write the failing test proving the OLD inline threshold and the NEW module must agree**

```python
# tests/test_planner.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python -m pytest tests/test_planner.py -v -k test_find_uses_perception`
Expected: FAIL — `evie.computer.planner.choose_source` doesn't exist (nothing imported it yet), so `patch()` raises `AttributeError`.

- [ ] **Step 3: Wire it in**

```python
# planner.py -- add the import
from evie.computer.perception import PerceptionSource, choose_source
```

Find `_find`'s current inline check (`grep -n "VISION_BELOW" src/evie/computer/planner.py`) and replace:

```python
        labelled = [e for e in self._screen.elements if e.get("label")]
        if not self._web() and len(labelled) < VISION_BELOW:
            return await self._look_for(what)  # almost nothing readable: look at it instead
```

with:

```python
        if choose_source(what, self._screen) == PerceptionSource.VISION and not self._web():
            return await self._look_for(what)  # almost nothing readable: look at it instead
```

(Keep the `not self._web()` guard exactly as-is — `perception.choose_source` already returns `STRUCTURED` for a sparsely-labelled web screen per its own test, `test_a_sparsely_labelled_web_screen_still_prefers_structured`, so this guard becomes technically redundant, but keeping it is a defensive belt-and-suspenders against `choose_source`'s web-detection ever disagreeing with `self._web()`'s own broader definition — remove `VISION_BELOW` itself from `planner.py`'s module constants only after confirming nothing else references it: `grep -n VISION_BELOW src/evie/computer/planner.py` should show zero remaining hits.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python -m pytest tests/test_planner.py -v -k "test_find_uses_perception or vision"`
Expected: PASS, including any pre-existing test that exercised the old `VISION_BELOW` path (grep for `VISION_BELOW` in `tests/test_planner.py` first and confirm those tests still pass unmodified, since the observable behavior is identical, just routed through the new function).

- [ ] **Step 5: Run the full suite and both evals**

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python -m pytest -q && PYTHONPATH=. python evals/run_computer.py && PYTHONPATH=. python evals/run_tiers.py`
Expected: identical counts to before this task — this is a pure refactor of WHERE the decision is made, not a change to the decision itself (confirmed by Step 4's requirement that pre-existing vision-fallback tests pass unmodified).

- [ ] **Step 6: Live voice test**

Confirm with Isaac before this step (it doesn't require a rebuild — Python-only change picked up on the core's next restart, or hot if the core auto-reloads; check `docs/EXPLAINER.md` for whether `evie-core` needs a manual restart). Ask Evie to do something in a sparsely-labelled native app (Notion is a good real example already in the eval suite) and confirm she still falls back to vision exactly as before.

- [ ] **Step 7: Commit**

```bash
cd ~/Elemental/Water/evie
git add src/evie/computer/planner.py tests/test_planner.py
git commit -m "$(cat <<'EOF'
computer: _find routes through perception.choose_source, not its own threshold

P1-C wiring: closes the risk this plan's own Review Focus named -- once
perception.py existed, _find's inline VISION_BELOW check and choose_source's
identical logic could drift apart over time if only one got updated. Now
there is exactly one place this decision is made. Behavior is unchanged
(pinned by pre-existing vision-fallback tests passing unmodified) -- this is
a pure refactor of WHERE the decision lives, verified live with Isaac.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Swift window move/resize/focus op (for `workspace.assign_display`)

**Files:**
- Modify: `mac/EvieBar/Sources/EvieBar/Hands.swift` (new `"place_window"` op)
- Test: manual, via `--selftest` + live verification

**Interfaces:**
- Produces: a new `do` op `"place_window"` taking `{"app": str, "display_id": str}`, moving that app's frontmost window to fill (or center within) the named display's frame.
- Consumes: `NSScreen.screens` (same enumeration as Task 1's `state()`), Accessibility APIs to set a window's position/size (`AXUIElementSetAttributeValue` with `kAXPositionAttribute`/`kAXSizeAttribute` — the codebase's existing AX usage pattern, e.g. `Eyes.swift:512-514`'s `Self.attr(root, kAXWindowsAttribute)`).

This is the one remaining piece spec §6/§7 needs to make `workspace.assign_display()`'s answer into a real action (moving Evie's own work to the MacBook display, or bringing something to Isaac's external monitor on "show me").

- [ ] **Step 1: Confirm the AX window-move API works on a real window**

```bash
cd ~/Elemental/Water/evie/mac/EvieBar
swift -e '
import AppKit
let app = NSWorkspace.shared.frontmostApplication!
let axApp = AXUIElementCreateApplication(app.processIdentifier)
var windowsRef: CFTypeRef?
AXUIElementCopyAttributeValue(axApp, kAXWindowsAttribute as CFString, &windowsRef)
let windows = windowsRef as! [AXUIElement]
guard let w = windows.first else { print("no window"); exit(0) }
var pos = CGPoint(x: 100, y: 100)
let posValue = AXValueCreate(.cgPoint, &pos)!
let result = AXUIElementSetAttributeValue(w, kAXPositionAttribute as CFString, posValue)
print("move result:", result.rawValue)
'
```

Read the output (`0` = `kAXErrorSuccess`). If this returns a nonzero error, the app in front when you ran this may not support programmatic window moves (some apps report `kAXErrorAttributeUnsupported`) — note which apps work and which don't; the real op needs a graceful failure path for apps that refuse (return `HandsOutcome(ok: false, ...)`, never crash).

- [ ] **Step 2: Implement `place_window` in `Eyes.swift`/`Hands.swift`**

Follow the exact pattern confirmed in Step 1, wrapped as a `do` op parallel to the existing `activate`/`use_tab` ops (`grep -n 'case "activate"' mac/EvieBar/Sources/EvieBar/Eyes.swift` to find the sibling pattern to match). Move AND resize to fill 90% of the target display's frame (leaving a small margin so the window isn't flush against the screen edge) — the exact CGRect math: `targetFrame.insetBy(dx: targetFrame.width * 0.05, dy: targetFrame.height * 0.05)`.

- [ ] **Step 3: Build and self-test** (confirm with Isaac first — kills the running app)

```bash
cd ~/Elemental/Water/evie/mac && bash build.sh 2>&1 | tail -40
```

- [ ] **Step 4: Live verification**

With a second display connected (borrow one if Isaac doesn't have one plugged in at the moment — this needs REAL multi-display hardware, not a simulation), ask Evie to do something and confirm `workspace.assign_display`'s answer, once wired into a real caller (Task 5), actually results in a window moving to the right display.

- [ ] **Step 5: Commit**

```bash
cd ~/Elemental/Water/evie
git add mac/EvieBar/Sources/EvieBar/Eyes.swift mac/EvieBar/Sources/EvieBar/Hands.swift
git commit -m "$(cat <<'EOF'
swift: add place_window op (moves/resizes a window onto a named display)

P1-B Swift wiring: the one remaining piece workspace.assign_display() needs
to become a real action -- move/resize via AXUIElementSetAttributeValue
(kAXPositionAttribute/kAXSizeAttribute), same AX pattern already used
elsewhere in Eyes.swift. Fills 90% of the target display's frame. Some apps
refuse programmatic moves (kAXErrorAttributeUnsupported) -- this fails
gracefully (HandsOutcome(ok: false)) rather than crashing; [list which apps
you tested against and their result here after Step 1's manual check].

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Wire `workspace.assign_display` into a real caller

**Files:**
- Modify: wherever the Planner decides Evie's autonomous work should happen (likely `Recipes.run()` or `Planner.run()` itself — read `recipes.py` and `planner.py`'s `run()` entry point first to find the right insertion point; this task's exact file/line depends on what Task 2/3 already changed, so locate it fresh rather than trusting a line number written before those tasks ran).
- Test: `tests/test_workspace.py` (extend), plus a new integration test wherever the call site lands.

**Interfaces:**
- Consumes: `workspace.default_policy`, `workspace.assign_display` (already shipped), `Planner._state()` (Task 2), the new `place_window` op (Task 4).

This is genuinely the riskiest task in this plan — it changes what Evie's autonomous computer-use work actually does on Isaac's screens. Design it, write it, and test it against `SimHands`/eval fixtures fully before ever running it live. Do not skip straight to a live test.

- [ ] **Step 1: Decide (with Isaac, in chat, before writing code) exactly which computer-use tasks should move to the Evie-private display**

The spec's default is "all autonomous work"; but Isaac's own Task 5 experience with the first increment showed live wiring is exactly where surprises happen. Propose a narrower first cut: only tasks explicitly marked as background/non-urgent (however that's signaled today — check `brain.py`'s `_start_computer` call sites for any existing foreground/background distinction) move display; everything else stays exactly where it runs today. Get Isaac's explicit agreement on scope before writing this task's code.

- [ ] **Step 2-N**: (left deliberately unspecified — this task's concrete steps depend on: (a) what Task 2/3 changed in `planner.py` by the time this runs, and (b) Isaac's answer in Step 1 about scope. Write this task's TDD steps fresh, following the same RED-GREEN pattern as every other task in both plans, once (a) and (b) are known. Do not guess ahead of that conversation.)

---

## Task 6: Wire `task.TaskRegistry` into `brain.py`'s `_computer_task`

**Files:**
- Modify: `src/evie/brain.py` (replace `self._computer_task: asyncio.Task | None` bookkeeping with a `TaskRegistry` instance alongside it — the `asyncio.Task` itself still exists for actual cancellation, but `TaskRegistry` tracks its identity/objective/step for narration and "actually the other file" amendment)
- Test: wherever `brain.py`'s computer-task start/stop logic already has tests (check `grep -rln "_start_computer\|_run_computer" tests/`)

**Interfaces:**
- Consumes: `evie.computer.task.TaskRegistry`, `ComputerTask` (already shipped and tested).

Read this plan's own Review Focus item about job-control vocabulary collision before starting — confirm `jobs.py`'s `JobRunner` "pause/resume/stop" phrasing and whatever phrasing a computer-task stop command would use don't parse as the same intent to Jev's switchboard. This may need a `switchboard/` change, which is otherwise out of scope for this plan (the first plan's Global Constraints said "keep the Jev switchboard... unchanged") — if it does need one, stop and scope that separately with Isaac rather than quietly touching switchboard code.

- [ ] **Step 1-N**: (left deliberately unspecified for the same reason as Task 5 — this depends on live investigation of `brain.py`'s current voice-command routing that should happen fresh, not from a stale plan. Follow the same RED-GREEN TDD process once the investigation is done.)

---

## Task 7: Cloud-vision budget (spec §4 Level 3, "max 2 per task, logged")

**Files:**
- Modify: `src/evie/computer/planner.py` (`_look_for`, add a per-task call counter)
- Test: `tests/test_planner.py`

**Interfaces:**
- Produces: `Planner` gains a per-`run()`-call counter (`self._vision_calls = 0`, reset at the top of `run()`) incremented each time `_look_for` actually reaches the `self._groq.look(...)` call; once it hits 2, further `_look_for` calls raise `_Fail("already looked twice this task")` instead of calling the model again.

This is small and self-contained — a straightforward TDD task following the exact pattern of every task in the first plan. No hardware dependency.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_planner.py
async def test_vision_is_never_called_more_than_twice_per_task():
    """spec §4 Level 3: 'max 2 cloud vision calls per task, logged.' A goal that fails to find
    its target on every replan must not call self._groq.look() a third time."""
    plan_stuck = {"steps": [{"do": "find", "what": "a button that never appears", "then": "press"},
                            {"do": "done", "say": "x"}]}
    hands = SimHands(apps={"SomeApp": []}, world={"front_app": "SomeApp", "apps": ["SomeApp"], "windows": [],
                                                   "tabs": [], "selected": ""})
    groq = PlanGroq(plan_stuck, plan_stuck, plan_stuck)
    calls = []
    async def fake_look(prompt, png):
        calls.append(1)
        return '{"n": null}'
    groq.look = fake_look
    hands_calls = []
    orig_do = hands.do
    async def counting_do(op, **kw):
        if op == "marked_shot":
            hands_calls.append(1)
        return await orig_do(op, **kw)
    hands.do = counting_do
    p, _ = planner(hands, groq)
    await p.run("press a button that never appears")
    assert len(calls) <= 2
```

Before finalizing, check `_look_for`'s exact current structure (`grep -n "async def _look_for" -A 20 src/evie/computer/planner.py`) to confirm the mock points (`self._groq.look`, `self._hands.do("marked_shot", ...)`) match what actually gets called, and adjust the test's mocking approach to match real call signatures rather than guessing.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python -m pytest tests/test_planner.py -v -k test_vision_is_never_called_more_than_twice`
Expected: FAIL — today `_look_for` has no call budget, so it would call `self._groq.look` on every replan attempt (up to `MAX_REPLANS` + 1 times), exceeding 2.

- [ ] **Step 3: Implement the budget**

```python
# planner.py -- Planner.run(), near where self._history etc. get reset at the top:
        self._vision_calls = 0
```

```python
# planner.py -- _look_for, at the top:
    async def _look_for(self, what: str) -> dict:
        if self._vision_calls >= 2:
            raise _Fail(f"already looked twice this task, couldn't find {what!r}")
        self._vision_calls += 1
        # ... rest of the existing method unchanged ...
```

- [ ] **Step 4: Run test to verify it passes, then the full suite + evals**

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python -m pytest -q && PYTHONPATH=. python evals/run_computer.py && PYTHONPATH=. python evals/run_tiers.py`
Expected: new test passes; existing counts unchanged (no existing eval case calls `_look_for` more than twice in one task today, so this shouldn't change any existing outcome — if it does, investigate why a passing eval case relied on a third vision call, which would itself be a violation of the spec's stated budget).

- [ ] **Step 5: Commit**

```bash
cd ~/Elemental/Water/evie
git add src/evie/computer/planner.py tests/test_planner.py
git commit -m "$(cat <<'EOF'
computer: cap _look_for at 2 cloud-vision calls per task (spec §4 Level 3)

"Vision should therefore be a capability Evie CAN invoke, not something
constantly consuming model tokens" -- max 2 per task, per the spec. A third
attempt within the same run() call now fails fast instead of calling Groq
again, which also means a genuinely-stuck task reaches its stuck outcome
faster rather than exhausting replans on repeated failed looks.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Stateful SimMac + ≥15 new unseen eval outcomes (spec §27)

**Files:**
- Create: `evals/simmac.py` (extends `evals/sim.py`'s `SimHands` with persistent state across calls: files, dialogs, delays, stale ids)
- Modify: `evals/computer/tasks.py` (add ≥15 new cases)
- Test: the eval harness itself IS the test — `evals/run_computer.py` run against the new cases.

This is explicitly called out in the first plan's Deferred Work as "a small test-infrastructure project by itself" — treat it as its own sub-plan. Before writing code, read `evals/sim.py` in full (already read once this session, ~130 lines) and `evals/computer/tasks.py`'s existing 36 cases to identify what "unseen" actually means here (don't duplicate an existing failure mode with a new `tid`).

The spec's §27 list of outcome categories to cover: known app + novel wording, known app + novel starting state, unknown app, multi-window task, multi-display task, filesystem task, browser task, visual task, PDF task, slow UI, unexpected dialog, stale UI, ambiguous target, partial failure, recovery, long autonomous task. Cross-reference this list against the existing 36 cases in `evals/computer/tasks.py` (grep each category's keywords against the file) to find which are already covered and which of the ≥15 new cases should target genuinely uncovered categories — likely candidates based on what this session's two plans have NOT touched: multi-display (needs `ComputerState`/`workspace.py`, now available), a stale-id case (an element id from an old snapshot pressed against a new one — `Planner._act`'s `snapshot` field already exists for this, check if any existing test/eval exercises a stale snapshot rejection), a slow-dialog case (an `expect` that must wait through a native macOS alert), and a filesystem-op case (via the `finder_*` actions already in `cards.py`).

- [ ] **Step 1-N**: (left deliberately unspecified — write this sub-plan's own TDD steps once the category cross-reference above is done, following the exact task(tid, goal, world, expect, pages=...) schema already established in `evals/computer/tasks.py`, and the exact PlanGroq/PickJev/planner() test conventions established throughout both plans.)

---

## Deferred beyond this plan too

- **P2 D/E full implementation** (`objective.py`'s outcome/completion-predicate model beyond what `task.py` already gives; `verifier.py`'s polled checks; `recovery.py`'s §13 failure classification) — these are real subsystems, not wiring tasks, and deserve their own from-scratch plan once Tasks 1-8 here have landed and Isaac has lived with the wired-in P1-A/B/C/F for a while to see what `objective.py`/`verifier.py` actually need to consume.
- **P3** (plain-code bridge, in-process Claude MCP server) — explicitly gated behind P2-D/E/G being real, not just designed.
- **EXPLAINER.md / HOW-IT-WORKS.md updates** — do this once, at the end of whichever session finishes Task 5 (the first genuinely user-visible behavior change from this whole V2 effort), not incrementally per task.
