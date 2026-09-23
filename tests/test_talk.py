import json

import httpx
import pytest
import respx

from evie.config import Settings
from evie.talk import FALLBACK, GroqClient, Talker, clean

URL = "https://api.groq.com/openai/v1/chat/completions"
S = Settings(openrouter_key="sk-or", groq_key="gsk-test")


def ok(text):
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


def talker():
    return Talker(GroqClient(S))


@respx.mock
async def test_reply_sends_model_persona_and_facts():
    route = respx.post(URL).mock(return_value=ok("It's 4:05."))
    out = await talker().reply("what time is it", {"now": "Tue 23 Sep, 16:05"})
    assert out == "It's 4:05."
    req = route.calls[0].request
    body = json.loads(req.content)
    assert req.headers["Authorization"] == "Bearer gsk-test"
    assert body["model"] == "openai/gpt-oss-20b"
    assert "Evie" in body["messages"][0]["content"]
    assert "Current date and time: Tue 23 Sep, 16:05" in body["messages"][1]["content"]
    assert "what time is it" in body["messages"][1]["content"]


@respx.mock
async def test_reply_labels_calendar_and_job_facts_in_plain_english():
    route = respx.post(URL).mock(return_value=ok("ok"))
    await talker().reply("what's on", {"calendar_today": "Today: 9:00 Math", "calendar_tomorrow": "x",
                                       "job": "Working on: fix it"})
    content = json.loads(route.calls[0].request.content)["messages"][1]["content"]
    assert "Isaac's calendar today: Today: 9:00 Math" in content
    assert "Isaac's calendar tomorrow: x" in content
    assert "Evie's background job: Working on: fix it" in content


@respx.mock
async def test_5xx_then_ok_retries():
    route = respx.post(URL).mock(side_effect=[httpx.Response(500), ok("Hey.")])
    assert await talker().narrate("fix bug", "Ran: pytest") == "Hey."
    assert route.call_count == 2


@respx.mock
async def test_timeout_gives_fallback_without_a_second_wait():
    route = respx.post(URL).mock(side_effect=httpx.ReadTimeout("slow"))
    assert await talker().clarify("evie play that song", "missing detail") == FALLBACK
    assert route.call_count == 1  # one 4s wait, not two


@respx.mock
async def test_connect_error_retries_once():
    route = respx.post(URL).mock(side_effect=[httpx.ConnectError("blip"), ok("Hi.")])
    assert await talker().reply("hi", {}) == "Hi."
    assert route.call_count == 2


@respx.mock
async def test_4xx_gives_fallback_without_retry():
    route = respx.post(URL).respond(401, json={"error": "bad key"})
    assert await talker().summarize("fix bug", "done") == FALLBACK
    assert route.call_count == 1


@respx.mock
async def test_empty_content_gives_fallback():
    respx.post(URL).mock(return_value=ok("   "))
    assert await talker().reply("hi", {}) == FALLBACK


@respx.mock
async def test_reply_output_is_cleaned():
    respx.post(URL).mock(return_value=ok("**Sure** — it's 4pm. You have Math. Then iGEM. Then sleep."))
    assert await talker().reply("time", {}) == "Sure, it's 4pm. You have Math."


def test_clean_strips_markdown():
    assert clean("**Done**, `pytest` passes") == "Done, pytest passes"


def test_clean_replaces_em_and_en_dashes():
    assert clean("Fixed it — tests pass") == "Fixed it, tests pass"
    assert clean("Fixed it – tests pass") == "Fixed it, tests pass"


def test_clean_keeps_two_sentences():
    assert clean("One. Two! Three? Four.") == "One. Two!"


@pytest.mark.live
async def test_live_groq_reply_is_short_and_clean():
    from evie.config import load_settings
    t = Talker(GroqClient(load_settings()))
    out = await t.reply("what's on tomorrow", {"now": "Tue 23 Sep, 16:05",
                                               "calendar_tomorrow": "Tomorrow: 9:00 Math, 14:30 iGEM"})
    await t.aclose()
    assert out != FALLBACK and "math" in out.lower()
    assert "*" not in out and "—" not in out


def test_clean_keeps_underscores_inside_names():
    assert clean("The longest is tests/test_brain.py, then snake_case_name") == \
        "The longest is tests/test_brain.py, then snake_case_name"


def test_clean_strips_underscore_emphasis():
    assert clean("That's _really_ done") == "That's really done"
