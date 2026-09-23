"""Settings for Evie. Secrets (OpenRouter for Jev, Groq for words) come from the repo .env."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO = Path(__file__).resolve().parents[2]
load_dotenv(REPO / ".env")


@dataclass(frozen=True)
class Settings:
    openrouter_key: str
    groq_key: str = ""
    jev_model: str = "jev-1.13"
    jev_url: str = "https://openrouter.ai/api/v1/systemone"
    jev_timeout_s: float = 3.0
    groq_model: str = "openai/gpt-oss-20b"
    groq_url: str = "https://api.groq.com/openai/v1"
    groq_timeout_s: float = 4.0
    core_host: str = "127.0.0.1"
    core_port: int = 8765


def load_settings() -> Settings:
    keys = {}
    for name in ("OPENROUTER_API_KEY", "GROQ_API_KEY"):
        keys[name] = os.environ.get(name, "")
        if not keys[name]:
            raise RuntimeError(f"{name} missing: put it in ~/Elemental/Water/evie/.env")
    return Settings(openrouter_key=keys["OPENROUTER_API_KEY"], groq_key=keys["GROQ_API_KEY"])
