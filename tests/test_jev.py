import httpx
import pytest
import respx

from evie.config import Settings
from evie.jev import JevClient, JevError

URL = "https://openrouter.ai/api/v1/systemone"
S = Settings(openrouter_key="sk-test")
Q = {"x": {"type": "noul", "instructions": "Is it?"}}
OK = {"answers": {"x": {"type": "noul", "noul": 0.9}}, "usage": {"cost": 0.00002}}


@respx.mock
async def test_ask_returns_answers_latency_cost_and_sends_auth():
    route = respx.post(URL).respond(200, json=OK)
    r = await JevClient(S).ask("state", Q)
    assert r.answers["x"]["noul"] == 0.9
    assert r.cost_usd == 0.00002
    assert r.latency_ms >= 0
    sent = route.calls[0].request
    assert sent.headers["Authorization"] == "Bearer sk-test"
    assert b'"model":"jev-1.13"' in sent.content.replace(b" ", b"")


@respx.mock
async def test_4xx_raises_without_retry():
    route = respx.post(URL).respond(403, json={"error": {"message": "Key limit exceeded"}})
    with pytest.raises(JevError, match="403"):
        await JevClient(S).ask("s", Q)
    assert route.call_count == 1


@respx.mock
async def test_5xx_retries_once_then_raises():
    route = respx.post(URL).respond(502)
    with pytest.raises(JevError):
        await JevClient(S).ask("s", Q)
    assert route.call_count == 2


@respx.mock
async def test_timeout_then_success():
    route = respx.post(URL).mock(side_effect=[httpx.ReadTimeout("slow"), httpx.Response(200, json=OK)])
    r = await JevClient(S).ask("s", Q)
    assert r.answers["x"]["noul"] == 0.9
    assert route.call_count == 2


@respx.mock
async def test_missing_answer_raises():
    respx.post(URL).respond(200, json={"answers": {}, "usage": {}})
    with pytest.raises(JevError, match="missing answers"):
        await JevClient(S).ask("s", Q)
