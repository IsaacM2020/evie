from fastapi.testclient import TestClient

from evie.calendar_store import CalendarStore
from evie.events import EventBus
from evie.jev import JevError, JevResult
from evie.jobs import Job
from evie.server import Deps, create_app
from evie.switchboard import Switchboard
from tests.helpers import make_answers


class FakeJev:
    def __init__(self, error=None):
        self.error = error

    async def ask(self, state, questions):
        if self.error:
            raise self.error
        return JevResult(make_answers(), 200.0, 0.00002)

    async def aclose(self):
        pass


class FakeBrain:
    def __init__(self, bus):
        self.bus, self.heard = bus, []

    async def hear(self, text, speaker="isaac"):
        self.heard.append((text, speaker))
        self.bus.publish("heard", text=text)
        return {"text": text, "action": "act", "reason": "answer", "route": "answer", "said": "Hi."}


class FakeMouth:
    def __init__(self):
        self.stops = 0

    def stop(self):
        self.stops += 1


class FakeSTT:
    def __init__(self, text="evie what time is it"):
        self.text, self.got = text, []

    async def transcribe(self, audio):
        self.got.append(audio)
        return self.text


class FakeRunner:
    def __init__(self, running=None):
        self.job = Job(goal=running) if running else None
        self.shutdowns = self.stops = 0

    @property
    def current(self):
        return self.job

    async def stop(self):
        self.stops += 1
        self.job = None

    async def shutdown(self):
        self.shutdowns += 1


def full_deps(error=None, stt_text="evie what time is it", running=None):
    bus = EventBus()
    return Deps(sb=Switchboard(FakeJev(error)), calendar=CalendarStore(), bus=bus, brain=FakeBrain(bus),
                mouth=FakeMouth(), stt=FakeSTT(stt_text), runner=FakeRunner(running))


def client(error=None, probe=False, deps=None):
    d = deps or Deps(sb=Switchboard(FakeJev(error)), calendar=CalendarStore(), bus=EventBus())
    return TestClient(create_app(lambda: d, probe=probe))


def test_status_before_any_decision():
    with client() as c:
        body = c.get("/status").json()
    assert body == {"ok": True, "version": "0.1.0", "jev_ok": None, "stt_ready": False,
                    "voice_ready": False, "calendar_fresh": False, "job": None}


def test_decide_returns_outcome_and_marks_jev_ok():
    with client() as c:
        out = c.post("/decide", json={"utterance": "evie pause the music"}).json()
        status = c.get("/status").json()
    assert out["action"] == "act" and out["decision"]["route"] == "quick_action"
    assert status["jev_ok"] is True


def test_decide_marks_jev_down():
    with client(error=JevError("http 403: Key limit exceeded")) as c:
        out = c.post("/decide", json={"utterance": "evie pause the music"}).json()
        status = c.get("/status").json()
    assert out["action"] == "ignore" and out["reason"].startswith("jev unavailable")
    assert status["jev_ok"] is False


def test_probe_sets_jev_ok_on_startup():
    with client(probe=True) as c:
        assert c.get("/status").json()["jev_ok"] is True


def test_bad_speaker_rejected():
    with client() as c:
        r = c.post("/decide", json={"utterance": "hi", "speaker": "robot"})
    assert r.status_code == 422


def test_voice_endpoints_503_without_brain():
    with client() as c:
        assert c.post("/hear", json={"text": "hi"}).status_code == 503
        assert c.post("/voice", content=b"RIFF").status_code == 503


def test_hear_goes_through_brain():
    d = full_deps()
    with client(deps=d) as c:
        out = c.post("/hear", json={"text": "evie what time is it", "speaker": "other"}).json()
    assert out["said"] == "Hi." and d.brain.heard == [("evie what time is it", "other")]


def test_voice_transcribes_then_hears():
    d = full_deps(stt_text="evie what time is it")
    with client(deps=d) as c:
        out = c.post("/voice", content=b"RIFFfake", headers={"Content-Type": "audio/wav"}).json()
    assert d.stt.got == [b"RIFFfake"] and d.brain.heard == [("evie what time is it", "isaac")]
    assert out["text"] == "evie what time is it"


def test_voice_with_nothing_heard_skips_brain():
    d = full_deps(stt_text="")
    with client(deps=d) as c:
        out = c.post("/voice", content=b"RIFF").json()
    assert out["reason"] == "heard nothing" and d.brain.heard == []


def test_voice_start_cuts_evie_off():
    d = full_deps()
    with client(deps=d) as c:
        c.post("/voice/start")
    assert d.mouth.stops == 1


def test_websocket_streams_events():
    d = full_deps()
    with client(deps=d) as c:
        with c.websocket_connect("/ws") as ws:
            assert ws.receive_json()["kind"] == "hello"
            c.post("/hear", json={"text": "yo"})
            ev = ws.receive_json()
    assert ev["kind"] == "heard" and ev["text"] == "yo"


def test_job_endpoints():
    d = full_deps(running="fix the chase bug")
    with client(deps=d) as c:
        assert c.get("/job").json()["goal"] == "fix the chase bug"
        assert c.get("/status").json()["job"]["goal"] == "fix the chase bug"
        assert c.post("/job/stop").json() == {"stopped": True}
        assert c.get("/job").json() is None
        assert c.post("/job/stop").json() == {"stopped": False}


def test_shutdown_stops_the_job_runner():
    d = full_deps(running="x")
    with client(deps=d):
        pass
    assert d.runner.shutdowns == 1
