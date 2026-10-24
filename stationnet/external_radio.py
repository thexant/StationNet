"""Bounded discovery of public internet radio endpoints and ICY metadata."""
import ipaddress
import re
import socket
from http.client import HTTPConnection, HTTPSConnection
from urllib.parse import urljoin, urlsplit
from urllib.request import (Request, HTTPRedirectHandler, HTTPHandler, HTTPSHandler,
                            ProxyHandler, build_opener)


MAX_PLAYLIST_BYTES = 65536
STREAM_CHUNK_BYTES = 16384


def _opener():
    # Disable ambient proxies: each DNS result and redirect must pass the public-IP check.
    return build_opener(ProxyHandler({}), _SafeRedirect(), _PublicHTTPHandler(),
                        _PublicHTTPSHandler())


def validate_stream_url(url, resolve=True):
    url = (url or "").strip()
    parts = urlsplit(url)
    if (len(url) > 2048 or parts.scheme not in {"http", "https"} or
            not parts.hostname or parts.username or parts.password or parts.fragment):
        raise ValueError("Enter a public HTTP or HTTPS radio stream URL.")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("Enter a valid radio stream port.") from exc
    if port == 0:
        raise ValueError("Enter a valid radio stream port.")
    if parts.hostname.lower() == "localhost" or parts.hostname.lower().endswith(".local"):
        raise ValueError("Radio streams must use a public internet address.")
    if not resolve:
        try:
            address = ipaddress.ip_address(parts.hostname)
        except ValueError:
            return url
        if not address.is_global:
            raise ValueError("Radio streams must use a public internet address.")
        return url
    try:
        addresses = socket.getaddrinfo(parts.hostname, port or
                                        (443 if parts.scheme == "https" else 80))
        if not addresses or any(not ipaddress.ip_address(entry[4][0]).is_global
                                for entry in addresses):
            raise ValueError("Radio streams must use a public internet address.")
    except (OSError, ValueError) as exc:
        raise ValueError("Radio streams must use a public internet address.") from exc
    return url


class _SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        validate_stream_url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def _public_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT,
                       source_address=None):
    host, port = address
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global
                            for item in addresses):
        raise OSError("Radio stream resolved to a private address.")
    last_error = None
    for item in addresses:
        try:
            return socket.create_connection((item[4][0], port), timeout,
                                            source_address=source_address)
        except OSError as problem:
            last_error = problem
    raise last_error or OSError("Radio stream is unavailable.")


class _PublicHTTPConnection(HTTPConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _public_connection


class _PublicHTTPSConnection(HTTPSConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _public_connection


class _PublicHTTPHandler(HTTPHandler):
    def http_open(self, request):
        return self.do_open(_PublicHTTPConnection, request)


class _PublicHTTPSHandler(HTTPSHandler):
    def https_open(self, request):
        return self.do_open(_PublicHTTPSConnection, request, context=self._context)


def _playlist_target(data, url, content_type):
    text = data.decode("utf-8-sig", "replace")
    if "scpls" in text[:100].lower() or "scpls" in content_type or urlsplit(url).path.lower().endswith(".pls"):
        match = re.search(r"(?im)^File\d+\s*=\s*(\S.+?)\s*$", text)
        return urljoin(url, match.group(1).strip()) if match else None
    if "mpegurl" in content_type or urlsplit(url).path.lower().endswith(".m3u"):
        if "#EXT-X-" in text:
            return url  # HLS manifest: the browser handles the segments.
        entry = next((line.strip() for line in text.splitlines()
                      if line.strip() and not line.lstrip().startswith("#")), None)
        return urljoin(url, entry) if entry else None
    return url


def inspect_stream(url, metadata=False):
    """Resolve M3U/PLS and sample headers/ICY title without proxying audio."""
    opener = _opener()
    current = validate_stream_url(url)
    station = title = artist = description = ""
    for _ in range(4):
        request = Request(current, headers={"User-Agent": "StationNet/1.0",
                                            "Icy-MetaData": "1" if metadata else "0"})
        with opener.open(request, timeout=5) as response:
            current = validate_stream_url(response.geturl())
            headers = response.headers
            station = (headers.get("icy-name") or headers.get("x-audiocast-name") or station)[:80].strip()
            description = (headers.get("icy-description") or
                           headers.get("x-audiocast-description") or description)[:240].strip()
            content_type = headers.get_content_type().lower()
            path = urlsplit(current).path.lower()
            playlist = (path.endswith((".m3u", ".pls", ".m3u8")) or
                        "mpegurl" in content_type or "scpls" in content_type)
            if playlist:
                data = response.read(MAX_PLAYLIST_BYTES + 1)
                if len(data) > MAX_PLAYLIST_BYTES:
                    raise ValueError("Radio playlist is too large.")
                target = _playlist_target(data, current, content_type)
                if not target:
                    raise ValueError("Radio playlist has no stream URL.")
                text = data.decode("utf-8-sig", "replace")
                label = (re.search(r"(?im)^Title\d+\s*=\s*(.+?)\s*$", text) or
                         re.search(r"(?m)^#EXTINF:[^,]*,\s*(.+?)\s*$", text))
                if label and not station:
                    station = label.group(1).strip()[:80]
                if target == current:
                    return {"url": current, "station": station, "description": description,
                            "title": "", "artist": "", "hls": True}
                current = validate_stream_url(target)
                continue
            if metadata:
                interval = int(headers.get("icy-metaint") or 0)
                if 0 < interval <= 1024 * 1024:
                    response.read(interval)
                    length = response.read(1)
                    if length:
                        block = response.read(length[0] * 16).decode("utf-8", "replace")
                        match = re.search(r"StreamTitle='([^']*)'", block)
                        if match:
                            value = match.group(1).strip()[:240]
                            artist, separator, title = value.partition(" - ")
                            if not separator:
                                title, artist = artist, ""
            return {"url": current, "station": station, "description": description,
                    "title": title, "artist": artist,
                    "hls": False}
    raise ValueError("Radio playlist contains too many redirects.")


def open_audio_stream(url):
    """Open a validated continuous audio response for one tuned listener."""
    details = inspect_stream(url)
    if details["hls"]:
        raise ValueError("HLS streams require native browser playback.")
    response = _opener().open(Request(details["url"], headers={
        "User-Agent": "StationNet/1.0", "Icy-MetaData": "0",
        "Accept": "audio/mpeg,audio/aac,audio/aacp,*/*",
    }), timeout=10)
    try:
        resolved = validate_stream_url(response.geturl())
        content_type = response.headers.get_content_type().lower()
        if content_type in {"text/html", "application/xhtml+xml", "application/json"}:
            raise ValueError(f"Radio server returned {content_type} instead of audio.")
        if content_type in {"audio/mpeg", "audio/mp3", "audio/x-mpeg", "audio/mpeg3"}:
            mime = "audio/mpeg"
        elif content_type in {"audio/aac", "audio/aacp", "audio/x-aac", "audio/x-aacp"}:
            mime = "audio/aac"
        else:
            path = urlsplit(resolved).path.lower()
            if path.endswith(('.mp3', '.mpga')):
                mime = "audio/mpeg"
            elif path.endswith(('.aac', '.aacp')):
                mime = "audio/aac"
            else:
                # Some SHOUTcast servers label their audio as octet-stream. Sniff the
                # first bytes without ever buffering an unbounded response.
                prefix = response.read(4)
                if prefix.startswith(b'ID3') or (len(prefix) >= 2 and prefix[0] == 0xff
                                                 and prefix[1] & 0xe0 == 0xe0
                                                 and prefix[1] & 0x06 != 0):
                    mime = "audio/mpeg"
                elif len(prefix) >= 2 and prefix[0] == 0xff and prefix[1] & 0xf6 == 0xf0:
                    mime = "audio/aac"
                else:
                    raise ValueError(f"Unsupported radio audio type: {content_type}.")
                return response, mime, prefix
        return response, mime, b""
    except Exception:
        response.close()
        raise
