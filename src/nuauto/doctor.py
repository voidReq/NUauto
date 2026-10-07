"""Health check for the laptop and the homelab: `nuauto doctor`.

Read-only: never changes the sheet, NUworks, the homelab or any file. Secret files are only
checked for existence and mode 600, never opened.
On the laptop it checks this machine (health.py's checks), then pipes this same file to python on the homelab
(`--server`), so the homelab checks work even before the code there is up to date (they use only config and sync).
`--json`: one JSON object instead of the text, {"ok": bool, "checks": [{"where", "level", "msg"}, ...]}.
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date

from nuauto import config
from nuauto import sync

UNITS = ["nuauto-daily.timer", "nuauto-weekly.timer", "nuauto-web.service"]
WEB_FILES = ("web.py", "config.py", "sheet.py", "jobs.py")  # nuauto-web needs a restart when these change (= deploy.WEB_CODE)
JSON = "--json" in sys.argv
fails = []
results = []  # every say(), for --json
where = ["laptop"]  # the section being checked


def section(name):
    where[0] = name
    if not JSON:
        print(name)


def say(level, msg):
    results.append({"where": where[0], "level": level, "msg": msg})
    if not JSON:
        print(f"  {level:4}  {msg}")
    if level == "FAIL":
        fails.append(msg)


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60, **kw)


def check_files(names):
    for name in names:
        path = os.path.join(config.LOCAL_DIR, name)
        if not os.path.exists(path):
            say("FAIL", f"local/{name} missing (see docs/DEPLOY.md, SECRETS)")
        elif os.path.isfile(path) and os.stat(path).st_mode & 0o077:
            say("WARN", f"local/{name} is readable by others: chmod 600 local/{name}")


def check_claude():
    """The claude CLI that `nuauto update` runs (same lookup as daily.CLAUDE), and whether it is logged in."""
    if hasattr(config, "tool_path"):
        path = config.tool_path("claude")
    else:  # older code on the homelab (before the next push)
        path = shutil.which("claude", path=os.path.expanduser("~/.local/bin") + os.pathsep + os.environ.get("PATH", ""))
    if not path:
        return say("FAIL", "claude CLI not found (Claude triage/scoring can't run): see README.md, Setup")
    try:
        logged_in = json.loads(sh([path, "auth", "status", "--json"]).stdout).get("loggedIn")
    except (OSError, ValueError, AttributeError, subprocess.TimeoutExpired):
        return say("WARN", "claude CLI found, but `claude auth status` gave no answer")
    if logged_in:
        say("ok", "Claude Code logged in")
    else:
        say("FAIL", "Claude Code is logged out, so scoring can't run: run `claude auth login` on this machine")


def check_google_login():
    try:
        with open(config.GOOGLE_LOGIN_PATH) as f:
            left = config.GOOGLE_LOGIN_DAYS - (date.today() - date.fromisoformat(f.read().strip())).days
    except (OSError, ValueError):
        return say("WARN", "no Google login date (google_login.txt): run `nuauto login google` on the laptop")
    if left <= 0:
        say("FAIL", "Google login expired: run `nuauto login google` on the laptop")
    elif left <= 1:
        say("WARN", f"Google login expires in {left} day: run `nuauto login google` on the laptop")
    else:
        say("ok", f"Google login: {left} days left")


def changed_files(src, dest, relative=False):
    """Files rsync would copy from src (list) to dest: the ones that differ. Dry run (-n)."""
    r = sh(["rsync", "-anci", "-e", " ".join(sync.SSH)] + (["-R"] if relative else []) + src + [dest],
           cwd=config.PROJECT_DIR)
    if r.returncode != 0:
        return None
    return [line.split(" ", 1)[1] for line in r.stdout.splitlines() if line.startswith("<f")]


LEVEL = {"ok": "ok", "warn": "WARN", "fail": "FAIL", "busy": "WARN", "off": "ok", "unknown": "ok"}


def laptop():
    from nuauto import health
    section("laptop")
    for c in health.merge(health.run(["files", "resume", "google", "nuworks_saved"])):
        say(LEVEL[c.status], f"{c.title}: {c.detail}")
    if not os.path.isdir(config.PROFILE_DIR):
        say("FAIL", "local/browser_profile missing: run `nuauto login`")
    check_claude()  # the update runs here in local mode; `nuauto assist` needs it either way
    if not config.HAS_SERVER:
        return say("ok", "no homelab configured (local mode)")
    if shutil.which("systemctl") and \
            sh(["systemctl", "--user", "is-enabled", "nuauto-daily.timer"]).stdout.strip() == "enabled":  # no systemd on macOS
        say("WARN", "the old laptop timer is enabled; the homelab runs the update now: "
                    "systemctl --user disable --now nuauto-daily.timer")

    section("homelab")
    if JSON:
        found = server_results()
        if found is None:
            return say("FAIL", "can't reach the homelab over ssh (Tailscale up?)")
        for r in found:
            say(r["level"], r["msg"])
    else:
        if sh(sync.SSH + [config.SERVER, "true"]).returncode != 0:
            return say("FAIL", "can't reach the homelab over ssh (Tailscale up?)")
        sys.stdout.flush()  # our lines first, then the homelab's
        with open(os.path.abspath(__file__)) as f:  # run this file there, output straight to this terminal
            r = subprocess.run(sync.SSH + [config.SERVER, f"cd {config.SERVER_DIR} && .venv/bin/python - --server"],
                               stdin=f, timeout=120)
        if r.returncode != 0:
            fails.append("homelab")

    section("laptop vs homelab")
    diff = changed_files(sync.CODE, f"{sync.REMOTE}/", relative=True)
    if diff is None:
        say("WARN", "could not compare code with the homelab")
    elif diff:
        say("WARN", f"homelab code differs in {len(diff)} file(s): {', '.join(diff)} "
                    "(the next `nuauto status` pushes them)")
    else:
        say("ok", "homelab code matches the laptop")
    diff = changed_files(["deploy/systemd/"], f"{config.SERVER}:.config/systemd/user/")
    if diff is None:
        say("WARN", "could not compare unit files with the homelab")
    elif diff:
        say("WARN", f"unit files differ from deploy/systemd/: {', '.join(diff)} (install: docs/DEPLOY.md)")
    else:
        say("ok", "homelab unit files match deploy/systemd/")
    check_public_page()


def server_results():
    """The homelab's own checks (this file run there with --server --json): [{"where", "level", "msg"}], or None
    if ssh can't reach it. Used by --json and by the GUI (health.homelab)."""
    if sh(sync.SSH + [config.SERVER, "true"]).returncode != 0:
        return None
    with open(os.path.abspath(__file__)) as f:
        r = subprocess.run(sync.SSH + [config.SERVER, f"cd {config.SERVER_DIR} && .venv/bin/python - --server --json"],
                           stdin=f, capture_output=True, text=True, timeout=120)
    try:
        return json.loads(r.stdout)["checks"]
    except (ValueError, KeyError, TypeError):
        return [{"where": "homelab", "level": "FAIL", "msg": f"the homelab's checks gave no result (exit {r.returncode})"}]


def check_public_page():
    """The Mark-done page through the tunnel. "/" is a 404 page with our title when all is up."""
    from nuauto import web
    if not web.BASE_URL:
        return say("ok", "no Mark-done page configured (web_base_url empty)")
    req = urllib.request.Request(web.BASE_URL + "/", headers={"User-Agent": "nuauto-doctor"})
    try:
        urllib.request.urlopen(req, timeout=15).close()
        code, body = 200, ""
    except urllib.error.HTTPError as e:
        code, body = e.code, e.read(4000).decode(errors="replace")
    except OSError as e:
        return say("FAIL", f"{web.BASE_URL} unreachable ({type(e).__name__}): tunnel down?")
    if code == 404 and "NUauto" in body:
        say("ok", f"{web.BASE_URL} answers (tunnel -> homelab web)")
    else:
        say("FAIL", f"{web.BASE_URL} gave HTTP {code}: nuauto-web down on the homelab, or the tunnel")


def server():
    if not config.IS_SERVER:
        say("FAIL", f"hostname is not {config.SERVER_HOSTNAME}: config.SERVER_HOSTNAME out of date?")
    check_files(["token.json", "client_secret.json", "session_cookies.json", "browser_profile",
                 "discord_webhook.txt", "web_secret.txt"])
    if not os.path.exists(config.RESUME_PATH):
        say("FAIL", "local/resume.pdf missing (the laptop pushes it: `nuauto status`)")
    check_google_login()
    for unit in UNITS + (["nuauto-deploy.timer"] if getattr(config, "DEPLOY_FROM_GIT", False) else []):
        enabled = sh(["systemctl", "--user", "is-enabled", unit]).stdout.strip()
        active = sh(["systemctl", "--user", "is-active", unit]).stdout.strip()
        if enabled == "enabled" and active == "active":
            say("ok", f"{unit} enabled and active")
        else:
            say("FAIL", f"{unit} is {enabled}/{active}: systemctl --user enable --now {unit}")
    result = sh(["systemctl", "--user", "show", "nuauto-daily.service", "-p", "Result", "--value"]).stdout.strip()
    if result != "success":
        say("FAIL", f"last update run ended with {result!r}: journalctl --user -u nuauto-daily -n 50")
    runs = sorted(os.path.join(config.LOGS_DIR, n) for n in os.listdir(config.LOGS_DIR) if n.startswith("daily-")) \
        if os.path.isdir(config.LOGS_DIR) else []
    hours = (time.time() - os.path.getmtime(runs[-1])) / 3600 if runs else None
    if hours is None or hours > 15:  # runs are 10 and 14 hours apart, plus up to 15 min random delay
        say("WARN", "no update run in the last 15 hours" if runs else "no update run logged yet")
    elif result == "success":
        say("ok", f"last update run {hours:.0f}h ago, succeeded")
    check_web_fresh()
    if getattr(config, "DEPLOY_FROM_GIT", False):  # (getattr: this file may run against older code there)
        check_deploy()
    if sh(["loginctl", "show-user", os.environ.get("USER", ""), "-p", "Linger", "--value"]).stdout.strip() != "yes":
        say("FAIL", "lingering is off, so timers stop when you log out: sudo loginctl enable-linger $USER")
    check_claude()
    free_gb = shutil.disk_usage(config.PROJECT_DIR).free / 1e9
    if free_gb < 2:
        say("WARN", f"only {free_gb:.1f} GB free on the homelab")


def check_web_fresh():
    """nuauto-web keeps the code it started with; warn if web.py or a module it uses changed since.
    ctime, not mtime: rsync keeps the laptop's mtime, but ctime is when the file landed here."""
    pid = sh(["systemctl", "--user", "show", "nuauto-web.service", "-p", "MainPID", "--value"]).stdout.strip()
    up = sh(["ps", "-o", "etimes=", "-p", pid]).stdout.strip() if pid not in ("", "0") else ""
    if not up.isdigit():
        return
    started = time.time() - int(up)
    newer = [n for n in WEB_FILES
             if os.path.getctime(os.path.join(config.SRC_DIR, n)) > started]
    if newer:
        say("WARN", f"{', '.join(newer)} changed after nuauto-web started: systemctl --user restart nuauto-web")


def check_deploy():
    """deploy.py's record (local/deployed.json) against GitHub's main. Uses only config, like the rest of --server."""
    try:
        with open(os.path.join(config.LOCAL_DIR, "deployed.json")) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    if state.get("failed"):
        say("FAIL", f"deploying {state['failed'][:7]} failed ({state.get('error', '?')[:120]}); still on "
                    f"{str(state.get('sha') or 'older code')[:7]}: logs/deploy.log")
    try:
        r = subprocess.run(["git", "ls-remote", config.DEPLOY_REPO, "refs/heads/main"], capture_output=True, text=True,
                           timeout=30, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        main_sha = r.stdout.split()[0] if r.returncode == 0 and r.stdout.strip() else None
    except (OSError, subprocess.SubprocessError):
        main_sha = None
    if main_sha is None:
        say("WARN", "can't ask GitHub which commit main is at")
    elif state.get("sha") == main_sha:
        say("ok", f"deployed main {main_sha[:7]} ({state.get('time', '?')})")
    elif not state.get("failed"):
        say("WARN", f"main is at {main_sha[:7]} but the homelab runs {str(state.get('sha') or 'something older')[:7]} "
                    "(the timer deploys within 5 minutes; now: nuauto deploy)")


def main():
    piped = "--server" in sys.argv  # run by the laptop's doctor, which prints the header and summary
    if config.IS_SERVER:
        where[0] = "homelab"
        if not piped and not JSON:
            print("homelab")
        server()
    else:
        laptop()
    if JSON:
        print(json.dumps({"ok": not fails, "checks": results}))
    elif not piped:
        print("All good." if not fails else "Problems found: see the FAIL lines above.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
