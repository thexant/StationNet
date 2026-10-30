"""Tkinter desktop launcher for the stations of one StationNet install."""
import os
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import font as tkfont
from tkinter import filedialog, messagebox

from . import autostart, instance, station_hours, themes
from . import tray as tray_module
from .host_power import HostPower, manager_command
from .hours_scheduler import HoursScheduler, HoursState
from .station_manager import (ACTIVE, DEFAULT_THEME, LAN_HOST, LOCAL_HOST, StationManager, clean_environment,
                              lan_address)
from .station_runtime import HostLock

REFRESH_MS = 1000
HOST_LOCK = ".manager.lock"
# Status colours are the same under every theme.
STATES = {
    "running": ("RUNNING", "#3fcf6e"),
    "starting": ("STARTING", "#e0b341"),
    "stopping": ("STOPPING", "#e0b341"),
    "restarting": ("RESTARTING", "#e0b341"),
    "stopped": ("STOPPED", "#8b8b86"),
    "crashed": ("CRASHED", "#ff3237"),
    "error": ("CONFIG ERROR", "#ff3237"),
}


def _luminance(color):
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(first, second):
    light, dark = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def readable(color, background, minimum=4.5):
    """``color``, or black or white if a custom theme makes it hard to read."""
    if contrast(color, background) >= minimum:
        return color
    return max(("#ffffff", "#000000"), key=lambda option: contrast(option, background))


def colors(palette):
    """Manager colour roles from a StationNet site palette (see station.css)."""
    card = palette["field"]
    return {
        "bg": palette["black"], "card": card, "border": palette["green-dim"],
        "heading": readable(palette["green-bright"], card), "text": readable(palette["white"], card),
        "muted": readable(palette["grey"], card), "link": readable(palette["cyan"], card),
        "error": readable(palette["red"], card), "field": palette["field"],
        "button": palette["button"], "button_border": palette["green"],
        "button_text": readable(palette["green"], palette["button"]),
        "primary_border": palette["green-bright"],
        "primary_text": readable(palette["green-bright"], palette["button"]),
        "hover": palette["green"], "hover_text": readable(palette["black"], palette["green"]),
    }


# The manager itself wears StationNet's default theme.
DEFAULT = colors(themes.palette(DEFAULT_THEME))
BG, TEXT, MUTED, FIELD, BORDER, ERROR = (DEFAULT[key] for key in ("bg", "text", "muted", "field", "border", "error"))


def open_path(path):
    path = str(path)
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


class Fonts:
    def __init__(self, root):
        family = tkfont.nametofont("TkDefaultFont").actual("family")
        if "Segoe UI" in tkfont.families(root):
            family = "Segoe UI"
        self.heading = tkfont.Font(root, family=family, size=11, weight="bold")
        self.title = tkfont.Font(root, family=family, size=12, weight="bold")
        self.body = tkfont.Font(root, family=family, size=10)
        self.small = tkfont.Font(root, family=family, size=9)
        self.small_bold = tkfont.Font(root, family=family, size=9, weight="bold")


def make_button(parent, fonts, text, command, primary=False, colors=DEFAULT):
    """A flat button with a 1px border; returns the frame to pack."""
    normal, hover = colors["button"], colors["hover"]
    text_color = colors["primary_text"] if primary else colors["button_text"]
    # Windows ignores highlight colors on buttons, so a frame draws the border.
    border = tk.Frame(parent, bg=colors["primary_border"] if primary else colors["button_border"], padx=1, pady=1)
    button = tk.Button(border, text=text, command=command, font=fonts.small_bold if primary else fonts.small,
                       bg=normal, fg=text_color, activebackground=hover, activeforeground=colors["hover_text"],
                       relief="flat", bd=0, padx=10, pady=3, cursor="hand2")
    button.pack()
    button.bind("<Enter>", lambda _event: button.configure(bg=hover, fg=colors["hover_text"]))
    button.bind("<Leave>", lambda _event: button.configure(bg=normal, fg=text_color))
    return border


def separator(parent):
    return tk.Frame(parent, bg=BORDER, height=1)


def host_lock(manager):
    """Held by the one open manager that carries out station hours."""
    return HostLock(manager.stations_dir / HOST_LOCK)


def countdown(seconds):
    minutes, seconds = divmod(max(0, int(seconds)), 60)
    return f"{minutes}:{seconds:02d}"


def short_time(moment, clock="12h"):
    return station_hours.format_time(moment.strftime("%H:%M"), clock)


class StationCard(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, highlightthickness=1, padx=12, pady=10)
        self.app = app
        fonts = app.fonts
        self.top = tk.Frame(self)
        self.top.pack(fill="x")
        self.name = tk.Label(self.top, font=fonts.title, anchor="w")
        self.name.pack(side="left")
        self.status = tk.Label(self.top, font=fonts.small_bold, anchor="e")
        self.status.pack(side="right")
        self.dot = tk.Canvas(self.top, width=12, height=12, highlightthickness=0)
        self.dot.pack(side="right", padx=(0, 6))
        self.dot_item = self.dot.create_oval(1, 1, 11, 11, outline="")
        self.summary = tk.Label(self, font=fonts.body, anchor="w", justify="left")
        self.summary.pack(fill="x", pady=(6, 0))
        self.address = tk.Label(self, font=fonts.body, anchor="w", justify="left")
        self.hours_line = tk.Label(self, font=fonts.small, anchor="w", justify="left")
        self.countdown = tk.Label(self, font=fonts.small_bold, anchor="w", justify="left")
        self.folder_note = tk.Label(self, font=fonts.small, anchor="w", justify="left")
        self.restart_note = tk.Label(self, font=fonts.small_bold, anchor="w", justify="left")
        self.detail = tk.Label(self, font=fonts.small, anchor="w", justify="left")
        self.buttons = tk.Frame(self)
        self.buttons.pack(fill="x", pady=(8, 0))
        self.button_key = None
        self.colors = None
        self.apply_colors(DEFAULT)
        self.bind("<Configure>", self._wrap)

    def apply_colors(self, colors):
        """Recolour the card for its station's theme."""
        self.colors = colors
        card = colors["card"]
        self.configure(bg=card, highlightbackground=colors["border"], highlightcolor=colors["border"])
        for widget in (self.top, self.buttons, self.dot, self.status):
            widget.configure(bg=card)
        for widget, role in ((self.name, "heading"), (self.summary, "muted"), (self.address, "link"),
                             (self.hours_line, "muted"), (self.folder_note, "muted"), (self.detail, "error")):
            widget.configure(bg=card, fg=colors[role])
        for widget in (self.restart_note, self.countdown):
            widget.configure(bg=card, fg=readable(STATES["restarting"][1], card))
        self.button_key = None  # rebuild the buttons in the new colours

    def _wrap(self, event):
        width = max(200, event.width - 30)
        self.detail.configure(wraplength=width)
        self.address.configure(wraplength=width)
        self.folder_note.configure(wraplength=width)
        self.restart_note.configure(wraplength=width)
        self.hours_line.configure(wraplength=width)

    def update_info(self, info, hours=None):
        """``hours`` is the station's HoursState from the scheduler, if any."""
        hours = hours or HoursState()
        state = info.state
        if self.app.manager.restarting(info.id) and state in ("stopping", "stopped", "crashed"):
            state = "restarting"
        label, color = STATES[state]
        colors_now = colors(self.app.manager.palette(info))
        if colors_now != self.colors:
            self.apply_colors(colors_now)
        self.name.configure(text=info.name)
        self.status.configure(text=label, fg=readable(color, self.colors["card"]))
        self.dot.itemconfigure(self.dot_item, fill=color)
        if info.error:
            self.summary.configure(text=f"Station id: {info.id}")
        else:
            access = {"local": "Local only", "lan": "Local network"}.get(info.access, f"Address {info.host}")
            https = " · HTTPS proxy" if info.https else ""
            self.summary.configure(text=f"Port {info.port} · {access}{https} · folder {info.folder}")
        self._show(self.address, "  ·  ".join(info.addresses), before=self.buttons)
        self._show(self.hours_line, self._hours_text(info), before=self.buttons)
        self._show(self.countdown, {
            "warning": f"Closing for station hours in {countdown(hours.seconds_left or 0)}",
            "closing": "Closing for station hours…",
            "sleep": f"This computer sleeps in {countdown(hours.seconds_left or 0)}",
        }.get(hours.phase), before=self.buttons)
        note = None
        if info.pending_folder and not info.rename_error:
            note = f"Folder will be renamed to {info.pending_folder} when the station stops or restarts."
        self._show(self.folder_note, note, before=self.buttons)
        restart = None
        if info.restart_needed and state != "restarting":
            restart = f"Restart to apply the new {', '.join(info.restart_needed)}."
        self._show(self.restart_note, restart, before=self.buttons)
        detail = (info.error or (info.detail if state in ("crashed", "stopped") else None) or info.rename_error
                  or hours.note)
        self._show(self.detail, detail, before=self.buttons)
        key = (state, info.can_force_stop, bool(info.error), bool(info.restart_needed), hours.phase)
        if key != self.button_key:
            self.button_key = key
            self._build_buttons(info, state, hours.phase)

    @staticmethod
    def _hours_text(info):
        if info.hours is None:
            return None
        text = f"Station hours {info.hours.text}"
        if info.extended_until is not None and info.extended_until > datetime.now():
            return f"{text} · extended until {short_time(info.extended_until, info.hours.clock)}"
        return f"{text} · sleeps after closing" if info.hours.sleep else text

    def _show(self, widget, text, before):
        if text:
            widget.configure(text=text)
            if not widget.winfo_manager():
                widget.pack(fill="x", pady=(4, 0), before=before)
        elif widget.winfo_manager():
            widget.pack_forget()

    def _build_buttons(self, info, state, phase=None):
        for child in self.buttons.winfo_children():
            child.destroy()
        app, station_id = self.app, info.id
        actions = []
        if phase == "warning":
            actions.append(("Stay Online", lambda: app.stay_online(station_id), True))
        elif phase == "sleep":
            actions.append(("Cancel Sleep", app.cancel_sleep, True))
        if info.error:
            actions.append(("Folder", lambda: app.open_folder(station_id)))
        elif state == "running":
            actions += [("Open", lambda: app.open_browser(station_id)),
                        ("Restart", lambda: app.act(app.manager.restart, station_id), bool(info.restart_needed)),
                        ("Stop", lambda: app.act(app.manager.stop, station_id))]
        elif state == "starting":
            actions.append(("Stop", lambda: app.act(app.manager.stop, station_id)))
        elif state in ("stopped", "crashed"):
            actions.append(("Start", lambda: app.act(app.manager.start, station_id)))
        elif state == "stopping" and info.can_force_stop:
            actions.append(("Force Stop", lambda: app.force_stop(station_id)))
        if not info.error:
            actions += [("Settings", lambda: app.open_settings(station_id)),
                        ("Hours", lambda: app.open_hours(station_id)),
                        ("Logs", lambda: app.open_log(station_id))]
        actions.append(("Delete", lambda: app.open_delete(station_id)))
        for text, command, *primary in actions:
            make_button(self.buttons, app.fonts, text, command, primary=bool(primary and primary[0]),
                        colors=self.colors).pack(side="left", padx=(0, 8))


class Dialog(tk.Toplevel):
    """A modal form in two columns, labels on the left."""

    def __init__(self, app, heading):
        super().__init__(app.root, bg=BG, padx=18, pady=16)
        self.app = app
        self.row = 0
        self.focus_widget = None
        self.title(heading.title())
        self.resizable(False, False)
        self.transient(app.root)
        self._label(heading.upper(), font=app.fonts.heading, pady=(0, 12))

    def _label(self, text, font=None, fg=TEXT, pady=0):
        label = tk.Label(self, text=text, font=font or self.app.fonts.body, bg=BG, fg=fg, justify="left",
                         wraplength=360)
        label.grid(row=self.row, column=0, columnspan=2, sticky="w", pady=pady)
        self.row += 1
        return label

    def _hint(self, text, parent=None, row=None):
        parent = parent or self
        label = tk.Label(parent, text=text, font=self.app.fonts.small, bg=BG, fg=MUTED, justify="left",
                         wraplength=300)
        if row is None:
            row, self.row = self.row, self.row + 1
        label.grid(row=row, column=1, sticky="w")
        return label

    def _entry(self, parent, variable, width=32, show=None):
        return tk.Entry(parent, textvariable=variable, width=width, font=self.app.fonts.body, bg=FIELD, fg=TEXT,
                        insertbackground=TEXT, relief="flat", highlightthickness=1, show=show,
                        highlightbackground=BORDER, highlightcolor=DEFAULT["heading"], disabledbackground=BG,
                        disabledforeground=MUTED)

    def _field(self, label, variable, width=32, parent=None, show=None):
        parent = parent or self
        row = self.row if parent is self else parent.grid_size()[1]
        tk.Label(parent, text=label, font=self.app.fonts.body, bg=BG, fg=MUTED).grid(row=row, column=0, sticky="w",
                                                                                  padx=(0, 12), pady=4)
        entry = self._entry(parent, variable, width=width, show=show)
        entry.grid(row=row, column=1, sticky="w", pady=4, ipady=3)
        if parent is self:
            self.row += 1
        return entry

    def _radio(self, parent, text, variable, value, command=None):
        return tk.Radiobutton(parent, text=text, variable=variable, value=value, command=command,
                              font=self.app.fonts.body, bg=BG, fg=TEXT, selectcolor=FIELD, activebackground=BG,
                              activeforeground=TEXT, highlightthickness=0, disabledforeground=MUTED)

    def _check(self, text, variable, command=None):
        check = tk.Checkbutton(self, text=text, variable=variable, command=command, font=self.app.fonts.body,
                               bg=BG, fg=TEXT, selectcolor=FIELD, activebackground=BG, activeforeground=TEXT,
                               highlightthickness=0, disabledforeground=MUTED)
        check.grid(row=self.row, column=1, sticky="w", pady=(6, 0))
        self.row += 1
        return check

    def _finish(self, buttons):
        """Add the error line and the buttons ((text, command, primary), right to left), then show."""
        self.error = tk.Label(self, font=self.app.fonts.small, bg=BG, fg=ERROR, wraplength=360, justify="left")
        self.error.grid(row=self.row, column=0, columnspan=2, sticky="w", pady=(8, 0))
        row = tk.Frame(self, bg=BG)
        row.grid(row=self.row + 1, column=0, columnspan=2, sticky="e", pady=(10, 0))
        make_button(row, self.app.fonts, "Cancel", self.destroy).pack(side="right")
        for text, command, primary in buttons:
            make_button(row, self.app.fonts, text, command, primary=primary).pack(side="right", padx=(0, 8))
        self.bind("<Return>", lambda _event: buttons[0][1]())
        self.bind("<Escape>", lambda _event: self.destroy())
        if self.focus_widget is not None:
            self.focus_widget.focus_set()
        self.update_idletasks()
        root = self.app.root
        x = root.winfo_rootx() + (root.winfo_width() - self.winfo_width()) // 2
        y = root.winfo_rooty() + 40
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.grab_set()


class StationForm(Dialog):
    """The server settings shared by New Station and Station Settings."""

    def __init__(self, app, heading, info=None):
        super().__init__(app, heading)
        self.name = tk.StringVar(value=info.name if info else "")
        self.port = tk.StringVar(value=str(info.port) if info else "")
        self.access = tk.StringVar(value=info.access if info else "local")
        self.address = tk.StringVar(value=info.host if info and info.access == "address" else lan_address() or "")
        self.https = tk.BooleanVar(value=info.https if info else False)
        self.name_entry = self.focus_widget = self._field("Name", self.name)
        self.name_hint = self._hint("")
        self._field("Port", self.port, width=8)
        tk.Label(self, text="Access", font=app.fonts.body, bg=BG, fg=MUTED).grid(row=self.row, column=0, sticky="nw",
                                                                            pady=4)
        access = tk.Frame(self, bg=BG)
        access.grid(row=self.row, column=1, sticky="w", pady=4)
        self.row += 1
        for text, value in (("Local only (this computer)", "local"),
                            ("Local network (every network adapter)", "lan"),
                            ("One address of this computer:", "address")):
            self._radio(access, text, self.access, value, command=self._access_changed).pack(anchor="w")
        self.address_entry = self._entry(access, self.address, width=18)
        self.address_entry.pack(anchor="w", padx=(22, 0), pady=(2, 0), ipady=3)
        self._check("Served through an HTTPS proxy", self.https)
        self._hint("Only when visitors reach the station at an https:// address; sign-in stops "
                   "working over plain http.")
        self.start_with_manager = tk.BooleanVar(value=info.start_with_manager if info else False)
        self._check("Start Station with Manager", self.start_with_manager)
        self._hint("Started when the Station Manager opens, unless it is already running or closed "
                   "for station hours.")
        self._access_changed()

    def _access_changed(self):
        self.address_entry.configure(state="normal" if self.access.get() == "address" else "disabled")

    def host(self):
        access = self.access.get()
        return LAN_HOST if access == "lan" else self.address.get() if access == "address" else LOCAL_HOST


class NewStationDialog(StationForm):
    def __init__(self, app):
        super().__init__(app, "New Station")
        manager = app.manager
        self.name.set(manager.suggest_name())
        try:
            self.port.set(str(manager.suggest_port()))
        except ValueError:
            pass
        self.start_now = tk.BooleanVar(value=True)
        self.with_sysop = tk.BooleanVar(value=False)
        self.username = tk.StringVar()
        self.password = tk.StringVar()
        self.confirm = tk.StringVar()
        self.description = tk.StringVar()
        self.registration = tk.StringVar(value="open")
        self._check("Create the SYSOP account now", self.with_sysop, command=self._sysop_changed)
        self.sysop_hint = self._hint("")
        self.sysop_row = self.row
        self.row += 1
        self.sysop = tk.Frame(self, bg=BG)
        self._field("Username", self.username, width=24, parent=self.sysop)
        self._field("Password", self.password, width=24, parent=self.sysop, show="•")
        self._field("Confirm", self.confirm, width=24, parent=self.sysop, show="•")
        self._field("Description", self.description, parent=self.sysop)
        tk.Label(self.sysop, text="Registration", font=app.fonts.body, bg=BG, fg=MUTED).grid(
            row=4, column=0, sticky="nw", pady=4)
        policy = tk.Frame(self.sysop, bg=BG)
        policy.grid(row=4, column=1, sticky="w", pady=4)
        for text, value in (("Open: anyone may create an account", "open"),
                            ("Private: a single-use invitation is required", "private"),
                            ("Disabled: no new accounts", "disabled")):
            self._radio(policy, text, self.registration, value).pack(anchor="w")
        self._check("Start the station now", self.start_now)
        self._sysop_changed()
        self._finish([("Create Station", self.create, True)])

    def _sysop_changed(self):
        if self.with_sysop.get():
            self.sysop.grid(row=self.sysop_row, column=0, columnspan=2, sticky="w", pady=(4, 0))
            self.sysop_hint.configure(text="The station starts fully set up, with this account as its SYSOP.")
            self.name_hint.configure(text="Shown to visitors; it can be changed later.")
        else:
            self.sysop.grid_remove()
            self.sysop_hint.configure(text="Leave unchecked to finish setup in the browser.")
            self.name_hint.configure(text="Optional. Leave blank to name it during setup.")

    def create(self):
        manager = self.app.manager
        sysop = None
        if self.with_sysop.get():
            sysop = {"username": self.username.get(), "password": self.password.get(),
                     "confirm_password": self.confirm.get(), "description": self.description.get(),
                     "registration_mode": self.registration.get()}
        try:
            info = manager.create_station(self.name.get(), self.port.get(), host=self.host(),
                                          https=self.https.get(), sysop=sysop,
                                          start_with_manager=self.start_with_manager.get())
        except (ValueError, OSError) as exc:
            self.error.configure(text=str(exc))
            return
        start = self.start_now.get()
        self.destroy()
        if start:
            self.app.act(manager.start, info.id)
        self.app.refresh(reschedule=False)


class SettingsDialog(StationForm):
    def __init__(self, app, info):
        super().__init__(app, "Station Settings", info)
        self.station_id = info.id
        self.name_hint.configure(text=f"Folder {info.folder}. Station content, accounts and modules are "
                                      "managed in the station's own SYSOP settings.")
        buttons = [("Save", self.save, info.state not in ACTIVE)]
        if info.state in ACTIVE:
            self._label("Changes take effect when the station restarts.", fg=STATES["starting"][1],
                        pady=(10, 0))
            buttons.append(("Save & Restart", lambda: self.save(restart=True), True))
        self._finish(buttons)

    def save(self, restart=False):
        manager = self.app.manager
        try:
            manager.update_station(self.station_id, self.name.get(), self.port.get(), self.host(),
                                   https=self.https.get(), start_with_manager=self.start_with_manager.get())
        except (ValueError, OSError) as exc:
            self.error.configure(text=str(exc))
            return
        self.destroy()
        if restart:
            self.app.act(manager.restart, self.station_id)
        self.app.refresh(reschedule=False)


class HoursDialog(Dialog):
    """A station's daily hours; saved to station.json and shown at once."""

    def __init__(self, app, info):
        super().__init__(app, "Station Hours")
        self.station_id = info.id
        try:
            saved = instance.load_config(info.data_dir).get("hours")
        except ValueError:
            saved = None
        saved = saved if isinstance(saved, dict) else {}
        clock = saved.get("clock") if saved.get("clock") in station_hours.CLOCKS else "12h"

        def shown(key, default):
            try:
                return station_hours.format_time(station_hours.parse_time(saved.get(key) or default), clock)
            except ValueError:
                return str(saved.get(key) or "")

        self.enabled = tk.BooleanVar(value=bool(saved.get("enabled")))
        self.open = tk.StringVar(value=shown("open", "08:00"))
        self.close = tk.StringVar(value=shown("close", "21:00"))
        self.clock = tk.StringVar(value=clock)
        self.warning = tk.StringVar(value=str(saved.get("warning_minutes") or station_hours.DEFAULT_WARNING))
        self.sleep = tk.BooleanVar(value=bool(saved.get("sleep")))
        self.message = tk.StringVar(value=saved.get("adjusted_message") or station_hours.DEFAULT_MESSAGE)
        self.early_message = tk.StringVar(value=saved.get("early_message") or station_hours.DEFAULT_EARLY_MESSAGE)
        self.label = tk.StringVar(value=saved.get("label") or station_hours.LABEL)
        self._label(f"{info.name}: shown under the station's header while it runs. The station closes at its "
                    "closing time and is started at its opening time while this manager is open.",
                    fg=MUTED, pady=(0, 8))
        self._check("Keep station hours", self.enabled, command=self._enabled_changed)
        self.widgets = [self._field("Label", self.label, width=24)]
        self._hint("Shown before the hours in the station's header.")
        self.widgets += [self._field("Opens", self.open, width=10), self._field("Closes", self.close, width=10)]
        self._hint("Such as 8:00 AM or 21:00. Closing before the opening time runs past midnight.")
        tk.Label(self, text="Show as", font=app.fonts.body, bg=BG, fg=MUTED).grid(row=self.row, column=0,
                                                                                 sticky="nw", pady=4)
        clocks = tk.Frame(self, bg=BG)
        clocks.grid(row=self.row, column=1, sticky="w", pady=4)
        self.row += 1
        for text, value in (("12-hour (8:00AM - 9:00PM)", "12h"), ("24-hour (08:00 - 21:00)", "24h")):
            radio = self._radio(clocks, text, self.clock, value)
            radio.pack(anchor="w")
            self.widgets.append(radio)
        self.widgets.append(self._field("Warning", self.warning, width=4))
        self._hint("Minutes before closing that Stay Online is offered here.")
        self.widgets.append(self._check("SLEEP: put this computer to sleep after closing", self.sleep))
        self._hint("Only if no other station is running then. The computer is woken before the next "
                   "opening (see Power for this computer's wake settings).")
        self.widgets.append(self._field("Extended", self.message))
        self._hint("Shown in place of the hours after Stay Online, until the next opening.")
        self.widgets.append(self._field("Early Open", self.early_message))
        self._hint("Shown in place of the hours when the station starts before its next opening.")
        self.focus_widget = self.widgets[1]
        self._enabled_changed()
        self._finish([("Save", self.save, True)])

    def _enabled_changed(self):
        state = "normal" if self.enabled.get() else "disabled"
        for widget in self.widgets:
            widget.configure(state=state)

    def save(self):
        values = {"enabled": self.enabled.get(), "open": self.open.get(), "close": self.close.get(),
                  "clock": self.clock.get(), "warning_minutes": self.warning.get(), "sleep": self.sleep.get(),
                  "adjusted_message": self.message.get(), "early_message": self.early_message.get(),
                  "label": self.label.get()}
        try:
            self.app.manager.update_hours(self.station_id, values)
        except (ValueError, OSError) as exc:
            self.error.configure(text=str(exc))
            return
        self.destroy()
        self.app.refresh(reschedule=False)


class PowerDialog(Dialog):
    """This computer's sleep and wake settings, shared by every station."""

    def __init__(self, app):
        super().__init__(app, "Sleep and Wake")
        root = app.manager.root
        settings = station_hours.load_host(root)
        self.countdown = tk.StringVar(value=str(settings["sleep_countdown_minutes"]))
        self.lead = tk.StringVar(value=str(settings["wake_lead_minutes"]))
        self._label("These apply to this computer and every station on it.", fg=MUTED, pady=(0, 8))
        self.focus_widget = self._field("Sleep after", self.countdown, width=4)
        self._hint("Minutes Cancel Sleep is offered after a station with SLEEP closes.")
        self._field("Wake", self.lead, width=4)
        self._hint("Minutes before the earliest opening that the computer is woken.")
        _supported, note = HostPower(root, settings=settings).support()
        scheduler = app.scheduler
        if scheduler is None:
            status = "Another open Station Manager is carrying out station hours."
        elif scheduler.wake_error:
            status = scheduler.wake_error
        elif scheduler.next_wake:
            status = f"Next wake: {scheduler.next_wake:%a %d %b} {short_time(scheduler.next_wake)}."
        else:
            status = "No wake is scheduled: no station with hours has SLEEP on."
        self._label(status, pady=(10, 0))
        self._label(note, font=app.fonts.small, fg=MUTED, pady=(4, 0))
        readme = next((path for path in (root / "README.md", app.core_dir / "README.md") if path.is_file()), None)
        if readme is not None:
            link = self._label("Open the setup instructions (README, Station Hours)", font=app.fonts.small,
                               fg=DEFAULT["link"], pady=(4, 0))
            link.configure(cursor="hand2")
            link.bind("<Button-1>", lambda _event: app._open(readme))
        self._finish([("Save", self.save, True)])

    def save(self):
        try:
            values = station_hours.check_host(self.countdown.get(), self.lead.get())
            station_hours.save_host(self.app.manager.root, **values)
        except (ValueError, OSError) as exc:
            self.error.configure(text=str(exc))
            return
        self.destroy()
        self.app.refresh(reschedule=False)


class OptionsDialog(Dialog):
    """How the Station Manager window itself behaves on this computer."""

    def __init__(self, app):
        super().__init__(app, "Manager Options")
        settings = station_hours.load_host(app.manager.root)
        self.fullscreen = tk.BooleanVar(value=bool(settings["fullscreen"]))
        self.startup = tk.BooleanVar(value=autostart.enabled(app.manager.root))
        self.tray = tk.BooleanVar(value=bool(settings["minimize_to_tray"]))
        self._label("These apply to this computer's Station Manager.", fg=MUTED, pady=(0, 8))
        self._check("Fullscreen", self.fullscreen)
        self._hint("F11 switches fullscreen on and off; Esc leaves it.")
        startup = self._check("Start with Windows", self.startup)
        if autostart.SUPPORTED:
            self._hint("Opens the manager minimized when you sign in to Windows.")
        else:
            startup.configure(state="disabled")
            self._hint("Available only on Windows.")
        tray = self._check("Minimize to tray", self.tray)
        problem = tray_module.unavailable(app.icon)
        if problem:
            tray.configure(state="disabled")
            self._hint(problem)
        else:
            self._hint("Minimizing hides the window to the notification area; open it again from there.")
        self._finish([("Save", self.save, True)])

    def save(self):
        app = self.app
        try:
            if autostart.SUPPORTED and self.startup.get() != autostart.enabled(app.manager.root):
                autostart.set_enabled(app.manager.root, self.startup.get(),
                                      manager_command(app.manager.root, app.core_dir))
            station_hours.save_host(app.manager.root, fullscreen=self.fullscreen.get(),
                                    minimize_to_tray=self.tray.get())
        except OSError as exc:
            self.error.configure(text=str(exc))
            return
        self.destroy()
        app.set_fullscreen(self.fullscreen.get())
        app.minimize_to_tray = self.tray.get()


class DeleteDialog(Dialog):
    """Stop a station, optionally back it up, and delete its folder."""

    POLL_MS = 500

    def __init__(self, app, info):
        super().__init__(app, "Delete Station")
        self.info = info
        self.busy = False
        self.force_offered = False
        self.backup_path = None
        self.result = None
        self._label(f"This permanently deletes {info.name}: its accounts, messages, files, programs, and "
                    f"settings in folder {info.folder}.", fg=ERROR, pady=(0, 8))
        if info.state in ACTIVE:
            self._label("It is running and will be stopped first.", fg=STATES["starting"][1], pady=(0, 8))
        self.backup = tk.BooleanVar(value=True)
        self.confirm = tk.StringVar()
        self.backup_check = self._check("Back up the station first", self.backup)
        self._hint("Saves a .zip of its folder. WIKI (.zim) archives are not included.")
        self.confirm_entry = self.focus_widget = self._field("Confirm", self.confirm)
        self._hint(f'Type "{info.name}" to delete it.')
        self.status = self._label("", font=app.fonts.small, fg=MUTED, pady=(8, 0))
        self._finish([("Delete Station", self.delete, True)])
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def destroy(self):
        if self.busy:
            self.app.manager.cancel_delete(self.info.id)
        super().destroy()

    def _set_busy(self, busy, status=""):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.backup_check.configure(state=state)
        self.confirm_entry.configure(state=state)
        self.status.configure(text=status)

    def _fail(self, message):
        self.app.manager.cancel_delete(self.info.id)
        self._set_busy(False)
        self.error.configure(text=message)

    def delete(self):
        if self.busy:
            return
        info = self.info
        if self.confirm.get().strip() != info.name:
            self.error.configure(text="Type the station's name exactly to confirm.")
            return
        self.error.configure(text="")
        self.backup_path = None
        if self.backup.get():
            path = filedialog.asksaveasfilename(
                parent=self, title="Save station backup", defaultextension=".zip",
                filetypes=[("Zip archive", "*.zip")], initialdir=str(Path.home()),
                initialfile=f"{info.folder}-{datetime.now():%Y-%m-%d}.zip")
            if not path:
                return
            self.backup_path = Path(path)
        manager = self.app.manager
        manager.begin_delete(info.id)
        self._set_busy(True, "Stopping…")
        try:
            manager.stop(info.id)
        except OSError as exc:
            self._fail(str(exc))
            return
        self._wait_stopped()

    def _wait_stopped(self):
        if not self.busy or not self.winfo_exists():
            return
        info = self.app.manager.station(self.info.id)
        if info.state in ACTIVE:
            if info.can_force_stop and not self.force_offered:
                self.force_offered = True
                self.app.force_stop(info.id)
            self.after(self.POLL_MS, self._wait_stopped)
        elif self.backup_path is not None:
            self._set_busy(True, f"Backing up to {self.backup_path}…")
            threading.Thread(target=self._back_up, daemon=True).start()
            self.after(self.POLL_MS, self._wait_backup)
        else:
            self._remove()

    def _back_up(self):
        try:
            self.result = self.app.manager.backup_station(self.info.id, self.backup_path)
        except Exception as exc:  # noqa: BLE001 - any failure must reach the dialog, or it waits forever
            self.result = exc

    def _wait_backup(self):
        if not self.busy or not self.winfo_exists():
            return
        if self.result is None:
            self.after(self.POLL_MS, self._wait_backup)
        elif isinstance(self.result, Exception):
            self._fail(f"The backup failed, so nothing was deleted: {self.result}")
        else:
            self._remove()

    def _remove(self):
        info, app = self.info, self.app
        self.status.configure(text="Deleting…")
        try:
            app.manager.delete_station(info.id)
        except (ValueError, OSError) as exc:
            self._fail(str(exc))
            app.refresh(reschedule=False)
            return
        self.busy = False
        self.destroy()
        app.refresh(reschedule=False)
        message = f"Deleted {info.name}."
        if isinstance(self.result, tuple):
            path, skipped = self.result
            message += f"\n\nBackup saved to {path}"
            if skipped:
                noun = "archive was" if skipped == 1 else "archives were"
                message += f"\n{skipped} WIKI {noun} not included."
        messagebox.showinfo("StationNet Manager", message, parent=app.root)


class ManagerApp:
    def __init__(self, root, manager, icon=None):
        self.root = root
        self.manager = manager
        self.core_dir = manager.core_dir
        self.hours_states = {}
        self.scheduler = None
        self.host_lock = host_lock(manager)
        self.fonts = Fonts(root)
        self.cards = {}
        self.order = []
        self.icon = icon if icon and Path(icon).is_file() else None
        self.tray = None
        settings = station_hours.load_host(manager.root)
        self.minimize_to_tray = bool(settings["minimize_to_tray"])
        root.title("StationNet Manager")
        root.configure(bg=BG)
        root.geometry("760x600")
        root.minsize(520, 360)
        if self.icon:
            try:
                self._icon = tk.PhotoImage(file=str(icon))
                root.iconphoto(True, self._icon)
            except tk.TclError:
                pass
        shell = tk.Frame(root, bg=BG, padx=12, pady=10)
        shell.pack(fill="both", expand=True)
        tk.Label(shell, text="STATIONNET MANAGER", font=self.fonts.heading, bg=BG, fg=DEFAULT["heading"],
                 anchor="w").pack(fill="x")
        separator(shell).pack(fill="x", pady=(8, 10))
        bar = tk.Frame(shell, bg=BG)
        bar.pack(fill="x", pady=(0, 6))
        self.count = tk.Label(bar, font=self.fonts.small, bg=BG, fg=MUTED)
        self.count.pack(side="left")
        make_button(bar, self.fonts, "+ New Station", lambda: NewStationDialog(self),
                    primary=True).pack(side="right")
        make_button(bar, self.fonts, "Sleep & Wake", lambda: PowerDialog(self)).pack(side="right", padx=(0, 8))
        make_button(bar, self.fonts, "Options", lambda: OptionsDialog(self)).pack(side="right", padx=(0, 8))
        self.power_note = tk.Label(bar, font=self.fonts.small, bg=BG, fg=MUTED)
        self.power_note.pack(side="left", padx=(16, 0))
        self._build_list(shell)
        separator(shell).pack(fill="x", pady=(10, 6))
        footer = tk.Frame(shell, bg=BG)
        footer.pack(fill="x")
        self.running = tk.Label(footer, font=self.fonts.small_bold, bg=BG, fg=STATES["running"][1])
        self.running.pack(side="left")
        exit_link = tk.Label(footer, text="Exit", font=self.fonts.small, bg=BG, fg=DEFAULT["link"], cursor="hand2")
        exit_link.pack(side="right")
        exit_link.bind("<Button-1>", lambda _event: self.close())
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.closing_note = tk.Label(footer, text="Closing the manager leaves stations running.",
                                     font=self.fonts.small, bg=BG, fg=MUTED)
        self.closing_note.pack(side="right", padx=(0, 16))
        self.empty = tk.Label(self.inner, text="No stations yet. Use + New Station to create one.",
                              font=self.fonts.body, bg=BG, fg=MUTED)
        root.bind("<F11>", lambda _event: self.set_fullscreen(not self.fullscreen()))
        root.bind("<Escape>", lambda _event: self.set_fullscreen(False) if self.fullscreen() else None)
        root.bind("<Unmap>", self._unmapped)
        self.set_fullscreen(bool(settings["fullscreen"]))
        self._autostart()
        self.refresh()

    def fullscreen(self):
        try:
            return bool(self.root.attributes("-fullscreen"))
        except tk.TclError:
            return False

    def set_fullscreen(self, on):
        try:
            self.root.attributes("-fullscreen", bool(on))
        except tk.TclError:
            pass

    def _autostart(self):
        """Start each station marked Start Station with Manager that is stopped
        and, if it keeps hours, open now."""
        now = datetime.now()
        problems = []
        for info in self.manager.list_stations():
            if info.error or not info.start_with_manager or info.state not in ("stopped", "crashed"):
                continue
            extended = info.extended_until is not None and info.extended_until > now
            if info.hours is not None and not extended and not station_hours.is_open(info.hours, now):
                continue
            try:
                self.manager.start(info.id)
            except (ValueError, OSError) as exc:
                problems.append(f"{info.name}: {exc}")
        if problems:
            self.root.after(0, lambda: messagebox.showerror(
                "StationNet Manager", "Could not start:\n\n" + "\n".join(problems), parent=self.root))

    def _unmapped(self, event):
        # <Unmap> also reaches the root for its children; only a minimized window counts.
        if event.widget is not self.root or not self.minimize_to_tray:
            return
        self.root.after_idle(self._to_tray)

    def _to_tray(self):
        if self.root.state() != "iconic" or not self.minimize_to_tray:
            return
        if self.tray is None:
            if tray_module.unavailable(self.icon):
                return
            try:
                self.tray = tray_module.TrayIcon(self.icon, "StationNet Manager",
                                                 on_show=lambda: self.root.after(0, self.show_window),
                                                 on_exit=lambda: self.root.after(0, self.close))
            except Exception:  # noqa: BLE001 - a tray that fails leaves an ordinary minimized window
                self.minimize_to_tray = False
                return
        self.root.withdraw()
        self.tray.show()

    def show_window(self):
        if self.tray is not None:
            self.tray.hide()
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _build_list(self, parent):
        holder = tk.Frame(parent, bg=BG)
        holder.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(holder, bg=BG, highlightthickness=0)
        self.scrollbar = tk.Scrollbar(holder, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=BG)
        window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda _event: self._layout_scroll())
        self.canvas.bind("<Configure>", lambda event: (self.canvas.itemconfigure(window, width=event.width),
                                                       self._layout_scroll()))
        self.root.bind_all("<MouseWheel>", lambda event: self._scroll(-event.delta // 120))
        self.root.bind_all("<Button-4>", lambda _event: self._scroll(-1))
        self.root.bind_all("<Button-5>", lambda _event: self._scroll(1))

    def _layout_scroll(self):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        needed = self.inner.winfo_reqheight() > self.canvas.winfo_height()
        if needed and not self.scrollbar.winfo_manager():
            self.scrollbar.pack(side="right", fill="y", before=self.canvas)
        elif not needed and self.scrollbar.winfo_manager():
            self.scrollbar.pack_forget()
            self.canvas.yview_moveto(0)

    def _scroll(self, units):
        if self.inner.winfo_height() > self.canvas.winfo_height():
            self.canvas.yview_scroll(units, "units")

    def refresh(self, reschedule=True):
        try:
            stations = self.manager.poll()
        except OSError as exc:
            self.count.configure(text=f"Cannot read {self.manager.stations_dir}: {exc}")
            stations = None
        if stations is not None:
            self._run_hours(stations)
            self._render(stations)
        if reschedule:
            self.root.after(REFRESH_MS, self.refresh)

    def _render(self, stations):
        ids = [info.id for info in stations]
        for station_id in list(self.cards):
            if station_id not in ids:
                self.cards.pop(station_id).destroy()
        for info in stations:
            card = self.cards.get(info.id)
            if card is None:
                card = self.cards[info.id] = StationCard(self.inner, self)
            card.update_info(info, self.hours_states.get(info.id))
        if ids != self.order:
            # Re-pack in order so new stations take their sorted place.
            self.order = ids
            for station_id in ids:
                self.cards[station_id].pack_forget()
            for station_id in ids:
                self.cards[station_id].pack(fill="x", pady=(0, 8))
        if stations:
            self.empty.pack_forget()
        else:
            self.empty.pack(pady=30)
        noun = "station" if len(stations) == 1 else "stations"
        self.count.configure(text=f"{len(stations)} {noun} installed")
        running = sum(info.state == "running" for info in stations)
        self.running.configure(text=f"● {running} running" if running else "",
                               fg=STATES["running"][1])
        with_hours = any(info.hours is not None for info in stations)
        self.closing_note.configure(text="Closing the manager leaves stations running; station hours "
                                         "are kept only while it is open." if with_hours else
                                         "Closing the manager leaves stations running.")
        scheduler, note, color = self.scheduler, "", MUTED
        if with_hours and scheduler is None:
            note = "Station hours are kept by another open Station Manager."
        elif scheduler is not None and scheduler.wake_error:
            note, color = scheduler.wake_error, ERROR
        elif scheduler is not None and scheduler.next_wake:
            note = f"Next wake {scheduler.next_wake:%a} {short_time(scheduler.next_wake)}"
        self.power_note.configure(text=note, fg=color)

    def _run_hours(self, stations):
        """Carry out station hours, if this is the manager that keeps them."""
        if self.scheduler is None and self.host_lock.acquire():
            settings = station_hours.load_host(self.manager.root)
            power = HostPower(self.manager.root, manager_command(self.manager.root, self.core_dir), settings)
            self.scheduler = HoursScheduler(self.manager, power)
        if self.scheduler is None:
            self.hours_states = {}
            return
        before = {key for key, state in self.hours_states.items() if state.phase in ("warning", "sleep")}
        try:
            self.hours_states = self.scheduler.tick(stations)
        except (OSError, ValueError) as exc:
            self.power_note.configure(text=f"Station hours: {exc}", fg=ERROR)
            return
        now = {key for key, state in self.hours_states.items() if state.phase in ("warning", "sleep")}
        if now - before:
            self._attention()
        # Show a stop the scheduler just asked for without waiting a second.
        for index, info in enumerate(stations):
            if info.state == "running" and self.hours_states.get(info.id, HoursState()).phase == "closing":
                stations[index] = self.manager.station(info.id)

    def _attention(self):
        """Bring the window forward so Stay Online or Cancel Sleep is seen."""
        root = self.root
        if self.tray is not None:
            self.tray.hide()
        root.deiconify()
        root.lift()
        try:
            root.attributes("-topmost", True)
            root.after(800, lambda: root.attributes("-topmost", False))
        except tk.TclError:
            pass
        root.bell()

    def stay_online(self, station_id):
        if self.scheduler is not None:
            self.act(self.scheduler.stay_online, station_id)

    def cancel_sleep(self):
        if self.scheduler is not None:
            self.scheduler.cancel_sleep()
        self.refresh(reschedule=False)

    def close(self):
        if self.scheduler is not None:
            self.scheduler.shutdown()
        self.host_lock.release()
        if self.tray is not None:
            self.tray.stop()
        self.root.destroy()

    def act(self, action, station_id):
        try:
            action(station_id)
        except (ValueError, OSError) as exc:
            messagebox.showerror("StationNet Manager", str(exc), parent=self.root)
        self.refresh(reschedule=False)

    def force_stop(self, station_id):
        info = self.manager.station(station_id)
        if not messagebox.askyesno(
                "Force stop",
                f"{info.name} has not stopped on its own. End its process now?\n\n"
                "Unsaved work in progress may be lost, and a WIKI reader it started may keep running "
                "until the station is started and stopped again.",
                icon="warning", parent=self.root):
            return
        try:
            if not self.manager.force_stop(station_id):
                messagebox.showinfo("StationNet Manager", f"{info.name} is no longer running.", parent=self.root)
        except OSError as exc:
            messagebox.showerror("StationNet Manager", str(exc), parent=self.root)
        self.refresh(reschedule=False)

    def open_browser(self, station_id):
        info = self.manager.station(station_id)
        if info.addresses:
            webbrowser.open(info.addresses[0] + "/")

    def open_settings(self, station_id):
        info = self.manager.station(station_id)
        if info.error:
            messagebox.showerror("StationNet Manager", info.error, parent=self.root)
            return
        SettingsDialog(self, info)

    def open_hours(self, station_id):
        info = self.manager.station(station_id)
        if info.error:
            messagebox.showerror("StationNet Manager", info.error, parent=self.root)
            return
        HoursDialog(self, info)

    def open_delete(self, station_id):
        DeleteDialog(self, self.manager.station(station_id))

    def open_log(self, station_id):
        path = self.manager.station(station_id).log_path
        if not path.is_file():
            messagebox.showinfo("StationNet Manager", "This station has no log yet. Start it to create one.",
                                parent=self.root)
            return
        self._open(path)

    def open_folder(self, station_id):
        self._open(self.manager.station(station_id).data_dir)

    def _open(self, path):
        try:
            open_path(path)
        except OSError as exc:
            messagebox.showerror("StationNet Manager", f"Cannot open {path}: {exc}", parent=self.root)


def wake_handled(source_root):
    """True if another open manager already keeps this install's station hours."""
    root = instance.install_root(source_root)
    return HostLock(instance.stations_root(root) / HOST_LOCK).held_elsewhere()


def _core_dir(source_root):
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(source_root)


def launch_minimized(source_root):
    """Start a minimized manager in its own process, outside the wake task that
    Task Scheduler may end or replace."""
    root = instance.install_root(source_root)
    command = autostart.minimized_command(manager_command(root, _core_dir(source_root)))
    options = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
               "cwd": str(root)}
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            subprocess.Popen(command, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **options)
        except OSError:
            subprocess.Popen(command, creationflags=flags, **options)
    else:
        subprocess.Popen(command, start_new_session=True, **options)


def main(source_root, minimized=False):
    """``minimized`` starts the window minimized, as the wake timer does."""
    clean_environment()
    source_root = Path(source_root)
    core_dir = _core_dir(source_root)
    if getattr(sys, "frozen", False):
        icon = Path(getattr(sys, "_MEIPASS", core_dir)) / "icon.png"
    else:
        icon = source_root / "icon.png"
    manager = StationManager(instance.install_root(source_root), core_dir)
    root = tk.Tk()
    app = ManagerApp(root, manager, icon=icon)
    if minimized:
        root.iconify()
        # A window minimized before it was ever shown sends no <Unmap>.
        root.after(500, app._to_tray)
    root.mainloop()
