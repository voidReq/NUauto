"""Health checks: `nuauto doctor` prints them, the GUI shows them as its status strip (and re-runs them on timers).

Every check returns a list of Check. They change nothing, with one exception: `nuworks` opens the hidden browser
on NUworks and, if the session expired, does the usual one-click SSO re-login (like every NUworks run), which
refreshes the saved cookies. No check opens a login page you have to deal with, prints a secret, or touches the
sheet's contents.

  python -m nuauto.health [--json] [name ...]
      names: quick (files, resume, google, nuworks_saved, updates), light (sheet, claude, firefox, discord,
      homelab), nuworks (hidden browser, slow), claude_live (one tiny real Claude call). Default: quick light.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import date, datetime

from nuauto import config

OK, WARN, FAIL, BUSY, OFF, UNKNOWN = "ok", "warn", "fail", "busy", "off", "unknown"
# fix: what the GUI's button does (gui.ACTIONS); "" = nothing to click
SETUP = ("setup", "Open setup")
LOGIN_GOOGLE = ("login_google", "Log in to Google")
LOGIN_NUWORKS = ("login_nuworks", "Log in to NUworks")
SECRET_FILES = ["token.json", "session_cookies.json", "client_secret.json", "discord_webhook.txt", "profile.json",
                "answers.json", "web_secret.txt"]
PLACEHOLDER_SHEET = "YOUR_GOOGLE_SHEET_ID"
STALE_UPDATE_HOURS = 15  # updates run 10 and 14 hours apart (+ up to 15 min random delay)


@dataclass
class Check:
    id: str
    title: str
    status: str
    detail: str
    fix: str = ""
    fix_label: str = ""
    at: float = field(default_factory=time.time)

    def to_dict(self):
        return asdict(self)


def check(id_, title, status, detail, fix=("", "")):
    return Check(id_, title, status, detail, fix[0], fix[1])


def _local(name):
    return os.path.join(config.LOCAL_DIR, name)


# ---------------------------------------------------------------- quick (files only, no network)

def files():
    if not os.path.exists(config.LOCAL_CONFIG_PATH):
        return [check("files", "Settings", FAIL, "No settings yet (local/local_config.json).", SETUP)]
    if config.SHEET_ID in ("", PLACEHOLDER_SHEET):
        return [check("files", "Settings", FAIL, "No Google Sheet chosen yet.", SETUP)]
    loose = [n for n in SECRET_FILES if os.path.isfile(_local(n)) and os.stat(_local(n)).st_mode & 0o077]
    if os.stat(config.LOCAL_DIR).st_mode & 0o077:
        loose.insert(0, "local/ itself")
    if loose:
        return [check("files", "Settings", WARN, f"Readable by other users: {', '.join(loose)}.",
                      ("fix_permissions", "Fix permissions"))]
    return [check("files", "Settings", OK, "Settings saved; secret files are private (mode 600).")]


def fix_permissions():
    """local/ mode 700, the secret files in it mode 600 (what files() asks for)."""
    os.chmod(config.LOCAL_DIR, 0o700)
    for n in SECRET_FILES:
        if os.path.isfile(_local(n)):
            os.chmod(_local(n), 0o600)


def resume_label():
    try:
        with open(config.PROFILE_PATH) as f:
            return (json.load(f).get("resume_label") or "").strip()
    except (OSError, ValueError, AttributeError):
        return ""


def resume():
    if not os.path.isfile(config.RESUME_PATH):
        return [check("resume", "Resume", FAIL, f"Resume PDF not found ({config.LAPTOP_RESUME}).", SETUP)]
    name = os.path.basename(config.RESUME_PATH)
    if not resume_label():
        return [check("resume", "Resume", WARN, f"{name} found, but the NUworks resume label is not set "
                      "(apply can't pick your resume in the Apply popup).", SETUP)]
    return [check("resume", "Resume", OK, f"{name}; NUworks resume label set.")]


DOWNLOADS = os.path.expanduser("~/Downloads")  # where Google Cloud's "Download JSON" lands (config.find_client_json)


def client_json_found():
    if os.path.exists(_local("client_secret.json")):
        return True
    import glob
    return len(glob.glob(os.path.join(DOWNLOADS, "client_secret_*.json"))) == 1


def google_age():
    """Days since the last Google login (google_login.txt), or None."""
    try:
        with open(config.GOOGLE_LOGIN_PATH) as f:
            return (date.today() - date.fromisoformat(f.read().strip())).days
    except (OSError, ValueError):
        return None


def google():
    if not os.path.exists(config.TOKEN_PATH):
        if not client_json_found():
            return [check("google", "Google", FAIL, "No Google OAuth client yet (client_secret.json).", SETUP)]
        return [check("google", "Google", FAIL, "Not logged in to Google.", LOGIN_GOOGLE)]
    age = google_age()
    if age is None:
        return [check("google", "Google", WARN, "Logged in, but the login date is unknown; log in again to reset "
                      f"the {config.GOOGLE_LOGIN_DAYS}-day clock.", LOGIN_GOOGLE)]
    left = config.GOOGLE_LOGIN_DAYS - age
    if left <= 0:
        return [check("google", "Google", FAIL, "Google login expired.", LOGIN_GOOGLE)]
    if left <= 1:
        return [check("google", "Google", WARN, "Google login expires tomorrow." if left == 1 else
                      "Google login expires today.", LOGIN_GOOGLE)]
    return [check("google", "Google", OK, f"Logged in; {left} days left.")]


def nuworks_saved():
    """The saved NUworks session, from the files only. The real answer comes from `nuworks` (hidden browser)."""
    if not os.path.exists(config.COOKIES_PATH):
        return [check("nuworks", "NUworks", FAIL, "Not logged in to NUworks yet.", LOGIN_NUWORKS)]
    days = (time.time() - os.path.getmtime(config.COOKIES_PATH)) / 86400
    when = "today" if days < 1 else f"{days:.0f} day{'s' if days >= 1.5 else ''} ago"
    return [check("nuworks", "NUworks", UNKNOWN, f"Session saved {when}; not checked yet.")]


def updates():
    from nuauto import jobs
    scans = jobs.load("scans.json", [])
    if not scans:
        return [check("updates", "New jobs", WARN, "No check for new jobs has run yet.", ("update", "Check now"))]
    last = scans[-1]
    hours = (datetime.now() - datetime.fromisoformat(last["time"])).total_seconds() / 3600
    when = f"{hours * 60:.0f} min ago" if hours < 1 else f"{hours:.0f} h ago"
    detail = f"Last check {when}: {last['listed']} new on NUworks, {last['pool']} made your pool."
    if hours > STALE_UPDATE_HOURS:
        return [check("updates", "New jobs", WARN, detail, ("update", "Check now"))]
    return [check("updates", "New jobs", OK, detail)]


# ---------------------------------------------------------------- light (one network call or subprocess each)

def sheet_and_limits():
    """Opens the sheet (never a login page). Returns (checks, rows or None): google (if the login failed), sheet,
    limits. The rows let the GUI skip a second read."""
    from nuauto import sheet
    try:
        rows = sheet.read_rows(sheet.open_worksheet(interactive=False))
        week, total = sheet.check_limits_safe(rows)
    except sheet.NotLoggedIn as e:
        return [check("google", "Google", FAIL, str(e), LOGIN_GOOGLE)], None
    except sheet.SheetError as e:
        return [check("sheet", "Sheet", FAIL, str(e), ("open_sheet", "Open the sheet"))], None
    except Exception as e:  # network, Google API errors
        return [check("sheet", "Sheet", WARN, f"Could not reach Google Sheets ({type(e).__name__}).")], None
    out = [check("sheet", "Sheet", OK, f"{len(rows)} rows, columns as expected.")]
    counts = f"{week}/{sheet.MAX_PER_WEEK} applied {sheet.week_window()[1]}; {total}/{sheet.MAX_TOTAL} in total."
    if total >= sheet.MAX_TOTAL:
        out.append(check("limits", "Limits", WARN, "Total limit reached: " + counts))
    elif week >= sheet.MAX_PER_WEEK:
        out.append(check("limits", "Limits", WARN, "Weekly limit reached; Approved rows wait for next week. " + counts))
    else:
        out.append(check("limits", "Limits", OK, counts))
    return out, rows


def sheet_live():
    return sheet_and_limits()[0]


def claude_status():
    """`claude auth status --json` as a dict ({} if it gave no usable answer), or None if claude is not installed."""
    path = config.tool_path("claude")
    if not path:
        return None
    try:
        r = subprocess.run([path, "auth", "status", "--json"], capture_output=True, text=True, timeout=30)
        data = json.loads(r.stdout)
        return data if isinstance(data, dict) else {}
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {}


def claude():
    status = claude_status()
    if status is None:
        return [check("claude", "Claude Code", FAIL, "Claude Code is not installed (it scores the jobs).",
                      ("install_claude", "Install Claude Code"))]
    if not status:
        return [check("claude", "Claude Code", WARN, "Claude Code did not answer `claude auth status`.")]
    if not status.get("loggedIn"):
        return [check("claude", "Claude Code", FAIL, "Claude Code is logged out.", ("login_claude", "Log in to Claude"))]
    plan = status.get("subscriptionType") or status.get("authMethod") or ""
    return [check("claude", "Claude Code", OK, "Logged in" + (f" ({plan})." if plan else "."))]


def claude_live():
    """One tiny real call (no tools, no MCP servers, not saved as a session): catches what `auth status` can't
    see, like a revoked login or a usage limit."""
    path = config.tool_path("claude")
    if not path:
        return claude()
    cmd = [path, "-p", "Reply with the single word OK.", "--model", "sonnet", "--tools", "", "--strict-mcp-config",
           "--no-session-persistence", "--output-format", "json"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180, cwd=tempfile.gettempdir())
    except subprocess.TimeoutExpired:
        return [check("claude", "Claude Code", WARN, "Claude did not answer within 3 minutes.")]
    except OSError as e:
        return [check("claude", "Claude Code", FAIL, f"Could not run claude ({type(e).__name__}).")]
    try:
        data = json.loads(r.stdout)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return [check("claude", "Claude Code", FAIL, f"Claude call failed (exit {r.returncode}).",
                      ("login_claude", "Log in to Claude"))]
    if data.get("is_error") or "ok" not in str(data.get("result", "")).lower():
        text = " ".join(str(data.get("result") or data.get("subtype") or "error").split())[:160]
        return [check("claude", "Claude Code", FAIL, f"Claude answered with an error: {text}", ("login_claude", "Log in to Claude"))]
    return [check("claude", "Claude Code", OK, "Logged in; a test call just worked.")]


def firefox():
    """Playwright's Firefox is installed and really starts (a hidden one, with its own empty profile: never the
    NUworks profile). On Linux a missing system library shows up here."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            if not os.path.exists(p.firefox.executable_path):
                return [check("firefox", "Browser", FAIL, "Playwright's Firefox is not installed.",
                              ("install_firefox", "Install the browser"))]
            p.firefox.launch(headless=True).close()
    except Exception as e:
        first = (str(e).strip().splitlines() or [""])[0][:160]
        return [check("firefox", "Browser", FAIL, f"Playwright's Firefox does not start: {first} "
                      "(on Linux it may need system libraries: see README.md, Setup).", ("install_firefox", "Install the browser"))]
    return [check("firefox", "Browser", OK, "Playwright's Firefox is installed and starts.")]


WEBHOOK = re.compile(r"^https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+$")


def discord():
    """GET on the webhook returns its details and posts nothing. The URL is a secret: never shown."""
    try:
        with open(config.DISCORD_WEBHOOK_PATH) as f:
            url = f.read().strip()
    except OSError:
        return [check("discord", "Discord", OFF, "Not set up (optional).", ("setup", "Set up"))]
    if not WEBHOOK.match(url):
        return [check("discord", "Discord", FAIL, "discord_webhook.txt does not hold a Discord webhook URL.", SETUP)]
    try:
        urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "nuauto-health"}), timeout=15).close()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404):
            return [check("discord", "Discord", FAIL, "Discord says this webhook no longer exists.", SETUP)]
        return [check("discord", "Discord", WARN, f"Discord answered HTTP {e.code}.")]
    except OSError:
        return [check("discord", "Discord", WARN, "Could not reach Discord.")]
    return [check("discord", "Discord", OK, "Webhook works.")]


def homelab():
    if not config.HAS_SERVER or config.IS_SERVER:
        return [check("homelab", "Homelab", OFF, "Not used (everything runs on this machine).")]
    from nuauto import doctor
    results = doctor.server_results()
    if results is None:
        return [check("homelab", "Homelab", FAIL, "Can't reach the homelab over ssh (Tailscale up?).")]
    bad = [r for r in results if r["level"] == "FAIL"]
    warn = [r for r in results if r["level"] == "WARN"]
    if bad:
        return [check("homelab", "Homelab", FAIL, "; ".join(r["msg"] for r in bad)[:300])]
    if warn:
        return [check("homelab", "Homelab", WARN, "; ".join(r["msg"] for r in warn)[:300])]
    return [check("homelab", "Homelab", OK, f"All {len(results)} homelab checks pass.")]


# ---------------------------------------------------------------- heavy (hidden browser)

class MemoryLog:
    """browser.RunLog's write(), kept in memory: the check makes no logs/ folder."""

    def __init__(self):
        self.lines = []

    def write(self, msg):
        self.lines.append(msg)


def nuworks():
    """Open NUworks in the hidden browser (domain lock on), doing the one-click SSO re-login if needed."""
    if not os.path.exists(config.COOKIES_PATH):
        return nuworks_saved()
    os.environ["AUTO_HEADLESS"] = "1"
    from playwright.sync_api import sync_playwright
    from nuauto import browser
    log, ok, password = MemoryLog(), False, False
    try:
        with sync_playwright() as p:
            context = browser.launch(p)
            try:
                browser.install_domain_lock(context, log, [])
                page = context.pages[0] if context.pages else context.new_page()
                ok = browser.goto_logged_in(page, context, config.NUWORKS_START_URL, log)
                if ok:
                    browser.save_cookies(context.cookies())  # keeps the saved session fresh
                else:
                    password = page.locator("input[type=password]").count() > 0
            finally:
                context.close()
    except browser.ProfileBusy as e:
        return [check("nuworks", "NUworks", BUSY, str(e))]
    except Exception as e:
        return [check("nuworks", "NUworks", WARN, f"Could not check NUworks ({type(e).__name__}).")]
    if ok:
        renewed = any("re-login succeeded" in line for line in log.lines)
        return [check("nuworks", "NUworks", OK, "Logged in" + (" (renewed with the one-click re-login)." if renewed else "."))]
    if password or any("password" in line for line in log.lines):
        return [check("nuworks", "NUworks", FAIL, "NUworks needs you to log in (the SSO page asks for a password).",
                      LOGIN_NUWORKS)]
    return [check("nuworks", "NUworks", FAIL, "Could not get past NUworks' sign-in page.", LOGIN_NUWORKS)]


QUICK = {"files": files, "resume": resume, "google": google, "nuworks_saved": nuworks_saved, "updates": updates}
LIGHT = {"sheet": sheet_live, "claude": claude, "firefox": firefox, "discord": discord, "homelab": homelab}
HEAVY = {"nuworks": nuworks, "claude_live": claude_live}
ALL = {**QUICK, **LIGHT, **HEAVY}


def run(names):
    out = []
    for name in names:
        try:
            out += ALL[name]()
        except Exception as e:  # a broken check must not hide the others
            out.append(check(name, name, WARN, f"Check failed: {type(e).__name__}: {str(e)[:120]}"))
    return out


def merge(checks):
    """One Check per id; a later result for the same id replaces an earlier one (e.g. sheet's google failure
    replaces the quick google check)."""
    by_id = {}
    for c in checks:
        by_id[c.id] = c
    return list(by_id.values())


def desktop_notify(title, body=""):
    """A desktop notification: notify-send on Linux, osascript on macOS; nothing if neither is there."""
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "NUauto", title, body], check=False)
    elif sys.platform == "darwin" and shutil.which("osascript"):
        script = (f"display notification {json.dumps(body[:200], ensure_ascii=False)} "
                  f"with title {json.dumps(title, ensure_ascii=False)}")
        subprocess.run(["osascript", "-e", script], check=False)


def main(argv):
    as_json = "--json" in argv
    names = [a for a in argv if a != "--json"] or ["quick", "light"]
    expanded = []
    for n in names:
        if n == "quick":
            expanded += list(QUICK)
        elif n == "light":
            expanded += list(LIGHT)
        elif n in ALL:
            expanded.append(n)
        else:
            sys.exit(__doc__)
    checks = merge(run(expanded))
    if as_json:
        print(json.dumps([c.to_dict() for c in checks]))
    else:
        for c in checks:
            print(f"  {c.status:7} {c.title}: {c.detail}")
    return 1 if any(c.status == FAIL for c in checks) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
