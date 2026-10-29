from datetime import datetime

import pytest

from stationnet import host_power, station_hours
from stationnet.station_hours import Hours, crossed, header_text, is_open, next_time, parse_time, previous_time


@pytest.mark.parametrize("text, expected", [
    ("8:00", "08:00"), ("21:00", "21:00"), ("9pm", "21:00"), ("9:30 PM", "21:30"), ("12am", "00:00"),
    ("12:15 p.m.", "12:15"), (" 7:05am ", "07:05"),
])
def test_parse_time(text, expected):
    assert parse_time(text) == expected


@pytest.mark.parametrize("text", ["", "9", "25:00", "8:60", "13pm", "noon", "0am"])
def test_parse_time_rejects(text):
    with pytest.raises(ValueError):
        parse_time(text)


def test_format_both_clocks():
    hours = Hours(open="08:00", close="21:00")
    assert hours.text == "8:00AM - 9:00PM"
    assert Hours(open="08:00", close="21:00", clock="24h").text == "08:00 - 21:00"
    assert station_hours.format_time("00:30") == "12:30AM"


def test_check_validates_and_normalizes():
    values = station_hours.check({"enabled": True, "open": "8am", "close": "9:00 pm", "sleep": 1,
                                  "warning_minutes": "10", "adjusted_message": "  OPEN   LATE "})
    assert values == {"enabled": True, "open": "08:00", "close": "21:00", "clock": "12h", "sleep": True,
                      "warning_minutes": 10, "adjusted_message": "OPEN LATE"}
    assert station_hours.check({"enabled": False})["adjusted_message"] == station_hours.DEFAULT_MESSAGE
    for bad in ({"enabled": True, "close": "21:00"}, {"enabled": True, "open": "8:00", "close": "08:00"},
                {"enabled": True, "open": "8:00", "close": "9:00", "warning_minutes": 0},
                {"enabled": True, "open": "8:00", "close": "9:00", "clock": "36h"},
                {"enabled": True, "open": "8:00", "close": "9:00", "adjusted_message": "x" * 81}):
        with pytest.raises(ValueError):
            station_hours.check(bad)


def test_from_config_needs_enabled_usable_hours():
    assert Hours.from_config({}) is None
    assert Hours.from_config({"hours": {"enabled": False, "open": "08:00", "close": "21:00"}}) is None
    assert Hours.from_config({"hours": {"enabled": True, "open": "bad", "close": "21:00"}}) is None
    assert Hours.from_config({"hours": {"enabled": True, "open": "08:00", "close": "21:00"}}).close == "21:00"


def test_times_and_overnight_hours():
    now = datetime(2026, 10, 28, 21, 0)
    assert next_time("21:00", now) == datetime(2026, 10, 29, 21, 0)
    assert previous_time("21:00", now) == now
    assert next_time("08:00", now) == datetime(2026, 10, 29, 8, 0)
    day = Hours(open="08:00", close="21:00")
    assert is_open(day, datetime(2026, 10, 28, 12)) and not is_open(day, now)
    night = Hours(open="20:00", close="02:00")
    assert is_open(night, datetime(2026, 10, 28, 23, 59)) and is_open(night, datetime(2026, 10, 29, 1))
    assert not is_open(night, datetime(2026, 10, 29, 2)) and not is_open(night, datetime(2026, 10, 28, 12))
    assert crossed("21:00", datetime(2026, 10, 28, 20, 59, 59), now)
    assert not crossed("21:00", now, datetime(2026, 10, 28, 21, 0, 1))
    assert crossed("00:00", datetime(2026, 10, 28, 23, 59), datetime(2026, 10, 29, 0, 0, 1))


def test_header_text():
    config = {"hours": {"enabled": True, "open": "08:00", "close": "21:00", "adjusted_message": "OPEN LATE"}}
    now = datetime(2026, 10, 28, 20, 0)
    assert header_text(config, now) == ("STATION HOURS:", "8:00AM - 9:00PM")
    extended = {**config, "hours_extended_until": "2026-10-29T08:00"}
    assert header_text(extended, now) == ("STATION HOURS:", "OPEN LATE")
    assert header_text(extended, datetime(2026, 10, 29, 8, 0)) == ("STATION HOURS:", "8:00AM - 9:00PM")
    assert header_text({}, now) is None


def test_host_settings_round_trip(tmp_path):
    assert station_hours.load_host(tmp_path)["sleep_countdown_minutes"] == 5
    station_hours.save_host(tmp_path, **station_hours.check_host("2", "10"))
    station_hours.save_host(tmp_path, registered_wake="2026-10-29T07:50:00")
    values = station_hours.load_host(tmp_path)
    assert (values["sleep_countdown_minutes"], values["wake_lead_minutes"]) == (2, 10)
    assert values["registered_wake"] == "2026-10-29T07:50:00"
    with pytest.raises(ValueError):
        station_hours.check_host("0", "5")


def test_wake_task_xml(tmp_path):
    command = ["C:\\Py\\pythonw.exe", "C:\\Station Net\\manager.py", "--hours-wake", "--home", "C:\\Station Net"]
    xml = host_power.task_xml(datetime(2026, 10, 29, 7, 55), command, tmp_path)
    assert "<WakeToRun>true</WakeToRun>" in xml and "<StartWhenAvailable>false</StartWhenAvailable>" in xml
    assert "<StartBoundary>2026-10-29T07:55:00</StartBoundary>" in xml
    assert "<Command>C:\\Py\\pythonw.exe</Command>" in xml
    assert '"C:\\Station Net\\manager.py" --hours-wake --home "C:\\Station Net"' in xml
    assert "<LogonType>InteractiveToken</LogonType>" in xml
    assert host_power.task_name(tmp_path) == host_power.task_name(tmp_path / ".")
    assert host_power.task_name(tmp_path) != host_power.task_name(tmp_path / "other")


def test_custom_commands_are_used(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(host_power, "_run", lambda command, shell=False: calls.append(command))
    power = host_power.HostPower(tmp_path, settings={"sleep_command": "goodnight",
                                                     "wake_command": "alarm {epoch} {iso}"})
    power.sleep_now()
    when = datetime(2026, 10, 29, 7, 55)
    power.schedule_wake(when)
    power.cancel_wake()
    assert calls == ["goodnight", f"alarm {int(when.timestamp())} 2026-10-29T07:55:00"]
    assert power.support()[0]


def test_host_power_status_cli(tmp_path, capsys):
    host_power.main(["status", "--home", str(tmp_path)])
    assert "Registered wake: none" in capsys.readouterr().out
