"""Add, move and remove sheet rows by hand (the GUI's Sheet screen, or `nuauto sheet add|move|remove`).

  nuauto sheet add <nuworks|other> <url> [<company> <title>] [--proposed]
  nuauto sheet move <nuworks|other> <row> [<new url>]     the row goes to the other tab
  nuauto sheet remove <nuworks|other> <row>

Tabs: nuworks = the main tab (NUworks co-ops: a NUworks job page link), other = the Other jobs tab (an https link
that is not NUworks). Added rows are Approved (that is the go-ahead, as in Review) unless --proposed.
Adding a NUworks job looks it up in your job data (data/): company, title and the match % come from there, and the
job leaves the Review queue (it is in the sheet now). A NUworks job that is not in your data needs the company and
title typed, and its Notes say so.
Rows are never deleted: a removed row's cells are cleared, so every other row keeps its number (a run in progress,
a Mark link or an open GUI list can never land on a different job). Applied rows are never removed or moved (they
count toward the weekly and total limits), nor a row whose Submit click is still unresolved.
"""
import re
import sys
from datetime import date
from urllib.parse import urlparse

from nuauto import config
from nuauto import jobs
from nuauto import sheet

TABS = {"nuworks": "NUworks", "other": sheet.OTHER_TAB}
ADD_STATUSES = ("Approved", "Proposed")
JOB_PATH = re.compile(r"/students/app/jobs/detail/([0-9A-Za-z]+)")


def nuworks_id(url):
    """The job id of a NUworks job page link (https, the NUworks host, /students/app/jobs/detail/<id>), else None."""
    u = urlparse(str(url).strip())
    m = JOB_PATH.fullmatch(u.path.rstrip("/"))
    return m.group(1) if u.scheme == "https" and u.hostname in config.ALLOWED_HOSTS and m else None


def check_url(tab, url):
    """(the link as the sheet stores it, None) if it belongs in that tab, else (None, why)."""
    from nuauto import assist
    url = str(url or "").strip()
    if tab == "nuworks":
        i = nuworks_id(url)
        if i:
            return jobs.job_url(i), None
        if assist.check_link(url)[0]:
            return None, "the NUworks tab takes NUworks job links only (…/students/app/jobs/detail/<id>); this one goes in Other jobs"
        return None, f"not a NUworks job link: {url!r} (copy it from the job's page on NUworks)"
    if tab == "other":
        return assist.check_link(url)
    return None, f"unknown tab {tab!r}"


def lookup(url):
    """What the add form shows for a link: the tab it belongs in, and for a NUworks job what your data knows."""
    i = nuworks_id(url)
    if not i:
        ok, why = check_url("other", url)
        return {"tab": "other" if ok else None, "why": why}
    out = {"tab": "nuworks", "url": jobs.job_url(i), "known": False}
    try:
        d = jobs.load(f"details/{i}.json", None)
    except ValueError:
        d = None
    if not d:
        return out
    score = jobs.load("scores.json", {}).get(i)
    entry = next((r for r in jobs.load("pool.json", []) if r["id"] == i), None)
    out.update(known=True, company=d["company"], title=d["title"], match=score["match"] if score else None,
               in_pool=entry is not None)
    if entry:
        out["notes"] = jobs.sheet_item(entry)["notes"]
    elif score:
        keep, threshold, _, why = jobs.hard_rules(d)
        why = why if not keep else f"below its {threshold}% bar" if score["match"] < threshold else "not in the latest list"
        out["notes"] = f"match {score['match']}%; not in your pool ({why}); added by hand"
    else:
        out["notes"] = "not scored yet; added by hand"
    return out


def open_tab(tab, interactive=True, create=False):
    return sheet.open_other(interactive, create=create) if tab == "other" else sheet.open_worksheet(interactive)


def add(tab, url, company="", title="", status="Approved", interactive=True):
    """A job added by hand -> that tab (the Other jobs tab is made if missing). Returns its row number.
    SheetError if the link does not belong in that tab or is already in the sheet."""
    if status not in ADD_STATUSES:
        raise sheet.SheetError(f"Added jobs are {' or '.join(ADD_STATUSES)}, not {status!r}.")
    url, why = check_url(tab, url)
    if why:
        raise sheet.SheetError(why[0].upper() + why[1:] + ".")
    company, title, notes = str(company or "").strip(), str(title or "").strip(), ""
    if tab == "nuworks":
        info = lookup(url)
        company, title = company or info.get("company", ""), title or info.get("title", "")
        notes = info.get("notes") or "not in your job data; added by hand"
    if not company or not title:
        raise sheet.SheetError("A company and a job title, please.")
    ws = open_tab(tab, interactive, create=True)
    rows = sheet.read_rows(ws)
    if any(r.url == url for r in rows):
        raise sheet.SheetError(f"That job is already in the {TABS[tab]} tab (row "
                               f"{next(r.number for r in rows if r.url == url)}).")
    sheet.add_proposed(ws, [{"url": url, "company": company, "title": title, "notes": notes}], status=status)
    return next(r.number for r in sheet.read_rows(ws) if r.url == url)


def movable(row, tab="nuworks", action="move"):
    """None if this row of tab may be moved (action "move") or removed ("remove"), else why not.
    An Applied row never leaves the NUworks tab (that would hide it from the limits) and is never removed; an Applied
    Other job may move to the NUworks tab (it then counts toward the limits there: the safe direction)."""
    if row.status == "Applied":
        if tab == "other" and action == "move":
            return None
        if tab == "other":
            return "it is Applied (your record that you applied; you can move it to the NUworks tab)"
        return "it is Applied (it counts toward your weekly and total limits)"
    if row.status == "Needs Human" and row.notes.startswith(sheet.SUBMIT_MARK):
        return "its Submit click is still unresolved: check NUworks first"
    return None


def _take(ws, number, url, tab, action):
    current = sheet._row_for(ws, number, url)
    why = movable(current, tab, action)
    if why:
        raise sheet.SheetError(f"Row {number} can't be changed: {why}.")
    return current


def clear(ws, number, url):
    """Empty the row's cells (the row stays, so no other row changes number)."""
    sheet._row_for(ws, number, url)
    ws.update(range_name=f"A{number}:F{number}", values=[[""] * len(sheet.HEADERS)], value_input_option="RAW")


def remove(tab, number, url, interactive=True):
    """Remove a job from the sheet (its cells are cleared). Only while the row still holds this job."""
    ws = open_tab(tab, interactive)
    _take(ws, number, url, tab, "remove")
    clear(ws, number, url)


def move(tab, number, url, new_url=None, interactive=True):
    """Move a row to the other tab: status, notes and date go with it (plus a note saying where it came from), then the
    old row is cleared. new_url: its link in the new tab (a NUworks job link for the NUworks tab, the company's
    posting for Other jobs). Returns (the other tab, its new row number)."""
    to = "other" if tab == "nuworks" else "nuworks"
    src = open_tab(tab, interactive)
    current = _take(src, number, url, tab, "move")
    dest_url, why = check_url(to, new_url or current.url)
    if why:
        raise sheet.SheetError(why[0].upper() + why[1:] + ".")
    dst = open_tab(to, interactive, create=True)
    rows = sheet.read_rows(dst)
    if any(r.url == dest_url for r in rows):
        raise sheet.SheetError(f"That job is already in the {TABS[to]} tab.")
    was = f", was {current.url}" if dest_url != current.url else ""
    note = f"Moved from the {TABS[tab]} tab (row {number}{was}) {date.today():%Y-%m-%d}."
    sheet.add_proposed(dst, [{"url": dest_url, "company": current.company, "title": current.title,
                              "notes": f"{current.notes} {note}".strip()}], status=current.status or "Proposed")
    new = next(r for r in sheet.read_rows(dst) if r.url == dest_url)
    if current.date:
        dst.update(range_name=f"F{new.number}", values=[[current.date]], value_input_option="RAW")
    clear(src, number, url)
    return to, new.number


USAGE = "\n\n".join(__doc__.split("\n\n")[:2])


def main(argv):
    proposed = "--proposed" in argv
    argv = [a for a in argv if a != "--proposed"]
    if len(argv) < 3 or argv[0] not in ("add", "move", "remove") or argv[1] not in TABS:
        sys.exit(USAGE)
    cmd, tab, rest = argv[0], argv[1], argv[2:]
    try:
        if cmd == "add" and len(rest) in (1, 3):
            n = add(tab, *rest, status="Proposed" if proposed else "Approved")
            return print(f"Added as {TABS[tab]} row {n} ({'Proposed' if proposed else 'Approved'}).")
        if cmd in ("move", "remove") and rest[0].isdigit() and len(rest) <= (2 if cmd == "move" else 1):
            n = int(rest[0])
            row = next((r for r in sheet.read_rows(open_tab(tab)) if r.number == n), None)
            if row is None:
                sys.exit(f"{TABS[tab]} row {n} is empty.")
            what = f"{TABS[tab]} row {n}: {row.company} | {row.title} ({row.status or 'no status'})"
            why = movable(row, tab, cmd)
            if why:
                sys.exit(f"{what}\nCan't be changed: {why}.")
            verb = "Move it to the other tab" if cmd == "move" else "Remove it from the sheet"
            if cmd == "move" and row.status == "Applied":
                verb += " (in the NUworks tab it counts toward your weekly and total limits)"
            if input(f"{what}\n{verb}? [y/N]: ").strip().lower() != "y":
                return print("Nothing changed.")
            if cmd == "remove":
                remove(tab, n, row.url)
                return print("Removed (the row's cells are cleared; other rows keep their numbers).")
            to, m = move(tab, n, row.url, rest[1] if len(rest) > 1 else None)
            return print(f"Moved to {TABS[to]} row {m}.")
    except sheet.SheetError as e:
        sys.exit(str(e))
    except (KeyboardInterrupt, EOFError):
        sys.exit("\nNothing changed.")
    sys.exit(USAGE)
