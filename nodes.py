"""
Second Unit nodes: DaVinci Resolve as an input and an output for any workflow.

Everything Resolve-related is imported INSIDE the methods. ComfyUI imports every
custom node at start-up, and a pack that raises on import takes the whole server
down with it — so a machine with no Resolve, or with Resolve closed, must still
load this file without complaint. The failure belongs at run time, where it can
say something useful.
"""

import os
import time

CATEGORY = "Second Unit"


def _capture_dir():
    """Where grabbed frames land: ComfyUI's own input folder, so they are
    reachable by every other node and survive a browser reload."""
    import folder_paths

    path = os.path.join(folder_paths.get_input_directory(), "second-unit")
    os.makedirs(path, exist_ok=True)
    return path


def _output_dir():
    import folder_paths

    path = os.path.join(folder_paths.get_output_directory(), "second-unit")
    os.makedirs(path, exist_ok=True)
    return path


def _stamp(tag, ext):
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(tag or "frame"))
    return "%s_%d%s" % (safe, int(time.time() * 1000), ext)


def _resolve():
    """
    The Resolve layer, or a clear explanation of why not.

    The two things that actually go wrong in the field are Resolve not running
    and its scripting permission being switched off — so name both rather than
    reporting a bare "not connected".
    """
    from .secondunit import resolve as R

    ctx = R.Context()
    if not ctx.connected:
        raise RuntimeError(
            "Cannot reach DaVinci Resolve. Check that Resolve is running, and that "
            "Preferences -> System -> General -> 'External scripting using' is not set "
            "to None."
        )
    return R, ctx


# --------------------------------------------------------------------------- #
# Input
# --------------------------------------------------------------------------- #




IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
AUDIO_SUFFIXES = (".mp3", ".wav", ".flac", ".m4a", ".ogg", ".opus")
VIDEO_SUFFIXES = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi")

# How many input-root uploads to offer alongside grabbed files.
RECENT_UPLOADS = 40


def _input_files(suffixes):
    """
    Everything a picker widget can offer: grabbed files first, then uploads.

    Grabs land in the second-unit folder, while ComfyUI's own upload button
    drops files in the INPUT ROOT. Listing both is what lets one widget accept
    either — take it off the timeline, or bring your own — and what keeps an
    uploaded file selectable after a reload instead of turning the widget red.
    """
    import folder_paths

    root = folder_paths.get_input_directory()
    here = os.path.join(root, "second-unit")

    grabbed = []
    if os.path.isdir(here):
        grabbed = sorted(
            ("second-unit/" + f for f in os.listdir(here)
             if os.path.isfile(os.path.join(here, f)) and f.lower().endswith(suffixes)),
            reverse=True,
        )

    # Only the most recent handful from the input ROOT. A busy ComfyUI has
    # thousands of files in there, and a 3000-entry dropdown is not a picker.
    uploaded = []
    try:
        entries = [
            f for f in os.listdir(root)
            if os.path.isfile(os.path.join(root, f)) and f.lower().endswith(suffixes)
        ]
        entries.sort(key=lambda f: os.path.getmtime(os.path.join(root, f)), reverse=True)
        uploaded = entries[:RECENT_UPLOADS]
    except Exception:
        uploaded = []

    return grabbed + uploaded


def _grabbed_files():
    return _input_files(IMAGE_SUFFIXES)


def _load_grabbed(value, label):
    """Turn a pinned filename back into an IMAGE tensor."""
    import folder_paths

    from .secondunit import convert

    if not value:
        raise RuntimeError("No %s yet — press the button on the node." % label)
    path = folder_paths.get_annotated_filepath(value)
    if not path or not os.path.exists(path):
        path = os.path.join(folder_paths.get_input_directory(), value)
    if not os.path.exists(path):
        raise RuntimeError("That grabbed frame is gone: %s" % value)
    return convert.file_to_image(path)




class ResolveGrabbedAudio:
    """
    Sound off the Resolve timeline, fetched by pressing the button.

    `what` decides how much: the single clip under the playhead (the usual
    answer, and far quicker), a clip picked by position, or every audio track
    mixed into one — which is right for transcribing an edit and wrong for
    almost everything else, since it hands a model music and dialogue at once.

    Fetching takes real time, so doing it on every graph run would make every
    experiment slow. Press once, then iterate freely against the same audio.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                # `audio_upload` is the flag ComfyUI's own Load Audio sets: it
                # turns the combo into a picker with a "choose file to upload"
                # button. The player next to it is added by this pack's own JS —
                # the frontend hands that out only to its own audio classes.
                # Uploads land in the input root whatever we ask for, which is
                # why the list covers that folder too.
                "audio": (_input_files(AUDIO_SUFFIXES), {
                    "tooltip": "Press the button to grab one, or upload your own.",
                    "audio_upload": True,
                }),
            },
            # `seconds` stays SECOND. ComfyUI stores a node's widget values as a
            # bare positional list, so inserting anything above it would silently
            # feed the old saved number into a new widget on every graph anyone
            # had already saved.
            "optional": {
                "seconds": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 86400.0, "step": 0.001}),
                "what": (
                    ["the clip at the playhead", "the whole timeline", "a clip by index"],
                    {"default": "the clip at the playhead",
                     "tooltip": "One clip is usually what you want. The whole timeline "
                                "mixes every track together."},
                ),
                "track": ("INT", {"default": 0, "min": 0, "max": 20,
                                  "tooltip": "0 means whichever enabled track has a clip "
                                             "under the playhead, A1 first."}),
                "clip_index": ("INT", {"default": 0, "min": 0, "max": 9999,
                                       "tooltip": "Only used by 'a clip by index'."}),
            },
        }

    RETURN_TYPES = ("AUDIO", "FLOAT")
    RETURN_NAMES = ("audio", "seconds")
    FUNCTION = "load"
    CATEGORY = CATEGORY
    DESCRIPTION = ("Sound grabbed off the Resolve timeline with the button on this node — one "
                   "clip, or everything mixed down. Does not touch Resolve when the graph runs.")

    def load(self, audio, seconds=0.0, **_picked_by_the_button):
        import folder_paths

        from .secondunit import convert

        if not audio:
            raise RuntimeError("Nothing grabbed yet — press 'Grab the audio' on the node.")
        path = folder_paths.get_annotated_filepath(audio)
        if not path or not os.path.exists(path):
            path = os.path.join(folder_paths.get_input_directory(), audio)
        if not os.path.exists(path):
            raise RuntimeError("That grabbed audio is gone: %s" % audio)
        return (convert.file_to_audio(path), float(seconds))

    @classmethod
    def IS_CHANGED(cls, audio, **kwargs):
        import folder_paths

        try:
            return os.path.getmtime(folder_paths.get_annotated_filepath(audio))
        except Exception:
            return audio



class ResolveGrabbedVideo:
    """
    One video clip off the Resolve timeline, fetched by pressing the button.

    Park the playhead on a clip, press it, and about a second later that
    clip's picture — plus its sound, when it has any — is on the node as an
    mp4, ready to wire into any video workflow.

    Fetching takes real time, so doing it on every graph run would make every
    experiment slow. Press once, then iterate freely against the same clip.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                # `video_upload` is the flag ComfyUI's own Load Video sets: it
                # turns the combo into a picker with a "choose file to upload"
                # button. Uploads land in the input root whatever we ask for,
                # which is why the list covers that folder too.
                "video": (_input_files(VIDEO_SUFFIXES), {
                    "tooltip": "Press the button to grab one, or upload your own.",
                    "video_upload": True,
                }),
            },
            # `seconds` stays SECOND. ComfyUI stores a node's widget values as a
            # bare positional list, so inserting anything above it would silently
            # feed the old saved number into a new widget on every graph anyone
            # had already saved.
            "optional": {
                "seconds": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 86400.0, "step": 0.001}),
                "what": (
                    ["the clip at the playhead", "a clip by index"],
                    {"default": "the clip at the playhead",
                     "tooltip": "One clip is usually what you want."},
                ),
                "track": ("INT", {"default": 0, "min": 0, "max": 20,
                                  "tooltip": "0 means whichever enabled track has a clip "
                                             "under the playhead, topmost first."}),
                "clip_index": ("INT", {"default": 0, "min": 0, "max": 9999,
                                       "tooltip": "Only used by 'a clip by index'."}),
            },
        }

    RETURN_TYPES = ("VIDEO", "FLOAT")
    RETURN_NAMES = ("video", "seconds")
    FUNCTION = "load"
    CATEGORY = CATEGORY
    DESCRIPTION = ("One video clip grabbed off the Resolve timeline with the button on this "
                   "node. Does not touch Resolve when the graph runs.")

    def load(self, video, seconds=0.0, **_picked_by_the_button):
        import folder_paths

        if not video:
            raise RuntimeError("Nothing grabbed yet — press 'Grab the video' on the node.")
        path = folder_paths.get_annotated_filepath(video)
        if not path or not os.path.exists(path):
            path = os.path.join(folder_paths.get_input_directory(), video)
        if not os.path.exists(path):
            raise RuntimeError("That grabbed video is gone: %s" % video)

        try:
            from comfy_api.input_impl import VideoFromFile
        except ImportError as err:
            # Core's own Load Video imports this too, so when it is broken
            # nothing video-shaped works — say so plainly instead of a bare
            # ImportError.
            raise RuntimeError("ComfyUI's video support is broken in this install: %s" % err)
        return (VideoFromFile(path), float(seconds))

    @classmethod
    def IS_CHANGED(cls, video, **kwargs):
        import folder_paths

        try:
            return os.path.getmtime(folder_paths.get_annotated_filepath(video))
        except Exception:
            return video



class ResolveFrames:
    """
    One or two pictures for a workflow, from Resolve or from disk.

    Most video models want either a single starting frame or a first/last pair,
    so this covers both rather than making you pick a different node. Turn the
    second frame off and it behaves as a plain one-picture loader.

    Nothing here re-reads Resolve when the graph runs: a press pins the picture
    by filename, so tweaking a prompt ten times compares against the same frame
    instead of whatever the playhead happened to be over.
    """

    @classmethod
    def INPUT_TYPES(cls):
        files = _grabbed_files()
        return {
            "required": {
                "first_frame": (files, {"tooltip": "Grab it, or upload your own."}),
            },
            "optional": {
                "second_frame": ("BOOLEAN", {"default": True,
                                             "label_on": "two frames",
                                             "label_off": "one frame"}),
                "last_frame": (files, {"tooltip": "Only used when 'two frames' is on."}),
                "where": (["playhead", "clip first frame", "clip last frame"],
                          {"default": "playhead"}),
                "track": ("INT", {"default": 1, "min": 1, "max": 20}),
                "clip_index": ("INT", {"default": 0, "min": 0, "max": 9999}),
                "which": (["nearest the playhead", "by index"],
                          {"default": "nearest the playhead"}),
                "cut_index": ("INT", {"default": 0, "min": 0, "max": 9999}),
                "cut_duration": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 86400.0,
                                           "step": 0.001,
                                           "tooltip": "Set by 'Grab the cut': the gap between "
                                                      "those two frames, in seconds. Drive an "
                                                      "animation length with it."}),
            },
        }

    RETURN_TYPES = ("IMAGE", "IMAGE", "FLOAT")
    RETURN_NAMES = ("first frame", "last frame", "cut duration")
    FUNCTION = "load"
    CATEGORY = CATEGORY
    DESCRIPTION = ("One or two frames from DaVinci Resolve — the playhead, a clip edge, "
                   "both sides of a cut, or pictures you upload. 'Grab the cut' also pins "
                   "the gap between its two frames as a seconds output, for timing an "
                   "animation to fill it.")

    def load(self, first_frame, second_frame=True, last_frame=None, where="playhead",
             track=1, clip_index=0, which="nearest the playhead", cut_index=0,
             cut_duration=0.0):
        first = _load_grabbed(first_frame, "first frame")
        duration = float(cut_duration or 0.0)
        if not second_frame:
            # Nothing is wired to the second output in one-frame mode, so leaving
            # it empty is honest; inventing a duplicate would hide mistakes.
            return (first, None, duration)
        return (first, _load_grabbed(last_frame, "last frame"), duration)

    @classmethod
    def IS_CHANGED(cls, first_frame, second_frame=True, last_frame=None, cut_duration=0.0,
                   **kwargs):
        import folder_paths

        stamps = []
        for value in (first_frame, last_frame if second_frame else None):
            try:
                stamps.append(os.path.getmtime(folder_paths.get_annotated_filepath(value)))
            except Exception:
                stamps.append(value)
        stamps.append(float(cut_duration or 0.0))
        return str(stamps)


class ResolveLoadTimelineAudio:
    """The whole timeline's audio, mixed down."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {}}

    RETURN_TYPES = ("AUDIO", "FLOAT")
    RETURN_NAMES = ("audio", "seconds")
    FUNCTION = "load"
    CATEGORY = CATEGORY
    DESCRIPTION = "Mix the open timeline's audio down to one track."

    def load(self):
        from .secondunit import audio as A
        from .secondunit import convert

        _resolve()
        result = A.grab_timeline_audio(_capture_dir(), tag="timeline")
        if not result.get("success"):
            raise RuntimeError(result.get("error") or "Could not mix the timeline audio.")

        return (convert.file_to_audio(result["path"]), float(result.get("durationSeconds") or 0.0))


class ResolveTimelineInfo:
    """What is on the timeline right now."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {}}

    RETURN_TYPES = ("STRING", "FLOAT", "INT", "INT", "INT")
    RETURN_NAMES = ("summary", "fps", "playhead frame", "width", "height")
    FUNCTION = "read"
    CATEGORY = CATEGORY
    DESCRIPTION = "Frame rate, playhead, size and the list of cuts in the open timeline."

    def read(self, **kwargs):
        from .secondunit import cuts as C

        R, _ctx = _resolve()
        info = R.get_timeline_info()
        if not info.get("success"):
            raise RuntimeError(info.get("error") or "No timeline is open in Resolve.")

        fps = float(info.get("fps") or 24.0)
        found = C.find_cuts(info)
        lines = [
            "%s  %.3f fps  %sx%s" % (info.get("name"), fps, info.get("width"), info.get("height")),
            "playhead %s (frame %s)" % (info.get("playheadTimecode"), info.get("playheadFrame")),
            "%d cut(s):" % len(found),
        ]
        lines += ["  [%d] %s" % (i, C.describe(cut, fps)) for i, cut in enumerate(found)]

        return (
            "\n".join(lines),
            fps,
            int(info.get("playheadFrame") or 0),
            int(info.get("width") or 0),
            int(info.get("height") or 0),
        )





# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #



class ResolveSend:
    """
    One node to put whatever you made back into DaVinci Resolve.

    Wire any combination of picture, video and sound; each one that is connected
    gets written and imported. Everything is optional, so the same node serves a
    still, a clip, a voice track, or all three at once.

    Two ways to send:
      * leave 'send automatically' on and it goes as soon as the run finishes;
      * turn it off and press the button on the node when you are happy with it.
    The button re-sends what the last run produced, so you can audition a result
    before it touches your edit.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "place": (
                    ["media pool only", "at the playhead", "at the cut it came from", "at a time"],
                    {"default": "media pool only",
                     "tooltip": "Where it lands. 'at the cut it came from' uses the gap "
                                "the last Grab Cut came from, with no wiring needed."},
                ),
                "send_automatically": ("BOOLEAN", {"default": True,
                                                   "label_on": "on finish",
                                                   "label_off": "button only"}),
            },
            "optional": {
                "image": ("IMAGE",),
                "video": ("VIDEO",),
                "audio": ("AUDIO",),
                "seconds": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 86400.0, "step": 0.01}),
                "name": ("STRING", {"default": "second-unit"}),
                # Last on purpose. ComfyUI stores widget values as a bare
                # positional list, so anything added above `seconds` or `name`
                # would feed the wrong number into an already-saved graph.
                "subtitles": ("STRING", {
                    "forceInput": True,
                    "tooltip": "SubRip text — wire the 'srt' output of a transcription "
                               "node straight in. A path to an .srt works too.",
                }),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("result",)
    FUNCTION = "send"
    OUTPUT_NODE = True
    CATEGORY = CATEGORY
    DESCRIPTION = ("Save picture, video, sound and/or subtitles and send them to "
                   "DaVinci Resolve.")

    def send(self, place, send_automatically, image=None, video=None, audio=None,
             seconds=0.0, name="second-unit", subtitles=None):
        from .secondunit import convert

        written = []      # absolute paths, for sending
        preview = {}      # what the node shows, using ComfyUI's own preview shapes
        out = _output_dir()

        def seen(path, kind):
            """Describe a written file the way ComfyUI's /view route expects."""
            return {"filename": os.path.basename(path), "subfolder": "second-unit",
                    "type": "output", "kind": kind}

        if image is not None and len(image):
            # A batch is written frame by frame; importing only the first would
            # silently lose the rest of somebody's render.
            for index in range(len(image)):
                suffix = "" if len(image) == 1 else "_%02d" % (index + 1)
                path = os.path.join(out, _stamp(str(name) + suffix, ".png"))
                convert.image_to_file(image[index : index + 1], path)
                written.append(path)
                preview.setdefault("images", []).append(seen(path, "image"))

        if video is not None:
            path = os.path.join(out, _stamp(name, ".mp4"))
            video.save_to(path)
            written.append(path)
            # ComfyUI has no native preview shape for an mp4, so this node's own
            # JS builds a <video> from it.
            preview.setdefault("secondunit_video", []).append(seen(path, "video"))

        if audio is not None:
            path = os.path.join(out, _stamp(name, ".flac"))
            convert.audio_to_file(audio, path)
            written.append(path)
            # Exactly the entry SaveAudio emits — same shape, same player, so
            # saved sound shows up scrubbable instead of only on disk.
            preview.setdefault("audio", []).append({
                "filename": os.path.basename(path),
                "subfolder": "second-unit",
                "type": "output",
            })

        if subtitles and str(subtitles).strip():
            # Written unshifted. Where they land is decided at SEND time, by
            # rewriting the timecodes — see _send_subtitles — because the button
            # re-reads `place` and `seconds` after the run has finished.
            path = os.path.join(out, _stamp(name, ".srt"))
            _write_subtitles(subtitles, path)
            written.append(path)
            # And the preview SaveText emits, so the captions can be read right
            # on the node instead of only after opening the file. The file is
            # UTF-8 with a BOM (see subtitles.write); utf-8-sig takes it off.
            with open(path, encoding="utf-8-sig") as handle:
                preview.setdefault("text", []).append(handle.read().strip())

        if not written:
            raise RuntimeError(
                "Nothing is wired in — connect a picture, a video, some sound or subtitles."
            )

        # The button re-sends exactly these, so it must know them either way.
        info = {"paths": written, "place": place, "seconds": float(seconds)}

        if not send_automatically:
            note = "Saved %d file(s). Have a look, then press 'Send to Resolve'." % len(written)
            preview["secondunit"] = [dict(info, sent=False)]
            return {"ui": preview, "result": (note,)}

        try:
            messages = [_send(path, place, seconds) for path in written]
        except Exception as err:
            # Everything is already safe on disk, so a closed or unreachable
            # Resolve must not fail the run — saving is the node's first job.
            # The paths are recorded, so the 'Send to Resolve' button can push
            # them later, once Resolve is actually up.
            note = ("Saved %d file(s) to %s, but could not send them: %s"
                    % (len(written), out, err))
            preview["secondunit"] = [dict(info, sent=False)]
            return {"ui": preview, "result": (note,)}
        preview["secondunit"] = [dict(info, sent=True)]
        return {"ui": preview, "result": ("; ".join(messages),)}






class ResolveImportFile:
    """Send a file that already exists on disk back to Resolve."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "path": ("STRING", {"default": "", "tooltip": "Absolute path to a file."}),
                "place": (
                    ["media pool only", "at the playhead", "at a time"],
                    {"default": "media pool only"},
                ),
            },
            "optional": {
                "seconds": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 86400.0, "step": 0.01}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("result",)
    FUNCTION = "send"
    OUTPUT_NODE = True
    CATEGORY = CATEGORY
    DESCRIPTION = "Import any existing file into Resolve — pairs with ComfyUI's own Save nodes."

    def send(self, path, place, seconds=0.0):
        if not path or not os.path.exists(path):
            raise RuntimeError("No such file: %s" % path)
        return (_send(path, place, seconds),)


def _write_subtitles(text, path, offset_seconds=0.0):
    """SubRip text — or a path to some — onto disk, shifted by `offset_seconds`."""
    from .secondunit import subtitles as S

    source = str(text).strip()
    if S.looks_like_a_path(source):
        with open(source, encoding="utf-8-sig") as handle:
            source = handle.read()
    return S.write(source, path, offset_seconds)


def _subtitle_offset(R, place, seconds):
    """How far to move the captions, in seconds, for the chosen placement."""
    if place == "at a time":
        return float(seconds)

    if place == "at the cut it came from":
        from .secondunit import state

        cut = state.last_cut()
        if not cut:
            raise RuntimeError(
                "No cut has been grabbed yet. Press 'Grab the cut' first, or choose "
                "another placement."
            )
        return float(cut["seconds"])

    # At the playhead. Subtitle timecodes are measured from the START of the
    # timeline, not from frame zero, so the offset is the playhead's distance
    # from that start — get_timeline_info has already worked it out.
    info = R.get_timeline_info(include_items=False)
    if not info.get("success"):
        raise RuntimeError(info.get("error") or "No timeline is open in Resolve.")
    return float(info.get("playheadOffsetFrames") or 0) / float(info.get("fps") or 24.0)


def _send_subtitles(R, path, place, seconds):
    """
    Import an .srt, and put it on a subtitle track unless asked not to.

    Resolve will not position a subtitle clip — the cues land at their own
    timecodes, always. So a placement other than the head of the timeline is
    honoured by writing a SHIFTED copy alongside the original and importing
    that. The original stays untouched, so pressing the button again with a
    different `seconds` shifts from the real timings rather than compounding.
    """
    if place == "media pool only":
        imported = R.import_media(path)
        if not imported.get("success"):
            raise RuntimeError(imported.get("error") or "Resolve refused the subtitles.")
        return "Subtitles are in the media pool, under %s." % imported.get("bin", "Second Unit")

    offset = _subtitle_offset(R, place, seconds)
    if offset:
        with open(path, encoding="utf-8-sig") as handle:
            original = handle.read()
        stem, _ = os.path.splitext(path)
        shifted = "%s_at%s.srt" % (stem, ("%.3f" % offset).rstrip("0").rstrip(".").replace(".", "-"))
        _write_subtitles(original, shifted, offset)
        path = shifted

    added = R.add_to_timeline(file_path=path)
    if not added.get("success"):
        raise RuntimeError(added.get("error") or "Could not add the subtitles to the timeline.")

    where = " starting at %s" % added["timecode"] if added.get("timecode") else ""
    note = " Added a subtitle track for them." if added.get("createdTrack") else ""
    return "%d caption(s) on subtitle track %d%s.%s" % (
        added.get("captions", 0), added.get("trackIndex", 1), where, note,
    )


class SecondUnitPrompt:
    """
    Turn a short brief into a finished video prompt for Minimax or LTX.

    Works like ComfyUI's own Generate Text node: the `clip` must be a
    generate-capable text encoder (the Qwen or Gemma one from your
    Minimax/LTX workflow), and the writing runs on your GPU. Pick the
    model family and the mode, describe the shot in `idea`, and wire the
    finished text straight into your video workflow's prompt.

    `first_image` / `last_image` ground image-to-video and first/last-frame
    modes: the clip sees the exact boundary frames while it writes, so the
    motion it describes starts and lands where your frames are.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "clip": ("CLIP", {
                    "tooltip": "A text encoder that can write: the Qwen or Gemma "
                               "one from your Minimax/LTX workflow.",
                }),
                "model": (["minimax", "ltx"], {
                    "tooltip": "Which prompt style to write in.",
                }),
                "mode": (["text to video", "image to video", "first and last frame"], {
                    "tooltip": "Text to video invents the shot. The other two "
                               "read the wired frames while writing.",
                }),
                "idea": ("STRING", {
                    "multiline": True, "default": "",
                    "tooltip": "The shot in your own words. Put exact spoken lines in "
                               "\"quotes\" and they survive word for word.",
                }),
            },
            "optional": {
                "first_image": ("IMAGE", {
                    "tooltip": "First frame, or a batch of reference images. Read by the clip "
                               "for image to video and first/last frame modes.",
                }),
                "last_image": ("IMAGE", {
                    "tooltip": "Last frame. Only used by first and last frame mode.",
                }),
                "duration": ("FLOAT", {
                    "default": 5.0, "min": 1.0, "max": 60.0, "step": 0.5,
                    "tooltip": "How long the video will be. Sizes the detail to match.",
                }),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xFFFFFFFFFFFFFFFF}),
                "max_length": ("INT", {"default": 512, "min": 1, "max": 4096}),
                "temperature": ("FLOAT", {"default": 0.7, "min": 0.0, "max": 2.0, "step": 0.01}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("prompt",)
    FUNCTION = "write"
    CATEGORY = CATEGORY
    DESCRIPTION = ("Expand a short brief into a finished Minimax or LTX video "
                   "prompt, using the wired text encoder.")

    def write(self, clip, model, mode, idea, first_image=None, last_image=None,
              duration=5.0, seed=0, max_length=512, temperature=0.7):
        import re

        import torch

        from .secondunit import prompts as P

        if not (idea or "").strip() and first_image is None and last_image is None:
            raise RuntimeError("Describe the shot in 'idea' first.")

        system, user = P.build(
            model, mode, idea, duration=duration,
            has_first=first_image is not None, has_last=last_image is not None,
        )
        full_text = system + "\n\n" + user

        frames = [t for t in (first_image, last_image) if t is not None]
        tokenize_kwargs = {}
        if frames:
            tokenize_kwargs["image"] = torch.cat(frames, dim=0)
        try:
            tokens = clip.tokenize(full_text, **tokenize_kwargs)
        except TypeError:
            # A text-only encoder does not take frames; the brief alone
            # still gets written.
            tokens = clip.tokenize(full_text)

        try:
            generated = clip.generate(
                tokens,
                do_sample=float(temperature) > 0,
                max_length=int(max_length),
                temperature=float(temperature),
                top_k=64, top_p=0.95, min_p=0.05, repetition_penalty=1.05,
                seed=int(seed),
            )
        except (AttributeError, TypeError) as err:
            raise RuntimeError(
                "This text encoder cannot write prompts. Load the Qwen or Gemma "
                "text encoder from your Minimax/LTX workflow into 'clip'."
            ) from err

        text = clip.decode(generated)
        text = re.sub(r"<think>.*?(?:</think>|$)", "", text, flags=re.DOTALL).strip()
        if not text:
            text = (idea or "").strip()

        # The encoder did its job; move everything off the GPU so the video
        # model has the whole card. ComfyUI reloads on demand next run.
        import comfy.model_management as model_management

        model_management.unload_all_models()
        model_management.soft_empty_cache()

        preview = {"text": [text]}
        return {"ui": preview, "result": (text,)}


def _send(path, place, seconds):
    """Import, then optionally place. Shared by every output node."""
    R, _ctx = _resolve()

    if path.lower().endswith(".srt"):
        return _send_subtitles(R, path, place, seconds)

    from .secondunit import convert

    # Resolve takes the video but not every audio codec inside an mp4, so a
    # file whose audio is not mp3 goes in as a remuxed copy: video copied,
    # audio mp3. Anything else comes back untouched.
    path, audio_note = convert.ensure_mp3_audio(path)

    def done(message):
        if not audio_note:
            return message
        return "%s (%s)." % (message.rstrip("."), audio_note)

    imported = R.import_media(path)
    if not imported.get("success"):
        raise RuntimeError(imported.get("error") or "Resolve refused the file.")

    if place == "media pool only":
        return done("In the media pool, under %s." % imported.get("bin", "Second Unit"))

    if place == "at the cut it came from":
        from .secondunit import state

        cut = state.last_cut()
        if not cut:
            raise RuntimeError(
                "No cut has been grabbed yet. Press 'Grab the cut' first, or choose "
                "another placement."
            )
        seconds = cut["seconds"]
        place = "at a time"

    # These are the PYTHON parameter names of resolve.add_to_timeline, not the
    # JSON keys the old HTTP bridge used. Confusing the two is what broke this.
    options = {"file_path": path}
    if place == "at a time":
        options["start_seconds"] = float(seconds)

    added = R.add_to_timeline(**options)
    if not added.get("success"):
        raise RuntimeError(added.get("error") or "Could not add it to the timeline.")

    # add_to_timeline says plainly when it had to land somewhere other than asked;
    # passing that straight through is the difference between a trustworthy node
    # and one that quietly puts a clip in the wrong place.
    if added.get("note"):
        return done("Added, but: %s" % added["note"])
    return done("Added at %s." % (added.get("timecode") or "the playhead"))


NODE_CLASS_MAPPINGS = {
    "ResolveFrames": ResolveFrames,
    "ResolveGrabbedAudio": ResolveGrabbedAudio,
    "ResolveGrabbedVideo": ResolveGrabbedVideo,
    "ResolveLoadTimelineAudio": ResolveLoadTimelineAudio,
    "ResolveTimelineInfo": ResolveTimelineInfo,
    "ResolveSend": ResolveSend,
    "ResolveImportFile": ResolveImportFile,
    "SecondUnitPrompt": SecondUnitPrompt,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "ResolveFrames": "Grab Frames from Resolve",
    "ResolveGrabbedAudio": "Grab Audio from Resolve",
    "ResolveGrabbedVideo": "Grab Video from Resolve",
    "ResolveLoadTimelineAudio": "Load Timeline Audio from Resolve (on run)",
    "ResolveTimelineInfo": "Resolve Timeline Info",
    "ResolveSend": "Send to Resolve",
    "ResolveImportFile": "Import File into Resolve",
    "SecondUnitPrompt": "Write Video Prompt",
}
