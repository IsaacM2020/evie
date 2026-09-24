"""Finding music: Spotify's Web API with client credentials (the app's own key, no Isaac login
and no Premium needed). It only finds the right link; the Evie app then tells the Spotify app
to play it, through AppleScript."""
import logging
import re
import time

import httpx

log = logging.getLogger("evie.music")

TOKEN_URL = "https://accounts.spotify.com/api/token"
SEARCH_URL = "https://api.spotify.com/v1/search"
KINDS = ("track", "artist", "album", "playlist")


def _bare(name: str) -> str:
    """"Trance (with Travis Scott & Young Thug)" / "Trance - Remastered" -> "trance"."""
    name = re.split(r"\s[-–]\s|\s*[(\[]", name.lower(), maxsplit=1)[0]
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", name).split())


def _label(it: dict, kind: str) -> str:
    artist = (it.get("artists") or [{}])[0].get("name")
    return f"{it['name']} by {artist}" if kind in ("track", "album") and artist else it["name"]


class SpotifySearch:
    def __init__(self, client_id: str, secret: str, http: httpx.AsyncClient | None = None, clock=time.monotonic):
        self._id, self._secret, self._clock = client_id, secret, clock
        self._http = http or httpx.AsyncClient(timeout=4.0)
        self._token: str | None = None
        self._expires = 0.0

    async def _get_token(self) -> str:
        if self._token and self._clock() < self._expires - 60:
            return self._token
        r = await self._http.post(TOKEN_URL, data={"grant_type": "client_credentials"}, auth=(self._id, self._secret))
        r.raise_for_status()
        d = r.json()
        self._token, self._expires = d["access_token"], self._clock() + float(d.get("expires_in", 3600))
        return self._token

    async def find(self, query: str, kind: str = "track", title: str | None = None,
                   artist: str | None = None) -> tuple[str, str] | None:
        """(spotify uri, spoken label) for the best match, or None."""
        ranked = await self.ranked(query, kind, title, artist)
        return ranked[0] if ranked else None

    async def ranked(self, query: str, kind: str = "track", title: str | None = None,
                     artist: str | None = None) -> list[tuple[str, str]]:
        """Best first, so "no, the other one" can play the next. title: the song's name alone
        ("Trance"): among songs actually called that, the most popular wins (the one people mean).
        artist: if he named one it must match; a title by that artist that isn't a song is tried as
        an album ("utopia by travis scott")."""
        if not (self._id and self._secret and query.strip()):
            return []
        kind = kind if kind in KINDS else "track"
        if kind == "artist":  # "play trance": no artist is called that, so it's the song people mean
            found = await self._items(query, "artist", 3)
            if found and any(_bare(a.get("name", "")) == _bare(query) for a in found):
                return [(a["uri"], a["name"]) for a in found if _bare(a.get("name", "")) == _bare(query)][:1] + \
                    [(a["uri"], a["name"]) for a in found if _bare(a.get("name", "")) != _bare(query)]
            kind, title = "track", title or query
        items = await self._items(query, kind, 10 if (title or artist) else 3)
        if items is None:
            return []
        by = (lambda i: artist is None or any(_bare(artist) in _bare(a.get("name", "")) for a in i.get("artists") or []))
        named = [i for i in items if title and _bare(i.get("name", "")) == _bare(title) and by(i)]
        if kind == "track" and named:
            best = sorted(named, key=lambda i: -(i.get("popularity") or 0))
        elif kind == "track" and title and artist:
            albums = await self._items(f"{title} {artist}", "album", 5) or []
            best = [a for a in albums if _bare(a.get("name", "")) == _bare(title) and by(a)]
            kind = "album" if best else kind
            best = best or [i for i in items if by(i)]
        else:
            best = []
        rest = [i for i in items if i not in best]
        out, seen = [], set()
        for i in (best + rest if kind == "track" else best or items):
            label = _label(i, kind)
            if label.lower() not in seen:  # the single and the album copy are the same song to him
                seen.add(label.lower())
                out.append((i["uri"], label))
        return out[:5]

    async def _items(self, query: str, kind: str, limit: int) -> list[dict] | None:
        try:
            token = await self._get_token()
            r = await self._http.get(SEARCH_URL, params={"q": query, "type": kind, "limit": limit, "market": "SG"},
                                     headers={"Authorization": f"Bearer {token}"})
            r.raise_for_status()
            return [i for i in r.json()[f"{kind}s"]["items"] if i]
        except (httpx.HTTPError, KeyError, ValueError) as e:
            log.warning("spotify search failed: %r", e)
            return None

    async def aclose(self) -> None:
        await self._http.aclose()
