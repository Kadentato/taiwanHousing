"""Parse deal-quality flags from the free-text note (備註) / termination status.

These mark transactions that a market analyst would usually treat with caution or
exclude: non-arm's-length (related-party) sales, cancelled contracts, and units
with noted unpermitted additions (增建, which distort area/price).
"""

from __future__ import annotations

# Non-arm's-length markers (related-party / special relationship).
_RELATED = ("親友", "特殊關係", "二親等", "三親等", "關係人")
_CANCELLED = ("解約",)
_ADDITION = ("增建",)


def dealFlags(note, terminationStatus=None):
    """Return (relatedParty, cancelled, hasAddition) as 0/1 ints."""
    text = ("" if note is None else str(note)) + " " + ("" if terminationStatus is None else str(terminationStatus))
    related = int(any(k in text for k in _RELATED))
    cancelled = int(any(k in text for k in _CANCELLED))
    addition = int(any(k in text for k in _ADDITION))
    return related, cancelled, addition
