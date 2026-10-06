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

print("All health checks passed.")
