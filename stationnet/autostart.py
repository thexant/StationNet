"""Start with Windows: a per-user Run entry that opens the Station Manager minimized."""
import hashlib
import subprocess
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_PREFIX = "StationNet Manager"
SUPPORTED = sys.platform == "win32"


def value_name(root):
    """One Run entry per install, like the wake task."""
    digest = hashlib.sha1(str(Path(root).resolve()).lower().encode("utf-8")).hexdigest()[:8]
    return f"{VALUE_PREFIX} {digest}"


def minimized_command(command):
    """``manager_command()`` as a minimized manager rather than the wake task."""
    return [part if part != "--hours-wake" else "--minimized" for part in command]


def command_line(command):
    return subprocess.list2cmdline(minimized_command(command))


def enabled(root):
    if not SUPPORTED:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, value_name(root))
    except OSError:
        return False
    return True


def set_enabled(root, on, command):
    """Add or remove the Run entry; ``command`` is ``manager_command()``."""
    if not SUPPORTED:
        raise OSError("Start with Windows is available only on Windows.")
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if on:
            winreg.SetValueEx(key, value_name(root), 0, winreg.REG_SZ, command_line(command))
        else:
            try:
                winreg.DeleteValue(key, value_name(root))
            except FileNotFoundError:
                pass
