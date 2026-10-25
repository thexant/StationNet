"""Smoke-check the two Kiwix response security modes without a ZIM file."""
import os
from contextlib import nullcontext
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stationnet import create_app


configured_dir = os.environ.get("STATIONNET_TEST_DATA")
data_dir_context = nullcontext(configured_dir) if configured_dir else TemporaryDirectory()
with data_dir_context as data_dir:
    app = create_app({"TESTING": True, "DATA_DIR": data_dir})
    client = app.test_client()
    interactive = client.get(
        "/wiki/content/content/world/?stationnet-interactive=1",
        headers={"Origin": "null"},
    )
    static = client.get("/wiki/content/content/world/")

    interactive_csp = interactive.headers["Content-Security-Policy"]
    static_csp = static.headers["Content-Security-Policy"]
    assert interactive.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert "sandbox allow-scripts" in interactive_csp
    assert "script-src 'self'" in interactive_csp
    assert interactive.headers["Access-Control-Allow-Origin"] == "null"
    assert "script-src 'none'" in static_csp
    assert "sandbox allow-scripts" not in static_csp

print("Kiwix interactive/static CSP modes: PASS")
