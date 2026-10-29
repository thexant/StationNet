from stationnet import autostart


def test_minimized_command_replaces_the_wake_flag():
    command = [r"C:\Station Net\pythonw.exe", "manager.py", "--hours-wake", "--home", r"C:\Station Net"]
    assert autostart.minimized_command(command) == [r"C:\Station Net\pythonw.exe", "manager.py", "--minimized",
                                                    "--home", r"C:\Station Net"]
    assert autostart.command_line(command) == r'"C:\Station Net\pythonw.exe" manager.py --minimized --home "C:\Station Net"'


def test_value_name_is_per_install(tmp_path):
    first, second = autostart.value_name(tmp_path / "a"), autostart.value_name(tmp_path / "b")
    assert first.startswith(autostart.VALUE_PREFIX) and first != second
    assert autostart.value_name(tmp_path / "a") == first
