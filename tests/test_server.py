from fastapi.testclient import TestClient

from evie.jev import JevError, JevResult
from evie.server import create_app
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


def client(error=None, probe=False):
    return TestClient(create_app(lambda: Switchboard(FakeJev(error)), probe=probe))


def test_status_before_any_decision():
    with client() as c:
        body = c.get("/status").json()
    assert body == {"ok": True, "version": "0.1.0", "jev_ok": None}


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
