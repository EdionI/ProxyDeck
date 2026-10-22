from __future__ import annotations

import csv
import queue
import threading
import tkinter as tk
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from urllib.parse import urlparse

from proxy_manager import (
    DEFAULT_PROXY_FILE,
    BrowserManager,
    ProxyError,
    ProxySpec,
    ProxyStore,
    check_proxy,
    format_bytes,
    format_duration,
    parse_proxy,
)


COLORS = {
    "bg": "#09111f",
    "panel": "#101a2d",
    "panel_alt": "#152239",
    "raised": "#1a2943",
    "border": "#263754",
    "text": "#edf4ff",
    "muted": "#8ea2bf",
    "faint": "#60728f",
    "accent": "#42d392",
    "accent_hover": "#35bd80",
    "blue": "#61a9ff",
    "blue_hover": "#4a95ec",
    "warning": "#f6b94a",
    "danger": "#ff6b7a",
    "healthy_bg": "#14362f",
    "error_bg": "#3b1c2b",
    "selection": "#203c58",
}


def local_time(iso_text: str, *, compact: bool = False) -> str:
    if not iso_text:
        return "Never"
    try:
        parsed = datetime.fromisoformat(iso_text)
        parsed = parsed.astimezone()
        return parsed.strftime("%b %d, %H:%M" if compact else "%b %d, %Y · %H:%M:%S")
    except ValueError:
        return iso_text


def safe_url(value: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError("Enter a URL to open.")
    parsed = urlparse(text if "://" in text else "https://" + text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Enter a valid http:// or https:// URL.")
    return parsed.geturl()


class ProxyDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, title: str, initial: str = "") -> None:
        super().__init__(parent)
        self.result: str | None = None
        self.title(title)
        self.geometry("560x260")
        self.resizable(False, False)
        self.configure(background=COLORS["panel"])
        self.transient(parent)
        self.grab_set()

        body = tk.Frame(self, background=COLORS["panel"], padx=28, pady=24)
        body.pack(fill="both", expand=True)
        tk.Label(
            body,
            text=title,
            font=("Segoe UI Semibold", 18),
            background=COLORS["panel"],
            foreground=COLORS["text"],
        ).pack(anchor="w")
        tk.Label(
            body,
            text="HOST:PORT or HOST:PORT:USERNAME:PASSWORD  ·  optional | REGION",
            font=("Segoe UI", 9),
            background=COLORS["panel"],
            foreground=COLORS["muted"],
        ).pack(anchor="w", pady=(5, 16))

        self.entry = ttk.Entry(body, font=("Consolas", 11))
        self.entry.pack(fill="x", ipady=7)
        self.entry.insert(0, initial)
        self.error_var = tk.StringVar()
        tk.Label(
            body,
            textvariable=self.error_var,
            font=("Segoe UI", 9),
            background=COLORS["panel"],
            foreground=COLORS["danger"],
        ).pack(anchor="w", pady=(7, 0))

        actions = ttk.Frame(body, style="Panel.TFrame")
        actions.pack(fill="x", side="bottom")
        ttk.Button(
            actions, text="Cancel", style="Ghost.TButton", command=self.destroy
        ).pack(side="right")
        ttk.Button(
            actions, text="Save proxy", style="Accent.TButton", command=self._save
        ).pack(side="right", padx=(0, 8))
        self.entry.bind("<Return>", lambda _event: self._save())
        self.bind("<Escape>", lambda _event: self.destroy())
        self.after(80, self._focus)

    def _focus(self) -> None:
        self.entry.focus_set()
        self.entry.selection_range(0, "end")

    def _save(self) -> None:
        value = self.entry.get().strip()
        try:
            parse_proxy(value)
        except ValueError as error:
            self.error_var.set(str(error))
            return
        self.result = value
        self.destroy()


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, settings: dict) -> None:
        super().__init__(parent)
        self.result: dict | None = None
        self.title("ProxyDeck settings")
        self.geometry("580x330")
        self.resizable(False, False)
        self.configure(background=COLORS["panel"])
        self.transient(parent)
        self.grab_set()

        body = tk.Frame(self, background=COLORS["panel"], padx=28, pady=24)
        body.pack(fill="both", expand=True)
        tk.Label(
            body,
            text="Settings",
            font=("Segoe UI Semibold", 18),
            background=COLORS["panel"],
            foreground=COLORS["text"],
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        tk.Label(
            body,
            text="Changes are stored locally in proxydeck_data/settings.json.",
            font=("Segoe UI", 9),
            background=COLORS["panel"],
            foreground=COLORS["muted"],
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 22))

        tk.Label(
            body,
            text="Default start URL",
            background=COLORS["panel"],
            foreground=COLORS["text"],
            font=("Segoe UI Semibold", 9),
        ).grid(row=2, column=0, sticky="w", pady=6)
        self.url_var = tk.StringVar(value=settings.get("start_url", ""))
        ttk.Entry(body, textvariable=self.url_var).grid(
            row=2, column=1, sticky="ew", padx=(18, 0), ipady=5
        )

        tk.Label(
            body,
            text="Health check timeout",
            background=COLORS["panel"],
            foreground=COLORS["text"],
            font=("Segoe UI Semibold", 9),
        ).grid(row=3, column=0, sticky="w", pady=6)
        timeout_row = ttk.Frame(body, style="Panel.TFrame")
        timeout_row.grid(row=3, column=1, sticky="w", padx=(18, 0))
        self.timeout_var = tk.StringVar(value=str(settings.get("health_timeout", 15)))
        ttk.Spinbox(
            timeout_row,
            from_=5,
            to=60,
            width=7,
            textvariable=self.timeout_var,
        ).pack(side="left", ipady=4)
        ttk.Label(timeout_row, text="seconds", style="Muted.TLabel").pack(
            side="left", padx=8
        )

        info = (
            "Bandwidth includes traffic forwarded by ProxyDeck while a browser is open "
            "and during health checks. Chrome profiles stay isolated per proxy."
        )
        tk.Label(
            body,
            text=info,
            wraplength=510,
            justify="left",
            background=COLORS["panel_alt"],
            foreground=COLORS["muted"],
            padx=14,
            pady=12,
            font=("Segoe UI", 9),
        ).grid(row=4, column=0, columnspan=2, sticky="ew", pady=(18, 0))

        actions = ttk.Frame(body, style="Panel.TFrame")
        actions.grid(row=5, column=0, columnspan=2, sticky="e", pady=(22, 0))
        ttk.Button(actions, text="Cancel", style="Ghost.TButton", command=self.destroy).pack(
            side="right"
        )
        ttk.Button(
            actions, text="Save settings", style="Accent.TButton", command=self._save
        ).pack(side="right", padx=(0, 8))
        body.columnconfigure(1, weight=1)
        self.bind("<Escape>", lambda _event: self.destroy())

    def _save(self) -> None:
        try:
            url = safe_url(self.url_var.get())
            timeout = int(self.timeout_var.get())
            if not 5 <= timeout <= 60:
                raise ValueError("Timeout must be between 5 and 60 seconds.")
        except ValueError as error:
            messagebox.showerror("Invalid settings", str(error), parent=self)
            return
        self.result = {"start_url": url, "health_timeout": timeout}
        self.destroy()


class NotesDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, proxy_name: str, initial: str) -> None:
        super().__init__(parent)
        self.result: str | None = None
        self.title(f"Notes · {proxy_name}")
        self.geometry("600x430")
        self.minsize(480, 320)
        self.configure(background=COLORS["panel"])
        self.transient(parent)
        self.grab_set()

        body = tk.Frame(self, background=COLORS["panel"], padx=24, pady=22)
        body.pack(fill="both", expand=True)
        tk.Label(
            body,
            text="Private notes",
            font=("Segoe UI Semibold", 18),
            background=COLORS["panel"],
            foreground=COLORS["text"],
        ).pack(anchor="w")
        tk.Label(
            body,
            text=f"Stored locally for {proxy_name}. Credentials are never copied here.",
            font=("Segoe UI", 9),
            background=COLORS["panel"],
            foreground=COLORS["muted"],
        ).pack(anchor="w", pady=(4, 14))
        self.text = tk.Text(
            body,
            wrap="word",
            background=COLORS["panel_alt"],
            foreground=COLORS["text"],
            insertbackground=COLORS["text"],
            selectbackground=COLORS["selection"],
            relief="flat",
            padx=12,
            pady=10,
            font=("Segoe UI", 10),
        )
        self.text.pack(fill="both", expand=True)
        self.text.insert("1.0", initial)

        actions = ttk.Frame(body, style="Panel.TFrame")
        actions.pack(fill="x", pady=(14, 0))
        ttk.Button(actions, text="Cancel", style="Ghost.TButton", command=self.destroy).pack(
            side="right"
        )
        ttk.Button(
            actions, text="Save notes", style="Accent.TButton", command=self._save
        ).pack(side="right", padx=(0, 8))
        self.bind("<Escape>", lambda _event: self.destroy())
        self.after(80, self.text.focus_set)

    def _save(self) -> None:
        self.result = self.text.get("1.0", "end-1c")[:4000]
        self.destroy()


class ProxyDeckApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("ProxyDeck · Chrome Proxy Manager")
        self.root.geometry("1480x900")
        self.root.minsize(1120, 720)
        self.root.configure(background=COLORS["bg"])

        self.store = ProxyStore(DEFAULT_PROXY_FILE)
        self.settings = self.store.settings()
        self.events: queue.Queue[tuple] = queue.Queue()
        self.browser = BrowserManager(self.store, self._browser_event)
        self.executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="proxy-check")
        self._closing = False
        self._checking: set[str] = set()
        self._specs: dict[str, ProxySpec] = {}
        self._records: dict[str, dict] = {}
        self._activities: dict[str, dict] = {}
        self._rate_history: dict[str, deque[float]] = {}
        self._sort_column = "favorite"
        self._sort_reverse = True
        self._last_selected_key = ""

        self.search_var = tk.StringVar()
        self.filter_var = tk.StringVar(value="All proxies")
        self.url_var = tk.StringVar(value=self.settings["start_url"])
        self.status_var = tk.StringVar(value="Ready")
        self.shown_var = tk.StringVar(value="0 shown")
        self.card_vars = {
            "total": tk.StringVar(value="0"),
            "active": tk.StringVar(value="0"),
            "healthy": tk.StringVar(value="0"),
            "usage": tk.StringVar(value="0 B"),
        }
        self.detail_vars = {
            key: tk.StringVar(value="—")
            for key in (
                "name",
                "address",
                "status",
                "exit_ip",
                "location",
                "isp",
                "protocol",
                "last_opened",
                "last_checked",
                "usage_summary",
                "traffic",
                "live_rate",
            )
        }

        self._configure_styles()
        self._build_layout()
        self._bind_shortcuts()
        self.reload_proxies()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(200, self._process_events)
        self.root.after(1000, self._poll_activity)

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 9), background=COLORS["bg"])
        style.configure("TFrame", background=COLORS["bg"])
        style.configure("Panel.TFrame", background=COLORS["panel"])
        style.configure("Raised.TFrame", background=COLORS["panel_alt"])
        style.configure(
            "TLabel", background=COLORS["bg"], foreground=COLORS["text"]
        )
        style.configure(
            "Panel.TLabel", background=COLORS["panel"], foreground=COLORS["text"]
        )
        style.configure(
            "Muted.TLabel", background=COLORS["panel"], foreground=COLORS["muted"]
        )
        style.configure(
            "TEntry",
            fieldbackground=COLORS["raised"],
            foreground=COLORS["text"],
            insertcolor=COLORS["text"],
            bordercolor=COLORS["border"],
            lightcolor=COLORS["border"],
            darkcolor=COLORS["border"],
            padding=7,
        )
        style.map("TEntry", bordercolor=[("focus", COLORS["blue"])])
        style.configure(
            "TCombobox",
            fieldbackground=COLORS["raised"],
            background=COLORS["raised"],
            foreground=COLORS["text"],
            arrowcolor=COLORS["muted"],
            bordercolor=COLORS["border"],
            lightcolor=COLORS["border"],
            darkcolor=COLORS["border"],
            padding=6,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", COLORS["raised"])],
            selectbackground=[("readonly", COLORS["raised"])],
            selectforeground=[("readonly", COLORS["text"])],
        )
        style.configure(
            "TSpinbox",
            fieldbackground=COLORS["raised"],
            foreground=COLORS["text"],
            arrowcolor=COLORS["muted"],
            bordercolor=COLORS["border"],
        )
        style.configure(
            "Accent.TButton",
            background=COLORS["accent"],
            foreground="#07140f",
            borderwidth=0,
            focusthickness=0,
            padding=(15, 9),
            font=("Segoe UI Semibold", 9),
        )
        style.map(
            "Accent.TButton",
            background=[("active", COLORS["accent_hover"]), ("disabled", "#29493f")],
            foreground=[("disabled", COLORS["faint"])],
        )
        style.configure(
            "Blue.TButton",
            background=COLORS["blue"],
            foreground="#07111f",
            borderwidth=0,
            padding=(14, 8),
            font=("Segoe UI Semibold", 9),
        )
        style.map("Blue.TButton", background=[("active", COLORS["blue_hover"])])
        style.configure(
            "Ghost.TButton",
            background=COLORS["raised"],
            foreground=COLORS["text"],
            bordercolor=COLORS["border"],
            lightcolor=COLORS["border"],
            darkcolor=COLORS["border"],
            padding=(13, 8),
        )
        style.map(
            "Ghost.TButton",
            background=[("active", COLORS["selection"]), ("disabled", COLORS["panel_alt"])],
            foreground=[("disabled", COLORS["faint"])],
        )
        style.configure(
            "Danger.TButton",
            background=COLORS["error_bg"],
            foreground=COLORS["danger"],
            bordercolor="#653044",
            lightcolor="#653044",
            darkcolor="#653044",
            padding=(13, 8),
        )
        style.map("Danger.TButton", background=[("active", "#51243a")])
        style.configure(
            "Proxy.Treeview",
            background=COLORS["panel"],
            fieldbackground=COLORS["panel"],
            foreground=COLORS["text"],
            borderwidth=0,
            rowheight=38,
            font=("Segoe UI", 9),
        )
        style.layout("Proxy.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        style.map(
            "Proxy.Treeview",
            background=[("selected", COLORS["selection"])],
            foreground=[("selected", COLORS["text"])],
        )
        style.configure(
            "Proxy.Treeview.Heading",
            background=COLORS["panel_alt"],
            foreground=COLORS["muted"],
            bordercolor=COLORS["border"],
            lightcolor=COLORS["border"],
            darkcolor=COLORS["border"],
            padding=(8, 10),
            font=("Segoe UI Semibold", 8),
        )
        style.map(
            "Proxy.Treeview.Heading", background=[("active", COLORS["raised"])]
        )
        style.configure(
            "Vertical.TScrollbar",
            background=COLORS["raised"],
            troughcolor=COLORS["panel"],
            bordercolor=COLORS["panel"],
            arrowcolor=COLORS["muted"],
        )
        self.root.option_add("*TCombobox*Listbox.background", COLORS["raised"])
        self.root.option_add("*TCombobox*Listbox.foreground", COLORS["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", COLORS["selection"])

    def _build_layout(self) -> None:
        self._build_header()
        content = ttk.Frame(self.root, padding=(20, 16, 20, 12))
        content.pack(fill="both", expand=True)
        self._build_cards(content)
        self._build_toolbar(content)

        work = tk.PanedWindow(
            content,
            orient="horizontal",
            background=COLORS["bg"],
            borderwidth=0,
            sashwidth=8,
            sashrelief="flat",
            showhandle=False,
        )
        work.pack(fill="both", expand=True, pady=(12, 0))
        table_panel = tk.Frame(
            work,
            background=COLORS["panel"],
            highlightthickness=1,
            highlightbackground=COLORS["border"],
        )
        detail_panel = tk.Frame(
            work,
            background=COLORS["panel"],
            highlightthickness=1,
            highlightbackground=COLORS["border"],
        )
        work.add(table_panel, minsize=720, stretch="always")
        work.add(detail_panel, minsize=330, width=380, stretch="never")
        self._build_table(table_panel)
        self._build_detail(detail_panel)

        footer = tk.Frame(self.root, background=COLORS["panel"], height=34)
        footer.pack(fill="x", side="bottom")
        footer.pack_propagate(False)
        self.status_dot = tk.Canvas(
            footer,
            width=18,
            height=18,
            background=COLORS["panel"],
            highlightthickness=0,
        )
        self.status_dot.pack(side="left", padx=(20, 0), pady=8)
        self.status_dot.create_oval(5, 5, 11, 11, fill=COLORS["accent"], outline="")
        tk.Label(
            footer,
            textvariable=self.status_var,
            background=COLORS["panel"],
            foreground=COLORS["muted"],
            font=("Segoe UI", 8),
        ).pack(side="left", pady=7)
        tk.Label(
            footer,
            text=f"SOURCE  {DEFAULT_PROXY_FILE.name}",
            background=COLORS["panel"],
            foreground=COLORS["faint"],
            font=("Segoe UI Semibold", 8),
        ).pack(side="right", padx=20, pady=7)

    def _build_header(self) -> None:
        header = tk.Frame(self.root, background=COLORS["panel"], height=78)
        header.pack(fill="x")
        header.pack_propagate(False)
        brand = tk.Frame(header, background=COLORS["panel"])
        brand.pack(side="left", padx=22, pady=14)
        mark = tk.Canvas(
            brand,
            width=40,
            height=40,
            background=COLORS["panel"],
            highlightthickness=0,
        )
        mark.pack(side="left", padx=(0, 12))
        mark.create_oval(2, 2, 38, 38, fill=COLORS["accent"], outline="")
        mark.create_arc(10, 10, 30, 30, start=35, extent=275, style="arc", width=3, outline="#092017")
        mark.create_oval(17, 17, 23, 23, fill="#092017", outline="")
        title_block = tk.Frame(brand, background=COLORS["panel"])
        title_block.pack(side="left")
        tk.Label(
            title_block,
            text="ProxyDeck",
            background=COLORS["panel"],
            foreground=COLORS["text"],
            font=("Segoe UI Semibold", 19),
        ).pack(anchor="w")
        tk.Label(
            title_block,
            text="ISOLATED CHROME WORKSPACES",
            background=COLORS["panel"],
            foreground=COLORS["faint"],
            font=("Segoe UI Semibold", 7),
        ).pack(anchor="w")

        controls = ttk.Frame(header, style="Panel.TFrame")
        controls.pack(side="right", padx=22, pady=18)
        chrome_text = "Chrome ready" if self.browser.chrome else "Chrome not found"
        chrome_color = COLORS["accent"] if self.browser.chrome else COLORS["danger"]
        tk.Label(
            controls,
            text=f"●  {chrome_text}",
            background=COLORS["panel"],
            foreground=chrome_color,
            font=("Segoe UI Semibold", 9),
        ).pack(side="left", padx=(0, 16))
        ttk.Button(
            controls, text="Settings", style="Ghost.TButton", command=self.open_settings
        ).pack(side="left")
        ttk.Button(
            controls, text="＋ Add proxy", style="Accent.TButton", command=self.add_proxy
        ).pack(side="left", padx=(8, 0))

    def _build_cards(self, parent: ttk.Frame) -> None:
        row = ttk.Frame(parent)
        row.pack(fill="x")
        cards = (
            ("total", "TOTAL PROXIES", COLORS["blue"]),
            ("active", "ACTIVE BROWSERS", COLORS["accent"]),
            ("healthy", "HEALTHY", COLORS["warning"]),
            ("usage", "LIFETIME TRAFFIC", "#bb8cff"),
        )
        for index, (key, label, color) in enumerate(cards):
            card = tk.Frame(
                row,
                background=COLORS["panel"],
                highlightthickness=1,
                highlightbackground=COLORS["border"],
                padx=17,
                pady=13,
            )
            card.grid(
                row=0,
                column=index,
                sticky="ew",
                padx=(0, 10) if index < len(cards) - 1 else 0,
            )
            row.columnconfigure(index, weight=1)
            tk.Frame(card, height=3, width=28, background=color).pack(anchor="w")
            tk.Label(
                card,
                textvariable=self.card_vars[key],
                font=("Segoe UI Semibold", 21),
                background=COLORS["panel"],
                foreground=COLORS["text"],
            ).pack(anchor="w", pady=(8, 0))
            tk.Label(
                card,
                text=label,
                font=("Segoe UI Semibold", 7),
                background=COLORS["panel"],
                foreground=COLORS["faint"],
            ).pack(anchor="w")

    def _build_toolbar(self, parent: ttk.Frame) -> None:
        bar = ttk.Frame(parent)
        bar.pack(fill="x", pady=(14, 0))
        ttk.Label(bar, text="Search", foreground=COLORS["muted"]).pack(side="left")
        search = ttk.Entry(bar, textvariable=self.search_var, width=30)
        search.pack(side="left", padx=(8, 10), ipady=2)
        self.search_entry = search
        self.search_var.trace_add("write", lambda *_args: self.render_table())
        filters = ttk.Combobox(
            bar,
            textvariable=self.filter_var,
            state="readonly",
            width=15,
            values=(
                "All proxies",
                "Active",
                "Healthy",
                "Errors",
                "Unchecked",
                "Favorites",
            ),
        )
        filters.pack(side="left")
        filters.bind("<<ComboboxSelected>>", lambda _event: self.render_table())
        ttk.Label(bar, textvariable=self.shown_var, foreground=COLORS["faint"]).pack(
            side="left", padx=12
        )
        ttk.Button(
            bar, text="Import", style="Ghost.TButton", command=self.import_proxies
        ).pack(side="right")
        ttk.Button(
            bar, text="Export stats", style="Ghost.TButton", command=self.export_stats
        ).pack(side="right", padx=(0, 8))
        ttk.Button(
            bar, text="Check all", style="Ghost.TButton", command=self.check_all
        ).pack(side="right", padx=(0, 8))
        ttk.Button(
            bar, text="Check selected", style="Blue.TButton", command=self.check_selected
        ).pack(side="right", padx=(0, 8))

    def _build_table(self, parent: tk.Frame) -> None:
        header = tk.Frame(parent, background=COLORS["panel"], padx=14, pady=12)
        header.pack(fill="x")
        tk.Label(
            header,
            text="PROXY FLEET",
            background=COLORS["panel"],
            foreground=COLORS["muted"],
            font=("Segoe UI Semibold", 8),
        ).pack(side="left")
        tk.Label(
            header,
            text="Double-click to launch · multi-select supported",
            background=COLORS["panel"],
            foreground=COLORS["faint"],
            font=("Segoe UI", 8),
        ).pack(side="right")

        table_frame = ttk.Frame(parent, style="Panel.TFrame")
        table_frame.pack(fill="both", expand=True)
        columns = (
            "favorite",
            "proxy",
            "exit_ip",
            "location",
            "status",
            "latency",
            "usage",
            "last_opened",
        )
        self.table = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            style="Proxy.Treeview",
            selectmode="extended",
        )
        headings = {
            "favorite": "★",
            "proxy": "PROXY / ALIAS",
            "exit_ip": "EXIT IP",
            "location": "LOCATION",
            "status": "STATUS",
            "latency": "LATENCY",
            "usage": "TRAFFIC",
            "last_opened": "LAST OPENED",
        }
        widths = {
            "favorite": (42, 42),
            "proxy": (210, 130),
            "exit_ip": (125, 95),
            "location": (150, 100),
            "status": (100, 85),
            "latency": (78, 68),
            "usage": (95, 75),
            "last_opened": (135, 105),
        }
        for column in columns:
            self.table.heading(
                column,
                text=headings[column],
                command=lambda name=column: self.sort_by(name),
            )
            width, minimum = widths[column]
            self.table.column(
                column,
                width=width,
                minwidth=minimum,
                anchor="center" if column in {"favorite", "status", "latency"} else "w",
                stretch=column in {"proxy", "location"},
            )
        scrollbar = ttk.Scrollbar(
            table_frame, orient="vertical", command=self.table.yview
        )
        self.table.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.table.pack(side="left", fill="both", expand=True)
        self.table.tag_configure("active", foreground=COLORS["accent"])
        self.table.tag_configure("error", foreground="#ff9aa5")
        self.table.tag_configure("checking", foreground=COLORS["blue"])
        self.table.bind("<<TreeviewSelect>>", self._on_select)
        self.table.bind("<Double-1>", lambda _event: self.open_selected())
        self.table.bind("<Button-3>", self._show_context_menu)

        self.context_menu = tk.Menu(
            self.root,
            tearoff=False,
            background=COLORS["raised"],
            foreground=COLORS["text"],
            activebackground=COLORS["selection"],
            activeforeground=COLORS["text"],
            borderwidth=0,
        )
        self.context_menu.add_command(label="Open browser", command=self.open_selected)
        self.context_menu.add_command(label="Open new tab", command=self.new_tab)
        self.context_menu.add_command(label="Run health check", command=self.check_selected)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Favorite / unfavorite", command=self.toggle_favorite)
        self.context_menu.add_command(label="Set alias", command=self.set_alias)
        self.context_menu.add_command(label="Edit notes", command=self.edit_notes)
        self.context_menu.add_command(label="Copy address", command=self.copy_address)
        self.context_menu.add_command(label="Edit proxy", command=self.edit_proxy)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Delete Chrome profile", command=self.delete_profile)
        self.context_menu.add_command(label="Remove from list", command=self.remove_proxy)

    def _build_detail(self, parent: tk.Frame) -> None:
        canvas = tk.Canvas(
            parent,
            background=COLORS["panel"],
            borderwidth=0,
            highlightthickness=0,
        )
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        shell = tk.Frame(canvas, background=COLORS["panel"], padx=18, pady=15)
        self.detail_shell = shell
        self.detail_canvas = canvas
        detail_window = canvas.create_window((0, 0), window=shell, anchor="nw")
        shell.bind(
            "<Configure>",
            lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfigure(detail_window, width=event.width),
        )
        self.root.bind("<MouseWheel>", self._on_detail_mousewheel, add="+")
        title_row = tk.Frame(shell, background=COLORS["panel"])
        title_row.pack(fill="x")
        tk.Label(
            title_row,
            textvariable=self.detail_vars["name"],
            anchor="w",
            background=COLORS["panel"],
            foreground=COLORS["text"],
            font=("Segoe UI Semibold", 15),
        ).pack(fill="x")
        tk.Label(
            title_row,
            textvariable=self.detail_vars["address"],
            anchor="w",
            background=COLORS["panel"],
            foreground=COLORS["muted"],
            font=("Consolas", 9),
        ).pack(fill="x", pady=(3, 0))
        self.detail_status = tk.Label(
            shell,
            textvariable=self.detail_vars["status"],
            background=COLORS["panel_alt"],
            foreground=COLORS["muted"],
            font=("Segoe UI Semibold", 8),
            padx=10,
            pady=5,
        )
        self.detail_status.pack(anchor="w", pady=(10, 8))

        url_row = ttk.Frame(shell, style="Panel.TFrame")
        url_row.pack(fill="x")
        ttk.Entry(url_row, textvariable=self.url_var).pack(side="left", fill="x", expand=True)
        ttk.Button(
            url_row, text="OPEN", style="Accent.TButton", command=self.open_selected
        ).pack(side="left", padx=(7, 0))

        action_row = ttk.Frame(shell, style="Panel.TFrame")
        action_row.pack(fill="x", pady=(6, 10))
        ttk.Button(
            action_row, text="New tab", style="Ghost.TButton", command=self.new_tab
        ).pack(side="left", fill="x", expand=True)
        ttk.Button(
            action_row, text="Check", style="Ghost.TButton", command=self.check_selected
        ).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(
            action_row, text="Close", style="Danger.TButton", command=self.close_selected
        ).pack(side="left", fill="x", expand=True)

        tk.Frame(shell, height=1, background=COLORS["border"]).pack(fill="x")
        facts = tk.Frame(shell, background=COLORS["panel"])
        facts.pack(fill="x", pady=8)
        fact_rows = (
            ("Exit IP", "exit_ip"),
            ("Location", "location"),
            ("ISP", "isp"),
            ("Protocol", "protocol"),
            ("Last opened", "last_opened"),
            ("Last checked", "last_checked"),
            ("Sessions / runtime", "usage_summary"),
            ("Traffic ↓ / ↑", "traffic"),
            ("Live speed", "live_rate"),
        )
        for row, (label, key) in enumerate(fact_rows):
            tk.Label(
                facts,
                text=label,
                background=COLORS["panel"],
                foreground=COLORS["faint"],
                font=("Segoe UI", 8),
            ).grid(row=row, column=0, sticky="w", pady=2)
            tk.Label(
                facts,
                textvariable=self.detail_vars[key],
                background=COLORS["panel"],
                foreground=COLORS["text"],
                font=("Segoe UI Semibold", 8),
                anchor="e",
                wraplength=210,
                justify="right",
            ).grid(row=row, column=1, sticky="e", pady=2, padx=(10, 0))
        facts.columnconfigure(1, weight=1)

        tk.Label(
            shell,
            text="LIVE THROUGHPUT · 60 SECONDS",
            background=COLORS["panel"],
            foreground=COLORS["faint"],
            font=("Segoe UI Semibold", 7),
        ).pack(anchor="w", pady=(4, 5))
        self.chart = tk.Canvas(
            shell,
            height=72,
            background=COLORS["panel_alt"],
            highlightthickness=1,
            highlightbackground=COLORS["border"],
        )
        self.chart.pack(fill="x")
        self.chart.bind("<Configure>", lambda _event: self._draw_chart())

        metadata_actions = ttk.Frame(shell, style="Panel.TFrame")
        metadata_actions.pack(fill="x", pady=(12, 0))
        ttk.Button(
            metadata_actions,
            text="★ Favorite",
            style="Ghost.TButton",
            command=self.toggle_favorite,
        ).pack(side="left", fill="x", expand=True)
        ttk.Button(
            metadata_actions,
            text="Alias",
            style="Ghost.TButton",
            command=self.set_alias,
        ).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(
            metadata_actions,
            text="Notes",
            style="Ghost.TButton",
            command=self.edit_notes,
        ).pack(side="left", fill="x", expand=True)

    def _on_detail_mousewheel(self, event):
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        while widget is not None:
            if widget == self.detail_shell:
                self.detail_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
                return "break"
            parent_name = widget.winfo_parent()
            if not parent_name:
                break
            try:
                widget = widget._nametowidget(parent_name)
            except KeyError:
                break
        return None

    def _bind_shortcuts(self) -> None:
        self.root.bind("<Control-n>", lambda _event: self.add_proxy())
        self.root.bind("<Control-f>", self._focus_search)
        self.root.bind("<F5>", lambda _event: self.check_selected())
        self.root.bind("<Control-r>", lambda _event: self.reload_proxies())
        self.root.bind("<Escape>", lambda _event: self.table.selection_remove(*self.table.selection()))

    def _focus_search(self, _event=None) -> str:
        self.search_entry.focus_set()
        self.search_entry.selection_range(0, "end")
        return "break"

    def reload_proxies(self, select_key: str = "") -> None:
        try:
            specs, errors = self.store.proxies()
        except ProxyError as error:
            messagebox.showerror("Could not load proxies", str(error), parent=self.root)
            return
        self._specs = {spec.key: spec for spec in specs}
        self._records = self.store.all_records()
        self.render_table(select_key=select_key or self._last_selected_key)
        if errors:
            self.set_status(f"Loaded {len(specs)} proxies · {len(errors)} invalid line(s) skipped", "warning")
        else:
            self.set_status(f"Loaded {len(specs)} proxies from {self.store.proxy_file.name}")

    def render_table(self, select_key: str = "") -> None:
        if not hasattr(self, "table"):
            return
        selected = set(self.table.selection())
        focus = select_key or self.table.focus()
        search = self.search_var.get().strip().lower()
        selected_filter = self.filter_var.get()
        active_keys = set(self._activities)
        rows = []
        for key, spec in self._specs.items():
            record = self._records.get(key) or self.store.record(spec)
            searchable = " ".join(
                str(record.get(field, ""))
                for field in ("alias", "address", "exit_ip", "country", "region", "city", "isp")
            ).lower()
            if search and search not in searchable:
                continue
            health = record.get("health", "Unchecked")
            if selected_filter == "Active" and key not in active_keys:
                continue
            if selected_filter == "Healthy" and health != "Healthy":
                continue
            if selected_filter == "Errors" and health != "Error":
                continue
            if selected_filter == "Unchecked" and health != "Unchecked":
                continue
            if selected_filter == "Favorites" and not record.get("favorite"):
                continue
            rows.append((spec, record))

        rows.sort(key=self._row_sort_key, reverse=self._sort_reverse)
        self.table.delete(*self.table.get_children())
        for spec, record in rows:
            key = spec.key
            active = self._activities.get(key)
            if key in self._checking:
                status, tag = "Checking…", "checking"
            elif active:
                status, tag = "Active", "active"
            elif record.get("health") == "Healthy":
                status, tag = "Healthy", ""
            elif record.get("health") == "Error":
                status, tag = "Error", "error"
            else:
                status, tag = "Unchecked", ""
            alias = record.get("alias", "").strip()
            proxy_label = f"{alias}\n{spec.address}" if alias else spec.address
            country = record.get("country_code") or record.get("country") or spec.region_hint
            location_parts = [record.get("city", ""), country]
            location = ", ".join(part for part in location_parts if part) or "—"
            latency = record.get("latency_ms")
            stored_total = int(record.get("bytes_up", 0)) + int(record.get("bytes_down", 0))
            live_total = (active or {}).get("bytes_up", 0) + (active or {}).get("bytes_down", 0)
            values = (
                "★" if record.get("favorite") else "☆",
                proxy_label,
                record.get("exit_ip") or "—",
                location,
                status,
                f"{latency} ms" if latency is not None else "—",
                format_bytes(stored_total + live_total),
                local_time(record.get("last_opened_at", ""), compact=True),
            )
            self.table.insert("", "end", iid=key, values=values, tags=(tag,) if tag else ())
        visible_keys = set(self.table.get_children())
        restore = (selected | {focus}) & visible_keys
        if restore:
            self.table.selection_set(tuple(restore))
            if focus in visible_keys:
                self.table.focus(focus)
        self.shown_var.set(f"{len(rows)} shown")
        self._update_summary()
        self._update_detail()

    def _row_sort_key(self, row: tuple[ProxySpec, dict]):
        spec, record = row
        column = self._sort_column
        if column == "favorite":
            return bool(record.get("favorite")), (record.get("alias") or spec.address).lower()
        if column == "proxy":
            return (record.get("alias") or spec.address).lower()
        if column == "exit_ip":
            return record.get("exit_ip", "")
        if column == "location":
            return (record.get("country", ""), record.get("city", ""))
        if column == "status":
            if spec.key in self._activities:
                return "0 Active"
            return record.get("health", "Unchecked")
        if column == "latency":
            return record.get("latency_ms") if record.get("latency_ms") is not None else 10**9
        if column == "usage":
            return int(record.get("bytes_up", 0)) + int(record.get("bytes_down", 0))
        return record.get("last_opened_at", "")

    def sort_by(self, column: str) -> None:
        if self._sort_column == column:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = column
            self._sort_reverse = column in {"favorite", "usage", "last_opened"}
        self.render_table()

    def _selected_specs(self) -> list[ProxySpec]:
        return [self._specs[key] for key in self.table.selection() if key in self._specs]

    def _primary_spec(self) -> ProxySpec | None:
        focus = self.table.focus()
        if focus in self._specs and focus in self.table.selection():
            return self._specs[focus]
        selected = self._selected_specs()
        return selected[0] if selected else None

    def _on_select(self, _event=None) -> None:
        spec = self._primary_spec()
        if spec:
            self._last_selected_key = spec.key
        self._update_detail()

    def _update_summary(self) -> None:
        records = [self._records.get(key, {}) for key in self._specs]
        stored = sum(
            int(record.get("bytes_up", 0)) + int(record.get("bytes_down", 0))
            for record in records
        )
        live = sum(
            activity.get("bytes_up", 0) + activity.get("bytes_down", 0)
            for activity in self._activities.values()
        )
        self.card_vars["total"].set(str(len(self._specs)))
        self.card_vars["active"].set(str(len(self._activities)))
        self.card_vars["healthy"].set(
            str(sum(record.get("health") == "Healthy" for record in records))
        )
        self.card_vars["usage"].set(format_bytes(stored + live))

    def _update_detail(self) -> None:
        spec = self._primary_spec()
        if spec is None:
            for variable in self.detail_vars.values():
                variable.set("—")
            self.detail_vars["name"].set("Select a proxy")
            self.detail_vars["address"].set("Choose a row to see its workspace")
            self.detail_status.configure(background=COLORS["panel_alt"], foreground=COLORS["muted"])
            self._draw_chart()
            return
        record = self._records.get(spec.key) or self.store.record(spec)
        activity = self._activities.get(spec.key)
        name = record.get("alias") or "Proxy workspace"
        if spec.key in self._checking:
            status, status_bg, status_fg = "CHECKING", COLORS["selection"], COLORS["blue"]
        elif activity:
            status, status_bg, status_fg = "● ACTIVE", COLORS["healthy_bg"], COLORS["accent"]
        elif record.get("health") == "Healthy":
            status, status_bg, status_fg = "● HEALTHY", COLORS["healthy_bg"], COLORS["accent"]
        elif record.get("health") == "Error":
            status, status_bg, status_fg = "● ERROR", COLORS["error_bg"], COLORS["danger"]
        else:
            status, status_bg, status_fg = "UNCHECKED", COLORS["panel_alt"], COLORS["muted"]
        stored_up = int(record.get("bytes_up", 0))
        stored_down = int(record.get("bytes_down", 0))
        live_up = (activity or {}).get("bytes_up", 0)
        live_down = (activity or {}).get("bytes_down", 0)
        runtime = float(record.get("active_seconds", 0)) + (activity or {}).get("seconds", 0)
        location = ", ".join(
            part
            for part in (
                record.get("city", ""),
                record.get("region", ""),
                record.get("country_code") or record.get("country", ""),
            )
            if part
        )
        self.detail_vars["name"].set(name)
        self.detail_vars["address"].set(spec.address)
        self.detail_vars["status"].set(status)
        self.detail_vars["exit_ip"].set(record.get("exit_ip") or "—")
        self.detail_vars["location"].set(location or "—")
        self.detail_vars["isp"].set(record.get("isp") or "—")
        auth = "authenticated" if spec.username else "no authentication"
        self.detail_vars["protocol"].set(f"{spec.scheme.upper()} · {auth}")
        self.detail_vars["last_opened"].set(local_time(record.get("last_opened_at", "")))
        self.detail_vars["last_checked"].set(local_time(record.get("last_checked_at", "")))
        self.detail_vars["usage_summary"].set(
            f"{record.get('launch_count', 0)} / {format_duration(runtime)}"
        )
        self.detail_vars["traffic"].set(
            f"{format_bytes(stored_down + live_down)} / {format_bytes(stored_up + live_up)}"
        )
        self.detail_vars["live_rate"].set(
            f"{format_bytes(activity.get('rate', 0))}/s" if activity else "Idle"
        )
        self.detail_status.configure(background=status_bg, foreground=status_fg)
        self._draw_chart()

    def _draw_chart(self) -> None:
        if not hasattr(self, "chart"):
            return
        canvas = self.chart
        canvas.delete("all")
        width = max(20, canvas.winfo_width())
        height = max(20, canvas.winfo_height())
        for fraction in (0.25, 0.5, 0.75):
            y = height * fraction
            canvas.create_line(0, y, width, y, fill="#20304a")
        spec = self._primary_spec()
        history = list(self._rate_history.get(spec.key, ())) if spec else []
        if len(history) < 2 or max(history, default=0) <= 0:
            canvas.create_text(
                width / 2,
                height / 2,
                text="Traffic appears here while Chrome is active",
                fill=COLORS["faint"],
                font=("Segoe UI", 8),
            )
            return
        peak = max(history)
        step = width / max(1, len(history) - 1)
        points: list[float] = []
        for index, value in enumerate(history):
            points.extend((index * step, height - 6 - (value / peak) * (height - 14)))
        fill_points = [0, height] + points + [width, height]
        canvas.create_polygon(fill_points, fill="#15394a", outline="")
        canvas.create_line(points, fill=COLORS["accent"], width=2, smooth=True)
        canvas.create_text(
            7,
            6,
            anchor="nw",
            text=f"peak {format_bytes(peak)}/s",
            fill=COLORS["muted"],
            font=("Segoe UI", 7),
        )

    def open_selected(self) -> None:
        specs = self._selected_specs()
        if not specs:
            self.set_status("Select one or more proxies first", "warning")
            return
        try:
            url = safe_url(self.url_var.get())
        except ValueError as error:
            messagebox.showerror("Invalid URL", str(error), parent=self.root)
            return
        opened = 0
        tabs = 0
        for spec in specs:
            try:
                if self.browser.launch(spec, url):
                    opened += 1
                else:
                    tabs += 1
            except (ProxyError, OSError) as error:
                self.store.update(spec, last_error=str(error), health="Error")
                self.events.put(("launch_error", spec.key, str(error)))
        if opened or tabs:
            self.set_status(f"Opened {opened} browser(s) and {tabs} new tab(s)")

    def new_tab(self) -> None:
        spec = self._primary_spec()
        if not spec:
            self.set_status("Select a proxy first", "warning")
            return
        try:
            self.browser.open_tab(spec, safe_url(self.url_var.get()))
        except (ProxyError, ValueError, OSError) as error:
            messagebox.showerror("Could not open tab", str(error), parent=self.root)

    def close_selected(self) -> None:
        specs = self._selected_specs()
        if not specs:
            self.set_status("Select an active proxy first", "warning")
            return
        for spec in specs:
            self.browser.close(spec.key)
        self.set_status(f"Closing {len(specs)} selected browser(s)…")

    def check_selected(self) -> None:
        specs = self._selected_specs()
        if not specs:
            self.set_status("Select one or more proxies to check", "warning")
            return
        self._start_checks(specs)

    def check_all(self) -> None:
        specs = list(self._specs.values())
        if specs:
            self._start_checks(specs)

    def _start_checks(self, specs: list[ProxySpec]) -> None:
        pending = [spec for spec in specs if spec.key not in self._checking]
        if not pending:
            self.set_status("Those proxies are already being checked", "warning")
            return
        self._checking.update(spec.key for spec in pending)
        self.set_status(f"Checking {len(pending)} proxy(s)…")
        self.render_table()
        timeout = float(self.settings.get("health_timeout", 15))
        for spec in pending:
            future = self.executor.submit(check_proxy, spec, timeout)
            future.add_done_callback(
                lambda completed, item=spec: self._check_finished(item, completed)
            )

    def _check_finished(self, spec: ProxySpec, future) -> None:
        try:
            result, up, down = future.result()
            self.store.save_check(spec, result, bytes_up=up, bytes_down=down)
            self.events.put(("check", spec.key, result))
        except Exception as error:
            self.events.put(("check_crash", spec.key, str(error)))

    def add_proxy(self) -> None:
        dialog = ProxyDialog(self.root, "Add a proxy")
        self.root.wait_window(dialog)
        if dialog.result is None:
            return
        try:
            spec = self.store.add(dialog.result)
        except (ValueError, OSError, ProxyError) as error:
            messagebox.showerror("Could not add proxy", str(error), parent=self.root)
            return
        self.reload_proxies(spec.key)
        self.set_status(f"Added {spec.address}")

    def edit_proxy(self) -> None:
        spec = self._primary_spec()
        if not spec:
            return
        if spec.key in self.browser.active_keys():
            messagebox.showwarning(
                "Browser is active",
                "Close this proxy's Chrome window before editing it.",
                parent=self.root,
            )
            return
        dialog = ProxyDialog(self.root, "Edit proxy", spec.raw)
        self.root.wait_window(dialog)
        if dialog.result is None or dialog.result == spec.raw:
            return
        try:
            updated = self.store.edit(spec, dialog.result)
        except (ValueError, OSError, ProxyError) as error:
            messagebox.showerror("Could not edit proxy", str(error), parent=self.root)
            return
        self.reload_proxies(updated.key)
        self.set_status(f"Updated {updated.address}")

    def remove_proxy(self) -> None:
        specs = self._selected_specs()
        if not specs:
            return
        active = [spec for spec in specs if spec.key in self.browser.active_keys()]
        if active:
            messagebox.showwarning(
                "Browser is active",
                "Close selected Chrome windows before removing their proxies.",
                parent=self.root,
            )
            return
        if not messagebox.askyesno(
            "Remove proxies",
            f"Remove {len(specs)} selected proxy(s) from proxies.txt?\n\n"
            "Saved usage statistics and Chrome profile data will be kept.",
            parent=self.root,
        ):
            return
        try:
            for spec in specs:
                self.store.remove(spec)
        except (ValueError, OSError) as error:
            messagebox.showerror("Could not remove proxy", str(error), parent=self.root)
        self.reload_proxies()

    def delete_profile(self) -> None:
        spec = self._primary_spec()
        if not spec:
            return
        if spec.key in self.browser.active_keys():
            messagebox.showwarning(
                "Browser is active",
                "Close this proxy's Chrome window before deleting its profile.",
                parent=self.root,
            )
            return
        if not messagebox.askyesno(
            "Delete Chrome profile",
            f"Permanently delete cookies, logins, history, and settings for\n{spec.address}?\n\n"
            "Proxy statistics will not be deleted.",
            parent=self.root,
        ):
            return
        try:
            self.store.delete_profile(spec)
        except (OSError, ProxyError) as error:
            messagebox.showerror("Could not delete profile", str(error), parent=self.root)
            return
        self.set_status(f"Deleted Chrome profile for {spec.address}")

    def import_proxies(self) -> None:
        filename = filedialog.askopenfilename(
            parent=self.root,
            title="Import proxy list",
            filetypes=(("Text files", "*.txt"), ("All files", "*.*")),
        )
        if not filename:
            return
        try:
            added, errors = self.store.import_file(Path(filename))
        except (OSError, ProxyError) as error:
            messagebox.showerror("Import failed", str(error), parent=self.root)
            return
        self.reload_proxies()
        detail = f"Imported {added} new proxy(s)."
        if errors:
            detail += f"\n\nSkipped {len(errors)} invalid line(s):\n" + "\n".join(errors[:8])
        messagebox.showinfo("Import complete", detail, parent=self.root)

    def export_stats(self) -> None:
        filename = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export proxy statistics",
            defaultextension=".csv",
            filetypes=(("CSV file", "*.csv"),),
        )
        if not filename:
            return
        fields = (
            "address",
            "alias",
            "health",
            "latency_ms",
            "exit_ip",
            "country",
            "region",
            "city",
            "isp",
            "launch_count",
            "active_seconds",
            "bytes_up",
            "bytes_down",
            "last_opened_at",
            "last_checked_at",
            "notes",
        )
        try:
            with Path(filename).open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                for key in self._specs:
                    record = self._records.get(key, {})
                    writer.writerow({field: record.get(field, "") for field in fields})
        except OSError as error:
            messagebox.showerror("Export failed", str(error), parent=self.root)
            return
        self.set_status(f"Exported statistics to {Path(filename).name}")

    def toggle_favorite(self) -> None:
        specs = self._selected_specs()
        if not specs:
            return
        make_favorite = not all(
            self._records.get(spec.key, {}).get("favorite") for spec in specs
        )
        for spec in specs:
            self.store.update(spec, favorite=make_favorite)
        self._records = self.store.all_records()
        self.render_table()

    def set_alias(self) -> None:
        spec = self._primary_spec()
        if not spec:
            return
        record = self._records.get(spec.key, {})
        dialog = simpledialog.askstring(
            "Set alias",
            "A friendly name for this proxy (leave blank to clear):",
            initialvalue=record.get("alias", ""),
            parent=self.root,
        )
        if dialog is None:
            return
        self.store.update(spec, alias=dialog.strip()[:80])
        self._records = self.store.all_records()
        self.render_table(select_key=spec.key)

    def edit_notes(self) -> None:
        spec = self._primary_spec()
        if not spec:
            return
        record = self._records.get(spec.key, {})
        dialog = NotesDialog(
            self.root,
            record.get("alias") or spec.address,
            record.get("notes", ""),
        )
        self.root.wait_window(dialog)
        if dialog.result is None:
            return
        self.store.update(spec, notes=dialog.result)
        self._records = self.store.all_records()
        self.set_status("Notes saved")

    def copy_address(self) -> None:
        spec = self._primary_spec()
        if not spec:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(spec.address)
        self.set_status("Copied proxy address (credentials excluded)")

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.root, self.settings)
        self.root.wait_window(dialog)
        if dialog.result is None:
            return
        self.settings.update(dialog.result)
        self.url_var.set(self.settings["start_url"])
        self.store.save_settings(self.settings)
        self.set_status("Settings saved")

    def _show_context_menu(self, event) -> None:
        row = self.table.identify_row(event.y)
        if row:
            if row not in self.table.selection():
                self.table.selection_set(row)
                self.table.focus(row)
            self.context_menu.tk_popup(event.x_root, event.y_root)

    def _browser_event(self, kind: str, key: str, message: str) -> None:
        self.events.put(("browser", kind, key, message))

    def _process_events(self) -> None:
        if self._closing:
            return
        changed = False
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event[0]
            if kind == "browser":
                _kind, action, key, message = event
                changed = True
                self.set_status(message)
                if action == "closed":
                    self._activities.pop(key, None)
            elif kind == "check":
                _kind, key, result = event
                self._checking.discard(key)
                changed = True
                if result.get("health") == "Healthy":
                    self.set_status(f"Proxy check complete · {result.get('latency_ms')} ms")
                else:
                    self.set_status("Proxy check failed", "error")
            elif kind in {"check_crash", "launch_error"}:
                _kind, key, error = event
                self._checking.discard(key)
                changed = True
                self.set_status(error, "error")
        if changed:
            self._records = self.store.all_records()
            self._activities = {
                key: activity
                for key, activity in self._activities.items()
                if key in self.browser.active_keys()
            }
            self.render_table()
        self.root.after(200, self._process_events)

    def _poll_activity(self) -> None:
        if self._closing:
            return
        self._activities = self.browser.activities()
        for key, activity in self._activities.items():
            history = self._rate_history.setdefault(key, deque(maxlen=60))
            history.append(float(activity.get("rate", 0)))
        for key, history in self._rate_history.items():
            if key not in self._activities and history:
                history.append(0.0)
        self.render_table()
        self.root.after(1000, self._poll_activity)

    def set_status(self, message: str, tone: str = "normal") -> None:
        self.status_var.set(message)
        color = {
            "normal": COLORS["accent"],
            "warning": COLORS["warning"],
            "error": COLORS["danger"],
        }.get(tone, COLORS["accent"])
        if hasattr(self, "status_dot"):
            self.status_dot.itemconfigure(1, fill=color)

    def on_close(self) -> None:
        active_count = len(self.browser.active_keys())
        if active_count and not messagebox.askyesno(
            "Close ProxyDeck",
            f"ProxyDeck must close {active_count} active proxied Chrome window(s) "
            "because their authentication bridges run inside the manager. Continue?",
            parent=self.root,
        ):
            return
        self._closing = True
        self.browser.shutdown()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    ProxyDeckApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
