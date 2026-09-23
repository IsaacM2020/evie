"""Read-only Google Calendar for Evie. Phase 1 uses it for 'what's on tomorrow'."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Singapore")
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
APP_DIR = Path.home() / "Library" / "Application Support" / "Evie"
CLIENT_SECRET = APP_DIR / "google_client_secret.json"
TOKEN = APP_DIR / "google_token.json"


class CalendarUnavailable(Exception):
    """Google can't be reached right now (no internet, Google down). Not a login problem."""


@dataclass(frozen=True)
class Event:
    title: str
    start: datetime
    end: datetime
    all_day: bool


def _when(w: dict) -> tuple[datetime, bool]:
    if "dateTime" in w:
        return datetime.fromisoformat(w["dateTime"]).astimezone(TZ), False
    return datetime.combine(date.fromisoformat(w["date"]), time.min, TZ), True


def parse_events(items: list[dict]) -> list[Event]:
    out = []
    for it in items:
        if it.get("status") == "cancelled":
            continue
        start, all_day = _when(it["start"])
        end, _ = _when(it["end"])
        out.append(Event(it.get("summary") or "(no title)", start, end, all_day))
    return sorted(out, key=lambda e: (not e.all_day, e.start))


def load_credentials():
    from google.auth.exceptions import RefreshError, TransportError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if TOKEN.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
        except ValueError:  # corrupt token file (JSONDecodeError is a ValueError): log in again
            creds = None
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:  # dead token (the old invalid_grant): log in again instead of crashing
            creds = None
        except TransportError as e:  # no internet: the token is fine, so don't make Isaac log in again
            raise CalendarUnavailable(f"can't reach Google right now: {e}") from e
    if not creds or not creds.valid:
        if not CLIENT_SECRET.exists():
            raise FileNotFoundError(f"Put the Google OAuth desktop client JSON at {CLIENT_SECRET}")
        creds = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES).run_local_server(port=0)
    APP_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN.write_text(creds.to_json())
    TOKEN.chmod(0o600)
    return creds


def events_on(day: date) -> list[Event]:
    from googleapiclient.discovery import build

    svc = build("calendar", "v3", credentials=load_credentials(), cache_discovery=False)
    lo = datetime.combine(day, time.min, TZ)
    hi = lo + timedelta(days=1)
    resp = svc.events().list(calendarId="primary", timeMin=lo.isoformat(), timeMax=hi.isoformat(),
                             singleEvents=True, orderBy="startTime").execute()
    return parse_events(resp.get("items", []))


def main(argv: list[str] | None = None) -> None:
    arg = (argv if argv is not None else sys.argv[1:] or ["today"])[0]
    day = datetime.now(TZ).date() + timedelta(days=1 if arg == "tomorrow" else 0)
    for e in events_on(day):
        print("all day    " if e.all_day else f"{e.start:%H:%M}-{e.end:%H:%M}", e.title)


if __name__ == "__main__":
    main()
