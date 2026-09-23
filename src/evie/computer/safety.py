"""Which computer steps can't be taken back: sending, posting, buying, deleting.

Two independent checks, so one mistake can't slip through: a plain-code rail on the words
(a "Send" button is risky whatever any model thinks) and the planner's own flag. A risky step is
read back out loud and waits 3 s for "stop" (Isaac's rule), and only Isaac's own voice or the
talk key can start it in the first place.
"""
import re

_RISKY = re.compile(r"\b(send|sent|post|publish|tweet|reply|comment|pay|buy|purchase|order|checkout|check out|"
                    r"place order|subscribe|delete|remove|trash|discard|erase|transfer|donate|confirm|submit|"
                    r"unsubscribe|leave group|block)\b", re.IGNORECASE)


def risky_words(text: str) -> bool:
    return bool(_RISKY.search(text or ""))


def is_risky(op: str, element: dict | None, text: str = "", flagged: bool = False) -> bool:
    if flagged:
        return True
    if op in ("press", "set_text") and element is not None and risky_words(element.get("label", "")):
        return True
    # Enter in a message box sends it.
    if op == "set_text" and element is not None and text and \
            re.search(r"message|chat|reply|comment|tweet|post", element.get("label", ""), re.IGNORECASE):
        return True
    return False
