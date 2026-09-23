import asyncio
import json

import httpx
import pytest
import respx

from evie.hands import HandsResult
from evie.skills.catalog import RISK, Skills, allowed
from evie.skills.music import SpotifySearch
from evie.skills.system import System
from evie.skills.timers import Timers
from evie.switchboard.questions import SKILLS


class FakeHands:
    def __init__(self, results=None):
        self.calls, self.results = [], results or {}

    async def do(self, op, timeout=5.0, **args):
        self.calls.append((op, args))
        r = self.results.get(op, HandsResult(True, "playing", {"state": "playing", "uri": args.get("uri", "spotify:track:x"),
                                                                "name": "Espresso", "artist": "Sabrina Carpenter"}))
        return r


class FakeTalker:
    def __init__(self, out=None):
        self.out, self.calls = out or {}, []

    async def extract(self, instructions, text):
        self.calls.append(text)
        return self.out


class FakeSystem:
    def __init__(self, volume=40, running=True):
        self.volume, self.muted_, self.running = volume, False, running
        self.opened, self.urls = [], []

    async def get_volume(self):
        return self.volume

    async def set_volume(self, n):
        self.volume = n
        return True

    async def set_muted(self, on):
        self.muted_ = on
        return True

    async def open_app(self, name):
        self.opened.append(name)
        return True

    async def app_running(self, name):
        return self.running

    async def open_url(self, url):
        self.urls.append(url)
        return True


class FakeSpotify:
    def __init__(self, found=("spotify:track:2qSkIjg1o9h3YT9RAgYN75", "Espresso by Sabrina Carpenter")):
        self.found, self.queries = found, []

    async def find(self, query, kind="track"):
        self.queries.append((query, kind))
        return self.found


class FakeJev:
    def __init__(self, choice=None):
        self.choice, self.questions = choice, []

    async def ask(self, state, questions):
        from evie.jev import JevResult
        self.questions.append(questions)
        return JevResult({"app": {"type": "choice", "choice": self.choice, "confidence": 0.9}}, 300.0, 0.0)


def skills(tmp_path, hands=None, talker=None, system=None, spotify=None, jev=None, apps=("Notion", "Safari")):
    fired = []
    timers = Timers(lambda t: fired.append(t), path=tmp_path / "timers.json")
    s = Skills(hands or FakeHands(), talker or FakeTalker(), jev or FakeJev(), system or FakeSystem(),
               spotify or FakeSpotify(), timers, apps=lambda: list(apps), log=tmp_path / "actions.jsonl")
    return s, fired


def test_every_skill_has_a_risk_and_only_deletes_are_risky():
    assert set(RISK) == set(SKILLS)
    assert all(RISK[k] in ("read_only", "reversible") for k in SKILLS if k not in ("other", "event_delete"))
    assert RISK["event_delete"] == "deletes"


def test_risky_actions_need_isaacs_own_voice():
    assert allowed("reversible", "unknown", addressed=False)
    assert not allowed("sends_as_isaac", "unknown", addressed=False)
    assert allowed("sends_as_isaac", "isaac", addressed=False)
    assert allowed("deletes", "unknown", addressed=True)


async def test_play_a_song_searches_spotify_and_verifies(tmp_path):
    hands, sp = FakeHands(), FakeSpotify()
    s, _ = skills(tmp_path, hands=hands, spotify=sp, talker=FakeTalker({"query": "Espresso", "kind": "track"}))
    done = await s.run("music_play", "play espresso")
    assert sp.queries == [("Espresso", "track")]
    assert hands.calls == [("spotify_play", {"uri": "spotify:track:2qSkIjg1o9h3YT9RAgYN75"})]
    assert done.said == "Playing Espresso by Sabrina Carpenter." and done.verified is True


async def test_play_with_nothing_specific_just_resumes(tmp_path):
    hands = FakeHands()
    s, _ = skills(tmp_path, hands=hands, talker=FakeTalker({"query": ""}))
    await s.run("music_play", "play some music")
    assert hands.calls[0][0] == "spotify_resume"


async def test_song_not_found_says_so(tmp_path):
    s, _ = skills(tmp_path, spotify=FakeSpotify(found=None), talker=FakeTalker({"query": "zzqx"}))
    done = await s.run("music_play", "play zzqx")
    assert not done.ok and "Spotify" in done.said


async def test_hands_failure_is_a_short_plain_line(tmp_path):
    hands = FakeHands({"spotify_pause": HandsResult(False, "Spotify isn't open")})
    s, _ = skills(tmp_path, hands=hands)
    done = await s.run("music_pause", "pause")
    assert not done.ok and done.said == "Couldn't do that: Spotify isn't open."


async def test_next_song_names_it(tmp_path):
    s, _ = skills(tmp_path)
    assert (await s.run("music_next", "skip")).said == "Next up, Espresso by Sabrina Carpenter."


async def test_volume_set_verify_and_undo(tmp_path):
    sysm = FakeSystem(volume=40)
    s, _ = skills(tmp_path, system=sysm)
    done = await s.run("volume", "volume 30")
    assert sysm.volume == 30 and done.said == "Volume 30." and done.verified
    undo = await s.run("undo", "undo that")
    assert sysm.volume == 40 and undo.said == "Volume's back to 40."
    assert (await s.run("undo", "undo")).said == "Nothing to undo."


async def test_volume_up_is_relative(tmp_path):
    sysm = FakeSystem(volume=95)
    s, _ = skills(tmp_path, system=sysm)
    await s.run("volume", "louder")
    assert sysm.volume == 100


async def test_volume_without_a_number_asks(tmp_path):
    s, _ = skills(tmp_path)
    assert not (await s.run("volume", "what's the volume like")).ok


async def test_mute(tmp_path):
    sysm = FakeSystem()
    s, _ = skills(tmp_path, system=sysm)
    assert (await s.run("volume", "mute")).said == "Muted." and sysm.muted_


async def test_open_app_by_name_needs_no_model(tmp_path):
    sysm, jev = FakeSystem(), FakeJev()
    s, _ = skills(tmp_path, system=sysm, jev=jev)
    done = await s.run("open_app", "evie open notion")
    assert sysm.opened == ["Notion"] and done.said == "Opening Notion." and jev.questions == []


async def test_open_app_not_named_outright_asks_jev_to_pick_from_real_apps(tmp_path):
    sysm, jev = FakeSystem(), FakeJev(choice="Notion")
    s, _ = skills(tmp_path, system=sysm, jev=jev)
    await s.run("open_app", "open my notes app")
    assert sysm.opened == ["Notion"] and set(jev.questions[0]["app"]["criteria"]) == {"Notion", "Safari", "none"}


async def test_open_app_that_isnt_installed_says_so(tmp_path):
    sysm = FakeSystem()
    s, _ = skills(tmp_path, system=sysm, jev=FakeJev(choice="none"))
    done = await s.run("open_app", "open photoshop")
    assert sysm.opened == [] and not done.ok


async def test_open_website_only_opens_real_web_addresses(tmp_path):
    sysm = FakeSystem()
    s, _ = skills(tmp_path, system=sysm, talker=FakeTalker({"url": "youtube.com"}))
    assert (await s.run("open_website", "open youtube")).said == "Opening youtube.com."
    s2, _ = skills(tmp_path, system=sysm, talker=FakeTalker({"url": "file:///etc/passwd"}))
    assert not (await s2.run("open_website", "open my files")).ok
    assert sysm.urls == ["https://youtube.com"]


async def test_timer_set_fires_and_cancel(tmp_path):
    s, fired = skills(tmp_path)
    done = await s.run("timer_set", "set a timer for 10 minutes")
    assert done.said == "10 minute timer, starting now."
    assert (await s.run("timer_cancel", "cancel the timer")).said == "Timer cancelled."
    assert (await s.run("timer_cancel", "cancel the timer")).said == "No timer running."


async def test_other_falls_through_to_claude_code(tmp_path):
    s, _ = skills(tmp_path)
    assert (await s.run("other", "rename my screenshots")).said is None


async def test_every_action_is_logged(tmp_path):
    s, _ = skills(tmp_path)
    await s.run("volume", "volume 20")
    row = json.loads((tmp_path / "actions.jsonl").read_text().splitlines()[0])
    assert row["skill"] == "volume" and row["ok"] is True and row["risk"] == "reversible" and row["verified"]


# -- timers ------------------------------------------------------------------------------
async def test_timer_fires_on_time(tmp_path):
    fired = []
    t = Timers(fired.append, path=tmp_path / "t.json")
    t.start(0.02)
    await asyncio.sleep(0.06)
    assert len(fired) == 1 and t.active() == []


async def test_timers_survive_a_restart(tmp_path):
    t = Timers(lambda x: None, path=tmp_path / "t.json")
    t.start(0.05)
    t.close()
    fired = []
    again = Timers(fired.append, path=tmp_path / "t.json")
    again.restore()
    await asyncio.sleep(0.1)
    assert len(fired) == 1


async def test_timer_that_ended_while_down_fires_at_once(tmp_path):
    path = tmp_path / "t.json"
    path.write_text(json.dumps([{"id": "a", "seconds": 60, "ends_at": 1.0, "label": ""}]))
    fired = []
    Timers(fired.append, path=path).restore()
    await asyncio.sleep(0.01)
    assert len(fired) == 1


# -- system ------------------------------------------------------------------------------
async def test_system_volume_uses_osascript():
    calls = []

    async def run(argv, timeout=5.0):
        calls.append(argv)
        return 0, "37\n"

    sysm = System(run=run)
    assert await sysm.get_volume() == 37
    await sysm.set_volume(20)
    assert calls[1] == ["/usr/bin/osascript", "-e", "set volume output volume 20"]


async def test_system_quotes_app_names_safely():
    calls = []

    async def run(argv, timeout=5.0):
        calls.append(argv)
        return 0, "true"

    await System(run=run).app_running('Evil" & do shell script "rm')
    assert '\\"' in calls[0][2]


# -- spotify search ----------------------------------------------------------------------
@respx.mock
async def test_spotify_search_uses_client_credentials_and_caches_the_token():
    tok = respx.post("https://accounts.spotify.com/api/token").mock(
        return_value=httpx.Response(200, json={"access_token": "T", "expires_in": 3600}))
    search = respx.get("https://api.spotify.com/v1/search").mock(return_value=httpx.Response(200, json={
        "tracks": {"items": [{"uri": "spotify:track:abc1234567", "name": "Espresso",
                              "artists": [{"name": "Sabrina Carpenter"}]}]}}))
    sp = SpotifySearch("id", "secret")
    assert await sp.find("espresso") == ("spotify:track:abc1234567", "Espresso by Sabrina Carpenter")
    await sp.find("espresso")
    assert tok.call_count == 1 and search.call_count == 2
    assert search.calls[0].request.headers["Authorization"] == "Bearer T"


@respx.mock
async def test_spotify_playlist_search_skips_empty_items():
    respx.post("https://accounts.spotify.com/api/token").mock(
        return_value=httpx.Response(200, json={"access_token": "T", "expires_in": 3600}))
    respx.get("https://api.spotify.com/v1/search").mock(return_value=httpx.Response(200, json={
        "playlists": {"items": [None, {"uri": "spotify:playlist:xyz1234567", "name": "lofi beats"}]}}))
    assert await SpotifySearch("id", "s").find("lofi", "playlist") == ("spotify:playlist:xyz1234567", "lofi beats")


@respx.mock
async def test_spotify_down_gives_none():
    respx.post("https://accounts.spotify.com/api/token").mock(return_value=httpx.Response(500))
    assert await SpotifySearch("id", "s").find("x") is None


async def test_spotify_without_keys_gives_none():
    assert await SpotifySearch("", "").find("x") is None


@pytest.mark.live
async def test_live_spotify_search_finds_a_track():
    from evie.config import load_settings
    s = load_settings()
    found = await SpotifySearch(s.spotify_id, s.spotify_secret).find("Espresso Sabrina Carpenter")
    assert found and found[0].startswith("spotify:track:")
