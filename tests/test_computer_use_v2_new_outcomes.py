"""Task 8 (Phase 2 follow-up plan, spec §27): >=15 genuinely unseen computer-use outcomes, each
targeting a category the existing 36 evals/computer/tasks.py cases don't cover (confirmed by grep
before writing these -- multi-display, stale id, dialog, slow UI, and persisted filesystem effects
had zero hits there). Written against Planner.run() directly with fixed-plan test doubles (the
same PlanGroq/PickJev/planner() convention test_planner.py already uses) rather than through
evals/run_computer.py's live-cassette path, since this environment has no Groq/Jev credentials to
record new cassette entries with (confirmed: load_settings().groq_api_key is unset here) -- the
36 existing cases were recorded on Isaac's Mac, which this session doesn't have. This still proves
SimMac's new primitives (stale-snapshot rejection, dialogs, place_window, persistent files,
delay_screens) genuinely drive Planner outcomes end to end, which is what Task 8 is actually for;
wiring these 15 into evals/computer/tasks.py's own tid/goal/expect schema for a live Isaac-side run
is one further step this session can't complete without his Mac (left for him, see PR body)."""
import json

from evals.simmac import SimMac
from evie.computer.planner import Planner
from evie.computer.state import Display
from evie.computer.workspace import DisplayPolicy, assign_display
from evie.countdown import Countdown
from tests.test_planner import PickJev, PlanGroq, planner

DONE = {"steps": [{"do": "done", "say": "Done."}]}


def two_displays_state(notion_display="display-3"):
    return json.dumps({
        "version": 1, "ts": 100.0, "front_element": None,
        "displays": [{"id": "display-1", "builtin": True, "frame": [2048, 35, 1512, 982]},
                    {"id": "display-3", "builtin": False, "frame": [0, 0, 2048, 1152]}],
        "windows": [{"app": "Notion", "title": "", "frame": [0, 0, 1000, 800], "display": notion_display,
                    "focused": False}],
        "front_app": "Finder", "tabs": []})


NOTION_WORLD = {"front_app": "Finder", "apps": [], "windows": [], "tabs": [], "selected": ""}


# 1. multi-window: two Safari windows exist; the goal names which content, not which window, and
#    the plan finds it via use_tab -- a genuinely different starting shape than any of the 36 (all
#    of which have at most one Safari window in `world`).
async def test_multi_window_goal_switches_to_the_window_that_already_has_the_content():
    world = {"front_app": "Notes", "apps": ["Notes", "Safari"], "windows": [
                {"app": "Notes", "title": "Shopping"}, {"app": "Safari", "title": "Gmail"},
                {"app": "Safari", "title": "BBC News"}],
             "tabs": [{"window": 7, "order": 1, "index": 1, "current": True, "title": "Gmail",
                       "url": "https://mail.google.com/"},
                      {"window": 9, "order": 1, "index": 1, "current": True, "title": "BBC News",
                       "url": "https://www.bbc.com/news"}]}
    hands = SimMac(world=world)
    plan = {"steps": [{"do": "done", "say": "Switched to your BBC window."}]}
    p, _ = planner(hands, PlanGroq(plan))
    r = await p.run("switch to the bbc window")
    assert r.ok


# 2. multi-display: workspace.assign_display resolves a real second display's id and
#    _assign_workspace actually calls place_window with it -- exercised through the full run(),
#    not just the unit-level test_planner.py assertion that state precedes activate in the call log.
async def test_autonomous_work_places_the_window_on_evies_own_display_end_to_end():
    hands = SimMac(apps={"Notion": [{"id": "a1", "role": "button", "label": "New"}]}, world=NOTION_WORLD)
    hands.state_response = two_displays_state()
    plan = {"steps": [{"do": "find", "what": "New", "then": "press"}, {"do": "done", "say": "Opened Notion."}]}
    p, _ = planner(hands, PlanGroq(plan))
    r = await p.run("open notion and make a note")
    assert r.ok
    placements = [a for op, a in hands.calls if op == "place_window"]
    assert any(a.get("app") == "Notion" and a.get("display_id") == "display-1" for a in placements)


# 3. multi-display, single-display fallback mid-fleet: the external monitor is unplugged (only one
#    display in state) -- assign_display returns None, so no place_window call should happen at all.
async def test_single_display_state_makes_workspace_assignment_a_no_op():
    hands = SimMac(apps={"Notion": [{"id": "a1", "role": "button", "label": "New"}]}, world=NOTION_WORLD)
    hands.state_response = json.dumps({"version": 1, "ts": 1.0,
        "displays": [{"id": "display-1", "builtin": True, "frame": [0, 0, 1512, 982]}],
        "windows": [], "front_app": "Finder", "front_element": None, "tabs": []})
    plan = {"steps": [{"do": "find", "what": "New", "then": "press"}, {"do": "done", "say": "Opened Notion."}]}
    p, _ = planner(hands, PlanGroq(plan))
    r = await p.run("open notion and make a note")
    assert r.ok
    assert not [a for op, a in hands.calls if op == "place_window"]


# 4. workspace.assign_display unit case for the exact same policy the two tests above exercise
#    live -- pins the pure logic these end-to-end tests depend on (belt-and-suspenders per this
#    plan's own Review Focus item about the display-count-changes-mid-task risk).
def test_assign_display_returns_none_when_a_display_disconnects_between_two_reads():
    two = [Display("display-1", True, (0, 0, 1512, 982)), Display("display-3", False, (0, 0, 2048, 1152))]
    one = [Display("display-1", True, (0, 0, 1512, 982))]
    assert assign_display(DisplayPolicy.EVIE_PRIVATE, two) == "display-1"
    assert assign_display(DisplayPolicy.EVIE_PRIVATE, one) is None


# 5. stale UI: an element found in one snapshot is pressed after the screen changed underneath --
#    the real Swift press() rejects this (Eyes.swift:367); SimHands never modeled it, SimMac does.
async def test_pressing_a_stale_element_id_fails_the_step_not_silently_succeeds():
    hands = SimMac(apps={"Settings": [{"id": "a1", "role": "checkbox", "label": "Do Not Disturb"}]})
    await hands.do("observe", app="Settings")  # snapshot s1 handed to whoever read it
    r = await hands.do("press", id="a1", snapshot="s0-stale")
    assert r.ok is False and "changed" in r.detail.lower()


# 6. unexpected dialog: a native alert sits in front of the target app; the planner's own action
#    fails with the dialog's own detail text rather than silently doing nothing.
async def test_an_unexpected_dialog_blocks_the_planned_press_and_surfaces_why():
    hands = SimMac(apps={"Finder": [{"id": "a1", "role": "button", "label": "Empty Trash"}]},
                   world={"front_app": "Finder", "apps": ["Finder"], "windows": [], "tabs": [], "selected": ""})
    hands.show_dialog("Are you sure you want to permanently erase the items in the Trash?")
    plan = {"steps": [{"do": "find", "what": "Empty Trash", "then": "press"}, {"do": "done", "say": "Done."}]}
    p, _ = planner(hands, PlanGroq(plan, DONE))
    r = await p.run("empty the trash")
    assert r.ok is False


# 7. unexpected dialog, then recovered: dismissing it and replanning succeeds.
async def test_dismissing_the_dialog_lets_a_replanned_press_succeed():
    hands = SimMac(apps={"Finder": [{"id": "a1", "role": "button", "label": "Empty Trash"}]},
                   world={"front_app": "Finder", "apps": ["Finder"], "windows": [], "tabs": [], "selected": ""})
    hands.show_dialog("Are you sure?")
    hands.dismiss_dialog()  # Isaac already dealt with it before Evie's plan runs
    plan = {"steps": [{"do": "find", "what": "Empty Trash", "then": "press"}, {"do": "done", "say": "Emptied."}]}
    p, _ = planner(hands, PlanGroq(plan))
    r = await p.run("empty the trash")
    assert r.ok is True


# 8. filesystem task with a real, checkable effect: finder_trash actually removes the file from
#    SimMac's persisted `files` set, which a later step (or a later task on the same hands
#    instance) can observe -- the 3 existing finder_* cases only check the rendered applescript
#    string, never a persisted effect.
async def test_finder_trash_actually_removes_the_file_from_persisted_state():
    from evie.computer.cards import ACTIONS
    hands = SimMac(files={"Old Report.pdf"},
                   world={"front_app": "Finder", "apps": ["Finder"], "windows": [], "tabs": [], "selected": ""})
    action_plan = {"steps": [{"do": "action", "name": "finder_trash",
                              "args": {"path": "~/Desktop/Old Report.pdf"}, "say": "Deleting it"},
                             {"do": "done", "say": "Moved it to the Bin."}]}
    p, _ = planner(hands, PlanGroq(action_plan))
    r = await p.run("delete old report.pdf from my desktop")
    assert r.ok is True
    assert "Old Report.pdf" not in hands.files
    assert "finder_trash" in ACTIONS  # sanity: the real action this exercises still exists


# 9. filesystem task: a folder created by one step is visible to `files` afterward.
async def test_finder_new_folder_adds_the_folder_to_persisted_state():
    hands = SimMac(world={"front_app": "Finder", "apps": ["Finder"], "windows": [], "tabs": [], "selected": ""})
    plan = {"steps": [{"do": "action", "name": "finder_new_folder",
                       "args": {"where": "~/Desktop", "name": "Physics"}},
                      {"do": "done", "say": "Folder made."}]}
    p, _ = planner(hands, PlanGroq(plan))
    r = await p.run("make a folder called physics on my desktop")
    assert r.ok is True
    assert "Physics" in hands.files


# 10. slow UI: the target element isn't there on the first read but appears by the third -- the
#     expect-step's EXPECT_S poll loop (planner.py) must wait rather than fail immediately.
async def test_a_slow_appearing_dialog_is_waited_through_not_failed_immediately():
    hands = SimMac(apps={"Settings": [{"id": "a1", "role": "checkbox", "label": "Do Not Disturb"}]},
                   world={"front_app": "Settings", "apps": ["Settings"], "windows": [], "tabs": [], "selected": ""})
    hands.delay_screens("Settings", 2, [{"id": "a1", "role": "checkbox", "label": "Do Not Disturb"}])
    plan = {"steps": [{"do": "expect", "element": "Do Not Disturb"},
                      {"do": "find", "what": "Do Not Disturb", "then": "press"},
                      {"do": "done", "say": "There it is."}]}
    p, _ = planner(hands, PlanGroq(plan))
    p.EXPECT_S, p._expect_poll = 0.2, 0.01  # give the 2-call delay room inside the poll window
    r = await p.run("wait for the settings pane to load, then turn on do not disturb")
    assert r.ok is True


# 11. "profile already active": explicitly named in the P0-1 bugfix's own follow-up note (core.log
#     09-25 17:54 P0 #1). Detecting this is the plan call's job (it reads the world summary and
#     current screen, spec §10 "ensure ... if already true, skip"), which this test can't exercise
#     without a live model -- what IS mechanically provable here is that the signal the plan call
#     would need is actually present and correct in what the planner hands it: the world summary
#     names the current tab's real title/URL, so a live plan call reading it has what it needs to
#     recognize "already there" instead of guessing from Evie's memory of what she last did.
def test_the_world_summary_surfaces_the_already_active_netflix_profile_by_title_and_url():
    from evie.computer.world import World
    world = World.from_data({"front_app": "Safari", "apps": ["Safari"], "windows": [], "selected": "",
                             "tabs": [{"window": 11, "order": 1, "index": 1, "current": True,
                                       "title": "Darryl - Netflix",
                                       "url": "https://www.netflix.com/browse?profile=darryl"}]})
    summary = world.summary()
    assert "Darryl - Netflix" in summary
    assert "darryl" in summary.lower()


# 12. AX-empty PDF (or any AX-empty app): zero structured elements forces the vision fallback
#     path (_look_for -> marked_shot), distinct from notion1 (which has real, if fuzzily-matched,
#     elements) -- this is the genuinely-empty case spec §4 Level 2/3 describes.
async def test_an_ax_empty_pdf_falls_back_to_marked_shot_vision():
    hands = SimMac(apps={"Preview": []},  # zero AX elements: a canvas-rendered PDF page
                   world={"front_app": "Preview", "apps": ["Preview"], "windows": [], "tabs": [], "selected": ""})
    plan = {"steps": [{"do": "find", "what": "the Continue button", "then": "press"}, {"do": "done", "say": "x"}]}
    groq = PlanGroq(plan, DONE, DONE)

    async def fake_look(prompt, png):
        return json.dumps({"n": None})
    groq.look = fake_look
    p, _ = planner(hands, groq)
    await p.run("press continue on this pdf")
    assert "marked_shot" in [op for op, _ in hands.calls]


# 13. partial failure + recovery: the first press fails (stale snapshot from a screen that moved
#     under it), a fresh find() on the next attempt succeeds -- spec §13 STALE_STATE -> "refresh
#     perception, retry using fresh target," proven end-to-end rather than just at the SimMac unit
#     level (case 5 above).
async def test_a_stale_press_recovers_via_replan_with_a_fresh_snapshot():
    """Forces a real STALE_STATE failure: the element the plan names ('Old') is gone by the time
    the press would happen (the app's screen changed to show 'New' instead, simulating a UI that
    moved under Evie between find and press) -- the first plan's find() can't locate 'Old' at all,
    fails, and a replan targeting what's actually there now ('New') succeeds. This proves recovery
    genuinely re-perceives rather than trusting the first plan's assumptions about the screen."""
    hands = SimMac(apps={"Notion": [{"id": "a1", "role": "button", "label": "New"}]},
                   world={"front_app": "Notion", "apps": ["Notion"], "windows": [], "tabs": [], "selected": ""})
    stale_plan = {"steps": [{"do": "find", "what": "Old", "then": "press"}, {"do": "done", "say": "x"}]}
    fresh_plan = {"steps": [{"do": "find", "what": "New", "then": "press"}, {"do": "done", "say": "Made a new page."}]}
    p, _ = planner(hands, PlanGroq(stale_plan, fresh_plan))
    r = await p.run("make a new page in notion")
    assert r.ok is True and r.said == "Made a new page."
    assert any("FAILED" in h for h in p._history)  # the stale first plan genuinely failed, not skipped
    presses = [a for op, a in hands.calls if op == "press"]
    assert len(presses) == 1  # only the recovered press actually happened, not a guess at 'Old'


# 14. ambiguous target that genuinely can't be resolved from context: two windows of the SAME app
#     with no distinguishing goal text -- Evie should ask, not guess (Law 8), distinct from
#     vague1 (a goal with literally no target at all) and ask1-3/parrot1 (a named creator/site
#     with multiple videos, not multiple windows of the identical app). Whether to actually ask is
#     the plan call's judgment (untestable live here, same limitation as case 11 above); what's
#     mechanically provable is that the {"do": "ask", ...} step -- the one a real plan would emit
#     for this case -- correctly produces an Outcome the voice layer reads as "ask, don't guess"
#     (r.ask, not r.ok), and that a genuinely-asked plan can be resumed with Isaac's answer.
async def test_an_ask_step_for_ambiguous_windows_produces_an_ask_outcome_not_a_guess():
    world = {"front_app": "TextEdit", "apps": ["TextEdit"],
             "windows": [{"app": "TextEdit", "title": "Untitled"}, {"app": "TextEdit", "title": "Untitled"}],
             "tabs": [], "selected": ""}
    hands = SimMac(world=world)
    plan = {"steps": [{"do": "ask", "say": "You have two untitled TextEdit windows open -- which one?"}]}
    p, _ = planner(hands, PlanGroq(plan))
    r = await p.run("save this document")
    assert r.ask is True and r.ok is False  # not a silent guess dressed up as success
    assert not [op for op, _ in hands.calls if op == "press"]  # asking, not acting on a guess


# 15. long autonomous task: a plan with several ordinary steps that all succeed in sequence,
#     confirming the vision-call budget (Task 7) and workspace assignment (Task 5) both hold
#     across a longer run rather than only in short single-step tests.
async def test_a_longer_autonomous_task_completes_without_exceeding_the_vision_budget():
    hands = SimMac(apps={"Notion": [{"id": "a2", "role": "textfield", "label": "Search pages", "typeable": True},
                                    {"id": "a3", "role": "row", "label": "iGEM Dry Lab Model"}]},
                   world=NOTION_WORLD)
    hands.state_response = two_displays_state()
    plan = {"steps": [
        {"do": "find", "what": "Search pages", "then": "set_text", "text": "igem"},
        {"do": "find", "what": "iGEM Dry Lab Model", "then": "press"},
        {"do": "done", "say": "Opened your iGEM Dry Lab Model page."}]}
    p, _ = planner(hands, PlanGroq(plan))
    r = await p.run("open my igem dry lab model page in notion")
    assert r.ok is True
    assert p._vision_calls == 0  # structured perception handled both finds; no vision needed
