"""Health check for the laptop and the homelab: `nuworks doctor`.

Read-only: never changes the sheet, NUworks, the homelab or any file. Secret files are only
checked for existence and mode 600, never opened.
On the laptop it checks this machine, then pipes this same file to python on the homelab
(`--server`), so the homelab checks work even before the code there is up to date.
"""
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date

import config
import sync

UNITS = ["nuworks-daily.timer", "nuworks-weekly.timer", "nuworks-web.service"]
fails = []


def say(level, msg):
    print(f"  {level:4}  {msg}")
    if level == "FAIL":
        fails.append(msg)


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60, **kw)


def check_files(names):
    for name in names:
        path = os.path.join(config.PROJECT_DIR, name)
        if not os.path.exists(path):
            say("FAIL", f"{name} missing (see docs/DEPLOY.md, SECRETS)")
        elif os.path.isfile(path) and os.stat(path).st_mode & 0o077:
            say("WARN", f"{name} is readable by others: chmod 600 {name}")


def check_claude():
    """The claude CLI that `nuworks update` runs (same lookup as daily.CLAUDE)."""
    if not shutil.which("claude", path=os.path.expanduser("~/.local/bin") + os.pathsep + os.environ.get("PATH", "")):
        say("FAIL", "claude CLI not found (Claude triage/scoring can't run): see README.md, Setup")


def check_google_login():
    try:
        with open(config.GOOGLE_LOGIN_PATH) as f:
            left = config.GOOGLE_LOGIN_DAYS - (date.today() - date.fromisoformat(f.read().strip())).days
    except (OSError, ValueError):
        return say("WARN", "no Google login date (google_login.txt): run `nuworks login google` on the laptop")
    if left <= 0:
        say("FAIL", "Google login expired: run `nuworks login google` on the laptop")
    elif left <= 1:
        say("WARN", f"Google login expires in {left} day: run `nuworks login google` on the laptop")
    else:
        say("ok", f"Google login: {left} days left")


def changed_files(src, dest, relative=False):
    """Files rsync would copy from src (list) to dest: the ones that differ. Dry run (-n)."""
    r = sh(["rsync", "-anci", "-e", " ".join(sync.SSH)] + (["-R"] if relative else []) + src + [dest],
           cwd=config.PROJECT_DIR)
    if r.returncode != 0:
        return None
    return [line.split(" ", 1)[1] for line in r.stdout.splitlines() if line.startswith("<f")]


def laptop():
    print("laptop")
    check_files(["token.json", "client_secret.json", "session_cookies.json", "browser_profile", "profile.json"]
                + (["answers.json"] if os.path.exists(config.ANSWERS_PATH) else []))  # created on the first answer
    check_google_login()
    if not os.path.exists(config.LOCAL_CONFIG_PATH):
        say("FAIL", "local_config.json missing: copy local_config.example.json and fill it in")
    if not os.path.exists(config.LAPTOP_RESUME):
        say("FAIL", f"resume missing: {config.LAPTOP_RESUME}")
    if not config.HAS_SERVER:
        check_claude()  # local mode: the update runs here
        return say("ok", "no homelab configured (local mode)")
    if sh(["systemctl", "--user", "is-enabled", "nuworks-daily.timer"]).stdout.strip() == "enabled":
        say("WARN", "the old laptop timer is enabled; the homelab runs the update now: "
                    "systemctl --user disable --now nuworks-daily.timer")

    print("homelab")
    if sh(sync.SSH + [config.SERVER, "true"]).returncode != 0:
        return say("FAIL", "can't reach the homelab over ssh (Tailscale up?)")
    sys.stdout.flush()  # our lines first, then the homelab's
    with open(os.path.abspath(__file__)) as f:  # run this file there, output straight to this terminal
        r = subprocess.run(sync.SSH + [config.SERVER, f"cd {config.SERVER_DIR} && .venv/bin/python - --server"],
                           stdin=f, timeout=120)
    if r.returncode != 0:
        fails.append("homelab")

    print("laptop vs homelab")
    diff = changed_files(sync.CODE, f"{sync.REMOTE}/", relative=True)
    if diff is None:
        say("WARN", "could not compare code with the homelab")
    elif diff:
        say("WARN", f"homelab code differs in {len(diff)} file(s): {', '.join(diff)} "
                    "(the next `nuworks status` pushes them)")
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


def check_public_page():
    """The Mark-done page through the Pi tunnel. "/" is a 404 page with our title when all is up."""
    import web
    req = urllib.request.Request(web.BASE_URL + "/", headers={"User-Agent": "nuworks-doctor"})
    try:
        urllib.request.urlopen(req, timeout=15).close()
        code, body = 200, ""
    except urllib.error.HTTPError as e:
        code, body = e.code, e.read(4000).decode(errors="replace")
    except OSError as e:
        return say("FAIL", f"{web.BASE_URL} unreachable ({type(e).__name__}): Pi tunnel down?")
    if code == 404 and "NUworks helper" in body:
        say("ok", f"{web.BASE_URL} answers (Pi tunnel -> homelab web)")
    else:
        say("FAIL", f"{web.BASE_URL} gave HTTP {code}: nuworks-web down on the homelab, or the Pi tunnel")


def server():
    if not config.IS_SERVER:
        say("FAIL", f"hostname is not {config.SERVER_HOSTNAME}: config.SERVER_HOSTNAME out of date?")
    check_files(["token.json", "client_secret.json", "session_cookies.json", "browser_profile",
                 "discord_webhook.txt", "web_secret.txt"])
    if not os.path.exists(config.RESUME_PATH):
        say("FAIL", "resume.pdf missing (the laptop pushes it: `nuworks status`)")
    check_google_login()
    for unit in UNITS:
        enabled = sh(["systemctl", "--user", "is-enabled", unit]).stdout.strip()
        active = sh(["systemctl", "--user", "is-active", unit]).stdout.strip()
        if enabled == "enabled" and active == "active":
            say("ok", f"{unit} enabled and active")
        else:
            say("FAIL", f"{unit} is {enabled}/{active}: systemctl --user enable --now {unit}")
    result = sh(["systemctl", "--user", "show", "nuworks-daily.service", "-p", "Result", "--value"]).stdout.strip()
    if result != "success":
        say("FAIL", f"last update run ended with {result!r}: journalctl --user -u nuworks-daily -n 50")
    runs = sorted(os.path.join(config.LOGS_DIR, n) for n in os.listdir(config.LOGS_DIR) if n.startswith("daily-")) \
        if os.path.isdir(config.LOGS_DIR) else []
    hours = (time.time() - os.path.getmtime(runs[-1])) / 3600 if runs else None
    if hours is None or hours > 15:  # runs are 10 and 14 hours apart, plus up to 15 min random delay
        say("WARN", "no update run in the last 15 hours" if runs else "no update run logged yet")
    elif result == "success":
        say("ok", f"last update run {hours:.0f}h ago, succeeded")
    check_web_fresh()
    if sh(["loginctl", "show-user", os.environ.get("USER", ""), "-p", "Linger", "--value"]).stdout.strip() != "yes":
        say("FAIL", "lingering is off, so timers stop when you log out: sudo loginctl enable-linger $USER")
    check_claude()
    free_gb = shutil.disk_usage(config.PROJECT_DIR).free / 1e9
    if free_gb < 2:
        say("WARN", f"only {free_gb:.1f} GB free on the homelab")


def check_web_fresh():
    """nuworks-web keeps the code it started with; warn if web.py or a module it uses changed since.
    ctime, not mtime: rsync keeps the laptop's mtime, but ctime is when the file landed here."""
    pid = sh(["systemctl", "--user", "show", "nuworks-web.service", "-p", "MainPID", "--value"]).stdout.strip()
    up = sh(["ps", "-o", "etimes=", "-p", pid]).stdout.strip() if pid not in ("", "0") else ""
    if not up.isdigit():
        return
    started = time.time() - int(up)
    newer = [n for n in ("web.py", "config.py", "sheet.py", "jobs.py")
             if os.path.getctime(os.path.join(config.PROJECT_DIR, n)) > started]
    if newer:
        say("WARN", f"{', '.join(newer)} changed after nuworks-web started: systemctl --user restart nuworks-web")


def main():
    piped = "--server" in sys.argv  # run by the laptop's doctor, which prints the header and summary
    if config.IS_SERVER:
        if not piped:
            print("homelab")
        server()
    else:
        laptop()
    if not piped:
        print("All good." if not fails else "Problems found: see the FAIL lines above.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
