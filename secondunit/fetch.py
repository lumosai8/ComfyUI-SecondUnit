"""
Pulling media in from a link, with yt-dlp.

Downloads land in a scratch folder and only move into the library once they are
whole. A failed or half-finished download must never leave a broken file sitting
in someone's collection.

yt-dlp is optional. Without it the "From a link" button simply says so.
"""

import os
import shutil
import subprocess
import tempfile

VIDEO_FORMAT = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"

_available = None


def binary():
    return shutil.which("yt-dlp") or shutil.which("yt_dlp")


def available():
    global _available
    if _available is None:
        _available = bool(binary())
    return _available


def _friendly(message):
    text = (message or "").strip().splitlines()
    line = text[-1] if text else ""
    lowered = line.lower()
    if "unsupported url" in lowered:
        return "That site is not supported."
    if "private" in lowered or "sign in" in lowered or "age" in lowered:
        return "That video is private or needs a sign-in."
    if "unavailable" in lowered or "removed" in lowered:
        return "That video is no longer available."
    return line[:200] or "The download failed."


def fetch(url, mode="video"):
    """
    Download and return the path of the finished file in a scratch folder.

    The caller moves it into the library and then deletes `scratch`.
    """
    if not str(url or "").lower().startswith(("http://", "https://")):
        raise ValueError("That does not look like a web link.")

    tool = binary()
    if not tool:
        raise RuntimeError("yt-dlp is not installed, so links cannot be downloaded.")

    scratch = tempfile.mkdtemp(prefix="second-unit-fetch-")
    args = [tool]
    if mode == "audio":
        args += ["-x", "--audio-format", "mp3", "--audio-quality", "0"]
    else:
        args += ["-f", VIDEO_FORMAT]
    args += [
        "--no-playlist",
        "--no-warnings",
        "--no-progress",
        "-o",
        os.path.join(scratch, "%(title).120s.%(ext)s"),
        url,
    ]

    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=1800)
    except subprocess.TimeoutExpired:
        shutil.rmtree(scratch, ignore_errors=True)
        raise RuntimeError("That download took too long and was stopped.")
    except Exception as err:
        shutil.rmtree(scratch, ignore_errors=True)
        raise RuntimeError(str(err))

    if result.returncode != 0:
        shutil.rmtree(scratch, ignore_errors=True)
        raise RuntimeError(_friendly(result.stderr or result.stdout))

    files = [
        os.path.join(scratch, name)
        for name in os.listdir(scratch)
        if os.path.isfile(os.path.join(scratch, name)) and os.path.getsize(os.path.join(scratch, name)) > 0
    ]
    if not files:
        shutil.rmtree(scratch, ignore_errors=True)
        raise RuntimeError("Nothing was downloaded from that link.")

    # Largest wins: yt-dlp leaves subtitle and thumbnail files behind too.
    files.sort(key=os.path.getsize, reverse=True)
    return files[0], scratch
