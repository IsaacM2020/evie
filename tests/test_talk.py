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
    from evie.talk import MathError
    for bad in ("[[__import__('os').system('ls')]]", "[[1/0]]", "[[9**9**9]]", "[[open('x')]]", "[[x+1]]"):
        with pytest.raises(MathError):
            fill_math(bad)


def test_fill_math_does_trig_in_degrees_and_more():
    # 2026-09-23: "what's cos 60" came out as "That's that." (no trig in the evaluator)
    assert fill_math("[[cos(60)]]") == "0.5"
    assert fill_math("[[sin(30)]] and [[tan(45)]]") == "0.5 and 1"
    assert fill_math("[[cosr(pi)]]") == "-1"
    assert fill_math("[[sqrt(2)]]") == "1.41"
    assert fill_math("[[log(1000)]] [[ln(e)]] [[factorial(5)]]") == "3 1 120"
    assert fill_math("[[degrees(asin(0.5))]]") == "30"
    assert fill_math("[[round(2/3, 3)]]") == "0.67"


def test_fill_math_rejects_huge_factorials():
    from evie.talk import MathError
    with pytest.raises(MathError):
        fill_math("[[factorial(5000)]]")


@respx.mock
async def test_a_sum_she_cant_compute_is_said_honestly_not_that():
    respx.post(URL).mock(return_value=ok("That's [[foo(3)]]."))
    assert await talker().reply("what's foo 3", {}) == "I couldn't work that one out exactly, sorry."


@respx.mock
async def test_reply_and_clarify_know_what_evie_can_do():
    route = respx.post(URL).mock(return_value=ok("Sure."))
    t = talker()
    await t.reply("can you check my calendar", {})
    await t.clarify("add it", "a detail is missing")
    for call in route.calls:
        content = json.loads(call.request.content)["messages"][0]["content"]
        assert "read and change Isaac's calendar" in content and "can't yet" in content


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


def test_fill_math_reads_how_models_actually_write_it():
    assert fill_math("[[cos 60]]") == "0.5"
    assert fill_math("[[sin 30°]]") == "0.5"
    assert fill_math("[[2^10]]") == "1024"
    assert fill_math("[[sqrt 16 + 1]]") == "5"


@respx.mock
async def test_rate_limited_big_model_falls_back_to_qwen():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body["model"])
        if body["model"] == "openai/gpt-oss-120b":
            return httpx.Response(429, json={"error": {"message": "Rate limit reached"}})
        return ok('{"op": "done"}')

    respx.post(URL).mock(side_effect=handler)
    out = await GroqClient(S).chat("s", "u", json_mode=True, model="openai/gpt-oss-120b", reasoning="low")
    assert out == '{"op": "done"}' and calls == ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"]


@respx.mock
async def test_look_sends_the_screenshot_to_qwen_as_an_image():
    from evie.config import Settings
    from evie.talk import GroqClient
    route = respx.post("https://api.groq.com/openai/v1/chat/completions").respond(
        200, json={"choices": [{"message": {"content": '{"n": 3}'}}]})
    out = await GroqClient(Settings(openrouter_key="k", groq_key="g")).look("which box?", "iVBORw0KGgo=")
    body = route.calls[0].request.content.decode()
    assert out == '{"n": 3}' and "data:image/png;base64,iVBORw0KGgo=" in body and "qwen/qwen3.8-27b" in body


@respx.mock
async def test_a_rate_limited_planner_falls_back_down_its_chain_of_models():
    """Each Groq model has its own 8k tokens/minute (2026-09-24): the planner tries gpt-oss-120b,
    then gpt-oss-20b, then qwen."""
    from evie.config import Settings
    from evie.talk import GroqClient
    seen = []

    def reply(request):
        import json as _j
        m = _j.loads(request.content)["model"]
        seen.append(m)
        if m == "openai/gpt-oss-120b":
            return httpx.Response(429, json={"error": {"message": "rate limit"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(side_effect=reply)
    g = GroqClient(Settings(openrouter_key="k", groq_key="g"), hedge_after_s=5)
    out = await g.chat("s", "u", json_mode=True, model="openai/gpt-oss-120b",
                       fallbacks=["openai/gpt-oss-20b", "qwen/qwen3.8-27b"])
    assert out == "{}" and seen == ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]


async def test_readback_turns_the_request_into_a_short_line_or_a_question():
    import json as _j

    from evie.talk import Talker

    class G:
        def __init__(self, out):
            self.out, self.prompts = out, []

        async def chat(self, system, user, max_tokens=400, json_mode=False, model=None, reasoning=None, fallbacks=None):
            self.prompts.append(system + user)
            return _j.dumps(self.out)

    g = G({"line": "Checking why your website deploy failed", "unsure": False})
    rb = await Talker(g).readback("why did my website deploy fail")
    assert rb["line"] == "Checking why your website deploy failed" and not rb["unsure"]
    assert "speech-to-text" in g.prompts[0]
    g = G({"line": "", "unsure": True, "question": "Which website?"})
    assert (await Talker(g).readback("check my site thingy"))["question"] == "Which website?"


class _JsonGroq:
    def __init__(self, out=None, raw=None, error=None):
        self.out, self.raw, self.error, self.prompts = out, raw, error, []

    async def chat(self, system, user, max_tokens=400, json_mode=False, model=None, reasoning=None, fallbacks=None):
        import json as _j
        self.prompts.append((system, user))
        if self.error:
            raise self.error
        return self.raw if self.raw is not None else _j.dumps(self.out)


async def test_resolve_fills_in_that_song_from_the_conversation():
    from evie.talk import Talker
    g = _JsonGroq({"request": "play Trance by Travis Scott on Spotify"})
    out = await Talker(g).resolve("play that song on spotify", ['Someone: "trance, travis scott"'])
    assert out == "play Trance by Travis Scott on Spotify"
    system, user = g.prompts[0]
    assert "trance, travis scott" in user and "play that song on spotify" in user


async def test_resolve_keeps_his_words_when_it_cant():
    from evie.talk import TalkError, Talker
    for g in (_JsonGroq(error=TalkError("down")), _JsonGroq(raw="not json"), _JsonGroq({"request": ""}),
              _JsonGroq({"nope": 1})):
        assert await Talker(g).resolve("open it", ['Isaac: "hi"']) == "open it"


async def test_clarify_sees_the_conversation():
    from evie.talk import Talker
    g = _JsonGroq(raw="Which song, the Travis Scott one?")
    await Talker(g).clarify("play that song", "a detail is missing", recent=['Someone: "trance, travis scott"'])
    system, user = g.prompts[0]
    assert "trance, travis scott" in user and "already" in user.lower()


async def test_hedged_never_leaves_an_unread_error():
    """2026-09-24 14:09:30: "Task exception was never retrieved" (a ReadTimeout from the losing
    hedged request). Both requests finishing in the same tick used to leave the loser's error unread."""
    import asyncio
    import gc

    from evie.config import load_settings
    from evie.talk import GroqClient, TalkError

    seen = []
    loop = asyncio.get_running_loop()
    old = loop.get_exception_handler()
    loop.set_exception_handler(lambda l, ctx: seen.append(ctx.get("message", "")))
    try:
        for _ in range(20):
            go = asyncio.Event()
            n = {"calls": 0}

            class G(GroqClient):
                async def _once(self, body):
                    n["calls"] += 1
                    mine = n["calls"]
                    await go.wait()
                    if mine == 2:
                        raise TalkError("timeout: ReadTimeout('')")
                    return "ok"

            g = G(load_settings(), hedge_after_s=0.001)
            loop.call_later(0.01, go.set)
            assert await g._hedged({}) == "ok"
            await asyncio.sleep(0)
        gc.collect()
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(old)
    assert not [m for m in seen if "never retrieved" in m]


async def test_morning_brief_is_short_and_uses_only_the_facts():
    from evie.talk import Talker
    g = _JsonGroq(raw="Morning Isaac. School till 3:30, then sax at 4:45. It's Vedant's birthday.")
    out = await Talker(g).brief({"today": "Today: Vedant Bday (all day), 8:00-15:30 School", "due": "Email bio teacher"})
    system, user = g.prompts[0]
    assert out.startswith("Morning") and "Email bio teacher" in user and "Vedant Bday" in user
    assert "three short sentences" in user.lower()
    assert await Talker(_JsonGroq(error=__import__("evie.talk", fromlist=["TalkError"]).TalkError("x"))).brief({}) == ""


# T19 (Isaac, 2026-09-24): "when there's too much text, list it down" -> say it short, list it all.
from evie.talk import split_list  # noqa: E402


def test_a_list_answer_is_one_spoken_line_plus_items():
    say, items = split_list("You've got 4 things due, top one is Chem HW.\n- Chem tuition HW\n- Email bio teacher\n"
                            "* Physics worksheet\n2. Spanish vocab")
    assert say == "You've got 4 things due, top one is Chem HW."
    assert items == ["Chem tuition HW", "Email bio teacher", "Physics worksheet", "Spanish vocab"]


def test_two_bullets_are_not_a_list():
    say, items = split_list("Two things.\n- Chem HW\n- Sax")
    assert items == [] and say == "Two things. Chem HW. Sax."


def test_long_prose_is_said_short_and_the_rest_listed():
    text = ("Jev is the model that decides what Evie does with each sentence. It reads the words and gives "
            "probabilities for each route. Plain code then turns those into act, ask or ignore. The thresholds "
            "live in one file so the evals can tune them.")
    say, items = split_list(text)
    assert say == "Jev is the model that decides what Evie does with each sentence."
    assert len(items) == 3 and items[0].startswith("It reads the words")


def test_short_prose_is_left_alone():
    assert split_list("It's 4pm. You have Math. Then iGEM.") == ("It's 4pm. You have Math. Then iGEM.", [])


@respx.mock
async def test_reply_keeps_the_list_for_the_card_and_speaks_one_line():
    route = respx.post(URL).mock(return_value=ok("You've got 3 today, **Chem** first.\n- Chem HW\n- Sax — 4:45\n- Call Dada"))
    out = await talker().reply("what's due today", {})
    assert out == "You've got 3 today, Chem first." and out.items == ["Chem HW", "Sax, 4:45", "Call Dada"]
    assert "own line" in json.loads(route.calls[0].request.content)["messages"][1]["content"]


@respx.mock
async def test_a_plain_reply_has_no_items():
    respx.post(URL).mock(return_value=ok("It's 4:05."))
    out = await talker().reply("time", {})
    assert out == "It's 4:05." and out.items == []
