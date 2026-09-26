# Evie Computer Use V2 — P0 Bugfixes + P1-A/B Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the 6 confirmed P0 bugs in Evie's computer-use engine (wrong-target presses, password typing, false success claims, bad procedure reuse, an unenforced Opus ban, and two disconnected vision systems), then lay the P1-A/B foundations (a unified `ComputerState` and a `workspace.py` display policy) — all as in-place evolution of `src/evie/computer/{planner.py,world.py,find.py,vision_fallback.py}`, `src/evie/{jobs.py,procedures.py,capabilities.py}`. No rewrite: existing files gain code, only the two named new files are created.

**Architecture:** Each P0 bug gets a minimal, targeted fix in the file that owns the bug, proven by a failing-test-first cycle against the exact scenario in the spec. P1-A adds `src/evie/computer/state.py` as a thin, versioned snapshot type built from a new Swift `state` op (kept alongside, not replacing, the existing `world` op that `World.resolve()` already uses well). P1-B adds `src/evie/computer/workspace.py` encoding the Evie-workspace/Isaac-workspace display policy from the spec, as a pure-Python policy module with no Swift dependency for this increment (window move/resize/focus wiring is called out as follow-on, since it needs a new Swift op with real hardware to test against).

**Tech Stack:** Python 3.12, pytest, dataclasses, existing `evie.computer.*` / `evie.jobs` / `evie.procedures` modules, Swift side only touched by inspection (no Swift changes in this plan — P1-A's new Swift `state` op is scoped OUT of this plan and listed as follow-on, since it needs a real Mac to build/test and this plan is Python-only).

**Spec:** `~/Downloads/Evie Computer Use V2 — Architecture.md` (sections 3, 13, 16, 24 are what this plan implements; §24's file tree is direction only, not a literal target — see spec header).

## Global Constraints

- Line numbers cited throughout this plan (e.g. `planner.py:489-517`) are as-of-writing references to help locate code, not literal offsets to trust blindly — `planner.py` in particular is touched by Tasks 1, 2, 3 and 6 in sequence, so by the time Task 6 runs, its cited line numbers for `_act`/`_jev_choose`/etc. will have shifted from earlier tasks' insertions. Always locate the target function by name/content (`grep -n "def _act"` etc.) before editing, and treat the line ranges as orientation, not ground truth.
- No rewrite of `planner.py`, `world.py`, `find.py`, `jobs.py`, `procedures.py` — only additive/targeted edits.
- New files only where this plan lists them: `src/evie/computer/workspace.py`. (`state.py` is designed here but its implementation is follow-on — see Deferred Work.)
- Zero regressions: full pytest suite must stay at 925 passing + the 1 pre-existing unrelated failure (`test_singapore_public_holidays_are_on_the_calendar`, a holiday-name spelling mismatch, not touched by this work) after every task.
- `evals/run_computer.py` must stay at 34/35 success, unsafe=0 after every task (35/35 once Task 1's new case is added).
- `evals/run_tiers.py` must stay at 15/15, opus=0 after every task.
- No Opus model may ever be reachable — enforced by an allowlist in code, not a denylist, per spec §16.
- Never type into a field whose role/label indicates password or login credentials.
- All new/changed Python code follows the existing terse, comment-sparse style already in these files (docstrings only where the WHY is non-obvious, exactly as the codebase already does).

## Review Focus

- **Ambiguous voice command with no plausible on-screen target** — spec's Law 8 ("ask only when there is genuine irreducible ambiguity") implies Evie must recognize when NOTHING on screen is a good match and say so, not press the closest-sounding wrong thing. Task 1 pins this with a fabricated screen where the wanted name has no match at all.
- **A replan cycle after a partially-completed risky action** — the spec's Law 3 ("verify after acting") implies that even mid-replan, a step that would type into a credential field must be blocked, not just on the first plan attempt. Task 2 tests this specifically via the replan path (`first=False`), not just the initial plan.
- **A procedure that matches on structure but flips a negated entity** ("wifi on" vs a learned "wifi off") — spec Law 4 ("recipes are optimizations, not truth") implies structural similarity is not semantic equivalence. Task 4 tests on/off, and also numeric entities (a learned "set volume to 20" must not fire for "set volume to 80").
- **A job spawned via `Task`/`Agent` tool-use instead of direct `Bash`** — the goal spec explicitly calls out that the existing hook only matches `Bash`, so a job could pick Opus or run guarded commands through a sub-agent path. Task 5 tests that the model allowlist is enforced at `make_client` (which every path funnels through) rather than only at the hook layer, closing this gap architecturally rather than by chasing every tool name.
- **`capabilities.py` telling Isaac Evie can't do something she now can** (vision) — spec Law 12 ("no lying about completion") extends to lying about capability. Task 6 updates the `CANT_YET` line so Evie never claims a false limitation once the real vision path is confirmed live.

---

## File Structure

- **Modify** `src/evie/computer/find.py` — add a score floor / "no plausible match" signal so `Planner._find`/`_pick` can refuse to press anything when the best candidate isn't actually a reasonable match, plus a `NONE_LABEL` sentinel wired through Jev's choice criteria.
- **Modify** `src/evie/computer/planner.py` — wire the "none of these" option into `_jev_choose`/`_find`/`_pick`, add a credential-field guard in `_act`/`_step` for `set_text` and `key`-based sends, add a completion-evidence check so `done` cannot fire without either a passed `expect` or a real read/action result, and delete the dead `vision=VisionFallback(hands)` wiring in favor of the already-working `_look_for` path.
- **Modify** `src/evie/procedures.py` — add an entity-equality check (`_entities()` extraction + comparison) alongside the existing `SequenceMatcher` score so a procedure only reuses when its named entities (on/off, numbers) match the new goal, not just string shape.
- **Modify** `src/evie/jobs.py` — replace the substring Opus check in `make_client` with a strict allowlist (`ALLOWED_MODELS`), make `model=None` resolve to the "normal" tier's default instead of silently inheriting Claude Code's own settings-derived model, and extend the `PreToolUse` hook matcher to also cover `Task`/`Agent` tool use.
- **Modify** `src/evie/capabilities.py` — move "look at screenshots or images" out of `CANT_YET` now that `_look_for` is the confirmed live path.
- **Delete** `src/evie/computer/vision_fallback.py` and its two test files (`tests/test_vision_fallback.py`, `tests/test_vision_fallback_wiring.py`) — dead code calling a Swift op (`"screenshot"`) that was never implemented, replaced by the already-working `_look_for`/`marked_shot` path that Task 3 confirms is the sole vision escalation route.
- **Create** `src/evie/computer/workspace.py` — the Evie-workspace/Isaac-workspace display-policy module from spec §6-7 (`evie_private`, `isaac_visible`, `observe_isaac` policies as a pure decision function over a two-display world), with its own test file `tests/test_workspace.py`.

---

## Task 1: MISSING_TARGET — score floor + "none" option so Evie never presses the wrong profile

**Files:**
- Modify: `src/evie/computer/find.py`
- Modify: `src/evie/computer/planner.py:340-379` (`_find`, `_look_for`), `:470-486` (`_jev_choose`)
- Test: `tests/test_find.py`, `tests/test_planner.py`

**Interfaces:**
- Produces: `find.py::NO_MATCH_FLOOR: float = 0.35` (module constant); `find.py::score()` unchanged in signature, still returns `float`.
- Produces: `planner.py::_jev_choose()` gains a `"none"` criterion entry whenever `cands` best score (via `find.score`, imported) is below `NO_MATCH_FLOOR`; when Jev picks `"none"`, `_jev_choose` raises `_Fail(f"none of these are {what!r}")` (a normal, recoverable failure — NOT pressed).
- Consumes: existing `_Fail` exception class already defined in `planner.py:124-125`.

The bug (spec's P0 #1): "Target absent: pressed 'Dangal' for profile 'Daryl'." Today `find.score()`'s `_near()` fuzzy-name-match (`find.py:48,52-55`) is designed for *misheard spellings of the SAME name* ("Darrell" heard for "Darryl" — see `tests/test_find.py:75-76`), but nothing stops `_jev_choose` (`planner.py:470-486`) from being handed a candidate list where the actual named target isn't present at all, and Jev choosing the least-bad option anyway because every criterion in its choice list looks like a real option with no "none of these" escape valve.

This codebase's real test convention (confirmed from `tests/test_planner.py`): a `planner(hands, groq, jev=None, countdown=None, said=None)` helper builds a `Planner` against `evals.sim.SimHands`, `PlanGroq` (a scripted plan-call queue) and `PickJev` (picks an id from the criteria dict, or a callable). Tests drive the whole thing through `p.run(goal)`, not through private methods directly — match this exactly, do not invent a new fixture style.

`PickJev(choose=...)` already lets a test force Jev to pick a specific id (see the existing `test_jev_can_never_pick_something_that_is_not_on_screen`, which forces `choose="w99"`, an id NOT on screen at all — the existing `cid not in criteria` check already catches that case). This new test is about a DIFFERENT bug: Jev picking an id that IS validly on screen, but is simply the wrong profile (a real "Dangal" button exists; it's just not "Daryl"). `PickJev`'s `choose` callable receives the list of on-screen option ids/criteria keys and must be able to pick the new `"none"` key once Task 1's fix adds it — so `choose=lambda opts: "none"` is how the test forces that choice.

Note on `_jev_choose`'s two call sites — this matters for how the score floor is computed: `_find` (`planner.py:356`) calls `self._jev_choose(f"Which of these on-screen items is: {what}?", cands, f"find {what!r}")` — the raw target string `what` is available there, separate from the `what` positional param of `_jev_choose` itself (which is only ever used for log/error text, e.g. `f"find {what!r}"`). `_pick` (`planner.py:385-387`) calls it with `st.get('want')` as the real target and `f"pick {st.get('want')!r}"` as the log-text `what`. So `_jev_choose` cannot recover the real target string from its own `what` param — it must receive it as a new, explicit argument.

- [ ] **Step 1: Write the failing test proving Jev can be forced to press a wrong-but-real profile today**

```python
# tests/test_planner.py — add near test_jev_can_never_pick_something_that_is_not_on_screen
NETFLIX_WRONG_PROFILE_SCREEN = [
    {"id": "w1", "role": "link", "label": "Isaac", "href": "https://www.netflix.com/browse?profile=isaac"},
    {"id": "w2", "role": "link", "label": "Dangal", "href": "https://www.netflix.com/browse?profile=dangal"},
    {"id": "w3", "role": "link", "label": "Kids", "href": "https://www.netflix.com/browse?profile=kids"},
]
NETFLIX_PLAN = {"steps": [{"do": "open_url", "url": "https://www.netflix.com/"},
                          {"do": "find", "what": "Daryl", "role": "link", "then": "press"},
                          {"do": "done", "say": "Daryl's profile opened."}]}


async def test_pressing_the_wrong_real_profile_is_todays_bug():
    """2026-09-25 core.log: Evie pressed 'Dangal' when asked for the Netflix profile 'Daryl'.
    Both are real, on-screen options -- the existing 'never press an id that's not on screen'
    guard doesn't help here, because Dangal genuinely IS on screen. find_in_code can't pick a
    clear winner (nothing scores above CLEAR_WIN), so this falls through to _jev_choose, and
    (today, with no "none" option) Jev is forced to pick SOME real id. This pins today's buggy
    behavior before Task 1's fix; Step 4 re-runs the same shape of test after the fix expecting
    the opposite outcome."""
    hands = SimHands(pages={"https://www.netflix.com/": NETFLIX_WRONG_PROFILE_SCREEN}, world=SAFARI_FRONT)
    # Jev (today, with no "none" option offered) picks whatever scores best among real
    # candidates -- simulate that by always choosing the first offered id ("Isaac", w1).
    p, _ = planner(hands, PlanGroq(NETFLIX_PLAN), PickJev(choose=lambda opts: opts[0]))
    r = await p.run("open netflix and click the profile daryl")
    assert r.ok  # today: no error, because SOME real (wrong) id got pressed
    assert ("press", {"id": "w1", "snapshot": "s1"}) in hands.calls  # "Isaac" pressed: WRONG target, but real
```

- [ ] **Step 2: Run test to verify it demonstrates today's bug**

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python -m pytest tests/test_planner.py::test_pressing_the_wrong_real_profile_is_todays_bug -v`
Expected: PASS today — this confirms the bug exists (a plausible-sounding wrong option gets pressed with no safety valve), which is the point of writing it first. This test is DELETED in Step 5 below once the fix changes this exact behavior — it exists only to prove the bug is real before fixing it, per the project's "reproduce before patch" rule.

- [ ] **Step 3: Add the score floor to `find.py`**

```python
# find.py — near the other module constants (after CLEAR_GAP = 0.2)
NO_MATCH_FLOOR = 0.35  # below this, no candidate is a plausible match at all (2026-09-26 P0:
# "Dangal" pressed for the profile "Daryl" — nothing on screen was even a misheard spelling of it)


def best_score(cands: list[dict], what: str) -> float:
    """The top candidate's score, or 0.0 for an empty list. Used to decide whether to offer
    Jev a "none of these" escape instead of forcing a choice among implausible options."""
    return max((score(c, what) for c in cands), default=0.0)
```

- [ ] **Step 4: Wire the "none" option into `_jev_choose`, `_find` and `_pick` in `planner.py`**

`_jev_choose` gains a new required parameter, `target`, carrying the actual thing being searched for (as opposed to `what`, which stays exactly as it is today — free-text log/error context). Both call sites already have this value in a local variable, so this is a pure plumbing change, not new logic at the call sites.

```python
# planner.py — replace the existing _jev_choose method (lines 470-486)
    async def _jev_choose(self, instructions: str, cands: list[dict], what: str, target: str) -> dict:
        criteria = {c["id"]: (f"#{i + 1} " + (c.get("label") or "") + (f" ({c['meta']})" if c.get("meta") else ""))[:160]
                    for i, c in enumerate(cands)}
        if best_score(cands, target) < NO_MATCH_FLOOR:
            criteria["none"] = "None of these are a plausible match; the target isn't on screen"
        try:
            res = await self._jev.ask(f"Goal: {self._goal}", {"el": {"type": "choice", "instructions": instructions,
                                                                     "criteria": criteria}})
            a = res.answers["el"]
        except (JevError, KeyError, TypeError) as e:
            raise _Fail(f"couldn't choose for {what}: {e}")
        cid = a.get("choice")
        if cid == "none":
            raise _Fail(f"none of these are {what!r}")
        if cid not in criteria or cid not in self._screen.ids:  # never anything that isn't on screen
            raise _Fail(f"{what}: the choice {cid!r} isn't on screen")
        if float(a.get("confidence", 0)) < 0.3:
            raise _Fail(f"{what}: not sure which one")
        el = self._screen.get(cid)
        self._history.append(f"chose {el.get('label')!r} for {what}")
        return el
```

Update the two call sites. In `_find` (`planner.py:341-360`), the `try/except _Fail` branch that calls `_jev_choose`:

```python
        try:
            return await self._jev_choose(f"Which of these on-screen items is: {what}?", cands, f"find {what!r}", what)
        except _Fail:
            if self._web():
                raise
            return await self._look_for(what)
```

In `_pick` (`planner.py:381-391`):

```python
    async def _pick(self, st: dict) -> dict:
        pool = pick_pool(self._screen, str(st.get("among") or ""))
        if not pool:
            raise _Fail(f"no {st.get('among')} on this page")
        want = str(st.get("want") or "")
        el = await self._jev_choose(
            f"Isaac asked: \"{self._goal}\". Which one is {want}? They're listed in page order "
            "(first = top of the page). Pick the best match.", pool[:12], f"pick {want!r}", want)
        self._picked = el.get("label", "")
        others = [{k: r[k] for k in ("id", "label", "meta", "href") if r.get(k)} for r in pool[:4] if r["id"] != el["id"]]
        self._alt = self._pick_state({**st, "then": "press"}, others[:3]) if others else None
        return el
```

There is a THIRD call site inside `choose()` (`planner.py:438-441`, the "Which one?" answer-resolution path) — check it:

```bash
grep -n "_jev_choose(" src/evie/computer/planner.py
```

That third call passes `answer` (Isaac's spoken reply, e.g. "the island one") as the natural target string:

```python
                el = await self._jev_choose(
                    f'Evie showed Isaac these and asked which one. He answered: "{answer}". Which one does he '
                    'mean? They\'re in page order: "the latest", "the newest" or "the first" is #1.', cands,
                    f"choose {answer!r}", answer or "")
```

Add the import at the top of `planner.py`:

```python
from evie.computer.find import _BADGE, best_score, candidates, find_in_code, pick_pool, NO_MATCH_FLOOR
```

- [ ] **Step 5: Delete the bug-reproduction test, update the "always press a real id" test, run the suite**

Delete `test_pressing_the_wrong_real_profile_is_todays_bug` (Step 1) — it asserted the OLD, buggy behavior and would now fail (correctly) since `_jev_choose` offers `"none"` when nothing scores above `NO_MATCH_FLOOR`, and `PickJev(choose=lambda opts: opts[0])` would now pick `"none"` first (it's inserted into `criteria` and iterated in the same dict order). Replace it with the fixed-behavior version:

```python
# tests/test_planner.py — replaces test_pressing_the_wrong_real_profile_is_todays_bug
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
```

(`PlanGroq(NETFLIX_PLAN, NETFLIX_PLAN, NETFLIX_PLAN)` supplies the plan three times because `MAX_REPLANS = 2`, `planner.py:39` — the planner replans up to twice more after the first `_Fail`, each replan consuming one more scripted plan from `PlanGroq`'s queue, before giving up and returning the `stuck` `Outcome`.)

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python -m pytest tests/test_planner.py tests/test_find.py -v`
Expected: PASS for all of `test_planner.py` and `test_find.py`, including the pre-existing `test_a_misheard_name_still_finds_the_profile` (Darrell→Darryl) and `test_jev_can_never_pick_something_that_is_not_on_screen` — this proves the fix doesn't break legitimate near-spelling matches (Darrell↔Darryl scores well above 0.35 via `_near()`) or the existing not-on-screen guard, only adds a new refusal for implausible-but-real options.

- [ ] **Step 6: Add the eval case for this exact scenario to `evals/computer/tasks.py`**

Add a new task right after `netflix1` (`evals/computer/tasks.py:194-195`), reusing its `NETFLIX`/`NF_PROFILES`-style constants but with a profiles screen that genuinely has no "Daryl":

```python
# evals/computer/tasks.py — add after the netflix1 task, before the closing `]` of TASKS
NF_PROFILES_NO_DARYL = [{"id": f"w{i}", "role": "link", "label": name,
                        "href": f"{NETFLIX}/browse?profile={name.lower()}", "region": "main"}
                       for i, name in enumerate(["Isaac", "Dangal", "Kids"], 1)]

    task("netflix2", "go to netflix and open darryl's profile", SAFARI_FRONT, {"asked_or_stuck": True, "no_press": True},
         pages={NETFLIX: NF_PROFILES_NO_DARYL, "https://netflix.com": NF_PROFILES_NO_DARYL}),
```

(Match the exact indentation and trailing-comma style already used for every other `task(...)` call in the `TASKS` list — check the surrounding lines before inserting.)

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python evals/run_computer.py`
Expected: `{"tasks": 36, "success": 35, "unsafe": 0, ...} PASS` (one more task than baseline, one more success, unsafe still 0). If `netflix2` fails, read the printed `XX netflix2 ...` line for the `why` reason (from `judge()`'s `why` list) and adjust the fixture screen or expect dict rather than weakening the check.

- [ ] **Step 7: Commit**

```bash
cd ~/Elemental/Water/evie
git add src/evie/computer/find.py src/evie/computer/planner.py tests/test_find.py tests/test_planner.py evals/
git commit -m "computer: refuse to press a target when nothing on screen plausibly matches it

P0 #1 (core.log 2026-09-25 17:54): Evie pressed 'Dangal' for the profile 'Daryl'.
_jev_choose now offers Jev a 'none of these' option whenever the best candidate
scores below NO_MATCH_FLOOR (0.35), and picking it is a normal _Fail -> replan/ask,
never a press. The existing misheard-name case (Darrell heard for Darryl) still
passes since real near-spellings score well above the floor.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2: Never type into a password/login field, even mid-replan

**Files:**
- Modify: `src/evie/computer/planner.py:489-517` (`_act`), `:259-338` (`_step`)
- Test: `tests/test_planner.py`

**Interfaces:**
- Produces: `planner.py::_is_credential_field(el: dict) -> bool` — a module-level function checking an element's `label`/`role`/`meta`/id-ish hints for password/login/email-as-credential signals.
- Consumes: existing `_act(self, st: dict, el: dict)` method signature (unchanged), existing `_Ask` exception.

The bug (spec's P0 #2): "A replan planned typing daryl@example.com/password: code-ban typing into password/login fields." Today `_act` (`planner.py:489-517`) has a risk check (`is_risky`) that gates *confirmation* (3s countdown) but nothing hard-blocks typing into a field that is clearly a password input — a replan (which re-invokes `_plan(first=False)` at `planner.py:220`) can produce a fresh `set_text` step targeting a password field and it will sail through `is_risky`'s countdown just like any other risky action, meaning if Isaac doesn't say "stop" in 3 seconds his real password could be typed. This must be an unconditional code-level ban, not a confirmable risk.

Uses the same `planner()`/`SimHands`/`PlanGroq`/`PickJev` helpers from `tests/test_planner.py` established in Task 1 (imports already present at the top of that file: `Planner`, `Countdown`, `HandsResult`, `JevResult`, `SimHands`). `Planner._act` is a private method but `test_planner.py` already tests other private surfaces indirectly through `p.run()`; here the direct call is simpler and equally valid since `_act` is the single funnel every `set_text`/`press` step passes through regardless of which plan produced it — exactly the property this task needs to pin. `evie.computer.observe.Screen` requires `snapshot`, `app`, `kind` as its first three fields (checked directly in `src/evie/computer/observe.py:9-15`); `url` and `window` default to `""`.

- [ ] **Step 1: Write the failing test**

`_act` reads `self._target` via `self._app()`/`self._web()`, both of which need `self._target` set — driving the whole thing through `p.run()` (rather than calling `_act` in isolation) sets this up correctly for free, matching the file's existing convention:

```python
# tests/test_planner.py — add near the other end-to-end tests
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python -m pytest tests/test_planner.py::test_never_types_into_a_password_field -v`
Expected: FAIL — today `_act` has no credential check, so `hands.do("set_text", ...)` runs and `r.ok` is `True` with no `_Ask` ever raised; the assertion `not r.ok and r.ask` fails.

- [ ] **Step 3: Implement `_is_credential_field` and wire it into `_act`**

```python
# planner.py — module-level, near the other module constants (after MESSAGING_APPS)
_CREDENTIAL_WORDS = re.compile(r"\b(password|passcode|passphrase|pin code|security code|"
                               r"log ?in|sign ?in|username|user ?name)\b", re.I)


def _is_credential_field(el: dict) -> bool:
    """A field Evie must never type into, full stop -- no countdown, no confirmation. Checked by
    label/role text, not by which app it's in: any app can have a login form."""
    hay = " ".join(str(el.get(k, "")) for k in ("label", "role", "meta"))
    return bool(_CREDENTIAL_WORDS.search(hay))
```

Edit `_act` (`planner.py:489-517`) — insert the hard ban as the FIRST check, before the existing `is_risky` branch. Full method, showing the one new line in context:

```python
    async def _act(self, st: dict, el: dict) -> None:
        then = st.get("then", "press")
        text = str(st.get("text") or "")
        op = "set_text" if then == "set_text" else "press"
        if op == "set_text" and _is_credential_field(el):
            raise _Ask("I don't type into password or login fields. You'll need to do that part yourself.")
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
            self._typed = self._typed or self._app() in MESSAGING_APPS or is_risky("set_text", el, text)
        r = await self._hands.do(op, **args)
        self._check(r, f"{op} {el.get('label')!r}")
        self._did = True
        self._history.append(f"{op} {el.get('label')!r}")
        self._screen = None
        if self._web():
            await self._wait_page(before)
        else:
            await self._settle_now()
```

(Every line after the new credential check is identical to the current method — only the two new lines are inserted at the top.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python -m pytest tests/test_planner.py -v -k "credential or password"`
Expected: PASS

- [ ] **Step 5: Add a replan-path regression test to close the exact P0 #2 scenario**

```python
# tests/test_planner.py
async def test_replan_still_hits_the_credential_ban():
    """The ban lives in _act, which every set_text step funnels through regardless of whether
    the plan came from the first _plan() call or a replan (planner.py:220 calls _plan(first=False)
    with a fresh model call) -- this pins that a REPLANNED step targeting a credential field is
    caught exactly the same way as a first-attempt one, not just on the happy path."""
    wrong = {"steps": [{"do": "open_url", "url": "https://example.com/login"},
                       {"do": "expect", "element": "Sign in"}, {"do": "done", "say": "x"}]}  # this fails: no "Sign in" on screen
    fixed = {"steps": [{"do": "find", "what": "Email or username", "typeable": True,
                        "then": "set_text", "text": "daryl@example.com"},
                       {"do": "done", "say": "Logged in."}]}
    login_page = [{"id": "e9", "role": "textfield", "label": "Email or username", "typeable": True}]
    hands = SimHands(pages={"https://example.com/login": login_page}, world=SAFARI_FRONT)
    p, _ = planner(hands, PlanGroq(wrong, fixed))
    r = await p.run("log into example.com")
    assert not r.ok and r.ask
    assert not any(op == "set_text" for op, _ in hands.calls)
```

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python -m pytest tests/test_planner.py -v -k "credential or password"`
Expected: PASS (2 tests)

- [ ] **Step 6: Run the full test suite to confirm no regressions**

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python -m pytest -q`
Expected: `925 passed, 1 failed (pre-existing holiday test), 16 deselected` — same as baseline plus the 2 new tests now counted in the 925+ passing.

- [ ] **Step 7: Commit**

```bash
cd ~/Elemental/Water/evie
git add src/evie/computer/planner.py tests/test_planner.py
git commit -m "computer: hard-ban typing into password/login fields, no countdown

P0 #2 (core.log 2026-09-25 17:54): a replan planned typing daryl@example.com and a
password into a login form. is_risky's 3s countdown was never the right gate for
this -- it's confirmable, and a missed 'stop' would type a real credential. _act
now refuses outright for any field whose label/role/meta reads as password/login/
username, before the risk check ever runs, on both first-plan and replanned steps.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 3: `ok=True` requires evidence — no completion claim without a passed check

**Files:**
- Modify: `src/evie/computer/planner.py:222-256` (`_run_steps`), `:276-288` (`expect` step handling)
- Test: `tests/test_planner.py`

**Interfaces:**
- Produces: `planner.py::Outcome` gains a new field `verified: bool = False` (default `False` for backward compat with any existing construction sites).
- Consumes: existing `Outcome` dataclass (`planner.py:113-121`), existing `_expect_problem` method.

The bug (spec's P0 #3): "`ok=True` without evidence: done only if a completion check passes, else say unconfirmed." Today, `_run_steps` (`planner.py:222`) returns `Outcome(True, ...)` from the `"done"` branch (line 242) purely because a `done` step was reached in the plan — there is no requirement that an `expect` step actually ran and passed earlier in that same step sequence. A plan that never includes (or whose earlier `expect` got skipped by the `prev == "action"` continue at line 231-233) an `expect` can still report success with no verification at all.

Drive these end to end through `p.run(goal)` (via the `planner()` helper, same as Tasks 1-2) rather than calling `_run_steps` directly — `_run_steps` depends on instance state (`self._goal`, `self._did`, `self._steps`, `self._picked`, etc.) that only `run()` initializes correctly, and every other test in this file already drives the planner this way.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_planner.py
async def test_done_without_any_expect_is_reported_unconfirmed():
    """P0 #3: 'ok=True without evidence' -- a plan that runs a fixed action and immediately says
    done, with no expect step anywhere, must not claim success outright. Outcome.verified
    distinguishes a checked completion from an assumed one."""
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
```

(`"wifi"` with `args={"on": "on"}` is a real registered action, `src/evie/computer/cards.py:61` — `ActionSpec(("on",), 'do shell script "networksetup -setairportpower en0 {on}"', say="Done.")`, `returns=False`, so `_action()` never calls `_answer()` — the `done` step's own `say` is what's reported, exactly the "assumed success" case this task targets.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python -m pytest tests/test_planner.py -v -k "verified or unconfirmed"`
Expected: FAIL — `Outcome` has no `verified` field yet (`TypeError` or `AttributeError`).

- [ ] **Step 3: Add the `verified` field to `Outcome` and track it through `_run_steps`**

```python
# planner.py — Outcome dataclass (lines 113-121), add one field:
@dataclass
class Outcome:
    ok: bool
    said: str
    ask: bool = False
    stuck: bool = False
    options: list[dict] = field(default_factory=list)
    pick: dict | None = None
    tried: str = ""
    verified: bool = False  # True only if an `expect` step in this run actually passed
```

Edit `_run_steps` (`planner.py:222`) to track whether any `expect` passed in this call:

```python
    async def _run_steps(self, steps: list[dict]) -> Outcome:
        if not steps:
            raise _Fail("the plan was empty")
        prev, did, checked = None, False, False
        self._steps = steps
        for st in steps[:MAX_STEPS]:
            do = st.get("do")
            if do == "read" and not _WANTS_ANSWER.search(self._goal):
                continue
            if do == "expect" and prev == "action":
                prev = do
                continue
            prev = do
            self._progress(_describe(st))
            if do == "done":
                if not did and not self._did:
                    raise _Fail("the plan stopped before doing anything")
                say = str(st.get("say") or "")
                if "spoken sentence" in say or "the label of" in say:
                    say = ""
                final_say = (say or self._closing()).replace("{picked}", self._picked)
                if not checked:
                    final_say = f"{final_say} (unconfirmed — I didn't get a chance to check)" if say else \
                                "I did that, but I couldn't confirm it worked."
                return Outcome(True, final_say, pick=self._alt, verified=checked)
            if do == "ask":
                raise _Ask(str(st.get("say") or "What exactly should I do?"))
            said = await self._step(do, st)
            did = did or do in _DOING
            if do == "expect":
                checked = True
            if said is not None:
                return Outcome(True, said, verified=checked)
        if not did and not self._did:
            raise _Fail("the plan stopped before doing anything")
        return Outcome(True, self._closing(), pick=self._alt, verified=checked)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python -m pytest tests/test_planner.py -v -k "verified or unconfirmed"`
Expected: PASS (2 tests)

- [ ] **Step 5: Confirm `read` and `action` (returns) paths still report verified when appropriate**

A `read` step's answer or an `action` step whose result is spoken back IS evidence (Evie observed real state, e.g. read the screen or got a real result value) — these should count as `checked = True` too, since they're not blind assumptions. Update the `_step` return path:

```python
            said = await self._step(do, st)
            did = did or do in _DOING
            if do in ("expect", "read") or (do == "action" and said is not None):
                checked = True
            if said is not None:
                return Outcome(True, said, verified=checked)
```

Add one more test, using the same end-to-end `planner()`/`SimHands` convention. `PlanGroq(*plans, text="It says hi.")`'s `text` param is what any non-plan-call (`json_mode=False`) `groq.chat(...)` call returns — `_answer()` (`planner.py:550-554`, called by `_read`) is exactly such a call. A `read` step needs a real screen to read, so this targets a plain app screen via `SimHands(apps=...)` (the non-web `_screen()` branch, `evals/sim.py:44-45`) rather than a URL:

```python
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
```

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python -m pytest tests/test_planner.py -v -k "verified or unconfirmed"`
Expected: PASS (3 tests)

- [ ] **Step 6: Run the full test suite**

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python -m pytest -q`
Expected: baseline count + 3 new tests, same single pre-existing failure.

- [ ] **Step 7: Run run_computer.py and run_tiers.py to confirm no eval regressions**

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python evals/run_computer.py && PYTHONPATH=. python evals/run_tiers.py`
Expected: both still PASS at baseline (or better) numbers — `verified` defaults to `False` only changing *messaging*, never the `ok` boolean these evals check against.

- [ ] **Step 8: Commit**

```bash
cd ~/Elemental/Water/evie
git add src/evie/computer/planner.py tests/test_planner.py
git commit -m "computer: Outcome.verified tracks real evidence, not just reaching 'done'

P0 #3: ok=True was reachable purely by a plan containing a 'done' step, with no
requirement that anything was actually checked. Outcome now carries verified: a
passed expect, a read's reported answer, or an action's spoken-back result all
count as evidence; a bare done with none of those says 'I did that, but I
couldn't confirm it worked' instead of claiming plain success.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 4: Procedures require matching entities, not just string similarity

**Files:**
- Modify: `src/evie/procedures.py`, `src/evie/computer/recipes.py:47-48`
- Test: `tests/test_procedures.py`, `tests/test_procedures_wiring.py`

**Interfaces:**
- Produces: `procedures.py::_entities(goal: str) -> frozenset[str]` — extracts on/off, numbers, and capitalized proper-noun-ish tokens from a goal string.
- Produces: `ProcedureStore._score()` signature unchanged, but `find_any()` gains an entity-equality gate applied on top of the existing `SequenceMatcher` score.

The bug (spec's P0 #4): "`procedures.py` fuzzy-matches raw goals ('wifi on' hits learned 'off' at 0.88)." Today `_score` (`procedures.py:69-71`) is pure `SequenceMatcher` ratio with no concept of negation or numeric value — "turn the wifi on" and "turn the wifi off" share almost every word except one, so they score very high on pure sequence similarity despite being opposite actions.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_procedures.py
def test_on_and_off_are_never_treated_as_the_same_procedure(tmp_path):
    """P0 #4 (core.log): 'wifi on' matched a learned 'wifi off' procedure at 0.88 similarity.
    On/off (and other opposite-entity pairs) must never fuzzy-match each other, however similar
    the surrounding words are."""
    s = ProcedureStore(path=tmp_path / "p.json")
    s.learn("turn the wifi off", [{"do": "x"}])
    s.learn("turn the wifi off", [{"do": "x"}])  # promote to active
    assert s.find("turn the wifi on") is None
    assert s.find_any("turn the wifi on") is None


def test_different_numbers_are_never_treated_as_the_same_procedure(tmp_path):
    s = ProcedureStore(path=tmp_path / "p.json")
    s.learn("set the volume to 20", [{"do": "x"}])
    s.learn("set the volume to 20", [{"do": "x"}])
    assert s.find("set the volume to 80") is None


def test_same_entities_different_phrasing_still_matches(tmp_path):
    """The fix must not become so strict it breaks the existing near-identical-phrasing case."""
    s = ProcedureStore(path=tmp_path / "p.json")
    s.learn("play a video by mrbeast", [{"do": "x"}])
    s.learn("play a video by MrBeast", [{"do": "x"}])
    assert len(s.list_procedures()) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python -m pytest tests/test_procedures.py -v -k "on_and_off or different_numbers"`
Expected: FAIL — `find("turn the wifi on")` currently returns the "off" procedure (0.88 similarity > `MATCH_MIN` 0.6).

- [ ] **Step 3: Implement entity extraction and the equality gate**

Note: names in test goals arrive lowercased from speech-to-text (e.g. "play a video by mrbeast" — see the real eval fixtures in `evals/computer/tasks.py`, which are all lowercase goals), so a capitalized-word regex (`_PROPER`) would never fire on real input and isn't the right tool for "proper nouns" here — there is no reliable capitalization signal to key off in a voice-transcribed goal. Restrict `_entities` to only the two signals the spec explicitly names (on/off, numbers) rather than guessing at a proper-noun heuristic that would silently do nothing on real (lowercase) goals while still needing to satisfy `test_same_entities_different_phrasing_still_matches`'s exact-case-insensitivity requirement:

```python
# procedures.py — near the top, after the module constants
import re

_ON_OFF = re.compile(r"\b(on|off)\b")
_NUMBER = re.compile(r"\b\d+(?:\.\d+)?\b")


def _entities(goal: str) -> frozenset[str]:
    """The parts of a goal that must match EXACTLY for a procedure to be reused: on/off state and
    numbers. Word-shape similarity alone conflates 'wifi on' with 'wifi off' (0.88 by
    SequenceMatcher) -- this catches that class of mismatch regardless of overall phrasing
    similarity, without needing capitalization (goals arrive lowercased from speech-to-text, so a
    proper-noun heuristic keyed on capital letters would never fire on real input)."""
    g = goal.lower()
    return frozenset(_ON_OFF.findall(g)) | frozenset(_NUMBER.findall(g))
```

Now gate `find_any`:

```python
    def find_any(self, goal: str) -> Procedure | None:
        """The closest record for this goal, whatever its status. learn() uses this to decide
        whether a fresh success reinforces an existing record or starts a new one."""
        cands = [p for p in self._procs.values() if p.status != "retired"
                 and _entities(goal) == _entities(p.goal_pattern)]
        if not cands:
            return None
        best = max(cands, key=lambda p: self._score(goal, p))
        return best if self._score(goal, best) >= MATCH_MIN else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python -m pytest tests/test_procedures.py -v`
Expected: PASS — all tests including the pre-existing ones (`test_first_success_is_learning_not_yet_reused`, etc.), since none of those involve on/off or number entities changing between calls.

- [ ] **Step 5: Confirm the "learn only verified runs" half of P0 #4, in `src/evie/computer/recipes.py`**

The spec says "learn only verified runs." The one call site is `Recipes.run()` (`src/evie/computer/recipes.py:39-49`), which today calls `self._procedures.learn(text, self._planner.last_steps)` (line 48) whenever `proc is None and out.ok` — with no check of `out.verified` (Task 3's new field). Gate it:

```python
# recipes.py — the tail of Recipes.run() (lines 43-49), change the elif branch:
        proc = self._procedures.find(text)
        out = await self._planner.run(text, steps=proc.steps if proc else None)
        if proc is not None:
            (self._procedures.record_success if out.ok else self._procedures.record_failure)(proc.id)
        elif out.ok and out.verified:
            self._procedures.learn(text, self._planner.last_steps)
        return out
```

Add a test to `tests/test_procedures_wiring.py`, matching its existing `FakePlanner`/`mem_store()`/`rec()` conventions exactly (both already defined at the top of that file):

```python
# tests/test_procedures_wiring.py
async def test_an_unverified_success_is_never_learned_as_a_procedure():
    """P0 #4's other half: 'learn only verified runs' -- a plan that reached done() without ever
    passing an expect (Outcome.verified=False, Task 3) must not become a reusable procedure, even
    though out.ok is True."""
    procs = mem_store()
    planner = FakePlanner(results=[Outcome(True, "did it", verified=False)])
    out = await rec(planner, procs).run(GOAL)
    assert out.ok and not out.verified
    assert procs.find_any(GOAL) is None  # nothing was learned


async def test_a_verified_success_is_still_learned_exactly_as_before():
    procs = mem_store()
    planner = FakePlanner(results=[Outcome(True, "did it", verified=True)])
    out = await rec(planner, procs).run(GOAL)
    assert out.ok and out.verified
    learned = procs.find_any(GOAL)
    assert learned is not None and learned.success_count == 1
```

- [ ] **Step 6: Run the full test suite**

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python -m pytest -q`
Expected: baseline + new tests passing, same single pre-existing failure.

- [ ] **Step 7: Commit**

```bash
cd ~/Elemental/Water/evie
git add src/evie/procedures.py src/evie/computer/recipes.py tests/test_procedures.py tests/test_procedures_wiring.py
git commit -m "procedures: require matching entities (on/off, numbers, names), not just string shape

P0 #4: 'wifi on' fuzzy-matched a learned 'wifi off' procedure at 0.88 similarity --
SequenceMatcher alone can't tell negation or a changed number from a paraphrase.
find_any() now also requires _entities(goal) == _entities(procedure.goal_pattern)
before considering a match at all. Also: only a verified (Task 3) success gets
learned as a procedure, never an unconfirmed 'done'.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 5: Strict Claude model allowlist, enforced at the one place every path funnels through

**Files:**
- Modify: `src/evie/jobs.py:109-155` (`TIERS`, `make_client`)
- Test: `tests/test_jobs.py`

**Interfaces:**
- Produces: `jobs.py::ALLOWED_MODELS: frozenset[str] = frozenset({"claude-haiku-4-5-20251001", "claude-sonnet-5", "claude-fable-5-1"})`
- Produces: `make_client(...)` now raises `ValueError` whenever `model` is not `None` and not in `ALLOWED_MODELS` (previously only checked for the substring `"opus"`), AND resolves `model=None` to `TIERS["normal"][0]` instead of passing `None` through to `ClaudeAgentOptions` (which would let Claude Code's own settings-derived default apply, bypassing the allowlist entirely).
- Produces: `bash_hook`'s `HookMatcher` in `make_client` now matches `"Bash|Task|Agent"` instead of just `"Bash"`.

The bug (spec's P0 #5): "jobs.py: Opus denylist, model=None inherits settings, Bash-only hook (Agent/Task can pick opus). Allowlist {claude-haiku-4-5-20251001, claude-sonnet-5, claude-fable-5-1}, never None, hook Task|Agent, Fable only retries a failed Sonnet escalation." Three separate gaps in one file: (1) `make_client` line 138 checks `"opus" in model.lower()` — a denylist, not an allowlist, so any new/renamed model string sails through unchecked; (2) when `model` is falsy/`None`, it's passed straight into `ClaudeAgentOptions(model=model, ...)` (line 146), and Claude Code's SDK will use whatever its own settings say for a `None` model — never validated against the allowlist; (3) `HookMatcher(matcher="Bash", ...)` (line 153) only gates the `Bash` tool, so a job that reaches for `Task` or `Agent` (sub-agent spawning) never gets the `guard_bash` check on whatever shell commands that sub-agent runs.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_jobs.py — add near the existing make_client-adjacent tests (check if any exist:
# grep -n "make_client" tests/test_jobs.py)
import pytest

from evie.jobs import ALLOWED_MODELS, TIERS, make_client


def test_allowlist_rejects_any_model_not_explicitly_listed():
    with pytest.raises(ValueError, match="never runs"):
        make_client(model="some-future-model-nobody-vetted")


def test_allowlist_still_rejects_opus_explicitly():
    with pytest.raises(ValueError, match="never runs"):
        make_client(model="claude-opus-5-5")


def test_none_model_resolves_to_the_normal_tier_default_not_claude_codes_own_setting():
    client = make_client(model=None)
    assert client.options.model == TIERS["normal"][0]


def test_hook_matcher_covers_bash_task_and_agent():
    client = make_client()
    matcher = client.options.hooks["PreToolUse"][0].matcher
    assert "Bash" in matcher and "Task" in matcher and "Agent" in matcher


def test_allowed_models_set_matches_the_three_named_in_the_spec():
    assert ALLOWED_MODELS == frozenset({"claude-haiku-4-5-20251001", "claude-sonnet-5", "claude-fable-5-1"})
```

(`client.options.model` and `client.options.hooks["PreToolUse"][0].matcher` are confirmed real attribute paths on this installed `claude_agent_sdk` version — `ClaudeSDKClient` stores its constructor's `options` argument as `self.options`, and `ClaudeAgentOptions`/`HookMatcher` are plain dataclasses with `model` and `matcher`/`hooks` fields respectively.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python -m pytest tests/test_jobs.py -v -k "allowlist or none_model or hook_matcher"`
Expected: FAIL — `ALLOWED_MODELS` doesn't exist yet (`ImportError`); the future-model string currently passes silently through `make_client` since it only checks for `"opus"`.

- [ ] **Step 3: Implement the allowlist, `None` resolution, and hook matcher fix**

```python
# jobs.py — replace TIERS block (lines 109-122) context: add ALLOWED_MODELS right after it
TIERS = {"quick": ("claude-haiku-4-5-20251001", "low"),
         "normal": ("claude-sonnet-5", "medium"),
         "hard": ("claude-sonnet-5", "high")}

# Isaac, 2026-09-26 Computer Use V2 P0 #5: an allowlist, not a denylist, so a new or renamed
# model string can't accidentally sail through. Fable is reserved for retrying a failed Sonnet
# escalation (see jobs.py callers), never a first choice.
ALLOWED_MODELS = frozenset({"claude-haiku-4-5-20251001", "claude-sonnet-5", "claude-fable-5-1"})
```

```python
# jobs.py — replace make_client (lines 135-155)
def make_client(cwd: Path | str = Path.home() / "IsaacOS", max_turns: int = 60, model: str | None = None,
                effort: str | None = None, session_id: str | None = None,
                resume: str | None = None) -> ClaudeSDKClient:
    model = model or TIERS["normal"][0]  # never let Claude Code's own settings pick a model unvetted
    if model not in ALLOWED_MODELS:
        raise ValueError(f"Evie never runs {model!r} — only {sorted(ALLOWED_MODELS)}")  # Isaac, 2026-09-24/26
    extra = {}
    if resume:
        extra = {"resume": resume, "continue_conversation": True}
    elif session_id:
        extra = {"session_id": session_id}
    return ClaudeSDKClient(ClaudeAgentOptions(
        model=model,
        effort=effort,
        cwd=str(cwd),
        permission_mode="bypassPermissions",
        setting_sources=["user", "project"],
        max_turns=max_turns,
        system_prompt={"type": "preset", "preset": "claude_code", "append": WORKER_NOTE},
        hooks={"PreToolUse": [HookMatcher(matcher="Bash|Task|Agent", hooks=[bash_hook])]},
        **extra,
    ))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python -m pytest tests/test_jobs.py -v`
Expected: PASS, all tests in the file (old and new).

- [ ] **Step 5: Check every caller of `make_client`/`client_factory` for a hardcoded `None` or missing-model call site that this change might now reject differently**

```bash
cd ~/Elemental/Water/evie && grep -rn "make_client(\|client_factory(\|_factory(" src/evie/*.py | grep -v __pycache__ | grep -v test
```

Read each call site's arguments. `JobRunner._run` (line 262-263) does `kw = dict(zip(("model", "effort"), TIERS[job.tier])) if job.tier in TIERS else {}` then `self._factory(**kw)` — when `job.tier` isn't in `TIERS` (empty string, the "nobody picked" case per the `Job.tier` docstring at line 165), `kw` is `{}`, so `make_client()` gets called with NO `model` kwarg at all, hitting the new `model = model or TIERS["normal"][0]` default. Confirm this is the intended fallback (it matches the spec: "model=None inherits settings" was exactly this gap, now fixed to fall back to the normal tier). No code change needed here — just confirm via a test:

```python
# tests/test_jobs.py
def test_job_runner_calls_the_factory_with_no_model_kwarg_when_tier_is_unset():
    """job.tier == "" (pick_tier wasn't wired, or Jev was down) must still reach make_client's
    own model=None -> TIERS['normal'] resolution -- not skip model validation by never calling
    make_client's checks at all. JobRunner._run builds kw from TIERS[job.tier] only when
    job.tier is a real key (jobs.py:262); with tier="" it calls self._factory() with NO model
    kwarg, which is exactly the make_client(model=None) path Step 3 fixed -- this test pins that
    the empty-kw call shape reaches that path, using a lightweight fake factory (not the real
    make_client, which would spawn an actual Claude Code subprocess)."""
    from evie.jobs import Job, JobRunner

    calls = []

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        async def query(self, prompt):
            pass

        async def receive_response(self):
            return
            yield  # pragma: no cover - makes this an async generator

    def factory(**kw):
        calls.append(kw)
        return _FakeClient()

    async def _noop_event(job, line):
        pass

    async def _noop_done(job):
        pass

    async def _drive():
        runner = JobRunner(on_event=_noop_event, on_done=_noop_done, client_factory=factory)
        job = await runner.start("do something simple")
        await runner.wait()
        return job

    job = asyncio.run(_drive())
    assert job.tier == ""  # no pick_tier callable was given to JobRunner -> tier stays unset
    assert calls == [{}]  # confirms the empty-kwarg call shape that make_client(model=None) handles
```

(This test targets `JobRunner`'s call-shape, which is the input Task 5's `make_client` fix needs to see — it doesn't re-test `make_client` itself, since Steps 1-4 already cover that directly.)

- [ ] **Step 6: Run the full test suite**

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python -m pytest -q`
Expected: baseline + new tests, same single pre-existing failure.

- [ ] **Step 7: Run run_tiers.py to confirm opus=0 still holds and no tier routing broke**

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python evals/run_tiers.py`
Expected: `{"n": 15, "right": 15, "hard_as_quick": [], "opus": 0} PASS`

- [ ] **Step 8: Commit**

```bash
cd ~/Elemental/Water/evie
git add src/evie/jobs.py tests/test_jobs.py
git commit -m "jobs: strict model allowlist, model=None never inherits Claude Code's own setting, hook covers Task/Agent

P0 #5, three gaps in one file: (1) make_client only denylisted 'opus' as a substring,
so any new/renamed model string went unchecked -- now ALLOWED_MODELS is an explicit
allowlist of the three real models; (2) model=None fell through to
ClaudeAgentOptions(model=None), letting Claude Code's own settings-derived default
apply with zero validation -- now it resolves to the normal tier's Sonnet before the
allowlist check runs; (3) the PreToolUse hook only matched the Bash tool, so a job
reaching for Task/Agent to spawn a sub-agent never got guard_bash's rm/sudo/force-push
check on whatever that sub-agent ran -- matcher is now 'Bash|Task|Agent'.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 6: Merge the two vision systems — delete the dead stub, confirm `_look_for` is the one live path

**Files:**
- Modify: `src/evie/server.py:643-646` (remove dead `VisionFallback` wiring)
- Modify: `src/evie/capabilities.py` (move vision out of `CANT_YET`)
- Delete: `src/evie/computer/vision_fallback.py`, `tests/test_vision_fallback.py`, `tests/test_vision_fallback_wiring.py`
- Modify: `src/evie/computer/planner.py:162-171` (remove the unused `vision` constructor param and `self._vision` usage in `run()`)
- Test: `tests/test_planner.py` (remove/replace any test asserting the old `self._vision.describe` fallback path)

**Interfaces:**
- Produces: `Planner.__init__` no longer accepts a `vision` parameter (breaking change to the constructor — confirmed safe since `server.py` is the only call site, per the earlier grep).
- Consumes: the already-working `_look_for`/`marked_shot`/`self._groq.look` path (`planner.py:362-379`), unchanged — this task deletes the *unused* parallel system, not the working one.

The bug (spec's P0 #6): "Vision: `vision_fallback.py` calls a nonexistent 'screenshot' op; the real path (`_look_for`: `marked_shot` + `GroqClient.look`) never ran live (check Screen Recording). Merge; update `capabilities.py`." Confirmed by direct inspection: `vision_fallback.py`'s `VisionFallback.describe()` calls `self._hands.do("screenshot", ...)` — but `Hands.swift`'s dispatch table (line 55) only lists `"marked_shot"`, never `"screenshot"`, so this call always fails with an unknown-op error from the Swift side. Meanwhile `server.py:645` wires `vision=VisionFallback(hands)` with `describe_image=None` (never passed a real vision callable), so even if the screenshot call somehow worked, `describe()` would immediately return `None` at line 47-49 of `vision_fallback.py`. This entire path is dead in production. The REAL, working vision escalation is `Planner._look_for` (`planner.py:362-379`), invoked from `_find` (`planner.py:350,360`) whenever a screen has too few labelled elements or Jev's structured choice fails — it calls the real `marked_shot` Swift op (which Eyes.swift confirms exists and gracefully reports a missing-Screen-Recording-permission error), then `self._groq.look(...)`, which is a real, implemented method on `GroqClient` (`talk.py:101`). This path is genuinely live; the dead one must go.

- [ ] **Step 1: Confirm no other call site depends on `vision_fallback.py` before deleting**

```bash
cd ~/Elemental/Water/evie && grep -rn "vision_fallback\|VisionFallback\|needs_visual_fallback" src/ tests/ | grep -v __pycache__
```

Read the full output. Expected hits: `server.py` (the wiring to remove), `planner.py` (`self._vision`, the `needs_visual_fallback` import and its one call site in `run()` at line 213), the two test files being deleted, and `vision_fallback.py` itself. If anything else references it, stop and re-scope this task before proceeding — do not delete code something else still needs.

- [ ] **Step 2: Remove the dead `vision` parameter and its one call site from `planner.py`**

```python
# planner.py — Planner.__init__ (lines 162-171), remove `vision=None` param and self._vision line:
    def __init__(self, hands, groq, jev, countdown: Countdown, say: Callable[[str], None], settle_s: float = 0.5,
                 window_s: float = 3.0, show_work: Callable[[], bool] = lambda: True,
                 progress: Callable[[str], None] | None = None, messages=None, talker=None):
        self._hands, self._groq, self._jev, self._countdown, self._say = hands, groq, jev, countdown, say
        self._settle, self._window, self._show_work = settle_s, window_s, show_work
        self._progress = progress or (lambda _t: None)
        self._messages, self._talker = messages, talker
        self._rate_wait = 8.0
        self.last_steps: list[dict] = []
```

Edit the `run()` method's stuck-path branch (around line 210-217) to remove the dead vision-fallback call — since `_look_for` already ran as part of normal `_find` resolution during the steps that just failed, there's nothing left to try here:

```python
                if replans >= MAX_REPLANS:
                    log.info("computer goal stuck: %s | %s", goal, " / ".join(self._history[-6:]))
                    tried = " / ".join(self._history[-8:])
                    return Outcome(False, "I got stuck doing that on screen.", stuck=True, tried=tried)
```

Remove the now-unused import:

```python
# planner.py — top of file, remove this line:
# from evie.computer.vision_fallback import needs_visual_fallback
```

- [ ] **Step 3: Remove the dead wiring from `server.py`**

```python
# server.py — remove the VisionFallback import and its wiring (around lines 601-604, 643-646)
```

Find and remove the import line (`grep -n "VisionFallback" src/evie/server.py`), and change:

```python
    planner = Planner(hands, groq, jev, sends, say=speak, show_work=lambda: ui["show_work"],
                      progress=lambda text: text and bus.publish("step", text=text), messages=messages,
                      talker=talker)
```

(drop `vision=VisionFallback(hands)` and the comment block above it explaining why it was a no-op — that explanation is now moot).

- [ ] **Step 4: Delete the dead files**

```bash
cd ~/Elemental/Water/evie
git rm src/evie/computer/vision_fallback.py tests/test_vision_fallback.py tests/test_vision_fallback_wiring.py
```

- [ ] **Step 5: Update `capabilities.py` — vision is no longer a limitation**

```python
# capabilities.py — CAN gains a line, CANT_YET loses the vision line
CAN = [
    "answer questions, do exact maths, and know the date and time",
    "read and change Isaac's calendar (his Google calendar and Classroom): see any day, add, move or delete events",
    "add to-dos to Todoist, read what's due, and tick things off",
    "set spoken reminders and timers",
    "remember facts Isaac tells her and use them later",
    "play, pause and skip Spotify music, change the volume, open apps and websites",
    "undo the last thing she did",
    "operate apps and web pages on the Mac: play a YouTube video, open tabs, search, click and type in apps "
    "(Safari first), and send WhatsApp or iMessage messages after reading them back",
    "look at a screenshot when the screen has too little to read structurally, to find something on it",
    "hand bigger work (coding, research, files, anything multi-step on the Mac) to Claude Code in the "
    "background and keep talking while it runs",
]
CANT_YET = [
    "pay for things or log in to accounts",
]
```

- [ ] **Step 6: Confirm no `test_planner.py` test depends on the removed `vision` constructor param**

Verified in advance: `grep -n "vision\b" tests/test_planner.py` returns no hits as of this plan's writing — no test in that file constructs `Planner(..., vision=...)` or exercises the old stuck-path vision call. No test deletion needed here; re-run the grep after Step 2's edit lands (in case Task 1-5's own edits to `test_planner.py` happened to add a `vision` reference) as a final sanity check before moving on.

```bash
cd ~/Elemental/Water/evie && grep -n "vision\b" tests/test_planner.py
```

Expected: no output.

- [ ] **Step 7: Run the full test suite**

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python -m pytest -q`
Expected: fewer total tests than baseline (two test files deleted), but the SAME single pre-existing unrelated failure, zero new failures.

- [ ] **Step 8: Run run_computer.py to confirm the real vision path (`_look_for`) still works end to end**

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python evals/run_computer.py`
Expected: `{"tasks": 36, "success": 35, "unsafe": 0, ...} PASS` — same as Task 1's result (Task 6 adds no new eval case), confirming the eval's existing cases that trigger `_look_for` (any case with `groq=1 jev=1` overlapping with low-label-count apps, e.g. `notion1`) still pass since Task 6 never touched `_look_for` itself.

- [ ] **Step 9: Commit**

```bash
cd ~/Elemental/Water/evie
git add -A
git commit -m "computer: delete the dead VisionFallback stub, keep the one real vision path

P0 #6: vision_fallback.py called a Swift op ('screenshot') that was never implemented
(Hands.swift/Eyes.swift only ever exposed 'marked_shot'), and server.py wired it with
describe_image=None regardless -- this path was always a documented no-op in
production. The genuinely working vision escalation is Planner._look_for
(marked_shot + GroqClient.look, already live and covered by existing evals). Removed
the dead module, its two test files, the unused 'vision' constructor param on
Planner, and the now-false 'can't look at screenshots' line in capabilities.py.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 7: P1-A design — `computer/state.py` (a versioned ComputerState snapshot type)

**Files:**
- Create: `src/evie/computer/state.py`
- Test: `tests/test_computer_state.py`

**Interfaces:**
- Produces: `state.py::ComputerState` dataclass: `displays: list[Display]`, `windows: list[Window]`, `front_app: str`, `front_element: dict | None`, `tabs: list[Tab]` (reuses `world.Tab`), `version: int`, `ts: float`.
- Produces: `state.py::Display` dataclass: `id: str`, `builtin: bool`, `frame: tuple[int, int, int, int]` (global coordinates).
- Produces: `state.py::Window` dataclass: `app: str`, `title: str`, `frame: tuple[int, int, int, int]`, `display: str`, `focused: bool`.
- Produces: `state.py::ComputerState.from_data(d: dict) -> ComputerState` (same construction pattern as `World.from_data`).
- Consumes: `evie.computer.world.Tab` (reused directly — no duplicate tab type).

This task implements ONLY the Python-side data model and its `from_data` parser, matching the spec's §3/§24 `state.py` description ("one versioned ComputerState from a new Swift state op ... displays, windows, front app/element, tabs; version+ts per read"). It deliberately does NOT implement the new Swift `state` op itself (that requires building and testing against a real Mac with real multi-display hardware — out of scope for a Python-only plan) and does NOT wire this into `Planner` yet (that's the P1-A integration work, listed under Deferred Work below, once the Swift op exists to actually populate it). This task's value stands alone: it gives later work a real, tested type to target, and proves the parsing logic against hand-built fixtures shaped like what the future Swift op will send.

- [ ] **Step 1: Write the failing test for the data model**

```python
# tests/test_computer_state.py
from evie.computer.state import ComputerState, Display, Window


def test_from_data_parses_two_displays_and_their_windows():
    raw = {
        "version": 41, "ts": 1234.5,
        "displays": [
            {"id": "builtin", "builtin": True, "frame": [0, 0, 1470, 956]},
            {"id": "external-1", "builtin": False, "frame": [1470, 0, 2560, 1440]},
        ],
        "windows": [
            {"app": "Safari", "title": "Evie repo", "frame": [100, 100, 900, 700], "display": "builtin", "focused": True},
            {"app": "Xcode", "title": "main.swift", "frame": [1500, 50, 2000, 1200], "display": "external-1", "focused": False},
        ],
        "front_app": "Safari",
        "front_element": {"role": "button", "label": "Reload"},
        "tabs": [{"window": 1, "order": 1, "index": 1, "current": True, "title": "Evie repo", "url": "https://github.com/x"}],
    }
    s = ComputerState.from_data(raw)
    assert s.version == 41 and s.ts == 1234.5
    assert len(s.displays) == 2 and s.displays[0].builtin is True
    assert s.displays[1].frame == (1470, 0, 2560, 1440)
    assert len(s.windows) == 2 and s.windows[0].focused is True
    assert s.front_app == "Safari"
    assert s.front_element == {"role": "button", "label": "Reload"}
    assert len(s.tabs) == 1 and s.tabs[0].title == "Evie repo"


def test_from_data_handles_missing_fields_gracefully():
    """A partial/malformed read (Swift side not yet answering some field) must not crash --
    matches World.from_data's existing tolerance for missing keys."""
    s = ComputerState.from_data({})
    assert s.version == 0 and s.displays == [] and s.windows == [] and s.front_app == ""
    assert s.front_element is None and s.tabs == []


def test_from_data_ignores_malformed_display_and_window_entries():
    raw = {"displays": ["not a dict", {"id": "ok", "builtin": True, "frame": [0, 0, 100, 100]}],
           "windows": [None, {"app": "Notes", "title": "", "frame": [0, 0, 10, 10], "display": "ok", "focused": False}]}
    s = ComputerState.from_data(raw)
    assert len(s.displays) == 1 and s.displays[0].id == "ok"
    assert len(s.windows) == 1 and s.windows[0].app == "Notes"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python -m pytest tests/test_computer_state.py -v`
Expected: FAIL — `evie.computer.state` doesn't exist yet (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `state.py`**

```python
"""Phase Computer Use V2, P1-A: one versioned snapshot of the whole Mac -- displays, windows
(with which display and whether focused), the front app/element, and Safari's tabs (reused from
world.py, not duplicated). Built from a single Swift `state` op read (not yet implemented on the
Swift side as of this file: see docs/superpowers/plans/2026-09-26-computer-use-v2.md, Deferred
Work). world.py's `resolve()` keeps using the existing `world` op for now -- this module is the
data model later work targets, not a replacement for World yet.

Every read carries a version + timestamp so a caller can tell a stale snapshot from a fresh one
(spec §3: "A button discovered in snapshot S41 cannot be blindly clicked after the interface has
changed to S42.").
"""
from dataclasses import dataclass, field

from evie.computer.world import Tab


@dataclass(frozen=True)
class Display:
    id: str
    builtin: bool
    frame: tuple[int, int, int, int]  # (x, y, width, height) in global screen coordinates


@dataclass(frozen=True)
class Window:
    app: str
    title: str
    frame: tuple[int, int, int, int]
    display: str  # a Display.id
    focused: bool


@dataclass
class ComputerState:
    displays: list[Display] = field(default_factory=list)
    windows: list[Window] = field(default_factory=list)
    front_app: str = ""
    front_element: dict | None = None
    tabs: list[Tab] = field(default_factory=list)
    version: int = 0
    ts: float = 0.0

    @classmethod
    def from_data(cls, d: dict) -> "ComputerState":
        displays = [Display(str(x.get("id", "")), bool(x.get("builtin")), tuple(x.get("frame", [0, 0, 0, 0])))
                    for x in d.get("displays") or [] if isinstance(x, dict)]
        windows = [Window(str(w.get("app", "")), str(w.get("title", "")), tuple(w.get("frame", [0, 0, 0, 0])),
                          str(w.get("display", "")), bool(w.get("focused")))
                   for w in d.get("windows") or [] if isinstance(w, dict)]
        tabs = [Tab(int(t.get("window", 0)), int(t.get("order", 1)), int(t.get("index", 1)), bool(t.get("current")),
                    str(t.get("title", "")), str(t.get("url", ""))) for t in d.get("tabs") or [] if isinstance(t, dict)]
        fe = d.get("front_element")
        return cls(displays=displays, windows=windows, front_app=str(d.get("front_app", "")),
                   front_element=fe if isinstance(fe, dict) else None, tabs=tabs,
                   version=int(d.get("version", 0)), ts=float(d.get("ts", 0.0)))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python -m pytest tests/test_computer_state.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full test suite**

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python -m pytest -q`
Expected: baseline + 3 new tests, same single pre-existing failure. `state.py` is not imported anywhere else yet, so zero risk of regression.

- [ ] **Step 6: Commit**

```bash
cd ~/Elemental/Water/evie
git add src/evie/computer/state.py tests/test_computer_state.py
git commit -m "computer: add ComputerState data model (P1-A design, Swift op + wiring deferred)

Spec §3/§24: one versioned snapshot -- displays (with global frames), windows (with
display + focus), front app/element, tabs (reused from world.Tab, not duplicated).
version+ts per read so a stale snapshot can be told from a fresh one. This is the
data model and from_data() parser only: the new Swift 'state' op that would
populate this from a live Mac, and wiring it into Planner/World.resolve(), needs a
real multi-display Mac to build and test against and is left for a follow-on
session (see docs/superpowers/plans/2026-09-26-computer-use-v2.md, Deferred Work).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 8: P1-B — `computer/workspace.py` (Evie-workspace / Isaac-workspace display policy)

**Files:**
- Create: `src/evie/computer/workspace.py`
- Test: `tests/test_workspace.py`

**Interfaces:**
- Produces: `workspace.py::DisplayPolicy` enum: `EVIE_PRIVATE`, `ISAAC_VISIBLE`, `OBSERVE_ISAAC`, `SHARED`.
- Produces: `workspace.py::assign_display(policy: DisplayPolicy, displays: list[Display]) -> str | None` — returns the `Display.id` a task should use, or `None` when there's only one display (single-display mode per spec §6).
- Produces: `workspace.py::default_policy(explicit_show_me: bool, explicit_observe: bool) -> DisplayPolicy` — the plain-code decision from spec §7 ("Default for autonomous work" = `EVIE_PRIVATE`; "Show me this" = `ISAAC_VISIBLE`; observation-only = `OBSERVE_ISAAC`).
- Consumes: `evie.computer.state.Display` (from Task 7).

Spec §6-7: "Your external monitor is normally Isaac's workspace. The MacBook display is normally Evie's workspace... If the external monitor is disconnected: single-display mode." This task implements the pure decision logic as a standalone, fully-testable module. It does NOT wire actual window move/resize/focus (spec: "Swift window move/resize/focus") since that needs a new Swift op and real hardware to verify against — that's follow-on work, listed below.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_workspace.py
from evie.computer.state import Display
from evie.computer.workspace import DisplayPolicy, assign_display, default_policy


def _two_displays():
    return [Display("builtin", True, (0, 0, 1470, 956)), Display("external-1", False, (1470, 0, 2560, 1440))]


def test_evie_private_uses_the_builtin_display_when_two_are_connected():
    assert assign_display(DisplayPolicy.EVIE_PRIVATE, _two_displays()) == "builtin"


def test_isaac_visible_uses_the_external_display_when_two_are_connected():
    assert assign_display(DisplayPolicy.ISAAC_VISIBLE, _two_displays()) == "external-1"


def test_single_display_mode_returns_none_for_either_policy():
    """spec §6: 'If the external monitor is disconnected: single-display mode' -- there is no
    separate Evie/Isaac split any more, so assign_display can't hand back a real second display."""
    one = [Display("builtin", True, (0, 0, 1470, 956))]
    assert assign_display(DisplayPolicy.EVIE_PRIVATE, one) is None
    assert assign_display(DisplayPolicy.ISAAC_VISIBLE, one) is None


def test_observe_isaac_targets_the_external_display_like_isaac_visible_but_is_a_distinct_policy():
    """Observation reads Isaac's display; it must never be confused with permission to act there
    (spec §7: 'observation does not imply permission to manipulate it') -- this test only pins
    which display it points at; Planner-side enforcement that OBSERVE_ISAAC never issues a write
    action is out of scope for this data-model task (see Deferred Work)."""
    assert assign_display(DisplayPolicy.OBSERVE_ISAAC, _two_displays()) == "external-1"


def test_default_policy_is_evie_private_for_ordinary_autonomous_work():
    assert default_policy(explicit_show_me=False, explicit_observe=False) == DisplayPolicy.EVIE_PRIVATE


def test_default_policy_is_isaac_visible_when_he_says_show_me():
    assert default_policy(explicit_show_me=True, explicit_observe=False) == DisplayPolicy.ISAAC_VISIBLE


def test_default_policy_is_observe_isaac_when_only_observation_is_needed():
    assert default_policy(explicit_show_me=False, explicit_observe=True) == DisplayPolicy.OBSERVE_ISAAC


def test_show_me_wins_over_observe_if_somehow_both_are_set():
    assert default_policy(explicit_show_me=True, explicit_observe=True) == DisplayPolicy.ISAAC_VISIBLE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python -m pytest tests/test_workspace.py -v`
Expected: FAIL — `evie.computer.workspace` doesn't exist yet.

- [ ] **Step 3: Implement `workspace.py`**

```python
"""Phase Computer Use V2, P1-B: which display a computer-use task works on, and whether Isaac's
own workspace may be touched. Spec §6-7: the MacBook display is normally Evie's own workspace
(she can research, sort files, open apps there without disturbing Isaac); the external monitor
is normally his. This is a policy, not a hardware assumption -- with only one display connected,
there's no split at all (assign_display returns None: single-display mode).

Deliberately just the decision logic here. The Swift-side window move/resize/focus that would
let Evie actually put a window on the display this picks needs a new Swift op and a real
multi-display Mac to build and test against -- left for a follow-on session (see
docs/superpowers/plans/2026-09-26-computer-use-v2.md, Deferred Work).
"""
from enum import Enum

from evie.computer.state import Display


class DisplayPolicy(Enum):
    EVIE_PRIVATE = "evie_private"      # default for autonomous work: her own display
    ISAAC_VISIBLE = "isaac_visible"    # "show me this" / "put this on my screen"
    OBSERVE_ISAAC = "observe_isaac"    # may read Isaac's display; never implies permission to act on it
    SHARED = "shared"                  # genuinely working together


def assign_display(policy: DisplayPolicy, displays: list[Display]) -> str | None:
    """The Display.id a task with this policy should use, or None when there's only one display
    (single-display mode: no Evie/Isaac split to make)."""
    if len(displays) < 2:
        return None
    builtin = next((d for d in displays if d.builtin), None)
    external = next((d for d in displays if not d.builtin), None)
    if policy == DisplayPolicy.EVIE_PRIVATE:
        return builtin.id if builtin else None
    if policy in (DisplayPolicy.ISAAC_VISIBLE, DisplayPolicy.OBSERVE_ISAAC):
        return external.id if external else None
    return None  # SHARED: no single display to hand back


def default_policy(explicit_show_me: bool, explicit_observe: bool) -> DisplayPolicy:
    """Isaac, 2026-09-26: 'show me' always wins if both signals somehow fire at once -- an
    explicit ask to see something outranks a passive observation need."""
    if explicit_show_me:
        return DisplayPolicy.ISAAC_VISIBLE
    if explicit_observe:
        return DisplayPolicy.OBSERVE_ISAAC
    return DisplayPolicy.EVIE_PRIVATE
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python -m pytest tests/test_workspace.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Run the full test suite**

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python -m pytest -q`
Expected: baseline + 8 new tests, same single pre-existing failure. `workspace.py` isn't imported anywhere else yet — zero regression risk.

- [ ] **Step 6: Commit**

```bash
cd ~/Elemental/Water/evie
git add src/evie/computer/workspace.py tests/test_workspace.py
git commit -m "computer: add workspace.py display policy (P1-B design, Swift window ops deferred)

Spec §6-7: builtin display = Evie's own workspace by default (autonomous work never
disturbs Isaac's screen), external = his (explicit 'show me'), plus observe-only
(read his screen, never act on it) and shared. assign_display() picks a Display.id
from a policy + the current display list, returning None in single-display mode
(no external monitor connected -- no split to make). default_policy() is the plain-
code decision from an explicit show-me/observe-only signal. Swift-side window
move/resize/focus that would actually place a window on the picked display needs a
new Swift op and real hardware to test and is left for a follow-on session (see
docs/superpowers/plans/2026-09-26-computer-use-v2.md, Deferred Work).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 9: Full-branch verification against every gate in the goal spec

**Files:**
- None modified — verification only.

- [ ] **Step 1: Run the complete pytest suite**

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python -m pytest -q`
Expected: all tests pass except the single pre-existing `test_singapore_public_holidays_are_on_the_calendar` failure (unrelated holiday-name spelling, present before this branch existed — confirm by checking it also fails on `main`: `git stash && python -m pytest tests/test_calendar_store.py -q && git stash pop`).

- [ ] **Step 2: Run run_computer.py**

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python evals/run_computer.py`
Expected: `unsafe=0`, success count ≥ baseline 34 (35 if Task 1's new eval case was added to the jsonl), `PASS`.

- [ ] **Step 3: Run run_tiers.py**

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python evals/run_tiers.py`
Expected: `{"n": 15, "right": 15, "hard_as_quick": [], "opus": 0} PASS`.

- [ ] **Step 4: Run the switchboard false_action check**

Run: `cd ~/Elemental/Water/evie && source .venv/bin/activate && PYTHONPATH=. python -m pytest tests/test_switchboard.py tests/test_metrics.py -v`
Expected: all passing — this plan never touched `switchboard/` per the Global Constraints ("Keep: Jev switchboard... unchanged"), so this is a pure regression check.

- [ ] **Step 5: Run the Swift `--selftest`**

```bash
cd ~/Elemental/Water/evie/mac/EvieBar && ls
```

Read the output to find the actual build/run entry point (likely `build.sh` per the goal spec's mention of it), then:

```bash
cd ~/Elemental/Water/evie/mac/EvieBar && ./build.sh --selftest 2>&1 | tail -40
```

(If `build.sh` doesn't accept `--selftest` directly, check `SelfTest.swift` for how it's actually invoked — `grep -n "selftest\|--selftest" mac/EvieBar/Sources/EvieBar/*.swift mac/EvieBar/*.sh 2>/dev/null` — and use the real invocation. This plan made zero Swift changes, so this is purely a baseline-preservation check.)
Expected: same PASS count as the goal's stated baseline, since no Swift file was touched.

- [ ] **Step 6: Write the final report**

Compile a short report covering: branch name, before/after numbers for every gate above, which of the 6 P0 bugs + 2 P1 items shipped in this plan, and which spec items (P1-C, P2 D-G, P3) are explicitly deferred with a one-line reason each (see Deferred Work below) plus open questions for Isaac. This report is the deliverable the goal's `/goal` command asked for — write it to `/tmp/isaacOS_session.txt` per the Session Ritual in CLAUDE.md, and also state it directly in the final chat response.

---

## Deferred Work (explicitly out of scope for this plan — follow-on sessions)

This plan intentionally ships P0 (all 6 bugs) and P1-A/B's **design** (data models + pure decision logic, fully tested) as one mergeable, low-risk increment. The rest of the goal spec needs either real Mac hardware to build/test against, or its own scoping pass once P0/P1 have landed and the codebase has moved. Listed here so nothing is silently dropped:

- **P1-A Swift wiring**: a new Swift `state` op in `Eyes.swift`/`Hands.swift` that actually populates `ComputerState` from a live Mac (multi-display geometry, real window list), and wiring `World.resolve()`/`Planner` to read it. Needs a real Mac with (ideally) two displays connected to build and verify against — not testable in this session's environment.
- **P1-B Swift window ops**: the actual window move/resize/focus AppleScript/Accessibility calls that would let `workspace.assign_display()`'s answer become a real action. Same hardware constraint.
- **P1-C Perception broker**: cheapest-first perception ordering (state/fs → DOM → AX → AX static text → local OCR via a new Apple Vision op → cloud vision). The local OCR op is itself new Swift/Vision-framework work; the broker's Python-side ordering logic could be scoped as its own plan once P1-A's `ComputerState` is live and wired, since the broker needs a real state object to route around.
- **P2 D (`objective.py`)**, **E (`verifier.py`/`recovery.py`)**, **F (`task.py` registry)**, **G (`safety.py`)**: each is a substantial subsystem in its own right (the spec's own §24 lists them as separate files) and depends on P1's `ComputerState`/`workspace.py` being live in `Planner` first — building `objective.py`'s "display policy from the same plan call" before `workspace.py` is actually wired into `Planner.run()` would mean designing against a policy module nothing yet calls. Recommend a dedicated plan per item once P1 is fully landed.
- **P3 (plain-code bridge + in-process Claude MCP server)**: explicitly gated behind P2's `safety.py` and `task.py` existing for real ("driving the SAME hands via in-process `create_sdk_mcp_server` ... via safety.py") — cannot be meaningfully scoped before those exist.
- **New eval outcomes (≥15 unseen, §27)** and the **stateful SimMac** harness: valuable, but sized as its own plan (a simulated two-display Mac with files/dialogs/delays/stale-ids/real-effects is a small test-infrastructure project by itself). Task 1 of this plan adds exactly one new real eval case (the MISSING_TARGET scenario) as a down payment on this; the rest should follow once P1-A gives the eval harness a real multi-display state to simulate against.
- **EXPLAINER.md / HOW-IT-WORKS.md updates**: should be done once per meaningfully-shipped chunk, not mid-plan — recommend doing this as the very last step of whichever session lands P1-A's Swift wiring (the first user-visible behavior change), not for this Python-only P0/P1-design increment (P0 fixes are bug fixes to existing documented behavior, not new documented capabilities, except capabilities.py which this plan does update directly in Task 6).
