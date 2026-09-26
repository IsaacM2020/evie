"""P3: the plain-code bridge (spec §15/§17/§25). Claude Code's heavyweight escalation should drive
the SAME hands, SAME state, SAME safety as Evie's own fast planner -- not reinvent osascript from a
free-text instruction, which is what brain.py's stuck-task handoff does today. This module exposes
a small, safe subset of Hands.do() as an in-process SDK MCP server (claude_agent_sdk's
create_sdk_mcp_server), so Claude Code becomes a reasoning backend operating the exact same
primitives, gated by the exact same checks Planner._act already applies (is_risky's countdown,
_is_credential_field's outright refusal, safety.classify's narration).

There is deliberately no second safety system here: build_evie_hands_server imports and calls the
real predicates from safety.py and planner.py rather than re-deriving them. A stale element id (one
not on the last observation this server itself returned) is refused outright, mirroring
Planner._act's own snapshot field -- Claude Code must observe before it presses, exactly like the
fast planner.

Deliberately small: observe, press, set_text, open_url, key, activate. Not the whole action model
(§8) -- read/pick/expect-style reasoning is what Claude Code itself is FOR; this only needs to give
it eyes and hands on the real screen, not reimplement the planner inside an MCP server.
"""
import json

from claude_agent_sdk import McpSdkServerConfig, SdkMcpTool, create_sdk_mcp_server, tool

from evie.computer.safety import classify, is_risky
from evie.countdown import Countdown
from evie.hands import Hands

_CREDENTIAL_WORDS_HINT = "password|passcode|passphrase|pin code|security code|log ?in|sign ?in|username|user ?name"


def _is_credential(el: dict) -> bool:
    import re
    hay = " ".join(str(el.get(k, "")) for k in ("label", "role", "meta"))
    return bool(re.search(rf"\b({_CREDENTIAL_WORDS_HINT})\b", hay, re.I))


def _err(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "is_error": True}


def _ok(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def build_evie_hands_tools(hands: Hands, countdown: Countdown, say) -> list[SdkMcpTool]:
    """say: Callable[[str], None] -- the same read-back channel _act uses for a risky action's
    "say stop" line. countdown: a Countdown Isaac's own voice or the talk key can interrupt,
    exactly the one the live planner uses (never a fresh one Claude Code controls itself).

    Returns the raw SdkMcpTool list (each with a plain, directly-callable .handler) rather than an
    already-wrapped server, so tests can call a tool's handler directly without reaching into
    create_sdk_mcp_server's opaque McpSdkServerConfig. build_evie_hands_server() below wraps this
    for real use."""
    last_snapshot: dict[str, str | None] = {"id": None}
    last_elements: dict[str, dict] = {}

    def _remember(data: dict) -> None:
        last_snapshot["id"] = data.get("snapshot")
        try:
            els = json.loads(data.get("elements") or "[]")
        except (TypeError, ValueError):
            els = []
        last_elements.clear()
        last_elements.update({e["id"]: e for e in els if isinstance(e, dict) and e.get("id")})

    @tool("evie_observe", "Read what's on screen right now in the given app (or Safari's page if app is "
          "'Safari'). Always call this before pressing or typing anything -- an id is only valid for the "
          "observation it came from.", {"app": str})
    async def evie_observe(args: dict) -> dict:
        r = await hands.do("observe", app=args["app"])
        if not r.ok:
            return _err(f"couldn't observe {args['app']}: {r.detail}")
        _remember(r.data)
        try:
            elements = json.loads(r.data.get("elements") or "[]")
        except (TypeError, ValueError):
            elements = []
        payload = {"app": r.data.get("app", args["app"]), "url": r.data.get("url", ""), "elements": elements}
        return _ok(json.dumps(payload))

    def _resolve(app: str, eid: str, label: str, role: str = "") -> dict | str:
        """The real, currently-observed element for this id, or an error string. Refuses anything
        not from the last evie_observe call -- Claude Code must look before it presses, exactly
        like the fast planner (Planner._act's own snapshot field)."""
        el = last_elements.get(eid)
        if el is None:
            return (f"{eid!r} isn't in the last observation of {app!r} -- call evie_observe again "
                    "before pressing (the screen may have changed).")
        if label and (el.get("label") or "") != label:
            return f"{eid!r}'s label is {el.get('label')!r}, not {label!r} -- observe again, ids can be reused."
        return el

    async def _act(op: str, app: str, el: dict, text: str = "", risky_hint: bool = False) -> dict:
        if op == "set_text" and _is_credential(el):
            return _err("I don't type into password or login fields. Isaac needs to do that part himself.")
        effect = classify(op, el, text, flagged=risky_hint)
        if is_risky(op, el, text, flagged=risky_hint):
            say(f"About to {'type into' if op == 'set_text' else 'press'} {el.get('label', '')} in {app}. "
                f"Say stop to cancel.")
            if not await countdown.wait():
                return _err("Isaac said stop -- didn't do it.")
        do_args = {"id": el["id"], "snapshot": last_snapshot["id"]}
        if op == "set_text":
            do_args |= {"text": text, "submit": False}
        r = await hands.do(op, **do_args)
        if not r.ok:
            return _err(f"{op} {el.get('label')!r} failed: {r.detail}")
        return _ok(f"{op} {el.get('label')!r} in {app} -- ok [{effect.value}]")

    @tool("evie_press", "Press/click a real element id from the last evie_observe call.",
          {"app": str, "id": str, "label": str})
    async def evie_press(args: dict) -> dict:
        el = _resolve(args["app"], args["id"], args.get("label", ""))
        if isinstance(el, str):
            return _err(el)
        return await _act("press", args["app"], el)

    @tool("evie_set_text", "Type into a real element id from the last evie_observe call. Never works on "
          "password/login fields -- Isaac types those himself.",
          {"app": str, "id": str, "label": str, "role": str, "text": str})
    async def evie_set_text(args: dict) -> dict:
        el = _resolve(args["app"], args["id"], args.get("label", ""), args.get("role", ""))
        if isinstance(el, str):
            return _err(el)
        return await _act("set_text", args["app"], el, text=args.get("text", ""))

    @tool("evie_open_url", "Open a URL in Safari (a new tab).", {"url": str})
    async def evie_open_url(args: dict) -> dict:
        r = await hands.do("open_url", url=args["url"], app="Safari", new_tab=True, window="", front=True)
        if not r.ok:
            return _err(f"couldn't open {args['url']}: {r.detail}")
        return _ok(f"opened {args['url']}")

    @tool("evie_key", "Send a key combo (like 'cmd+t') to an app.", {"app": str, "combo": str})
    async def evie_key(args: dict) -> dict:
        r = await hands.do("key", combo=args["combo"], app=args["app"])
        if not r.ok:
            return _err(f"key {args['combo']} failed: {r.detail}")
        return _ok(f"sent {args['combo']} to {args['app']}")

    @tool("evie_activate", "Bring an app to the front, launching it if needed.", {"app": str})
    async def evie_activate(args: dict) -> dict:
        r = await hands.do("activate", app=args["app"])
        if not r.ok:
            return _err(f"couldn't open {args['app']}: {r.detail}")
        return _ok(f"{args['app']} is frontmost")

    return [evie_observe, evie_press, evie_set_text, evie_open_url, evie_key, evie_activate]


def build_evie_hands_server(hands: Hands, countdown: Countdown, say) -> McpSdkServerConfig:
    """The real, in-process MCP server for a job's ClaudeAgentOptions(mcp_servers=...)."""
    return create_sdk_mcp_server("evie_hands", tools=build_evie_hands_tools(hands, countdown, say))
