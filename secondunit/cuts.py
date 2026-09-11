"""
Finding the cuts in a Resolve timeline.

A "cut" is any handover between two adjacent clips on the same video track,
including one across a gap of black. The gap matters: Resolve's API can only
place a clip into FREE space, so the gap at a cut is both where the generated
shot belongs and the only place it can actually land.

Ported from the app's cut picker, which was the one screen this pack replaces
with a node.
"""


def find_cuts(timeline_info):
    """
    Every join between adjacent clips, newest-first per track.

    `timeline_info` is what resolve.get_timeline_info() returns.
    Each cut carries the two clips, their indices (needed to grab their edge
    frames) and the gap between them in frames — 0 or 1 is a butt cut, a
    negative gap means the clips overlap.
    """
    fps = float(timeline_info.get("fps") or 24.0)
    start_frame = int(timeline_info.get("startFrame") or 0)
    tracks = (timeline_info.get("tracks") or {}).get("video") or []

    cuts = []
    for track in tracks:
        items = track.get("items") or []
        for index in range(len(items) - 1):
            before = items[index]
            after = items[index + 1]
            gap = int(after["start"]) - int(before["end"])
            cuts.append(
                {
                    "track": track.get("index"),
                    "before": before,
                    "after": after,
                    "beforeIndex": index,
                    "afterIndex": index + 1,
                    "gap": gap,
                    "frame": int(after["start"]),
                    # Where a generated clip should land: the first frame of the
                    # gap, in seconds from the start of the timeline. A timeline
                    # can start at 01:00:00:00, hence subtracting startFrame.
                    "placeStartSeconds": (int(before["end"]) - start_frame) / fps,
                }
            )
    return cuts


def nearest_to(cuts, frame):
    """The cut closest to a frame — used to default to the playhead."""
    if not cuts:
        return None
    return min(cuts, key=lambda cut: abs(int(cut["frame"]) - int(frame)))


def describe(cut, fps=24.0):
    """A one-line label, the same wording the app used."""
    gap = int(cut["gap"])
    if gap > 1:
        detail = "%.1fs gap" % (gap / float(fps or 24.0))
    elif gap < 0:
        detail = "overlaps %df" % abs(gap)
    else:
        detail = "butt cut"
    return "%s -> %s  (%s)" % (
        cut["before"].get("name", "?"),
        cut["after"].get("name", "?"),
        detail,
    )
