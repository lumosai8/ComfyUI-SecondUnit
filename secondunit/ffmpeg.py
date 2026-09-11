"""
Finding an ffmpeg that actually runs, from inside DaVinci Resolve.

This exists because of one specific, deeply confusing failure. Resolve ships its
own FFmpeg libraries in `/opt/resolve/libs` and puts that directory on
`LD_LIBRARY_PATH` for everything it launches. The system ffmpeg then loads
Resolve's `libavutil.so.58` instead of the distribution's, and dies before it
renders a single frame:

    /usr/bin/ffmpeg: symbol lookup error: /lib/x86_64-linux-gnu/libswresample.so.4:
    undefined symbol: av_bessel_i0, version LIBAVUTIL_58

The binary is fine. Run it from a terminal and it works; run it from Resolve and
it does not. Since the ffmpeg tier is the last fallback for grabbing a frame —
the gallery tier is refused by Resolve Studio and the Fusion tier needs a comp —
this took out frame grabbing entirely on an otherwise healthy machine.

So: strip the loader variables for any binary that lives outside Resolve's own
tree, and prove the binary runs before trusting it.
"""

import os
import shutil
import subprocess

# Where to look when PATH does not help. Resolve's own copy comes first: if it
# exists it is guaranteed to match the libraries Resolve is already loading.
CANDIDATES = {
    "ffmpeg": (
        "/opt/resolve/bin/ffmpeg",
        "/usr/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/opt/homebrew/bin/ffmpeg",
        "/snap/bin/ffmpeg",
    ),
    "ffprobe": (
        "/opt/resolve/bin/ffprobe",
        "/usr/bin/ffprobe",
        "/usr/local/bin/ffprobe",
        "/opt/homebrew/bin/ffprobe",
        "/snap/bin/ffprobe",
    ),
}

# Loader variables Resolve sets that make a system binary load Resolve's copies
# of libav*. Harmless to drop: a binary outside Resolve's tree never wants them.
LOADER_VARS = (
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
    "DYLD_LIBRARY_PATH",
    "DYLD_INSERT_LIBRARIES",
    "DYLD_FRAMEWORK_PATH",
)

_cache = {}


def env_for(binary_path):
    """
    A child environment that will not poison this binary.

    Resolve's own ffmpeg is left alone — it is built against those libraries and
    needs them. Everything else gets the loader variables removed.
    """
    env = dict(os.environ)
    if not str(binary_path).startswith("/opt/resolve"):
        for key in LOADER_VARS:
            env.pop(key, None)
    return env


def _runs(path):
    """Does this binary actually start? A broken one must not be chosen."""
    try:
        result = subprocess.run(
            [path, "-hide_banner", "-version"],
            capture_output=True,
            timeout=20,
            env=env_for(path),
        )
        return result.returncode == 0
    except Exception:
        return False


def find(name="ffmpeg"):
    """
    The first candidate that runs.

    Verified rather than assumed: `shutil.which` finding something on PATH says
    nothing about whether it will survive the environment Resolve hands it.
    """
    if name in _cache:
        return _cache[name]

    seen = []
    on_path = shutil.which(name)
    if on_path:
        seen.append(on_path)
    for candidate in CANDIDATES.get(name, ()):
        if candidate not in seen and os.path.exists(candidate):
            seen.append(candidate)

    for path in seen:
        if _runs(path):
            _cache[name] = path
            return path

    if seen:
        raise RuntimeError(
            name
            + " is installed but will not run here ("
            + ", ".join(seen)
            + "). Resolve's own libraries usually shadow it; this build strips "
            + "LD_LIBRARY_PATH, so the binary itself is likely broken."
        )
    raise RuntimeError(name + " is not installed")


def run(command, **kwargs):
    """subprocess.run for an ffmpeg command, always with a safe environment."""
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("env", env_for(command[0]))
    return subprocess.run(command, **kwargs)
