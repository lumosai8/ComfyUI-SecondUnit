"""
The media library.

Clips, music and voice takes kept in one place, one click from the timeline.

THE FOLDERS ARE THE TRUTH. The Electron version kept a JSON index listing every
item with an absolute path, and that index was wrong the moment anything moved:
renaming the app's data folder left every path in it pointing at a directory
that no longer existed, while the files sat there perfectly fine. So this port
reads the disk on every call and keeps only what the disk cannot tell us —
whether a category is video or sound, and when a file was added — in a small
side file. Delete that file and you lose nothing but the dates.

On disk:

    <root>/<Category>/clip.mp4
    <root>/<Category>/<Group>/clip.mp4
    <root>/.thumbs/<hash>.jpg
    <root>/.secondunit/index.json

which means the library stays browsable in a file manager, and dropping files
in from outside is a perfectly good way to add them.
"""

import hashlib
import json
import os
import re
import shutil
import sys
import time

VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mxf"}
AUDIO_SUFFIXES = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}

MEDIA_SUFFIXES = VIDEO_SUFFIXES | AUDIO_SUFFIXES | IMAGE_SUFFIXES

# Created on a fresh library. The same six the app shipped with, so anyone
# moving over lands in a library that looks like the one they left.
DEFAULT_CATEGORIES = (
    ("General", "video", ("Clips",)),
    ("B-Roll", "video", ("Nature", "City", "People")),
    ("Backgrounds", "video", ()),
    ("Music", "audio", ("Calm", "Upbeat")),
    ("Sound effects", "audio", ()),
    ("Voice", "audio", ()),
)

DEFAULT_KINDS = {name: kind for name, kind, _groups in DEFAULT_CATEGORIES}

PRIVATE = (".thumbs", ".secondunit")


# ------------------------------------------------------------------ #
# Where the library lives
# ------------------------------------------------------------------ #

def _user_data_dir():
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~\\AppData\\Roaming")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return base


def _config_file():
    """Beside ComfyUI's own user data, so an update to this pack cannot eat it."""
    try:
        import folder_paths

        base = folder_paths.get_user_directory()
    except Exception:
        base = os.path.join(_user_data_dir(), "second-unit")
    path = os.path.join(base, "second-unit")
    os.makedirs(path, exist_ok=True)
    return os.path.join(path, "library.json")


def _default_root():
    return os.path.join(_user_data_dir(), "second-unit", "Library")


def root():
    """
    The library folder, in order of preference: the environment, the saved
    setting, then the default. An existing library from the Electron app is
    adopted rather than ignored — same layout, same files.
    """
    from_env = os.environ.get("SECONDUNIT_LIBRARY")
    if from_env:
        return os.path.abspath(os.path.expanduser(from_env))

    try:
        with open(_config_file(), "r", encoding="utf-8") as handle:
            saved = json.load(handle).get("root")
        if saved:
            return os.path.abspath(os.path.expanduser(saved))
    except Exception:
        pass

    chosen = _default_root()
    if not os.path.isdir(chosen):
        # The app before the rename. If that is the only one on disk, use it.
        legacy = os.path.join(_user_data_dir(), "davinci-generator", "Library")
        if os.path.isdir(legacy):
            return legacy
    return chosen


def set_root(path):
    clean = os.path.abspath(os.path.expanduser(str(path or "").strip()))
    if not clean:
        raise ValueError("Give it a folder.")
    os.makedirs(clean, exist_ok=True)
    with open(_config_file(), "w", encoding="utf-8") as handle:
        json.dump({"root": clean}, handle, indent=2)
    return clean


# ------------------------------------------------------------------ #
# Paths — every one of these has to be checked
# ------------------------------------------------------------------ #

def _inside(path, base):
    """
    Containment by real path, never by prefix string.

    "/library-old/secret.mp4".startswith("/library") is True, which is exactly
    the bug this avoids. Symlinks are resolved first so a link planted in the
    library cannot reach outside it either.
    """
    try:
        real = os.path.realpath(path)
        home = os.path.realpath(base)
        return os.path.commonpath([real, home]) == home
    except Exception:
        return False


def resolve_rel(rel):
    """A library-relative path turned into an absolute one, or an error."""
    text = str(rel or "")
    if "\0" in text:
        raise ValueError("Bad path.")
    # The library's own bookkeeping is not media and is nobody's to fetch.
    if any(part.startswith(".") for part in re.split(r"[/\\]", text) if part):
        raise ValueError("That is not a library item.")
    base = root()
    full = os.path.abspath(os.path.join(base, text.lstrip("/\\")))
    if not _inside(full, base):
        raise ValueError("That is outside the library.")
    return full


def safe_name(name):
    """Strip anything a filesystem would object to."""
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", str(name))
    clean = re.sub(r"\s+", " ", clean).strip(" .")
    return clean[:120]


def unique_path(candidate):
    """Never overwrite: "clip.mp4" becomes "clip (2).mp4"."""
    if not os.path.exists(candidate):
        return candidate
    stem, suffix = os.path.splitext(candidate)
    for n in range(2, 999):
        nxt = "%s (%d)%s" % (stem, n, suffix)
        if not os.path.exists(nxt):
            return nxt
    return "%s-%d%s" % (stem, int(time.time()), suffix)


def kind_of(path):
    suffix = os.path.splitext(path)[1].lower()
    if suffix in AUDIO_SUFFIXES:
        return "audio"
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in VIDEO_SUFFIXES:
        return "video"
    return "other"


# ------------------------------------------------------------------ #
# The side file: what the disk cannot tell us
# ------------------------------------------------------------------ #

def _meta_file():
    path = os.path.join(root(), ".secondunit")
    os.makedirs(path, exist_ok=True)
    return os.path.join(path, "index.json")


def _read_meta():
    try:
        with open(_meta_file(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("kinds", {})
    data.setdefault("added", {})
    return data


def _write_meta(data):
    target = _meta_file()
    tmp = target + ".tmp-%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    os.replace(tmp, target)
    return data


def _remember(rel):
    data = _read_meta()
    if rel not in data["added"]:
        data["added"][rel] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _write_meta(data)


def _forget(rel):
    data = _read_meta()
    if data["added"].pop(rel, None) is not None:
        _write_meta(data)


# ------------------------------------------------------------------ #
# Reading the library
# ------------------------------------------------------------------ #

def ensure():
    """Create the library on first use. Never touches an existing one."""
    base = root()
    os.makedirs(base, exist_ok=True)
    os.makedirs(os.path.join(base, ".thumbs"), exist_ok=True)

    if _categories_on_disk():
        return base

    meta = _read_meta()
    for name, kind, groups in DEFAULT_CATEGORIES:
        os.makedirs(os.path.join(base, name), exist_ok=True)
        for group in groups:
            os.makedirs(os.path.join(base, name, group), exist_ok=True)
        meta["kinds"][name] = kind
    _write_meta(meta)
    return base


def _order(name):
    """
    The six that ship keep the order they shipped in; anything the user adds
    lands after them, alphabetically. Sorting purely by name would open the
    library on "Backgrounds", which is nobody's main shelf.
    """
    order = [entry[0] for entry in DEFAULT_CATEGORIES]
    return (order.index(name) if name in order else len(order), name.lower())


def _categories_on_disk():
    base = root()
    if not os.path.isdir(base):
        return []
    names = []
    for name in os.listdir(base):
        if name.startswith(".") or name in PRIVATE:
            continue
        if os.path.isdir(os.path.join(base, name)):
            names.append(name)
    return sorted(names, key=_order)


def _files_in(folder):
    """Media files directly inside a folder — subfolders are groups, not items."""
    out = []
    try:
        entries = sorted(os.listdir(folder))
    except OSError:
        return out
    for name in entries:
        if name.startswith("."):
            continue
        full = os.path.join(folder, name)
        if os.path.isfile(full) and os.path.splitext(name)[1].lower() in MEDIA_SUFFIXES:
            out.append(full)
    return out


def _groups_in(folder):
    out = []
    try:
        entries = sorted(os.listdir(folder))
    except OSError:
        return out
    for name in entries:
        if name.startswith("."):
            continue
        if os.path.isdir(os.path.join(folder, name)):
            out.append(name)
    return out


def _kind_of_category(name, meta, files):
    """
    Video or sound? Whichever the user said, else what the name says, else what
    is actually in there.

    The guess matters because an EMPTY category has nothing to go on, and the
    six that ship by default arrive empty — without the name check "Voice" and
    "Sound effects" would sit under Video until the first file landed in them.
    """
    saved = meta["kinds"].get(name)
    if saved in ("audio", "video"):
        return saved
    if name in DEFAULT_KINDS:
        return DEFAULT_KINDS[name]
    kinds = {kind_of(f) for f in files}
    if kinds and kinds <= {"audio"}:
        return "audio"
    return "video"


def tree():
    """The rail: categories, their groups, and how much is in each."""
    base = ensure()
    meta = _read_meta()
    categories = []

    for name in _categories_on_disk():
        folder = os.path.join(base, name)
        loose = _files_in(folder)
        groups = []
        total = len(loose)
        for group in _groups_in(folder):
            files = _files_in(os.path.join(folder, group))
            groups.append({"name": group, "count": len(files)})
            total += len(files)

        everything = list(loose)
        for group in _groups_in(folder):
            everything += _files_in(os.path.join(folder, group))
        kind = _kind_of_category(name, meta, everything)

        categories.append({"name": name, "type": kind, "count": total, "groups": groups})

    return {"root": base, "categories": categories}


def _describe(full, category, group):
    base = root()
    rel = os.path.relpath(full, base).replace(os.sep, "/")
    meta = _read_meta()
    try:
        stat = os.stat(full)
        size = stat.st_size
        mtime = stat.st_mtime
    except OSError:
        size = 0
        mtime = 0

    added = meta["added"].get(rel)
    if not added:
        added = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(mtime))

    return {
        "id": rel,
        "path": rel,
        "file": full,
        "name": os.path.splitext(os.path.basename(full))[0],
        "type": kind_of(full),
        "size": size,
        "added": added,
        "category": category,
        "group": group,
    }


def items(category=None, group=None):
    base = ensure()
    if not category:
        return []
    folder = os.path.join(base, category, group) if group else os.path.join(base, category)
    if not _inside(folder, base) or not os.path.isdir(folder):
        return []
    return [_describe(f, category, group) for f in _files_in(folder)]


def search(query):
    """Everything, flattened, matching the words typed — in any order."""
    base = ensure()
    words = [w for w in str(query or "").lower().split() if w]
    if not words:
        return []

    found = []
    for category in _categories_on_disk():
        folder = os.path.join(base, category)
        for full in _files_in(folder):
            found.append(_describe(full, category, None))
        for group in _groups_in(folder):
            for full in _files_in(os.path.join(folder, group)):
                found.append(_describe(full, category, group))

    def matches(item):
        haystack = (item["name"] + " " + item["category"] + " " + (item["group"] or "")).lower()
        return all(word in haystack for word in words)

    return [item for item in found if matches(item)][:400]


# ------------------------------------------------------------------ #
# Changing the library
# ------------------------------------------------------------------ #

def create_category(name, kind="video"):
    base = ensure()
    clean = safe_name(name)
    if not clean:
        raise ValueError("Give it a name.")
    folder = os.path.join(base, clean)
    if os.path.isdir(folder):
        raise ValueError("You already have a category called that.")
    os.makedirs(folder)
    meta = _read_meta()
    meta["kinds"][clean] = "audio" if kind == "audio" else "video"
    _write_meta(meta)
    return clean


def create_group(category, name):
    base = ensure()
    clean = safe_name(name)
    if not clean:
        raise ValueError("Give it a name.")
    folder = resolve_rel(os.path.join(safe_name(category), clean))
    if os.path.isdir(folder):
        raise ValueError("That group already exists.")
    os.makedirs(folder)
    return clean


def delete_folder(category, group=None):
    """Deleting a category deletes its files. The caller confirms first."""
    base = ensure()
    rel = category if not group else os.path.join(category, group)
    folder = resolve_rel(rel)
    if os.path.realpath(folder) == os.path.realpath(base):
        raise ValueError("That is the library itself.")
    if not os.path.isdir(folder):
        raise ValueError("Already gone.")

    for full, item_rel in _walk(folder):
        _drop_thumb(item_rel)
        _forget(item_rel)
    shutil.rmtree(folder)

    if not group:
        meta = _read_meta()
        if meta["kinds"].pop(category, None) is not None:
            _write_meta(meta)
    return True


def _walk(folder):
    base = root()
    for dirpath, _dirs, files in os.walk(folder):
        for name in files:
            full = os.path.join(dirpath, name)
            yield full, os.path.relpath(full, base).replace(os.sep, "/")


def add_file(category, group, source, name=None, move=False):
    """Copy (or move) a file in. Returns the new item."""
    base = ensure()
    if not os.path.isfile(source):
        raise ValueError("That file is gone.")

    folder = resolve_rel(os.path.join(category, group) if group else category)
    os.makedirs(folder, exist_ok=True)

    suffix = os.path.splitext(source)[1]
    stem = safe_name(name or os.path.splitext(os.path.basename(source))[0]) or "clip"
    target = unique_path(os.path.join(folder, stem + suffix))

    if move:
        try:
            os.replace(source, target)
        except OSError:
            shutil.copy2(source, target)
            os.unlink(source)
    else:
        shutil.copy2(source, target)

    item = _describe(target, category, group)
    _remember(item["id"])
    return item


def add_bytes(category, group, filename, data):
    """A file uploaded from the browser."""
    base = ensure()
    folder = resolve_rel(os.path.join(category, group) if group else category)
    os.makedirs(folder, exist_ok=True)

    suffix = os.path.splitext(filename)[1]
    if suffix.lower() not in MEDIA_SUFFIXES:
        raise ValueError("Second Unit does not know what to do with a %s file." % (suffix or "?"))

    stem = safe_name(os.path.splitext(os.path.basename(filename))[0]) or "clip"
    target = unique_path(os.path.join(folder, stem + suffix))
    with open(target, "wb") as handle:
        handle.write(data)

    item = _describe(target, category, group)
    _remember(item["id"])
    return item


def rename(rel, name):
    full = resolve_rel(rel)
    if not os.path.isfile(full):
        raise ValueError("That item is gone.")
    clean = safe_name(name)
    if not clean:
        raise ValueError("Give it a name.")

    suffix = os.path.splitext(full)[1]
    target = unique_path(os.path.join(os.path.dirname(full), clean + suffix))
    os.replace(full, target)

    _drop_thumb(rel)
    _forget(rel)
    base = root()
    new_rel = os.path.relpath(target, base).replace(os.sep, "/")
    _remember(new_rel)
    parts = new_rel.split("/")
    return _describe(target, parts[0], parts[1] if len(parts) > 2 else None)


def move(rel, category, group=None):
    base = ensure()
    full = resolve_rel(rel)
    if not os.path.isfile(full):
        raise ValueError("That item is gone.")

    folder = resolve_rel(os.path.join(category, group) if group else category)
    os.makedirs(folder, exist_ok=True)
    target = unique_path(os.path.join(folder, os.path.basename(full)))

    try:
        os.replace(full, target)
    except OSError:
        # A different filesystem: copy, then remove the original.
        shutil.copy2(full, target)
        os.unlink(full)

    _drop_thumb(rel)
    _forget(rel)
    new_rel = os.path.relpath(target, base).replace(os.sep, "/")
    _remember(new_rel)
    return _describe(target, category, group)


def delete(rel):
    full = resolve_rel(rel)
    if not os.path.isfile(full):
        raise ValueError("Already gone.")
    os.unlink(full)
    _drop_thumb(rel)
    _forget(rel)
    return True


# ------------------------------------------------------------------ #
# Thumbnails
# ------------------------------------------------------------------ #

def thumb_path(rel):
    """
    Keyed on the path INSIDE the library, not the basename.

    Two clips both called "intro.mp4" in different folders shared one picture in
    the version this is ported from. Keyed on the relative path they cannot, and
    unlike the app's absolute-path key, moving the whole library does not
    invalidate every thumbnail at once.
    """
    digest = hashlib.sha1(rel.encode("utf-8")).hexdigest()[:16]
    folder = os.path.join(root(), ".thumbs")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, digest + ".jpg")


def _drop_thumb(rel):
    try:
        os.unlink(thumb_path(rel))
    except OSError:
        pass


def make_thumb(rel):
    """
    Returns a jpg path, or None for audio and anything ffmpeg cannot read.

    Blocking — call it off the event loop.
    """
    full = resolve_rel(rel)
    kind = kind_of(full)
    if kind in ("audio", "other"):
        return None

    target = thumb_path(rel)
    if os.path.exists(target) and os.path.getsize(target) > 0:
        return target

    from . import ffmpeg

    try:
        binary = ffmpeg.find("ffmpeg")
    except Exception:
        return None

    # A second in is a better picture than frame zero, which is often black —
    # but on a very short clip that lands past the end, so try the start too.
    seeks = [None] if kind == "image" else ["00:00:01", "00:00:00"]
    for seek in seeks:
        command = [binary, "-hide_banner", "-loglevel", "error"]
        if seek:
            command += ["-ss", seek]
        command += ["-i", full, "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4", "-y", target]
        try:
            ffmpeg.run(command, timeout=25)
        except Exception:
            continue
        if os.path.exists(target) and os.path.getsize(target) > 0:
            return target
    return None
