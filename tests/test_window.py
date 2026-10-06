"""Offline checks for the packaged-app plumbing: how NUauto runs itself (source vs packaged), the assistant's
command in a packaged app, which window a system gets, and the environment outside programs get.
Run: python test_window.py
"""
import os
import subprocess
import sys

from nuauto import assist
from nuauto import cli
from nuauto import config
from nuauto import window

# ---- running itself: `python -m nuauto ...` from source; the app's own binary when packaged
assert config.FROZEN is False
assert config.self_cmd("apply", "--ui", "json") == [sys.executable, "-m", "nuauto", "apply", "--ui", "json"]
assert config.self_exe() == os.path.join(os.path.dirname(sys.executable), "nuauto")
real_exe = sys.executable
config.FROZEN, sys.executable = True, "/opt/NUauto/NUauto"
try:
    assert config.self_cmd("apply", "--ui", "json") == ["/opt/NUauto/NUauto", "apply", "--ui", "json"]
    os.environ.pop("APPIMAGE", None)
    assert config.self_exe() == "/opt/NUauto/NUauto"            # macOS app / unpacked
    os.environ["APPIMAGE"] = "/home/u/Apps/NUauto.AppImage"
    assert config.self_exe() == "/home/u/Apps/NUauto.AppImage"  # an AppImage: the file, not its temporary mount

    # the assistant's answer-bank / hook command: still exactly two words, and the guard still checks them
    prefix = assist.answer_prefix()
    assert prefix == ["/opt/NUauto/NUauto", "_assist"], prefix
    state = {"answer_cmd": prefix}
    assert assist.check_bash(state, "/opt/NUauto/NUauto _assist answer 'Phone'") is None
    assert assist.check_bash(state, "/opt/NUauto/NUauto _assist rm x") is not None       # not an answer-bank command
    assert assist.check_bash(state, "/opt/NUauto/NUauto apply") is not None              # not the assistant's
    assert assist.check_bash(state, "/opt/NUauto/NUauto _assist answer x; rm -rf ~") is not None  # no shell operators
finally:
    config.FROZEN, sys.executable = False, real_exe
    os.environ.pop("APPIMAGE", None)
assert assist.answer_prefix() == [sys.executable, os.path.abspath(assist.__file__)]

# ---- which window: NUauto's own (mac / gtk), else an app-mode browser, else a tab; --browser = a tab
real = (window.mac_ok, window.gtk_ok, window.chrome, sys.platform)
try:
    window.mac_ok, window.gtk_ok, window.chrome = (lambda: True), (lambda: True), (lambda: "/usr/bin/chromium")
    assert window.kind() == "mac" and window.kind(browser_tab=True) == "tab"
    window.mac_ok = lambda: False
    if sys.platform.startswith("linux"):
        assert window.kind() == "gtk"
    window.gtk_ok = lambda: False
    assert window.kind() == "app"
    window.chrome = lambda: None
    assert window.kind() == "tab"
finally:
    window.mac_ok, window.gtk_ok, window.chrome, _ = real

# ---- programs outside NUauto (system python3, browsers, a terminal) get none of the app's own settings
os.environ.update({"_PYI_ARCHIVE_FILE": "/tmp/x", "APPDIR": "/tmp/.mount_x", "PYTHONHOME": "/tmp/py", "NUAUTO_KEEP": "1"})
try:
    env = window.system_env()
    assert "_PYI_ARCHIVE_FILE" not in env and "APPDIR" not in env and "PYTHONHOME" not in env and env["NUAUTO_KEEP"] == "1"
    assert os.environ["APPDIR"] == "/tmp/.mount_x"  # this process keeps its own
finally:
    for k in ("_PYI_ARCHIVE_FILE", "APPDIR", "PYTHONHOME", "NUAUTO_KEEP"):
        os.environ.pop(k, None)

# ---- the GTK helper runs with the system's python3 and only says yes or no (no window in a check)
py = window.system_python()
if py:
    r = subprocess.run([py, window.GTK_HELPER, "--check"], env=window.system_env(), capture_output=True, timeout=60)
    assert r.returncode in (0, 1), r.stderr[-300:]
    r = subprocess.run([py, window.GTK_HELPER], env=window.system_env(), capture_output=True, text=True, timeout=60)
    assert r.returncode != 0 and "window_gtk.py" in (r.stderr + r.stdout)  # no URL: usage, no window

# ---- Playwright's own command line through the driver in the package (what a packaged app uses to install Firefox)
assert cli.playwright_cli(["--version"]) == 0

print("All window and packaging checks passed.")
