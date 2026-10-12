"""Offline checks for sheet.py. Run: python test_sheet.py"""
from datetime import date

from nuauto import sheet
from nuauto.sheet import HEADERS, LimitReached, SheetError, parse_rows, check_limits

TODAY = date(2026, 10, 1)


def rows_with(statuses_dates):
    values = [HEADERS]
    for i, (status, d) in enumerate(statuses_dates):
        values.append([f"https://x/{i}", "Co", "Job", status, "", d])
    return parse_rows(values)


def expect(exc, fn):
    try:
        fn()
    except exc:
        return
    raise AssertionError(f"expected {exc.__name__}")


# blank row (the sheet's [[]] quirk) is skipped
assert parse_rows([HEADERS, []]) == []

# wrong headers refuse
expect(SheetError, lambda: parse_rows([["URL", "Oops"]]))

# unknown status refuses
expect(SheetError, lambda: rows_with([("Maybe", "")]))

# 7-day window: day 7 ago is outside, day 6 ago is inside
inside = [("Applied", "2026-09-26")] * (sheet.MAX_PER_WEEK - 1)
outside = [("Applied", "2026-09-24")] * 5
assert check_limits(rows_with(inside + outside), TODAY) == (sheet.MAX_PER_WEEK - 1, sheet.MAX_PER_WEEK + 4)

# fixed weeks from local_config week_start (2026-10-06 here); before that day: the last 7 days
from datetime import date as D  # noqa: E402
from nuauto import config  # noqa: E402
config.WEEK_START = D(2026, 10, 6)
days = ["2026-10-01", "2026-10-05", "2026-10-06", "2026-10-09", "2026-10-12", "2026-10-13"]
R = rows_with([("Applied", x) for x in days])
assert sheet.week_window(D(2026, 10, 5)) == (D(2026, 9, 29), "in the last 7 days")  # day before: rolling
assert check_limits(R, D(2026, 10, 5))[0] == 2                                      # Oct 1 + Oct 5
assert check_limits(R, D(2026, 10, 6))[0] == 1                                      # the new week starts at 0 (+ Oct 6)
assert check_limits(R, D(2026, 10, 12))[0] == 3                                     # Oct 6, 9, 12
assert sheet.week_window(D(2026, 10, 12))[0] == D(2026, 10, 6)
assert check_limits(R, D(2026, 10, 13))[0] == 1                                     # next week: Oct 13 only
assert sheet.week_window(D(2026, 10, 20)) == (D(2026, 10, 20), "this week (since Tue Oct 20)")
assert sheet.week_window(D(2026, 10, 6))[1] == "this week (since Tue Oct 6)"
expect(LimitReached, lambda: check_limits(rows_with([("Applied", "2026-10-07")] * sheet.MAX_PER_WEEK), D(2026, 10, 12)))
config.WEEK_START = None

# hitting the weekly limit refuses
expect(LimitReached, lambda: check_limits(rows_with([("Applied", "2026-09-30")] * sheet.MAX_PER_WEEK), TODAY))

# the weekly cap is a setting: 1..ceiling; anything else in the file falls back to the default
real = config.LOCAL
for value, want in [(None, sheet.MAX_PER_WEEK), (5, 5), (sheet.MAX_PER_WEEK_CEILING, sheet.MAX_PER_WEEK_CEILING),
                    (0, sheet.MAX_PER_WEEK), (-3, sheet.MAX_PER_WEEK), (sheet.MAX_PER_WEEK_CEILING + 1, sheet.MAX_PER_WEEK),
                    ("20", sheet.MAX_PER_WEEK), (True, sheet.MAX_PER_WEEK), (7.5, sheet.MAX_PER_WEEK)]:
    config.LOCAL = {**real, "max_per_week": value}
    assert sheet.max_per_week() == want, (value, sheet.max_per_week())
config.LOCAL = {**real, "max_per_week": 3}
expect(LimitReached, lambda: check_limits(rows_with([("Applied", "2026-09-30")] * 3), TODAY))
assert check_limits(rows_with([("Applied", "2026-09-30")] * 2), TODAY)[0] == 2
config.LOCAL = real

# Applied with no date refuses (no guessing)
expect(SheetError, lambda: check_limits(rows_with([("Applied", "")]), TODAY))

# only Approved rows are returned
assert [r.number for r in sheet.approved(rows_with([("Proposed", ""), ("Approved", ""), ("Failed", "")]))] == [3]

print("All checks passed.")


# --- submit safety, using an in-memory fake worksheet ---
class FakeWS:
    def __init__(self, values):
        self.values = values

    def get_all_values(self):
        return [list(r) for r in self.values]

    def update(self, range_name, values, value_input_option=None):
        row = int(range_name.split(":")[0][1:])
        self.values[row - 1][3:6] = values[0]


def fresh(status="Approved"):
    return FakeWS([HEADERS, ["https://x/1", "Co", "Job", status, "", ""]])


ws = fresh()
sheet.mark_submit_started(ws, 2)
assert ws.values[1][3] == "Needs Human" and ws.values[1][4].startswith(sheet.SUBMIT_MARK)
sheet.resolve_submit(ws, 2, "Applied", "confirmed")
assert ws.values[1][3] == "Applied" and ws.values[1][5] == date.today().strftime("%Y-%m-%d")

# resolve refuses on an ordinary Approved row, and on a Needs Human row without the marker
expect(SheetError, lambda: sheet.resolve_submit(fresh(), 2, "Applied"))
expect(SheetError, lambda: sheet.resolve_submit(fresh("Needs Human"), 2, "Applied"))

# mark_submit_started refuses on a row that is not Approved
expect(SheetError, lambda: sheet.mark_submit_started(fresh("Proposed"), 2))

# drop (not applying: closed, a duplicate, a bad location): Failed, the reason in front of Notes, the date kept; only on
# Approved / Needs Human rows still holding the job, never on an unresolved Submit click
ws = FakeWS([HEADERS, ["https://x/1", "Co", "Job", "Needs Human", "External application: x", ""]])
sheet.drop(ws, 2, "https://x/1", "duplicate")
assert ws.values[1][3:6] == ["Failed", f"Dropped {date.today():%Y-%m-%d}: a duplicate of another row. External application: x", ""]
assert sheet.approved(sheet.read_rows(ws)) == []
sheet.drop(fresh(), 2, "https://x/1", "nonsense")  # an unknown reason is "not applying"
expect(SheetError, lambda: sheet.drop(fresh("Applied"), 2, "https://x/1"))
expect(SheetError, lambda: sheet.drop(fresh("Proposed"), 2, "https://x/1"))
expect(SheetError, lambda: sheet.drop(fresh(), 2, "https://x/other"))
ws = fresh()
sheet.mark_submit_started(ws, 2)
expect(SheetError, lambda: sheet.drop(ws, 2, "https://x/1"))

# mark_on_laptop: the mark goes at the END of Notes (NUworks "External application" notes must keep starting the cell),
# only on Approved / Needs Human rows, once, and only on the row that still holds the job
from nuauto import assist  # noqa: E402


class CellWS(FakeWS):
    """update() writes the one cell it is given (E7 = column E, row 7), as Google does; every call is recorded."""
    def __init__(self, values):
        super().__init__(values)
        self.calls = []

    def update(self, range_name, values, value_input_option=None):
        self.calls.append((range_name, values, value_input_option))
        col = ord(range_name[0]) - ord("A")
        self.values[int(range_name[1:]) - 1][col] = values[0][0]


def laptop_ws(status="Needs Human", notes="External application: acme.example -> https://acme.example/j | match 80%", n=3):
    rows = [HEADERS] + [["https://x/%d" % i, "Co", "Job", "Approved", "", ""] for i in range(1, n)]
    return CellWS(rows + [["https://x/job", "Acme", "Intern", status, notes, "2026-10-01"]])


ws = laptop_ws()
sheet.mark_on_laptop(ws, 4, "https://x/job")
cell = ws.values[3][4]
assert cell == "External application: acme.example -> https://acme.example/j | match 80% | " + assist.LAPTOP_MARK, cell
assert cell.startswith("External application") and ws.values[3][3] == "Needs Human" and ws.values[3][5] == "2026-10-01"
assert ws.calls == [("E4", [[cell]], "RAW")] and ws.values[1][4] == ""                  # one cell, raw, only that row
sheet.mark_on_laptop(ws, 4, "https://x/job", "again")                                    # once: nothing more is written
sheet.mark_on_laptop(ws, 4, "https://x/job")
assert len(ws.calls) == 1 and ws.values[3][4] == cell and cell.count(assist.LAPTOP_MARK) == 1
ws = laptop_ws("Approved", "match 90%")                                                  # Approved rows (the Other jobs tab)
sheet.mark_on_laptop(ws, 4, "https://x/job", "the site showed it a sign-in page with no way in")
assert ws.values[3][4] == f"match 90% | {assist.LAPTOP_MARK} (the site showed it a sign-in page with no way in)" and ws.values[3][3] == "Approved"
ws = laptop_ws("Approved", "")                                                           # no notes: no stray separator
sheet.mark_on_laptop(ws, 4, "https://x/job")
assert ws.values[3][4] == assist.LAPTOP_MARK
ws = laptop_ws("Approved", "")
sheet.mark_on_laptop(ws, 4, "https://x/job", "why")
assert ws.values[3][4] == f"{assist.LAPTOP_MARK} (why)"
for status in ("Applied", "Failed", "Proposed"):                                         # other statuses: refused, nothing written
    ws = laptop_ws(status, "x")
    expect(SheetError, lambda: sheet.mark_on_laptop(ws, 4, "https://x/job"))
    assert ws.calls == [] and ws.values[3][4] == "x", status
ws = laptop_ws()
expect(SheetError, lambda: sheet.mark_on_laptop(ws, 4, "https://x/another-job"))        # the row now holds another job
expect(SheetError, lambda: sheet.mark_on_laptop(ws, 9, "https://x/job"))                 # no such row (shifted)
expect(SheetError, lambda: sheet.mark_on_laptop(ws, 2, "https://x/job"))                 # row 2 holds another job
assert ws.calls == []

# Google allows about 60 reads a minute: every client NUauto makes waits and retries when told to slow down (429)
import os  # noqa: E402
import tempfile  # noqa: E402
import gspread  # noqa: E402
from nuauto import config  # noqa: E402
made = []
real = (gspread.Client, gspread.oauth, config.TOKEN_PATH, config.DEMO, config.find_client_json)
config.find_client_json = lambda: "client_secret.json"  # (never read: gspread.oauth is a stand-in here)
tok = os.path.join(tempfile.mkdtemp(), "token.json")
with open(tok, "w") as f:
    f.write('{"token": "x", "refresh_token": "y", "client_id": "z", "client_secret": "w", "type": "authorized_user"}')
gspread.Client = lambda **kw: made.append(("Client", kw.get("http_client")))
gspread.oauth = lambda **kw: made.append(("oauth", kw.get("http_client")))
config.TOKEN_PATH, config.DEMO = tok, False
try:
    sheet.client(interactive=False)
    sheet.client(interactive=True)
finally:
    gspread.Client, gspread.oauth, config.TOKEN_PATH, config.DEMO, config.find_client_json = real
assert made == [("Client", gspread.BackOffHTTPClient), ("oauth", gspread.BackOffHTTPClient)], made

print("Submit-safety checks passed.")
