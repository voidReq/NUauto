"""Offline checks for sheet.py. Run: python test_sheet.py"""
from datetime import date

import sheet
from sheet import HEADERS, LimitReached, SheetError, parse_rows, check_limits

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

# hitting the weekly limit refuses
expect(LimitReached, lambda: check_limits(rows_with([("Applied", "2026-09-30")] * sheet.MAX_PER_WEEK), TODAY))

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
