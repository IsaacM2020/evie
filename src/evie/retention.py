"""Phase 6 P2: Memory V2 — not a rewrite of the existing stores (they already work and are
tested), but the policy that's shared across all of them.

The five kinds of memory this system already has, one file each:
  semantic    evie.facts.FactStore          things Isaac told her to remember
  episodic    evie.memory.Conversation      today's turns with him, summarised past 12
  procedural  evie.procedures.ProcedureStore  screen-task steps that have proven themselves
  project     evie.goals.GoalStore          outcomes, milestones, next actions
  preference  evie.preferences.Preferences  small settings that should survive a restart

This module holds what all five need in common: pruning by age, a growth cap, and the one rule
that must never be worked around — an utterance Evie ignored (not addressed to her, not acted
on) never gets written down as text anywhere persistent. That's not a new mechanism bolted on
top; every store above only ever writes what a caller explicitly extracted or was told, never a
raw utterance, and evie.world_model's event handling only copies named fields off each event
(never the event's own text) into anything that survives a restart. safe_to_store() names that
rule so it can be checked and tested in one place instead of re-derived per file.
"""
import time
from typing import Callable, TypeVar

T = TypeVar("T")


def safe_to_store(text: str, addressed: bool, ignored: bool = False) -> bool:
    """Isaac's own words, in his own voice, aimed at Evie, are fine to keep. Anything ignored or
    only overheard is not — a source may extract a fact or a plan out of it (already just a few
    words, not the sentence), but the raw utterance itself stops here."""
    return bool(text) and addressed and not ignored


def prune_by_age(items: list[T], age_key: Callable[[T], float | None], max_age_s: float,
                 now: Callable[[], float] = time.time) -> list[T]:
    """Drop items older than max_age_s. An item with no timestamp (age_key returns None) is kept:
    pruning is for stale entries, not for guessing about ones that were never dated."""
    cutoff = now() - max_age_s
    return [it for it in items if (age_key(it) is None or age_key(it) >= cutoff)]


def cap_count(items: list[T], max_n: int, sort_key: Callable[[T], float]) -> list[T]:
    """A growth cap: keep at most max_n, the ones with the highest sort_key (usually recency)."""
    if len(items) <= max_n:
        return items
    return sorted(items, key=sort_key, reverse=True)[:max_n]
