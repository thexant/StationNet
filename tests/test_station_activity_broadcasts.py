"""The terminal keeps long broadcast lists compact until expanded."""

from pathlib import Path

from jinja2 import Environment


def test_active_broadcasts_collapse_after_five():
    source = (Path(__file__).resolve().parents[1] / "stationnet/templates/terminal.html").read_text()
    start = '        <div class="activity-broadcasts" data-activity-broadcasts>'
    fragment = start + source.split(start, 1)[1].split("        </div>", 1)[0] + "        </div>"
    environment = Environment(autoescape=True)
    environment.globals["url_for"] = lambda endpoint: "/radio"
    template = environment.from_string(fragment)

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
