"""
Mixing the current timeline's audio down to a single file.

This backs the "transcribe the whole timeline" use of the Transcribe app: the
user has a recording they can upload, or they can pull the audio straight off
the timeline that is open in Resolve.

We deliberately do NOT use Resolve's Deliver render queue. On this Resolve build
an externally-connected bridge (via RESOLVE_SCRIPT_LIB) can set render settings
but `AddRenderJob` returns nothing and Resolve refuses the render path — the
queue is only dependable from Resolve's own Scripts menu. Instead we mix the
audio with ffmpeg: each timeline audio clip is cut to its in/out range, delayed
to its timeline position, and mixed into one file. This is reliable and pops no
dialogs.

Two robustness rules:

1. Clips without a readable file or an audio stream are skipped (counted), not
   fatal — an offline clip must not sink the whole mix.
2. `adelay` uses `all=1` (ffmpeg >= 4.4) so any channel layout is delayed, not
   just stereo.
"""

import os
import time

from . import ffmpeg as dg_ffmpeg
from . import resolve as R


def _ffmpeg():
    return dg_ffmpeg.find("ffmpeg")


def _ffprobe():
    return dg_ffmpeg.find("ffprobe")


def _has_audio_stream(path):
    """True if the file carries at least one audio stream."""
    try:
        r = dg_ffmpeg.run(
            [_ffprobe(), "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", path],
            timeout=30,
        )
        return bool((r.stdout or b"").strip())
    except Exception:
        return False


def grab_timeline_audio(dest_dir, tag="audio"):
    """Mix the open timeline's audio to a single MP3 in `dest_dir`."""
    info = R.get_timeline_info(include_items=True)
    if not info.get("success"):
        if info.get("connected") is False:
            return {"success": False, "error": "Not connected to DaVinci Resolve."}
        return {"success": False, "error": info.get("error") or "Could not read the timeline."}

    fps = float(info.get("fps") or 24)
    start_frame = int(info.get("startFrame") or 0)

    items = []
    for track in info.get("tracks", {}).get("audio", []):
        items.extend(track.get("items", []) or [])
    real = [it for it in items if it.get("path")]
    if not real:
        return {"success": False, "error": "No audio clips with a readable file were found on the timeline."}

    ff = _ffmpeg()
    os.makedirs(dest_dir, exist_ok=True)
    stem = "%s_%d" % (_safe_tag(tag), int(time.time() * 1000))
    out_path = os.path.join(dest_dir, stem + ".mp3")

    inputs = []
    filters = []
    mix_inputs = []
    skipped = 0
    n = 0

    for it in real:
        path = it.get("path")
        if not path or not os.path.exists(path):
            skipped += 1
            continue
        if not _has_audio_stream(path):
            skipped += 1
            continue

        try:
            start_sec = (int(it["start"]) - start_frame) / fps
            dur_sec = (int(it["end"]) - int(it["start"])) / fps
            src_in = int(it.get("leftOffset") or 0) / fps
        except Exception:
            skipped += 1
            continue
        if dur_sec <= 0:
            skipped += 1
            continue

        inputs += ["-ss", "%.4f" % src_in, "-t", "%.4f" % dur_sec, "-i", path]
        ms = int(round(max(0.0, start_sec) * 1000))
        filters.append("[%d:a]adelay=%d:all=1[a%d]" % (n, ms, n))
        mix_inputs.append("[a%d]" % n)
        n += 1

    if n == 0:
        return {
            "success": False,
            "error": "No audio clips on the timeline could be mixed "
                     "(offline or without an audio stream).",
        }

    filters.append("".join(mix_inputs) + "amix=inputs=%d:duration=longest:normalize=0[mix]" % n)
    command = (
        [ff, "-hide_banner", "-loglevel", "error", "-y"]
        + inputs
        + ["-filter_complex", ";".join(filters), "-map", "[mix]",
           "-c:a", "libmp3lame", "-q:a", "4", out_path]
    )

    try:
        result = dg_ffmpeg.run(command, timeout=1800)
    except Exception as err:
        return {"success": False, "error": "The audio mix failed: %s" % err}

    if result.returncode != 0 or not os.path.exists(out_path):
        detail = (result.stderr or b"").decode("utf-8", "replace").strip()[-200:]
        return {"success": False, "error": detail or "ffmpeg could not mix the timeline audio."}

    duration_sec = None
    try:
        duration_sec = max(0, int(info.get("endFrame") or start_frame) - start_frame) / fps
    except Exception:
        duration_sec = None

    return {
        "success": True,
        "ok": True,
        "path": out_path,
        "format": "mp3",
        "timeline": info.get("name", ""),
        "durationSeconds": round(duration_sec, 3) if duration_sec is not None else None,
        "skipped": skipped,
    }


def grab_clip_audio(dest_dir, tag="clip", track=0, index=None):
    """
    ONE audio clip off the timeline, rather than the whole thing mixed down.

    Mixing every track together is right for "transcribe this edit" and wrong for
    almost everything else: feed a model the mix and you have handed it music,
    room tone and dialogue at once. Usually you want the clip you are parked on.

    `track` 0 means whichever enabled audio track has something under the
    playhead, lowest first — A1 is where dialogue lives. `index` picks a clip by
    position in the track instead of by the playhead.
    """
    info = R.get_timeline_info(include_items=True)
    if not info.get("success"):
        if info.get("connected") is False:
            return {"success": False, "error": "Not connected to DaVinci Resolve."}
        return {"success": False, "error": info.get("error") or "Could not read the timeline."}

    fps = float(info.get("fps") or 24)
    timeline_start = int(info.get("startFrame") or 0)
    playhead = int(info.get("playheadFrame") or timeline_start)
    tracks = info.get("tracks", {}).get("audio", []) or []
    if not tracks:
        return {"success": False, "error": "This timeline has no audio tracks."}

    wanted = [t for t in tracks if not track or int(t.get("index") or 0) == int(track)]
    if not wanted:
        return {"success": False, "error": "There is no audio track %d on this timeline." % track}

    item = None
    if index is None:
        for entry in wanted:
            # A muted track is not what you are listening to, so it is not what
            # we should hand you either.
            if entry.get("enabled") is False:
                continue
            for candidate in entry.get("items") or []:
                if int(candidate.get("start", 0)) <= playhead < int(candidate.get("end", 0)):
                    item = candidate
                    break
            if item:
                break
        if not item:
            where = "track A%d" % track if track else "any enabled audio track"
            return {"success": False, "error": "No audio clip under the playhead on %s." % where}
    else:
        items = wanted[0].get("items") or []
        if not items:
            return {"success": False, "error": "That audio track is empty."}
        if index < 0 or index >= len(items):
            return {
                "success": False,
                "error": "Track A%d has %d clips, so there is no clip %d."
                % (int(wanted[0].get("index") or 1), len(items), index),
            }
        item = items[index]

    path = item.get("path")
    if not path or not os.path.exists(path):
        return {"success": False, "error": "“%s” is offline — its file is not on disk." % item.get("name")}
    if not _has_audio_stream(path):
        return {"success": False, "error": "“%s” has no audio in it." % item.get("name")}

    duration = max(0, int(item["end"]) - int(item["start"])) / fps
    if duration <= 0:
        return {"success": False, "error": "That clip has no length."}

    # Where the clip starts inside its own file. Ask Resolve rather than deriving
    # it from GetLeftOffset, and convert with the SOURCE's rate — the same two
    # mistakes that made frame grabbing land on the wrong picture.
    source_fps = _clip_source_fps(path, fps)
    source_start = item.get("sourceStartFrame")
    if source_start is None:
        source_start = int(item.get("leftOffset") or 0) * (source_fps / fps if fps else 1.0)
    src_in = max(0.0, float(source_start) / source_fps)

    ff = _ffmpeg()
    os.makedirs(dest_dir, exist_ok=True)
    stem = "%s_%d" % (_safe_tag(tag), int(time.time() * 1000))
    out_path = os.path.join(dest_dir, stem + ".mp3")

    command = [
        ff, "-hide_banner", "-loglevel", "error", "-y",
        "-ss", "%.4f" % src_in,
        "-t", "%.4f" % duration,
        "-i", path,
        "-vn",
        "-c:a", "libmp3lame", "-q:a", "2",
        out_path,
    ]
    try:
        result = dg_ffmpeg.run(command, timeout=900)
    except Exception as err:
        return {"success": False, "error": "Could not cut that clip's audio: %s" % err}

    if result.returncode != 0 or not os.path.exists(out_path):
        detail = (result.stderr or b"").decode("utf-8", "replace").strip()[-200:]
        return {"success": False, "error": detail or "ffmpeg could not cut that clip's audio."}

    return {
        "success": True,
        "ok": True,
        "path": out_path,
        "format": "mp3",
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
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(tag or "audio"))
    return cleaned[:48] or "audio"
