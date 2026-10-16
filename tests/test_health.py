"""Offline checks for health.py (the checks behind `nuauto doctor` and the GUI's status strip). Run: python test_health.py

Everything runs on a temp folder; claude is a fake script; no network (the Discord GET and the sheet are never
reached), no browser.
"""
import json
import os
import sys
import tempfile
import time
from datetime import date, datetime, timedelta

from nuauto import config
from nuauto import health
from nuauto import sheet

tmp = tempfile.mkdtemp()
local = os.path.join(tmp, "local")
os.makedirs(local, mode=0o700)
config.LOCAL_DIR = local
config.LOCAL_CONFIG_PATH = os.path.join(local, "local_config.json")
config.PROFILE_PATH = os.path.join(local, "profile.json")
config.TOKEN_PATH = os.path.join(local, "token.json")
config.GOOGLE_LOGIN_PATH = os.path.join(local, "google_login.txt")
config.COOKIES_PATH = os.path.join(local, "session_cookies.json")
config.DISCORD_WEBHOOK_PATH = os.path.join(local, "discord_webhook.txt")
config.DATA_DIR = os.path.join(tmp, "data")
config.RESUME_PATH = config.LAPTOP_RESUME = os.path.join(tmp, "resume.pdf")
health.DOWNLOADS = os.path.join(tmp, "Downloads")  # never this machine's ~/Downloads


def one(fn):
    checks = fn()
    assert len(checks) == 1, checks
    return checks[0]


def write(name, text, mode=0o600):
    path = os.path.join(local, name)
    with open(path, "w") as f:
        f.write(text)
    os.chmod(path, mode)
    return path


# files: settings first, then the sheet ID, then permissions (with a fix that works)
c = one(health.files)
assert (c.status, c.fix) == ("fail", "setup"), c
write("local_config.json", "{}", 0o644)
config.SHEET_ID = health.PLACEHOLDER_SHEET
assert one(health.files).status == "fail"
config.SHEET_ID = "abc123"
assert one(health.files).status == "ok"
write("token.json", "{}", 0o644)
c = one(health.files)
assert (c.status, c.fix) == ("warn", "fix_permissions") and "token.json" in c.detail, c
health.fix_permissions()
assert one(health.files).status == "ok" and oct(os.stat(config.TOKEN_PATH).st_mode & 0o777) == "0o600"
os.remove(config.TOKEN_PATH)

# resume: the PDF, then the NUworks resume label
assert one(health.resume).status == "fail"
open(config.RESUME_PATH, "wb").write(b"%PDF-1.4")
assert one(health.resume).status == "warn"
write("profile.json", json.dumps({"resume_label": "Me | Resume"}))
assert one(health.resume).status == "ok"

# google: client, then login, then the 7-day clock
c = one(health.google)
assert c.status == "fail" and "OAuth client" in c.detail, c
write("client_secret.json", "{}")
c = one(health.google)
assert (c.status, c.fix) == ("fail", "login_google"), c
write("token.json", "{}")
assert one(health.google).status == "warn"  # no login date
for days, status in ((0, "ok"), (5, "ok"), (6, "warn"), (7, "fail"), (30, "fail")):
    write("google_login.txt", (date.today() - timedelta(days=days)).isoformat() + "\n", 0o644)
    c = one(health.google)
    assert c.status == status, (days, c)
assert "7 days left" in (write("google_login.txt", date.today().isoformat()) and one(health.google).detail)

# NUworks from the files: never logged in = fail; a saved session = unknown until the browser check runs
assert one(health.nuworks_saved).status == "fail"
write("session_cookies.json", "[]")
assert one(health.nuworks_saved).status == "unknown"

# updates: none yet / recent / stale
from nuauto import jobs  # noqa: E402
assert one(health.updates).status == "warn"
jobs.save("scans.json", [{"time": datetime.now().isoformat(timespec="minutes"), "listed": 3, "pool": 1}])
c = one(health.updates)
assert c.status == "ok" and "3 new on NUworks, 1 made your pool" in c.detail, c
jobs.save("scans.json", [{"time": (datetime.now() - timedelta(hours=20)).isoformat(timespec="minutes"), "listed": 0, "pool": 0}])
assert one(health.updates).status == "warn"

# updates: the last update stopped (daily.py's last_run.json): why, and the fix for it
def stopped(reason, said, local=True):
    config.HAS_SERVER, config.IS_SERVER = not local, False
    jobs.save("last_run.json", {"time": datetime.now().isoformat(timespec="minutes"), "ok": False, "reason": reason,
                                "said": said})
    return one(health.updates)
c = stopped("nuworks_login", "NUworks needs you to log in", local=False)
assert c.status == "fail" and c.fix == "login_nuworks" and "on the homelab stopped" in c.detail, c
assert "copies the session to the homelab" in c.detail, c
c = stopped("nuworks", "NUworks did not send the job list")
assert c.status == "warn" and c.fix == "update" and "slow or down" in c.detail and "homelab" not in c.detail, c
assert stopped("claude", "Claude Code is logged out").fix == "login_claude"
assert stopped("claude", "Claude Code is logged out", local=False).fix == "homelab_claude"  # its own Claude, over ssh
c = stopped("failed", "ProfileBusy: the browser profile is in use")
assert c.status == "warn" and c.fix == "update" and "ProfileBusy" in c.detail, c
jobs.save("last_run.json", {"time": datetime.now().isoformat(timespec="minutes"), "ok": True, "reason": None, "said": ""})
assert one(health.updates).detail.startswith("Last check"), one(health.updates)  # went fine: the scan counts again
jobs.save("last_run.json", {"ok": False})  # unreadable record: ignored, never a crash
assert one(health.updates).detail.startswith("Last check")
os.remove(os.path.join(config.DATA_DIR, "last_run.json"))
config.HAS_SERVER = False

# claude: through a fake `claude` script (auth status / -p), and missing altogether
FAKE = """#!{py}
import json, sys
mode = {mode!r}
if sys.argv[1:3] == ["auth", "status"]:
    print(json.dumps({{"loggedIn": mode != "out", "subscriptionType": "pro"}}))
elif "-p" in sys.argv:
    print(json.dumps({{"type": "result", "is_error": mode != "in", "result": "OK" if mode == "in" else "Usage limit reached"}}))
"""


def fake_claude(mode):
    path = os.path.join(tmp, f"claude-{mode}")
    with open(path, "w") as f:
        f.write(FAKE.format(py=sys.executable, mode=mode))
    os.chmod(path, 0o755)
    config.LOCAL["tools"] = {"claude": path}


fake_claude("in")
c = one(health.claude)
assert c.status == "ok" and "pro" in c.detail, c
assert one(health.claude_live).status == "ok"
fake_claude("out")
c = one(health.claude)
assert (c.status, c.fix) == ("fail", "login_claude"), c
fake_claude("limit")
c = one(health.claude_live)
assert c.status == "fail" and "Usage limit" in c.detail, c
real_tool_path = config.tool_path
config.tool_path = lambda name: None
c = one(health.claude)
assert (c.status, c.fix) == ("fail", "install_claude"), c
config.tool_path = real_tool_path

# discord: optional (off), a file that is not a webhook URL = fail; the URL is never shown
assert one(health.discord).status == "off"
write("discord_webhook.txt", "https://example.com/not-a-webhook")
c = one(health.discord)
assert c.status == "fail" and "example.com" not in c.detail, c
assert health.WEBHOOK.match("https://discord.com/api/webhooks/123/abc-DEF_9")

# homelab: local mode = off
config.HAS_SERVER = False
assert one(health.homelab).status == "off"

# bitwarden: optional (off unless local_config "password_manager" is "bitwarden"); a fake `bw` first on PATH answers
# `bw status`; the real one is never reached (PATH is only the fake's folder; config.tool_path is cut off too)
from nuauto import accounts  # noqa: E402

accounts.PATH = os.path.join(local, "accounts.json")
bw_dir = os.path.join(tmp, "fakebin")
os.makedirs(bw_dir)
FAKE_BW = """#!{py}
import sys
assert sys.argv[1:] == ["status", "--nointeraction"], sys.argv
{body}
"""


def fake_bw(body):
    path = os.path.join(bw_dir, "bw")
    with open(path, "w") as f:
        f.write(FAKE_BW.format(py=sys.executable, body=body))
    os.chmod(path, 0o755)


def says(status):
    return f"print('{{\"status\": \"{status}\"}}')"


real_path, real_pm, real_tool_path = os.environ["PATH"], config.LOCAL.get("password_manager"), config.tool_path
config.tool_path = lambda name: None
os.environ["PATH"] = bw_dir
try:
    config.LOCAL.pop("password_manager", None)
    c = one(health.bitwarden)
    assert c.status == "off" and not c.fix, c
    config.LOCAL["password_manager"] = ""
    assert one(health.bitwarden).status == "off"
    config.LOCAL["password_manager"] = "keepass"
    assert one(health.bitwarden).status == "off"
    config.LOCAL["password_manager"] = "bitwarden"
    c = one(health.bitwarden)  # on, but no bw
    assert (c.status, c.fix) == ("warn", "bitwarden_install") and "not installed" in c.detail, c
    assert accounts.waiting() == 0  # no accounts.json yet
    fake_bw(says("unauthenticated"))
    c = one(health.bitwarden)
    assert (c.status, c.fix, c.fix_label) == ("warn", "bitwarden_login", "Log in"), c
    fake_bw("print('not json')")
    c = one(health.bitwarden)
    assert c.status == "warn" and not c.fix and "no answer" in c.detail, c
    fake_bw("print('{}')")
    assert one(health.bitwarden).status == "warn"
    fake_bw("sys.exit(1)")
    assert one(health.bitwarden).status == "warn"
    for state in ("locked", "unlocked"):  # locked is fine: it is never unlocked here
        fake_bw(says(state))
        c = one(health.bitwarden)
        assert c.status == "ok" and not c.fix, (state, c)
    # waiting: logins NUauto made and did not export yet; your own and exported ones never count
    with open(accounts.PATH, "w") as f:
        json.dump({"a.example.com": {"site": "a.example.com", "password": "pw-a"},
                   "b.example.com": {"site": "b.example.com", "password": "pw-b", "exported": False},
                   "c.example.com": {"site": "c.example.com", "password": "pw-c", "exported": True},
                   "d.example.com": {"site": "d.example.com", "own": True},
                   "e.example.com": {"site": "e.example.com", "own": True, "exported": False}}, f)
    assert accounts.waiting() == 2
    c = one(health.bitwarden)
    assert (c.status, c.fix, c.fix_label) == ("warn", "bitwarden_add", "Add 2 to Bitwarden") and "2 logins" in c.detail, c
    assert "pw-" not in json.dumps(c.to_dict())
    with open(accounts.PATH, "w") as f:
        json.dump({"a.example.com": {"site": "a.example.com"}, "c.example.com": {"exported": True}}, f)
    c = one(health.bitwarden)
    assert (c.fix, c.fix_label) == ("bitwarden_add", "Add 1 to Bitwarden") and "1 login " in c.detail, c
    with open(accounts.PATH, "w") as f:
        json.dump({"c.example.com": {"exported": True}, "d.example.com": {"own": True}}, f)
    assert one(health.bitwarden).status == "ok"
    open(accounts.PATH, "w").write("not json")
    assert accounts.waiting() == 0 and one(health.bitwarden).status == "ok"
    # demo mode never uses a real bw, even with one on PATH
    config.DEMO = True
    try:
        assert one(health.bitwarden).fix == "bitwarden_install"
    finally:
        config.DEMO = False
    # a slow bw is a "no answer", not a hang
    fake_bw("import time; time.sleep(30)")
    real_run = health.subprocess.run

    def quick_timeout(*a, **kw):
        kw["timeout"] = 0.5
        return real_run(*a, **kw)
    health.subprocess.run = quick_timeout
    try:
        c = one(health.bitwarden)
        assert c.status == "warn" and "no answer" in c.detail, c
    finally:
        health.subprocess.run = real_run
finally:
    os.environ["PATH"] = real_path
    config.tool_path = real_tool_path
    if real_pm is None:
        config.LOCAL.pop("password_manager", None)
    else:
        config.LOCAL["password_manager"] = real_pm
assert "bitwarden" in health.ALL

# the sheet: login problems become a google failure, a broken sheet a sheet failure, counts become limits
real_open = sheet.open_worksheet


class WS:
    def __init__(self, values):
        self.values = values

    def get_all_values(self):
        return self.values


def opened(result):
    def fake(interactive=True):
        assert interactive is False  # a check never opens Google's login page
        if isinstance(result, Exception):
            raise result
        return result
    sheet.open_worksheet = fake
    return health.sheet_and_limits()


checks, rows = opened(sheet.NotLoggedIn("Google login expired."))
assert [(c.id, c.status, c.fix) for c in checks] == [("google", "fail", "login_google")] and rows is None
checks, rows = opened(WS([["URL", "Oops"]]))
assert [(c.id, c.status) for c in checks] == [("sheet", "fail")], checks
checks, rows = opened(ConnectionError("offline"))
assert [(c.id, c.status) for c in checks] == [("sheet", "warn")], checks
today = date.today().isoformat()
values = [sheet.HEADERS] + [[f"https://x/{i}", "Co", "Job", "Applied", "", today] for i in range(3)]
checks, rows = opened(WS(values))
by = {c.id: c for c in checks}
assert by["sheet"].status == "ok" and by["limits"].status == "ok" and "3/11" in by["limits"].detail, by
values = [sheet.HEADERS] + [[f"https://x/{i}", "Co", "Job", "Applied", "", today] for i in range(sheet.MAX_PER_WEEK)]
checks, rows = opened(WS(values))
assert {c.id: c for c in checks}["limits"].status == "warn" and len(rows) == sheet.MAX_PER_WEEK
sheet.open_worksheet = real_open

# merge: a later result for the same id wins; run(): a crashing check becomes a warning, the others still run
a = health.check("google", "Google", "ok", "fine")
b = health.check("google", "Google", "fail", "expired")
assert health.merge([a, b]) == [b]
health.ALL["boom"] = lambda: 1 / 0
out = health.run(["boom", "homelab"])
assert out[0].status == "warn" and "ZeroDivisionError" in out[0].detail and out[1].id == "homelab"
assert json.loads(json.dumps([c.to_dict() for c in out]))[1]["status"] == "off"
assert time.time() - out[0].at < 60

# the version check: only the packaged app asks GitHub; versions compare as numbers
assert health.newer("0.10.0", "0.9.1") and not health.newer("0.3.0", "0.3.0") and not health.newer("v1", "0.3.0")
assert one(health.app_update).status == "off"  # a source install: no network call

# the two machines' code: a warning only when both are known and differ (the homelab's page and this app could differ)
assert health.same_code("a" * 40, "a" * 40) is None and health.same_code(None, "b" * 40) is None
assert health.same_code("a" * 40, None) is None
skew = health.same_code("abcdef1234", "1234567abc")
assert skew["level"] == "WARN" and "the homelab runs 1234567, this computer abcdef1" in skew["msg"], skew

print("All health checks passed.")
