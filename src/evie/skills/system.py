"""Mac controls the core can run itself, with no permission prompt: volume (Standard Additions),
`open -a` for apps, `open URL` for websites, and "is this app running?" for verifying."""
import asyncio
import time
from pathlib import Path
from typing import Awaitable, Callable

Runner = Callable[..., Awaitable[tuple[int, str]]]

APP_DIRS = ["/Applications", "/Applications/Utilities", "/System/Applications",
            "/System/Applications/Utilities", str(Path.home() / "Applications")]


async def run_cmd(argv: list[str], timeout: float = 5.0) -> tuple[int, str]:
    p = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE,
                                             stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(p.communicate(), timeout)
    except TimeoutError:
        p.kill()
        return 124, "timed out"
    return p.returncode or 0, (out or err).decode(errors="replace")


def _quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


class System:
    def __init__(self, run: Runner = run_cmd):
        self._run = run

    async def _osa(self, script: str) -> tuple[bool, str]:
        code, out = await self._run(["/usr/bin/osascript", "-e", script])
        return code == 0, out.strip()

    async def get_volume(self) -> int | None:
        ok, out = await self._osa("output volume of (get volume settings)")
        return int(out) if ok and out.isdigit() else None

    async def set_volume(self, n: int) -> bool:
        return (await self._osa(f"set volume output volume {int(n)}"))[0]

    async def set_muted(self, on: bool) -> bool:
        return (await self._osa(f"set volume output muted {'true' if on else 'false'}"))[0]

    async def open_app(self, name: str) -> bool:
        return (await self._run(["/usr/bin/open", "-a", name]))[0] == 0

    async def app_running(self, name: str) -> bool:
        ok, out = await self._osa(f"application {_quote(name)} is running")
        return ok and out == "true"

    async def open_url(self, url: str) -> bool:
        return (await self._run(["/usr/bin/open", url]))[0] == 0


_apps_cache: tuple[float, list[str]] = (0.0, [])


def installed_apps(max_age_s: float = 600) -> list[str]:
    """Names of the apps on this Mac (Jev picks from these, so it can't invent one)."""
    global _apps_cache
    if time.monotonic() - _apps_cache[0] < max_age_s and _apps_cache[1]:
        return _apps_cache[1]
    names = sorted({p.stem for d in APP_DIRS if Path(d).is_dir() for p in Path(d).glob("*.app")
                    if not p.name.startswith(".")},
                   key=str.lower)
    _apps_cache = (time.monotonic(), names)
    return names
