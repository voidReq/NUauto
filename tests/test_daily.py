"""Offline checks for daily.py's morning summary (scan counts + to-do) and Claude errors. Run: python test_daily.py

Uses a temp data dir, temp log file and a stubbed notify: no sheet, no Discord, no network.
"""
import os
import tempfile
from datetime import datetime

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

# record_scan keeps per-run counts; scan_summary adds up the last 24 hours only
daily.record_scan(12, 2)
scans = daily.jobs.load(daily.SCANS, [])
assert len(scans) == 1 and scans[0]["listed"] == 12 and scans[0]["pool"] == 2, scans
now = datetime(2026, 10, 5, 8, 5)
scans = [{"time": "2026-10-04T07:00", "listed": 99, "pool": 9},   # older than 24h: ignored
         {"time": "2026-10-04T18:04", "listed": 5, "pool": 1},
         {"time": "2026-10-05T08:03", "listed": 3, "pool": 0}]
s = daily.scan_summary(scans, now)
assert s == "Last 24h (2 scans): 8 new postings on NUworks, 1 made your pool (nuauto approve)", s
s = daily.scan_summary([], now)
assert s == "Last 24h (0 scans): 0 new postings on NUworks, 0 made your pool", s
for _ in range(70):
    daily.record_scan(1, 0)
assert len(daily.jobs.load(daily.SCANS, [])) == 60  # capped

# morning message is always sent, with the summary first
row = lambda n, status, notes: sheet.Row(n, f"https://x/jobs/{n}", f"Co{n}", "Title", status, notes, "")
daily.todo_notice([row(2, "Applied", "")], "SUMMARY")
assert sent[-1] == ("NUauto morning", "SUMMARY\nNothing only you need to finish."), sent[-1]
daily.todo_notice(None, "SUMMARY")  # sheet unreadable: still the summary, no claim about the to-do list
assert sent[-1] == ("NUauto morning", "SUMMARY"), sent[-1]
web.BASE_URL = ""  # no Mark-done page: no links
daily.todo_notice([row(3, "Applied", sheet.SITE_MARK + " ..."), row(4, "Needs Human", "External application ...")], "SUMMARY")
title, body = sent[-1]
lines = body.split("\n")
assert title == "NUauto morning" and lines[:2] == ["SUMMARY", "Only you can finish:"], sent[-1]
assert "Co3" in lines[2] and "Co4" in lines[3] and "Mark" not in body, body

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

# a failed scan (e.g. NUworks too slow) still sends the morning reminders, from the sheet + last saved pool
class Morning(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 10, 5, 8, 6)
daily.datetime = Morning
def slow(before):
    raise TimeoutError("Page.goto: Timeout 30000ms exceeded.")
def never(*a):
    raise AssertionError("hand-application check needs a fresh list; must not run after a failed scan")
daily.scan, daily.record_hand_applications = slow, never
daily.sheet_rows = lambda: [row(4, "Needs Human", "External application ...")]
daily.google_notice = lambda: 1 / 0  # one broken reminder must not stop the others
sent.clear()
assert daily.main() == 1
titles = [t for t, _ in sent]
assert titles[0] == "NUauto: daily update failed" and "TimeoutError" in sent[0][1], sent
assert "NUauto morning" in titles, sent
body = dict(sent)["NUauto morning"]
assert "(this morning's scan failed)" in body and "Co4" in body, body

# afternoon run: no reminders
class Evening(Morning):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 10, 5, 18, 6)
daily.datetime = Evening
sent.clear()
assert daily.main() == 1 and [t for t, _ in sent] == ["NUauto: daily update failed"], sent

print("All morning-summary checks passed.")
