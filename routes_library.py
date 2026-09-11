"""
HTTP routes for the media library.

Everything the library panel does goes through here. Two rules run through the
whole file:

  * Every path from the browser is library-RELATIVE and is resolved through
    `library.resolve_rel`, which rejects anything that escapes the library. The
    browser never names an absolute path and is never trusted with one.
  * Anything that touches the disk, ffmpeg, yt-dlp or Resolve runs in a thread.
    ComfyUI serves the whole UI from this one event loop; a two-second Resolve
    call on it freezes the graph for everybody.
"""

import asyncio
import functools
import os
import shutil

try:
    from aiohttp import web
    from server import PromptServer

    _SERVER = PromptServer.instance
except Exception:  # pragma: no cover - older or embedded ComfyUI
    _SERVER = None

from .secondunit import library as L

UPLOAD_CHUNK = 1 << 18  # 256 KB


async def _off_loop(fn, *args, **kwargs):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, functools.partial(fn, *args, **kwargs))


async def _body(request):
    try:
        return await request.json()
    except Exception:
        return {}


def _fail(err, status=400):
    return web.json_response({"error": str(err)}, status=status)


def _register():
    if _SERVER is None:
        return

    routes = _SERVER.routes

    # -------------------------------------------------------------- #
    # Reading
    # -------------------------------------------------------------- #

    @routes.get("/secondunit/library")
    async def library_tree(request):
        try:
            from .secondunit import fetch

            data = await _off_loop(L.tree)
            data["ytdlp"] = fetch.available()
            return web.json_response(data)
        except Exception as err:
            return _fail(err, 500)

    @routes.get("/secondunit/library/items")
    async def library_items(request):
        query = request.query.get("q", "").strip()
        try:
            if query:
                found = await _off_loop(L.search, query)
            else:
                found = await _off_loop(
                    L.items,
                    request.query.get("category") or None,
                    request.query.get("group") or None,
                )
            # The absolute path is the panel's business only for display; it is
            # never accepted back from the browser.
            for item in found:
                item.pop("file", None)
            return web.json_response({"items": found})
        except Exception as err:
            return _fail(err, 500)

    @routes.get("/secondunit/library/file")
    async def library_file(request):
        """The media itself. FileResponse handles Range, so video can seek."""
        try:
            full = L.resolve_rel(request.query.get("path", ""))
        except Exception as err:
            return _fail(err, 403)
        if not os.path.isfile(full):
            return _fail("That file is gone.", 404)
        return web.FileResponse(full, headers={"Cache-Control": "no-cache"})

    @routes.get("/secondunit/library/thumb")
    async def library_thumb(request):
        rel = request.query.get("path", "")
        try:
            L.resolve_rel(rel)
            made = await _off_loop(L.make_thumb, rel)
        except Exception as err:
            return _fail(err, 403)
        if not made:
            return web.json_response({"error": "no thumbnail"}, status=404)
        return web.FileResponse(made, headers={"Cache-Control": "max-age=60"})

    # -------------------------------------------------------------- #
    # Shelves
    # -------------------------------------------------------------- #

    @routes.post("/secondunit/library/category")
    async def library_category(request):
        body = await _body(request)
        try:
            name = await _off_loop(L.create_category, body.get("name"), body.get("type") or "video")
            return web.json_response({"ok": True, "name": name})
        except Exception as err:
            return _fail(err)

    @routes.post("/secondunit/library/group")
    async def library_group(request):
        body = await _body(request)
        try:
            name = await _off_loop(L.create_group, body.get("category"), body.get("name"))
            return web.json_response({"ok": True, "name": name})
        except Exception as err:
            return _fail(err)

    @routes.post("/secondunit/library/delete_folder")
    async def library_delete_folder(request):
        body = await _body(request)
        try:
            await _off_loop(L.delete_folder, body.get("category"), body.get("group") or None)
            return web.json_response({"ok": True})
        except Exception as err:
            return _fail(err)

    @routes.post("/secondunit/library/root")
    async def library_root(request):
        body = await _body(request)
        try:
            chosen = await _off_loop(L.set_root, body.get("root"))
            return web.json_response({"ok": True, "root": chosen})
        except Exception as err:
            return _fail(err)

    # -------------------------------------------------------------- #
    # Adding
    # -------------------------------------------------------------- #

    @routes.post("/secondunit/library/upload")
    async def library_upload(request):
        """
        Streamed, deliberately.

        `request.post()` would be four lines shorter and would also refuse every
        file over --max-upload-size (100 MB by default) — which is most video.
        Reading the multipart stream ourselves writes straight to disk with that
        limit out of the way and nothing large held in memory.
        """
        category = None
        group = None
        added = []
        failed = []

        try:
            reader = await request.multipart()
        except Exception as err:
            return _fail(err)

        try:
            while True:
                part = await reader.next()
                if part is None:
                    break

                if part.name == "category":
                    category = (await part.text()).strip() or None
                    continue
                if part.name == "group":
                    group = (await part.text()).strip() or None
                    continue
                if not part.filename:
                    await part.read()
                    continue

                if not category:
                    failed.append({"name": part.filename, "error": "Pick a category first."})
                    await part.read()
                    continue

                suffix = os.path.splitext(part.filename)[1].lower()
                if suffix not in L.MEDIA_SUFFIXES:
                    failed.append({"name": part.filename, "error": "Not a media file."})
                    await part.read()
                    continue

                # Land it beside the library first, then hand it to add_file,
                # which owns naming and never overwrites.
                scratch = os.path.join(L.ensure(), ".secondunit", "incoming")
                os.makedirs(scratch, exist_ok=True)
                staged = os.path.join(scratch, "%d%s" % (id(part), suffix))

                try:
                    with open(staged, "wb") as handle:
                        while True:
                            chunk = await part.read_chunk(UPLOAD_CHUNK)
                            if not chunk:
                                break
                            handle.write(chunk)

                    item = await _off_loop(
                        L.add_file,
                        category,
                        group,
                        staged,
                        os.path.splitext(os.path.basename(part.filename))[0],
                        True,
                    )
                    item.pop("file", None)
                    added.append(item)
                except Exception as err:
                    failed.append({"name": part.filename, "error": str(err)})
                finally:
                    if os.path.exists(staged):
                        os.unlink(staged)

            return web.json_response({"ok": True, "added": added, "failed": failed})
        except Exception as err:
            return _fail(err, 500)

    @routes.post("/secondunit/library/link")
    async def library_link(request):
        body = await _body(request)
        category = body.get("category")
        group = body.get("group") or None
        if not category:
            return _fail("Pick a category first.")

        def work():
            from .secondunit import fetch

            downloaded, scratch = fetch.fetch(body.get("url"), body.get("mode") or "video")
            try:
                return L.add_file(category, group, downloaded, None, True)
            finally:
                shutil.rmtree(scratch, ignore_errors=True)

        try:
            item = await _off_loop(work)
            item.pop("file", None)
            return web.json_response({"ok": True, "item": item})
        except Exception as err:
            return _fail(err)

    # -------------------------------------------------------------- #
    # Item actions
    # -------------------------------------------------------------- #

    @routes.post("/secondunit/library/rename")
    async def library_rename(request):
        body = await _body(request)
        try:
            item = await _off_loop(L.rename, body.get("path"), body.get("name"))
            item.pop("file", None)
            return web.json_response({"ok": True, "item": item})
        except Exception as err:
            return _fail(err)

    @routes.post("/secondunit/library/move")
    async def library_move(request):
        body = await _body(request)
        try:
            item = await _off_loop(L.move, body.get("path"), body.get("category"), body.get("group") or None)
            item.pop("file", None)
            return web.json_response({"ok": True, "item": item})
        except Exception as err:
            return _fail(err)

    @routes.post("/secondunit/library/delete")
    async def library_delete(request):
        body = await _body(request)
        try:
            await _off_loop(L.delete, body.get("path"))
            return web.json_response({"ok": True})
        except Exception as err:
            return _fail(err)

    # -------------------------------------------------------------- #
    # Out of the library
    # -------------------------------------------------------------- #

    @routes.post("/secondunit/library/send")
    async def library_send(request):
        """Into Resolve: the media pool, or straight onto the timeline."""
        body = await _body(request)
        place = body.get("place") or "media pool only"
        try:
            full = L.resolve_rel(body.get("path"))
        except Exception as err:
            return _fail(err, 403)
        if not os.path.isfile(full):
            return _fail("That file is gone.", 404)

        try:
            from .nodes import _send

            message = await _off_loop(_send, full, place, 0.0)
            return web.json_response({"ok": True, "message": message})
        except Exception as err:
            return _fail(err, 500)

    @routes.post("/secondunit/library/to_input")
    async def library_to_input(request):
        """
        A copy in ComfyUI's input folder, so a loader node can use it.

        Copied rather than referenced: ComfyUI's loaders only look inside their
        own input directory, and a workflow that pointed at the library would
        break the moment the file was renamed or moved.
        """
        body = await _body(request)
        try:
            full = L.resolve_rel(body.get("path"))
        except Exception as err:
            return _fail(err, 403)
        if not os.path.isfile(full):
            return _fail("That file is gone.", 404)

        def work():
            import folder_paths

            folder = os.path.join(folder_paths.get_input_directory(), "second-unit")
            os.makedirs(folder, exist_ok=True)
            target = L.unique_path(os.path.join(folder, os.path.basename(full)))
            shutil.copy2(full, target)
            return os.path.basename(target)

        try:
            name = await _off_loop(work)
            return web.json_response({"ok": True, "name": "second-unit/" + name, "type": L.kind_of(full)})
        except Exception as err:
            return _fail(err, 500)


_register()
