"""StationNet Station Manager: a desktop launcher for this install's stations."""
import argparse
import os
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Open the StationNet Station Manager.")
    parser.add_argument("--hours-wake", action="store_true",
                        help="run by the station hours wake timer: open minimized unless a manager is open")
    parser.add_argument("--minimized", action="store_true", help="start with the window minimized")
    parser.add_argument("--home", help="the StationNet install folder (default: this one)")
    args = parser.parse_args(argv)
    if args.home:
        os.environ["STATIONNET_HOME"] = args.home
    try:
        import tkinter  # noqa: F401
    except ImportError:
        raise SystemExit("ERROR: The Station Manager needs Tkinter. Install it (for example "
                         "`sudo apt install python3-tk`) and try again.")
    # Imported late so the Tkinter check runs before loading StationNet.
    from stationnet import manager_gui
    source_root = Path(__file__).resolve().parent
    if args.hours_wake:
        # An open manager keeps station hours already; the computer was only woken.
        if not manager_gui.wake_handled(source_root):
            manager_gui.launch_minimized(source_root)
        return
    manager_gui.main(source_root, minimized=args.minimized)


if __name__ == "__main__":
    main()
