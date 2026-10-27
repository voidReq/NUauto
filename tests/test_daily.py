"""Offline checks for daily.py's morning summary (scan counts + to-do) and Claude errors. Run: python test_daily.py

Uses a temp data dir, temp log file and a stubbed notify: no sheet, no Discord, no network.
"""
import json
import re
import os
import tempfile
from datetime import date, datetime

from nuauto import config

tmp = tempfile.mkdtemp()
config.DATA_DIR = os.path.join(tmp, "data")
config.LOGS_DIR = os.path.join(tmp, "logs")

from nuauto import daily  # noqa: E402
from nuauto import intern  # noqa: E402
from nuauto import sheet  # noqa: E402
from nuauto import web  # noqa: E402

intern.ENABLED = False  # your own settings may turn internships on: daily.main must not run that update here (network)
daily.LOG_PATH = os.path.join(config.LOGS_DIR, "test.txt")
sent = []
daily.notify = lambda title, body="": sent.append((title, body))

# the applications check (every run): asks NUworks about each sent row (jobs.check_applications) and logs what counts;
# never changes the run's result, and never reads the real sheet here
from nuauto import setup_sheet  # noqa: E402
real_sheet_fns = (sheet.open_worksheet, sheet.read_rows, daily.jobs.check_applications, setup_sheet.ensure_dropdown)
R_APPS = [sheet.Row(2, "https://n/jobs/detail/a", "Co", "T", "Applied", "NUworks side NOT submitted yet", "2026-10-01"),
          sheet.Row(3, "https://n/jobs/detail/b", "Co", "T", "Rejected", "", "2026-10-02")]
asked = []
sheet.open_worksheet = lambda interactive=True: "ws"
sheet.read_rows = lambda ws: R_APPS
setup_sheet.ensure_dropdown = lambda ws: asked.append("dropdown")
daily.jobs.check_applications = lambda rows: asked.append(len(rows)) or {
    "a": {"applied": False, "fingerprint": sheet.fingerprint(R_APPS[0])}, "b": {"applied": True, "fingerprint": sheet.fingerprint(R_APPS[1])}}
daily.applications()
assert asked == ["dropdown", 2], asked
log_text = open(daily.LOG_PATH).read()
assert "applications: 1 count toward the total; NUworks has none for row 2" in log_text, log_text[-300:]
assert "rejected, not withdrawn on NUworks: row 3" in log_text, log_text[-300:]   # NUworks still has the Rejected row's application
# an Applied row whose job stopped interviewing / got filled: in the log every run, no message (the morning one has it)
R_APPS.append(sheet.Row(4, "https://n/jobs/detail/c", "Filled Co", "Firmware", "Applied", "", "2026-10-03"))
saved = os.path.join(config.DATA_DIR, sheet.APPLICATIONS)
answers = {"b": {"applied": True, "fingerprint": sheet.fingerprint(R_APPS[1])},
           "c": {"applied": True, "fingerprint": sheet.fingerprint(R_APPS[2]), "interviewing_ended": "t2"}}
daily.jobs.check_applications = lambda rows: answers
sent.clear()
daily.applications()
assert "job looks filled: row 4 (stopped interviewing)" in open(daily.LOG_PATH).read() and sent == [], sent
os.makedirs(config.DATA_DIR, exist_ok=True)
with open(saved, "w") as f:                                 # (what that check saved: the morning message reads it)
    json.dump(answers, f)
line = daily.withdraw_line(R_APPS)
assert line == "Withdraw on NUworks to free room: rejected Co · may be filled Filled Co", line
os.remove(saved)
assert daily.withdraw_line(R_APPS) is None and daily.withdraw_line(None) is None
R_APPS.pop()
def no_nuworks(rows):
    raise SystemExit(daily.jobs.NOT_LOGGED_IN)
daily.jobs.check_applications = no_nuworks
daily.applications()                                        # NUworks wants a login: logged, nothing raised
assert "applications check stopped" in open(daily.LOG_PATH).read()
def no_sheet(interactive=True):
    raise sheet.NotLoggedIn("no login")
sheet.open_worksheet = no_sheet
daily.applications()                                        # no sheet: logged, nothing raised
assert "applications check skipped" in open(daily.LOG_PATH).read()
sheet.open_worksheet, sheet.read_rows, daily.jobs.check_applications, setup_sheet.ensure_dropdown = real_sheet_fns
daily.applications = lambda: None  # (main() below: never the real sheet or NUworks)

# record_scan keeps per-run counts and the best new job; scan_part adds up the last 24 hours only
daily.record_scan(12, 2, {"score": 109, "text": "Firmware Co-op | Harbor (score 109)"})
scans = daily.jobs.load(daily.SCANS, [])
assert len(scans) == 1 and scans[0]["listed"] == 12 and scans[0]["pool"] == 2 and scans[0]["best"]["score"] == 109, scans
for _ in range(70):
    daily.record_scan(1, 0)
assert len(daily.jobs.load(daily.SCANS, [])) == 60 and "best" not in daily.jobs.load(daily.SCANS, [])[-1]  # capped
now = datetime(2026, 10, 5, 8, 5)
scans = [{"time": "2026-10-04T07:00", "listed": 99, "pool": 9, "best": {"score": 200, "text": "old"}},  # older than 24h
         {"time": "2026-10-04T18:04", "listed": 5, "pool": 1, "best": {"score": 90, "text": "A | X (score 90)"}},
         {"time": "2026-10-05T08:03", "listed": 3, "pool": 2, "best": {"score": 95, "text": "B | Y (score 95)"}}]
assert daily.scan_part(scans, now, "co-op") == "3 co-ops (best: B | Y (score 95))"
assert daily.scan_part([], now, "internship") == "0 internships"
daily.jobs.save(daily.SCANS, scans)

# a problem: a message at once, then the same one at most once a day however often the runs fail
sent.clear()
daily.alert("NUauto: X broke", "why")
daily.alert("NUauto: X broke", "why")
assert sent == [("NUauto: X broke", "why")], sent
told = daily.jobs.load(daily.TOLD, {})
daily.jobs.save(daily.TOLD, {**told, "NUauto: X broke": (datetime.now() - daily.TELL_AGAIN).isoformat(timespec="minutes")})
daily.alert("NUauto: X broke", "why")
assert len(sent) == 2, sent                                 # the next day: told again

# the morning message: always sent, one short line for each thing that applies
row = lambda n, status, notes, day="2026-10-05": sheet.Row(n, f"https://x/jobs/{n}", f"Co{n}", "Title", status, notes, day)
web.BASE_URL = ""  # no Mark-done page: no Mark links
config.REMOTE_APP = False  # (your own settings may have the homelab's page: its link would end the message)
sheet.google_login_age = lambda: 1
lines = daily.morning([row(2, "Applied", "")], [], now).split("\n")
assert lines[0] == "New: 3 co-ops (best: B | Y (score 95))" and len(lines) == 2, lines
assert re.fullmatch(r"Week: \d+/\d+ applied · total 1/99 · 0 Approved waiting", lines[1]), lines  # (this week: by today)
lines = daily.morning([row(3, "Applied", sheet.SITE_MARK + " ..."), row(4, "Needs Human", "External application ...", "")],
                      [], now).split("\n")
assert lines[1] == "To do: 1 company site owed, 1 external application: [Co3](https://x/jobs/3) · [Co4](https://x/jobs/4)", lines
daily.later("recorded", ["Acme"])
daily.later("recorded", ["Acme", "Beta"])                   # (kept once each)
daily.later("deployed", ["abc1234 Shorter messages"])
sheet.google_login_age = lambda: 7
lines = daily.morning(None, [], now).split("\n")             # the sheet can't be read: no to-do or week, said why
assert lines[1:] == ["Recorded what you applied to on NUworks: Acme, Beta", "Updated: abc1234 Shorter messages",
                     "Wrong: can't read the sheet (the Google login? on the laptop run `nuauto login google`)"], lines
lines = daily.morning([], [], now).split("\n")
assert lines[-1] == "Wrong: the Google login has expired: on the laptop run `nuauto login google`", lines
sheet.google_login_age = lambda: 1
config.REMOTE_APP = True
from nuauto import review  # noqa: E402
real_page, review.page_url = review.page_url, lambda slot=None: "http://page/"
assert daily.morning([], [], now).split("\n")[-1] == "http://page/"   # the homelab's page ends it
review.page_url, config.REMOTE_APP = real_page, False

# Closing soon: Approved rows on both tabs that close within a week (soonest first, with the day; an internship's date
# is its posting's), and pool jobs not in the sheet that close within 3 days
os.makedirs(os.path.join(config.DATA_DIR, "details"), exist_ok=True)
for i, end in (("n1", "2026-10-05"), ("n2", "2026-10-09"), ("n3", "2026-10-20")):
    daily.jobs.save(f"details/{i}.json", {"id": i, "deadline": end, "posting_end": None})
nrow = lambda n, i, status: sheet.Row(n, f"https://x/jobs/{i}", f"Co {i}", "T", status, "", "2026-10-01")  # noqa: E731
real_dates = intern.closing_dates
intern.closing_dates = lambda: {"https://acme.example/j/1": date(2026, 10, 6)}
intern.save("links.json", {"https://go.vansh.nyc/AB": "https://acme.example/j/1"})  # (a row added with vanshb03's short link)
other = [sheet.Row(2, "https://go.vansh.nyc/AB", "Acme", "Intern", "Approved", "", ""),
         sheet.Row(3, "https://beta.example/j/2", "Beta", "Intern", "Approved", "", "")]
pool = [{"id": "p1", "closes": "2026-10-07"}, {"id": "n1", "closes": "2026-10-05"}, {"id": "p2", "closes": "2026-10-20"}]
line = daily.closing_line([nrow(5, "n1", "Approved"), nrow(6, "n2", "Approved"), nrow(7, "n3", "Approved"),
                           nrow(8, "n2", "Applied")], pool, date(2026, 10, 5), other)
assert line == f"Closing soon: Co n1 today, Acme tomorrow, Co n2 {date(2026, 10, 9):%a} · 1 pool job within 3 days (Review)", line
assert daily.closing_line([], [], date(2026, 10, 5)) is None
intern.closing_dates = real_dates
os.remove(intern.path("links.json"))
for i in ("n1", "n2", "n3"):
    os.remove(os.path.join(config.DATA_DIR, "details", f"{i}.json"))

# a batch's Claude is shut in work/ (the batches hold web text: job postings, Simplify's list): it runs there, may read
# and edit only there (Edit rules cover Write; never a bare Write, which would allow every path), no shell, web or MCP
config.WORK_DIR = os.path.join(tmp, "work")
seen = {}
real_run = daily.subprocess.run
daily.subprocess.run = lambda cmd, **kw: seen.update(cmd=cmd, **kw) or type("R", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()
try:
    assert daily.run_claude("TRIAGE_PROMPT.md", os.path.join(config.WORK_DIR, "triage_in_001.json")) is None
finally:
    daily.subprocess.run = real_run
cmd = seen["cmd"]
assert seen["cwd"] == config.WORK_DIR and os.path.isdir(config.WORK_DIR), seen.get("cwd")
allowed = cmd[cmd.index("--allowedTools") + 1: cmd.index("--disallowedTools")]
assert allowed == ["Read(./**)", "Edit(./**)"], allowed
assert cmd[cmd.index("--tools") + 1: cmd.index("--allowedTools")] == ["Read", "Write", "Edit"]
assert cmd[cmd.index("--disallowedTools") + 1: cmd.index("--permission-mode")] == ["Bash", "WebFetch", "WebSearch"]
assert "--strict-mcp-config" in cmd and cmd[cmd.index("--permission-mode") + 1] == "acceptEdits"
assert not any(a in ("Write", "Read", "Edit") for a in allowed)  # no bare (every path) allow

# a Claude login error (auth status said logged in, the call itself failed) -> the "logged out" message, no import
calls = []
daily.run_claude = lambda prompt, b, extra=None: calls.append(b) or "Failed to authenticate: OAuth session expired and could not be refreshed"
def no_import():
    raise AssertionError("must not import after a login error")
sent.clear()
assert daily.claude_step(lambda: True, "TRIAGE_PROMPT.md", ["a/triage_in_001.json", "a/triage_in_002.json"],
                         no_import, "NUauto: triage results incomplete") is False
assert calls == ["a/triage_in_001.json"], calls  # stops at the first batch
assert len(sent) == 1 and sent[0][0] == "NUauto: Claude Code is logged out", sent
assert "claude auth login" in sent[0][1] and "OAuth session expired" in sent[0][1], sent

# another Claude error: the import's problems, with what Claude said in front
daily.run_claude = lambda prompt, b, extra=None: "API Error: 529 Overloaded"
def missing():
    raise SystemExit("Not imported:\n  missing triage_out_001.json")
sent.clear()
assert daily.claude_step(lambda: True, "TRIAGE_PROMPT.md", ["a/triage_in_001.json"], missing, "T") is False
assert sent == [("T", "Claude failed on triage_in_001.json: API Error: 529 Overloaded\n"
                      "Not imported:\n  missing triage_out_001.json")], sent

# no batches: nothing to do, no login check
assert daily.claude_step(lambda: 1 / 0, "TRIAGE_PROMPT.md", [], no_import, "T") is True

# a failed scan (e.g. NUworks too slow) still sends the morning message, from the sheet + last saved pool; the
# problem is in it (no message of its own), and one broken line never stops the others
class Morning(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 10, 5, 8, 6)
daily.datetime = Morning
def slow(before):
    raise TimeoutError("Page.goto: Timeout 30000ms exceeded.")
def never(*a):
    raise AssertionError("hand-application check needs a fresh list; must not run after a failed scan")
real_scan = daily.scan
daily.scan, daily.record_hand_applications = slow, never
daily.sheet_rows = lambda tell=True: [row(4, "Needs Human", "External application ...", "")]
daily.closing_line = lambda *a: 1 / 0
sent.clear()
assert daily.main() == 1 and not daily.QUIET
assert [t for t, _ in sent] == ["NUauto morning"], sent
body = sent[0][1]
assert "1 external application: [Co4]" in body and "Wrong: the update stopped: NUworks did not load in time (NUworks was slow" in body, body
assert daily.jobs.load(daily.LATER, {}) == {}               # what it kept went out with it

# the evening run: no morning message, and the same problem was told today already: nothing
class Evening(Morning):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 10, 5, 18, 6)
daily.datetime = Evening
sent.clear()
assert daily.main() == 1 and sent == [], sent

# why a run stopped is kept for the GUI (last_run.json), and a slow NUworks gets a second try
daily.scan = real_scan
daily.RETRY_WAIT = 0
tries = []
def no_list():
    tries.append(1)
    raise SystemExit("No job list response for page 1; stopping.")
daily.jobs.cmd_list = no_list
daily.STOPPED.clear(); sent.clear()
assert daily.scan(set()) == 1 and len(tries) == 2, tries  # tried again
assert sent[-1][0] == "NUauto: NUworks did not answer" and "nuauto login" not in sent[-1][1], sent
daily.record_run(1)
run = daily.jobs.load(daily.LAST_RUN, {})
assert run["ok"] is False and run["reason"] == "nuworks" and run["said"] == "NUworks did not send the job list", run
def logged_out():
    tries.append(1)
    raise SystemExit(daily.jobs.NOT_LOGGED_IN)
daily.jobs.cmd_list = logged_out
tries.clear(); daily.STOPPED.clear()
assert daily.scan(set()) == 1 and len(tries) == 1  # a login is not retried
assert sent[-1][0] == "NUauto: NUworks needs you to log in" and "Log in to NUworks" in sent[-1][1], sent
daily.record_run(1)
assert daily.jobs.load(daily.LAST_RUN, {})["reason"] == "nuworks_login"
daily.scan = slow  # main(): Playwright's TimeoutError reads as NUworks not loading
assert daily.main() == 1
run = daily.jobs.load(daily.LAST_RUN, {})
assert run["reason"] == "nuworks" and run["said"] == "NUworks did not load in time" and run["time"] == "2026-10-05T18:06", run
daily.scan = lambda before: 0
assert daily.main() == 0 and daily.jobs.load(daily.LAST_RUN, {})["ok"] is True  # a good run clears it

# what the company-site assistant says goes to Discord unless local_config "assist_discord" is false; nothing else is affected
posted = []
daily.discord = posted.append
config.LOCAL_CONFIG_PATH = os.path.join(tmp, "local_config.json")
config.LOCAL = {k: v for k, v in config.LOCAL.items() if k != "assist_discord"}  # (not your own setting)
daily.discord_assistant("on by default (no file)")
json.dump({"assist_discord": False}, open(config.LOCAL_CONFIG_PATH, "w"))
daily.discord_assistant("off")
daily.discord("the morning message")
json.dump({"assist_discord": True}, open(config.LOCAL_CONFIG_PATH, "w"))
daily.discord_assistant("on again")
assert posted == ["on by default (no file)", "the morning message", "on again"], posted

print("All morning-summary checks passed.")
