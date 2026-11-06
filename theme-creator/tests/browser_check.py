"""Run the creator in an isolated headless browser, using only Python stdlib.

Copies the whole app outside the checkout; checks file:// and HTTP launch.
The two probe scripts are injected only into that temporary copy.
"""
import argparse
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time

APP = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--browser", help="Path to a Chrome or Chromium executable")
parser.add_argument("--output", type=Path, default=APP / "tests/artifacts")
args = parser.parse_args()
browser = args.browser or shutil.which("chromium") or shutil.which("google-chrome")
if not browser and os.name == "nt":
    for candidate in [Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe",
                      Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe"]:
        if candidate.exists():
            browser = str(candidate)
            break
if not browser:
    parser.error("No installed Chrome/Chromium found; use --browser.")
args.output.mkdir(parents=True, exist_ok=True)
output = args.output.resolve()

class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def handle(self):
        try:
            super().handle()
        except (ConnectionResetError, BrokenPipeError):
            # A closing test tab can cancel its favicon connection.
            pass

with tempfile.TemporaryDirectory(prefix="stationnet-theme-creator-") as scratch:
    temporary = Path(scratch)
    assert temporary.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())
    assert temporary.name.startswith("stationnet-theme-creator-")
    copied = temporary / "standalone"
    shutil.copytree(APP, copied, ignore=shutil.ignore_patterns("artifacts", "__pycache__"))
    html = (copied / "index.html").read_text(encoding="utf-8")
    html = html.replace('<script src="creator.js" defer>', '<script src="tests/browser-setup.js" defer></script>\n  <script src="creator.js" defer>')
    html = html.replace('</head>', '<script src="tests/browser-probe.js" defer></script>\n</head>')
    (copied / "index.html").write_text(html, encoding="utf-8")
    handler = functools.partial(QuietHandler, directory=str(copied))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_http = f"http://127.0.0.1:{server.server_port}/index.html"
    base_file = (copied / "index.html").as_uri()
    runs = [("file-edit", base_file, "edit", 1440), ("file-restore", base_file, "restore", 390),
            ("http-edit", base_http, "edit", 1440), ("http-restore", base_http, "restore", 390),
            ("http-blocked", base_http, "blocked", 768), ("file-corrupt", base_file, "corrupt", 1100),
            ("http-small", base_http, "smoke", 320),
            ("default-desktop", base_file, "default", 1440),
            ("default-mobile", base_http, "default", 390)]
    all_results = []
    startup = None
    if os.name == "nt":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    profile = temporary / "profile"
    chrome_log = (output / "chrome-startup.log").open("w", encoding="utf-8")
    process = subprocess.Popen([browser, "--headless", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                                "--disable-extensions", "--disable-background-networking", "--remote-debugging-port=0",
                                "--remote-debugging-address=127.0.0.1", f"--user-data-dir={profile}", "about:blank"],
                               stdout=subprocess.DEVNULL, stderr=chrome_log, startupinfo=startup)
    try:
        port_file = profile / "DevToolsActivePort"
        deadline = time.monotonic() + 15
        while not port_file.exists() and time.monotonic() < deadline:
            time.sleep(.1)
        if not port_file.exists():
            raise RuntimeError(f"Headless Chrome did not start. See {output / 'chrome-startup.log'}.")
        port, endpoint = port_file.read_text().splitlines()[:2]
        websocket = f"ws://127.0.0.1:{port}{endpoint}"
        for name, base, probe, width in runs:
            command = ["node", str(APP / "tests/run-browser.cjs"), websocket, base + "?probe=" + probe,
                       str(width), str(output / (name + ".png"))]
            result = subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace", timeout=45)
            (output / (name + ".log")).write_text(result.stderr, encoding="utf-8")
            if result.returncode or not result.stdout:
                raise RuntimeError(f"{name}: browser did not finish the checks (exit {result.returncode}). See {output / (name + '.log')}.")
            report = json.loads(result.stdout)
            report["name"] = name
            all_results.append(report)
            print(f"{report['status'].upper()}: {name} ({report['width']}px; {len(report['results'])} checks)", flush=True)
            if report["status"] != "passed":
                raise AssertionError(report["results"][-1])
    finally:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        chrome_log.close()
        server.shutdown()
        server.server_close()
        (output / "results.json").write_text(json.dumps(all_results, indent=2), encoding="utf-8")
    print(f"Screenshots and reports: {output}")
