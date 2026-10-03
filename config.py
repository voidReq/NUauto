import glob
import os
import socket
import sys

SHEET_ID = "YOUR_GOOGLE_SHEET_ID"
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(PROJECT_DIR, "token.json")
# Date of the last Google login (the app is in Testing mode, so a login lasts 7 days). Not secret.
GOOGLE_LOGIN_PATH = os.path.join(PROJECT_DIR, "google_login.txt")
GOOGLE_LOGIN_DAYS = 7

NUWORKS_START_URL = "https://northeastern-csm.symplicity.com/students/app/jobs/discover"
# Domain lock: automated browsing may only navigate to these hosts. The SSO login
# host is deliberately NOT here; log in by hand with `browser.py login`.
ALLOWED_HOSTS = {"northeastern-csm.symplicity.com"}
# Job pool (jobs.py)
# Laptop: the resume in ~/Documents. Homelab: a copy the laptop pushes to resume.pdf in the project dir.
LAPTOP_RESUME = os.path.expanduser("~/Documents/resume.pdf")
RESUME_PATH = LAPTOP_RESUME if os.path.exists(LAPTOP_RESUME) else os.path.join(PROJECT_DIR, "resume.pdf")
DATA_DIR = os.path.join(PROJECT_DIR, "data")   # list, triage, details, scores, ratings, pool
WORK_DIR = os.path.join(PROJECT_DIR, "work")   # batch files exchanged with the Claude subagents
ANSWERS_PATH = os.path.join(PROJECT_DIR, "answers.json")
PROFILE_PATH = os.path.join(PROJECT_DIR, "profile.json")
# Extra hosts allowed ONLY while apply.py clicks the one-click re-login (see browser.relogin).
SSO_HOSTS = {"shibboleth-northeastern-csm.symplicity.com", "neuidmsso.neu.edu"}
COOKIES_PATH = os.path.join(PROJECT_DIR, "session_cookies.json")
PROFILE_DIR = os.path.join(PROJECT_DIR, "browser_profile")
LOGS_DIR = os.path.join(PROJECT_DIR, "logs")

# Homelab server: runs the twice-daily update and the Discord reminders. The laptop syncs with it over
# Tailscale (sync.py): it pulls job data and pushes ratings, the NUworks session and the resume.
SERVER = "homelab"                                  # ssh alias in ~/.ssh/config
SERVER_HOSTNAME = "homelab-hostname"
SERVER_DIR = "/home/you/projects/auto"
IS_SERVER = socket.gethostname() == SERVER_HOSTNAME
DISCORD_WEBHOOK_PATH = os.path.join(PROJECT_DIR, "discord_webhook.txt")  # secret, mode 600, never print

READ_SCOPES =["https://www.googleapis.com/auth/spreadsheets.readonly"]
WRITE_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def find_client_json():
    """client_secret.json in the project dir (both machines since 2026-10-03), else the one in ~/Downloads."""
    local = os.path.join(PROJECT_DIR, "client_secret.json")
    if os.path.exists(local):
        return local
    matches = glob.glob(os.path.expanduser("~/Downloads/client_secret_*.json"))
    if len(matches) != 1:
        sys.exit(f"Expected client_secret.json in the project dir or exactly 1 ~/Downloads/client_secret_*.json, found {len(matches)}.")
    return matches[0]


def lock_token():
    if os.path.exists(TOKEN_PATH):
        os.chmod(TOKEN_PATH, 0o600)
