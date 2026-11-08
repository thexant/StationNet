"""Disk-backed ASGI request bodies for the existing video upload forms."""
import asyncio
import tempfile

from uvicorn.middleware.wsgi import WSGIResponder, build_environ

from .db import get_station
from .videos import upload_request_limit


def is_video_upload(scope):
    path = scope.get("path", "")
    root = scope.get("root_path", "")
    if root and path.startswith(root):
        path = path[len(root):]
    return scope.get("method") == "POST" and path in {"/videos/upload", "/videos/station-upload"}


async def _finish(future):
    """Let a running disk/WSGI operation finish before its resources are closed."""
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(future)
            break
        except asyncio.CancelledError:
            if future.cancelled():
                raise
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
    return result


async def _disk(function, *args):
    return await _finish(asyncio.create_task(asyncio.to_thread(function, *args)))


async def receive_video_upload(flask_app, executor, scope, receive, send):
    def configured_maximum():
        with flask_app.app_context():
            station = get_station()
            return station["videos_max_upload_mb"] if station else 120

    async def reject(status, message):
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
        await send({"type": "http.response.body", "body": message.encode(), "more_body": False})

    max_upload_mb = await _disk(configured_maximum)
    limit = upload_request_limit(max_upload_mb)
    too_large = f"Video upload is too large. Maximum video file size: {max_upload_mb} MB."
    lengths = [value for key, value in scope.get("headers", []) if key.lower() == b"content-length"]
    try:
        declared = int(lengths[0]) if lengths else None
        if len(lengths) > 1 or (declared is not None and declared < 0):
            raise ValueError
    except ValueError:
        await reject(400, "Invalid upload request length.")
        return
    if declared is not None and declared > limit:
        await reject(413, too_large)
        return

    body = None

    def open_body():
        nonlocal body
        body = tempfile.TemporaryFile(mode="w+b")

    try:
        try:
            await _disk(open_body)
            size = 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                size += len(chunk)
                if size > limit:
                    await reject(413, too_large)
                    return
                if declared is not None and size > declared:
                    await reject(400, "Upload request length does not match its body.")
                    return
                await _disk(body.write, chunk)
                if not message.get("more_body", False):
                    break
            if declared is not None and size != declared:
                await reject(400, "Upload request length does not match its body.")
                return
            await _disk(body.seek, 0)
        except OSError:
            await reject(507, "Unable to stage video upload. Check temporary disk space.")
            return

        environ = build_environ(scope, {"type": "http.request"}, body)
        environ["CONTENT_LENGTH"] = str(size)

        def closing_app(environ, start_response):
            response = flask_app(environ, start_response)
            try:
                yield from response
            finally:
                close = getattr(response, "close", None)
                if close is not None:
                    close()

        responder = WSGIResponder(closing_app, executor, scope)
        worker = asyncio.get_running_loop().run_in_executor(
            executor, responder.wsgi, environ, responder.start_response)
        sender = asyncio.create_task(responder.sender(send))
        try:
            await _finish(worker)
        finally:
            responder.send_queue.append(None)
            responder.send_event.set()
            await _finish(sender)
        if responder.exc_info is not None:
            raise responder.exc_info[0].with_traceback(responder.exc_info[1], responder.exc_info[2])
    finally:
        if body is not None:
            await _disk(body.close)
