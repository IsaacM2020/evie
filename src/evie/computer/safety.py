"""Which computer steps can't be taken back: sending, posting, buying, deleting.

Two independent checks, so one mistake can't slip through: a plain-code rail on the words
(a "Send" button is risky whatever any model thinks) and the planner's own flag. A risky step is
read back out loud and waits 3 s for "stop" (Isaac's rule), and only Isaac's own voice or the
talk key can start it in the first place.
"""
import re
from enum import Enum

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


class EffectClass(Enum):
    """Spec §18 (P2-G): one policy by effect, instead of scattered per-check booleans (is_risky
    here, _is_credential_field in planner.py, guard_bash's rm/sudo/force-push list in jobs.py).
    Ordered roughly least to most consequential; a not-yet-built objective.py can use this to
    decide autonomous-vs-confirm-vs-refuse policy from one categorized signal rather than
    re-deriving it. is_risky/risky_words stay exactly as they are -- nothing calling them
    changes; classify() is additive, read by new code, not a replacement for the existing gates."""
    READ = "read"                # looking, never changes anything
    REVERSIBLE = "reversible"     # an ordinary action Evie can undo or redo
    SEND = "send"                 # a message, post or reply that reaches someone else
    DESTRUCTIVE = "destructive"   # deletes or otherwise can't be easily undone
    FINANCIAL = "financial"       # spends money or commits to a purchase
    CREDENTIAL = "credential"     # a password/login field -- never typed into, full stop


_SEND_WORDS = re.compile(r"\b(send|sent|post|publish|tweet|reply|comment|submit|unsubscribe|leave group|block)\b",
                        re.IGNORECASE)
_DESTRUCTIVE_WORDS = re.compile(r"\b(delete|remove|trash|discard|erase)\b", re.IGNORECASE)
_FINANCIAL_WORDS = re.compile(r"\b(pay|buy|purchase|order|checkout|check out|place order|transfer|donate)\b",
                              re.IGNORECASE)
_MESSAGE_FIELD = re.compile(r"message|chat|reply|comment|tweet|post", re.IGNORECASE)
_CREDENTIAL_LABEL = re.compile(r"\b(password|passcode|passphrase|pin code|security code|log ?in|sign ?in|"
                               r"username|user ?name)\b", re.IGNORECASE)


def classify(op: str, element: dict | None, text: str = "", flagged: bool = False,
            screen_has_password: bool = False) -> EffectClass:
    """The effect category of one step, before it runs. screen_has_password: True when ANY
    element on the current screen is a real password field (matches planner.py's
    _is_login_screen rule) -- an email field on a login screen is CREDENTIAL even though its own
    label says nothing about passwords."""
    label = (element or {}).get("label", "")
    role = (element or {}).get("role", "")
    if op == "set_text" and (screen_has_password or "password" in role.lower() or _CREDENTIAL_LABEL.search(label)):
        return EffectClass.CREDENTIAL
    if op in ("press", "set_text") and _FINANCIAL_WORDS.search(label):
        return EffectClass.FINANCIAL
    if op in ("press", "set_text") and _DESTRUCTIVE_WORDS.search(label):
        return EffectClass.DESTRUCTIVE
    if op in ("press", "set_text") and _SEND_WORDS.search(label):
        return EffectClass.SEND
    if op == "set_text" and text and _MESSAGE_FIELD.search(label):
        return EffectClass.SEND
    if flagged:
        return EffectClass.DESTRUCTIVE  # the planner flagged it risky but gave no matching keyword: treat as confirmable
    if op == "set_text":
        return EffectClass.REVERSIBLE
    return EffectClass.READ
