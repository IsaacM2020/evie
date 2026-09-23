"""Settings for Evie. The only secret is the OpenRouter key, read from the repo .env."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO = Path(__file__).resolve().parents[2]
load_dotenv(REPO / ".env")


@dataclass(frozen=True)
class Settings:
    openrouter_key: str
    jev_model: str = "jev-1.13"
    jev_url: str = "https://openrouter.ai/api/v1/systemone"
    jev_timeout_s: float = 3.0
    core_host: str = "127.0.0.1"
    core_port: int = 8765


def load_settings() -> Settings:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY missing: put it in ~/Elemental/Water/evie/.env")
    return Settings(openrouter_key=key)
