import threading
import time

import numpy as np

from evie.voice import AppOut, RoutedOut


class FakeLink:
    def __init__(self, connected=True, auto_done=True):
        self.connected, self.auto_done = connected, auto_done
        self.sent = []
        self.done = {}

    def send_json(self, msg):
        self.sent.append(("json", msg))
        if msg["kind"] == "end" and self.auto_done:
            self.finish(msg["id"])
        if msg["kind"] == "stop":
            self.finish(msg["id"])

    def send_bytes(self, data):
        self.sent.append(("bytes", len(data)))

    def waiter(self, lid):
        return self.done.setdefault(lid, threading.Event())

    def finish(self, lid):
        self.waiter(lid).set()


def chunks(n=3, size=2400):
    return [np.zeros(size, dtype=np.float32) for _ in range(n)]


def test_app_out_streams_float32_chunks_and_waits_for_the_app():
    link = FakeLink()
    started = []
    assert AppOut(link, 24000).play(chunks(), threading.Event(), on_start=lambda: started.append(1)) is True
    kinds = [s[1]["kind"] if s[0] == "json" else "bytes" for s in link.sent]
    assert kinds == ["start", "bytes", "bytes", "bytes", "end"]
    assert link.sent[0][1]["rate"] == 24000 and link.sent[1][1] == 2400 * 4 and started == [1]


def test_app_out_cancel_tells_the_app_to_stop():
    link = FakeLink(auto_done=False)
    cancel = threading.Event()
    cancel.set()
    assert AppOut(link, 24000).play(chunks(), cancel) is False
    assert link.sent[-1][1]["kind"] == "stop"


def test_app_out_never_hangs_if_the_app_goes_quiet():
    link = FakeLink(auto_done=False)
    t0 = time.monotonic()
    AppOut(link, 24000, grace_s=0.1).play(chunks(1, 2400), threading.Event())  # 0.1 s of audio
    assert time.monotonic() - t0 < 1.0


def test_routed_out_uses_the_app_when_it_is_listening_else_the_speakers():
    class Out:
        def __init__(self):
            self.n = 0

        def play(self, chunks, cancel, on_start=None):
            self.n += 1
            return True

    link, app, spk = FakeLink(connected=False), Out(), Out()
    r = RoutedOut(link, app, spk)
    r.play([], threading.Event())
    link.connected = True
    r.play([], threading.Event())
    assert (app.n, spk.n) == (1, 1)


def test_mouth_socket_carries_lines_to_the_app_and_done_back():
    from fastapi.testclient import TestClient

    from evie.mouth_link import MouthLink
    from evie.server import create_app
    from tests.test_server import full_deps

    d = full_deps()
    d.mouth_link = MouthLink()
    with TestClient(create_app(lambda: d, probe=False)) as c:
        assert d.mouth_link.connected is False
        with c.websocket_connect("/ws/mouth") as ws:
            ws.send_json({"kind": "hello", "echo_cancel": True})
            for _ in range(50):
                if d.mouth_link.connected:
                    break
                time.sleep(0.01)
            assert d.mouth_link.connected
            done = d.mouth_link.waiter("abc")
            d.mouth_link.send_json({"kind": "start", "id": "abc", "rate": 24000})
            d.mouth_link.send_bytes(b"\x00" * 16)
            assert ws.receive_json()["kind"] == "start"
            assert ws.receive_bytes() == b"\x00" * 16
            ws.send_json({"kind": "done", "id": "abc"})
            assert done.wait(1.0)
        for _ in range(50):
            if not d.mouth_link.connected:
                break
            time.sleep(0.01)
        assert d.mouth_link.connected is False
