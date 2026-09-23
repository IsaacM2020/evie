"""Evie core: a local server on 127.0.0.1:8765. The menu bar app is its only client.

App -> core over HTTP: audio (/voice), typed text (/hear), calendar snapshots (/calendar).
Core -> app over one WebSocket (/ws): everything that happens, live.
"""
import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Literal

import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import AwareDatetime, BaseModel

from evie import __version__
from evie.calendar_store import TZ, CalendarStore, CalEvent
from evie.config import Settings, load_settings
from evie.ears import FRAME, wav_to_pcm
from evie.events import EventBus
from evie.hands import Hands
from evie.jev import JevClient
from evie.switchboard import Switchboard
from evie.switchboard.context import Context

log = logging.getLogger("evie.server")


class DecideIn(BaseModel):
    utterance: str
    speaker: Literal["isaac", "other", "unknown"] = "isaac"
    in_call: bool = False
    front_app: str = ""
    recent: list[str] = []
    active_jobs: list[str] = []


class HearIn(BaseModel):
    text: str
    speaker: Literal["isaac", "other", "unknown"] = "isaac"


class EventIn(BaseModel):
    title: str
    start: AwareDatetime
    end: AwareDatetime
    all_day: bool = False
    calendar: str = ""
    id: str = ""


class CalendarInfoIn(BaseModel):
    id: str = ""
    title: str
    source: str = ""
    writable: bool = False
    used: bool = True
    account: str = ""
    events: int = 0


class CalendarIn(BaseModel):
    events: list[EventIn]
    calendars: list[CalendarInfoIn] | None = None


class ModeIn(BaseModel):
    mode: Literal["off", "shadow", "live"]


class EnrollIn(BaseModel):
    on: bool


class HandsResultIn(BaseModel):
    id: str
    ok: bool
    detail: str = ""
    data: dict = {}


@dataclass
class Deps:
    """Everything the core runs. Tests pass fakes; build_deps() makes the real ones."""
    sb: Switchboard
    calendar: CalendarStore
    bus: EventBus
    brain: object | None = None
    mouth: object | None = None
    stt: object | None = None
    runner: object | None = None
    warm: Callable[[], Awaitable[dict]] | None = None
    pings: list = field(default_factory=list)  # keep-warm callables
    open_mic: object | None = None
    voiceid: object | None = None
    hands: object | None = None
    close: Callable[[], Awaitable[None]] | None = None


async def keep_warm(pings: list[Callable[[], Awaitable[None]]], interval_s: float = 20.0) -> None:
    """Every interval, touch each API connection so it never goes cold."""
    while True:
        for ping in pings:
            try:
                await ping()
            except Exception:
                log.debug("keep-warm ping failed", exc_info=True)
        await asyncio.sleep(interval_s)


def _job_dict(job) -> dict | None:
    if job is None:
        return None
    return {"id": job.id, "goal": job.goal, "status": job.status, "started": job.started,
            "events": job.events[-5:]}


def create_app(make_deps: Callable[[], Deps], probe: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        d = make_deps()
        app.state.d = d
        app.state.jev_ok = None
        app.state.ready = {"stt_ready": False, "voice_ready": False}
        app.state.enrolling = False
        app.state.app_clients = 0  # the menu bar app's live /ws connections
        if d.warm:
            app.state.ready = await d.warm()
        if probe:
            o = await d.sb.handle(Context(utterance="what time is it", speaker="isaac"))
            app.state.jev_ok = o.decision is not None
        warmer = asyncio.create_task(keep_warm(d.pings)) if d.pings else None
        yield
        if warmer:
            warmer.cancel()
        if d.runner:
            await d.runner.shutdown()
        if d.open_mic:
            d.open_mic.close()
        if d.close:
            await d.close()
        await d.sb.aclose()

    app = FastAPI(lifespan=lifespan)

    def need(*parts):
        d: Deps = app.state.d
        if any(getattr(d, p) is None for p in parts):
            raise HTTPException(503, "voice parts not running")
        return d

    @app.get("/status")
    async def status() -> dict:
        d: Deps = app.state.d
        out = {"ok": True, "version": __version__, "jev_ok": app.state.jev_ok, **app.state.ready,
               "calendar_fresh": not d.calendar.stale(datetime.now(TZ)),
               "job": _job_dict(d.runner.current) if d.runner else None,
               "app_online": app.state.app_clients > 0}
        if d.open_mic and d.voiceid:
            out |= {"ears_mode": d.open_mic.modes.mode, "voiceprint": d.voiceid.print.status()}
        return out

    @app.post("/decide")
    async def decide(body: DecideIn) -> dict:
        o = await app.state.d.sb.handle(Context(
            utterance=body.utterance, speaker=body.speaker, in_call=body.in_call,
            front_app=body.front_app, recent=tuple(body.recent), active_jobs=tuple(body.active_jobs),
        ))
        if o.decision is not None:
            app.state.jev_ok = True
        elif o.verdict.reason.startswith("jev unavailable"):
            app.state.jev_ok = False
        return o.to_dict()

    @app.post("/hear")
    async def hear(body: HearIn) -> dict:
        return await need("brain").brain.hear(body.text, body.speaker)

    @app.post("/voice/start")
    async def voice_start() -> dict:
        d = need("mouth")
        d.mouth.stop()  # barge-in: Isaac pressed the talk key, so Evie stops talking
        if d.open_mic:
            d.open_mic.ptt_start()  # this turn belongs to the key, not the open mic
        d.bus.publish("state", state="listening")
        return {"ok": True}

    async def learn_voice(d: Deps, wav: bytes) -> bool:
        """A talk-key clip is certainly Isaac: it teaches voice ID (embedding only, ~50 ms)."""
        pcm = wav_to_pcm(wav)
        learned = await asyncio.get_running_loop().run_in_executor(None, d.voiceid.learn, pcm)
        if learned:
            d.bus.publish("voiceprint", **d.voiceid.print.status())
        return learned

    @app.post("/voice")
    async def voice(request: Request) -> dict:
        d = need("brain", "stt")
        audio = await request.body()
        try:
            if d.voiceid and app.state.enrolling:
                await learn_voice(d, audio)
                d.bus.publish("state", state="idle")
                return {"text": "", "action": "ignore", "reason": "enrolled", "route": None, "said": None,
                        "voiceprint": d.voiceid.print.status()}
            if d.voiceid:
                asyncio.get_running_loop().create_task(learn_voice(d, audio))
            return await transcribe_and_hear(d, audio)
        finally:
            if d.open_mic:
                d.open_mic.ptt_end()

    async def transcribe_and_hear(d: Deps, audio: bytes) -> dict:
        t0 = time.perf_counter()
        text = await d.stt.transcribe(audio)
        stt_ms = round((time.perf_counter() - t0) * 1000)
        if not text:
            d.bus.publish("heard", text="")
            d.bus.publish("state", state="working" if d.runner and d.runner.current else "idle")
            return {"text": "", "action": "ignore", "reason": "heard nothing", "route": None, "said": None,
                    "stt_ms": stt_ms}
        return {**await d.brain.hear(text), "stt_ms": stt_ms}

    @app.get("/job")
    async def job() -> dict | None:
        return _job_dict(need("runner").runner.current)

    @app.post("/job/stop")
    async def job_stop() -> dict:
        d = need("runner")
        running = d.runner.current
        if not running:
            return {"stopped": False}
        await d.runner.stop()
        d.bus.publish("job_done", id=running.id, status="stopped", summary="Stopped.", result="")
        d.bus.publish("state", state="idle")
        return {"stopped": True}

    @app.websocket("/ws")
    async def ws(sock: WebSocket) -> None:
        d: Deps = app.state.d
        await sock.accept()
        q = d.bus.subscribe()
        app.state.app_clients += 1
        try:
            await sock.send_json({"kind": "hello", "t": time.time(),
                                  "job": _job_dict(d.runner.current) if d.runner else None})
            while True:
                await sock.send_json(await q.get())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            app.state.app_clients -= 1
            d.bus.unsubscribe(q)

    @app.post("/hands/result")
    async def hands_result(body: HandsResultIn) -> dict:
        d = need("hands")
        return {"accepted": d.hands.result(body.id, body.ok, body.detail, body.data)}

    # -- open mic ----------------------------------------------------------------------------
    def ears_body(d: Deps) -> dict:
        return {"mode": d.open_mic.modes.mode, "enrolling": app.state.enrolling,
                "voiceprint": d.voiceid.print.status()}

    @app.get("/ears")
    async def ears() -> dict:
        return ears_body(need("open_mic", "voiceid"))

    @app.post("/ears/mode")
    async def ears_mode(body: ModeIn) -> dict:
        d = need("open_mic", "voiceid")
        if body.mode == "live" and not d.voiceid.print.ready:
            raise HTTPException(409, "Evie needs to learn your voice first (hold the talk key a few times)")
        d.open_mic.modes.set(body.mode)
        d.bus.publish("ears", **ears_body(d))
        return ears_body(d)

    @app.get("/voiceid")
    async def voiceid() -> dict:
        return ears_body(need("open_mic", "voiceid"))

    @app.post("/voiceid/enroll")
    async def enroll(body: EnrollIn) -> dict:
        d = need("open_mic", "voiceid")
        app.state.enrolling = body.on
        d.bus.publish("ears", **ears_body(d))
        return ears_body(d)

    @app.post("/voiceid/reset")
    async def voiceid_reset() -> dict:
        d = need("open_mic", "voiceid")
        d.voiceid.print.clear()
        if d.open_mic.modes.mode == "live":
            d.open_mic.modes.set("shadow")
        d.bus.publish("ears", **ears_body(d))
        return ears_body(d)

    @app.get("/recorder")
    async def recorder() -> dict:
        rec = getattr(need("open_mic").open_mic, "recorder", None)
        if rec is None:
            raise HTTPException(503, "recorder not running")
        return {"on": rec.enabled, "segments": len(rec.rows())}

    @app.post("/recorder")
    async def recorder_set(body: EnrollIn) -> dict:
        rec = getattr(need("open_mic").open_mic, "recorder", None)
        if rec is None:
            raise HTTPException(503, "recorder not running")
        rec.set(body.on)
        return {"on": rec.enabled, "segments": len(rec.rows())}

    @app.websocket("/ws/ears")
    async def ws_ears(sock: WebSocket) -> None:
        """The app streams the mic here: binary = 16 kHz int16 samples, text = JSON context."""
        d: Deps = app.state.d
        await sock.accept()
        if not d.open_mic:
            await sock.close()
            return
        rest = np.zeros(0, dtype=np.float32)
        try:
            while True:
                msg = await sock.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("bytes") is not None:
                    pcm = np.frombuffer(msg["bytes"], dtype="<i2").astype(np.float32) / 32768
                    rest = np.concatenate([rest, pcm])
                    while len(rest) >= FRAME:
                        d.open_mic.feed(rest[:FRAME])
                        rest = rest[FRAME:]
                elif msg.get("text"):
                    try:
                        ctx = json.loads(msg["text"])
                    except ValueError:
                        continue
                    for k in ("front_app", "in_call"):
                        if k in ctx:
                            d.open_mic.context[k] = ctx[k]
        except (WebSocketDisconnect, RuntimeError):
            pass

    @app.post("/calendar")
    async def calendar(body: CalendarIn) -> dict:
        events = [CalEvent(**e.model_dump()) for e in body.events]
        cals = None if body.calendars is None else [c.model_dump() for c in body.calendars]
        app.state.d.calendar.update(events, at=datetime.now(TZ), calendars=cals)
        return {"ok": True, "count": len(events)}

    @app.get("/debug/calendar")
    async def debug_calendar() -> dict:
        today = datetime.now(TZ).date()
        cal = app.state.d.calendar
        return {"now": cal.now_line(datetime.now(TZ)), "today": cal.summary(today),
                "tomorrow": cal.summary(today + timedelta(days=1)), "stale": cal.stale(datetime.now(TZ)),
                "reading": [f"{c['title']} ({c['source']} {c['account'][:6]}, {c['events']} events)"
                            for c in cal.calendars if c["used"]],
                "ignored": [f"{c['title']} ({c['source']})" for c in cal.calendars if not c["used"]]}

    return app


def build_deps(s: Settings) -> Deps:
    """The real thing: Jev, Groq, Pocket TTS, Whisper, Claude Code, all wired to one event bus."""
    from evie.brain import Brain
    from evie.jobs import JobRunner
    from evie.facts import FactStore
    from evie.narrator import Narrator
    from evie.remember import Remember, Todoist
    from evie.countdown import Countdown
    from evie.skills.catalog import Skills
    from evie.skills.events import EventSkills
    from evie.skills.music import SpotifySearch
    from evie.skills.parse import say_duration
    from evie.skills.system import System, installed_apps
    from evie.skills.tasks import TaskSkills
    from evie.skills.timers import Timers, done_line
    from evie.stt import Transcriber
    from evie.talk import GroqClient, Talker
    from evie.voice import Mouth, PocketVoice, SpeakerOut

    jev = JevClient(s)
    sb = Switchboard(jev)
    bus = EventBus()
    cal = CalendarStore()
    groq = GroqClient(s)
    talker = Talker(groq)
    voice = PocketVoice()

    def on_say(text: str) -> None:
        bus.publish("say", text=text)
        bus.publish("state", state="speaking")

    runner: JobRunner | None = None

    def on_quiet() -> None:
        bus.publish("state", state="working" if runner and runner.current else "idle")

    def on_audio(text: str) -> None:  # the moment real sound starts: what Isaac actually hears
        bus.publish("audio", text=text)

    mouth = Mouth(voice, SpeakerOut(voice.rate), on_say=on_say, clips=voice.prepare_clips(),
                  on_quiet=on_quiet, on_audio=on_audio)
    narrator = Narrator(jev, talker, mouth, bus)
    runner = JobRunner(narrator.on_event, narrator.on_done)
    hands = Hands(bus)
    spotify = SpotifySearch(s.spotify_id, s.spotify_secret)
    timers = Timers(lambda t: mouth.say(done_line(t), kind="reply"))
    skills = Skills(hands, talker, jev, System(), spotify, timers, apps=installed_apps)
    countdown = Countdown()
    skills.events = EventSkills(hands, talker, jev, cal, skills, countdown)
    todoist = Todoist(s.todoist_key)
    skills.tasks = TaskSkills(todoist, jev, skills)
    remember = Remember(talker, hands, todoist, FactStore(), cal, skills, timers=timers)
    brain = Brain(sb, talker, mouth, runner, narrator, cal, bus, jev, skills=skills, remember=remember,
                  countdown=countdown)
    stt = Transcriber(s, backend=s.stt_backend)
    open_mic, voiceid = build_ears(stt, brain, mouth, bus)

    async def warm() -> dict:
        t0 = time.perf_counter()
        await stt.warm()
        log.info("whisper warm in %.1fs", time.perf_counter() - t0)
        mouth.start()
        timers.restore()
        return {"stt_ready": True, "voice_ready": True}

    async def close() -> None:
        timers.close()
        await spotify.aclose()
        await todoist.aclose()
        await mouth.aclose()
        await talker.aclose()
        await stt.aclose()

    return Deps(sb=sb, calendar=cal, bus=bus, brain=brain, mouth=mouth, stt=stt, runner=runner,
                warm=warm, close=close, pings=[jev.warm, groq.warm], open_mic=open_mic, voiceid=voiceid,
                hands=hands)


def build_ears(stt, brain, mouth, bus):
    """Open mic + voice ID. Without the models (ops/get-ears-models.sh) Evie still works,
    push-to-talk only."""
    from evie.ears import MODELS, Segmenter, Vad
    from evie.open_mic import ModeStore, OpenMic
    from evie.voiceid import SpeakerEmbedder, VoiceId, VoicePrint
    if not (MODELS / "silero_vad.onnx").exists() or not (MODELS / "speaker.onnx").exists():
        log.warning("ears models missing: run ops/get-ears-models.sh. Push-to-talk only.")
        return None, None
    voiceid = VoiceId(SpeakerEmbedder(), VoicePrint())
    open_mic = OpenMic(Segmenter(), Vad().is_speech, voiceid, stt, brain, mouth, bus, ModeStore())
    from evie.recorder import SegmentRecorder
    open_mic.recorder = SegmentRecorder()
    open_mic.recorder.prune()
    brain.scene = lambda: open_mic.context
    return open_mic, voiceid


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    s = load_settings()
    uvicorn.run(create_app(lambda: build_deps(s)), host=s.core_host, port=s.core_port, log_level="info")
