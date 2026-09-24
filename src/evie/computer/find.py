"""Finding the element a step means, in plain code first (no model call, ~0 ms).

The planner describes WHAT to press ("the Videos tab", "the search box"). Code scores every element
on the screen it just read: word overlap with the label, then closeness of the whole label,
filtered by role / link pattern / typeable. One clear winner = done. Otherwise the top candidates
(real ids only) go to Jev as a choice. `pick_pool` gathers the rows a "pick" chooses among
(videos, articles, channels, results) in page order.
"""
import re
from difflib import SequenceMatcher

from evie.computer.observe import Screen

CLEAR_WIN = 0.9  # a winner this good ...
CLEAR_GAP = 0.2  # ... and this far ahead of the next is used without asking Jev
_FILLER = {"the", "a", "an", "button", "link", "tab", "box", "field", "on", "in", "of", "to", "my", "this", "that",
           "click", "press", "open", "go", "page"}


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in _FILLER}


def score(el: dict, what: str) -> float:
    want, label = _words(what), (el.get("label") or "")
    have = _words(label + " " + (el.get("meta") or ""))
    if not label:
        return 0.0
    overlap = len(want & have) / len(want) if want else 0.0
    close = SequenceMatcher(None, " ".join(sorted(want)), " ".join(sorted(_words(label)))).ratio() if want else 0.0
    lw = _words(label)
    exact = 1.0 if want and want == lw else 0.0
    # The whole label is named in the request ("NetworkChuck channel" -> the "NetworkChuck" link);
    # the leftover words just describe it.
    named = 0.92 if lw and lw <= want else 0.0
    return round(max(exact, named, 0.65 * overlap + 0.35 * close), 3)


def _fits(el: dict, role: str | None, href: str | None, typeable: bool | None) -> bool:
    if role and role not in (el.get("role") or ""):
        return False
    if href and href not in (el.get("href") or ""):
        return False
    if typeable is not None and bool(el.get("typeable")) != typeable:
        return False
    return el.get("enabled") is not False


def candidates(screen: Screen, what: str, role: str | None = None, href: str | None = None,
               typeable: bool | None = None, limit: int = 12) -> list[dict]:
    els = [e for e in screen.elements if _fits(e, role, href, typeable)]
    ranked = sorted(els, key=lambda e: score(e, what), reverse=True)
    return ranked[:limit]


def find_in_code(screen: Screen, what: str, role: str | None = None, href: str | None = None,
                 typeable: bool | None = None) -> tuple[dict | None, list[dict]]:
    """(the element, if one clearly wins; the ranked candidates for Jev otherwise)."""
    cands = candidates(screen, what, role, href, typeable)
    if typeable and len(cands) >= 1:
        # "the search box": when only one field fits, that's it, whatever its label says.
        fields = [c for c in cands if c.get("typeable")]
        if len(fields) == 1:
            return fields[0], cands
    if not cands:
        return None, []
    top = score(cands[0], what)
    nxt = score(cands[1], what) if len(cands) > 1 else 0.0
    if top >= CLEAR_WIN and top - nxt >= CLEAR_GAP:
        return cands[0], cands
    if (role or href) and len(cands) == 1:
        return cands[0], cands
    return None, cands


_POOLS = {
    "videos": lambda e: "/watch?v=" in (e.get("href") or "") and not (e.get("label") or "").lower().startswith(("shorts", "ad ")),
    "channels": lambda e: "/@" in (e.get("href") or "") and "/watch" not in (e.get("href") or ""),
    "articles": lambda e: e.get("role") == "link" and len((e.get("label") or "").split()) >= 5
                          and (e.get("region") or "main") == "main",
    "results": lambda e: e.get("role") == "link" and (e.get("region") or "main") == "main" and len(e.get("label") or "") > 12,
}


def pick_pool(screen: Screen, among: str) -> list[dict]:
    """The rows a pick chooses from, in page order (a channel's Videos tab is newest first)."""
    key = next((k for k in _POOLS if k in among.lower() or among.lower().rstrip("s") + "s" == k), None)
    test = _POOLS.get(key or "", lambda e: e.get("role") in ("link", "button") and bool(e.get("label")))
    pool, seen = [], set()
    for e in screen.elements:
        if not test(e) or not e.get("label") or e.get("label") in ("thumbnail",):
            continue
        k = e.get("href") or e["id"]
        if k in seen:
            continue
        seen.add(k)
        pool.append(e)
    return pool[:20]
