"""
Runs inside Resolve's own scripting interpreter (`fuscript -l py3`), never
inside ComfyUI. Reads one JSON request, writes one JSON result. Stdlib only,
no prints (fuscript replaces stdout and banners to it anyway — the result
file is the only channel).

Request:  {"op": "status"|"timeline_info"|"import_media"|"add_to_timeline"
                 |"grab_playhead"|"grab_edge", ...params}
Response: plain JSON, always written even on internal error.
"""

import glob
import json
import os
import sys
import time

BIN_NAME = "Second Unit"
EXPORT_PREFIX = "dg_grab_"


# --------------------------------------------------------------------------- #
# Connection
# --------------------------------------------------------------------------- #

def _modules_dirs(hints=None):
    dirs = []
    # Wherever the interpreter that launched us lives, the scripting Modules
    # are usually a few levels away — this covers custom install prefixes
    # with no hardcoded root.
    fuscript_dir = (hints or {}).get("fuscript_dir") or ""
    if fuscript_dir:
        root = os.path.abspath(fuscript_dir)
        for _ in range(5):
            for sub in ("Modules",
                        os.path.join("Developer", "Scripting", "Modules")):
                dirs.append(os.path.join(root, sub))
            parent = os.path.dirname(root)
            if parent == root:
                break
            root = parent
    env_api = os.environ.get("RESOLVE_SCRIPT_API")
    if env_api:
        dirs.append(env_api)
        dirs.append(os.path.join(env_api, "Modules"))
    env_lib = os.environ.get("RESOLVE_SCRIPT_LIB")
    if env_lib and not os.path.isdir(env_lib):
        env_lib = os.path.dirname(env_lib)
    if env_lib:
        dirs.append(env_lib)
    if sys.platform.startswith("win"):
        program_data = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        dirs.append(os.path.join(
            program_data, "Blackmagic Design", "DaVinci Resolve",
            "Support", "Developer", "Scripting", "Modules"))
    elif sys.platform == "darwin":
        dirs.append("/Library/Application Support/Blackmagic Design/DaVinci Resolve"
                    "/Developer/Scripting/Modules")
    else:
        dirs += ["/opt/resolve/Developer/Scripting/Modules",
                 "/opt/resolve/libs/Fusion/Modules",
                 "/home/resolve/Developer/Scripting/Modules"]
    seen = set()
    unique = []
    for path in dirs:
        if path and path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def _scriptapp(hints=None):
    # Under fuscript, `bmd` is a builtin global — `import bmd` does NOT work.
    try:
        builtin = globals().get("bmd")
        if builtin is None:
            try:
                builtin = bmd  # noqa: F821 (provided by fuscript at runtime)
            except NameError:
                builtin = None
        if builtin is not None:
            app = builtin.scriptapp("Resolve")
            if app:
                return app
    except Exception:
        pass
    for path in _modules_dirs(hints):
        if path and os.path.isdir(path) and path not in sys.path:
            sys.path.insert(0, path)
    try:
        import DaVinciResolveScript as dvr
        return dvr.scriptapp("Resolve")
    except Exception:
        return None


def _ctx(hints=None):
    resolve = _scriptapp(hints)
    if not resolve:
        return None
    try:
        pm = resolve.GetProjectManager()
    except Exception:
        pm = None
    project = None
    try:
        project = pm.GetCurrentProject() if pm else None
    except Exception:
        project = None
    media_pool = None
    timeline = None
    if project:
        try:
            media_pool = project.GetMediaPool()
        except Exception:
            media_pool = None
        try:
            timeline = project.GetCurrentTimeline()
        except Exception:
            timeline = None
    return {"resolve": resolve, "pm": pm, "project": project,
            "media_pool": media_pool, "timeline": timeline}


def _fps(timeline):
    if not timeline:
        return 24.0
    try:
        return float(timeline.GetSetting("timelineFrameRate") or 24)
    except Exception:
        return 24.0


# --------------------------------------------------------------------------- #
# Timecode
# --------------------------------------------------------------------------- #

def timecode_to_frames(timecode, fps):
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
    nominal = max(1, int(round(fps)))
    frame = max(0, int(frame))
    frames = frame % nominal
    total_seconds = frame // nominal
    seconds = total_seconds % 60
    minutes = (total_seconds // 60) % 60
    hours = total_seconds // 3600
    return "%02d:%02d:%02d:%02d" % (hours, minutes, seconds, frames)


# --------------------------------------------------------------------------- #
# Status / timeline
# --------------------------------------------------------------------------- #

def op_status(ctx, req):
    if not ctx:
        return {"connected": False}
    product = ""
    version = ""
    page = None
    try:
        product = ctx["resolve"].GetProductName() or ""
        version = ctx["resolve"].GetVersionString() or ""
        page = ctx["resolve"].GetCurrentPage()
    except Exception:
        pass
    project = ctx["project"]
    timeline = ctx["timeline"]
    try:
        project_name = project.GetName() if project else None
    except Exception:
        project_name = None
    try:
        timeline_name = timeline.GetName() if timeline else None
    except Exception:
        timeline_name = None
    return {
        "connected": True,
        "product": product,
        "version": version,
        "page": page,
        "project": project_name,
        "timeline": timeline_name,
        "bridgeVersion": req.get("bridge_version", ""),
    }


def _setting_int(timeline, key):
    try:
        return int(float(timeline.GetSetting(key) or 0))
    except Exception:
        return 0


def _track_name(timeline, track_type, index):
    try:
        return timeline.GetTrackName(track_type, index) or ""
    except Exception:
        return ""


def _track_enabled(timeline, track_type, index):
    try:
        return timeline.GetIsTrackEnabled(track_type, index) is not False
    except Exception:
        return True


def _items_in_track(timeline, track_type, index):
    items = None
    try:
        items = timeline.GetItemListInTrack(track_type, index)
    except Exception:
        items = None
    if not items:
        try:
            items = timeline.GetItemsInTrack(track_type, index)
        except Exception:
            items = None
    if isinstance(items, dict):
        items = [items[key] for key in sorted(items)]
    return items or []


def _safe(getter, default=""):
    try:
        return getter() or default
    except Exception:
        return default


def _safe_int(getter, default=0):
    if not getter:
        return default
    try:
        return int(getter() or default)
    except Exception:
        return default


def _describe_item(item, track_index, track_type, timeline_start):
    try:
        start = int(item.GetStart())
        end = int(item.GetEnd())
    except Exception:
        start, end = 0, 0
    path = ""
    try:
        media = item.GetMediaPoolItem()
        if media:
            path = media.GetClipProperty("File Path") or ""
    except Exception:
        pass
    source_start = None
    try:
        source_start = int(item.GetSourceStartFrame())
    except Exception:
        source_start = None
    return {
        "name": _safe(item.GetName),
        "path": path,
        "start": start,
        "end": end,
        "durationFrames": max(0, end - start),
        "leftOffset": _safe_int(getattr(item, "GetLeftOffset", None)),
        "sourceStartFrame": source_start,
        "track": track_index,
        "trackType": track_type,
        "timelineOffset": max(0, start - timeline_start),
    }


def _find_item_at(video_tracks, frame):
    for track in reversed(video_tracks or []):
        for position, item in enumerate(track.get("items", [])):
            if item["start"] <= frame < item["end"]:
                return dict(item, itemIndex=position)
    return None


def op_timeline_info(ctx, req):
    if not ctx:
        return {"success": False, "connected": False,
                "error": "Not connected to DaVinci Resolve."}
    timeline = ctx["timeline"]
    if not timeline:
        return {"success": False, "error": "No timeline is open in Resolve."}
    include_items = req.get("include_items", True)
    fps = _fps(timeline)
    try:
        start_frame = int(timeline.GetStartFrame())
        end_frame = int(timeline.GetEndFrame())
    except Exception:
        start_frame, end_frame = 0, 0
    playhead_tc = ""
    try:
        playhead_tc = timeline.GetCurrentTimecode() or ""
    except Exception:
        pass
    playhead_abs = timecode_to_frames(playhead_tc, fps) if playhead_tc else start_frame
    try:
        name = timeline.GetName()
    except Exception:
        name = ""
    info = {
        "success": True,
        "connected": True,
        "name": name,
        "fps": fps,
        "startFrame": start_frame,
        "endFrame": end_frame,
        "durationFrames": max(0, end_frame - start_frame),
        "playheadTimecode": playhead_tc,
        "playheadFrame": playhead_abs,
        "playheadOffsetFrames": max(0, playhead_abs - start_frame),
        "width": _setting_int(timeline, "timelineResolutionWidth"),
        "height": _setting_int(timeline, "timelineResolutionHeight"),
        "tracks": {},
    }
    for track_type in ("video", "audio"):
        try:
            count = int(timeline.GetTrackCount(track_type) or 0)
        except Exception:
            count = 0
        tracks = []
        for index in range(1, count + 1):
            track = {
                "index": index,
                "name": _track_name(timeline, track_type, index),
                "enabled": _track_enabled(timeline, track_type, index),
                "items": [],
            }
            if include_items:
                track["items"] = [
                    _describe_item(item, index, track_type, start_frame)
                    for item in _items_in_track(timeline, track_type, index)
                ]
            tracks.append(track)
        info["tracks"][track_type] = tracks
    info["clipAtPlayhead"] = _find_item_at(info["tracks"]["video"], playhead_abs)
    return info


# --------------------------------------------------------------------------- #
# Media pool
# --------------------------------------------------------------------------- #

def _get_or_create_bin(media_pool):
    root = media_pool.GetRootFolder()
    if not root:
        return None
    try:
        for folder in root.GetSubFolderList() or []:
            if folder.GetName() == BIN_NAME:
                return folder
    except Exception:
        pass
    try:
        return media_pool.AddSubFolder(root, BIN_NAME)
    except Exception:
        return root


def _find_clip_in_pool(media_pool, file_path):
    target = os.path.basename(file_path)

    def search(folder, depth=0):
        if not folder or depth > 6:
            return None
        try:
            for clip in folder.GetClipList() or []:
                for prop in ("File Name", "Clip Name"):
                    try:
                        value = clip.GetClipProperty(prop) or ""
                    except Exception:
                        value = ""
                    if value and os.path.basename(value) == target:
                        return clip
        except Exception:
            pass
        try:
            for sub in folder.GetSubFolderList() or []:
                found = search(sub, depth + 1)
                if found:
                    return found
        except Exception:
            pass
        return None

    bin_folder = _get_or_create_bin(media_pool)
    return search(bin_folder) or search(media_pool.GetRootFolder())


def op_import_media(ctx, req):
    if not ctx:
        return {"success": False, "connected": False,
                "error": "Not connected to DaVinci Resolve."}
    if not ctx["media_pool"]:
        return {"success": False, "error": "No project is open in Resolve."}
    file_path = req.get("file_path") or ""
    if not file_path or not os.path.exists(file_path):
        return {"success": False, "error": "That file does not exist: %s" % file_path}
    media_pool = ctx["media_pool"]
    folder = _get_or_create_bin(media_pool)
    try:
        if folder:
            media_pool.SetCurrentFolder(folder)
        result = media_pool.ImportMedia([file_path])
    except Exception as err:
        return {"success": False, "error": "Resolve refused the import: %s" % err}
    clip = _find_clip_in_pool(media_pool, file_path)
    if not clip and not result:
        return {"success": False, "error": "Resolve did not import the file."}
    return {
        "success": True,
        "imported": len(result) if isinstance(result, list) else 1,
        "bin": BIN_NAME,
    }


def _frame_occupied(timeline, track_type, track_index, frame):
    for item in _items_in_track(timeline, track_type, track_index):
        try:
            if int(item.GetStart()) <= frame < int(item.GetEnd()):
                return True
        except Exception:
            continue
    return False


def _resolve_track(timeline, track_type, track_name):
    try:
        count = int(timeline.GetTrackCount(track_type) or 0)
    except Exception:
        count = 0
    if not track_name:
        return count if count else 1
    for index in range(1, count + 1):
        if _track_name(timeline, track_type, index) == track_name:
            return index
    try:
        if track_type == "audio":
            timeline.AddTrack("audio", {"audio_type": "stereo"})
        else:
            timeline.AddTrack(track_type)
        new_index = int(timeline.GetTrackCount(track_type) or (count + 1))
        timeline.SetTrackName(track_type, new_index, track_name)
        return new_index
    except Exception:
        return count if count else 1


def _subtitle_counts(timeline):
    try:
        count = int(timeline.GetTrackCount("subtitle") or 0)
    except Exception:
        count = 0
    return {i: len(_items_in_track(timeline, "subtitle", i)) for i in range(1, count + 1)}


def _add_subtitles(ctx, timeline, clip, fps):
    before = _subtitle_counts(timeline)
    created_track = False
    if not before:
        try:
            added_track = timeline.AddTrack("subtitle")
        except Exception as err:
            return {"success": False, "error": "Resolve would not add a subtitle track: %s" % err}
        if not added_track:
            return {"success": False, "error": "Resolve would not add a subtitle track."}
        created_track = True
        before = _subtitle_counts(timeline)
    try:
        ctx["media_pool"].AppendToTimeline([clip])
    except Exception as err:
        return {"success": False, "error": "Resolve would not add the subtitles: %s" % err}
    after = _subtitle_counts(timeline)
    gained = sum(after.values()) - sum(before.values())
    if gained <= 0:
        return {
            "success": False,
            "error": "Resolve accepted the subtitle file but no captions appeared on the "
                     "timeline. It is in the media pool under %s — drag it onto a subtitle "
                     "track." % BIN_NAME,
        }
    landed = [i for i in after if after[i] > before.get(i, 0)]
    first_frame = None
    for index in landed:
        for item in _items_in_track(timeline, "subtitle", index):
            try:
                start = int(item.GetStart())
            except Exception:
                continue
            if first_frame is None or start < first_frame:
                first_frame = start
    try:
        ctx["project"].SaveProject()
    except Exception:
        pass
    return {
        "success": True,
        "trackType": "subtitle",
        "trackIndex": landed[0] if landed else 1,
        "captions": gained,
        "createdTrack": created_track,
        "timecode": frames_to_timecode(first_frame, fps) if first_frame is not None else "",
    }


def op_add_to_timeline(ctx, req):
    if not ctx:
        return {"success": False, "connected": False,
                "error": "Not connected to DaVinci Resolve."}
    if not ctx["media_pool"] or not ctx["timeline"]:
        return {"success": False, "error": "Open a project with a timeline first."}
    file_path = req.get("file_path") or ""
    if not file_path or not os.path.exists(file_path):
        return {"success": False, "error": "That file does not exist: %s" % file_path}
    start_seconds = req.get("start_seconds")
    track_name = req.get("track_name")

    timeline = ctx["timeline"]
    fps = _fps(timeline)

    imported = op_import_media(ctx, {"file_path": file_path})
    if not imported.get("success"):
        return imported
    clip = _find_clip_in_pool(ctx["media_pool"], file_path)
    if not clip:
        return {"success": False, "error": "The file imported but could not be found again."}

    clip_type = ""
    try:
        clip_type = (clip.GetClipProperty("Type") or "").lower()
    except Exception:
        pass
    if "subtitle" in clip_type:
        return _add_subtitles(ctx, timeline, clip, fps)

    is_audio = "audio" in clip_type and "video" not in clip_type
    track_type = "audio" if is_audio else "video"
    track_index = _resolve_track(timeline, track_type, track_name)
    try:
        timeline_start = int(timeline.GetStartFrame())
    except Exception:
        timeline_start = 0
    if start_seconds is not None:
        try:
            record_frame = timeline_start + int(round(float(start_seconds) * fps))
        except (TypeError, ValueError):
            return {"success": False, "error": "Bad start time: %r" % (start_seconds,)}
    else:
        try:
            playhead = timecode_to_frames(timeline.GetCurrentTimecode(), fps)
        except Exception:
            playhead = timeline_start
        record_frame = playhead if playhead >= timeline_start else timeline_start

    clip_info = {
        "mediaPoolItem": clip,
        "startFrame": 0,
        "trackIndex": track_index,
        "mediaType": 2 if is_audio else 1,
        "recordFrame": record_frame,
    }
    before = len(_items_in_track(timeline, track_type, track_index))
    target_free = not _frame_occupied(timeline, track_type, track_index, record_frame)
    fallback_used = False
    audio_track = None
    audio_blocked = False
    if target_free:
        try:
            ctx["media_pool"].AppendToTimeline([clip_info])
        except Exception:
            pass
        after = len(_items_in_track(timeline, track_type, track_index))
        if after <= before:
            fallback_used = True
        elif track_type == "video":
            placed = _place_linked_audio(ctx, timeline, clip, record_frame)
            if placed == "busy":
                audio_blocked = True
            else:
                audio_track = placed
    else:
        fallback_used = True
    if fallback_used:
        try:
            ctx["media_pool"].AppendToTimeline([clip])
        except Exception as err:
            return {"success": False, "error": "Resolve would not add the clip: %s" % err}
        after = len(_items_in_track(timeline, track_type, track_index))
        if after <= before:
            return {"success": False, "error": "Resolve accepted the clip but nothing appeared on the timeline."}
    try:
        ctx["project"].SaveProject()
    except Exception:
        pass
    response = {
        "success": True,
        "trackType": track_type,
        "trackIndex": track_index,
        "recordFrame": record_frame,
        "timecode": frames_to_timecode(record_frame, fps),
        "positioned": not fallback_used,
    }
    if audio_track is not None:
        response["audioTrackIndex"] = audio_track
    if audio_blocked and not response.get("note"):
        response["note"] = (
            "The picture landed at the playhead but every audio track is busy "
            "there, so the sound was not placed."
        )
    if fallback_used:
        if target_free:
            response["note"] = (
                "Resolve would not place the clip at the playhead this time, "
                "so it was added at the end of the timeline. Drag it into place."
            )
        else:
            response["note"] = (
                "The playhead is over another clip, and this Resolve build "
                "cannot insert between clips. The clip was added at the end of "
                "the timeline - drag it into place, or park the playhead in a gap."
            )
    return response


def _item_starts_at(item, frame):
    try:
        return int(item.GetStart()) == int(frame)
    except Exception:
        return False


def _place_linked_audio(ctx, timeline, clip, record_frame):
    """Put the clip's sound on a free audio track at the same frame.

    A positioned AppendToTimeline with mediaType video places the picture
    only — the linked audio never follows on its own, so a clip sent to the
    playhead arrived mute. Returns the audio track index when the sound
    landed, "busy" when every audio track is occupied there, else None (the
    clip carries no sound, or Resolve refused it).
    """
    try:
        count = int(timeline.GetTrackCount("audio") or 0)
    except Exception:
        count = 0
    for audio_index in range(1, max(count, 1) + 1):
        if _frame_occupied(timeline, "audio", audio_index, record_frame):
            continue
        try:
            before = len(_items_in_track(timeline, "audio", audio_index))
        except Exception:
            before = 0
        try:
            ctx["media_pool"].AppendToTimeline([{
                "mediaPoolItem": clip,
                "startFrame": 0,
                "trackIndex": audio_index,
                "mediaType": 2,
                "recordFrame": record_frame,
            }])
        except Exception:
            return None
        try:
            items = _items_in_track(timeline, "audio", audio_index)
        except Exception:
            return None
        if len(items) > before and any(_item_starts_at(item, record_frame) for item in items):
            return audio_index
        return None
    return "busy" if count else None


# --------------------------------------------------------------------------- #
# Frame grabs (gallery + fusion; ffmpeg fallback stays in ComfyUI)
# --------------------------------------------------------------------------- #

def _safe_tag(tag):
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(tag or "frame"))
    return cleaned[:48] or "frame"


def _move_playhead(timeline, frame, fps):
    timecode = frames_to_timecode(frame, fps)
    try:
        if timeline.SetCurrentTimecode(timecode):
            return True
    except Exception:
        pass
    try:
        return timecode_to_frames(timeline.GetCurrentTimecode(), fps) == frame
    except Exception:
        return False


def _item_at(timeline, frame):
    try:
        count = int(timeline.GetTrackCount("video") or 0)
    except Exception:
        count = 0
    for index in range(count, 0, -1):
        try:
            if timeline.GetIsTrackEnabled("video", index) is False:
                continue
        except Exception:
            pass
        for item in _items_in_track(timeline, "video", index):
            try:
                if int(item.GetStart()) <= frame < int(item.GetEnd()):
                    return item
            except Exception:
                continue
    return None


def _grab_gallery(ctx, dest_dir, skip=False):
    if skip:
        raise RuntimeError("skipped: this Resolve build refuses gallery exports (remembered)")
    resolve = ctx["resolve"]
    project = ctx["project"]
    if not project:
        raise RuntimeError("no project")
    previous_page = None
    try:
        previous_page = resolve.GetCurrentPage()
    except Exception:
        pass
    if previous_page != "color":
        try:
            resolve.OpenPage("color")
        except Exception:
            raise RuntimeError("could not open the Color page")
    still = None
    album = None
    try:
        still = ctx["timeline"].GrabStill()
        if not still:
            raise RuntimeError("Resolve returned no still")
        gallery = project.GetGallery()
        if not gallery:
            raise RuntimeError("no gallery")
        album = gallery.GetCurrentStillAlbum()
        if not album:
            raise RuntimeError("no still album")

        def listing():
            try:
                return set(os.listdir(dest_dir))
            except Exception:
                return set()

        before = listing()
        exported = album.ExportStills([still], dest_dir, EXPORT_PREFIX, "png")
        produced = sorted(listing() - before)
        if not produced:
            album_name = ""
            try:
                album_name = album.GetName() or "(unnamed)"
            except Exception:
                album_name = "?"
            if exported is False:
                raise RuntimeError(
                    "__GALLERY_DEAD__ the export produced no file "
                    "(ExportStills -> %r, album %s)" % (exported, album_name))
            raise RuntimeError(
                "the export produced no file (ExportStills -> %r, album %s)" % (exported, album_name))
        return os.path.join(dest_dir, produced[-1])
    finally:
        if still is not None and album is not None:
            try:
                album.DeleteStills([still])
            except Exception:
                pass
        if previous_page and previous_page != "color":
            try:
                resolve.OpenPage(previous_page)
            except Exception:
                pass


def _grab_fusion(dest_dir, item, frame):
    if not item:
        raise RuntimeError("no clip under the playhead")
    try:
        comp = item.GetFusionCompByIndex(1)
    except Exception:
        comp = None
    if not comp:
        raise RuntimeError("this clip has no Fusion composition")
    out_path = os.path.join(dest_dir, "%sfusion_%d.png" % (EXPORT_PREFIX, int(time.time() * 1000)))
    try:
        rendered = comp.Render({"Start": frame, "End": frame,
                                "FileName": out_path, "SuppressAllDialogs": True})
    except Exception as err:
        raise RuntimeError("the render failed: %s" % err)
    if not rendered:
        raise RuntimeError("the render returned false")
    if os.path.exists(out_path):
        return out_path
    stem, ext = os.path.splitext(out_path)
    numbered = sorted(glob.glob(stem + "*" + ext), key=os.path.getmtime)
    if numbered:
        return numbered[-1]
    raise RuntimeError("the render produced no file")


def _grab_native(ctx, dest_dir, tag, frame, item, skip_gallery):
    try:
        timeline_start = int(ctx["timeline"].GetStartFrame())
    except Exception:
        timeline_start = 0
    try:
        import shutil as _shutil
        _shutil.os.makedirs(dest_dir, exist_ok=True)
    except Exception:
        try:
            os.makedirs(dest_dir, exist_ok=True)
        except Exception:
            pass
    fps = _fps(ctx["timeline"])
    final_path = os.path.join(dest_dir, "%s_%d.png" % (_safe_tag(tag), int(time.time() * 1000)))
    attempts = []
    gallery_dead = False
    for name, method in (("gallery", lambda: _grab_gallery(ctx, dest_dir, skip_gallery)),
                         ("fusion", lambda: _grab_fusion(dest_dir, item, frame))):
        try:
            produced = method()
        except Exception as err:
            text = str(err)
            if "__GALLERY_DEAD__" in text:
                gallery_dead = True
                text = text.replace("__GALLERY_DEAD__", "").strip()
            attempts.append("%s: %s" % (name, text))
            continue
        if produced and os.path.exists(produced):
            try:
                if os.path.abspath(produced) != os.path.abspath(final_path):
                    import shutil
                    shutil.move(produced, final_path)
            except Exception:
                final_path = produced
            result = {
                "success": True,
                "path": final_path,
                "method": name,
                "frame": frame,
                "timecode": frames_to_timecode(frame, fps),
                "fps": fps,
            }
            try:
                result["timeline"] = ctx["timeline"].GetName()
            except Exception:
                result["timeline"] = ""
            if attempts:
                result["fell_back_from"] = "; ".join(attempts)
            if gallery_dead:
                result["gallery_dead"] = True
            return result
        attempts.append("%s: produced nothing" % name)
    return {"success": False, "attempts": attempts, "gallery_dead": gallery_dead}


def _meta_for(item, timeline_start):
    if not item:
        return None
    try:
        return _describe_item(item, 0, "video", timeline_start)
    except Exception:
        return None


def op_grab_playhead(ctx, req):
    if not ctx:
        return {"success": False, "connected": False,
                "error": "Not connected to DaVinci Resolve."}
    if not ctx["timeline"]:
        return {"success": False, "error": "No timeline is open in Resolve."}
    dest_dir = req.get("dest_dir") or ""
    tag = req.get("tag") or "frame"
    if not dest_dir:
        return {"success": False, "error": "No destination folder."}
    timeline = ctx["timeline"]
    fps = _fps(timeline)
    try:
        frame = timecode_to_frames(timeline.GetCurrentTimecode(), fps)
    except Exception as err:
        return {"success": False, "error": "Resolve would not report the playhead: %s" % err}
    item = _item_at(timeline, frame)
    try:
        timeline_start = int(timeline.GetStartFrame())
    except Exception:
        timeline_start = 0
    result = _grab_native(ctx, dest_dir, tag, frame, item, bool(req.get("skip_gallery")))
    if result.get("success"):
        return result
    return {
        "success": False,
        "error": "Could not grab that frame. Tried: " + "; ".join(result.get("attempts") or ["unknown"]),
        "frame": frame,
        "fps": fps,
        "timeline": _safe(timeline.GetName),
        "item": _meta_for(item, timeline_start),
        "fell_back_from": "; ".join(result.get("attempts") or []),
        "gallery_dead": result.get("gallery_dead", False),
    }


def op_grab_edge(ctx, req):
    if not ctx:
        return {"success": False, "connected": False,
                "error": "Not connected to DaVinci Resolve."}
    if not ctx["timeline"]:
        return {"success": False, "error": "No timeline is open in Resolve."}
    dest_dir = req.get("dest_dir") or ""
    tag = req.get("tag") or "frame"
    if not dest_dir:
        return {"success": False, "error": "No destination folder."}
    timeline = ctx["timeline"]
    try:
        track = int(req.get("track") or 1)
    except (TypeError, ValueError):
        track = 1
    try:
        item_index = int(req.get("item_index") or 0)
    except (TypeError, ValueError):
        item_index = 0
    edge = req.get("edge") or "first"
    track_type = req.get("track_type") or "video"
    items = _items_in_track(timeline, track_type, track)
    if not items:
        return {"success": False, "error": "That track is empty."}
    try:
        item = items[item_index]
    except (IndexError, ValueError, TypeError):
        return {"success": False, "error": "There is no clip at that position on the track."}
    try:
        start = int(item.GetStart())
        end = int(item.GetEnd())
    except Exception:
        return {"success": False, "error": "Resolve would not report that clip's position."}
    fps = _fps(timeline)
    frame = start if edge == "first" else max(start, end - 1)
    if not _move_playhead(timeline, frame, fps):
        return {"success": False, "error": "Resolve would not move the playhead to that frame."}
    try:
        timeline_start = int(timeline.GetStartFrame())
    except Exception:
        timeline_start = 0
    result = _grab_native(ctx, dest_dir, tag, frame, item, bool(req.get("skip_gallery")))
    if result.get("success"):
        result["clip"] = _safe(item.GetName)
        result["edge"] = edge
        return result
    return {
        "success": False,
        "error": "Could not grab that frame. Tried: " + "; ".join(result.get("attempts") or ["unknown"]),
        "frame": frame,
        "fps": fps,
        "timeline": _safe(timeline.GetName),
        "clip": _safe(item.GetName),
        "edge": edge,
        "item": _meta_for(item, timeline_start),
        "fell_back_from": "; ".join(result.get("attempts") or []),
        "gallery_dead": result.get("gallery_dead", False),
    }


OPS = {
    "status": op_status,
    "timeline_info": op_timeline_info,
    "import_media": op_import_media,
    "add_to_timeline": op_add_to_timeline,
    "grab_playhead": op_grab_playhead,
    "grab_edge": op_grab_edge,
}


def main():
    out = {"success": False, "error": "No request."}
    try:
        with open(sys.argv[1], "r", encoding="utf-8") as handle:
            req = json.load(handle)
    except Exception as err:
        req = {}
        out = {"success": False, "error": "Bad request file: %s" % err}
    else:
        op = req.get("op")
        fn = OPS.get(op)
        if not fn:
            out = {"success": False, "error": "Unknown op: %r" % (op,)}
        else:
            ctx = _ctx(req)
            if ctx is None and op != "status":
                out = {"success": False, "connected": False,
                       "error": "Not connected to DaVinci Resolve. Check that Resolve is running, "
                                "and that Preferences -> System -> General -> 'External scripting "
                                "using' is not set to None."}
            else:
                try:
                    out = fn(ctx, req) or {}
                except Exception as err:
                    out = {"success": False, "error": "%s" % err}
                if not isinstance(out, dict):
                    out = {"success": False, "error": "Bad helper result."}
    try:
        with open(sys.argv[2], "w", encoding="utf-8") as handle:
            json.dump(out, handle)
    except Exception:
        pass


main()
