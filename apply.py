"""Apply to every Approved row, one at a time. Marking a row Approved is the go-ahead:
there is no per-job prompt. Run it through the `nuauto` command:

  nuauto apply          # every Approved row, up to the weekly limit: closing within 7 days
                         # first (soonest first), then best match first
  nuauto apply -n 3     # at most 3 this run

Hidden dev flags: --dry-run (fill and screenshot, never submit), --row N.

A row becomes Applied after NUworks confirms. If the script has to stop (external
site, unknown field, ...) the row becomes Needs Human with the reason in Notes and
the run moves on. Unexpected errors, an expired session or an unclear submit stop the run.
"""
import argparse
import json
import os
import random
import re
import sys
import time
from datetime import date
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

import answers
import browser
import config
import jobs
import sheet
from inspect_form import FIELDS_JS

APPLY_NAME = re.compile(r"^\s*apply\s*$", re.IGNORECASE)


class NeedsHuman(Exception):
    """Stop, mark the row Needs Human with this reason."""


class SessionExpired(Exception):
    """NUworks is showing the sign-in page. Not the row's fault: leave the sheet alone."""


def load_resume_label():
    with open(config.PROFILE_PATH) as f:
        label = json.load(f).get("resume_label", "").strip()
    if not label:
        sys.exit("profile.json has no resume_label.")
    return label


def unwrap(url):
    """Real target of an Outlook "safelinks" redirect (employers paste them from email); other URLs as is."""
    u = urlparse(url)
    if u.hostname and u.hostname.endswith("safelinks.protection.outlook.com"):
        return parse_qs(u.query).get("url", [url])[0]
    return url


def required_labels(popup_text):
    """Labels marked required ("Cover Letter *") in the popup text; a lone "*" line joins the line before."""
    lines = [l.strip() for l in popup_text.splitlines() if l.strip()]
    out = []
    for i, l in enumerate(lines):
        if l == "*" and i > 0:
            out.append(lines[i - 1] + " *")
        elif l.endswith("*") and len(l) > 1:
            out.append(l)
    return [star(l) for l in out]


def star(label):
    """"Cover Letter*", "Cover Letter  *" -> "cover letter *" (for comparing labels)."""
    return re.sub(r"\s*\*$", " *", label.strip()).lower()


def check_popup(popup_text, links, fields, resume_label, company_site_done=False):
    """Raise NeedsHuman if the popup links off-site, has no usable Resume dropdown, or requires something
    that is not a form field we can fill (e.g. Cover Letter / Transcript pickers: "Add a new cover letter").
    company_site_done: the company-site application is already submitted (nuauto assist), so an off-site
    link in the popup is no reason to stop; every other check still applies."""
    links = [unwrap(u) for u in links]
    if not company_site_done and (links or "how to apply" in popup_text.lower()):
        hosts = sorted({re.sub(r"^https?://([^/]+).*", r"\1", u) for u in links}) or ["(no link)"]
        raise NeedsHuman(f"External application: {', '.join(hosts)}" + (f" -> {links[0]}" if links else ""))
    resume = [f for f in fields if is_resume(f)]
    if len(resume) != 1:
        raise NeedsHuman("Expected exactly one Resume dropdown.")
    if resume_label not in [o.strip() for o in resume[0]["options"]]:
        raise NeedsHuman(f"Resume {resume_label!r} is not in the dropdown: {resume[0]['options']}")
    known = {star(f["label"]) for f in fields}
    missing = [l.rstrip(" *").title() for l in required_labels(popup_text) if l not in known]
    if missing:
        raise NeedsHuman(f"Popup requires {', '.join(missing)}: attach by hand on NUworks.")


def is_resume(field):
    return field["tag"] == "select" and field["label"].strip().lower() == "resume *"


def resume_locator(dialog):
    """Locator for the Resume dropdown, found fresh each time (field handles are re-numbered per scan)."""
    found = [f for f in dialog.evaluate(FIELDS_JS) if is_resume(f)]
    if len(found) != 1:
        raise NeedsHuman("Expected exactly one Resume dropdown.")
    return dialog.locator(f'[data-fill-idx="{found[0]["idx"]}"]')


def shown_resume(dialog):
    return resume_locator(dialog).locator("option:checked").inner_text().strip()


TEXT_TYPES = {"text", "email", "tel", "url", "number"}


def fill_extra_fields(page, dialog, entries, io, log):
    """Fill every non-resume field that is still empty, from the answer bank (asking when needed).
    Free text and field types we have not seen yet stop the run. Fields NUworks pre-filled are left alone."""
    noted = set()
    for _ in range(20):  # re-scan: filling a field can reveal new ones
        pending = []
        for f in dialog.evaluate(FIELDS_JS):
            if is_resume(f):
                continue
            if f["has_value"]:
                if f["label"] not in noted:
                    noted.add(f["label"])
                    log.write(f"  {f['label']!r} already has a value (pre-filled by NUworks); left as is")
                continue
            pending.append(f)
        if not pending:
            return
        f = pending[0]
        label = f["label"].strip()
        if not label:
            raise NeedsHuman(f"Field with no label [{f['tag']}/{f['type']}].")
        if f["tag"] == "textarea":
            raise NeedsHuman(f"Free-text field {label!r}: needs you.")
        is_select = f["tag"] == "select"
        if not is_select and not (f["tag"] == "input" and f["type"] in TEXT_TYPES):
            raise NeedsHuman(f"Unsupported field {label!r} [{f['tag']}/{f['type']}].")
        field_type = "select" if is_select else "text"
        try:
            answer = answers.resolve_answer(entries, label, field_type, f["options"], io)
        except answers.Stop as e:
            raise NeedsHuman(f"No answer for {label!r}: {e}")
        target = dialog.locator(f'[data-fill-idx="{f["idx"]}"]')
        log.write(f"Filling {label!r} [{field_type}] with {answer!r}")
        if is_select:
            target.select_option(label=answer)
        else:
            target.fill(answer)
        browser.pause(page, 1, 2)
        shown = target.locator("option:checked").inner_text() if is_select else target.input_value()
        if shown.strip() != answer.strip():
            raise NeedsHuman(f"{label!r} shows {shown!r} after filling with {answer!r}.")
    raise NeedsHuman("Too many form fields; stopping.")


def row_details(row):
    """Saved NUworks job data for a sheet row, or {} (unknown job)."""
    path = os.path.join(config.DATA_DIR, "details", f"{jobs.job_id(row.url)}.json")
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def row_closes(row):
    """Closing date from the saved NUworks job data, or None (unknown job / no deadline)."""
    d = row_details(row)
    return jobs.closes(d) if d else None


def apply_order(rows):
    """Jobs closing within jobs.URGENT_DAYS go first, soonest first. The rest go best match first
    (pool rank: match + bonuses), since real postings often close early once they find someone.
    Rows not in the pool go last, in sheet order."""
    rank = {r["id"]: r["rank"] for r in jobs.load("pool.json", [])}
    today = date.today()

    def key(r):
        day = row_closes(r)
        if day is not None and (day - today).days <= jobs.URGENT_DAYS:
            return (0, day, 0, r.number)
        rk = rank.get(jobs.job_id(r.url))
        return (1, date.max, -rk, r.number) if rk is not None else (2, date.max, 0, r.number)
    return sorted(rows, key=key)


def pick_row(rows, row_number):
    candidates = apply_order(sheet.approved(rows))
    if row_number is not None:
        candidates = [r for r in candidates if r.number == row_number]
        if not candidates:
            sys.exit(f"Row {row_number} is not an Approved row.")
    if not candidates:
        sys.exit("No Approved rows.")
    return candidates[0]


def ask(prompt, allowed):
    """Terminal question. Anything not in `allowed` (including EOF) counts as the first, safest answer."""
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        answer = ""
    return answer if answer in allowed else allowed[0]


def fill_popup(page, context, row, resume_label, entries, io, log, blocked, company_site_done=False):
    """Open the Apply popup and select the resume. Returns the dialog locator."""
    if not browser.host_allowed(row.url) or "/jobs/detail/" not in row.url:
        raise NeedsHuman("URL is not a NUworks job page on an allowed host.")
    log.write(f"Row {row.number}: {row.company} | {row.title}")
    page.goto(row.url, wait_until="domcontentloaded")
    browser.pause(page)
    if blocked:
        raise NeedsHuman("Redirected off the allowed domain (session expired? run `nuauto login`).")

    if browser.on_login_page(page):
        if not browser.goto_logged_in(page, context, row.url, log):
            raise SessionExpired("NUworks is showing the sign-in page. Run `nuauto login` again.")

    btn = page.get_by_role("button", name=APPLY_NAME)
    if btn.count() != 1:
        raise NeedsHuman(f"Expected one Apply button, found {btn.count()}.")
    if btn.is_disabled():
        raise NeedsHuman("Apply button is disabled.")
    btn.click(timeout=10000)
    browser.pause(page, 2, 4)
    if blocked:
        raise NeedsHuman(f"Apply led off the allowed domain: {blocked}")

    dialog = page.get_by_role("dialog")
    if dialog.count() != 1:
        raise NeedsHuman(f"Expected one popup, found {dialog.count()}.")
    popup_text = dialog.inner_text()
    links = dialog.evaluate("d => [...d.querySelectorAll('a[href]')].map(a => a.href)")
    fields = dialog.evaluate(FIELDS_JS)
    for f in fields:
        log.write(f"  field {f['label']!r} [{f['tag']}/{f['type']}] options={f['options']}")
    check_popup(popup_text, links, fields, resume_label, company_site_done)

    log.write(f"Selecting resume {resume_label!r}")
    resume_locator(dialog).select_option(label=resume_label)
    browser.pause(page, 1, 2)
    if shown_resume(dialog) != resume_label:
        raise NeedsHuman("Resume dropdown does not show the chosen resume after selecting.")

    fill_extra_fields(page, dialog, entries, io, log)
    if shown_resume(dialog) != resume_label:
        raise NeedsHuman("Resume selection changed while filling other fields.")
    log.screenshot(page, "filled_popup")
    return dialog


def submit_flow(page, dialog, ws, row, resume_label, log, state):
    """Click Submit (the Approved status is the go-ahead), then confirm the result."""
    log.write(f"Screenshot: {log.dir}/filled_popup.png")
    submit_btn = dialog.get_by_role("button", name="Submit", exact=True)
    if submit_btn.count() != 1:
        raise NeedsHuman(f"Expected one Submit button, found {submit_btn.count()}.")
    if not submit_btn.is_enabled():  # NUworks still wants something; checked before the row is marked
        raise NeedsHuman("Submit button is disabled: the popup still wants something (see filled_popup.png).")
    # Re-check the selection is still right at the moment of clicking.
    if shown_resume(dialog) != resume_label:
        raise NeedsHuman("Resume selection changed before submit.")

    sheet.mark_submit_started(ws, row.number)  # from here on the row can never be sent twice
    state["submit_started"] = True
    log.write("Clicking Submit.")
    submit_btn.click(timeout=10000)
    browser.pause(page, 3, 5)
    log.write(f"Popup still open after submit: {dialog.count() > 0}")
    log.screenshot(page, "after_submit")

    try:
        page.get_by_text("Your application has been submitted").first.wait_for(timeout=20000)
        confirmed = True
        log.write("NUworks page says: Your application has been submitted.")
    except PlaywrightTimeout:
        confirmed = False
        log.write("Did not see NUworks' confirmation text within 20s.")

    if confirmed:
        result = "y"
    else:
        result = ask("Did NUworks confirm the submission? [y = yes / n = it failed / u = unsure]: ", ["u", "y", "n"])
    if result == "y":
        hint = jobs.external_hint(row_details(row))
        note = "Submitted via apply.py" + (" (confirmed by NUworks page)" if confirmed else " (confirmed by you)")
        if hint:
            note = f"{sheet.SITE_MARK} (posting: \"" + hint[:150].replace("). ", ") ") + "\"). " + note
        sheet.resolve_submit(ws, row.number, "Applied", note)
        log.write(f"Row {row.number} marked Applied.")
        if hint:
            log.write(f"!!! {row.company} also wants an application on their own site. Posting says: {hint}")
            log.write(f"!!! Open the job and follow its link: {row.url}")
    elif result == "n":
        sheet.resolve_submit(ws, row.number, "Failed", "Submit clicked but you reported it failed")
        log.write(f"Row {row.number} marked Failed.")
    else:
        log.write(f"Row {row.number} left as Needs Human ({sheet.SUBMIT_MARK}).")
        state["unresolved"] = True


def submit_nuworks_side(row, ws):
    """The company-site application is submitted and the row is Applied (nuauto assist): submit the same
    job on NUworks too, with the same checks as apply_one. The outcome goes into Notes; the row stays
    Applied (same job, so the weekly count does not change). Returns the note."""
    log = browser.RunLog(f"row{row.number}_nuworks_side")
    resume_label = load_resume_label()
    entries, io = answers.load(), (answers.TerminalIO() if sys.stdin.isatty() else answers.NoTerminalIO())
    blocked, clicked, note = [], False, None
    with sync_playwright() as p:
        context = browser.launch(p)
        try:
            browser.install_domain_lock(context, log, blocked)
            page = context.pages[0] if context.pages else context.new_page()
            dialog = fill_popup(page, context, row, resume_label, entries, io, log, blocked, company_site_done=True)
            submit_btn = dialog.get_by_role("button", name="Submit", exact=True)
            if submit_btn.count() != 1:
                raise NeedsHuman(f"Expected one Submit button, found {submit_btn.count()}.")
            if not submit_btn.is_enabled():
                raise NeedsHuman("Submit button is disabled: the popup still wants something (see filled_popup.png).")
            if shown_resume(dialog) != resume_label:
                raise NeedsHuman("Resume selection changed before submit.")
            log.write("Clicking Submit (NUworks side).")
            clicked = True
            submit_btn.click(timeout=10000)
            browser.pause(page, 3, 5)
            log.screenshot(page, "after_submit")
            try:
                page.get_by_text("Your application has been submitted").first.wait_for(timeout=20000)
                note = "NUworks side submitted too (confirmed by NUworks page)."
            except PlaywrightTimeout:
                note = "NUworks side: Submit clicked but no confirmation seen; check NUworks."
        except (NeedsHuman, SessionExpired) as e:
            note = f"NUworks side NOT submitted: {e}"
        except KeyboardInterrupt:
            note = "NUworks side: stopped by Ctrl+C " + ("after Submit was clicked; check NUworks." if clicked else "before Submit; not submitted.")
        except Exception as e:
            note = f"NUworks side: unexpected {type(e).__name__} " + ("after Submit was clicked; check NUworks." if clicked else "before Submit; not submitted.")
        finally:
            try:
                context.close()
            except Exception:
                pass
    log.write(note)
    sheet.append_note(ws, row.number, row.url, note)
    return note


def apply_one(row, ws, args, resume_label, entries, io):
    """Run one Approved row. Returns "next" (carry on with the next row) or "stop" (halt the run)."""
    log = browser.RunLog(f"row{row.number}")
    blocked = []
    state = {"submit_started": False}
    reason = None
    outcome = "next"
    with sync_playwright() as p:
        context = browser.launch(p)
        try:
            browser.install_domain_lock(context, log, blocked)
            page = context.pages[0] if context.pages else context.new_page()
            dialog = fill_popup(page, context, row, resume_label, entries, io, log, blocked)
            if args.submit:
                submit_flow(page, dialog, ws, row, resume_label, log, state)
                if state.get("unresolved"):
                    outcome = "stop"
            else:
                log.write("DRY RUN: stopped before Submit. Row stays Approved.")
        except NeedsHuman as e:
            reason = str(e)
        except SessionExpired as e:
            log.write(f"STOP: {e} Sheet left unchanged (row stays Approved).")
            outcome = "stop"
        except KeyboardInterrupt:
            if state["submit_started"]:
                log.write("Ctrl+C after Submit was clicked: row is Needs Human. Check NUworks, then fix the row by hand.")
            else:
                log.write("Ctrl+C: stopping. Nothing was submitted; row stays Approved.")
            outcome = "stop"
        except Exception as e:
            reason = f"Unexpected {type(e).__name__}: {str(e).splitlines()[0][:150]}"
            outcome = "stop"  # something we did not plan for: never carry on to the next job
        finally:
            try:
                context.close()
            except Exception:
                pass
    if reason:
        log.write(f"STOP: {reason}")
        if state["submit_started"]:
            log.write("Submit was already clicked. Row stays Needs Human; check NUworks and update the row by hand.")
            outcome = "stop"
        else:
            sheet.set_status(ws, row.number, "Needs Human", notes=reason)
            log.write(f"Row {row.number} marked Needs Human.")
    return outcome


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", type=int, metavar="N", help="apply to at most N jobs this run")
    ap.add_argument("--row", type=int, help=argparse.SUPPRESS)
    ap.add_argument("--dry-run", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    args.submit = not args.dry_run
    if args.submit and not sys.stdin.isatty():
        sys.exit("apply submits real applications; run it yourself in a terminal.")

    resume_label = load_resume_label()
    entries = answers.load()
    io = answers.TerminalIO() if sys.stdin.isatty() else answers.NoTerminalIO()
    ws = sheet.open_worksheet()
    seen = set()  # rows already handled in this run (a row you answered "n" to stays Approved)
    first = True
    while True:
        rows = sheet.read_rows(ws)
        try:
            week, total = sheet.check_limits(rows)
        except sheet.LimitReached as e:
            if first:
                sys.exit(f"Refusing to run: {e}")
            print(f"Stopping: {e}")
            break
        print(f"Applied in last 7 days: {week}/{sheet.MAX_PER_WEEK}. Total: {total}/{sheet.MAX_TOTAL}.")
        if first:
            row = pick_row(rows, args.row)
        else:
            left = apply_order([r for r in sheet.approved(rows) if r.number not in seen])
            if not left:
                print("No more Approved rows.")
                break
            row = left[0]
        first = False
        seen.add(row.number)
        day = row_closes(row)
        if day is not None and day < date.today():
            print(f"--- Row {row.number}: {row.company} | {row.title}: deadline passed ({day}). Needs Human.")
            sheet.set_status(ws, row.number, "Needs Human", notes=f"deadline passed ({day}); not attempted")
            if args.row is not None:
                break
            continue
        print(f"--- Row {row.number}: {row.company} | {row.title} | closes {jobs.closes_text(day)}")
        outcome = apply_one(row, ws, args, resume_label, entries, io)
        if outcome == "stop" or args.row is not None or (args.n and len(seen) >= args.n):
            break
        time.sleep(random.uniform(30, 60))  # slow, human-like pacing between applications


if __name__ == "__main__":
    main()
