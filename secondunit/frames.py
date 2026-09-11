"""
Grabbing still frames out of a DaVinci Resolve timeline.

Three ways to get a frame, tried in order:

1. **Gallery** — `timeline.GrabStill()` then `album.ExportStills(...)`. Highest
   fidelity: it is exactly what the viewer shows, grade included. Runs inside
   the Resolve helper (it needs live handles); see `resolve_helper.py`.
2. **Fusion render** — a single-frame comp render. Also in the helper.
3. **ffmpeg** — read the frame straight out of the clip's source file, here in
   ComfyUI's process from the item metadata the helper returns alongside its
   failure. Ignores the grade, but works even when Resolve is uncooperative.
"""

import os
import time

from . import ffmpeg as dg_ffmpeg
from . import resolve as R

EXPORT_PREFIX = "dg_grab_"


# --------------------------------------------------------------------------- #
# Public entry points (signatures unchanged — routes.py calls these)
# --------------------------------------------------------------------------- #

def grab_playhead(dest_dir, tag="frame"):
    """A still wherever the playhead is sitting."""
    os.makedirs(dest_dir, exist_ok=True)
    try:
        native = R.grab_playhead_native(dest_dir, tag=tag)
    except Exception as err:
        return {"success": False, "error": "Not connected to DaVinci Resolve: %s" % err}
    if native.get("success"):
        return _normalise(native)
    if native.get("connected") is False and not native.get("item"):
        return {"success": False,
                "error": native.get("error") or "Not connected to DaVinci Resolve."}
    return _with_ffmpeg_fallback(native, dest_dir, tag)


def grab_edge(track_type, track, item_index, edge, dest_dir, tag="frame"):
    """
    A still at the first or last frame of one specific clip.

    `last` deliberately targets `end - 1`: Resolve's end frame is exclusive, so
    landing on it would grab the first frame of whatever comes next.
    """
    os.makedirs(dest_dir, exist_ok=True)
    try:
        native = R.grab_edge_native(track_type, track, item_index, edge, dest_dir, tag=tag)
    except Exception as err:
        return {"success": False, "error": "Not connected to DaVinci Resolve: %s" % err}
    if native.get("success"):
        return _normalise(native)
    if native.get("connected") is False and not native.get("item"):
        return {"success": False,
                "error": native.get("error") or "Not connected to DaVinci Resolve."}
    # Track-level errors (empty track, bad index) carry no item to fall back to.
    if not native.get("item"):
        return {"success": False, "error": native.get("error") or "Could not grab that frame."}
    result = _with_ffmpeg_fallback(native, dest_dir, tag)
    if result.get("success") and native.get("clip"):
        result["clip"] = native["clip"]
        result["edge"] = native.get("edge", edge)
    return result


def _normalise(native):
    result = {
        "success": True,
        "ok": True,
        "path": native["path"],
        "method": native.get("method", "gallery"),
        "frame": native.get("frame"),
        "timecode": native.get("timecode"),
        "fps": native.get("fps"),
        "timeline": native.get("timeline", ""),
    }
    if native.get("fell_back_from"):
        result["fell_back_from"] = native["fell_back_from"]
    if native.get("clip"):
        result["clip"] = native["clip"]
    if native.get("edge"):
        result["edge"] = native["edge"]
    return result


def _with_ffmpeg_fallback(native, dest_dir, tag):
    item = native.get("item") or {}
    frame = native.get("frame")
    fps = native.get("fps") or 24.0
    if not item or not item.get("path") or frame is None:
        return {"success": False,
                "error": native.get("error") or "Could not grab that frame."}
    try:
        produced = _grab_ffmpeg(item, frame, fps, dest_dir, tag)
    except Exception as err:
        tried = native.get("fell_back_from") or native.get("error") or ""
        return {"success": False,
                "error": "Could not grab that frame. Tried: %s; ffmpeg: %s"
                         % (tried, err)}
    result = {
        "success": True,
        "ok": True,
        "path": produced,
        "method": "ffmpeg",
        "frame": frame,
        "timecode": R.frames_to_timecode(frame, fps),
        "fps": fps,
        "timeline": native.get("timeline", ""),
    }
    if native.get("fell_back_from"):
        result["fell_back_from"] = native["fell_back_from"]
    return result


# --------------------------------------------------------------------------- #
# ffmpeg on the source media
#
# NOTE: this reads the ORIGINAL file, so it sees no colour grade, no titles, no
# compound clips and no transforms — "what the camera recorded", not "what the
# viewer shows". The gallery tier above is the one that captures the graded
# composite; when Resolve refuses it that fidelity is simply lost.
# --------------------------------------------------------------------------- #

def _grab_ffmpeg(item, frame, fps, dest_dir, tag):
    source = item.get("path") or ""
    if not source or not os.path.exists(source):
        raise RuntimeError("the clip's media file is offline")

    try:
        timeline_start = int(item.get("start", 0))
    except (TypeError, ValueError):
        timeline_start = 0
    # Where this clip starts inside its source file. ASK Resolve (the helper
    # already did); do not derive it from leftOffset.
    #
    # leftOffset counts TIMELINE frames, and whether that equals source frames
    # depends on how Resolve conformed the media. A 23.976 clip on a 24 fps
    # timeline plays 1:1, so the two agree; a 30 fps clip on the same timeline
    # is conformed by TIME, so they do not.
    source_start = item.get("sourceStartFrame")
    offset_in_clip = max(0, int(frame) - timeline_start)
    source_fps = _source_fps(source, fps)

    if source_start is None:
        # Older API: fall back to the old assumption and hope the rates match.
        try:
            source_start = int(item.get("leftOffset") or 0)
        except (TypeError, ValueError):
            source_start = 0

    # One timeline frame is not one source frame when the rates differ.
    ratio = (source_fps / float(fps)) if fps else 1.0
    source_frame = max(0, int(source_start) + int(round(offset_in_clip * ratio)))

    # Seconds into the SOURCE, at the SOURCE's own frame rate. Seek half a
    # frame EARLY: ffmpeg returns the first frame whose timestamp is >= the
    # seek point, so asking for exactly frame_n/fps can tip into frame n+1 on
    # any floating-point wobble — a whole different shot on a cut.
    seconds = max(0.0, (source_frame - 0.5) / source_fps)

    out_path = os.path.join(dest_dir, "%sffmpeg_%d.png" % (EXPORT_PREFIX, int(time.time() * 1000)))

    command = [
        _ffmpeg(),
        "-hide_banner",
        "-loglevel", "error",
        "-accurate_seek",
        "-ss", "%.5f" % seconds,
        "-i", source,
        "-frames:v", "1",
        "-y",
        out_path,
    ]

    result = dg_ffmpeg.run(command, timeout=60)
    if result.returncode != 0 or not os.path.exists(out_path):
        detail = (result.stderr or b"").decode("utf-8", "replace").strip()[:200]
        raise RuntimeError(detail or "ffmpeg failed")
    return out_path


def _source_fps(path, fallback):
    """
    The media file's own frame rate, read from the file itself.

    Never assume it matches the timeline. A 23.976 fps film dropped on a 24 fps
    timeline keeps its own numbering; using the timeline rate to turn a source
    frame into seconds is wrong by a factor of 1.001 — invisible near the head
    of a clip, enormous deep into a feature.
    """
    try:
        result = dg_ffmpeg.run(
            [
                dg_ffmpeg.find("ffprobe"),
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=r_frame_rate,avg_frame_rate",
                "-of", "default=noprint_wrappers=1",
                path,
            ],
            timeout=30,
        )
        found = {}
        for line in (result.stdout or b"").decode("utf-8", "replace").splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                found[key.strip()] = value.strip()

        for key in ("r_frame_rate", "avg_frame_rate"):
            rate = _parse_rate(found.get(key))
            if rate:
                return rate
    except Exception:
        pass

    return float(fallback or 24)


def _parse_rate(value):
    """"24000/1001" or "23.976" -> a float, or None when it says nothing."""
    if not value or value in ("0/0", "N/A"):
        return None
    try:
        if "/" in value:
            numerator, _, denominator = value.partition("/")
            denominator = float(denominator)
            return float(numerator) / denominator if denominator else None
        rate = float(value)
        return rate if rate > 0 else None
    except (TypeError, ValueError):
        return None


def _ffmpeg():
    return dg_ffmpeg.find("ffmpeg")


def _safe_tag(tag):
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(tag or "frame"))
    return cleaned[:48] or "frame"
