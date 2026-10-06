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

print("Submit-safety checks passed.")
