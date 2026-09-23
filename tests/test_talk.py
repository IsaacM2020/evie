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
    assert body["model"] == "qwen/qwen3.8-27b"
    assert body["reasoning_effort"] == "none" and "include_reasoning" not in body
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
async def test_gpt_oss_hides_its_reasoning():
    route = respx.post(URL).mock(return_value=ok("ok"))
    oss = Settings(openrouter_key="sk-or", groq_key="gsk-test", groq_model="openai/gpt-oss-20b")
    await Talker(GroqClient(oss)).reply("hi", {})
    body = json.loads(route.calls[0].request.content)
    assert body["reasoning_effort"] == "low" and body["include_reasoning"] is False


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


@respx.mock
async def test_groq_warm_is_a_head_request():
    route = respx.head("https://api.groq.com/openai/v1/models").respond(200)
    await GroqClient(S).warm()
    assert route.call_count == 1


from evie.talk import fill_math


def test_fill_math_computes_bracketed_expressions():
    assert fill_math("That's [[0.18*240]].") == "That's 43.2."
    assert fill_math("[[17*23]] exactly") == "391 exactly"
    assert fill_math("About [[1000/3]] each") == "About 333.33 each"
    assert fill_math("[[(2+3)**2 % 7]]") == "4"


def test_fill_math_refuses_anything_but_arithmetic():
    assert fill_math("[[__import__('os').system('ls')]]") == "that"
    assert fill_math("[[1/0]]") == "that"
    assert fill_math("[[9**9**9]]") == "that"  # too big: never hang Evie


@respx.mock
async def test_reply_prompt_asks_for_bracketed_maths_and_fills_it():
    route = respx.post(URL).mock(return_value=ok("It's [[0.18*240]]."))
    assert await talker().reply("what's 18 percent of 240", {}) == "It's 43.2."
    assert "[[" in json.loads(route.calls[0].request.content)["messages"][0]["content"]


@respx.mock
async def test_extract_asks_for_json_and_parses_it():
    route = respx.post(URL).mock(return_value=ok('{"query": "Espresso", "kind": "track"}'))
    t = Talker(GroqClient(S))
    out = await t.extract('Return {"query": string, "kind": string}.', "play espresso")
    body = json.loads(route.calls[0].request.content)
    assert body["response_format"] == {"type": "json_object"} and "play espresso" in body["messages"][1]["content"]
    assert out == {"query": "Espresso", "kind": "track"}


@respx.mock
async def test_extract_gives_empty_dict_on_junk_or_failure():
    respx.post(URL).mock(return_value=ok("sure! here you go"))
    assert await Talker(GroqClient(S)).extract("x", "y") == {}
    respx.post(URL).mock(return_value=httpx.Response(401, text="bad key"))
    assert await Talker(GroqClient(S)).extract("x", "y") == {}


@respx.mock
async def test_a_stalled_groq_call_is_hedged_with_a_second_one():
    import asyncio
    import time
    n = []

    async def handler(request):
        n.append(1)
        if len(n) == 1:
            await asyncio.sleep(2.0)  # the 21:11 stall: this one would have hit the 4 s timeout
            return ok("late")
        return ok("fast")

    respx.post(URL).mock(side_effect=handler)
    t0 = time.perf_counter()
    assert await GroqClient(S, hedge_after_s=0.05).chat("sys", "user") == "fast"
    assert time.perf_counter() - t0 < 1.0 and len(n) == 2


@respx.mock
async def test_quick_groq_call_is_never_duplicated():
    route = respx.post(URL).mock(return_value=ok("hi"))
    assert await GroqClient(S, hedge_after_s=0.5).chat("sys", "user") == "hi"
    assert route.call_count == 1
