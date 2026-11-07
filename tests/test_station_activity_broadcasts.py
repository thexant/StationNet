"""The terminal keeps long broadcast lists compact until expanded."""

from pathlib import Path

from jinja2 import Environment


def test_active_broadcasts_collapse_after_five():
    environment = Environment(autoescape=True)
    environment.globals["url_for"] = lambda endpoint: "/radio"
    template = environment.from_string(_fragment('<div class="activity-broadcasts" data-activity-broadcasts>'))

    broadcasts = [
        {
            "name": f"Broadcast {index}", "frequency": f"7.0{index}",
            "automated": True, "message": "", "track_title": f"Track {index}",
            "track_artist": "",
        }
        for index in range(7)
    ]

    five = template.render(activity={"broadcasts": broadcasts[:5]})
    assert five.count('class="activity-broadcast"') == 5
    assert 'data-activity-broadcast-toggle' not in five
    assert 'data-activity-broadcast-more' not in five

    seven = template.render(activity={"broadcasts": broadcasts})
    assert seven.count('class="activity-broadcast"') == 7
    assert seven.count('class="activity-broadcast" hidden') == 2
    assert 'and 2 more' in seven
    assert 'aria-expanded="false" data-activity-broadcast-toggle' in seven


def _fragment(start):
    source = (Path(__file__).resolve().parents[1] / "stationnet/templates/terminal.html").read_text()
    return start + source.split(start, 1)[1].split("        </div>", 1)[0] + "        </div>"


def test_radio_collapses_after_three_when_tv_listings_show():
    environment = Environment(autoescape=True)
    environment.globals["url_for"] = lambda endpoint: "/radio"
    template = environment.from_string(_fragment('<div class="activity-broadcasts" data-activity-broadcasts>'))
    broadcasts = [{"name": f"B{index}", "frequency": "7.0", "automated": True, "message": "",
                   "track_title": "T", "track_artist": ""} for index in range(5)]
    html = template.render(activity={"broadcasts": broadcasts, "broadcast_limit": 3})
    assert html.count('class="activity-broadcast" hidden') == 2
    assert "and 2 more" in html


def test_tv_listings_collapse_after_three():
    environment = Environment(autoescape=True)
    environment.globals["url_for"] = lambda endpoint: "/tv"
    environment.filters["localtime"] = lambda value, style="datetime": f"{style}:{value}"
    template = environment.from_string(
        _fragment('<div class="activity-broadcasts activity-tv" data-activity-broadcasts'))
    channels = [{"number": index, "name": f"Ch{index}",
                 "now": {"title": f"Now {index}", "start": "s", "end": "e"},
                 "next": {"title": f"Next {index}", "start": "n", "far": index == 1}} for index in range(1, 6)]
    two = template.render(activity={"tv_channels": channels[:2]})
    assert two.count('class="activity-broadcast"') == 2
    assert "data-activity-broadcast-toggle" not in two
    assert "NEXT datetime:n / Next 1" in two and "NEXT time:n / Next 2" in two
    five = template.render(activity={"tv_channels": channels})
    assert five.count('class="activity-broadcast" hidden') == 2
    assert "and 2 more" in five and 'aria-controls="activity-tv-list"' in five
