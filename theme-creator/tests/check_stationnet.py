"""Read-only compatibility check against this checkout's StationNet theme code."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from stationnet import themes  # noqa: E402
from stationnet.ansi import station_banner  # noqa: E402

result = subprocess.run(
    ["node", str(Path(__file__).with_name("compat-fixtures.cjs"))],
    check=True, capture_output=True, encoding="utf-8",
)
fixtures = json.loads(result.stdout)
data = fixtures["data"]
assert data["siteKeys"] == list(themes.SITE_KEYS)
assert data["mastheadKeys"] == list(themes.MASTHEAD_KEYS)
assert data["presets"] == {key: dict(zip(themes.KEYS, value)) for key, value in themes.THEMES.items()}
template = json.loads((ROOT / "stationnet/theme_templates/template-theme.json").read_text(encoding="utf-8"))
for section, keys in [("site", themes.SITE_KEYS), ("masthead", themes.MASTHEAD_KEYS)]:
    for key in keys:
        assert data["descriptions"][key] == template[section]["_comment_" + key]
for case in fixtures["cases"]:
    state = case["state"]
    parsed = themes.parse(case["exported"])
    assert parsed == json.loads(case["exported"]), state["id"]
    assert ("site" in parsed) == state["includeSite"]
    assert ("masthead" in parsed) == state["includeMasthead"]
    default = dict(zip(themes.KEYS, themes.THEMES["cyan"]))
    site = state["site"] if state["includeSite"] else default
    masthead = state["masthead"] if state["includeMasthead"] else default
    expected = {f"--{key}": site[key] for key in themes.KEYS[1:12]}
    expected.update(themes._surfaces(site))
    expected.update({
        "--theme-rgb": ",".join(map(str, themes._rgb(site["green"]))),
        "--accent-rgb": ",".join(map(str, themes._rgb(site["cyan"]))),
        "--masthead-color": masthead["masthead"],
        "--masthead-shadow": masthead["masthead-shadow"],
    })
    assert case["variables"] == expected, state["id"]

html = (ROOT / "theme-creator/index.html").read_text(encoding="utf-8")
banner = html.split('<span class="ansi-logo-art">', 1)[1].split('</span>', 1)[0]
assert [line.rstrip() for line in banner.splitlines()] == [line.rstrip() for line in station_banner("THEME").splitlines()]
print(f"PASS: {len(fixtures['cases'])} exports accepted by StationNet; all preview variables match.")
print("PASS: all ten presets, key descriptions, and THEME FIGlet banner match StationNet.")
