import asyncio

from evie.events import EventBus
from evie.hands import Hands, HandsResult


async def test_command_goes_out_and_result_comes_back_by_id():
    bus = EventBus()
    q = bus.subscribe()  # the app is connected
    hands = Hands(bus)
    task = asyncio.create_task(hands.do("spotify_pause"))
    await asyncio.sleep(0)
    ev = q.get_nowait()
    assert ev["kind"] == "do" and ev["op"] == "spotify_pause" and ev["args"] == {} and ev["expires"] > ev["t"]
    assert hands.result(ev["id"], True, "paused", {"state": "paused"}) is True
    assert await task == HandsResult(True, "paused", {"state": "paused"})


async def test_args_travel_with_the_command():
    bus = EventBus()
    q = bus.subscribe()
    hands = Hands(bus)
    task = asyncio.create_task(hands.do("spotify_play", uri="spotify:track:abc"))
    await asyncio.sleep(0)
    ev = q.get_nowait()
    assert ev["args"] == {"uri": "spotify:track:abc"}
    hands.result(ev["id"], True)
    assert (await task).ok


async def test_no_app_connected_fails_fast():
    hands = Hands(EventBus())
    r = await asyncio.wait_for(hands.do("spotify_pause", timeout=5), 0.5)
    assert r.ok is False and "hands" in r.detail


async def test_timeout_gives_a_plain_failure():
    bus = EventBus()
    bus.subscribe()
    r = await Hands(bus).do("spotify_pause", timeout=0.05)
    assert r.ok is False and "hands" in r.detail


async def test_late_or_unknown_result_is_ignored():
    bus = EventBus()
    q = bus.subscribe()
    hands = Hands(bus)
    r = await hands.do("spotify_pause", timeout=0.05)
    ev = q.get_nowait()
    assert r.ok is False
    assert hands.result(ev["id"], True) is False
    assert hands.result("nope", True) is False


async def test_two_commands_in_flight_dont_mix():
    bus = EventBus()
    q = bus.subscribe()
    hands = Hands(bus)
    a = asyncio.create_task(hands.do("spotify_state"))
    b = asyncio.create_task(hands.do("calendar_add", title="x"))
    await asyncio.sleep(0)
    ea, eb = q.get_nowait(), q.get_nowait()
    hands.result(eb["id"], True, data={"id": "E1"})
    hands.result(ea["id"], False, "Spotify isn't open")
    assert (await a).detail == "Spotify isn't open" and (await b).data == {"id": "E1"}
