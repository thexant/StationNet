"""Shared, validated colour palettes for the station and its masthead."""

from collections import OrderedDict

# The default palette matches the original StationNet colours exactly. Values
# are CSS hex colours only; theme IDs never become arbitrary CSS input.
# Every palette shares the original near-black backgrounds and control surfaces.
# The final colour in each palette is the contrasting masthead drop shadow.
THEMES = OrderedDict([
    ("cyan", ("Default / Cyan", "#00ed45", "#71ff8a", "#008d31", "#00d8cf", "#d829be", "#e6a431", "#ff3237", "#7b1118", "#8b8b86", "#d8d8cf", "#000000", "#030703", "#001b08", "#00d8cf", "#d829be")),
    ("amber", ("Amber", "#e6a431", "#ffd27a", "#895d19", "#ffd27a", "#75c9cf", "#e6a431", "#ff6c59", "#87362d", "#aaa08d", "#f1e4c9", "#000000", "#030703", "#001b08", "#e6a431", "#5579d6")),
    ("terminal-green", ("Terminal Green", "#00ed45", "#72ff89", "#007f2c", "#00ca6c", "#b3e65a", "#d5c663", "#ff6565", "#772c2c", "#8fae91", "#d9efda", "#000000", "#030703", "#001b08", "#00ed45", "#bf45a8")),
    ("bright-green", ("Bright Green", "#a4ff3f", "#d5ff80", "#659927", "#ecff78", "#55dbc0", "#f4d868", "#ff6771", "#8a3340", "#b3bea1", "#f0f8df", "#000000", "#030703", "#001b08", "#caff59", "#a358e5")),
    ("magenta", ("Magenta", "#ed62db", "#ff9cf1", "#8f368c", "#65d9d4", "#ff70da", "#edbb73", "#ff6d79", "#8c3041", "#b9a4b8", "#f6e5f2", "#000000", "#030703", "#001b08", "#ed62db", "#3bcac2")),
    ("electric-blue", ("Electric Blue", "#54a8ff", "#91cbff", "#27629f", "#66e2ff", "#bb8cff", "#e9be6e", "#ff6979", "#813645", "#9caec5", "#e4efff", "#000000", "#030703", "#001b08", "#54a8ff", "#ef913f")),
    ("crimson", ("Crimson", "#f05c6a", "#ff9ba2", "#95353f", "#ffc078", "#d987b7", "#f0b96c", "#ff6565", "#8b2831", "#baa3a5", "#f8e7e5", "#000000", "#030703", "#001b08", "#f05c6a", "#49bfc0")),
    ("violet", ("Violet", "#b38aff", "#d6bbff", "#6949a0", "#8cdbf0", "#e79cff", "#e9bf75", "#ff728a", "#85374e", "#ada5bd", "#f0eaff", "#000000", "#030703", "#001b08", "#b38aff", "#d3b45c")),
    ("monochrome", ("Monochrome", "#d0d0d0", "#ffffff", "#747474", "#ededed", "#a9a9a9", "#c5c5c5", "#ffffff", "#777777", "#a9a9a9", "#f0f0f0", "#000000", "#030703", "#001b08", "#e5e5e5", "#737373")),
    ("vintage-terminal", ("Vintage Terminal", "#b7bc77", "#e0df9b", "#6d7344", "#d5c784", "#94b497", "#dcb474", "#df8b73", "#815347", "#aaa993", "#eee9cb", "#000000", "#030703", "#001b08", "#c8cc86", "#9b7ac7")),
])

KEYS = ("name", "green", "green-bright", "green-dim", "cyan", "magenta", "amber", "red", "red-dim", "grey", "white", "black", "field", "button", "masthead", "masthead-shadow")


def palette(theme_id):
    return dict(zip(KEYS, THEMES.get(theme_id, THEMES["cyan"])))


def valid(theme_id):
    return theme_id in THEMES


def effective(station, user):
    """Resolve each independent user preference against its station default."""
    def selected(column):
        choice = user[column] if user and user[column] else station[column] if station else "cyan"
        return choice if valid(choice) else "cyan"
    return selected("site_theme"), selected("masthead_theme")


def css(site_id, masthead_id, legacy_color=""):
    site = palette(site_id)
    masthead = palette(masthead_id)
    variables = {f"--{key}": site[key] for key in KEYS[1:12]}
    variables.update({"--surface-field": site["field"], "--surface-button": site["button"],
                      "--theme-rgb": ",".join(str(int(site["green"][i:i+2], 16)) for i in (1, 3, 5)),
                      "--accent-rgb": ",".join(str(int(site["cyan"][i:i+2], 16)) for i in (1, 3, 5)),
                      "--masthead-color": legacy_color or masthead["masthead"],
                      "--masthead-shadow": masthead["masthead-shadow"]})
    rules = [":root {" + "".join(f"{key}:{value};" for key, value in variables.items()) + "}"]
    for theme_id in THEMES:
        entry = palette(theme_id)
        rules.append(f".theme-swatch--{theme_id} {{ --swatch-bg:{entry['black']}; --swatch-fg:{entry['green']}; --swatch-accent:{entry['cyan']}; --swatch-masthead:{entry['masthead']}; --swatch-shadow:{entry['masthead-shadow']}; }}")
    return "\n".join(rules)
