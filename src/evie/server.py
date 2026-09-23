"""Evie core: a local server on 127.0.0.1:8765. The menu bar app is its only client.

App -> core over HTTP: audio (/voice), typed text (/hear), calendar snapshots (/calendar).
Core -> app over one WebSocket (/ws): everything that happens, live.
"""
import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Literal

import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import AwareDatetime, BaseModel

from evie import __version__
from evie.calendar_store import TZ, CalendarStore, CalEvent
from evie.config import Settings, load_settings
from evie.events import EventBus
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


class CalendarIn(BaseModel):
    events: list[EventIn]


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
        return {"ok": True, "version": __version__, "jev_ok": app.state.jev_ok, **app.state.ready,
                "calendar_fresh": not d.calendar.stale(datetime.now(TZ)),
                "job": _job_dict(d.runner.current) if d.runner else None}

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
        d.bus.publish("state", state="listening")
        return {"ok": True}

    @app.post("/voice")
    async def voice(request: Request) -> dict:
        d = need("brain", "stt")
        t0 = time.perf_counter()
        text = await d.stt.transcribe(await request.body())
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
        try:
            await sock.send_json({"kind": "hello", "t": time.time(),
                                  "job": _job_dict(d.runner.current) if d.runner else None})
            while True:
                await sock.send_json(await q.get())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            d.bus.unsubscribe(q)

    @app.post("/calendar")
    async def calendar(body: CalendarIn) -> dict:
        events = [CalEvent(**e.model_dump()) for e in body.events]
        app.state.d.calendar.update(events, at=datetime.now(TZ))
        return {"ok": True, "count": len(events)}

    @app.get("/debug/calendar")
    async def debug_calendar() -> dict:
        today = datetime.now(TZ).date()
        cal = app.state.d.calendar
        return {"today": cal.summary(today), "tomorrow": cal.summary(today + timedelta(days=1)),
                "stale": cal.stale(datetime.now(TZ))}

    return app


def build_deps(s: Settings) -> Deps:
    """The real thing: Jev, Groq, Pocket TTS, Whisper, Claude Code, all wired to one event bus."""
    from evie.brain import Brain
    from evie.jobs import JobRunner
    from evie.narrator import Narrator
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
    brain = Brain(sb, talker, mouth, runner, narrator, cal, bus, jev)
    stt = Transcriber(s, backend=s.stt_backend)

    async def warm() -> dict:
        t0 = time.perf_counter()
        await stt.warm()
        log.info("whisper warm in %.1fs", time.perf_counter() - t0)
        mouth.start()
        return {"stt_ready": True, "voice_ready": True}

    async def close() -> None:
        await mouth.aclose()
        await talker.aclose()
        await stt.aclose()

    return Deps(sb=sb, calendar=cal, bus=bus, brain=brain, mouth=mouth, stt=stt, runner=runner,
                warm=warm, close=close, pings=[jev.warm, groq.warm])


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    s = load_settings()
    uvicorn.run(create_app(lambda: build_deps(s)), host=s.core_host, port=s.core_port, log_level="info")
