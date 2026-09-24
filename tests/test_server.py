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
                    "voice_ready": False, "calendar_fresh": False, "job": None, "app_online": False,
                    "ears_offline": False}


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


class FakeVoicePrint:
    def __init__(self, ready=False):
        self.is_ready, self.clips, self.cleared = ready, 0, 0

    @property
    def ready(self):
        return self.is_ready

    def status(self):
        return {"clips": self.clips, "seconds": self.clips * 2.0, "ready": self.is_ready}

    def clear(self):
        self.cleared += 1
        self.clips = 0


class FakeVoiceId:
    def __init__(self, ready=False):
        self.print = FakeVoicePrint(ready)
        self.channels = []

    def learn(self, audio, channel="raw"):
        self.print.clips += 1
        self.channels.append(channel)
        return True


class FakeOpenMic:
    def __init__(self):
        from evie.open_mic import ModeStore
        self.modes = ModeStore(None)
        self.frames, self.ptt, self.context = [], [], {}

    def feed(self, frame):
        self.frames.append(frame)

    def ptt_start(self):
        self.ptt.append("start")

    def ptt_end(self):
        self.ptt.append("end")

    def close(self):
        pass


def ears_deps(ready=False):
    d = full_deps()
    d.open_mic, d.voiceid = FakeOpenMic(), FakeVoiceId(ready)
    return d


def speech_wav(seconds=2.0):
    import numpy as np

    from evie.ears import pcm_to_wav
    return pcm_to_wav(np.zeros(int(16000 * seconds), dtype=np.float32))


def test_ears_mode_round_trip_and_live_needs_a_voiceprint():
    d = ears_deps(ready=False)
    with client(deps=d) as c:
        assert c.get("/ears").json() == {"mode": "off", "enrolling": False,
                                         "voiceprint": {"clips": 0, "seconds": 0.0, "ready": False}}
        assert c.post("/ears/mode", json={"mode": "shadow"}).json()["mode"] == "shadow"
        assert c.post("/ears/mode", json={"mode": "live"}).status_code == 409
        assert c.post("/ears/mode", json={"mode": "loud"}).status_code == 422
        d.voiceid.print.is_ready = True
        assert c.post("/ears/mode", json={"mode": "live"}).json()["mode"] == "live"
        assert c.get("/status").json()["ears_mode"] == "live"


def test_ears_socket_feeds_512_sample_frames_and_context():
    import numpy as np
    d = ears_deps()
    with client(deps=d) as c:
        with c.websocket_connect("/ws/ears") as ws:
            ws.send_bytes(np.zeros(700, dtype=np.int16).tobytes())
            ws.send_bytes(np.zeros(400, dtype=np.int16).tobytes())
            ws.send_text('{"front_app": "zoom.us", "in_call": true}')
            ws.send_text("{}")  # a round trip so the server has handled everything above
            ws.close()
    assert [len(f) for f in d.open_mic.frames] == [512, 512]
    assert d.open_mic.frames[0].dtype == np.float32
    assert d.open_mic.context == {"front_app": "zoom.us", "in_call": True}


def test_talk_key_pauses_the_open_mic_and_teaches_voice_id():
    d = ears_deps()
    with client(deps=d) as c:
        c.post("/voice/start")
        c.post("/voice", content=speech_wav())
    assert d.open_mic.ptt == ["start", "end"]
    assert d.voiceid.print.clips == 1 and d.brain.heard  # learning never replaces the turn


def test_talk_key_audio_from_the_echo_cancelled_mic_teaches_the_live_print():
    """The app now records the talk key through the open mic's own engine: when that audio went
    through Apple's echo cancel, it's exactly what the open mic hears, so it trains the live print."""
    d = ears_deps()
    with client(deps=d) as c:
        c.post("/voice", content=speech_wav(), headers={"X-Evie-Channel": "live"})
        c.post("/voice", content=speech_wav())
        c.post("/voice", content=speech_wav(), headers={"X-Evie-Channel": "bogus"})
    assert d.voiceid.channels == ["live", "raw", "raw"]


def test_enrolling_trains_voice_id_without_a_turn():
    d = ears_deps()
    with client(deps=d) as c:
        assert c.post("/voiceid/enroll", json={"on": True}).json()["enrolling"] is True
        out = c.post("/voice", content=speech_wav()).json()
        c.post("/voiceid/enroll", json={"on": False})
    assert out["reason"] == "enrolled" and d.brain.heard == [] and d.voiceid.print.clips == 1


def test_voiceid_reset_forgets():
    d = ears_deps(ready=True)
    with client(deps=d) as c:
        c.post("/voiceid/reset")
    assert d.voiceid.print.cleared == 1


def test_hands_result_endpoint_rejects_unknown_and_bad_results():
    from evie.hands import Hands
    d = full_deps()
    d.hands = Hands(d.bus)
    with client(deps=d) as c:
        assert c.post("/hands/result", json={"id": "nobody", "ok": True}).json() == {"accepted": False}
        assert c.post("/hands/result", json={"id": "x", "ok": "maybe"}).status_code == 422


def test_debug_recorder_switch(tmp_path):
    from evie.recorder import SegmentRecorder
    d = ears_deps()
    d.open_mic.recorder = SegmentRecorder(tmp_path)
    with client(deps=d) as c:
        assert c.get("/recorder").json() == {"on": False, "segments": 0}
        assert c.post("/recorder", json={"on": True}).json()["on"] is True
    assert SegmentRecorder(tmp_path).enabled is True


def test_app_online_while_the_app_is_connected():
    with client() as c:
        with c.websocket_connect("/ws") as ws:
            ws.receive_json()  # hello
            assert c.get("/status").json()["app_online"] is True
        assert c.get("/status").json()["app_online"] is False


def test_debug_do_only_runs_read_only_ops():
    from evie.hands import Hands
    d = full_deps()
    d.hands = Hands(d.bus)
    with client(deps=d) as c:
        r = c.post("/debug/do", json={"op": "imessage_send", "args": {"to": "+6591234567", "text": "hi"}})
        assert r.status_code == 403
        r = c.post("/debug/do", json={"op": "press", "args": {"id": "w1", "snapshot": "x"}})
        assert r.status_code == 403
