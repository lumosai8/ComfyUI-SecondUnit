"""
The last cut that was grabbed.

'Grab Cut from Resolve' knows exactly where the generated shot belongs — the gap
those two frames came from. Carrying that to the send node as a wire meant three
extra outputs cluttering a node whose job is to hand you two pictures, so it is
remembered here instead and offered as a placement option.

Deliberately tiny and in-process: it is a convenience for the common
grab-then-send flow, never a source of truth. Sending before grabbing simply
falls back to the playhead rather than guessing.
"""

_last_cut = None


def remember_cut(seconds, gap=None, label=None):
    global _last_cut
    _last_cut = {"seconds": float(seconds), "gap": gap, "label": label}


def last_cut():
    return dict(_last_cut) if _last_cut else None
