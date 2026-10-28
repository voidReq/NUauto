"""The setup wizard's backend (the GUI's Setup screen): what each step needs, whether it is done, what its buttons do.

Each step checks itself: it is done when the thing works (the Google client file is a Desktop-app client, the sheet
opens with the right columns, the resume PDF has text Claude can read...). Nothing here types, stores or asks for
a password; logins happen in Google's and NUworks' own pages.

  python -m nuauto.onboard resume-labels   read the Resume dropdown's options from a real NUworks Apply popup
  python -m nuauto.onboard terms           read NUworks' list of co-op terms (for the term preference)
  python -m nuauto.onboard launcher        add NUauto to your apps menu / ~/Applications (install.sh runs it)
The first two are read-only (hidden browser, domain lock on, nothing filled or submitted) and save what they read in
local/onboard.json for the wizard.
  python -m nuauto.onboard homelab-check <ssh name> [<folder>]   what that homelab has (read-only, over ssh)
  python -m nuauto.onboard homelab-setup   set the homelab up, step by step, each asked first (Settings > Homelab)
  python -m nuauto.onboard homelab-deploy on|off [<fork address>]   the homelab deploys GitHub's main by itself, or not

Never imports daily.py: it sets AUTO_HEADLESS for its whole process, and the GUI's runs must keep a visible browser.
"""
import glob
import json
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime
from urllib.parse import urlparse

from nuauto import config
from nuauto import fields
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
    os.makedirs(config.LOCAL_DIR, mode=0o700, exist_ok=True)
    os.chmod(config.LOCAL_DIR, 0o700)
    with config.settings_lock():  # (sync merges the homelab's copy into it)
        cfg = {**local_config(), **updates}
        write_json(config.LOCAL_CONFIG_PATH, cfg, mode=0o644)
    config.LOCAL = cfg
    config.SHEET_ID = cfg.get("sheet_id", "")
    config.WEEK_START = date.fromisoformat(cfg["week_start"]) if cfg.get("week_start") else None
    config.set_files(cfg)
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


# ---------------------------------------------------------------- your files: the resume and the transcript
# What you give NUauto (Setup's resume step, Settings > Applying > Your files) is kept as its own copy in local/, mode 600, and
# local_config points at it: replacing the PDF you had elsewhere changes nothing until you give NUauto the new one.
# The laptop then pushes the copies to the homelab at once (gui.files_post -> sync.push_documents).

DOCUMENTS = {"resume": ("resume.pdf", "resume_path"), "transcript": ("transcript.pdf", "transcript_path")}
MAX_DOCUMENT = 10_000_000  # bytes (the GUI reads at most this, base64, from a request: gui.Handler.MAX_UPLOAD)


def save_document(kind, data):
    """Keep this PDF (bytes) as local/resume.pdf or local/transcript.pdf and use it from now on. The resume must have
    text Claude can read (resume_preview); the transcript only has to open as a PDF. Refused: nothing changes."""
    name, key = DOCUMENTS[kind]
    if not data:
        raise Refused("That file is empty.")
    if len(data) > MAX_DOCUMENT:
        raise Refused(f"That file is over {MAX_DOCUMENT // 1_000_000} MB.")
    if data[:5] != b"%PDF-":
        raise Refused("That is not a PDF.")
    os.makedirs(config.LOCAL_DIR, mode=0o700, exist_ok=True)
    dest = os.path.join(config.LOCAL_DIR, name)
    tmp = dest + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    try:
        if kind == "resume":
            ok, text = resume_preview(tmp)
            if not ok:
                raise Refused(text)
        else:
            try:
                import pypdf
                pypdf.PdfReader(tmp).pages[0]
            except Exception as e:
                raise Refused(f"Could not read the PDF ({type(e).__name__}).")
    except Refused:
        os.remove(tmp)
        raise
    os.replace(tmp, dest)
    save_config({key: dest})
    return dest


def remove_document(kind):
    """No transcript any more: NUauto's copy is deleted (the resume can only be replaced)."""
    if kind != "transcript":
        raise Refused("NUauto needs a resume: give it a new one instead.")
    name, key = DOCUMENTS[kind]
    dest = os.path.join(config.LOCAL_DIR, name)
    if os.path.exists(dest):
        os.remove(dest)
    save_config({key: ""})


def document_info(kind):
    """The PDF this machine uses ({name, size, modified}) or None. On the homelab: the copy the laptop pushed."""
    path = config.RESUME_PATH if kind == "resume" else config.TRANSCRIPT_PATH
    if not os.path.isfile(path):
        return None
    st = os.stat(path)
    return {"name": os.path.basename(path), "size": st.st_size,
            "modified": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="minutes")}


# ---------------------------------------------------------------- step: preferences

def check_categories(given):
    """The kinds of work: 2 to 25 of {key, label, description}; keys are lowercase words (letters, digits, _), labels
    and keys unique; "other" is always there (added at the end when missing). A key may be left out: it comes from
    the label."""
    if not isinstance(given, list) or not 1 <= len(given) <= 25 or not all(isinstance(c, dict) for c in given):
        raise Refused("Kinds of work: up to 25 of them.")
    out = []
    for c in given:
        label = " ".join(str(c.get("label", "")).split())
        desc = str(c.get("description", "")).strip()  # as given: the built-in ones keep their line breaks
        key = str(c.get("key") or "").strip() or re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:30]
        if not label and not desc:
            continue  # an empty row
        if not label or len(label) > 40:
            raise Refused("Kinds of work: each needs a name (up to 40 characters).")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,29}", key) or key == "uncategorized":
            raise Refused(f"{label}: a name that starts with a letter, please.")
        if not desc or len(desc) > 600 or "{{" in desc:
            raise Refused(f"{label}: say in a line what counts as this kind of work (up to 600 characters, no {{{{).")
        if key in (x["key"] for x in out) or label.lower() in (x["label"].lower() for x in out):
            raise Refused(f"Kinds of work: {label} is there twice.")
        out.append({"key": key, "label": label, "description": desc})
    if "other" not in (c["key"] for c in out):
        out.append({"key": "other", "label": "Other", "description": "anything else."})
    if len(out) < 2:
        raise Refused("Kinds of work: at least one besides Other.")
    return out


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
    for key in ("featured_bonus", "new_bonus"):  # (preferences saved before 2026-10-28 have neither: the defaults)
        out[key] = num(key, 0, 50) if str(p.get(key, "")).strip() not in ("", "None") else jobs.DEFAULTS[key]
    field = p.get("field") or "engineering"
    if field not in fields.FIELDS:
        raise Refused(f"What you study: one of {', '.join(f['label'] for f in fields.FIELDS.values())}.")
    out["field"] = field
    out["categories"] = check_categories(p.get("categories"))
    keys = [c["key"] for c in out["categories"]]
    for key, lo, hi in (("category_bonus", -50, 50), ("category_threshold", 0, 100)):
        given = p.get(key) or {}
        if not isinstance(given, dict) or any(c not in keys for c in given):
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
    if not isinstance(last, list) or any(c not in keys for c in last):
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

    from nuauto import sheet
    add("welcome", "Welcome", bool(cfg.get("tos_ack")), "You read NUworks' terms of use and know the limits.",
        week=sheet.max_per_week(), total=sheet.MAX_TOTAL)  # the limits the code enforces (Settings > Applying)
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
        file=document_info("resume") if rok else None, preview=rtext if rok else None,
        suggestions=resume_suggestions() if not rok else [])
    nw = checks.get("nuworks")
    label = health.resume_label()
    nok = bool(nw and nw.status == health.OK)
    add("nuworks", "NUworks", nok and bool(label), "Log in, then pick the resume NUauto should choose in the Apply "
        "popup.", session=nw.to_dict() if nw else None, label=label, labels=found.get("labels"),
        labels_error=found.get("labels_error"))
    prefs = cfg.get("preferences")
    add("preferences", "What you are looking for", bool(prefs), "Your term, year and major; Claude's description of you.",
        prefs={**jobs.DEFAULTS, **(prefs or {})}, years=jobs.YEARS,
        fields={k: {"label": f["label"], **fields.preset(k)} for k, f in fields.FIELDS.items()},
        terms=found.get("terms"), terms_error=found.get("terms_error"), using_defaults=not prefs)
    kind = scheduler_kind()
    add("extras", "Notifications, automatic updates, app icon", False, "Optional.", optional=True,
        discord=os.path.exists(config.DISCORD_WEBHOOK_PATH), scheduler=kind, scheduled=scheduler_enabled() if kind else False,
        homelab=config.HAS_SERVER, launcher=launcher_exists(), mac=sys.platform == "darwin", mac_app=bool(mac_app()))
    ii = internships_info()
    add("internships", "Summer internships (optional)", ii["on"], "Optional. Internships from public lists (not on "
        "NUworks), read from the companies' own sites and scored like your co-ops; approved ones go in the sheet's "
        "Other jobs tab, with no weekly limit.", optional=True, **ii)
    pm = password_manager(checks)
    add("password_manager", "Password manager (Bitwarden)", pm["on"] and (pm["check"] or {}).get("status") == health.OK,
        "Optional. Your own job-site logins for the assistant, and the ones it makes put into your vault.",
        optional=True, **pm)
    hl = homelab_link()
    if hl["available"]:  # one step: which homelab, then (once NUauto runs with it) its page and its assistant
        ha = homelab_assistant()
        add("homelab", "A homelab (optional)", hl["on"], "Optional. An always-on computer checks for new jobs and sends "
            "your Discord messages, so this one can be off; NUauto's page and its assistant can run there too, for your "
            "phone. Without one, everything runs here.", optional=True, **hl, assistant=ha if ha["available"] else None)
    return out


def password_manager(checks):
    """Settings' and setup's Password manager part (optional; local_config "password_manager"): no secrets."""
    from nuauto import accounts
    cfg = local_config()
    c = checks.get("bitwarden")
    try:
        with open(accounts.PATH) as f:
            own = sum(1 for a in json.load(f).values() if a.get("own"))
    except (OSError, ValueError):
        own = 0
    return {"on": cfg.get("password_manager") == "bitwarden", "check": c.to_dict() if c else None,
            "waiting": accounts.waiting(), "own": own, "only": cfg.get("bitwarden_import") or ["myworkday"],
            "bw": not config.DEMO and bool(shutil.which("bw") or config.tool_path("bw"))}


def homelab_assistant(here=False):
    """Settings' and setup's NUauto on your homelab part (optional; homelab mode only, docs/REMOTE.md): its page for your
    phone (remote_app) and its assistant (remote_assist). here: asked by the homelab's own page (its queue settings)."""
    from nuauto import remote
    cfg = local_config()
    slots = cfg.get("assist_slots")
    from nuauto import review
    on = bool(cfg.get("remote_assist"))
    app = on or bool(cfg.get("remote_app"))
    url = review.page_url() if app else ""
    return {"available": bool(config.HAS_SERVER and not config.IS_SERVER and not config.FROZEN) or here or config.DEMO_LINK,
            "on": on, "app": app, "auto": bool(remote.auto_tabs(cfg)),
            "auto_tabs": {"nuworks": "jobs" in remote.auto_tabs(cfg), "other": "other" in remote.auto_tabs(cfg)},
            "hours": list(remote.hours()),
            "slots": slots if isinstance(slots, int) and not isinstance(slots, bool) and 1 <= slots <= 3 else 3,
            "review_url": url.rstrip("/") if url.startswith("http") else None}


def homelab_link():
    """Settings' and setup's Homelab part (optional): the homelab this computer works with (local_config.json's server
    fields; docs/DEPLOY.md). Never in the packaged app: it is always local mode. saved: what the file says now; on: what
    this process runs with (a change takes a restart of NUauto)."""
    cfg = local_config()
    return {"available": not config.FROZEN, "on": config.HAS_SERVER, "saved": bool(cfg.get("server_hostname")),
            "ssh": cfg.get("server_ssh") or "", "hostname": cfg.get("server_hostname") or "",
            "dir": cfg.get("server_dir") or "", "listen": cfg.get("web_listen_host") or "",
            "deploy_from_git": bool(cfg.get("deploy_from_git")), "deploy_repo": cfg.get("deploy_repo") or "",
            "web_base_url": cfg.get("web_base_url") or "", "default_dir": HOMELAB_DIR}


HOMELAB_DIR = "projects/auto"  # under the homelab user's home: the units in deploy/systemd/ run from %h/projects/auto
SSH_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,99}$")   # an ~/.ssh/config alias, a host, or user@host
HOMELAB_UNITS = ("nuauto-daily.timer", "nuauto-weekly.timer", "nuauto-intern.timer", "nuauto-web.service",
                 "nuauto-deploy.timer", "nuauto-assist.timer", "nuauto-workday.timer")
# What homelab_probe asks the homelab (read-only): one key=value per line. $1: the project folder (under $HOME).
PROBE = r'''d="$HOME/$1"
echo "hostname=$(hostname)"
echo "tailscale=$(tailscale ip -4 2>/dev/null | head -n 1)"
[ -d "$d" ] && echo dir=1 || echo dir=0
[ -x "$d/.venv/bin/nuauto" ] && echo venv=1 || echo venv=0
if command -v uv >/dev/null 2>&1 || [ -x "$HOME/.local/bin/uv" ]; then echo uv=1; else echo uv=0; fi
if command -v claude >/dev/null 2>&1 || [ -x "$HOME/.local/bin/claude" ]; then echo claude=1; else echo claude=0; fi
echo "linger=$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)"
for u in ''' + " ".join(HOMELAB_UNITS) + r'''; do echo "unit.$u=$(systemctl --user is-enabled "$u" 2>/dev/null)"; done
for t in Xvfb x11vnc websockify tmux npx; do command -v "$t" >/dev/null 2>&1 && echo "tool.$t=1" || echo "tool.$t=0"; done
'''


def homelab_probe(ssh_name, folder=HOMELAB_DIR):
    """What the homelab (`ssh <ssh_name>`, a key, no password: BatchMode) has: its hostname, its Tailscale address, and
    how much of NUauto is set up there. Read-only. {"ok": False, "why"} when it can't be asked."""
    from nuauto import sync
    if not SSH_NAME.match(ssh_name or ""):
        return {"ok": False, "why": "The ssh name is a host, user@host, or an alias from ~/.ssh/config (no spaces)."}
    if not re.fullmatch(r"[A-Za-z0-9._/-]{1,200}", folder or "") or ".." in folder or folder.startswith("/"):
        return {"ok": False, "why": "The folder is a path under the homelab user's home, like projects/auto."}
    try:
        r = subprocess.run(sync.SSH + [ssh_name, "sh", "-s", "--", folder], input=PROBE, capture_output=True,
                           text=True, timeout=40)
    except (OSError, subprocess.TimeoutExpired):
        return {"ok": False, "why": f"ssh {ssh_name} did not answer in time (Tailscale up? the right name?)."}
    if r.returncode != 0 and "hostname=" not in r.stdout:
        return {"ok": False, "why": f"ssh {ssh_name} failed: it needs a key (no password prompt), as in "
                                    "docs/DEPLOY.md. " + (r.stderr.strip().splitlines() or [""])[-1][:200]}
    found = dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
    tools = {k[5:]: v == "1" for k, v in found.items() if k.startswith("tool.")}
    return {"ok": True, "hostname": found.get("hostname", ""), "tailscale": found.get("tailscale", ""),
            "dir": found.get("dir") == "1", "venv": found.get("venv") == "1", "uv": found.get("uv") == "1",
            "claude": found.get("claude") == "1", "linger": found.get("linger") == "yes",
            "units": {k[5:]: v for k, v in found.items() if k.startswith("unit.")},
            "assist_tools_missing": [t for t, ok in tools.items() if not ok], "folder": folder}


def homelab_save(ssh_name, hostname, folder, listen):
    """Use this homelab (or none: hostname ""): local_config.json's server fields. Takes a restart of NUauto."""
    import ipaddress
    if not hostname:
        save_config({"server_hostname": ""})
        return homelab_link()
    if not SSH_NAME.match(ssh_name or "") or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,99}", hostname):
        raise Refused("Check the ssh name and the hostname (Check fills them in).")
    if not re.fullmatch(r"[A-Za-z0-9._/-]{1,200}", folder or "") or ".." in folder or folder.startswith("/"):
        raise Refused("The folder is a path under the homelab user's home, like projects/auto.")
    try:
        ip = ipaddress.ip_address(listen)
    except ValueError:
        raise Refused("The homelab's Tailscale address is an IP like 100.101.102.103 (Check fills it in).")
    if ip.is_unspecified or ip.is_loopback:
        raise Refused("Its pages listen on its Tailscale address only: not 0.0.0.0 or 127.0.0.1.")
    save_config({"server_hostname": hostname, "server_ssh": ssh_name, "server_dir": folder,
                 "web_listen_host": str(ip)})
    return homelab_link()


def _ask(what):
    """y = do it, s = skip it, q = stop the setup here."""
    while True:
        a = input(f"\n{what}\n  Do it? [y]es / [s]kip / [q]uit: ").strip().lower()
        if a in ("y", "s", "q"):
            if a == "q":
                sys.exit("Stopped. Run it again any time: what is done stays done.")
            return a == "y"


REPO_URL = re.compile(r"^https://[A-Za-z0-9.-]+/[A-Za-z0-9._/-]+$")


def homelab_deploy(on, repo=None):
    """`python -m nuauto.onboard homelab-deploy on|off [<repo>]` (Settings > Homelab > Advanced, run as a GUI task): the
    homelab deploys GitHub's main by itself (deploy_from_git; docs/DEPLOY.md, TURN ON DEPLOY FROM GITHUB), or the laptop
    pushes its code again. repo: another fork's address ("" = this project's)."""
    from nuauto import sync
    if config.FROZEN or not config.HAS_SERVER or config.IS_SERVER:
        sys.exit("Deploys are set on your laptop (a source install) with a homelab in use.")
    if repo is not None and repo and not REPO_URL.match(repo):
        sys.exit("The fork's address is an https:// git address, like https://github.com/you/NUauto.git.")
    if on:
        print("Copying this computer's code there once (it carries the deploy tool and its timer)...")
        sync.push(code=True)
        units = [os.path.join(config.PROJECT_DIR, "deploy", "systemd", f) for f in ("nuauto-deploy.service", "nuauto-deploy.timer")]
        ok = sync.remote("mkdir -p .config/systemd/user", "make the units folder") and \
            sync._run(["rsync", "-a", "-e", " ".join(sync.SSH)] + units + [f"{config.SERVER}:.config/systemd/user/"], "copy the deploy timer") and \
            sync.remote("systemctl --user daemon-reload && systemctl --user enable --now nuauto-deploy.timer", "turn the deploy timer on")
        if not ok:
            sys.exit("The homelab's deploy timer could not be turned on (is the homelab reachable?): nothing changed.")
    else:
        sync.remote("systemctl --user disable --now nuauto-deploy.timer", "turn the deploy timer off")
    save_config({"deploy_from_git": bool(on), **({"deploy_repo": repo} if repo is not None else {})})
    config.DEPLOY_FROM_GIT = bool(on) and config.HAS_SERVER
    sync.push(code=not on)  # the setting itself (and, off, this computer's code again)
    print("The homelab deploys GitHub's main by itself now (every 5 minutes)." if on else
          "The homelab gets its code from this computer again (each push).")


def internships_info():
    """Settings' and setup's Summer internships part (optional; local_config "internships"; docs/PIPELINE.md)."""
    from nuauto import intern
    raw = local_config().get("internships")
    cfg = raw if isinstance(raw, dict) else {}
    lists = {**intern.DEFAULTS["lists"], **(cfg["lists"] if isinstance(cfg.get("lists"), dict) else {})}
    return {"on": isinstance(raw, dict) or raw is True, "term": cfg.get("term") or intern.DEFAULTS["term"],
            "threshold": cfg.get("threshold"), "lists": lists, "names": dict(intern.NAMES),
            "default_lists": dict(intern.DEFAULTS["lists"]), "co_op_bar": jobs.PREFS["threshold"]}


def homelab_setup():
    """`python -m nuauto.onboard homelab-setup` (Settings > Homelab > Set it up opens it in a terminal window): the
    steps of docs/DEPLOY.md's SET UP A HOMELAB, one at a time, each asked first. sudo asks for the homelab's password in
    this terminal (NUauto never sees it). Run it again any time: each step is safe to repeat."""
    from nuauto import sync
    if config.FROZEN or not config.HAS_SERVER or config.IS_SERVER:
        sys.exit("Run this on your laptop, from a source install, after Settings > Homelab > Use this homelab "
                 "(and a restart of NUauto).")
    host, folder = config.SERVER, config.SERVER_DIR
    q = shlex.quote
    run = lambda cmd, tty=False: subprocess.run(sync.SSH[:1] + (["-t"] if tty else []) + sync.SSH[1:]  # noqa: E731
                                                + [host, cmd]).returncode
    print(f"Setting up NUauto on {host} (folder ~/{folder}). Each step asks first; s skips it.")
    if run("true") != 0:
        sys.exit(f"ssh {host} does not work without a password yet: set up a key first (docs/DEPLOY.md).")
    if folder.rstrip("/") != HOMELAB_DIR:
        print(f"Note: the timers in deploy/systemd/ run NUauto from ~/{HOMELAB_DIR}. Another folder needs drop-ins "
              "(systemctl --user edit <unit>; docs/DEPLOY.md).")
    if _ask("1. Copy NUauto there: the code, your settings, resume, Google login and Discord webhook (sync.push)."):
        sync.push(code=True)
    if _ask("2. Its Python: uv (installed for your user if missing), a Python 3.12 venv with NUauto in it, and "
            "Playwright's Firefox (a few minutes)."):
        run(f"cd {q(folder)} && export PATH=\"$HOME/.local/bin:$PATH\" && "
            "(command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh) && "
            "(test -x .venv/bin/python || uv venv --python 3.12 .venv) && uv pip install --python .venv/bin/python -e . && "
            ".venv/bin/python -m playwright install firefox", tty=True)
    if os.path.exists(os.path.join(config.DATA_DIR, "pool.json")) and \
            _ask("3. Copy your job data (data/) there, so it doesn't score every job again."):
        sync._run(sync.RSYNC + ["data/", f"{sync.REMOTE}/data/"], "copy data")
    client = os.path.join(config.LOCAL_DIR, "client_secret.json")
    if os.path.exists(client) and _ask("4. Copy your Google client (local/client_secret.json, mode 600) there."):
        sync._run(sync.RSYNC + [client, f"{sync.REMOTE}/local/client_secret.json"], "copy the Google client")
        sync.lock_remote_local()
    if _ask("5. Claude Code there: installed for your user if missing, then started once, so you log in and trust "
            "the folder (in Claude: log in if it asks, answer Yes to trust the folder, then type /exit)."):
        run(f"export PATH=\"$HOME/.local/bin:$PATH\"; (command -v claude >/dev/null || curl -fsSL "
            f"https://claude.ai/install.sh | bash) && cd {q(folder)} && claude", tty=True)
    if os.path.exists(config.COOKIES_PATH) and _ask("6. Copy your NUworks session there (the browser profile)."):
        sync.push_session()
    elif not os.path.exists(config.COOKIES_PATH):
        print("\n6. No NUworks session on this computer yet: run `nuauto login` once, then this again.")
    units = ["nuauto-daily.timer", "nuauto-weekly.timer", "nuauto-intern.timer", "nuauto-web.service"] + \
        (["nuauto-deploy.timer"] if config.DEPLOY_FROM_GIT else []) + (["nuauto-assist.timer", "nuauto-workday.timer"] if config.REMOTE_ASSIST else [])
    if _ask("7. Its timers and web page (systemd user units), running even when nobody is logged in there "
            "(loginctl enable-linger: sudo asks for the homelab's password here). Turns on: " + ", ".join(units)):
        sync.remote("mkdir -p .config/systemd/user", "make the units folder")
        unit_dir = os.path.join(config.PROJECT_DIR, "deploy", "systemd")
        files = sorted(os.path.join(unit_dir, f) for f in os.listdir(unit_dir))
        sync._run(["rsync", "-a", "-e", " ".join(sync.SSH)] + files + [f"{host}:.config/systemd/user/"], "copy the units")
        run('sudo loginctl enable-linger "$USER"', tty=True)
        run("systemctl --user daemon-reload && systemctl --user enable --now " + " ".join(units))
    if config.REMOTE_ASSIST and _ask("8. The assistant's tools there: the virtual screen and its browser view, tmux, "
                                     "and the browser tool's own Firefox (sudo apt install: Debian / Ubuntu)."):
        from nuauto import assist
        run("sudo apt install -y xvfb x11vnc novnc nodejs npm tmux && "
            f"npx -y -p {q(assist.MCP_PACKAGE)} playwright install firefox", tty=True)
    print("\nDone. Check it: Settings > Status > Run all checks now (or `nuauto doctor`); then `nuauto update` runs "
          "one update there.")


def complete(step_list):
    return all(s["status"] != "todo" for s in step_list)


# ---------------------------------------------------------------- read-only NUworks reads (own process)

def remember(**data):
    found = read_json(ONBOARD_PATH, {})
    found.update(data)
    write_json(ONBOARD_PATH, found)


def a_job_urls(limit=4):
    """NUworks jobs to open the Apply popup of, best first: pool jobs that apply on NUworks itself; then jobs with
    details that don't point to the company's site; then any listed job (a new student's pool can be empty: the first
    update ran before they said what they study). The read tries them in turn until one has the Resume dropdown."""
    pool = jobs.load("pool.json", [])
    ids = [r["id"] for r in pool if "apply on company site too?" not in r.get("flags", [])] + [r["id"] for r in pool]
    ids += [i for i, d in jobs.load_details().items() if not jobs.external_hint(d)] + list(jobs.load("list.json", {}))
    if config.DEMO:
        ids.append("900101")
    return [jobs.job_url(i) for i in dict.fromkeys(ids)][:limit]


def read_resume_labels():
    """Open one job's Apply popup (hidden browser, domain lock on), read the Resume dropdown, close it. Nothing is
    filled or submitted: the same steps as `nuauto inspect`."""
    os.environ["AUTO_HEADLESS"] = "1"
    from playwright.sync_api import sync_playwright
    from nuauto import apply
    from nuauto import browser
    from nuauto.inspect_form import FIELDS_JS
    urls = a_job_urls()
    if not urls:
        return remember(labels=None, labels_error="No job to open yet: check for new jobs first (Today), then try again.")
    log = health.MemoryLog()
    labels, error = None, None
    with sync_playwright() as p:
        context = browser.launch(p)
        try:
            blocked = []
            browser.install_domain_lock(context, log, blocked)
            page = context.pages[0] if context.pages else context.new_page()
            for url in urls:  # until one job's popup has the Resume dropdown
                blocked.clear()
                if not browser.goto_logged_in(page, context, url, log):
                    error = "Not logged in to NUworks: log in first (the button above)."
                    break
                btn = apply.apply_button(page)
                if btn.count() != 1:
                    error = "That job has no Apply button; try again after the next update."
                    continue
                btn.click(timeout=10000)
                browser.pause(page, 2, 3)
                dialog = page.get_by_role("dialog")
                found = [f for f in dialog.evaluate(FIELDS_JS) if apply.is_resume(f)] if dialog.count() == 1 else []
                if len(found) != 1 or blocked:
                    error = "That job's Apply popup has no Resume dropdown (it may send you elsewhere)."
                else:
                    labels = [o.strip() for o in found[0]["options"] if o.strip() and o.strip() != "Select a resume"]
                    error = None
                cancel = dialog.get_by_role("button", name="Cancel")
                if cancel.count() == 1:
                    cancel.click()
                if labels:
                    break
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
    elif argv == ["homelab-setup"]:  # Settings > Homelab > Set it up (a terminal window)
        homelab_setup()
    elif argv[:1] == ["homelab-deploy"] and argv[1:2] in (["on"], ["off"]) and len(argv) <= 3:
        homelab_deploy(argv[1] == "on", argv[2] if len(argv) == 3 else None)
    elif argv[:1] == ["homelab-check"] and len(argv) in (2, 3):
        print(json.dumps(homelab_probe(*argv[1:]), indent=1))
    elif argv == ["resume-labels"]:
        read_resume_labels()
    elif argv == ["terms"]:
        read_terms()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
