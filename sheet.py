"""Sheet module: read Approved rows, update Status/Notes/Date, enforce limits.

Run `python sheet.py status` to see the Approved rows and the weekly count.
"""
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import gspread
from google.auth.exceptions import RefreshError

import config

HEADERS = ["URL", "Company", "Title", "Status", "Notes", "Date"]
STATUSES = ["Proposed", "Approved", "Applied", "Failed", "Needs Human"]
MAX_PER_WEEK = 11  # raised from 8 on 2026-10-03 (user's decision)
MAX_TOTAL = 99     # CLAUDE.md says "under 100"
DATE_FMT = "%Y-%m-%d"

COL = {name: i + 1 for i, name in enumerate(HEADERS)}  # 1-based sheet columns


class SheetError(Exception):
    """Something about the sheet is unexpected. Stop and ask the human."""


class LimitReached(SheetError):
    pass


@dataclass
class Row:
    number: int  # 1-based sheet row
    url: str
    company: str
    title: str
    status: str
    notes: str
    date: str


def parse_rows(values):
    """Turn raw sheet values (header row first) into Row objects."""
    if not values or [c.strip() for c in values[0][: len(HEADERS)]] != HEADERS:
        raise SheetError(f"Row 1 must be exactly {HEADERS}. Did the sheet change?")
    rows = []
    for i, raw in enumerate(values[1:], start=2):
        cells = [c.strip() for c in raw] + [""] * (len(HEADERS) - len(raw))
        if not any(cells):
            continue
        row = Row(i, *cells[: len(HEADERS)])
        if row.status and row.status not in STATUSES:
            raise SheetError(f"Row {i}: unknown Status {row.status!r}.")
        rows.append(row)
    return rows


def applied_dates(rows):
    """Dates of all Applied rows. A missing or odd date is an error, not a guess."""
    dates = []
    for r in rows:
        if r.status != "Applied":
            continue
        try:
            dates.append(datetime.strptime(r.date, DATE_FMT).date())
        except ValueError:
            raise SheetError(f"Row {r.number}: Applied but Date {r.date!r} is not {DATE_FMT}.")
    return dates


def check_limits(rows, today=None):
    today = today or date.today()
    dates = applied_dates(rows)
    week = sum(1 for d in dates if today - timedelta(days=7) < d <= today)
    if week >= MAX_PER_WEEK:
        raise LimitReached(f"Weekly limit reached: {week} applied in the last 7 days (max {MAX_PER_WEEK}).")
    if len(dates) >= MAX_TOTAL:
        raise LimitReached(f"Total limit reached: {len(dates)} applied (max {MAX_TOTAL}).")
    return week, len(dates)


def approved(rows):
    return [r for r in rows if r.status == "Approved"]


def open_worksheet():
    if config.IS_SERVER and not os.path.exists(config.TOKEN_PATH):
        # no browser on the homelab: never start Google's login flow there (it would wait forever)
        raise SheetError("No Google token on the homelab. Log in on the laptop; the next nuauto command syncs it.")
    fresh = not os.path.exists(config.TOKEN_PATH)  # gspread will open the browser for a Google login
    gc = gspread.oauth(
        scopes=config.WRITE_SCOPES,
        credentials_filename=config.find_client_json(),
        authorized_user_filename=config.TOKEN_PATH,
    )
    config.lock_token()
    if fresh:
        with open(config.GOOGLE_LOGIN_PATH, "w") as f:
            f.write(date.today().isoformat() + "\n")
    try:
        return gc.open_by_key(config.SHEET_ID).sheet1
    except RefreshError:
        if config.IS_SERVER:
            raise SheetError("Google login expired. On the laptop run: nuauto login google")
        print(f"Google login expired (it lasts {config.GOOGLE_LOGIN_DAYS} days). Opening the browser to log in again...")
        os.remove(config.TOKEN_PATH)
        return open_worksheet()


def google_login_age():
    """Days since the last Google login, or None if unknown."""
    try:
        with open(config.GOOGLE_LOGIN_PATH) as f:
            return (date.today() - date.fromisoformat(f.read().strip())).days
    except (OSError, ValueError):
        return None


def google_login():
    """Fresh Google login now (resets the 7-day clock)."""
    if os.path.exists(config.TOKEN_PATH):
        os.remove(config.TOKEN_PATH)
    open_worksheet()
    print("Google login done.")


def read_rows(ws):
    return parse_rows(ws.get_all_values())


def set_status(ws, row_number, status, notes=None, applied_date=None):
    """Move an Approved row to Applied / Failed / Needs Human.

    Re-reads the row first so we only ever change a row that is still Approved.
    Setting Applied re-checks the limits and always stamps today's date.
    """
    if status not in ("Applied", "Failed", "Needs Human"):
        raise SheetError(f"set_status only moves rows to Applied, Failed or Needs Human, not {status!r}.")
    rows = read_rows(ws)
    current = next((r for r in rows if r.number == row_number), None)
    if current is None or current.status != "Approved":
        raise SheetError(f"Row {row_number} is not Approved. Refusing to change it.")
    if status == "Applied":
        check_limits(rows)
        applied_date = applied_date or date.today()
    ws.update(
        range_name=f"D{row_number}:F{row_number}",
        values=[[
            status,
            notes if notes is not None else current.notes,
            applied_date.strftime(DATE_FMT) if applied_date else current.date,
        ]],
        value_input_option="RAW",
    )


SUBMIT_MARK = "SUBMIT CLICKED - confirm the outcome on NUworks"


def mark_submit_started(ws, row_number):
    """Call right BEFORE clicking Submit. If anything dies afterwards, the row
    reads Needs Human instead of Approved, so it can never be sent twice."""
    set_status(ws, row_number, "Needs Human", notes=f"{SUBMIT_MARK} ({datetime.now():%Y-%m-%d %H:%M})")


def resolve_submit(ws, row_number, status, notes=""):
    """After a Submit click: move a marked row to Applied / Failed / Needs Human.
    No limit check here: the application has already gone out."""
    if status not in ("Applied", "Failed", "Needs Human"):
        raise SheetError(f"Cannot resolve a submit to {status!r}.")
    current = next((r for r in read_rows(ws) if r.number == row_number), None)
    if current is None or current.status != "Needs Human" or not current.notes.startswith(SUBMIT_MARK):
        raise SheetError(f"Row {row_number} is not waiting on a submit result. Refusing to change it.")
    ws.update(
        range_name=f"D{row_number}:F{row_number}",
        values=[[status, notes, date.today().strftime(DATE_FMT) if status == "Applied" else ""]],
        value_input_option="RAW",
    )


SITE_MARK = "ALSO APPLY ON COMPANY SITE"   # Notes prefix on Applied rows whose company site still needs an application


def _row_for(ws, row_number, url):
    """The row, but only if it still holds this job (rows can shift if someone edits the sheet)."""
    current = next((r for r in read_rows(ws) if r.number == row_number), None)
    if current is None or current.url != url:
        raise SheetError(f"Row {row_number} no longer holds that job. Refusing to change it.")
    return current


def mark_site_done(ws, row_number, url):
    """Applied row whose company-site application is now done: replace the SITE_MARK note."""
    current = _row_for(ws, row_number, url)
    if current.status != "Applied" or not current.notes.startswith(SITE_MARK):
        raise SheetError(f"Row {row_number} is not waiting on a company-site application.")
    rest = current.notes.split("). ", 1)[1] if "). " in current.notes else ""
    ws.update(range_name=f"E{row_number}", values=[[f"Company site: done {date.today():%Y-%m-%d}. {rest}".strip()]],
              value_input_option="RAW")


def mark_applied_by_hand(ws, row_number, url, how="by hand"):
    """You applied yourself (e.g. an external job that went to Needs Human): Applied, dated today.
    No limit check: the application has already gone out."""
    current = _row_for(ws, row_number, url)
    if current.status == "Applied":
        raise SheetError(f"Row {row_number} is already Applied.")
    ws.update(range_name=f"D{row_number}:F{row_number}",
              values=[["Applied", f"Applied {how}. {current.notes}".strip(), date.today().strftime(DATE_FMT)]],
              value_input_option="RAW")


def add_proposed(ws, items, status="Proposed"):
    """Add suggested jobs as Proposed (or, with status, Approved) rows below the last used row. Never overwrites,
    skips URLs already in the sheet. items: dicts with url, company, title, notes."""
    values = ws.get_all_values()
    existing = {r.url for r in parse_rows(values)}
    new = [i for i in items if i["url"] not in existing]
    if not new:
        return 0
    start, end = len(values) + 1, len(values) + len(new)
    if any(c.strip() for row in ws.get(f"A{start}:F{end}") for c in row):
        raise SheetError(f"Rows {start}-{end} are not empty. Refusing to write.")
    ws.update(
        range_name=f"A{start}:F{end}",
        values=[[i["url"], i["company"], i["title"], status, i["notes"], ""] for i in new],
        value_input_option="RAW",
    )
    return len(new)


def main():
    if sys.argv[1:] != ["status"]:
        sys.exit("Usage: python sheet.py status")
    rows = read_rows(open_worksheet())
    week, total = check_limits_safe(rows)
    print(f"Applied in last 7 days: {week}/{MAX_PER_WEEK}. Total applied: {total}/{MAX_TOTAL}.")
    for r in approved(rows):
        print(f"  row {r.number}: {r.company} | {r.title} | {r.url}")
    if not approved(rows):
        print("No Approved rows.")


def check_limits_safe(rows):
    """Counts for display only; does not raise when a limit is hit."""
    dates = applied_dates(rows)
    today = date.today()
    return sum(1 for d in dates if today - timedelta(days=7) < d <= today), len(dates)


if __name__ == "__main__":
    main()
