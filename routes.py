"""
HTTP routes for the grab-on-click button.

The point of these is that grabbing a frame should NOT require running the
workflow. You park the playhead, press the button, and the frame is in ComfyUI
as an ordinary input image — previewed on the node, reusable, and stable across
runs. A node that re-grabbed on every execution would give you a different
picture each time you tweaked a prompt.

Registered defensively: a ComfyUI too old to expose PromptServer must still be
able to import this pack.
"""

import os
import time

try:
    from aiohttp import web
    from server import PromptServer

    _SERVER = PromptServer.instance
except Exception:  # pragma: no cover - older or embedded ComfyUI
    _SERVER = None


def _input_dir():
    import folder_paths

    path = os.path.join(folder_paths.get_input_directory(), "second-unit")
    os.makedirs(path, exist_ok=True)
    return path


def _register():
    if _SERVER is None:
        return

    @_SERVER.routes.post("/secondunit/grab")
    async def secondunit_grab(request):
        """Grab one frame now and drop it into ComfyUI's input folder."""
        try:
            body = await request.json()
        except Exception:
            body = {}

        where = body.get("where") or "playhead"
        track = int(body.get("track") or 1)
        clip_index = int(body.get("clip_index") or 0)

        try:
            from .secondunit import resolve as R
            from .secondunit import frames as F

            ctx = R.Context()
            if not ctx.connected:
                return web.json_response(
                    {
                        "error": "Cannot reach DaVinci Resolve. Is it running, and is "
                        "Preferences -> System -> General -> 'External scripting using' "
                        "set to something other than None?"
                    },
                    status=503,
                )

            dest = _input_dir()
            tag = "grab_%d" % int(time.time())
            if where == "playhead":
                result = F.grab_playhead(dest, tag=tag)
            else:
                edge = "first" if where == "clip first frame" else "last"
                result = F.grab_edge("video", track, clip_index, edge, dest, tag=tag)

            if not result.get("success"):
                return web.json_response(
                    {"error": result.get("error") or "Could not grab that frame."}, status=500
                )

            return web.json_response(
                {
                    # Shaped like ComfyUI's own upload response so the frontend can
                    # treat a grabbed frame exactly like an uploaded one.
                    "name": os.path.basename(result["path"]),
                    "subfolder": "second-unit",
                    "type": "input",
                    "frame": result.get("frame"),
                    "timecode": result.get("timecode"),
                    "method": result.get("method"),
                }
            )
        except Exception as err:
            return web.json_response({"error": str(err)}, status=500)

    @_SERVER.routes.post("/secondunit/grab_audio")
    async def secondunit_grab_audio(request):
        """
        Sound off the timeline, now, ready to play on the node.

        Either one clip — the usual case, and much faster — or the whole thing
        mixed down when you really do want everything at once.
        """
        try:
            body = await request.json()
        except Exception:
            body = {}

        what = body.get("what") or "the clip at the playhead"
        track = int(body.get("track") or 0)
        clip_index = int(body.get("clip_index") or 0)

        try:
            from .secondunit import resolve as R
            from .secondunit import audio as A

            ctx = R.Context()
            if not ctx.connected:
                return web.json_response(
                    {"error": "Cannot reach DaVinci Resolve. Is it running?"}, status=503
                )

            dest = _input_dir()
            stamp = int(time.time())
            if what == "the whole timeline":
                result = A.grab_timeline_audio(dest, tag="timeline_%d" % stamp)
            else:
                result = A.grab_clip_audio(
                    dest,
                    tag="clip_%d" % stamp,
                    track=track,
                    index=clip_index if what == "a clip by index" else None,
                )

            if not result.get("success"):
                return web.json_response(
                    {"error": result.get("error") or "Could not get that audio."},
                    status=500,
                )

            path = result["path"]

            return web.json_response(
                {
                    "name": os.path.basename(path),
                    "subfolder": "second-unit",
                    "type": "input",
                    "seconds": float(result.get("durationSeconds") or 0.0),
                    "startSeconds": result.get("startSeconds"),
                    "clip": result.get("clip"),
                    "track": result.get("track"),
                    "skipped": result.get("skipped") or [],
                }
            )
        except Exception as err:
            return web.json_response({"error": str(err)}, status=500)

    @_SERVER.routes.post("/secondunit/grab_video")
    async def secondunit_grab_video(request):
        """
        One video clip off the timeline, now, ready to play on the node.

        Park the playhead on a clip and press the button — that clip's
        picture (plus its sound, when it has any) lands in ComfyUI's input
        folder as an mp4.
        """
        try:
            body = await request.json()
        except Exception:
            body = {}

        what = body.get("what") or "the clip at the playhead"
        track = int(body.get("track") or 0)
        clip_index = int(body.get("clip_index") or 0)

        try:
            from .secondunit import resolve as R
            from .secondunit import video as V

            ctx = R.Context()
            if not ctx.connected:
                return web.json_response(
                    {"error": "Cannot reach DaVinci Resolve. Is it running?"}, status=503
                )

            dest = _input_dir()
            stamp = int(time.time())
            result = V.grab_clip_video(
                dest,
                tag="clip_%d" % stamp,
                track=track,
                index=clip_index if what == "a clip by index" else None,
            )

            if not result.get("success"):
                return web.json_response(
                    {"error": result.get("error") or "Could not get that video."},
                    status=500,
                )

            path = result["path"]

            return web.json_response(
                {
                    "name": os.path.basename(path),
                    "subfolder": "second-unit",
                    "type": "input",
                    "seconds": float(result.get("durationSeconds") or 0.0),
                    "startSeconds": result.get("startSeconds"),
                    "clip": result.get("clip"),
                    "track": result.get("track"),
                    "skipped": result.get("skipped") or [],
                }
            )
        except Exception as err:
            return web.json_response({"error": str(err)}, status=500)

    @_SERVER.routes.post("/secondunit/grab_cut")
    async def secondunit_grab_cut(request):
        """Grab BOTH frames either side of a cut, now, without running anything."""
        try:
            body = await request.json()
        except Exception:
            body = {}

        which = body.get("which") or "nearest the playhead"
        cut_index = int(body.get("cut_index") or 0)

        try:
            from .secondunit import resolve as R
            from .secondunit import frames as F
            from .secondunit import cuts as C

            ctx = R.Context()
            if not ctx.connected:
                return web.json_response(
                    {"error": "Cannot reach DaVinci Resolve. Is it running?"}, status=503
                )

            info = R.get_timeline_info()
            if not info.get("success"):
                return web.json_response(
                    {"error": info.get("error") or "No timeline is open."}, status=400
                )

            found = C.find_cuts(info)
            if not found:
                return web.json_response(
                    {"error": "This timeline has no cuts. Two clips must sit back to back "
                              "on the SAME video track."},
                    status=400,
                )

            if which == "by index":
                if cut_index >= len(found):
                    return web.json_response(
                        {"error": "Cut %d does not exist; this timeline has %d (0-%d)."
                                  % (cut_index, len(found), len(found) - 1)},
                        status=400,
                    )
                cut = found[cut_index]
            else:
                cut = C.nearest_to(found, info.get("playheadFrame") or 0)

            dest = _input_dir()
            stamp = int(time.time())
            before = F.grab_edge("video", cut["track"], cut["beforeIndex"], "last",
                                 dest, tag="cut%d_before" % stamp)
            if not before.get("success"):
                return web.json_response(
                    {"error": "Frame before the cut: %s" % before.get("error")}, status=500
                )

            after = F.grab_edge("video", cut["track"], cut["afterIndex"], "first",
                                dest, tag="cut%d_after" % stamp)
            if not after.get("success"):
                return web.json_response(
                    {"error": "Frame after the cut: %s" % after.get("error")}, status=500
                )

            from .secondunit import state

            state.remember_cut(cut["placeStartSeconds"], cut["gap"],
                               C.describe(cut, info.get("fps") or 24.0))

            return web.json_response(
                {
                    "first": os.path.basename(before["path"]),
                    "last": os.path.basename(after["path"]),
                    "subfolder": "second-unit",
                    "seconds": float(cut["placeStartSeconds"]),
                    "gap": int(cut["gap"]),
                    "gap_seconds": max(0, int(cut["gap"])) / float(info.get("fps") or 24.0),
                    "label": C.describe(cut, info.get("fps") or 24.0),
                    "cutCount": len(found),
                }
            )
        except Exception as err:
            return web.json_response({"error": str(err)}, status=500)

    @_SERVER.routes.post("/secondunit/send")
    async def secondunit_send(request):
        """Send files the last run produced into Resolve, on demand."""
        try:
            body = await request.json()
        except Exception:
            body = {}

        paths = body.get("paths") or []
        place = body.get("place") or "media pool only"
        seconds = float(body.get("seconds") or 0.0)

        if not paths:
            return web.json_response({"error": "Nothing to send — run the workflow first."}, status=400)

        try:
            from .nodes import _send

            missing = [p for p in paths if not os.path.exists(p)]
            if missing:
                return web.json_response(
                    {"error": "These files are gone: %s" % ", ".join(os.path.basename(m) for m in missing)},
                    status=404,
                )

            messages = [_send(p, place, seconds) for p in paths]
            return web.json_response({"ok": True, "message": "; ".join(messages), "count": len(paths)})
        except Exception as err:
            return web.json_response({"error": str(err)}, status=500)

    @_SERVER.routes.get("/secondunit/status")
    async def secondunit_status(request):
        """Is Resolve reachable, and what is open?"""
        try:
            from .secondunit import resolve as R

            return web.json_response(R.get_status("secondunit"))
        except Exception as err:
            return web.json_response({"connected": False, "error": str(err)})


_register()
