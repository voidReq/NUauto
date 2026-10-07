import glob
import json
from datetime import date as _date
import os
import shutil
import socket
import sys

# The packaged app (PyInstaller: an AppImage on Linux, NUauto.app on macOS; see packaging/). Its code, prompts and
# page are inside the bundle (read-only); your files go in the usual per-user app-data folder.
FROZEN = bool(getattr(sys, "frozen", False))
if FROZEN:
    PROJECT_DIR = getattr(sys, "_MEIPASS")
    SRC_DIR = os.path.join(PROJECT_DIR, "nuauto")
    _USER_STATE = os.path.expanduser("~/Library/Application Support/NUauto") if sys.platform == "darwin" else \
        os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "NUauto")
    # Playwright looks for its browsers inside the bundle when frozen; the bundle is read-only (an AppImage's is a
    # temporary mount), so they go in the usual per-user cache, where a source install would put them too.
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", os.path.expanduser("~/Library/Caches/ms-playwright")
                          if sys.platform == "darwin" else
                          os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "ms-playwright"))
    if sys.platform.startswith("linux"):
        # PyInstaller's bootloader put the bundle on LD_LIBRARY_PATH (saving the old value). Programs started from
        # here (the system's python3, claude, browsers, terminals) must not load the bundle's copies of libraries.
        if "LD_LIBRARY_PATH_ORIG" in os.environ:
            os.environ["LD_LIBRARY_PATH"] = os.environ.pop("LD_LIBRARY_PATH_ORIG")
        else:
            os.environ.pop("LD_LIBRARY_PATH", None)
else:
    # The repo root (this file is src/nuauto/config.py). Code lives in src/nuauto/, prompts in prompts/.
    PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    SRC_DIR = os.path.dirname(os.path.abspath(__file__))
    _USER_STATE = PROJECT_DIR
# Where local/, data/, logs/ and work/ live: the repo root (the app-data folder when packaged), unless
# NUAUTO_STATE_DIR names another folder (demo mode and tests: a temp folder, so they never touch your real files).
STATE_DIR = os.environ.get("NUAUTO_STATE_DIR") or _USER_STATE
# Demo mode (`nuauto gui --demo`, set by gui.py): fake sheet, fake NUworks pages, fake Claude (demo.py).
DEMO = os.environ.get("NUAUTO_DEMO") == "1"
if DEMO and os.path.realpath(STATE_DIR) == os.path.realpath(_USER_STATE):
    sys.exit("Demo mode needs its own NUAUTO_STATE_DIR; it never uses the real local/ and data/.")
# Demo runs wait this fraction of the usual pauses (NUAUTO_DEMO_PACE). Real runs always wait the full time.
DEMO_PACE = float(os.environ.get("NUAUTO_DEMO_PACE", "0.05")) if DEMO else 1.0
# local/ (gitignored): everything personal or secret: settings, Google/NUworks logins, answers, browser
# profiles, the homelab's resume copy and Mark-link secret. Generated state stays in data/, logs/, work/.
LOCAL_DIR = os.path.join(STATE_DIR, "local")
# Personal settings (sheet, resume, homelab, public URL): local/local_config.json.
# Start from local_config.example.json. Without it the example's placeholders are used (enough for the offline tests).
LOCAL_CONFIG_PATH = os.path.join(LOCAL_DIR, "local_config.json")
_local_path = LOCAL_CONFIG_PATH if os.path.exists(LOCAL_CONFIG_PATH) else os.path.join(PROJECT_DIR, "local_config.example.json")
with open(_local_path) as _f:
    LOCAL = json.load(_f)

SHEET_ID = LOCAL["sheet_id"]
# Weekly cap periods: fixed 7-day weeks from this day ("YYYY-MM-DD"); "" = any rolling 7 days.
WEEK_START = _date.fromisoformat(LOCAL["week_start"]) if LOCAL.get("week_start") else None
TOKEN_PATH = os.path.join(LOCAL_DIR, "token.json")
# Date of the last Google login (the app is in Testing mode, so a login lasts 7 days). Not secret.
GOOGLE_LOGIN_PATH = os.path.join(LOCAL_DIR, "google_login.txt")
GOOGLE_LOGIN_DAYS = 7

REPO = "voidReq/NUauto"  # GitHub: where the packaged app's releases come from (health.app_update)
NUWORKS_START_URL = "https://northeastern-csm.symplicity.com/students/app/jobs/discover"
# Domain lock: automated browsing may only navigate to these hosts. The SSO login
# host is deliberately NOT here; log in by hand with `nuauto login`.
ALLOWED_HOSTS = {"northeastern-csm.symplicity.com"}
# Job pool (jobs.py)
# Laptop: the resume at local_config resume_path. Homelab: a copy the laptop pushes to local/resume.pdf.
LAPTOP_RESUME = os.path.expanduser(LOCAL["resume_path"])
RESUME_PATH = LAPTOP_RESUME if os.path.exists(LAPTOP_RESUME) else os.path.join(LOCAL_DIR, "resume.pdf")
DATA_DIR = os.path.join(STATE_DIR, "data")   # list, triage, details, scores, ratings, pool
WORK_DIR = os.path.join(STATE_DIR, "work")   # batch files exchanged with the Claude subagents
ANSWERS_PATH = os.path.join(LOCAL_DIR, "answers.json")
PROFILE_PATH = os.path.join(LOCAL_DIR, "profile.json")
# Extra hosts allowed ONLY while apply.py clicks the one-click re-login (see browser.relogin).
SSO_HOSTS = {"shibboleth-northeastern-csm.symplicity.com", "neuidmsso.neu.edu"}
COOKIES_PATH = os.path.join(LOCAL_DIR, "session_cookies.json")
PROFILE_DIR = os.path.join(LOCAL_DIR, "browser_profile")
# Held (flock) by the one process using PROFILE_DIR; next to the profile, not in it (sync copies the profile).
PROFILE_LOCK_PATH = os.path.join(LOCAL_DIR, "browser_profile.lock")
# The running `nuauto gui`: its pid (so `nuauto apply --ui json` can check the GUI started it) and port.
GUI_LOCK_PATH = os.path.join(LOCAL_DIR, "gui.lock")
LOGS_DIR = os.path.join(STATE_DIR, "logs")
# nuauto assist (assist.py): the agent's browser profile, never the NUworks one. Holds company-site logins.
ASSIST_PROFILE_DIR = os.path.join(LOCAL_DIR, "assist_profile")
# Folders/files the agent may read (notes, writeups) to suggest answers; the resume is always added.
ASSIST_READ_PATHS = [os.path.expanduser(p) for p in LOCAL.get("assist_read_paths", [])]

# Homelab server (optional): runs the twice-daily update and the Discord reminders. The laptop syncs with it over
# Tailscale (sync.py): it pulls job data and pushes ratings, the NUworks session and the resume.
# server_hostname "" in local_config.json = no homelab: everything runs on this machine, no syncing.
SERVER_HOSTNAME = "" if FROZEN else LOCAL["server_hostname"]  # the packaged app is always local mode (sync copies a checkout)
SERVER = LOCAL.get("server_ssh") or SERVER_HOSTNAME  # how the laptop reaches it: ssh alias or host
SERVER_DIR = LOCAL["server_dir"]
HAS_SERVER = bool(SERVER_HOSTNAME)
IS_SERVER = HAS_SERVER and socket.gethostname() == SERVER_HOSTNAME
# Homelab code comes from GitHub's main (deploy.py, run by nuauto-deploy.timer) instead of from the laptop's checkout.
# Off by default: then sync.push copies the laptop's code, as before. deploy_repo: another fork's URL.
DEPLOY_FROM_GIT = bool(LOCAL.get("deploy_from_git")) and HAS_SERVER
DEPLOY_REPO = LOCAL.get("deploy_repo") or f"https://github.com/{REPO}.git"
DISCORD_WEBHOOK_PATH = os.path.join(LOCAL_DIR, "discord_webhook.txt")  # secret, mode 600, never print

WEB_SECRET_PATH = os.path.join(LOCAL_DIR, "web_secret.txt")  # homelab only: signs the Mark links; secret

READ_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
WRITE_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def find_client_json():
    """local/client_secret.json (both machines), else the one in ~/Downloads."""
    local = os.path.join(LOCAL_DIR, "client_secret.json")
    if os.path.exists(local):
        return local
    matches = glob.glob(os.path.expanduser("~/Downloads/client_secret_*.json"))
    if len(matches) != 1:
        sys.exit(f"Expected client_secret.json in the project dir or exactly 1 ~/Downloads/client_secret_*.json, found {len(matches)}.")
    return matches[0]


def lock_token():
    if os.path.exists(TOKEN_PATH):
        os.chmod(TOKEN_PATH, 0o600)


def self_cmd(*args):
    """The command line for `nuauto <args>` as a child of this process. The packaged app runs itself."""
    return [sys.executable, *args] if FROZEN else [sys.executable, "-m", "nuauto", *args]


def self_exe():
    """This program, for something that outlives this process (a terminal window, a timer, an app icon): the
    AppImage file or the app's binary when packaged, else the installed nuauto command."""
    if FROZEN:
        return os.environ.get("APPIMAGE") or sys.executable
    return os.path.join(os.path.dirname(sys.executable), "nuauto")


# Where command-line tools usually get installed. Apps started from the desktop (macOS especially) and systemd
# units get a short PATH, so these are searched too. ~/.local/bin comes first, as it always has.
TOOL_DIRS = ["~/.local/bin", "~/.claude/local", "/opt/homebrew/bin", "/usr/local/bin", "~/.npm-global/bin",
             "~/.volta/bin", "~/.bun/bin"]


def tool_path(name):
    """Full path of a tool such as claude or npx, or None. A path saved in local_config.json "tools" wins."""
    saved = os.path.expanduser((LOCAL.get("tools") or {}).get(name) or "")
    if saved and os.access(saved, os.X_OK):
        return saved
    dirs = [os.path.expanduser(TOOL_DIRS[0]), os.environ.get("PATH", "")]
    dirs += [os.path.expanduser(d) for d in TOOL_DIRS[1:]]
    dirs += sorted(glob.glob(os.path.expanduser("~/.nvm/versions/node/*/bin")), reverse=True)
    return shutil.which(name, path=os.pathsep.join(d for d in dirs if d))
