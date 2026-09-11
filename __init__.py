"""
Second Unit — DaVinci Resolve inside ComfyUI.

Grab frames and audio out of the open Resolve timeline, run them through any
workflow, and send the result back to the media pool or straight onto the
timeline.

No bridge, no server, no port: the nodes run inside ComfyUI's own Python and
talk to Resolve through its scripting module directly. Install is a git clone.
"""

from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

# Registers /secondunit/* so the node's button can grab without running the
# workflow. Guarded inside: an import failure here must not take ComfyUI down.
try:
    from . import routes  # noqa: F401
except Exception as _err:  # pragma: no cover
    print('[Second Unit] grab button unavailable:', _err)

# The media library panel. Same rule: it must never take ComfyUI down with it.
try:
    from . import routes_library  # noqa: F401
except Exception as _err:  # pragma: no cover
    print('[Second Unit] media library unavailable:', _err)

WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
__version__ = "0.1.0"
