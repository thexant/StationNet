"""Optional local Kiwix reader with StationNet library and article pages."""
import atexit
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from xml.etree import ElementTree
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from flask import Blueprint, Response, abort, current_app, g, render_template, request, url_for

bp = Blueprint("kiwix", __name__, url_prefix="/wiki")
ROOT = "/wiki/content"
INTERACTIVE_QUERY = "stationnet-interactive"


def find_executable(override=None):
    if override:
        return shutil.which(override)
    app_root = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    tools_dir = app_root / "kiwix_tools"
    name = "kiwix-serve.exe" if sys.platform == "win32" else "kiwix-serve"
    bundled = next(tools_dir.rglob(name), None) if tools_dir.is_dir() else None
    return str(bundled) if bundled else shutil.which(name)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = build_opener(_NoRedirect)


class KiwixServer:
    def __init__(self, data_dir, executable, legacy_dir=None):
        self.data_dir = Path(data_dir)
        self.legacy_dir = Path(legacy_dir) if legacy_dir else None
        self.executable = executable
        self.lock = threading.Lock()
        self.process = None
        self.port = None
        self.signature = None
        self.output_file = None
        self.diagnostic = None
        self.registered_count = 0
        atexit.register(self.stop)

    def _close_output(self):
        if self.output_file:
            self.output_file.close()
            self.output_file = None

    def _read_output(self):
        if not self.output_file:
            return None
        self.output_file.seek(0, 2)
        self.output_file.seek(max(0, self.output_file.tell() - 8192))
        return self.output_file.read().decode("utf-8", errors="replace").strip() or None

    def stop(self):
        with self.lock:
            self._stop_locked()

    def _stop_locked(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process = None
        self.port = None
        self.registered_count = 0
        self._close_output()

    def _files(self):
        # Older stations stored archives beside data/. Move them into data/ so
        # ordinary station backups carry the entire collection on restore.
        self.data_dir.mkdir(parents=True, exist_ok=True)
        current_files = [p for p in self.data_dir.iterdir()
                         if p.is_file() and p.suffix.lower() == ".zim"]
        if not current_files and self.legacy_dir and self.legacy_dir.is_dir():
            legacy_files = [p for p in self.legacy_dir.iterdir()
                            if p.is_file() and p.suffix.lower() == ".zim"]
            for source in legacy_files:
                source.rename(self.data_dir / source.name)
        return sorted((p for p in self.data_dir.iterdir()
                       if p.is_file() and p.suffix.lower() == ".zim"), key=lambda p: p.name.lower())

    def _catalog_count(self, port):
        url = f"http://127.0.0.1:{port}{ROOT}/catalog/v2/entries?count=-1"
        with _opener.open(Request(url), timeout=2) as response:
            feed = ElementTree.fromstring(response.read())
        return sum(element.tag.rsplit("}", 1)[-1] == "entry" for element in feed)

    def ready(self):
        with self.lock:
            try:
                files = self._files()
                signature = tuple((file.name, file.stat().st_size, file.stat().st_mtime_ns)
                                  for file in files)
            except (OSError, RuntimeError) as error:
                self.diagnostic = str(error)
                return False, "The station's WIKI folder could not be read."
            if not files:
                self._stop_locked()
                self.signature = None
                self.diagnostic = None
                return False, "No ZIM files found in the station's WIKI folder."
            if (self.process and self.process.poll() is None and signature == self.signature
                    and self.registered_count):
                try:
                    with socket.create_connection(("127.0.0.1", self.port), timeout=.1):
                        return True, None
                except OSError:
                    return False, "Kiwix is starting. Refresh this page in a few seconds."
            self._stop_locked()
            self.diagnostic = None
            try:
                executable = find_executable(self.executable)
            except OSError as error:
                self.diagnostic = str(error)
                return False, "The StationNet WIKI reader could not be accessed."
            if not executable:
                return False, "The StationNet WIKI reader is missing. Run the StationNet installer again."
            try:
                with socket.socket() as sock:
                    sock.bind(("127.0.0.1", 0))
                    port = sock.getsockname()[1]
            except OSError as error:
                self.diagnostic = str(error)
                return False, "Could not reserve a local port for Kiwix."
            try:
                self.output_file = tempfile.TemporaryFile()
                self.process = subprocess.Popen(
                    [executable, "--address=127.0.0.1", f"--port={port}",
                     f"--urlRootLocation={ROOT}", "--skipInvalid", "--verbose",
                     *map(str, files)],
                    stdin=subprocess.DEVNULL, stdout=self.output_file,
                    stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except OSError as error:
                self.diagnostic = str(error)
                self._close_output()
                return False, "Could not start kiwix-serve."
            self.port = port
            self.signature = signature
            for _ in range(30):
                if self.process.poll() is not None:
                    self.diagnostic = self._read_output() or f"kiwix-serve exited with code {self.process.returncode}."
                    self._close_output()
                    return False, "kiwix-serve stopped while opening the ZIM library."
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=.1):
                        pass
                    count = self._catalog_count(port)
                    if count == 0:
                        self.diagnostic = self._read_output() or "Kiwix registered no ZIM files. Check that the archives are valid and readable."
                        return False, "Kiwix could not load any ZIM files."
                    self.registered_count = count
                    if count < len(files):
                        self.diagnostic = (f"Kiwix loaded {count} of {len(files)} ZIM files. "
                                           + (self._read_output() or "Check the missing archives for errors."))
                    return True, None
                except (OSError, HTTPError, URLError, ElementTree.ParseError):
                    time.sleep(.1)
            self.diagnostic = self._read_output() or "The Kiwix catalog did not become available."
            return False, "Kiwix did not make its library available."


def init_app(app):
    default_dir = Path(app.config["DATA_DIR"]) / "kiwix_data"
    app.extensions["kiwix"] = KiwixServer(
        app.config["KIWIX_DATA_DIR"], app.config["KIWIX_SERVE_EXECUTABLE"],
        default_dir.parent.parent / "kiwix_data"
        if Path(app.config["KIWIX_DATA_DIR"]) == default_dir else None,
    )


def _enabled():
    if not g.station or not g.station["kiwix_enabled"]:
        abort(404)


def _library(server):
    """Read the public OPDS feed rather than relying on Kiwix's viewer markup."""
    url = f"http://127.0.0.1:{server.port}{ROOT}/catalog/v2/entries?count=-1"
    with _opener.open(Request(url), timeout=10) as response:
        feed = ElementTree.fromstring(response.read())
    books = []
    for entry in feed:
        if entry.tag.rsplit("}", 1)[-1] != "entry":
            continue
        fields = {child.tag.rsplit("}", 1)[-1]: (child.text or "").strip()
                  for child in entry}
        # OPDS <name> is archive metadata and can differ from the identifier
        # that /content and /search accept. The HTML link carries that ID.
        name = None
        for link in entry:
            if (link.tag.rsplit("}", 1)[-1] != "link"
                    or not link.get("type", "").startswith("text/html")):
                continue
            path = urlsplit(link.get("href", "")).path
            if "/content/" in path:
                name = unquote(path.rsplit("/content/", 1)[1].split("/", 1)[0])
                break
        if name and "/" not in name and name not in (".", ".."):
            books.append({"name": name, "title": fields.get("title") or name,
                          "description": fields.get("summary") or fields.get("description") or ""})
    return books


def _page(**kwargs):
    server = current_app.extensions["kiwix"]
    ready, error = server.ready()
    books = []
    if ready:
        try:
            books = _library(server)
        except (OSError, HTTPError, URLError, ElementTree.ParseError) as exc:
            server.diagnostic = str(exc)
            error = "The library catalog could not be loaded."
            ready = False
    return render_template("wiki.html", ready=ready, error=error,
                           books=books, diagnostic=server.diagnostic,
                           kiwix_dir=current_app.config["KIWIX_DATA_DIR"], **kwargs), (200 if ready else 503)


@bp.get("/")
def index():
    _enabled()
    return _page()


@bp.get("/read/<path:path>")
def read(path):
    _enabled()
    book = path.split("/", 1)[0]
    source = url_for("kiwix.content", path="content/" + path)
    if request.query_string:
        source += "?" + request.query_string.decode("latin-1")
    return _page(active_book=book, article_source=source)


@bp.get("/search")
def search():
    _enabled()
    book = request.args.get("book", "")
    query = request.args.get("q", "").strip()
    if not book or not query:
        return _page(active_book=book)
    try:
        start = max(0, int(request.args.get("start", "0")))
    except ValueError:
        start = 0
    source = url_for("kiwix.search_source") + "?" + urlencode(
        {"books.name": book, "pattern": query, "start": start})
    return _page(active_book=book, query=query, article_source=source)


@bp.get("/search-source")
def search_source():
    _enabled()
    book = request.args.get("books.name", "")
    pattern = request.args.get("pattern", "")
    if not book or not pattern:
        abort(400)
    try:
        start = max(0, int(request.args.get("start", "0")))
    except ValueError:
        start = 0
    return _proxy("search", urlencode({"books.name": book, "pattern": pattern,
                                      "start": start}))


@bp.route("/content/", defaults={"path": ""}, methods=["GET", "HEAD"])
@bp.route("/content/<path:path>", methods=["GET", "HEAD"])
def content(path):
    _enabled()
    return _proxy(path, request.query_string.decode("latin-1"))


@bp.route("/interactive/", defaults={"path": ""}, methods=["GET", "HEAD"])
@bp.route("/interactive/<path:path>", methods=["GET", "HEAD"])
def interactive(path):
    """Keep relative links inside the framed archive as it navigates."""
    _enabled()
    return _proxy(path, request.query_string.decode("latin-1"))


def _redirect_location(target, interactive=False, framed=False):
    query = target.query
    if interactive and not framed:
        args = [(key, value) for key, value in parse_qsl(query, keep_blank_values=True)
                if key != INTERACTIVE_QUERY]
        args.append((INTERACTIVE_QUERY, "1"))
        query = urlencode(args)
    path = target.path or ""
    if framed:
        path = url_for("kiwix.interactive", path="") + path[len(ROOT):].lstrip("/")
    return path + ("?" + query if query else "")


def _proxy(path, query):
    server = current_app.extensions["kiwix"]
    ready, error = server.ready()
    if not ready:
        return render_template("wiki.html", ready=False, error=error,
                               diagnostic=server.diagnostic,
                               kiwix_dir=current_app.config["KIWIX_DATA_DIR"]), 503
    # This StationNet-only flag selects the sandboxed runtime response. Never
    # pass it through to kiwix-serve, where it could alter an archive route or
    # create a duplicate upstream cache entry.
    framed = request.endpoint == "kiwix.interactive"
    interactive = framed or request.args.get(INTERACTIVE_QUERY) == "1"
    if INTERACTIVE_QUERY in request.args:
        query = urlencode([(key, value) for key, value in parse_qsl(query, keep_blank_values=True)
                           if key != INTERACTIVE_QUERY])
    url = f"http://127.0.0.1:{server.port}{ROOT}/" + quote(path, safe="/@:+")
    if query:
        url += "?" + query
    upstream_request = Request(url, method=request.method)
    try:
        upstream = _opener.open(upstream_request, timeout=30)
    except HTTPError as error:
        upstream = error
    except URLError as error:
        server.diagnostic = str(error)
        return render_template("wiki.html", ready=False,
                               error="The Kiwix service is unavailable. Refresh WIKI to retry.",
                               diagnostic=server.diagnostic,
                               kiwix_dir=current_app.config["KIWIX_DATA_DIR"]), 503
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
        # Kiwix commonly redirects a collection root to its index.html. Keep
        # the browser in interactive mode across that trusted internal hop;
        # the flag remains StationNet-only and was removed upstream above.
        headers["Location"] = _redirect_location(target, interactive, framed)
    if request.method == "HEAD":
        status = upstream.status
        upstream.close()
        return Response(status=status, headers=headers)
    # Assets stay on this origin; HTML is composed by the StationNet reader.
    def chunks():
        try:
            while block := upstream.read(65536):
                yield block
        finally:
            upstream.close()
    return Response(chunks(), status=upstream.status, headers=headers)
