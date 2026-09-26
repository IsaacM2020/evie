"""Task 8 (Phase 2 follow-up plan): a stateful SimMac for eval cases sim.py's SimHands can't
express -- a stale element id rejected the way the real Swift press()/set_text() reject one
(Eyes.swift:367/390: "guard snap == snapshot else return ok:false"), a place_window op SimHands
never implemented, and a dialog that blocks a `press` until dismissed. SimMac extends SimHands
rather than replacing it: every existing SimHands behavior (used by the 36 cases already in
evals/computer/tasks.py) is unchanged."""
from evals.simmac import SimMac


async def test_pressing_with_the_current_snapshot_id_still_works_like_simhands():
    m = SimMac(apps={"Notion": [{"id": "a1", "role": "button", "label": "New"}]})
    scr = await m.do("observe", app="Notion")
    r = await m.do("press", id="a1", snapshot=scr.data["snapshot"])
    assert r.ok


async def test_pressing_with_a_stale_snapshot_id_is_rejected_like_the_real_swift_press():
    """Eyes.swift:367 -- 'guard snap == snapshot else return the screen changed, look again'.
    SimHands today ignores the snapshot argument entirely; SimMac must actually check it."""
    m = SimMac(apps={"Notion": [{"id": "a1", "role": "button", "label": "New"}]})
    scr = await m.do("observe", app="Notion")
    stale = scr.data["snapshot"] + "-old"
    r = await m.do("press", id="a1", snapshot=stale)
    assert r.ok is False and "changed" in r.detail.lower()


async def test_set_text_with_a_stale_snapshot_id_is_also_rejected():
    m = SimMac(apps={"Notion": [{"id": "a1", "role": "textfield", "label": "Search", "typeable": True}]})
    scr = await m.do("observe", app="Notion")
    r = await m.do("set_text", id="a1", text="hi", snapshot=scr.data["snapshot"] + "-old")
    assert r.ok is False and "changed" in r.detail.lower()


async def test_place_window_moves_the_named_app_to_the_named_display():
    m = SimMac(apps={"Notion": []})
    r = await m.do("place_window", app="Notion", display_id="display-1")
    assert r.ok is True
    assert ("place_window", {"app": "Notion", "display_id": "display-1"}) in m.calls


async def test_a_dialog_blocks_a_press_until_dismissed():
    """spec §27 'unexpected dialog': a native alert sits in front of the app until Evie deals
    with it -- pressing something in the app underneath should fail, not silently succeed,
    until the dialog is dismissed (SimMac.dismiss_dialog)."""
    m = SimMac(apps={"Finder": [{"id": "a1", "role": "button", "label": "Empty Trash"}]})
    m.show_dialog("Are you sure you want to permanently erase the items in the Trash?")
    scr = await m.do("observe", app="Finder")
    r = await m.do("press", id="a1", snapshot=scr.data["snapshot"])
    assert r.ok is False and "dialog" in r.detail.lower()
    m.dismiss_dialog()
    scr2 = await m.do("observe", app="Finder")
    r2 = await m.do("press", id="a1", snapshot=scr2.data["snapshot"])
    assert r2.ok is True


async def test_filesystem_state_persists_across_calls_within_one_task():
    """spec §27 'filesystem task': finder_new_folder/finder_trash etc run as applescript in the
    real system -- SimMac tracks a plain in-memory file list so a later finder_find in the SAME
    task can see what an earlier step created, which SimHands's stateless script_out can't do."""
    m = SimMac()
    assert "Old Report.pdf" not in m.files
    m.files.add("Old Report.pdf")
    r = await m.do("applescript", source='tell application "Finder" to delete (POSIX file ("Old Report.pdf"))')
    assert r.ok is True
    assert "Old Report.pdf" not in m.files
