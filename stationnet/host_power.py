"""Sleep, wake timers and keep-awake requests for the computer hosting stations.

Windows is fully supported: sleep through SetSuspendState, keep-awake through
SetThreadExecutionState, and wake through a Task Scheduler task that may wake
the computer. Elsewhere sleep and keep-awake use the usual system tools, and a
wake timer needs a ``wake_command`` in manager.json (setting one usually needs
root). ``sleep_command`` and ``wake_command`` override the built-in behaviour
on any platform; ``{epoch}`` and ``{iso}`` in them are the wake time.

Run ``python -m stationnet.host_power status`` to check this computer.
"""
import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

TASK_PREFIX = "StationNet Wake"
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
# Child consoles would flash a window from the windowed manager.
_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def task_name(root):
    """One wake task per install, so two installs never cancel each other's."""
    digest = hashlib.sha1(str(Path(root).resolve()).lower().encode("utf-8")).hexdigest()[:8]
    return f"{TASK_PREFIX} {digest}"


def manager_command(root, core_dir):
    """What the wake task runs: the Station Manager, which starts stations on time."""
    core_dir = Path(core_dir)
    if getattr(sys, "frozen", False):
        exe = "StationNetManager.exe" if sys.platform == "win32" else "StationNetManager"
        program, arguments = Path(sys.executable).with_name(exe), []
    else:
        program = Path(sys.executable)
        if program.name.lower() == "python.exe" and program.with_name("pythonw.exe").is_file():
            program = program.with_name("pythonw.exe")
        arguments = [str(core_dir / "manager.py")]
    return [str(program), *arguments, "--hours-wake", "--home", str(Path(root))]


def task_xml(when, command, working_dir):
    """Task Scheduler XML for a one-time task at ``when`` that may wake the computer.

    It runs as the signed-in user (no administrator rights needed), does not
    run late if the computer was off at ``when``, and is not ended while the
    manager it started keeps running.
    """
    arguments = subprocess.list2cmdline(command[1:])
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Wakes this computer before a StationNet station opens.</Description>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <StartBoundary>{when.strftime("%Y-%m-%dT%H:%M:%S")}</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>false</StartWhenAvailable>
    <WakeToRun>true</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(command[0])}</Command>
      <Arguments>{escape(arguments)}</Arguments>
      <WorkingDirectory>{escape(str(working_dir))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _run(command, shell=False):
    result = subprocess.run(command, shell=shell, capture_output=True, text=True, timeout=30,
                            stdin=subprocess.DEVNULL, **_NO_WINDOW)
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip() or f"exit code {result.returncode}"
        raise OSError(message)
    return result.stdout


class HostPower:
    def __init__(self, root, command=None, settings=None):
        """``command`` is what a wake runs (see manager_command); ``settings``
        may hold ``sleep_command`` and ``wake_command`` from manager.json."""
        self.root = Path(root)
        self.command = command
        self.settings = settings or {}
        self._awake = False
        self._inhibitor = None

    def support(self):
        """``(wake_supported, note)`` for this computer."""
        if self.settings.get("wake_command"):
            return True, "Wake timers use the wake_command in manager.json."
        if sys.platform == "win32":
            return True, ("Wake timers use Windows Task Scheduler. 'Allow wake timers' must be enabled "
                          "in Power Options; see Station Hours in the README.")
        return False, ("Sleep works, but waking needs a wake_command in manager.json (for example "
                       "rtcwake); see Station Hours in the README.")

    def keep_awake(self, on):
        """Hold or release a request that stops the computer sleeping while idle."""
        on = bool(on)
        if on == self._awake:
            return
        self._awake = on
        if sys.platform == "win32":
            import ctypes
            flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0)
            ctypes.windll.kernel32.SetThreadExecutionState(flags)
            return
        if not on:
            if self._inhibitor is not None:
                self._inhibitor.terminate()
                self._inhibitor = None
            return
        if sys.platform == "darwin":
            command = ["caffeinate", "-i"]
        elif shutil.which("systemd-inhibit"):
            command = ["systemd-inhibit", "--what=idle:sleep", "--who=StationNet",
                       "--why=A station is open", "sleep", "infinity"]
        else:
            return
        try:
            self._inhibitor = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                               stderr=subprocess.DEVNULL)
        except OSError:
            self._inhibitor = None

    def sleep_now(self):
        custom = self.settings.get("sleep_command")
        if custom:
            _run(custom, shell=True)
        elif sys.platform == "win32":
            import ctypes
            # Not rundll32, which hibernates instead when hibernation is on.
            if not ctypes.windll.powrprof.SetSuspendState(False, False, False):
                raise OSError("Windows refused to sleep.")
        elif sys.platform == "darwin":
            _run(["pmset", "sleepnow"])
        else:
            _run(["systemctl", "suspend"])

    def schedule_wake(self, when):
        custom = self.settings.get("wake_command")
        if custom:
            _run(custom.format(epoch=int(when.timestamp()), iso=when.isoformat(timespec="seconds")), shell=True)
            return
        if sys.platform != "win32":
            raise OSError(self.support()[1])
        if not self.command:
            raise OSError("No command to run at wake time.")
        handle, path = tempfile.mkstemp(suffix=".xml", prefix="stationnet-wake-")
        try:
            with open(handle, "w", encoding="utf-16") as output:
                output.write(task_xml(when, self.command, self.root))
            _run(["schtasks", "/Create", "/TN", task_name(self.root), "/XML", path, "/F"])
        finally:
            Path(path).unlink(missing_ok=True)

    def cancel_wake(self):
        """Remove the wake task, if any. A custom wake_command cannot be undone."""
        if self.settings.get("wake_command") or sys.platform != "win32":
            return
        name = task_name(self.root)
        try:
            _run(["schtasks", "/Query", "/TN", name])
        except OSError:
            return  # no task to remove
        _run(["schtasks", "/Delete", "/TN", name, "/F"])


def main(argv=None):
    from . import instance, station_hours
    parser = argparse.ArgumentParser(description="Check or use this computer's sleep and wake support.")
    parser.add_argument("action", choices=("status", "sleep", "schedule-wake", "cancel-wake"))
    parser.add_argument("when", nargs="?", help="with schedule-wake: local time such as 2026-10-29T07:55")
    parser.add_argument("--home", help="the StationNet install folder (default: this one)")
    args = parser.parse_args(argv)
    core_dir = Path(__file__).resolve().parents[1]
    root = Path(args.home).resolve() if args.home else instance.install_root(core_dir)
    settings = station_hours.load_host(root)
    power = HostPower(root, manager_command(root, core_dir), settings)
    try:
        if args.action == "status":
            supported, note = power.support()
            print(f"Install: {root}")
            print(f"Wake timers: {'available' if supported else 'not available'}. {note}")
            if sys.platform == "win32" and not settings.get("wake_command"):
                print(f"Wake task: {task_name(root)}")
            print(f"Registered wake: {settings.get('registered_wake') or 'none'}")
            print(f"Sleep countdown: {settings['sleep_countdown_minutes']} min; "
                  f"wake lead: {settings['wake_lead_minutes']} min")
        elif args.action == "sleep":
            power.sleep_now()
        elif args.action == "schedule-wake":
            if not args.when:
                parser.error("schedule-wake needs a time")
            power.schedule_wake(datetime.fromisoformat(args.when))
            print(f"Wake scheduled for {args.when}.")
        else:
            power.cancel_wake()
            print("Wake cancelled.")
    except (OSError, ValueError) as exc:
        raise SystemExit(f"ERROR: {exc}")


if __name__ == "__main__":
    main()
