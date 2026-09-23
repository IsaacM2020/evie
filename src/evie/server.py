"""Evie core: a tiny local HTTP server. The menu bar app is its only client in Phase 0."""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Callable, Literal

import uvicorn
from fastapi import FastAPI
from pydantic import AwareDatetime, BaseModel

from evie import __version__
from evie.calendar_store import TZ, CalendarStore, CalEvent
from evie.config import load_settings
from evie.jev import JevClient
from evie.switchboard import Switchboard
from evie.switchboard.context import Context


class DecideIn(BaseModel):
    utterance: str
    speaker: Literal["isaac", "other", "unknown"] = "isaac"
    in_call: bool = False
    front_app: str = ""
    recent: list[str] = []
    active_jobs: list[str] = []


class EventIn(BaseModel):
    title: str
    start: AwareDatetime
    end: AwareDatetime
    all_day: bool = False
    calendar: str = ""


class CalendarIn(BaseModel):
    events: list[EventIn]


def create_app(make_switchboard: Callable[[], Switchboard], probe: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.sb = make_switchboard()
        app.state.jev_ok = None
        if probe:
            o = await app.state.sb.handle(Context(utterance="what time is it", speaker="isaac"))
            app.state.jev_ok = o.decision is not None
        yield
        await app.state.sb.aclose()

    app = FastAPI(lifespan=lifespan)
    app.state.calendar = CalendarStore()

    @app.get("/status")
    async def status() -> dict:
        return {"ok": True, "version": __version__, "jev_ok": app.state.jev_ok}

    @app.post("/decide")
    async def decide(body: DecideIn) -> dict:
        o = await app.state.sb.handle(Context(
            utterance=body.utterance, speaker=body.speaker, in_call=body.in_call,
            front_app=body.front_app, recent=tuple(body.recent), active_jobs=tuple(body.active_jobs),
        ))
        if o.decision is not None:
            app.state.jev_ok = True
        elif o.verdict.reason.startswith("jev unavailable"):
            app.state.jev_ok = False
        return o.to_dict()

    @app.post("/calendar")
    async def calendar(body: CalendarIn) -> dict:
        events = [CalEvent(**e.model_dump()) for e in body.events]
        app.state.calendar.update(events, at=datetime.now(TZ))
        return {"ok": True, "count": len(events)}

    @app.get("/debug/calendar")
    async def debug_calendar() -> dict:
        today = datetime.now(TZ).date()
        cal = app.state.calendar
        return {"today": cal.summary(today), "tomorrow": cal.summary(today + timedelta(days=1)),
                "stale": cal.stale(datetime.now(TZ))}

    return app


def main() -> None:
    s = load_settings()
    uvicorn.run(create_app(lambda: Switchboard(JevClient(s))), host=s.core_host, port=s.core_port,
                log_level="info")
