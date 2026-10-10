"""Laptop <-> homelab sync over Tailscale (ssh + rsync). Used by the `nuauto` command on the laptop.

The homelab owns the job data (it runs the update); the laptop owns your ratings.
  pull()          homelab data/ -> laptop (everything except ratings.json)
  push()          laptop -> homelab: ratings.json, code, local_config.json, resume, Google token (only if newer).
                  Not the code when "deploy_from_git" is on: the homelab then deploys main from GitHub itself (deploy.py)
  deploy_now()    start that deploy on the homelab now
  push_session()  laptop -> homelab: NUworks browser profile + session cookies (after `nuauto login`)
Secrets are copied file-to-file with rsync and never printed.
"""
import os
import shlex
import subprocess

from nuauto import config

SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]
RSYNC = ["rsync", "-a", "-e", " ".join(SSH)]
REMOTE = f"{config.SERVER}:{config.SERVER_DIR}"
# Pushed with rsync -R, so paths keep their folders (src/nuauto/, prompts/, docs/...). The docs go too, so agents
# there read current ones. The homelab installs the package once (uv pip install -e .); code changes need no reinstall.
PACKAGE = ["__init__.py", "__main__.py", "cli.py", "config.py", "sheet.py", "jobs.py", "fields.py", "daily.py", "browser.py",
           "apply.py", "answers.py", "inspect_form.py", "setup_sheet.py", "sync.py", "web.py", "doctor.py", "assist.py",
           "health.py", "demo.py", "gui.py", "onboard.py", "window.py", "window_gtk.py", "selftest.py", "deploy.py", "insights.py",
           "manage.py"]
CODE = (["pyproject.toml", "README.md", "local_config.example.json", "CLAUDE.md", "CONTRIBUTING.md",
         "docs/DEPLOY.md", "docs/PIPELINE.md", "docs/ARCHITECTURE.md", "docs/SAFETY.md", "docs/GUI.md"]
        + [f"src/nuauto/{f}" for f in PACKAGE]
        + [f"prompts/{f}" for f in ("TRIAGE_PROMPT.md", "SCORE_PROMPT.md", "CATEGORY_PROMPT.md", "ASSIST_PROMPT.md")])


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


# Only rsync options that macOS's old built-in rsync (2.6.9) also has: no --mkpath, no --chmod. Folders are
# made with mkdir over ssh, and local/ is locked down (mode 700/600) with chmod afterwards.
def remote(command, what):
    return _run(SSH + [config.SERVER, command], what)


def remote_dirs(*dirs):
    return remote("mkdir -p " + " ".join(shlex.quote(f"{config.SERVER_DIR}/{d}") for d in dirs), "make homelab folders")


def lock_remote_local():
    return remote(f"chmod -R go-rwx {shlex.quote(config.SERVER_DIR + '/local')}", "lock down homelab local/")


def push(code=None):
    """code: send the laptop's code too (default: yes, unless the homelab deploys main from GitHub)."""
    if code is None:
        code = not config.DEPLOY_FROM_GIT
    ok = remote_dirs("local", "data", "docs")
    if os.path.exists(config.LOCAL_CONFIG_PATH):  # before the code, which reads it
        ok &= _run(RSYNC + ["-c", "local/local_config.json", f"{REMOTE}/local/local_config.json"], "push local config")
    if code:
        ok &= _run(RSYNC + ["-c", "-R"] + CODE + [f"{REMOTE}/"], "push code")
    if os.path.exists("docs/STATUS.md"):  # local log (gitignored); agents on the homelab read it too
        ok &= _run(RSYNC + ["-c", "docs/STATUS.md", f"{REMOTE}/docs/STATUS.md"], "push status")
    ok &= _run(RSYNC + ["data/ratings.json", f"{REMOTE}/data/ratings.json"], "push ratings")
    if os.path.exists(config.LAPTOP_RESUME):
        ok &= _run(RSYNC + ["-c", config.LAPTOP_RESUME, f"{REMOTE}/local/resume.pdf"], "push resume")
    if os.path.exists(config.TOKEN_PATH):
        ok &= _run(RSYNC + ["-u", "local/token.json", f"{REMOTE}/local/token.json"], "push Google token")
    if os.path.exists(config.GOOGLE_LOGIN_PATH):
        ok &= _run(RSYNC + ["-u", "local/google_login.txt", f"{REMOTE}/local/google_login.txt"], "push Google login date")
    ok &= lock_remote_local()
    return ok


def deploy_now():
    """Run the homelab's deploy now (the timer does it every 5 minutes) and show the end of its log."""
    return ssh("systemctl --user start nuauto-deploy.service; "
               f"tail -n 6 {shlex.quote(config.SERVER_DIR + '/logs/deploy.log')}")


def server_busy():
    """True if the homelab is running its update (the browser profile is in use there)."""
    return subprocess.run(SSH + [config.SERVER, "pgrep -f 'nuauto daily'"], capture_output=True).returncode == 0


def push_session():
    if server_busy():
        print("Homelab is running its update right now; run `nuauto login` again in a few minutes to copy the session.")
        return False
    ok = remote_dirs("local/browser_profile")
    ok &= _run(RSYNC + ["--delete", "local/browser_profile/", f"{REMOTE}/local/browser_profile/"], "push browser profile")
    ok &= _run(RSYNC + ["local/session_cookies.json", f"{REMOTE}/local/session_cookies.json"], "push session cookies")
    ok &= lock_remote_local()
    if ok:
        print("NUworks session copied to the homelab.")
    return ok
