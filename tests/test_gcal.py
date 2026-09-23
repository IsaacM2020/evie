from datetime import datetime

from evie.gcal import TZ, parse_events


def test_parse_timed_all_day_cancelled_and_untitled():
    items = [
        {"summary": "iGEM call", "start": {"dateTime": "2026-09-24T18:00:00+08:00"},
         "end": {"dateTime": "2026-09-24T19:00:00+08:00"}},
        {"summary": "Chem IA due", "start": {"date": "2026-09-24"}, "end": {"date": "2026-09-25"}},
        {"summary": "old", "status": "cancelled", "start": {"date": "2026-09-24"}, "end": {"date": "2026-09-25"}},
        {"start": {"dateTime": "2026-09-24T01:00:00Z"}, "end": {"dateTime": "2026-09-24T02:00:00Z"}},
    ]
    ev = parse_events(items)
    assert [e.title for e in ev] == ["Chem IA due", "(no title)", "iGEM call"]
    assert ev[0].all_day and not ev[2].all_day
    assert ev[1].start == datetime(2026, 9, 24, 9, 0, tzinfo=TZ)   # 01:00Z is 09:00 in Singapore
    assert ev[2].start.hour == 18 and ev[2].start.tzinfo == TZ


import pytest

import evie.gcal as gcal


class FakeCreds:
    def __init__(self, error):
        self.expired, self.refresh_token, self.valid, self._error = True, "r", False, error

    def refresh(self, request):
        raise self._error


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(gcal, "APP_DIR", tmp_path)
    monkeypatch.setattr(gcal, "TOKEN", tmp_path / "google_token.json")
    monkeypatch.setattr(gcal, "CLIENT_SECRET", tmp_path / "missing_client.json")


def test_corrupt_token_falls_back_to_login(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    gcal.TOKEN.write_text("{not json")
    with pytest.raises(FileNotFoundError):   # reached the log-in path instead of crashing on the JSON
        gcal.load_credentials()


def test_dead_token_falls_back_to_login(tmp_path, monkeypatch):
    from google.auth.exceptions import RefreshError
    from google.oauth2.credentials import Credentials
    _isolate(tmp_path, monkeypatch)
    gcal.TOKEN.write_text("{}")
    monkeypatch.setattr(Credentials, "from_authorized_user_file",
                        classmethod(lambda cls, *a, **k: FakeCreds(RefreshError("invalid_grant"))))
    with pytest.raises(FileNotFoundError):
        gcal.load_credentials()


def test_network_blip_on_refresh_is_a_clear_error(tmp_path, monkeypatch):
    from google.auth.exceptions import TransportError
    from google.oauth2.credentials import Credentials
    _isolate(tmp_path, monkeypatch)
    gcal.TOKEN.write_text("{}")
    monkeypatch.setattr(Credentials, "from_authorized_user_file",
                        classmethod(lambda cls, *a, **k: FakeCreds(TransportError("no internet"))))
    with pytest.raises(gcal.CalendarUnavailable):
        gcal.load_credentials()
