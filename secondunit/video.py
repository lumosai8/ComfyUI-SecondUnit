"""
One video clip off the timeline, cut out of its source file with ffmpeg.

Mirrors `audio.grab_clip_audio`: park the playhead on a clip, press the
button, and that clip's picture (plus its sound, when it has any) lands in
ComfyUI's input folder as an mp4. The cut is re-encoded H.264/AAC rather
than stream-copied, so whatever the camera recorded plays in ComfyUI's own
video nodes.

Two deliberate limits:

1. One clip only — never a timeline render. Compositing every video track
   into one file would silently drop grades, titles and overlapping shots,
   while a straight cut is exactly what the playhead is sitting on.
2. `track` 0 means whichever enabled video track has something under the
   playhead, topmost first — the top clip is what the viewer shows.
"""

import os
import time

from . import ffmpeg as dg_ffmpeg
from . import resolve as R


def _ffmpeg():
    return dg_ffmpeg.find("ffmpeg")


def _ffprobe():
    return dg_ffmpeg.find("ffprobe")


def _has_stream(path, kind):
    """True if the file carries at least one stream of that kind."""
    try:
        r = dg_ffmpeg.run(
            [_ffprobe(), "-v", "error", "-select_streams",
             "v" if kind == "video" else "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", path],
            timeout=30,
        )
        return bool((r.stdout or b"").strip())
    except Exception:
        return False


def grab_clip_video(dest_dir, tag="clip", track=0, index=None):
    """
    ONE video clip off the timeline, rather than anything rendered.

    `track` 0 means whichever enabled video track has something under the
    playhead, topmost first. `index` picks a clip by position in the track
    instead of by the playhead.
    """
    info = R.get_timeline_info(include_items=True)
    if not info.get("success"):
        if info.get("connected") is False:
            return {"success": False, "error": "Not connected to DaVinci Resolve."}
        return {"success": False, "error": info.get("error") or "Could not read the timeline."}

    fps = float(info.get("fps") or 24)
    timeline_start = int(info.get("startFrame") or 0)
    playhead = int(info.get("playheadFrame") or timeline_start)
    tracks = info.get("tracks", {}).get("video", []) or []
    if not tracks:
        return {"success": False, "error": "This timeline has no video tracks."}

    wanted = [t for t in tracks if not track or int(t.get("index") or 0) == int(track)]
    if not wanted:
        return {"success": False, "error": "There is no video track V%d on this timeline." % track}

    item = None
    if index is None:
        # Topmost first: the upper clip is the one the viewer actually shows.
        # A disabled track is not what you are watching, so it is not what
        # we should hand you either.
        ordered = sorted(wanted, key=lambda t: int(t.get("index") or 0), reverse=True)
        for entry in ordered:
            if entry.get("enabled") is False:
                continue
            for candidate in entry.get("items") or []:
                if int(candidate.get("start", 0)) <= playhead < int(candidate.get("end", 0)):
                    item = candidate
                    break
            if item:
                break
        if not item:
            where = "track V%d" % track if track else "any enabled video track"
            return {"success": False, "error": "No video clip under the playhead on %s." % where}
    else:
        items = wanted[0].get("items") or []
        if not items:
            return {"success": False, "error": "That video track is empty."}
        if index < 0 or index >= len(items):
            return {
                "success": False,
                "error": "Track V%d has %d clips, so there is no clip %d."
                % (int(wanted[0].get("index") or 1), len(items), index),
            }
        item = items[index]

    path = item.get("path")
    if not path or not os.path.exists(path):
        return {"success": False, "error": "\u201c%s\u201d is offline \u2014 its file is not on disk." % item.get("name")}
    if not _has_stream(path, "video"):
        return {"success": False, "error": "\u201c%s\u201d has no video in it." % item.get("name")}

    duration = max(0, int(item["end"]) - int(item["start"])) / fps
    if duration <= 0:
        return {"success": False, "error": "That clip has no length."}

    # Where the clip starts inside its own file. Ask Resolve rather than
    # deriving it from GetLeftOffset, and convert with the SOURCE's rate —
    # the same two mistakes that made frame grabbing land on the wrong
    # picture.
    source_fps = _clip_source_fps(path, fps)
    source_start = item.get("sourceStartFrame")
    if source_start is None:
        source_start = int(item.get("leftOffset") or 0) * (source_fps / fps if fps else 1.0)
    src_in = max(0.0, float(source_start) / source_fps)

    ff = _ffmpeg()
    os.makedirs(dest_dir, exist_ok=True)
    stem = "%s_%d" % (_safe_tag(tag), int(time.time() * 1000))
    out_path = os.path.join(dest_dir, stem + ".mp4")

    command = [
        ff, "-hide_banner", "-loglevel", "error", "-y",
        "-ss", "%.4f" % src_in,
        "-t", "%.4f" % duration,
        "-i", path,
        "-map", "0:v:0",
    ]
    # The picture is the point; the sound comes along only when there is
    # one, so a silent drone shot still grabs.
    with_audio = _has_stream(path, "audio")
    if with_audio:
        command += ["-map", "0:a:0?"]
    command += [
        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
        "-pix_fmt", "yuv420p",
    ]
    if with_audio:
        command += ["-c:a", "aac", "-b:a", "160k"]
    command += ["-movflags", "+faststart", out_path]

    try:
        result = dg_ffmpeg.run(command, timeout=1800)
    except Exception as err:
        return {"success": False, "error": "Could not cut that clip's video: %s" % err}

    if result.returncode != 0 or not os.path.exists(out_path):
        detail = (result.stderr or b"").decode("utf-8", "replace").strip()[-200:]
        return {"success": False, "error": detail or "ffmpeg could not cut that clip's video."}

    return {
        "success": True,
        "ok": True,
        "path": out_path,
        "format": "mp4",
        "clip": item.get("name"),
        "track": int(item.get("track") or 1),
        "durationSeconds": round(duration, 3),
        "startSeconds": round(max(0, int(item["start"]) - timeline_start) / fps, 3),
        "skipped": 0,
    }


def _clip_source_fps(path, fallback):
    from . import frames as F

    try:
        return float(F._source_fps(path, fallback)) or fallback
    except Exception:
        return fallback


def _safe_tag(tag):
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(tag or "video"))
    return cleaned[:48] or "video"
