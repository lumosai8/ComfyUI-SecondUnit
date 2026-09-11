"""
Second Unit — DaVinci Resolve inside ComfyUI.

The Resolve layer is imported lazily by the nodes, never at module import time,
so the pack loads cleanly in a ComfyUI that has never seen Resolve.
"""
