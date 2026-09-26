from evie.health import HealthMonitor


def test_a_single_failure_is_not_degraded_yet():
    h = HealthMonitor(degraded_after=3)
    c, event = h.record("jev", ok=False, detail="timeout")
    assert c.consecutive_failures == 1 and not h.degraded("jev") and event is None


def test_repeated_failures_cross_into_degraded_exactly_once():
    h = HealthMonitor(degraded_after=3)
    h.record("jev", ok=False)
    h.record("jev", ok=False)
    c, event = h.record("jev", ok=False)
    assert h.degraded("jev") and event == "degraded"
    c2, event2 = h.record("jev", ok=False)  # a fourth failure: already degraded, no new event
    assert event2 is None and c2.consecutive_failures == 4


def test_a_success_after_degraded_reports_recovered_and_resets_the_streak():
    h = HealthMonitor(degraded_after=2)
    h.record("groq", ok=False)
    h.record("groq", ok=False)
    assert h.degraded("groq")
    c, event = h.record("groq", ok=True)
    assert event == "recovered" and c.consecutive_failures == 0 and not h.degraded("groq")


def test_overall_ok_reflects_any_degraded_component():
    h = HealthMonitor(degraded_after=1)
    assert h.overall_ok()
    h.record("stt", ok=False)
    assert not h.overall_ok()
    h.record("stt", ok=True)
    assert h.overall_ok()


def test_latency_window_keeps_only_the_last_n_and_reports_p50():
    h = HealthMonitor(latency_window=3)
    for ms in (100, 200, 300, 400):
        h.record("jev", ok=True, latency_ms=ms)
    c = h.get("jev")
    assert c.latencies_ms == [200, 300, 400] and c.p50_ms() == 300


def test_p50_is_none_with_no_latency_samples():
    h = HealthMonitor()
    h.record("jev", ok=True)
    assert h.get("jev").p50_ms() is None


def test_snapshot_shape():
    h = HealthMonitor(degraded_after=1)
    h.record("jev", ok=True, latency_ms=200)
    h.record("groq", ok=False, detail="rate limited")
    snap = h.snapshot()
    assert snap["ok"] is False  # groq is degraded
    assert snap["components"]["jev"]["ok"] is True
    assert snap["components"]["groq"]["degraded"] is True and snap["components"]["groq"]["last_detail"] == "rate limited"


def test_stuck_job_detection():
    now = [1000.0]
    h = HealthMonitor(clock=lambda: now[0])
    h.job_event("j1")
    h.job_event("j2")
    now[0] += 400
    h.job_event("j2")  # j2 got a fresh event, j1 didn't
    assert h.stuck_jobs(["j1", "j2"], stuck_after=300) == ["j1"]


def test_a_job_with_no_events_yet_is_not_considered_stuck():
    h = HealthMonitor()
    assert h.stuck_jobs(["brand-new-job"]) == []


def test_job_ended_forgets_it_so_a_reused_id_starts_fresh():
    now = [1000.0]
    h = HealthMonitor(clock=lambda: now[0])
    h.job_event("j1")
    now[0] += 400
    h.job_ended("j1")
    assert h.stuck_jobs(["j1"], stuck_after=300) == []
