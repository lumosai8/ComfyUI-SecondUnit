"""
Moving pictures between Resolve's world (files on disk) and ComfyUI's (tensors).

ComfyUI's IMAGE is a float32 tensor shaped [batch, height, width, channels] with
values in 0..1. Everything here is lazily imported: torch and numpy belong to
ComfyUI, not to us, and the pack must still import on a machine where a node has
simply never been run.
"""

import os


def file_to_image(path):
    """Load a PNG/JPEG from disk as a single-frame ComfyUI IMAGE tensor."""
    import numpy as np
    import torch
    from PIL import Image, ImageOps

    if not path or not os.path.exists(path):
        raise RuntimeError("No such image: %s" % path)

    with Image.open(path) as opened:
        # Honour EXIF rotation, then drop alpha — a grabbed frame is opaque and
        # a stray alpha channel breaks models expecting three channels.
        opened = ImageOps.exif_transpose(opened)
        rgb = opened.convert("RGB")
        array = np.array(rgb).astype(np.float32) / 255.0

    return torch.from_numpy(array)[None, ...]


def image_to_file(image, path):
    """
    Write the FIRST frame of an IMAGE tensor to disk as PNG.

    Only the first: a Resolve still is one picture, and silently importing a
    whole batch would litter the media pool.
    """
    import numpy as np
    from PIL import Image

    if image is None or len(image) == 0:
        raise RuntimeError("There is no picture to save.")

    array = image[0].detach().cpu().numpy()
    array = np.clip(array * 255.0, 0, 255).astype(np.uint8)

    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    Image.fromarray(array).save(path, compress_level=4)
    return path


def audio_to_file(audio, path):
    """
    Write a ComfyUI AUDIO dict ({waveform, sample_rate}) to a WAV.

    Not with torchaudio — see `file_to_audio` for why.
    """
    import numpy as np

    if not audio or "waveform" not in audio:
        raise RuntimeError("There is no sound to save.")

    waveform = audio["waveform"]
    if hasattr(waveform, "dim") and waveform.dim() == 3:  # [batch, channels, samples]
        waveform = waveform[0]
    samples = waveform.detach().cpu().numpy() if hasattr(waveform, "detach") else np.asarray(waveform)
    if samples.ndim == 1:
        samples = samples[None, :]
    rate = int(audio.get("sample_rate", 44100))

    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)

    try:
        import soundfile as sf

        # FLOAT (32-bit PCM) only exists in WAV/AIFF; FLAC tops out at PCM_24.
        # The node writes .flac, so an unconditional FLOAT is an invalid
        # combination and soundfile refuses the whole write.
        ext = os.path.splitext(str(path))[1].lower()
        subtype = "PCM_24" if ext == ".flac" else "FLOAT"
        sf.write(path, samples.T.astype("float32"), rate, subtype=subtype)
        return path
    except ImportError:
        pass

    _write_wav(path, samples, rate)
    return path


def _write_wav(path, samples, rate):
    """
    A 32-bit float WAV, by hand.

    Twenty lines of struct beats depending on a fourth audio library. Float is
    the honest choice for something coming out of a model — no clipping and no
    dither decision — and Resolve, ffmpeg and every editor read it.
    """
    import struct

    import numpy as np

    data = np.ascontiguousarray(samples.T.astype("<f4")).tobytes()
    channels = int(samples.shape[0])
    block = channels * 4

    with open(path, "wb") as handle:
        handle.write(b"RIFF")
        handle.write(struct.pack("<I", 36 + len(data)))
        handle.write(b"WAVEfmt ")
        handle.write(struct.pack("<IHHIIHH", 16, 3, channels, rate, rate * block, block, 32))
        handle.write(b"data")
        handle.write(struct.pack("<I", len(data)))
        handle.write(data)


def file_to_audio(path):
    """
    Load audio from disk as a ComfyUI AUDIO dict.

    Deliberately NOT torchaudio. From 2.9 its `load` is a thin wrapper around
    torchcodec, which is a compiled extension pinned to one torch build and to
    the system's libavutil. Get either wrong and every audio node in ComfyUI
    dies with `undefined symbol: torch_from_blob` or a missing `libavutil.so.57`
    — measured on this machine, where torchaudio 2.10 could not read a plain mp3
    that soundfile read in a millisecond.

    So: soundfile first, then ffmpeg (which this pack already needs and already
    knows how to run safely), and torchaudio last of all.
    """
    import numpy as np
    import torch

    if not path or not os.path.exists(path):
        raise RuntimeError("No such audio: %s" % path)

    samples, rate = _decode(path)
    if samples.size == 0:
        raise RuntimeError("That audio file is empty: %s" % os.path.basename(path))

    # ComfyUI wants [batch, channels, samples], float32.
    tensor = torch.from_numpy(np.ascontiguousarray(samples, dtype=np.float32))
    return {"waveform": tensor.unsqueeze(0), "sample_rate": int(rate)}


def _decode(path):
    """Returns (channels-first float array, sample rate). Tries three ways."""
    import numpy as np

    trouble = []

    try:
        import soundfile as sf

        data, rate = sf.read(path, dtype="float32", always_2d=True)
        return data.T, rate
    except Exception as err:
        trouble.append("soundfile: %s" % err)

    try:
        return _decode_with_ffmpeg(path)
    except Exception as err:
        trouble.append("ffmpeg: %s" % err)

    try:
        import torchaudio

        waveform, rate = torchaudio.load(path)
        return waveform.numpy(), rate
    except Exception as err:
        trouble.append("torchaudio: %s" % err)

    raise RuntimeError(
        "Could not read %s. Tried three decoders:\n  %s"
        % (os.path.basename(path), "\n  ".join(trouble))
    )


def _decode_with_ffmpeg(path):
    """Decode to raw 32-bit float PCM and read it straight into an array."""
    import json
    import subprocess

    import numpy as np

    from . import ffmpeg as our_ffmpeg

    probe = our_ffmpeg.run(
        [our_ffmpeg.find("ffprobe"), "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=sample_rate,channels", "-of", "json", path],
        timeout=60,
    )
    stream = (json.loads(probe.stdout or b"{}").get("streams") or [{}])[0]
    rate = int(stream.get("sample_rate") or 44100)
    channels = int(stream.get("channels") or 1)

    result = subprocess.run(
        [our_ffmpeg.find("ffmpeg"), "-hide_banner", "-loglevel", "error",
         "-i", path, "-f", "f32le", "-acodec", "pcm_f32le", "-"],
        capture_output=True,
        env=our_ffmpeg.env_for(our_ffmpeg.find("ffmpeg")),
        timeout=1800,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or b"").decode("utf-8", "replace").strip()[-200:])

    flat = np.frombuffer(result.stdout, dtype="<f4")
    usable = (flat.size // channels) * channels
    return flat[:usable].reshape(-1, channels).T, rate


# Containers whose video Resolve takes but whose audio it is picky about.
_RESOLVE_AUDIO_SUFFIXES = (".mp4", ".m4v", ".mov")


def ensure_mp3_audio(path):
    """(path to import, note). Video untouched, audio mp3 if it was not.

    A file with no audio, or whose audio is already mp3, goes through as-is.
    Anything else is remuxed next to the original: every stream copied, only
    the audio re-encoded to mp3. The converted copy is reused while it is
    newer than the source, so repeated sends do not re-encode.
    Never raises — the original is always a safe fallback.
    """
    try:
        if not path or not os.path.exists(path):
            return path, ""
        if os.path.splitext(path)[1].lower() not in _RESOLVE_AUDIO_SUFFIXES:
            return path, ""
        audio = [codec for kind, codec in _stream_codecs(path) if kind == "audio"]
        if not audio or all(codec == "mp3" for codec in audio):
            return path, ""
        stem, ext = os.path.splitext(path)
        converted = "%s_mp3%s" % (stem, ext)
        if (os.path.exists(converted)
                and os.path.getmtime(converted) >= os.path.getmtime(path)
                and _is_all_mp3(converted)):
            return converted, ""
        _remux_mp3(path, converted)
        return converted, "audio converted to mp3, video untouched"
    except Exception:
        return path, ""


def _stream_codecs(path):
    """[(codec_type, codec_name)] for every stream, via ffprobe."""
    import json

    from . import ffmpeg as our_ffmpeg

    probe = our_ffmpeg.run(
        [our_ffmpeg.find("ffprobe"), "-v", "error",
         "-show_entries", "stream=codec_type,codec_name",
         "-of", "json", path],
        timeout=60,
    )
    if probe.returncode != 0:
        raise RuntimeError("ffprobe would not read the file")
    streams = json.loads(probe.stdout or b"{}").get("streams") or []
    return [(s.get("codec_type") or "", s.get("codec_name") or "") for s in streams]


def _is_all_mp3(path):
    try:
        audio = [codec for kind, codec in _stream_codecs(path) if kind == "audio"]
    except Exception:
        return False
    return bool(audio) and all(codec == "mp3" for codec in audio)


def _remux_mp3(src, dst):
    """Copy every stream, re-encode only the audio to mp3."""
    from . import ffmpeg as our_ffmpeg

    last = ""
    for encoder in ("libmp3lame", "mp3"):
        result = our_ffmpeg.run(
            [our_ffmpeg.find("ffmpeg"), "-hide_banner", "-loglevel", "error",
             "-y", "-i", src, "-map", "0", "-c", "copy",
             "-c:a", encoder, "-b:a", "192k", dst],
            timeout=1800,
        )
        if result.returncode == 0 and os.path.exists(dst):
            return
        last = (result.stderr or b"").decode("utf-8", "replace").strip()[-200:]
        try:
            os.remove(dst)
        except OSError:
            pass
    raise RuntimeError(last or "ffmpeg refused the remux")
