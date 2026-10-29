"""The Station Manager's notification-area icon, for Minimize to tray."""
from pathlib import Path

try:
    import pystray
    from PIL import Image
except ImportError as exc:  # pragma: no cover - depends on the install
    pystray = None
    IMPORT_ERROR = str(exc)
else:
    IMPORT_ERROR = None


def unavailable(icon_path):
    """Why a tray icon cannot be shown here, or None."""
    if pystray is None:
        return f"Needs the pystray package ({IMPORT_ERROR})."
    if not icon_path or not Path(icon_path).is_file():
        return "The manager's icon is missing."
    return None


class TrayIcon:
    """A hidden tray icon, shown while the window is in the tray.

    pystray runs on its own thread, so ``on_show`` and ``on_exit`` are called
    there; the caller hands them back to Tk with ``root.after``.
    """

    def __init__(self, icon_path, title, on_show, on_exit):
        image = Image.open(icon_path)
        image.load()
        menu = pystray.Menu(pystray.MenuItem("Show", lambda: on_show(), default=True),
                            pystray.MenuItem("Exit", lambda: on_exit()))
        self.icon = pystray.Icon("StationNetManager", image, title, menu)
        # A setup that does nothing keeps the icon hidden until show().
        self.icon.run_detached(setup=lambda _icon: None)

    def show(self):
        self.icon.visible = True

    def hide(self):
        self.icon.visible = False

    def stop(self):
        self.icon.stop()
