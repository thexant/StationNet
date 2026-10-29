"""StationNet Station Manager: a desktop launcher for this install's stations."""
from pathlib import Path


def main():
    try:
        import tkinter  # noqa: F401
    except ImportError:
        raise SystemExit("ERROR: The Station Manager needs Tkinter. Install it (for example "
                         "`sudo apt install python3-tk`) and try again.")
    # Imported late so the Tkinter check runs before loading StationNet.
    from stationnet import manager_gui
    manager_gui.main(Path(__file__).resolve().parent)


if __name__ == "__main__":
    main()
