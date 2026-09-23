"""Finding music: Spotify's Web API with client credentials (the app's own key, no Isaac login
and no Premium needed). It only finds the right link; the Evie app then tells the Spotify app
to play it, through AppleScript."""
import logging
import time

import httpx

log = logging.getLogger("evie.music")

TOKEN_URL = "https://accounts.spotify.com/api/token"
SEARCH_URL = "https://api.spotify.com/v1/search"
KINDS = ("track", "artist", "album", "playlist")


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

    async def find(self, query: str, kind: str = "track") -> tuple[str, str] | None:
        """(spotify uri, spoken label) for the best match, or None."""
        if not (self._id and self._secret and query.strip()):
            return None
        kind = kind if kind in KINDS else "track"
        try:
            token = await self._get_token()
            r = await self._http.get(SEARCH_URL, params={"q": query, "type": kind, "limit": 3, "market": "SG"},
                                     headers={"Authorization": f"Bearer {token}"})
            r.raise_for_status()
            items = [i for i in r.json()[f"{kind}s"]["items"] if i]
        except (httpx.HTTPError, KeyError, ValueError) as e:
            log.warning("spotify search failed: %r", e)
            return None
        if not items:
            return None
        it = items[0]
        artist = (it.get("artists") or [{}])[0].get("name")
        label = f"{it['name']} by {artist}" if kind in ("track", "album") and artist else it["name"]
        return it["uri"], label

    async def aclose(self) -> None:
        await self._http.aclose()
