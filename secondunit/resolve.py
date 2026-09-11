"""
DaVinci Resolve API for the Second Unit bridge.

Everything Resolve-related runs in Resolve's own scripting interpreter
(`fuscript -l py3`, via `secondunit.bridge`) — never in ComfyUI's Python.
Resolve's client library crashes natively inside ComfyUI portable's Python
3.13, where no try/except can catch it, so this module must never import
`DaVinciResolveScript` itself. The pure helpers (timecodes) stay local;
every live-handle call is a JSON round-trip through the bridge.

Two rules learned the hard way, applied throughout:

1. Never cache `project`, `media_pool` or `timeline` across calls. Resolve hands
   out handles that go stale the moment the user switches project, page or
   timeline, and a stale handle fails silently rather than raising. Each
   bridge call connects fresh, inside the helper.
2. `recordFrame` is an ABSOLUTE frame number. A timeline starting at 01:00:00:00
   has a start frame of 86400 at 24fps, and that offset must be included or the
   clip lands an hour away from where the user asked for it.
"""

from . import bridge as _bridge

BIN_NAME = "Second Unit"


# --------------------------------------------------------------------------- #
# Connection
# --------------------------------------------------------------------------- #

def connect(force=False):
    """
    True when Resolve answers, None when it does not.

    Kept for compatibility: it used to hand back a live Resolve handle, which
    cannot cross the bridge — callers only ever need to know reachability, and
    the real work happens per-call inside the helper.
    """
    del force
    status = _bridge.call("status", {}, timeout=60)
    return True if status.get("connected") else None


class Context:
    """Reachability snapshot for one request. Live handles stay in the helper."""

    def __init__(self):
        status = _bridge.call("status", {}, timeout=60)
        self.connected = bool(status.get("connected"))
        self.error = status.get("error") or ""
        # No live objects cross the bridge; these stay None so any leftover
        # direct use fails loudly instead of silently.
        self.resolve = None
        self.project_manager = None
        self.project = None
        self.media_pool = None
        self.timeline = None

    def fps(self):
        """Fallback rate only — callers with a timeline use info['fps']."""
        return 24.0


# --------------------------------------------------------------------------- #
# Timecode (pure — no Resolve needed)
# --------------------------------------------------------------------------- #

def timecode_to_frames(timecode, fps):
    """'01:00:02:12' -> absolute frame number. Handles ';' drop-frame separators."""
    if not timecode:
        return 0
    parts = str(timecode).replace(";", ":").split(":")
    if len(parts) != 4:
        return 0
    try:
        hours, minutes, seconds, frames = (int(p) for p in parts)
    except ValueError:
        return 0
    nominal = max(1, int(round(fps)))
    return int((hours * 3600 + minutes * 60 + seconds) * nominal + frames)


def frames_to_timecode(frame, fps):
    """Absolute frame number -> '01:00:02:12'."""
    nominal = max(1, int(round(fps)))
    frame = max(0, int(frame))
    frames = frame % nominal
    total_seconds = frame // nominal
    seconds = total_seconds % 60
    minutes = (total_seconds // 60) % 60
    hours = total_seconds // 3600
    return "%02d:%02d:%02d:%02d" % (hours, minutes, seconds, frames)


# --------------------------------------------------------------------------- #
# Status / timeline / media pool — one bridge round-trip each
# --------------------------------------------------------------------------- #

def get_status(bridge_version=""):
    result = _bridge.call("status", {"bridge_version": bridge_version}, timeout=60)
    if not result.get("connected"):
        return {"connected": False}
    return result


def get_timeline_info(include_items=True):
    """Everything a client needs to reason about the current timeline."""
    return _bridge.call("timeline_info", {"include_items": bool(include_items)}, timeout=60)


def import_media(file_path):
    return _bridge.call("import_media", {"file_path": file_path}, timeout=120)


def add_to_timeline(file_path, start_seconds=None, track_name=None):
    payload = {"file_path": file_path}
    if start_seconds is not None:
        payload["start_seconds"] = float(start_seconds)
    if track_name:
        payload["track_name"] = track_name
    return _bridge.call("add_to_timeline", payload, timeout=180)


# --------------------------------------------------------------------------- #
# Native frame grabs (gallery + fusion inside the helper; the ffmpeg fallback
# in frames.py uses the failure context these return, no second round-trip)
# --------------------------------------------------------------------------- #

def grab_playhead_native(dest_dir, tag="frame"):
    return _bridge.call("grab_playhead", {"dest_dir": dest_dir, "tag": tag}, timeout=180)


def grab_edge_native(track_type, track, item_index, edge, dest_dir, tag="frame"):
    return _bridge.call("grab_edge", {
        "track_type": track_type or "video",
        "track": int(track or 1),
        "item_index": int(item_index or 0),
        "edge": edge,
        "dest_dir": dest_dir,
        "tag": tag,
    }, timeout=180)
