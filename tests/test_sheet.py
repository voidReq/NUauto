"""Offline checks for sheet.py. Run: python test_sheet.py"""
from datetime import date
import os
import tempfile

os.environ["NUAUTO_STATE_DIR"] = tempfile.mkdtemp(prefix="nuauto-test-sheet-")  # never the real local/ (its limits, its accounts)

from nuauto import sheet  # noqa: E402
from nuauto.sheet import HEADERS, LimitReached, SheetError, parse_rows, check_limits  # noqa: E402

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

# what you heard back (Accepted / Rejected) still counts: those applications went out (the week, and the total)
R2 = rows_with([("Applied", "2026-09-30"), ("Accepted", "2026-09-30"), ("Rejected", "2026-09-29"), ("Rejected", "2026-09-10")])
assert check_limits(R2, TODAY, seen={}) == (3, 4)
expect(SheetError, lambda: check_limits(rows_with([("Rejected", "")]), TODAY, seen={}))  # a sent row needs its date
# the total counts what NUworks has: a sent row NUworks says it has no application for (a company-site one whose
# NUworks side never went out) doesn't count, but only while the row is as it was when NUworks was asked
R3 = parse_rows([HEADERS, ["https://n/jobs/detail/a", "Co", "Job", "Applied", "NUworks side NOT submitted yet", "2026-09-30"],
                 ["https://n/jobs/detail/b", "Co", "Job", "Applied", "", "2026-09-30"],
                 ["https://n/jobs/detail/c", "Co", "Job", "Rejected", "", "2026-09-28"]])
fp = {r.url.rsplit("/", 1)[1]: sheet.fingerprint(r) for r in R3}
seen = {"a": {"applied": False, "fingerprint": fp["a"]}, "b": {"applied": True, "fingerprint": fp["b"]},
        "c": {"applied": False, "fingerprint": "an earlier version of the row"}}
assert sheet.not_on_nuworks(R3, seen) == {2} and sheet.total_count(R3, seen) == 2
assert check_limits(R3, TODAY, seen=seen) == (3, 2)                       # the week still counts all three
# a Rejected row NUworks still has an application for is one you haven't withdrawn there: listed until it is
on = {"c": {"applied": True, "fingerprint": fp["c"]}}
assert [r.number for r in sheet.rejected_not_withdrawn(R3, on)] == [4]
on["c"]["applied"] = False
assert sheet.rejected_not_withdrawn(R3, on) == [] and sheet.rejected_not_withdrawn(R3, {}) == []   # withdrawn / never asked
# an Applied row whose job looks filled (its Hiring Status, or it stopped interviewing) while NUworks has the application
jobs_now = {"b": {"applied": True, "fingerprint": fp["b"], "interviewing_ended": "t2"},
            "c": {"applied": True, "fingerprint": fp["c"], "filled_since": "t2"}}   # (c is Rejected: not listed)
assert [(r.number, why) for r, why in sheet.maybe_filled(R3, jobs_now)] == [(3, "stopped interviewing")]
jobs_now["b"]["filled_since"] = "t3"
assert [(r.number, why) for r, why in sheet.maybe_filled(R3, jobs_now)] == [(3, "filled")]
jobs_now["b"]["applied"] = False                                          # withdrawn: nothing left to do
assert sheet.maybe_filled(R3, jobs_now) == [] and sheet.maybe_filled(R3, {}) == []
assert sheet.maybe_filled(R3, {"b": {"applied": True, "fingerprint": "older", "filled_since": "t3"}}) == []
R3[0].notes += " NUworks side submitted."                                 # it changed since NUworks was asked: it counts
assert sheet.total_count(R3, seen) == 3 and sheet.total_count(R3, {}) == 3  # (never asked: counts)
many = rows_with([("Applied", "2026-09-01")] * sheet.MAX_TOTAL)
gone = {str(i): {"applied": False, "fingerprint": sheet.fingerprint(r)} for i, r in enumerate(many[:3])}
expect(LimitReached, lambda: check_limits(many, TODAY, seen={}))
assert check_limits(many, TODAY, seen=gone)[1] == sheet.MAX_TOTAL - 3   # three of them never reached NUworks

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

# set_outcome: Accepted / Rejected (or back to Applied) on a sent row only; the date stays (it went out that day), a
# note says when it was marked; mark_applied_by_hand refuses a sent row
ws = FakeWS([HEADERS, ["https://x/1", "Co", "Job", "Applied", "Applied by hand.", "2026-10-01"]])
sheet.set_outcome(ws, 2, "https://x/1", "Rejected")
assert ws.values[1][3:6] == ["Rejected", f"Rejected (marked {date.today():%Y-%m-%d}). Applied by hand.", "2026-10-01"], ws.values[1]
sheet.set_outcome(ws, 2, "https://x/1", "Applied")
assert ws.values[1][3] == "Applied" and ws.values[1][5] == "2026-10-01"
sheet.set_outcome(ws, 2, "https://x/1", "Accepted")
assert ws.values[1][3] == "Accepted"
expect(SheetError, lambda: sheet.set_outcome(fresh(), 2, "https://x/1", "Rejected"))          # not sent: Approved
expect(SheetError, lambda: sheet.set_outcome(ws, 2, "https://x/other", "Rejected"))           # another job now
expect(SheetError, lambda: sheet.set_outcome(ws, 2, "https://x/1", "Failed"))                 # not an outcome
expect(SheetError, lambda: sheet.mark_applied_by_hand(ws, 2, "https://x/1"))                   # Accepted: sent already

# re-apply with the new resume (sheet.mark_reapply, decided 2026-10-28): an Applied NUworks row goes back to Approved, its
# first date kept; it still counts toward both limits exactly once (also while stopped on the way: Needs Human), Apply
# sends it even past the limits and it ends Applied with its first date; Undo puts it back as it was
NU = "https://northeastern-csm.symplicity.com/students/app/jobs/detail/abc"
today = date.today().strftime("%Y-%m-%d")
ws = FakeWS([HEADERS, [NU, "Co", "Job", "Applied", "Submitted via apply.py", "2026-10-03"]])
before = (sheet.applied_dates(sheet.read_rows(ws)), sheet.total_count(sheet.read_rows(ws), seen={}))
sheet.mark_reapply(ws, 2, NU)
assert ws.values[1][3:6] == ["Approved", f"{sheet.REAPPLY_MARK} (first sent 2026-10-03). Submitted via apply.py", "2026-10-03"]
r = sheet.read_rows(ws)
assert sheet.reapplying(r[0]) and sheet.approved(r) == r
assert (sheet.applied_dates(r), sheet.total_count(r, seen={})) == before
expect(SheetError, lambda: sheet.mark_reapply(ws, 2, NU))                # not Applied any more
sheet.undo_reapply(ws, 2, NU)
assert ws.values[1][3:6] == ["Applied", "Submitted via apply.py", "2026-10-03"], ws.values[1]
expect(SheetError, lambda: sheet.undo_reapply(ws, 2, NU))                # nothing to undo
sheet.mark_reapply(ws, 2, NU)
sheet.mark_submit_started(ws, 2)                                         # sent again: the mark and the date stay...
assert ws.values[1][3] == "Needs Human" and ws.values[1][5] == "2026-10-03" and sheet.reapplying(sheet.read_rows(ws)[0])
assert sheet.total_count(sheet.read_rows(ws), seen={}) == 1
sheet.resolve_submit(ws, 2, "Applied", "confirmed")                      # ...and it ends Applied, first date kept
assert ws.values[1][3:6] == ["Applied", f"Re-applied {today} with the new resume (first sent 2026-10-03). confirmed",
                             "2026-10-03"], ws.values[1]
sheet.mark_reapply(ws, 2, NU)
sheet.set_status(ws, 2, "Needs Human", notes="Free-text field 'Why us?': needs you.")  # stopped on the way: still counted
assert sheet.reapplying(sheet.read_rows(ws)[0]) and ws.values[1][5] == "2026-10-03"
assert ws.values[1][4].startswith("Free-text field") and sheet.applied_dates(sheet.read_rows(ws)) == before[0]
sheet.mark_applied_by_hand(ws, 2, NU, how="by hand (marked in NUauto)")  # you sent it yourself: still its first date
assert ws.values[1][3] == "Applied" and ws.values[1][5] == "2026-10-03" and ws.values[1][4].startswith("Re-applied ")
expect(SheetError, lambda: sheet.mark_reapply(FakeWS([HEADERS, ["https://jobs.example.net/1", "Co", "Job", "Applied", "",
                                                                "2026-10-03"]]), 2, "https://jobs.example.net/1"))  # not NUworks
expect(SheetError, lambda: sheet.mark_reapply(FakeWS([HEADERS, [NU, "Co", "Job", "Rejected", "", "2026-10-03"]]), 2, NU))
expect(SheetError, lambda: sheet.mark_reapply(FakeWS([HEADERS, [NU, "Co", "Job", "Applied", "", "soon"]]), 2, NU))  # no date
other = FakeWS([HEADERS, [NU, "Co", "Job", "Applied", "", "2026-10-03"]])
other.title = sheet.OTHER_TAB
expect(SheetError, lambda: sheet.mark_reapply(other, 2, NU))             # the Other jobs tab has no NUworks applications
# not re-applied where the last NUworks check says the job has moved on (sheet.reapply_refused): marking refuses it
seen = {"abc": {"applied": True, "expired": False, "interviewing": True, "hiring": "Available"}}
row = sheet.Row(2, NU, "Co", "Job", "Applied", "", "2026-10-03")
assert sheet.reapply_refused(row, seen) == "the employer is interviewing"
assert sheet.reapply_refused(row, {"abc": {**seen["abc"], "interviewing": False}}) is None
assert sheet.reapply_refused(row, {"abc": {**seen["abc"], "interviewing": False, "hiring": "Filled"}}) == "its Hiring Status is Filled"
assert sheet.reapply_refused(row, {"abc": {**seen["abc"], "interviewing": False, "expired": True}}) == "its posting has closed"
assert sheet.reapply_refused(row, {}) is None                              # never asked: Apply checks it live anyway
saved_seen = sheet.nuworks_seen
sheet.nuworks_seen = lambda: seen
try:
    expect(SheetError, lambda: sheet.mark_reapply(FakeWS([HEADERS, [NU, "Co", "Job", "Applied", "", "2026-10-03"]]), 2, NU))
finally:
    sheet.nuworks_seen = saved_seen
from nuauto import manage  # noqa: E402
assert manage.movable(sheet.Row(2, NU, "Co", "Job", "Approved", f"{sheet.REAPPLY_MARK} (first sent 2026-10-03).", "2026-10-03"))

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
assert made == [("Client", sheet.HTTPClient), ("oauth", sheet.HTTPClient)], made
assert issubclass(sheet.HTTPClient, gspread.BackOffHTTPClient)

# The opened sheet and its tabs are kept (OPEN_FOR s): a read is one trip to Google, not three. Rows are always fresh.
import time  # noqa: E402
opens = []


class Creds:
    valid = True


class FakeWs:
    def __init__(self, title):
        self.title, self.values = title, [HEADERS]

    def get_all_values(self):
        return self.values


class FakeBook:
    def __init__(self):
        self.client = type("C", (), {"auth": Creds()})()
        self.tabs = [FakeWs("Jobs"), FakeWs(sheet.OTHER_TAB)]

    @property
    def sheet1(self):
        opens.append("sheet1")
        return self.tabs[0]

    def worksheet(self, title):
        opens.append("worksheet")
        return next(t for t in self.tabs if t.title == title)


BOOK = FakeBook()
real_client = sheet.client
sheet.client = lambda interactive=True: type("G", (), {"open_by_key": lambda self, key: (opens.append("book"), BOOK)[1]})()
config.TOKEN_PATH = tok
sheet.forget()
try:
    ws = sheet.open_worksheet(interactive=False)
    assert sheet.open_worksheet(interactive=False) is ws and sheet.open_other(interactive=False).title == sheet.OTHER_TAB
    sheet.open_other(interactive=False)
    assert opens == ["book", "sheet1", "worksheet"], opens               # opened once each
    ws.values = [HEADERS, ["https://x/1", "Co", "Job", "Approved", "", ""]]
    assert [r.status for r in sheet.read_rows(sheet.open_worksheet(interactive=False))] == ["Approved"]  # fresh rows
    BOOK.tabs.reverse()  # Other jobs dragged to the first place: the kept main tab is still the NUworks one
    assert sheet.open_worksheet(interactive=False).title == "Jobs"
    BOOK.tabs.reverse()
    opens.clear()
    Creds.valid = False  # the access token is about to expire: open again (renewing it happens in spreadsheet())
    sheet.open_worksheet(interactive=False)
    assert opens == ["book", "sheet1"], opens
    Creds.valid = True
    opens.clear()
    with open(tok, "a") as f:  # a new Google login: open again
        f.write(" ")
    sheet.open_worksheet(interactive=False)
    assert opens == ["book", "sheet1"], opens
    opens.clear()
    sheet.OPEN["at"] = time.monotonic() - sheet.OPEN_FOR - 1  # kept too long
    sheet.open_other(interactive=False)
    assert opens == ["book", "worksheet"], opens

    # Google refuses a request as unknown (a tab renamed or deleted): the kept sheet is forgotten
    class Refused(gspread.BackOffHTTPClient):
        def request(self, *a, **k):
            raise gspread.exceptions.APIError(type("R", (), {"json": lambda self: {"error": {"code": self.code, "message": "x"}},
                                                             "code": Refused.code, "text": ""})())
    real_request = gspread.BackOffHTTPClient.request
    gspread.BackOffHTTPClient.request = Refused.request
    try:
        for code, forgets in ((429, False), (400, True)):
            Refused.code = code
            sheet.open_worksheet(interactive=False)
            expect(gspread.exceptions.APIError, lambda: sheet.HTTPClient.request(object.__new__(sheet.HTTPClient), "get", "u"))
            assert bool(sheet.OPEN) != forgets, code
    finally:
        gspread.BackOffHTTPClient.request = real_request

    # a kept tab that was renamed: read_rows opens it again, once
    old = sheet.open_worksheet(interactive=False)
    BOOK.tabs[0] = FakeWs("Jobs")
    BOOK.tabs[0].values = [HEADERS, ["https://x/2", "Co", "Job", "Applied", "", ""]]

    def gone():
        sheet.forget()
        raise gspread.exceptions.APIError(type("R", (), {"json": lambda self: {"error": {"code": 400, "message": "range"}},
                                                         "text": ""})())
    old.get_all_values = gone
    assert [r.status for r in sheet.read_rows(old)] == ["Applied"]
finally:
    sheet.client, config.TOKEN_PATH = real_client, real[2]
    sheet.forget()

print("Submit-safety checks passed.")
