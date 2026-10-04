"""Offline checks for daily.py's morning summary (scan counts + to-do). Run: python test_daily.py

Uses a temp data dir, temp log file and a stubbed notify: no sheet, no Discord, no network.
"""
import os
import tempfile
from datetime import datetime

import config

tmp = tempfile.mkdtemp()
config.DATA_DIR = os.path.join(tmp, "data")
config.LOGS_DIR = os.path.join(tmp, "logs")

import daily  # noqa: E402
import sheet  # noqa: E402
import web  # noqa: E402

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
assert sent[-1] == ("NUworks morning", "SUMMARY\nNothing only you need to finish."), sent[-1]
daily.todo_notice(None, "SUMMARY")  # sheet unreadable: still the summary, no claim about the to-do list
assert sent[-1] == ("NUworks morning", "SUMMARY"), sent[-1]
web.BASE_URL = ""  # no Mark-done page: no links
daily.todo_notice([row(3, "Applied", sheet.SITE_MARK + " ..."), row(4, "Needs Human", "External application ...")], "SUMMARY")
title, body = sent[-1]
lines = body.split("\n")
assert title == "NUworks morning" and lines[:2] == ["SUMMARY", "Only you can finish:"], sent[-1]
assert "Co3" in lines[2] and "Co4" in lines[3] and "Mark" not in body, body

print("All morning-summary checks passed.")
