import threading
import time
import json
import requests
import re
import os
import sys
import fcntl          # LINUX CHANGE: used for the single-instance lock (replaces win32event mutex)
import subprocess     # LINUX CHANGE: used for desktop notifications via notify-send
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox
from PIL import Image

# LINUX CHANGE (v3.0.3): on Wayland desktops such as Raspberry Pi OS
# Bookworm, the panel ignores pystray's temp-file icons, so we talk to
# the AppIndicator system directly using the theme icon installed by
# the .deb ("digigrids"). On X11 desktops we keep using pystray.
USE_NATIVE_TRAY = False
if os.getenv("XDG_SESSION_TYPE", "").lower() == "wayland":
    try:
        import gi
        gi.require_version('AyatanaAppIndicator3', '0.1')
        gi.require_version('Gtk', '3.0')
        from gi.repository import AyatanaAppIndicator3 as AI, Gtk, GLib
        USE_NATIVE_TRAY = True
    except Exception:
        USE_NATIVE_TRAY = False

if not USE_NATIVE_TRAY:
    import pystray

# -------------------------------
# SINGLE INSTANCE LOCK
# LINUX CHANGE: Windows used a named mutex (win32event). On Linux the
# standard trick is a "lock file": we open a small file and ask the
# kernel for an exclusive lock on it. If another copy of the program
# already holds the lock, we get an error and know we should exit.
# The lock is released automatically when the program exits or crashes.
# -------------------------------

_RUNTIME_DIR = Path(os.getenv("XDG_RUNTIME_DIR", "/tmp"))
_LOCK_PATH = _RUNTIME_DIR / f"digigrids-{os.getuid()}.lock"

_lock_handle = open(_LOCK_PATH, "w")   # must stay open for the whole run

try:
    fcntl.flock(_lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    # Another instance is already running — inform the user then exit
    _root = tk.Tk()
    _root.withdraw()
    messagebox.showinfo("Digigrids", "Digigrids is already running in your system tray.")
    _root.destroy()
    sys.exit(0)

# -------------------------------
# CONFIG FILE
# LINUX CHANGE: Windows used %LOCALAPPDATA%\Digigrids. The Linux
# standard location for per-user app settings is ~/.config/<appname>,
# so config lives in ~/.config/digigrids/
# -------------------------------

APP_DIR = Path(os.getenv("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "digigrids"
APP_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_FILE = APP_DIR / "config.json"
RETRY_FILE = APP_DIR / "retry_queue.json"

DEFAULT_CONFIG = {
    "api_key": "",
    "adif_path": "",
    "server": "https://digigrids.net/incoming/receiver.php"
}

def load_config():
    if not CONFIG_FILE.exists():
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=4)

config = load_config()

# -------------------------------
# LOGGING
# -------------------------------

import logging
from logging.handlers import RotatingFileHandler

LOG_FILE = APP_DIR / "digigrids.log"

handler = RotatingFileHandler(
    LOG_FILE,
    maxBytes=200_000,   # 200 KB per file
    backupCount=3       # keep 3 old logs
)

formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s"
)

handler.setFormatter(formatter)

logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(handler)

def log(msg):
    logger.info(msg)

def log_error(msg):
    logger.error(msg)

def load_retry_queue():
    if not RETRY_FILE.exists():
        return []
    try:
        with open(RETRY_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []

def save_retry_queue(queue):
    try:
        with open(RETRY_FILE, "w") as f:
            json.dump(queue, f)
    except Exception as e:
        log_error(f"Retry save error: {e}")

def add_to_retry(payload):
    queue = load_retry_queue()
    queue.append(payload)
    save_retry_queue(queue)
    log(f"Added to retry queue: {payload.get('call', 'UNKNOWN')}")

# -------------------------------
# GLOBALS
# -------------------------------

running = False
tray_icon = None

def notify(title, message):
    # LINUX CHANGE: pystray's built-in notify() does not work on every
    # Linux desktop, so we try it first and fall back to the standard
    # "notify-send" command, which works on Ubuntu, Mint, etc.
    try:
        if tray_icon:
            tray_icon.notify(message, title)
            return
    except Exception:
        pass
    try:
        subprocess.Popen(
            ["notify-send", "--app-name=Digigrids",
             f"--icon={ICON_PATH}", title, message]
        )
    except Exception:
        pass

# -------------------------------
# CONFIG VALIDATION
# -------------------------------

def is_config_valid():
    api = config.get("api_key", "").strip()
    path = config.get("adif_path", "").strip()
    return bool(api and path and Path(path).exists())

# -------------------------------
# ADIF PARSING
# -------------------------------

FIELD_RE = {
    "call": re.compile(r"<call:\d+>([^ <]+)", re.I),
    "station_callsign": re.compile(r"<station_callsign:\d+>([^ <]+)", re.I),
    "gridsquare": re.compile(r"<gridsquare:\d+>([^ <]+)", re.I),
    "band": re.compile(r"<band:\d+>([^ <]+)", re.I),
    "mode": re.compile(r"<mode:\d+>([^ <]+)", re.I),
    "submode": re.compile(r"<submode:\d+>([^ <]+)", re.I),
    "qso_date": re.compile(r"<qso_date:\d+>(\d{8})", re.I),
}

def extract(field, text):
    m = FIELD_RE[field].search(text)
    return m.group(1) if m else None

# -------------------------------
# WATCHER
# -------------------------------

def watch_adif():
    global running

    adif_file = Path(config["adif_path"])

    if not adif_file.exists():
        log_error("ADIF file not found")
        notify("Error", "ADIF file not found")
        return

    last_size = adif_file.stat().st_size
    buffer = ""

    log(f"Watching: {adif_file}")

    while running:
        try:
            size = adif_file.stat().st_size

            if size > last_size:
                with adif_file.open("r", encoding="utf-8", errors="ignore") as f:
                    f.seek(last_size)
                    buffer += f.read()

                last_size = size

                while "<EOR>" in buffer.upper():
                    idx = buffer.upper().find("<EOR>") + 5
                    record = buffer[:idx]
                    buffer = buffer[idx:]

                    process_record(record)

            time.sleep(1)

        except Exception as e:
            log_error(f"Watcher error: {e}")
            notify("Watcher Error", str(e))
            time.sleep(2)

def retry_worker():
    while True:
        try:
            queue = load_retry_queue()

            if not queue:
                time.sleep(30)
                continue

            log(f"Retry queue size: {len(queue)}")

            new_queue = []

            for payload in queue:
                try:
                    headers = {
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                        "User-Agent": "Digigrids-Client/1.0"
                    }

                    r = requests.post(
                        config["server"],
                        json=payload,
                        headers=headers,
                        timeout=10
                    )

                    if r.status_code == 200:
                        try:
                            response = r.json()
                            status = response.get("status")

                            # Server returns "accepted", "duplicate", or
                            # "verifying" — any of these means the server
                            # accepted the QSO, so remove it from the queue.
                            if status in ("accepted", "duplicate", "verifying"):
                                log(f"Retry success: {payload.get('call', 'UNKNOWN')} ({status})")
                            else:
                                log(f"Retry failed (server rejected): {payload.get('call', 'UNKNOWN')}")
                                new_queue.append(payload)
                        except Exception:
                            log("Retry failed (bad JSON response)")
                            new_queue.append(payload)

                    elif r.status_code >= 500:
                        # Server error — keep in queue and try again later
                        log(f"Retry failed (HTTP {r.status_code}): {payload.get('call', 'UNKNOWN')}")
                        new_queue.append(payload)

                    else:
                        # 400, 401, 403 etc — permanent rejection, drop from queue
                        log(f"Retry dropped — server rejected (HTTP {r.status_code}): {payload.get('call', 'UNKNOWN')}")

                except Exception as e:
                    log_error(f"Retry exception: {e}")
                    new_queue.append(payload)

            save_retry_queue(new_queue)

        except Exception as e:
            log_error(f"Retry worker error: {e}")

        time.sleep(30)

# -------------------------------
# PROCESS + SEND
# -------------------------------

def process_record(record):
    call = extract("call", record)
    station = extract("station_callsign", record)
    grid = extract("gridsquare", record)
    band = extract("band", record)
    mode = extract("submode", record) or extract("mode", record)
    date = extract("qso_date", record)

    # --- VALIDATION ---
    missing = []

    VALID_BANDS = {'160m','80m','60m','40m','30m','20m','17m','15m','12m','10m','6m'}

    if not call:
        missing.append("call")
    if not station:
        missing.append("station")
    if not grid:
        missing.append("grid")
    if not band:
        missing.append("band")
    elif band.lower() not in VALID_BANDS:
        missing.append(f"band({band} not supported)")
    if not mode:
        missing.append("mode")
    if not date:
        missing.append("date")

    if missing:
        cs = call if call else "UNKNOWN"
        log(f"Skipped QSO ({cs}) missing: {', '.join(missing)}")
        notify("Invalid QSO - Skipped", f"{cs}: {', '.join(missing)}")
        return

    # --- SEND ---

    payload = {
        "api_key": config["api_key"].strip(),
        "call": call,
        "station_callsign": station,
        "gridsquare": grid,
        "band": band,
        "mode": mode,
        "qso_date": date
    }

    try:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Digigrids-Client/1.0"
        }

        r = requests.post(
            config["server"],
            json=payload,
            headers=headers,
            timeout=10
        )

        msg = f"{call} {grid} {band} {mode}"
        log(f"Sent: {msg} ({r.status_code})")

        if r.status_code == 200:
            try:
                response = r.json()
                status = response.get("status")

                # Server returns "verifying" for sea/rare grids that
                # need admin verification.
                if status == "verifying":
                    notify("Verification Required", "Grid sent for admin verification")
                    log(f"Pending verification: {msg}")

                elif response.get("is_new_grid") == 1:
                    notify("New Grid!", msg)
                    log(f"New grid: {msg}")
                    flash_icon()
            except Exception:
                pass

        elif r.status_code >= 500:
            # Server error — may be temporary, worth retrying later
            log(f"Server error (HTTP {r.status_code}): {msg}")
            notify("Send Failed", f"{msg} — will retry")
            add_to_retry(payload)

        else:
            # 400, 401, 403 etc — server deliberately rejected the QSO
            # Retrying will never succeed so just log and notify, do not queue
            log(f"QSO rejected by server (HTTP {r.status_code}): {msg}")
            notify("QSO Rejected", f"{msg} ({r.status_code})")

    except Exception as e:
        log_error(f"Send error: {e}")
        notify("Error sending QSO", str(e))
        add_to_retry(payload)

# -------------------------------
# STARTUP
# LINUX CHANGE: Windows used a registry "Run" key. The Linux desktop
# standard is an "autostart" folder: any .desktop file placed in
# ~/.config/autostart/ is launched when the user logs in. So enabling
# startup = create that file, disabling = delete it.
# -------------------------------

AUTOSTART_DIR = Path(os.getenv("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "autostart"
AUTOSTART_FILE = AUTOSTART_DIR / "digigrids.desktop"

AUTOSTART_CONTENT = """[Desktop Entry]
Type=Application
Name=Digigrids Client
Comment=Watches your ADIF log and uploads QSOs to digigrids.net
Exec=/usr/bin/digigrids-client
Icon=digigrids
Terminal=false
X-GNOME-Autostart-enabled=true
"""

def set_startup(enable=True):
    try:
        if enable:
            AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
            AUTOSTART_FILE.write_text(AUTOSTART_CONTENT)
            log(f"Startup enabled: {AUTOSTART_FILE}")
        else:
            if AUTOSTART_FILE.exists():
                AUTOSTART_FILE.unlink()
                log("Startup disabled")
            else:
                log("Startup entry not found")
    except Exception as e:
        log_error(f"Startup error: {e}")

def is_startup_enabled():
    return AUTOSTART_FILE.exists()

def toggle_startup(icon, item):
    set_startup(not is_startup_enabled())
    icon.update_menu()

# -------------------------------
# TRAY ICON
# LINUX CHANGE: instead of bundling the .ico next to the exe, the icon
# is installed to a fixed system path by the .deb package. If that path
# is missing (e.g. running the script by hand during testing) we fall
# back to a PNG sitting next to this script.
# -------------------------------

ICON_PATH = "/usr/share/digigrids/digigrids.png"

if not os.path.exists(ICON_PATH):
    ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "digigrids.png")

ICON_IMAGE = Image.open(ICON_PATH)

def create_icon():
    return ICON_IMAGE

HIGHLIGHT_ICON = None

def create_highlight_icon():
    global HIGHLIGHT_ICON
    if HIGHLIGHT_ICON is None:
        img = ICON_IMAGE.convert("RGBA")
        overlay = Image.new("RGBA", img.size, (0, 255, 0, 160))
        HIGHLIGHT_ICON = Image.alpha_composite(img, overlay)
    return HIGHLIGHT_ICON

def flash_icon(duration=3):
    if USE_NATIVE_TRAY:
        try:
            GLib.idle_add(_indicator.set_icon_full, "digigrids-alert", "New grid")

            def restore():
                time.sleep(duration)
                GLib.idle_add(_indicator.set_icon_full, "digigrids", "Digigrids")

            threading.Thread(target=restore, daemon=True).start()
        except Exception as e:
            log_error(f"Flash icon error: {e}")
        return

    if not tray_icon:
        return

    try:
        original = tray_icon.icon
        tray_icon.icon = create_highlight_icon()

        def restore():
            time.sleep(duration)
            tray_icon.icon = original

        threading.Thread(target=restore, daemon=True).start()

    except Exception as e:
        log_error(f"Flash icon error: {e}")

def start_watcher(icon, item):
    global running
    if not running:
        running = True
        threading.Thread(target=watch_adif, daemon=True).start()
        log("Watcher started")
        notify("Digigrids", "Watcher started")

def stop_watcher(icon, item):
    global running
    running = False
    log("Watcher stopped")

def configure(icon, item):
    def run_window():
        window = tk.Tk()
        window.title("Digigrids Settings")
        window.geometry("500x320")

        def save_and_close():
            global config
            config["api_key"] = api_entry.get()
            config["adif_path"] = path_entry.get()
            save_config(config)
            config = load_config()
            window.destroy()
            if USE_NATIVE_TRAY:
                _native_refresh_menu()
            elif icon:
                icon.menu = create_menu()
                icon.update_menu()
            log("Config saved")

        tk.Label(window, text="API Key:").pack(anchor="w", padx=10, pady=(10, 0))
        api_entry = tk.Entry(window, width=50)
        api_entry.pack(padx=10)
        api_entry.insert(0, config.get("api_key", ""))

        tk.Label(window, text="ADIF File:").pack(anchor="w", padx=10, pady=(10, 0))
        path_frame = tk.Frame(window)
        path_frame.pack(padx=10, fill="x")

        path_entry = tk.Entry(path_frame)
        path_entry.pack(side="left", fill="x", expand=True)
        path_entry.insert(0, config.get("adif_path", ""))

        def browse_file():
            file_path = filedialog.askopenfilename(title="Select ADIF file")
            if file_path:
                path_entry.delete(0, tk.END)
                path_entry.insert(0, file_path)

        tk.Button(path_frame, text="Browse", command=browse_file).pack(side="right")
        tk.Button(window, text="Save", command=save_and_close).pack(pady=15)

        window.mainloop()

    threading.Thread(target=run_window, daemon=True).start()

def quit_app(icon, item):
    global running

    log("Digigrids client shutting down")

    running = False  # stop watcher loop
    time.sleep(0.5)  # allow thread to exit cleanly

    if USE_NATIVE_TRAY:
        Gtk.main_quit()
    else:
        icon.stop()

    log("Digigrids client closed")

# -------------------------------
# MENU
# -------------------------------

def create_menu():
    if not is_config_valid():
        return pystray.Menu(
            pystray.MenuItem("Settings", configure),
            pystray.MenuItem("Exit", quit_app),
        )

    return pystray.Menu(
        pystray.MenuItem("Start Watch ADIF", start_watcher),
        pystray.MenuItem("Stop Watch ADIF", stop_watcher),
        pystray.MenuItem(
            "Startup Enabled",
            toggle_startup,
            checked=lambda item: is_startup_enabled()
        ),
        pystray.MenuItem("Configure", configure),
        pystray.MenuItem("Exit", quit_app),
    )

# -------------------------------
# MAIN
# -------------------------------

_indicator = None

def _native_menu():
    # Build a Gtk menu mirroring the pystray one
    menu = Gtk.Menu()

    def add(label, callback):
        item = Gtk.MenuItem(label=label)
        item.connect("activate", lambda _i: callback(None, None))
        menu.append(item)
        item.show()

    if is_config_valid():
        add("Start Watch ADIF", start_watcher)
        add("Stop Watch ADIF", stop_watcher)
        startup_label = "Startup: On" if is_startup_enabled() else "Startup: Off"
        add(startup_label + "  (click to toggle)", _native_toggle_startup)
        add("Configure", configure)
    else:
        add("Settings", configure)
    add("Exit", quit_app)
    return menu

def _native_refresh_menu():
    if _indicator:
        GLib.idle_add(_indicator.set_menu, _native_menu())

def _native_toggle_startup(icon, item):
    set_startup(not is_startup_enabled())
    _native_refresh_menu()

if USE_NATIVE_TRAY:
    _indicator = AI.Indicator.new(
        "digigrids", "digigrids", AI.IndicatorCategory.APPLICATION_STATUS
    )
    _indicator.set_status(AI.IndicatorStatus.ACTIVE)
    _indicator.set_title("Digigrids Client")
    _indicator.set_menu(_native_menu())
    tray_icon = None
    log("Digigrids client started (native tray)")
else:
    tray_icon = pystray.Icon(
        "Digigrids",
        create_icon(),
        "Digigrids Client"
    )
    tray_icon.menu = create_menu()
    log("Digigrids client started")

# AUTO START WATCHER
if is_config_valid():
    def auto_start():
        time.sleep(1)  # let tray fully initialise
        start_watcher(None, None)
        log("Watcher auto-started")

    threading.Thread(target=auto_start, daemon=True).start()

# START RETRY WORKER
    threading.Thread(target=retry_worker, daemon=True).start()

# FIRST RUN / NOT CONFIGURED
if not is_config_valid():
    def first_run():
        time.sleep(1)
        notify("Setup Required", "Please configure Digigrids")

        # Open settings automatically
        configure(tray_icon, None)

    threading.Thread(target=first_run, daemon=True).start()

if USE_NATIVE_TRAY:
    Gtk.main()
else:
    tray_icon.run()
