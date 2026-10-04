"""Laptop <-> homelab sync over Tailscale (ssh + rsync). Used by the `nuauto` command on the laptop.

The homelab owns the job data (it runs the update); the laptop owns your ratings.
  pull()          homelab data/ -> laptop (everything except ratings.json)
  push()          laptop -> homelab: ratings.json, code, local_config.json, resume, Google token (only if newer)
  push_session()  laptop -> homelab: NUworks browser profile + session cookies (after `nuauto login`)
Secrets are copied file-to-file with rsync and never printed.
"""
import os
import subprocess

import config

SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]
RSYNC = ["rsync", "-a", "-e", " ".join(SSH)]
REMOTE = f"{config.SERVER}:{config.SERVER_DIR}"
# Pushed with rsync -R, so paths keep their folders (docs/...). The docs go too, so agents there read current ones.
CODE = ["config.py", "sheet.py", "jobs.py", "daily.py", "browser.py", "apply.py", "answers.py", "inspect_form.py",
        "sync.py", "web.py", "doctor.py", "nuauto", "TRIAGE_PROMPT.md", "SCORE_PROMPT.md", "CATEGORY_PROMPT.md",
        "requirements.txt", "local_config.example.json", "CLAUDE.md", "docs/DEPLOY.md", "docs/PIPELINE.md", "docs/STATUS.md"]


def _run(cmd, what):
    try:
        r = subprocess.run(cmd, cwd=config.PROJECT_DIR, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        print(f"(sync: {what} timed out; using local data)")
        return False
    if r.returncode != 0:
        print(f"(sync: {what} failed, homelab unreachable? using local data)")
        return False
    return True


def ssh(command, timeout=None):
    """Run a command on the homelab, output straight to this terminal. Returns the exit code."""
    return subprocess.run(SSH + [config.SERVER, command], timeout=timeout).returncode


def pull():
    return _run(RSYNC + ["--exclude", "ratings.json", f"{REMOTE}/data/", "data/"], "pull")


def push():
    ok = True
    if os.path.exists(config.LOCAL_CONFIG_PATH):  # before the code, which reads it
        ok &= _run(RSYNC + ["-c", "local_config.json", f"{REMOTE}/local_config.json"], "push local config")
    ok &= _run(RSYNC + ["-c", "-R"] + CODE + [f"{REMOTE}/"], "push code")
    ok &= _run(RSYNC + ["data/ratings.json", f"{REMOTE}/data/ratings.json"], "push ratings")
    if os.path.exists(config.LAPTOP_RESUME):
        ok &= _run(RSYNC + ["-c", config.LAPTOP_RESUME, f"{REMOTE}/resume.pdf"], "push resume")
    if os.path.exists(config.TOKEN_PATH):
        ok &= _run(RSYNC + ["-u", "--chmod=F600", "token.json", f"{REMOTE}/token.json"], "push Google token")
    if os.path.exists(config.GOOGLE_LOGIN_PATH):
        ok &= _run(RSYNC + ["-u", "google_login.txt", f"{REMOTE}/google_login.txt"], "push Google login date")
    return ok


def server_busy():
    """True if the homelab is running its update (the browser profile is in use there)."""
    return subprocess.run(SSH + [config.SERVER, "pgrep -f daily.py"], capture_output=True).returncode == 0


def push_session():
    if server_busy():
        print("Homelab is running its update right now; run `nuauto login` again in a few minutes to copy the session.")
        return False
    ok = _run(RSYNC + ["--delete", "--chmod=D700,F600", "browser_profile/", f"{REMOTE}/browser_profile/"], "push browser profile")
    ok &= _run(RSYNC + ["--chmod=F600", "session_cookies.json", f"{REMOTE}/session_cookies.json"], "push session cookies")
    if ok:
        print("NUworks session copied to the homelab.")
    return ok
