"""One installable web app per station origin."""
from io import BytesIO
from pathlib import Path

from flask import Blueprint, Response, current_app, g, jsonify, send_file, url_for
from PIL import Image

bp = Blueprint("pwa", __name__)


def _icon_path():
    station_icon = Path(current_app.config["DATA_DIR"]) / "station_icon.png"
    if (g.station is not None and g.station["use_station_icon_for_web_app"]
            and station_icon.is_file()):
        return station_icon
    return Path(__file__).resolve().parents[1] / "icon.png"


@bp.get("/manifest.webmanifest")
def manifest():
    name = g.station["name"] if g.station is not None else "StationNet"
    response = jsonify({
        "id": url_for("station.index"),
        "name": name,
        "short_name": name,
        "start_url": url_for("station.index"),
        "scope": "/",
        "display": "standalone",
        "background_color": g.theme_background,
        "theme_color": g.theme_background,
        "icons": [
            {"src": url_for("pwa.icon", size=size), "sizes": f"{size}x{size}", "type": "image/png"}
            for size in (192, 512)
        ],
    })
    response.mimetype = "application/manifest+json"
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/pwa-icon-<int:size>.png")
def icon(size):
    if size not in (192, 512):
        return Response(status=404)
    with Image.open(_icon_path()) as source:
        icon_image = source.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
        output = BytesIO()
        icon_image.save(output, "PNG")
    output.seek(0)
    response = send_file(output, mimetype="image/png")
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/service-worker.js")
def service_worker():
    response = send_file(Path(__file__).parent / "static" / "service-worker.js", mimetype="text/javascript")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Service-Worker-Allowed"] = "/"
    return response
