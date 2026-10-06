"""The setup wizard's backend (the GUI's Setup screen): what each step needs, whether it is done, what its buttons do.

Each step checks itself: it is done when the thing works (the Google client file is a Desktop-app client, the sheet
opens with the right columns, the resume PDF has text Claude can read...). Nothing here types, stores or asks for
a password; logins happen in Google's and NUworks' own pages.

  python -m nuauto.onboard resume-labels   read the Resume dropdown's options from a real NUworks Apply popup
  python -m nuauto.onboard terms           read NUworks' list of co-op terms (for the term preference)
  python -m nuauto.onboard launcher        add NUauto to your apps menu / ~/Applications (install.sh runs it)
Both are read-only (hidden browser, domain lock on, nothing filled or submitted) and save what they read in
local/onboard.json for the wizard.

Never imports daily.py: it sets AUTO_HEADLESS for its whole process, and the GUI's runs must keep a visible browser.
"""
import glob
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from urllib.parse import urlparse

from nuauto import config
from nuauto import health
from nuauto import jobs

ONBOARD_PATH = os.path.join(config.LOCAL_DIR, "onboard.json")  # what the read-only NUworks reads found
SHEET_ID = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]{10,})")
BARE_ID = re.compile(r"^[A-Za-z0-9_-]{10,}$")
LINKS = {  # Google Cloud console pages for the client steps (they open in your browser)
    "project": "https://console.cloud.google.com/projectcreate",
    "sheets_api": "https://console.cloud.google.com/apis/library/sheets.googleapis.com",
    "consent": "https://console.cloud.google.com/auth/overview",
    "audience": "https://console.cloud.google.com/auth/audience",
    "client": "https://console.cloud.google.com/auth/clients/create",
}


class Refused(Exception):
    """A setup action that can't be done as asked; the message says why (the page shows it)."""


# ---------------------------------------------------------------- local files

def read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, data, mode=0o600):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def local_config():
    cfg = read_json(config.LOCAL_CONFIG_PATH, None)
    if cfg is None:  # first start: the template's keys, empty
        cfg = {k: ([] if isinstance(v, list) else {} if isinstance(v, dict) else "") for k, v in
               read_json(os.path.join(config.PROJECT_DIR, "local_config.example.json"), {}).items()}
    return cfg


def save_config(updates):
    """Merge into local/local_config.json (not secret: mode 644, like before) and into this process's config."""
    cfg = {**local_config(), **updates}
    os.makedirs(config.LOCAL_DIR, mode=0o700, exist_ok=True)
    os.chmod(config.LOCAL_DIR, 0o700)
    write_json(config.LOCAL_CONFIG_PATH, cfg, mode=0o644)
    config.LOCAL = cfg
    config.SHEET_ID = cfg.get("sheet_id", "")
    config.WEEK_START = date.fromisoformat(cfg["week_start"]) if cfg.get("week_start") else None
    config.LAPTOP_RESUME = os.path.expanduser(cfg.get("resume_path") or "")
    config.RESUME_PATH = config.LAPTOP_RESUME if os.path.exists(config.LAPTOP_RESUME) else \
        os.path.join(config.LOCAL_DIR, "resume.pdf")
    if "preferences" in updates:
        jobs.set_prefs(cfg["preferences"])
    return cfg


# ---------------------------------------------------------------- step: Google client

def check_client(data):
    """A Google OAuth client file of type Desktop app, or Refused saying what is wrong."""
    if not isinstance(data, dict):
        raise Refused("That is not a Google OAuth client file (it should be JSON).")
    if "web" in data:
        raise Refused("That is a 'Web application' client. Create one of type 'Desktop app' instead (step 4).")
    inst = data.get("installed")
    if not isinstance(inst, dict):
        raise Refused("That is not a Google OAuth client file (no 'installed' section). Download it from the "
                      "client's page in Google Cloud.")
    if not str(inst.get("client_id", "")).endswith(".apps.googleusercontent.com") or not inst.get("client_secret"):
        raise Refused("The file has no client ID or secret. Download it again from the client's page.")
    return True


def client_status():
    path = os.path.join(config.LOCAL_DIR, "client_secret.json")
    if os.path.exists(path):
        try:
            check_client(read_json(path, None))
            return True, None
        except Refused as e:
            return False, str(e)
    return False, None


def downloads_client():
    found = glob.glob(os.path.join(health.DOWNLOADS, "client_secret_*.json"))
    return max(found, key=os.path.getmtime) if found else None


def save_client(text):
    if len(text or "") > 20000:
        raise Refused("That file is too big to be a Google client file.")
    try:
        data = json.loads(text)
    except ValueError:
        raise Refused("That is not a Google OAuth client file (it should be JSON).")
    check_client(data)
    write_json(os.path.join(config.LOCAL_DIR, "client_secret.json"), data)


# ---------------------------------------------------------------- step: Google login (the token works)

_token_check = {}


def google_login_status():
    """(ok, message). Refreshes the saved token with Google once (cached while the token file is unchanged)."""
    if not os.path.exists(config.TOKEN_PATH):
        return False, "Not logged in yet."
    key = os.path.getmtime(config.TOKEN_PATH)
    if _token_check.get("key") == key and time.time() - _token_check.get("at", 0) < 600:
        return _token_check["result"]
    if config.DEMO:
        from nuauto import demo
        age = demo.google_age()
        result = (age is not None and age < config.GOOGLE_LOGIN_DAYS), "Logged in." if age is not None and \
            age < config.GOOGLE_LOGIN_DAYS else "Google login expired."
    else:
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        try:
            Credentials.from_authorized_user_file(config.TOKEN_PATH).refresh(Request())
            result = True, "Logged in."
        except RefreshError:
            result = False, "Google login expired. Log in again."
        except Exception as e:  # offline, etc.: not a verdict on the login
            return True, f"Logged in (could not reach Google to double-check: {type(e).__name__})."
    _token_check.update(key=key, at=time.time(), result=result)
    return result


# ---------------------------------------------------------------- step: the sheet

def sheet_id_from(text):
    text = (text or "").strip()
    m = SHEET_ID.search(text)
    if m:
        return m.group(1)
    if BARE_ID.match(text):
        return text
    raise Refused("Paste the sheet's address from your browser (it has /spreadsheets/d/... in it), or its ID.")


def open_and_prepare(sheet_id):
    """Open the sheet; an empty one gets set up (columns, Status dropdown, styling). A sheet with other data in it
    is refused: setup never writes over anything."""
    from nuauto import setup_sheet
    from nuauto import sheet
    try:
        sh = sheet.client(interactive=False).open_by_key(sheet_id)
    except sheet.NotLoggedIn as e:
        raise Refused(f"{e} Log in to Google first (the step above).")
    except Exception as e:
        raise Refused(f"Could not open that sheet ({type(e).__name__}). Is it in the Google account you logged in "
                      "with, and is the address right?")
    ws = sh.sheet1
    values = ws.get_all_values()
    if not any(c.strip() for row in values for c in row):
        setup_sheet.setup(sh, ws)
        return "set up"
    try:
        sheet.parse_rows(values)
    except sheet.SheetError:
        raise Refused("That sheet already has other data in it. NUauto never writes over a sheet: use an empty one, "
                      "or let NUauto create one.")
    return "ready"


def sheet_status():
    sid = config.SHEET_ID
    if not sid or sid == health.PLACEHOLDER_SHEET:
        return False, "No sheet yet."
    from nuauto import sheet
    try:
        rows = sheet.read_rows(sheet.open_worksheet(interactive=False))
        return True, f"Connected; {len(rows)} rows."
    except sheet.NotLoggedIn as e:
        return False, str(e)
    except Exception as e:
        return False, f"Can't open it: {type(e).__name__}: {str(e)[:120]}"


# ---------------------------------------------------------------- step: resume

def pick_file():
    """A native file dialog for the resume (osascript on macOS, zenity or kdialog on Linux). Path or None."""
    if config.DEMO:  # no dialog an agent would have to click: the demo resume
        path = os.path.join(config.STATE_DIR, "Demo_Student_Resume.pdf")
        return path if os.path.exists(path) else None
    try:
        if sys.platform == "darwin":
            r = subprocess.run(["osascript", "-e", 'POSIX path of (choose file with prompt "Choose your resume (PDF)" '
                                'of type {"com.adobe.pdf"})'], capture_output=True, text=True, timeout=600)
        elif shutil.which("zenity"):
            r = subprocess.run(["zenity", "--file-selection", "--title=Choose your resume (PDF)",
                                "--file-filter=PDF files | *.pdf *.PDF"], capture_output=True, text=True, timeout=600)
        elif shutil.which("kdialog"):
            r = subprocess.run(["kdialog", "--getopenfilename", os.path.expanduser("~"), "*.pdf *.PDF|PDF files"],
                               capture_output=True, text=True, timeout=600)
        else:
            return None
    except (OSError, subprocess.TimeoutExpired):
        return None
    path = r.stdout.strip()
    return path if r.returncode == 0 and path else None


def has_file_dialog():
    return config.DEMO or sys.platform == "darwin" or bool(shutil.which("zenity") or shutil.which("kdialog"))


def resume_suggestions():
    """PDFs called resume / CV in the usual folders, newest first."""
    found = []
    for folder in ("~/Documents", "~/Downloads", "~/Desktop", "~"):
        for pattern in ("*.pdf", "*/*.pdf"):
            found += glob.glob(os.path.join(os.path.expanduser(folder), pattern))
    if config.DEMO:
        found = glob.glob(os.path.join(config.STATE_DIR, "*.pdf"))
    named = {p for p in found if re.search(r"resume|résumé|\bcv\b|cv[_ .-]", os.path.basename(p), re.IGNORECASE)}
    return sorted(named, key=os.path.getmtime, reverse=True)[:8]


def resume_preview(path):
    """(ok, text or a reason). The first page's text, as Claude will see it."""
    path = os.path.expanduser(path or "")
    if not os.path.isfile(path):
        return False, "No file there."
    with open(path, "rb") as f:
        if f.read(5) != b"%PDF-":
            return False, "That is not a PDF."
    try:
        import pypdf
        text = " ".join((pypdf.PdfReader(path).pages[0].extract_text() or "").split())
    except Exception as e:
        return False, f"Could not read the PDF ({type(e).__name__})."
    if len(text) < 40:
        return False, "This PDF has (almost) no text to read: a scanned image? Export it from your editor as PDF."
    return True, text[:500]


# ---------------------------------------------------------------- step: preferences

def check_prefs(p):
    """The preferences form, validated into the shape jobs.PREFS uses."""
    if not isinstance(p, dict):
        raise Refused("Expected the preferences form.")
    out = {}

    def num(key, lo, hi):
        try:
            v = int(p[key])
        except (KeyError, TypeError, ValueError):
            raise Refused(f"{key.replace('_', ' ')}: a whole number, please.")
        if not lo <= v <= hi:
            raise Refused(f"{key.replace('_', ' ')}: between {lo} and {hi}.")
        return v

    def text(key, limit=1500):
        v = str(p.get(key, "")).strip()
        if not v or len(v) > limit or "{{" in v:
            raise Refused(f"{key.replace('_', ' ')}: fill it in (up to {limit} characters).")
        return v

    out["term"] = text("term", 60)
    out["term_id"] = str(p.get("term_id", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9]{6,64}", out["term_id"]):
        raise Refused("Term ID: read it from NUworks (the button), or paste the ID.")
    if p.get("class_year") not in jobs.YEARS:
        raise Refused(f"Your year: one of {', '.join(jobs.YEARS)}.")
    out["class_year"] = p["class_year"]
    out["threshold"] = num("threshold", 0, 100)
    out["threshold_above"] = num("threshold_above", 0, 100)
    if out["threshold_above"] < out["threshold"]:
        raise Refused("The bar for jobs above your year can't be lower than the usual one.")
    out["grad_year"] = num("grad_year", 2020, 2045)
    words = p.get("major_words") or []
    if isinstance(words, str):
        words = words.split(",")
    out["major_words"] = [w.strip().lower() for w in words if str(w).strip()]
    state = str(p.get("home_state", "")).strip().upper()
    if state and not re.fullmatch(r"[A-Z]{2}", state):
        raise Refused("Home state: two letters (MA), or empty.")
    out["home_state"], out["home_label"] = state, (str(p.get("home_label", "")).strip() or state)[:30]
    out["home_bonus"] = num("home_bonus", 0, 50)
    for key, lo, hi in (("category_bonus", -50, 50), ("category_threshold", 0, 100)):
        given = p.get(key) or {}
        if not isinstance(given, dict) or any(c not in jobs.CATEGORIES for c in given):
            raise Refused(f"{key.replace('_', ' ')}: unknown category.")
        out[key] = {}
        for c, v in given.items():
            if str(v).strip() in ("", "None"):
                continue
            try:
                v = int(v)
            except ValueError:
                raise Refused(f"{c}: a whole number, please.")
            if not lo <= v <= hi:
                raise Refused(f"{c}: between {lo} and {hi}.")
            out[key][c] = v
    tags = p.get("tags") or []
    if not isinstance(tags, list) or len(tags) > 20 or not all(isinstance(t, dict) for t in tags):
        raise Refused("Tags: up to 20.")
    out["tags"] = []
    for t in tags:
        name = str(t.get("name", "")).strip()
        phrases = t.get("phrases") or []
        if isinstance(phrases, str):
            phrases = phrases.split(",")
        phrases = [str(x).strip() for x in phrases if str(x).strip()]
        if not phrases and not name:
            continue  # an empty row
        if not name or len(name) > 30:
            raise Refused("Tags: each needs a name (up to 30 characters).")
        if name in (x["name"] for x in out["tags"]):
            raise Refused(f"Tags: {name} is there twice.")
        if not phrases or len(phrases) > 50 or any(len(x) > 60 for x in phrases):
            raise Refused(f"{name}: one or more phrases, comma-separated (up to 60 characters each).")
        try:
            bonus = int(t.get("bonus"))
        except (TypeError, ValueError):
            raise Refused(f"{name}: a whole number of points, please.")
        if not -50 <= bonus <= 50:
            raise Refused(f"{name}: between -50 and 50 points.")
        out["tags"].append({"name": name, "bonus": bonus, "phrases": phrases})
    last = p.get("rank_last") or []
    if not isinstance(last, list) or any(c not in jobs.CATEGORIES for c in last):
        raise Refused("Rank last: unknown category.")
    out["rank_last"] = last
    for key in jobs.PROMPT_FIELDS:
        out[key] = text(key)
    return out


# ---------------------------------------------------------------- step: notifications (Discord)

def save_webhook(url):
    url = (url or "").strip()
    if not health.WEBHOOK.match(url):
        raise Refused("That is not a Discord webhook URL (it starts https://discord.com/api/webhooks/).")
    if not config.DEMO:
        try:
            urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "nuauto-setup"}), timeout=15).close()
        except urllib.error.HTTPError as e:
            raise Refused(f"Discord says this webhook does not work (HTTP {e.code}).")
        except OSError:
            raise Refused("Could not reach Discord to check the webhook. Try again when you are online.")
    fd = os.open(config.DISCORD_WEBHOOK_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(url + "\n")


def test_webhook():
    """Posts one test message (the button says so)."""
    if config.DEMO:
        return "Demo: a test message would go to your Discord channel."
    with open(config.DISCORD_WEBHOOK_PATH) as f:
        url = f.read().strip()
    req = urllib.request.Request(url, data=json.dumps({"content": "**NUauto**: test message. Notifications work."}).encode(),
                                 method="POST", headers={"Content-Type": "application/json", "User-Agent": "nuauto-helper"})
    try:
        urllib.request.urlopen(req, timeout=20).close()
    except OSError as e:
        raise Refused(f"Discord did not take the message ({type(e).__name__}).")
    return "Sent: check your Discord channel."


# ---------------------------------------------------------------- step: automatic updates (local mode)

LABEL = "com.nuauto.daily"
SYSTEMD_DIR = os.path.expanduser("~/.config/systemd/user")
LAUNCH_AGENTS = os.path.expanduser("~/Library/LaunchAgents")


def nuauto_bin():
    return config.self_exe()


def scheduler_kind():
    if config.HAS_SERVER:
        return None
    if sys.platform == "darwin":
        return "launchd"
    return "systemd" if shutil.which("systemctl") else None


def scheduler_paths():
    if config.DEMO:
        base = os.path.join(config.STATE_DIR, "scheduler")
        return [os.path.join(base, "nuauto-daily.timer"), os.path.join(base, "nuauto-daily.service")]
    if scheduler_kind() == "launchd":
        return [os.path.join(LAUNCH_AGENTS, f"{LABEL}.plist")]
    return [os.path.join(SYSTEMD_DIR, "nuauto-daily.timer"), os.path.join(SYSTEMD_DIR, "nuauto-daily.service")]


def scheduler_enabled():
    if config.DEMO or scheduler_kind() == "launchd":
        return os.path.exists(scheduler_paths()[0])
    if scheduler_kind() == "systemd":
        r = subprocess.run(["systemctl", "--user", "is-enabled", "nuauto-daily.timer"], capture_output=True, text=True)
        return r.stdout.strip() == "enabled"
    return False


def tool_path_env():
    """The PATH for the scheduled run: where nuauto, claude and node live (a scheduler's own PATH is short)."""
    dirs = [os.path.dirname(sys.executable)] + [os.path.dirname(p) for p in (config.tool_path("claude"),
                                                                             config.tool_path("npx")) if p]
    dirs += [os.path.expanduser("~/.local/bin"), "/usr/local/bin", "/usr/bin", "/bin"]
    return os.pathsep.join(dict.fromkeys(dirs))


SERVICE = """[Unit]
Description=NUauto: check NUworks for new jobs (written by NUauto's setup)

[Service]
Type=oneshot
WorkingDirectory={dir}
Environment=PATH={path}
ExecStart={nuauto} daily
TimeoutStartSec=2h
Nice=10
"""
TIMER = """[Unit]
Description=NUauto: check NUworks for new jobs at 08:00 and 18:00 (written by NUauto's setup)

[Timer]
OnCalendar=*-*-* 08:00:00
OnCalendar=*-*-* 18:00:00
RandomizedDelaySec=15m
Persistent=true

[Install]
WantedBy=timers.target
"""


def scheduler_enable():
    kind = scheduler_kind()
    if kind is None:
        raise Refused("Automatic updates need systemd (Linux) or launchd (macOS); with a homelab, it runs them.")
    paths = scheduler_paths()
    os.makedirs(os.path.dirname(paths[0]), exist_ok=True)
    if kind == "launchd" and not config.DEMO:
        plist = {"Label": LABEL, "ProgramArguments": [nuauto_bin(), "daily"], "WorkingDirectory": config.STATE_DIR,
                 "StartCalendarInterval": [{"Hour": 8, "Minute": 0}, {"Hour": 18, "Minute": 0}],
                 "EnvironmentVariables": {"PATH": tool_path_env()},
                 "StandardOutPath": os.path.join(config.LOGS_DIR, "launchd-daily.log"),
                 "StandardErrorPath": os.path.join(config.LOGS_DIR, "launchd-daily.log")}
        with open(paths[0], "wb") as f:
            plistlib.dump(plist, f)
        uid = os.getuid()
        r = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", paths[0]], capture_output=True, text=True)
        if r.returncode != 0:
            subprocess.run(["launchctl", "load", "-w", paths[0]], capture_output=True, text=True)
        return
    with open(paths[0], "w") as f:
        f.write(TIMER)
    with open(paths[1], "w") as f:
        f.write(SERVICE.format(dir=config.STATE_DIR, path=tool_path_env(), nuauto=nuauto_bin()))
    if not config.DEMO:
        r = subprocess.run(["bash", "-c", "systemctl --user daemon-reload && systemctl --user enable --now nuauto-daily.timer"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise Refused(f"systemd said: {(r.stderr or r.stdout).strip()[:200]}")


def scheduler_disable():
    kind = scheduler_kind()
    paths = scheduler_paths()
    if kind == "launchd" and not config.DEMO:
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", paths[0]], capture_output=True)
    elif kind == "systemd" and not config.DEMO:
        subprocess.run(["systemctl", "--user", "disable", "--now", "nuauto-daily.timer"], capture_output=True)
    for p in paths:
        if os.path.exists(p):
            os.remove(p)
    if kind == "systemd" and not config.DEMO:
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)


# ---------------------------------------------------------------- app launcher (apps menu / Applications folder)

def mac_app():
    """The packaged NUauto.app this runs from (macOS), or None."""
    if not (config.FROZEN and sys.platform == "darwin"):
        return None
    path = os.path.abspath(sys.executable)  # .../NUauto.app/Contents/MacOS/NUauto
    return path.split("/Contents/MacOS/")[0] if "/Contents/MacOS/" in path else None


def launcher_paths():
    if config.DEMO:
        return [os.path.join(config.STATE_DIR, "launcher", "nuauto.desktop")]
    if mac_app():
        return [mac_app()]  # the app is its own icon
    if sys.platform == "darwin":
        return [os.path.expanduser("~/Applications/NUauto.app")]
    return [os.path.expanduser("~/.local/share/applications/nuauto.desktop")]


DESKTOP = """[Desktop Entry]
Type=Application
Name=NUauto
Comment=Find, review and apply to NUworks co-ops
Exec="{nuauto}" gui
Icon={icon}
Terminal=false
Categories=Office;Education;
StartupNotify=false
"""


def launcher_create():
    """NUauto in your apps menu (Linux: a .desktop file) or ~/Applications (macOS: a small app that starts
    `nuauto gui` in the background). Everything in your home folder; run again any time."""
    icon_src = os.path.join(config.SRC_DIR, "gui_static", "icon.svg")
    target = launcher_paths()[0]
    if mac_app():
        return target  # NUauto.app: drag it to Applications; nothing to make
    if sys.platform == "darwin" and not config.DEMO:
        macos = os.path.join(target, "Contents", "MacOS")
        os.makedirs(macos, exist_ok=True)
        with open(os.path.join(target, "Contents", "Info.plist"), "wb") as f:
            plistlib.dump({"CFBundleName": "NUauto", "CFBundleDisplayName": "NUauto", "CFBundleIdentifier": "com.nuauto.gui",
                           "CFBundleExecutable": "NUauto", "CFBundlePackageType": "APPL",
                           "CFBundleShortVersionString": __import__("nuauto").__version__}, f)
        script = os.path.join(macos, "NUauto")
        with open(script, "w") as f:  # in the background: the app itself ends at once, the window stays
            f.write(f'#!/bin/sh\nnohup "{nuauto_bin()}" gui >/dev/null 2>&1 &\n')
        os.chmod(script, 0o755)
        return target
    os.makedirs(os.path.dirname(target), exist_ok=True)
    icon = os.path.expanduser("~/.local/share/icons/hicolor/scalable/apps/nuauto.svg") if not config.DEMO else \
        os.path.join(os.path.dirname(target), "nuauto.svg")
    os.makedirs(os.path.dirname(icon), exist_ok=True)
    shutil.copyfile(icon_src, icon)
    with open(target, "w") as f:
        f.write(DESKTOP.format(nuauto=nuauto_bin(), icon=icon))
    if not config.DEMO and shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", os.path.dirname(target)], capture_output=True)
    return target


def launcher_exists():
    return os.path.exists(launcher_paths()[0])


# ---------------------------------------------------------------- the steps, for the page

def chrome_found():
    if sys.platform == "darwin":
        return os.path.exists("/Applications/Google Chrome.app")
    return bool(shutil.which("google-chrome") or shutil.which("google-chrome-stable"))


def steps(checks):
    """checks: the GUI's current health checks by id. Returns the wizard steps with their status
    (done / todo / optional) and what the page needs to show for each."""
    cfg = local_config()
    found = read_json(ONBOARD_PATH, {})
    out = []

    def add(id_, title, done, detail="", optional=False, **data):
        out.append({"id": id_, "title": title, "status": "done" if done else "optional" if optional else "todo",
                    "detail": detail, "data": data})

    add("welcome", "Welcome", bool(cfg.get("tos_ack")), "You read NUworks' terms of use and know the limits.",
        week=11, total=99)
    ff = checks.get("firefox")
    cl = checks.get("claude")
    tools_ok = bool(ff and ff.status == health.OK and cl and cl.status == health.OK)
    add("tools", "Browser and Claude Code", tools_ok, "Playwright's Firefox fills NUworks forms; Claude Code scores jobs.",
        firefox=ff.to_dict() if ff else None, claude=cl.to_dict() if cl else None,
        node=bool(config.tool_path("npx")), chrome=chrome_found())
    ok, problem = client_status()
    dl = downloads_client()
    add("google_client", "Google: your own sign-in client", ok, problem or "A free Google Cloud project, used only "
        "for your sheet.", links=LINKS, downloads=os.path.basename(dl) if dl and not ok else None)
    gok, gmsg = google_login_status() if ok else (False, "Do the step above first.")
    add("google_login", "Google: log in", gok, gmsg, days=config.GOOGLE_LOGIN_DAYS)
    sok, smsg = sheet_status() if gok else (False, "Log in to Google first.")
    sid = cfg.get("sheet_id") if cfg.get("sheet_id") != health.PLACEHOLDER_SHEET else ""
    add("sheet", "Your Google Sheet", sok, smsg, sheet_id=sid,
        url=f"https://docs.google.com/spreadsheets/d/{sid}/edit" if sid and not config.DEMO else None)
    path = os.path.expanduser(cfg.get("resume_path") or "")
    rok, rtext = resume_preview(path) if path else (False, "")
    add("resume", "Your resume", rok, f"{os.path.basename(path)}: Claude can read its text." if rok else
        (rtext or "Choose the PDF you use on NUworks."),
        path=path, preview=rtext if rok else None, suggestions=resume_suggestions() if not rok else [],
        dialog=has_file_dialog())
    nw = checks.get("nuworks")
    label = health.resume_label()
    nok = bool(nw and nw.status == health.OK)
    add("nuworks", "NUworks", nok and bool(label), "Log in, then pick the resume NUauto should choose in the Apply "
        "popup.", session=nw.to_dict() if nw else None, label=label, labels=found.get("labels"),
        labels_error=found.get("labels_error"))
    prefs = cfg.get("preferences")
    add("preferences", "What you are looking for", bool(prefs), "Your term, year and major; Claude's description of you.",
        prefs={**jobs.DEFAULTS, **(prefs or {})}, years=jobs.YEARS, categories=sorted(jobs.CATEGORIES),
        terms=found.get("terms"), terms_error=found.get("terms_error"), using_defaults=not prefs)
    kind = scheduler_kind()
    add("extras", "Notifications, automatic updates, app icon", False, "Optional.", optional=True,
        discord=os.path.exists(config.DISCORD_WEBHOOK_PATH), scheduler=kind, scheduled=scheduler_enabled() if kind else False,
        homelab=config.HAS_SERVER, launcher=launcher_exists(), mac=sys.platform == "darwin", mac_app=bool(mac_app()))
    return out


def complete(step_list):
    return all(s["status"] != "todo" for s in step_list)


# ---------------------------------------------------------------- read-only NUworks reads (own process)

def remember(**data):
    found = read_json(ONBOARD_PATH, {})
    found.update(data)
    write_json(ONBOARD_PATH, found)


def a_job_url():
    """A NUworks job to open the Apply popup of: the best pool job that applies on NUworks itself."""
    pool = jobs.load("pool.json", [])
    plain = [r for r in pool if "apply on company site too?" not in r.get("flags", [])]
    for r in plain + pool:
        return jobs.job_url(r["id"])
    if config.DEMO:
        return jobs.job_url("900101")
    return None


def read_resume_labels():
    """Open one job's Apply popup (hidden browser, domain lock on), read the Resume dropdown, close it. Nothing is
    filled or submitted: the same steps as `nuauto inspect`."""
    os.environ["AUTO_HEADLESS"] = "1"
    from playwright.sync_api import sync_playwright
    from nuauto import apply
    from nuauto import browser
    from nuauto.inspect_form import FIELDS_JS
    url = a_job_url()
    if url is None:
        return remember(labels=None, labels_error="No job to open yet: check for new jobs first (Today), then try again.")
    log = health.MemoryLog()
    labels, error = None, None
    with sync_playwright() as p:
        context = browser.launch(p)
        try:
            blocked = []
            browser.install_domain_lock(context, log, blocked)
            page = context.pages[0] if context.pages else context.new_page()
            if not browser.goto_logged_in(page, context, url, log):
                error = "Not logged in to NUworks: log in first (the button above)."
            else:
                btn = page.get_by_role("button", name=apply.APPLY_NAME)
                if btn.count() != 1:
                    error = "That job has no Apply button; try again after the next update."
                else:
                    btn.click(timeout=10000)
                    browser.pause(page, 2, 3)
                    dialog = page.get_by_role("dialog")
                    found = [f for f in dialog.evaluate(FIELDS_JS) if apply.is_resume(f)] if dialog.count() == 1 else []
                    if len(found) != 1 or blocked:
                        error = "That job's Apply popup has no Resume dropdown (it may send you elsewhere)."
                    else:
                        labels = [o.strip() for o in found[0]["options"] if o.strip() and o.strip() != "Select a resume"]
                    cancel = dialog.get_by_role("button", name="Cancel")
                    if cancel.count() == 1:
                        cancel.click()
        except Exception as e:
            error = f"Could not read NUworks ({type(e).__name__})."
        finally:
            context.close()
    remember(labels=labels, labels_error=error)
    print(json.dumps({"labels": labels, "error": error}))


TERM_LABEL = re.compile(r"^\d{4}\s*-\s*(Spring|Summer|Fall|Winter)\b", re.IGNORECASE)


def find_terms(data):
    """Every {id, label} in NUworks' filter list whose label looks like a term ("2027 - Spring"), wherever it sits
    in the JSON (the shape of that response is not something to rely on)."""
    out = {}

    def walk(x):
        if isinstance(x, dict):
            label = next((x[k] for k in ("_label", "label", "name", "title") if isinstance(x.get(k), str)), None)
            ident = next((x[k] for k in ("_id", "id", "value") if isinstance(x.get(k), (str, int))), None)
            if label and ident is not None and TERM_LABEL.match(label.strip()):
                out[str(ident)] = label.strip()
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(data)
    season = {"spring": 1, "summer": 2, "fall": 3, "winter": 0}

    def key(item):
        m = re.match(r"(\d{4})\s*-\s*(\w+)", item[1])
        return (int(m.group(1)), season.get(m.group(2).lower(), 9)) if m else (0, 0)
    return [{"id": i, "label": label} for i, label in sorted(out.items(), key=key)]


def read_terms():
    """Load NUworks' job search page (hidden browser, domain lock on) and read the filter list it asks for."""
    os.environ["AUTO_HEADLESS"] = "1"
    from playwright.sync_api import sync_playwright
    from nuauto import browser
    got, terms, error = [], None, None
    log = health.MemoryLog()
    with sync_playwright() as p:
        context = browser.launch(p)
        try:
            browser.install_domain_lock(context, log, [])
            page = context.pages[0] if context.pages else context.new_page()

            def on_resp(r):
                if "/api/v2/jobs/filters" in urlparse(r.url).path:
                    try:
                        got.append(r.json())
                    except Exception:
                        pass
            page.on("response", on_resp)
            if not browser.goto_logged_in(page, context, f"{jobs.HOST}/students/app/jobs/search", log):
                error = "Not logged in to NUworks: log in first."
            else:
                for _ in range(40):
                    if got:
                        break
                    page.wait_for_timeout(500)
                terms = find_terms(got) if got else None
                if not terms:
                    error = "Could not find the term list on NUworks. Enter your term and its ID by hand."
        except Exception as e:
            error = f"Could not read NUworks ({type(e).__name__})."
        finally:
            context.close()
    remember(terms=terms, terms_error=error)
    print(json.dumps({"terms": terms, "error": error}))


def main(argv):
    if argv == ["launcher"]:  # install.sh
        print(f"App launcher: {launcher_create()}")
    elif argv == ["resume-labels"]:
        read_resume_labels()
    elif argv == ["terms"]:
        read_terms()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
