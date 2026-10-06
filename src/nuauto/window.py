"""The NUauto window: its own window when this system can make one, else an app-mode browser window, else a tab.

  macOS   pywebview (the system's WebKit): runs on the main thread until the window closes
  Linux   window_gtk.py, run by the system's python3 when it has GTK + WebKitGTK (most GNOME desktops)
  else    Chrome / Chromium / Brave / Edge in app mode (--app: no tabs, no address bar), else your default browser
Closing NUauto's own window quits NUauto (a run in progress stops the way Ctrl+C stops it). In a browser window or
tab it keeps running until Settings > Quit, or 30 minutes after the last page closed.
"""
import os
import shutil
import subprocess
import sys
import threading
import webbrowser

from nuauto import config

CHROMES = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "brave-browser", "microsoft-edge"]
MAC_CHROMES = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
               "/Applications/Chromium.app/Contents/MacOS/Chromium",
               "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
               "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
GTK_HELPER = os.path.join(config.SRC_DIR, "window_gtk.py")
ICON = os.path.join(config.SRC_DIR, "gui_static", "icon.svg")
SIZE = (1200, 860)

_open = []          # NUauto's own windows (GTK helper processes) still open
_lock = threading.Lock()


def system_python():
    """The system's python3 (not NUauto's own): it is the one with GTK, if anything is."""
    for p in ("/usr/bin/python3", "/usr/local/bin/python3"):
        if os.access(p, os.X_OK):
            return p
    found = shutil.which("python3")
    return found if found and not found.startswith(config.PROJECT_DIR) else None


APP_VARS = ("PYTHONHOME", "PYTHONPATH", "PYTHONSAFEPATH", "APPDIR", "APPIMAGE", "ARGV0", "OWD")


def system_env():
    """For programs that are not NUauto's own children (the system's python3, browsers, a terminal window that may
    start a new NUauto): none of the packaged app's Python, PyInstaller or AppImage settings."""
    return {k: v for k, v in os.environ.items() if k not in APP_VARS and not k.startswith("_PYI_")}


def gtk_ok():
    py = system_python()
    if not py or not os.path.exists(GTK_HELPER) or not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    try:
        return subprocess.run([py, GTK_HELPER, "--check"], env=system_env(), capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def mac_ok():
    if sys.platform != "darwin":
        return False
    try:
        import webview  # noqa: F401  (pywebview; installed on macOS only)
        return True
    except ImportError:
        return False


def kind(browser_tab=False):
    """Which window this system gets: "mac", "gtk", "app" (a Chrome-like browser in app mode) or "tab"."""
    if browser_tab:
        return "tab"
    if mac_ok():
        return "mac"
    if sys.platform.startswith("linux") and gtk_ok():
        return "gtk"
    return "app" if chrome() else "tab"


def chrome():
    if sys.platform == "darwin":
        return next((p for p in MAC_CHROMES if os.path.exists(p)), None)
    return next((shutil.which(c) for c in CHROMES if shutil.which(c)), None)


def open_browser(url, browser_tab=False):
    found = None if browser_tab else chrome()
    if found:
        subprocess.Popen([found, f"--app={url}", "--new-window"], start_new_session=True, env=system_env(),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        cmd = ["open", url] if sys.platform == "darwin" else ["xdg-open", url]
        try:
            subprocess.Popen(cmd, env=system_env(), start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            webbrowser.open(url)


def open_gtk(url, on_last_close):
    """One more GTK window (its own process); on_last_close runs when no GTK window is left."""
    proc = subprocess.Popen([system_python(), GTK_HELPER, url, ICON], env=system_env(), start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with _lock:
        _open.append(proc)

    def wait():
        proc.wait()
        with _lock:
            _open.remove(proc)
            last = not _open
        if last:
            on_last_close()
    threading.Thread(target=wait, daemon=True).start()


def close_all():
    """Close NUauto's own windows (Quit from the page)."""
    with _lock:
        procs = list(_open)
    for p in procs:
        p.terminate()
    if mac_ok():
        import webview
        for w in list(webview.windows):
            w.destroy()


def run_mac(url):
    """NUauto's window on macOS. Blocks until every window is closed; must run on the main thread."""
    import webview
    webview.create_window("NUauto", url, width=SIZE[0], height=SIZE[1], min_size=(420, 560))
    webview.start(private_mode=True)


def another(url, how, on_last_close):
    """A second `nuauto gui` asked for a window: one more of the same kind."""
    if how == "mac":
        import webview
        webview.create_window("NUauto", url, width=SIZE[0], height=SIZE[1], min_size=(420, 560))
    elif how == "gtk":
        open_gtk(url, on_last_close)
    else:
        open_browser(url, browser_tab=how == "tab")
