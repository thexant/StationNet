"""Tkinter desktop launcher for the stations of one StationNet install."""
import os
import subprocess
import sys
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import font as tkfont
from tkinter import messagebox

from . import instance
from .station_manager import StationManager, clean_environment

REFRESH_MS = 1000
BG = "#0f1519"
CARD = "#121b20"
BORDER = "#2a3b40"
TEXT = "#e3eaee"
MUTED = "#98a6ad"
FIELD = "#0b1114"
BUTTON = "#141e23"
BUTTON_HOVER = "#1d2b31"
PRIMARY = "#1e3a2c"
PRIMARY_HOVER = "#27513b"
PRIMARY_TEXT = "#c9f5d6"
ERROR = "#f0716a"
STATES = {
    "running": ("RUNNING", "#3fcf6e"),
    "starting": ("STARTING", "#e0b341"),
    "stopping": ("STOPPING", "#e0b341"),
    "restarting": ("RESTARTING", "#e0b341"),
    "stopped": ("STOPPED", "#7d8a91"),
    "crashed": ("CRASHED", ERROR),
    "error": ("CONFIG ERROR", ERROR),
}


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


def make_button(parent, fonts, text, command, primary=False):
    """A flat button with a 1px border; returns the frame to pack."""
    normal, hover = (PRIMARY, PRIMARY_HOVER) if primary else (BUTTON, BUTTON_HOVER)
    # Windows ignores highlight colors on buttons, so a frame draws the border.
    border = tk.Frame(parent, bg=PRIMARY_HOVER if primary else BORDER, padx=1, pady=1)
    button = tk.Button(border, text=text, command=command, font=fonts.small_bold if primary else fonts.small,
                       bg=normal, fg=PRIMARY_TEXT if primary else TEXT, activebackground=hover,
                       activeforeground=TEXT, relief="flat", bd=0, padx=10, pady=3, cursor="hand2")
    button.pack()
    button.bind("<Enter>", lambda _event: button.configure(bg=hover))
    button.bind("<Leave>", lambda _event: button.configure(bg=normal))
    return border


def separator(parent):
    return tk.Frame(parent, bg=BORDER, height=1)


class StationCard(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=CARD, highlightthickness=1, highlightbackground=BORDER, padx=12, pady=10)
        self.app = app
        fonts = app.fonts
        top = tk.Frame(self, bg=CARD)
        top.pack(fill="x")
        self.name = tk.Label(top, font=fonts.title, bg=CARD, fg=TEXT, anchor="w")
        self.name.pack(side="left")
        self.status = tk.Label(top, font=fonts.small_bold, bg=CARD, anchor="e")
        self.status.pack(side="right")
        self.dot = tk.Canvas(top, width=12, height=12, bg=CARD, highlightthickness=0)
        self.dot.pack(side="right", padx=(0, 6))
        self.dot_item = self.dot.create_oval(1, 1, 11, 11, outline="")
        self.summary = tk.Label(self, font=fonts.body, bg=CARD, fg=MUTED, anchor="w", justify="left")
        self.summary.pack(fill="x", pady=(6, 0))
        self.address = tk.Label(self, font=fonts.body, bg=CARD, fg=TEXT, anchor="w", justify="left")
        self.folder_note = tk.Label(self, font=fonts.small, bg=CARD, fg=MUTED, anchor="w", justify="left")
        self.detail = tk.Label(self, font=fonts.small, bg=CARD, fg=ERROR, anchor="w", justify="left")
        self.buttons = tk.Frame(self, bg=CARD)
        self.buttons.pack(fill="x", pady=(8, 0))
        self.button_key = None
        self.bind("<Configure>", self._wrap)

    def _wrap(self, event):
        width = max(200, event.width - 30)
        self.detail.configure(wraplength=width)
        self.address.configure(wraplength=width)
        self.folder_note.configure(wraplength=width)

    def update_info(self, info):
        state = info.state
        if self.app.manager.restarting(info.id) and state in ("stopping", "stopped", "crashed"):
            state = "restarting"
        label, color = STATES[state]
        self.name.configure(text=info.name)
        self.status.configure(text=label, fg=color)
        self.dot.itemconfigure(self.dot_item, fill=color)
        if info.error:
            self.summary.configure(text=f"Station id: {info.id}")
        else:
            access = "Local network" if info.lan else "Local only"
            self.summary.configure(text=f"Port {info.port} · {access} · folder {info.folder}")
        self._show(self.address, "  ·  ".join(info.addresses), before=self.buttons)
        note = None
        if info.pending_folder and not info.rename_error:
            note = f"Folder will be renamed to {info.pending_folder} when the station stops or restarts."
        self._show(self.folder_note, note, before=self.buttons)
        detail = info.error or (info.detail if state in ("crashed", "stopped") else None) or info.rename_error
        self._show(self.detail, detail, before=self.buttons)
        key = (state, info.can_force_stop, bool(info.error))
        if key != self.button_key:
            self.button_key = key
            self._build_buttons(info, state)

    def _show(self, widget, text, before):
        if text:
            widget.configure(text=text)
            if not widget.winfo_manager():
                widget.pack(fill="x", pady=(4, 0), before=before)
        elif widget.winfo_manager():
            widget.pack_forget()

    def _build_buttons(self, info, state):
        for child in self.buttons.winfo_children():
            child.destroy()
        app, station_id = self.app, info.id
        actions = []
        if info.error:
            actions.append(("Folder", lambda: app.open_folder(station_id)))
        elif state == "running":
            actions += [("Open", lambda: app.open_browser(station_id)),
                        ("Restart", lambda: app.act(app.manager.restart, station_id)),
                        ("Stop", lambda: app.act(app.manager.stop, station_id))]
        elif state == "starting":
            actions.append(("Stop", lambda: app.act(app.manager.stop, station_id)))
        elif state in ("stopped", "crashed"):
            actions.append(("Start", lambda: app.act(app.manager.start, station_id)))
        elif state == "stopping" and info.can_force_stop:
            actions.append(("Force Stop", lambda: app.force_stop(station_id)))
        if not info.error:
            actions.append(("Logs", lambda: app.open_log(station_id)))
        for text, command in actions:
            make_button(self.buttons, app.fonts, text, command).pack(side="left", padx=(0, 8))


class NewStationDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root, bg=BG, padx=18, pady=16)
        self.app = app
        fonts = app.fonts
        self.title("New Station")
        self.resizable(False, False)
        self.transient(app.root)
        self.name = tk.StringVar()
        self.port = tk.StringVar()
        self.lan = tk.BooleanVar(value=False)
        self.start_now = tk.BooleanVar(value=True)
        try:
            self.port.set(str(app.manager.suggest_port()))
        except ValueError:
            pass
        tk.Label(self, text="NEW STATION", font=fonts.heading, bg=BG, fg=TEXT).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        name_entry = self._field(1, "Name", self.name)
        tk.Label(self, text="Optional. Leave blank to name it during setup.", font=fonts.small, bg=BG,
                 fg=MUTED).grid(row=2, column=1, sticky="w")
        self._field(3, "Port", self.port, width=8)
        tk.Label(self, text="Access", font=fonts.body, bg=BG, fg=MUTED).grid(row=4, column=0, sticky="nw", pady=4)
        access = tk.Frame(self, bg=BG)
        access.grid(row=4, column=1, sticky="w", pady=4)
        for text, value in (("Local only (this computer)", False), ("Local network (other devices)", True)):
            tk.Radiobutton(access, text=text, variable=self.lan, value=value, font=fonts.body, bg=BG, fg=TEXT,
                           selectcolor=FIELD, activebackground=BG, activeforeground=TEXT,
                           highlightthickness=0).pack(anchor="w")
        tk.Checkbutton(self, text="Start the station now", variable=self.start_now, font=fonts.body, bg=BG,
                       fg=TEXT, selectcolor=FIELD, activebackground=BG, activeforeground=TEXT,
                       highlightthickness=0).grid(row=5, column=1, sticky="w", pady=(6, 0))
        self.error = tk.Label(self, font=fonts.small, bg=BG, fg=ERROR, wraplength=340, justify="left")
        self.error.grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))
        row = tk.Frame(self, bg=BG)
        row.grid(row=7, column=0, columnspan=2, sticky="e", pady=(10, 0))
        make_button(row, fonts, "Cancel", self.destroy).pack(side="right")
        make_button(row, fonts, "Create Station", self.create, primary=True).pack(side="right", padx=(0, 8))
        self.bind("<Return>", lambda _event: self.create())
        self.bind("<Escape>", lambda _event: self.destroy())
        name_entry.focus_set()
        self.update_idletasks()
        x = app.root.winfo_rootx() + (app.root.winfo_width() - self.winfo_width()) // 2
        y = app.root.winfo_rooty() + 60
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.grab_set()

    def _field(self, row, label, variable, width=32):
        tk.Label(self, text=label, font=self.app.fonts.body, bg=BG, fg=MUTED).grid(row=row, column=0, sticky="w",
                                                                                padx=(0, 12), pady=4)
        entry = tk.Entry(self, textvariable=variable, width=width, font=self.app.fonts.body, bg=FIELD, fg=TEXT,
                         insertbackground=TEXT, relief="flat", highlightthickness=1,
                         highlightbackground=BORDER, highlightcolor=PRIMARY_TEXT)
        entry.grid(row=row, column=1, sticky="w", pady=4, ipady=3)
        return entry

    def create(self):
        manager = self.app.manager
        try:
            info = manager.create_station(self.name.get(), self.port.get().strip(), lan=self.lan.get())
        except (ValueError, OSError) as exc:
            self.error.configure(text=str(exc))
            return
        start = self.start_now.get()
        self.destroy()
        if start:
            self.app.act(manager.start, info.id)
        self.app.refresh(reschedule=False)


class ManagerApp:
    def __init__(self, root, manager, icon=None):
        self.root = root
        self.manager = manager
        self.fonts = Fonts(root)
        self.cards = {}
        self.order = []
        root.title("StationNet Manager")
        root.configure(bg=BG)
        root.geometry("760x600")
        root.minsize(520, 360)
        if icon and Path(icon).is_file():
            try:
                self._icon = tk.PhotoImage(file=str(icon))
                root.iconphoto(True, self._icon)
            except tk.TclError:
                pass
        shell = tk.Frame(root, bg=BG, padx=12, pady=10)
        shell.pack(fill="both", expand=True)
        tk.Label(shell, text="STATIONNET MANAGER", font=self.fonts.heading, bg=BG, fg=TEXT,
                 anchor="w").pack(fill="x")
        separator(shell).pack(fill="x", pady=(8, 10))
        bar = tk.Frame(shell, bg=BG)
        bar.pack(fill="x", pady=(0, 6))
        self.count = tk.Label(bar, font=self.fonts.small, bg=BG, fg=MUTED)
        self.count.pack(side="left")
        make_button(bar, self.fonts, "+ New Station", lambda: NewStationDialog(self),
                    primary=True).pack(side="right")
        self._build_list(shell)
        separator(shell).pack(fill="x", pady=(10, 6))
        footer = tk.Frame(shell, bg=BG)
        footer.pack(fill="x")
        self.running = tk.Label(footer, font=self.fonts.small_bold, bg=BG, fg=STATES["running"][1])
        self.running.pack(side="left")
        exit_link = tk.Label(footer, text="Exit", font=self.fonts.small, bg=BG, fg=TEXT, cursor="hand2")
        exit_link.pack(side="right")
        exit_link.bind("<Button-1>", lambda _event: root.destroy())
        tk.Label(footer, text="Closing the manager leaves stations running.", font=self.fonts.small,
                 bg=BG, fg=MUTED).pack(side="right", padx=(0, 16))
        self.empty = tk.Label(self.inner, text="No stations yet. Use + New Station to create one.",
                              font=self.fonts.body, bg=BG, fg=MUTED)
        self.refresh()

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
            card.update_info(info)
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


def main(source_root):
    clean_environment()
    source_root = Path(source_root)
    if getattr(sys, "frozen", False):
        core_dir = Path(sys.executable).parent
        icon = Path(getattr(sys, "_MEIPASS", core_dir)) / "icon.png"
    else:
        core_dir = source_root
        icon = source_root / "icon.png"
    manager = StationManager(instance.install_root(source_root), core_dir)
    root = tk.Tk()
    ManagerApp(root, manager, icon=icon)
    root.mainloop()
