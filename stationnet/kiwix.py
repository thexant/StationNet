"""Optional local Kiwix reader, served inside the StationNet shell."""
import atexit
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from flask import Blueprint, Response, abort, current_app, g, render_template, request

bp = Blueprint("kiwix", __name__, url_prefix="/wiki")
ROOT = "/wiki/content"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = build_opener(_NoRedirect)


class KiwixServer:
    def __init__(self, data_dir, executable):
        self.data_dir = Path(data_dir)
        self.executable = executable
        self.lock = threading.Lock()
        self.process = None
        self.port = None
        self.signature = None
        atexit.register(self.stop)

    def stop(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                self.process.terminate()
            self.process = None
            self.port = None

    def ready(self):
        with self.lock:
            files = sorted(self.data_dir.glob("*.zim"))
            if not files:
                return False, "No ZIM files found in the station's kiwix_data folder."
            signature = tuple((file.name, file.stat().st_size, file.stat().st_mtime_ns)
                              for file in files)
            if self.process and self.process.poll() is None and signature == self.signature:
                return True, None
            if self.process and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
            self.process = None
            executable = shutil.which(self.executable)
            if not executable:
                return False, "kiwix-serve is not installed or is not on the server PATH."
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            try:
                self.process = subprocess.Popen(
                    [executable, "--address=127.0.0.1", f"--port={port}",
                     f"--urlRootLocation={ROOT}", str(self.data_dir)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except OSError:
                return False, "Could not start kiwix-serve."
            self.port = port
            self.signature = signature
            for _ in range(30):
                if self.process.poll() is not None:
                    return False, "kiwix-serve stopped while opening the ZIM library."
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=.1):
                        return True, None
                except OSError:
                    time.sleep(.1)
            return False, "Kiwix is starting. Refresh this page in a few seconds."


def init_app(app):
    app.extensions["kiwix"] = KiwixServer(
        app.config["KIWIX_DATA_DIR"], app.config["KIWIX_SERVE_EXECUTABLE"]
    )


def _enabled():
    if not g.station or not g.station["kiwix_enabled"]:
        abort(404)


@bp.get("/")
def index():
    _enabled()
    ready, error = current_app.extensions["kiwix"].ready()
    return render_template("wiki.html", ready=ready, error=error,
                           kiwix_dir=current_app.config["KIWIX_DATA_DIR"])


@bp.route("/content/", defaults={"path": ""}, methods=["GET", "HEAD"])
@bp.route("/content/<path:path>", methods=["GET", "HEAD"])
def content(path):
    _enabled()
    server = current_app.extensions["kiwix"]
    ready, _ = server.ready()
    if not ready:
        abort(503)
    url = f"http://127.0.0.1:{server.port}{ROOT}/" + quote(path, safe="/@:+")
    if request.query_string:
        url += "?" + request.query_string.decode("latin-1")
    upstream_request = Request(url, method=request.method)
    try:
        upstream = _opener.open(upstream_request, timeout=30)
    except HTTPError as error:
        upstream = error
    except URLError:
        abort(503)
    location = upstream.headers.get("Location")
    if location:
        target = urlsplit(urljoin(url, location))
        if target.netloc != f"127.0.0.1:{server.port}":
            upstream.close()
            abort(502)
        if target.scheme and target.scheme != "http":
            upstream.close()
            abort(502)
        if target.path != ROOT and not target.path.startswith(ROOT + "/"):
            upstream.close()
            abort(502)
    headers = {}
    for name in ("Content-Type", "Content-Encoding", "Content-Disposition", "Content-Range"):
        if upstream.headers.get(name):
            headers[name] = upstream.headers[name]
    if location:
        headers["Location"] = (target.path or "") + ("?" + target.query if target.query else "")
    if request.method == "HEAD":
        status = upstream.status
        upstream.close()
        return Response(status=status, headers=headers)
    # Keep all Kiwix assets under the same origin while allowing its viewer to
    # run inside the themed StationNet page.
    def chunks():
        try:
            while block := upstream.read(65536):
                yield block
        finally:
            upstream.close()
    return Response(chunks(), status=upstream.status, headers=headers)
