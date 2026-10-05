import glob
import json
from datetime import date as _date
import os
import socket
import sys

# The repo root (this file is src/nuauto/config.py). Code lives in src/nuauto/, prompts in prompts/.
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
# local/ (gitignored): everything personal or secret: settings, Google/NUworks logins, answers, browser
# profiles, the homelab's resume copy and Mark-link secret. Generated state stays in data/, logs/, work/.
LOCAL_DIR = os.path.join(PROJECT_DIR, "local")
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

NUWORKS_START_URL = "https://northeastern-csm.symplicity.com/students/app/jobs/discover"
# Domain lock: automated browsing may only navigate to these hosts. The SSO login
# host is deliberately NOT here; log in by hand with `nuauto login`.
ALLOWED_HOSTS = {"northeastern-csm.symplicity.com"}
# Job pool (jobs.py)
# Laptop: the resume at local_config resume_path. Homelab: a copy the laptop pushes to local/resume.pdf.
LAPTOP_RESUME = os.path.expanduser(LOCAL["resume_path"])
RESUME_PATH = LAPTOP_RESUME if os.path.exists(LAPTOP_RESUME) else os.path.join(LOCAL_DIR, "resume.pdf")
DATA_DIR = os.path.join(PROJECT_DIR, "data")   # list, triage, details, scores, ratings, pool
WORK_DIR = os.path.join(PROJECT_DIR, "work")   # batch files exchanged with the Claude subagents
ANSWERS_PATH = os.path.join(LOCAL_DIR, "answers.json")
PROFILE_PATH = os.path.join(LOCAL_DIR, "profile.json")
# Extra hosts allowed ONLY while apply.py clicks the one-click re-login (see browser.relogin).
SSO_HOSTS = {"shibboleth-northeastern-csm.symplicity.com", "neuidmsso.neu.edu"}
COOKIES_PATH = os.path.join(LOCAL_DIR, "session_cookies.json")
PROFILE_DIR = os.path.join(LOCAL_DIR, "browser_profile")
LOGS_DIR = os.path.join(PROJECT_DIR, "logs")
# nuauto assist (assist.py): the agent's browser profile, never the NUworks one. Holds company-site logins.
ASSIST_PROFILE_DIR = os.path.join(LOCAL_DIR, "assist_profile")
# Folders/files the agent may read (notes, writeups) to suggest answers; the resume is always added.
ASSIST_READ_PATHS = [os.path.expanduser(p) for p in LOCAL.get("assist_read_paths", [])]

# Homelab server (optional): runs the twice-daily update and the Discord reminders. The laptop syncs with it over
# Tailscale (sync.py): it pulls job data and pushes ratings, the NUworks session and the resume.
# server_hostname "" in local_config.json = no homelab: everything runs on this machine, no syncing.
SERVER_HOSTNAME = LOCAL["server_hostname"]
SERVER = LOCAL.get("server_ssh") or SERVER_HOSTNAME  # how the laptop reaches it: ssh alias or host
SERVER_DIR = LOCAL["server_dir"]
HAS_SERVER = bool(SERVER_HOSTNAME)
IS_SERVER = HAS_SERVER and socket.gethostname() == SERVER_HOSTNAME
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
