"""Per-slot paragraph capacity.

Per the user's instruction "add the whole paragraphs", caps are effectively
unlimited. The renderer will insert as many paragraphs as needed to fit
all unmatched content into the empty slots.
"""
from __future__ import annotations

import os

# Effectively unlimited cap. The renderer will place all unmatched paragraphs
# into the available empty slots in priority order.
DEFAULT_SLOT_CAPACITY: int = int(os.environ.get("BRAIN_SLOT_CAPACITY", "10000"))

# Per-slot overrides. None = use DEFAULT_SLOT_CAPACITY.
SLOT_CAPACITY: dict[int, int] = {
    1:  10000,
    2:  10000,
    3:  10000,
    4:  10000,
    5:  10000,
    6:  10000,
    7:  10000,
    8:  10000,
    9:  10000,
    10: 10000,
    11: 10000,
    12: 10000,
    13: 10000,
    14: 10000,
}


def get_slot_capacity(slot_id: int) -> int:
    return SLOT_CAPACITY.get(slot_id, DEFAULT_SLOT_CAPACITY)
