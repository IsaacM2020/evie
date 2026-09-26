"""P3: the plain-code bridge -- Claude Code's heavyweight escalation drives the SAME hands Evie's
own fast planner uses (spec §15/§17/§25: "SAME HANDS, SAME STATE, SAME SAFETY"), through an
in-process SDK MCP server (claude_agent_sdk.create_sdk_mcp_server), instead of reinventing
osascript/Shortcuts from a free-text instruction. Gated behind P2 (safety.classify, the credential
ban, is_risky) being real and wired -- confirmed by the four prior commits on this branch.

Every tool call funnels through the exact same checks _act already applies: a credential field is
refused outright (never a tool call reaches hands.do for it), and a risky one gets the same 3s
"say stop" countdown before it runs. There is no second safety system -- build_evie_hands_server
imports and calls the real is_risky/_is_credential_field/classify from planner.py/safety.py.
"""
import json

import pytest

from evals.sim import SimHands
from evie.computer.bridge import build_evie_hands_tools
from evie.countdown import Countdown
from evie.hands import HandsResult


def _server(hands, countdown=None, say=None):
    return build_evie_hands_tools(hands, countdown or Countdown(seconds=0.02), say or (lambda _t: None))


def _tool(server, name):
    return next(t for t in server if t.name == name)


async def test_observe_returns_the_current_screen_as_json():
    hands = SimHands(apps={"Notion": [{"id": "a1", "role": "button", "label": "New"}]},
                     world={"front_app": "Notion", "apps": ["Notion"], "windows": [], "tabs": [], "selected": ""})
    server = _server(hands)
    out = await _tool(server, "evie_observe").handler({"app": "Notion"})
    payload = json.loads(out["content"][0]["text"])
    assert payload["app"] == "Notion"
    assert any(e["label"] == "New" for e in payload["elements"])


async def test_press_a_real_element_works():
    hands = SimHands(apps={"Notion": [{"id": "a1", "role": "button", "label": "New"}]},
                     world={"front_app": "Notion", "apps": ["Notion"], "windows": [], "tabs": [], "selected": ""})
    server = _server(hands)
    await _tool(server, "evie_observe").handler({"app": "Notion"})  # snapshot first, like _act expects
    out = await _tool(server, "evie_press").handler({"app": "Notion", "id": "a1", "label": "New"})
    assert not out.get("is_error")
    assert ("press", {"id": "a1", "snapshot": "s1"}) in hands.calls


async def test_press_refuses_a_credential_field_without_ever_calling_hands():
    hands = SimHands(apps={"Login": [{"id": "e1", "role": "textfield", "label": "Password"}]},
                     world={"front_app": "Login", "apps": ["Login"], "windows": [], "tabs": [], "selected": ""})
    server = _server(hands)
    await _tool(server, "evie_observe").handler({"app": "Login"})
    out = await _tool(server, "evie_set_text").handler({"app": "Login", "id": "e1", "label": "Password",
                                                        "role": "textfield", "text": "hunter2"})
    assert out.get("is_error")
    assert not any(op == "set_text" for op, _ in hands.calls)


async def test_press_a_risky_element_waits_for_the_countdown():
    hands = SimHands(apps={"Mail": [{"id": "s1", "role": "button", "label": "Send"}]},
                     world={"front_app": "Mail", "apps": ["Mail"], "windows": [], "tabs": [], "selected": ""})
    said = []
    server = _server(hands, say=said.append)
    await _tool(server, "evie_observe").handler({"app": "Mail"})
    out = await _tool(server, "evie_press").handler({"app": "Mail", "id": "s1", "label": "Send"})
    assert not out.get("is_error")
    assert any("stop" in s.lower() for s in said)
    assert ("press", {"id": "s1", "snapshot": "s1"}) in hands.calls


async def test_press_a_risky_element_can_be_stopped():
    """Isaac's own voice or the talk key cancels the SAME Countdown object -- this proves the tool
    honours cd.cancel() exactly like Planner._act does, not a countdown of its own it controls."""
    import asyncio
    hands = SimHands(apps={"Mail": [{"id": "s1", "role": "button", "label": "Send"}]},
                     world={"front_app": "Mail", "apps": ["Mail"], "windows": [], "tabs": [], "selected": ""})
    cd = Countdown(seconds=1.0)  # long enough to reliably cancel before it fires
    server = _server(hands, countdown=cd)
    await _tool(server, "evie_observe").handler({"app": "Mail"})
    task = asyncio.ensure_future(_tool(server, "evie_press").handler({"app": "Mail", "id": "s1", "label": "Send"}))
    await asyncio.sleep(0.05)
    assert cd.cancel()
    out = await task
    assert out.get("is_error")
    assert not any(op == "press" for op, _ in hands.calls)


async def test_press_an_id_not_on_the_last_observed_snapshot_is_refused():
    """A stale id from an old observation must never reach hands.do -- mirrors _act's own
    snapshot-based staleness guard (spec Law 4/§3: 'A button discovered in snapshot S41 cannot be
    blindly clicked after the interface has changed to S42')."""
    hands = SimHands(apps={"Notion": [{"id": "a1", "role": "button", "label": "New"}]},
                     world={"front_app": "Notion", "apps": ["Notion"], "windows": [], "tabs": [], "selected": ""})
    server = _server(hands)
    out = await _tool(server, "evie_press").handler({"app": "Notion", "id": "zzz", "label": "New"})
    assert out.get("is_error")
    assert not any(op == "press" for op, _ in hands.calls)


async def test_open_url_and_key_pass_straight_through():
    hands = SimHands(pages={"https://example.com/": []}, world={"front_app": "Safari", "apps": ["Safari"],
                                                                 "windows": [], "tabs": [], "selected": ""})
    server = _server(hands)
    out = await _tool(server, "evie_open_url").handler({"url": "https://example.com/"})
    assert not out.get("is_error")
    out2 = await _tool(server, "evie_key").handler({"app": "Safari", "combo": "cmd+t"})
    assert not out2.get("is_error")
    assert [op for op, _ in hands.calls] == ["open_url", "key"]
