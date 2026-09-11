"""
Run Resolve scripting in Resolve's own interpreter, never in ours.

Why this file exists: DaVinci Resolve's `fusionscript` client crashes with a
native access violation when loaded into ComfyUI portable's Python 3.13
(`ExtensionFileLoader.exec_module` dies inside `PyInit_fusionscript` — no
try/except can catch it, Windows kills the whole server). It works fine under
the Python Resolve ships itself (3.10, embedded in `fuscript`).

So every Resolve call runs as a short-lived
`fuscript -l py3 resolve_helper.py <in.json> <out.json>` subprocess and comes
back as plain JSON. ComfyUI's Python version stops mattering — portable,
venv, 3.10 or 3.13 — and a missing/closed Resolve is a normal error dict,
never a fatal exit.

Short-lived is deliberate: Resolve serialises external scripting, and a
persistent second client throttles every call to ~1/sec for everybody. Spawn,
act, exit — no idle handle is left behind.
"""

import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile

HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resolve_helper.py")

# Remembered for the life of this process: some builds implement ExportStills
# as a stub that always returns False. Skipping a tier we know is dead saves a
# Color-page flip on every grab.
_GALLERY_DEAD = False

_FUSCRIPT = None
_FUSCRIPT_PROBED = False


def _candidates():
    found = []
    if sys.platform.startswith("win"):
        program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        program_files_x86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        program_data = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        for base in (program_files, program_files_x86):
            found.append(os.path.join(base, "Blackmagic Design", "DaVinci Resolve", "fuscript.exe"))
            found.append(os.path.join(base, "Blackmagic Design", "DaVinci Resolve Studio", "fuscript.exe"))
        # A Modules dir on disk implies a Resolve install next to it; look for
        # the interpreter that belongs to it before giving up.
        for modules in (
            os.path.join(program_data, "Blackmagic Design", "DaVinci Resolve",
                         "Support", "Developer", "Scripting", "Modules"),
            os.environ.get("RESOLVE_SCRIPT_API", ""),
        ):
            if modules:
                root = os.path.abspath(os.path.join(modules, os.pardir, os.pardir, os.pardir))
                for _ in range(3):
                    found.append(os.path.join(root, "fuscript.exe"))
                    root = os.path.abspath(os.path.join(root, os.pardir))
    elif sys.platform == "darwin":
        found += [
            "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fuscript",
            "/Applications/DaVinci Resolve Studio/DaVinci Resolve.app/Contents/Libraries/Fusion/fuscript",
            "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/fuscript",
        ]
    else:
        found += [
            # Real Studio for Linux layout first; the entries below are kept
            # as fallbacks for distro/custom installs.
            "/opt/resolve/libs/Fusion/fuscript",
            "/opt/resolve/bin/fuscript",
            "/opt/resolve/Developer/Scripting/fuscript",
            "/home/resolve/Developer/Scripting/fuscript",
        ]
    return [p for p in found if p]


def _known_modules_dirs():
    """Wherever Resolve's scripting Modules may live (all platforms)."""
    dirs = []
    env_api = os.environ.get("RESOLVE_SCRIPT_API", "").strip().strip('"')
    if env_api:
        # May point at the Modules dir itself or at its parent.
        dirs.append(env_api)
        dirs.append(os.path.join(env_api, "Modules"))
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
                 "/home/resolve/Developer/Scripting/Modules"]
    env_lib = os.environ.get("RESOLVE_SCRIPT_LIB", "").strip().strip('"')
    if env_lib:
        dirs.append(env_lib if os.path.isdir(env_lib) else os.path.dirname(env_lib))
    seen = set()
    unique = []
    for path in dirs:
        if path and path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def _derived_candidates():
    """fuscript locations derived from the install itself, not hardcoded.

    Covers custom install prefixes: walk up from any known Modules dir and
    from the `resolve` binary on PATH, checking each level's usual layouts.
    Appended AFTER the platform defaults, so machines that already resolved
    keep returning the exact same path as before.
    """
    found = []
    sublayouts = (
        "",
        "bin",
        os.path.join("libs", "Fusion"),
        os.path.join("Contents", "Libraries", "Fusion"),
        os.path.join("Developer", "Scripting"),
    )
    exe_names = ("fuscript", "fuscript.exe")

    def walk_up(start, levels=5):
        root = os.path.abspath(start)
        for _ in range(levels):
            for sub in sublayouts:
                base = os.path.join(root, sub) if sub else root
                for name in exe_names:
                    found.append(os.path.join(base, name))
            parent = os.path.dirname(root)
            if parent == root:
                break
            root = parent

    for modules in _known_modules_dirs():
        if modules:
            walk_up(modules)
    for binary in ("resolve", "resolve.exe"):
        on_path = shutil.which(binary)
        if on_path:
            walk_up(os.path.dirname(os.path.abspath(on_path)), levels=3)

    if not sys.platform.startswith("win"):
        # Bounded globs for renamed/alternate prefixes (only existing paths
        # are returned by glob, so this is cheap and never guesses wrong).
        for pattern in (
            "/opt/resolve*/libs/Fusion/fuscript",
            "/opt/resolve*/bin/fuscript",
            "/opt/DaVinci*/libs/Fusion/fuscript",
            "/usr/local/bin/fuscript",
            "/Applications/DaVinci Resolve*.app/Contents/Libraries/Fusion/fuscript",
        ):
            try:
                found.extend(sorted(glob.glob(pattern)))
            except Exception:
                pass
    return [p for p in found if p]


def find_fuscript():
    """Absolute path of Resolve's scripting interpreter, or None."""
    global _FUSCRIPT, _FUSCRIPT_PROBED
    if _FUSCRIPT_PROBED:
        # A late-mounted drive or moved install must not stick forever.
        if _FUSCRIPT and os.path.isfile(_FUSCRIPT):
            return _FUSCRIPT
        if _FUSCRIPT is None:
            return None
        _FUSCRIPT_PROBED = False

    override = os.environ.get("SECONDUNIT_FUSCRIPT", "").strip().strip('"')
    if override:
        if os.path.isdir(override):
            for name in ("fuscript.exe", "fuscript"):
                candidate = os.path.join(override, name)
                if os.path.isfile(candidate):
                    _FUSCRIPT = candidate
                    _FUSCRIPT_PROBED = True
                    return _FUSCRIPT
        elif os.path.isfile(override):
            _FUSCRIPT = override
            _FUSCRIPT_PROBED = True
            return _FUSCRIPT

    for name in ("fuscript.exe", "fuscript"):
        on_path = shutil.which(name)
        if on_path and os.path.isfile(on_path):
            _FUSCRIPT = on_path
            _FUSCRIPT_PROBED = True
            return _FUSCRIPT

    for candidate in _candidates():
        if candidate and os.path.isfile(candidate):
            _FUSCRIPT = candidate
            _FUSCRIPT_PROBED = True
            return _FUSCRIPT

    # Custom install prefix: derive from the install itself instead of
    # guessing more hardcoded paths.
    for candidate in _derived_candidates():
        if candidate and os.path.isfile(candidate):
            _FUSCRIPT = candidate
            _FUSCRIPT_PROBED = True
            return _FUSCRIPT

    _FUSCRIPT = None
    _FUSCRIPT_PROBED = True
    return None


def available():
    return find_fuscript() is not None


def explain():
    """What to tell the user when no scripting interpreter was found."""
    if sys.platform.startswith("win"):
        where = r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fuscript.exe"
    elif sys.platform == "darwin":
        where = "/Applications/DaVinci Resolve/DaVinci Resolve.app/.../Fusion/fuscript"
    else:
        where = "/opt/resolve/libs/Fusion/fuscript"
    return (
        "Could not find Resolve's scripting interpreter (looked for %s). "
        "Install DaVinci Resolve Studio, or point SECONDUNIT_FUSCRIPT at fuscript "
        "if Resolve lives somewhere unusual." % where
    )


def gallery_dead():
    return _GALLERY_DEAD


def note_gallery_dead():
    global _GALLERY_DEAD
    _GALLERY_DEAD = True


def call(op, payload=None, timeout=120):
    """
    Run one helper op, return its result dict. Never raises, never crashes:
    every failure mode (no interpreter, timeout, Resolve closed) is a dict
    with success=False / connected=False so callers can report it.
    """
    payload = dict(payload or {})
    payload["op"] = op
    if op in ("grab_playhead", "grab_edge") and _GALLERY_DEAD:
        payload["skip_gallery"] = True

    exe = find_fuscript()
    if not exe:
        return {"success": False, "connected": False, "error": explain()}
    # Tells the helper where its own interpreter lives so it can find the
    # scripting Modules next to it even for custom install prefixes.
    payload.setdefault("fuscript_dir", os.path.dirname(os.path.abspath(exe)))
    if not os.path.isfile(HELPER):
        return {"success": False, "connected": False,
                "error": "The Resolve helper is missing: %s" % HELPER}

    try:
        with tempfile.TemporaryDirectory(prefix="second-unit-") as scratch:
            in_path = os.path.join(scratch, "in.json")
            out_path = os.path.join(scratch, "out.json")
            with open(in_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            try:
                proc = subprocess.run(
                    [exe, "-l", "py3", HELPER, in_path, out_path],
                    capture_output=True,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                return {"success": False, "connected": False,
                        "error": "Resolve did not answer within %ds." % int(timeout)}
            except Exception as err:
                return {"success": False, "connected": False,
                        "error": "Could not start Resolve scripting: %s" % err}

            try:
                with open(out_path, "r", encoding="utf-8") as handle:
                    result = json.load(handle)
            except Exception:
                detail = ((proc.stderr or b"").decode("utf-8", "replace") or
                          (proc.stdout or b"").decode("utf-8", "replace") or "").strip()
                detail = "\n".join(detail.splitlines()[-3:])[:300]
                return {"success": False, "connected": False,
                        "error": "Resolve scripting failed (exit %s). %s"
                                 % (proc.returncode, detail or "No output.").strip()}

            if isinstance(result, dict) and result.get("gallery_dead"):
                note_gallery_dead()
            return result if isinstance(result, dict) else {
                "success": False, "connected": False,
                "error": "Resolve returned something unreadable."}
    except Exception as err:
        return {"success": False, "connected": False, "error": str(err)}
