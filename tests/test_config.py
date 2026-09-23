import pytest

from evie.config import load_settings


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        load_settings()


def test_key_and_defaults(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    s = load_settings()
    assert s.openrouter_key == "sk-test"
    assert s.jev_model == "jev-1.13"
    assert s.core_host == "127.0.0.1" and s.core_port == 8765
