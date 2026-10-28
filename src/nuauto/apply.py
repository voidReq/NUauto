"""Apply to every Approved row, one at a time. Marking a row Approved is the go-ahead:
there is no per-job prompt. Run it through the `nuauto` command:

  nuauto apply          # every Approved row, up to the weekly limit: closing within 7 days
                         # first (soonest first), then best score first
  nuauto apply -n 3     # at most 3 this run
  nuauto apply --order featured,age,score   # another order (--help lists them and the steps)

Hidden dev flags: --dry-run (fill and screenshot, never submit), --row N.

A row becomes Applied after NUworks confirms. If the script has to stop (external
site, unknown field, ...) the row becomes Needs Human with the reason in Notes and
the run moves on. Unexpected errors, an expired session or an unclear submit stop the run.
A row being re-applied with the new resume (sheet.mark_reapply): its first application is withdrawn on NUworks (My Job
Applications), then it is sent again; it keeps its first date and goes even past the limits.
"""
import argparse
import contextlib
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

from nuauto import answers
from nuauto import browser
from nuauto import config
from nuauto import jobs
from nuauto import sheet
from nuauto.inspect_form import FIELDS_JS

APPLY_NAME = re.compile(r"^\s*apply\s*$", re.IGNORECASE)
APPLY_WAIT_MS = 15000  # NUworks draws the job page with JavaScript after it loads; a slow one showed no button yet
BETWEEN = (10, 20)    # seconds between two applications, at random (was 30-60 until 2026-10-23, my call)


def apply_button(page):
    """The job page's Apply button(s), once the page has drawn one (up to APPLY_WAIT_MS); count() alone does not wait."""
    btn = page.get_by_role("button", name=APPLY_NAME)
    try:
        btn.first.wait_for(state="visible", timeout=APPLY_WAIT_MS)
    except PlaywrightTimeout:
        pass  # none showed up: the caller's count check says so
    return btn


class NeedsHuman(Exception):
    """Stop, mark the row Needs Human with this reason."""


class SessionExpired(Exception):
    """NUworks is showing the sign-in page. Not the row's fault: leave the sheet alone."""


class NotWithdrawn(Exception):
    """A row being re-applied (sheet.reapplying) whose first application NUworks still has (withdrawing it didn't work, or
    a dry run): skip it, the row stays."""


def still_applied(page, row):
    """NUworks still has your application to this row's job (its job record, read-only): not withdrawn yet."""
    i = jobs.job_id(row.url)
    resp = page.request.get(f"{jobs.HOST}/api/v3/jobs/{i}")
    try:
        raw = resp.json() if resp.ok else None
    except Exception:
        raw = None
    if not raw or raw.get("job_id") != i:
        raise NotWithdrawn("Couldn't ask NUworks whether you withdrew it; try again later.")
    return bool(raw.get("applied"))


APPLICATIONS_URL = f"{jobs.HOST}/students/index.php?s=jobs&ss=applied&mode=list&subtab=nocr"  # My Job Applications
APPLICATION_ITEMS = "ul.list_job_applications > li"
WITHDRAW_CONFIRM = "Are you sure you want to withdraw this application?"  # NUworks' own confirm() (its confirmWithdraw)
# The application ids of the items whose job link is this job (each item has its Withdraw link twice: phone and desktop).
WITHDRAW_IDS_JS = r"""(id) => [...document.querySelectorAll('ul.list_job_applications > li')]
  .filter(li => { const a = li.querySelector('.list-item-title a');
                  return a && (a.getAttribute('href') || '').replace(/\/+$/, '').endsWith('/jobs/detail/' + id); })
  .map(li => [...new Set([...li.querySelectorAll('a[onclick*="confirmWithdraw"]')]
                .map(a => ((a.getAttribute('onclick') || '').match(/confirmWithdraw\('([0-9a-f]+)'\)/) || [])[1]).filter(Boolean))])"""


def find_application(page, row, log):
    """This row's application on My Job Applications (its pages, 20 a page): its id for NUworks' Withdraw. NotWithdrawn
    unless exactly one item is this job and it has exactly one Withdraw."""
    page.goto(APPLICATIONS_URL, wait_until="domcontentloaded")
    page.wait_for_selector(APPLICATION_ITEMS, timeout=30000)
    key = re.search(r"_so_list_from([0-9a-f]{32})", page.content())
    total = re.search(r"of (\d+) results", page.inner_text("body"))
    starts = range(0, int(total.group(1)), 20) if key and total else [None]
    found = []
    for start in starts:
        if start is not None:  # (the list remembers its page per session: every page is asked for by number)
            page.goto(f"{APPLICATIONS_URL}&_so_list_from{key.group(1)}={start}"
                      f"&_so_list_from{key.group(1)}_page={start // 20 + 1}", wait_until="domcontentloaded")
            page.wait_for_selector(APPLICATION_ITEMS, timeout=30000)
        found += page.evaluate(WITHDRAW_IDS_JS, jobs.job_id(row.url))
        if found:
            break
        browser.pause(page, 1, 2)
    if len(found) != 1:
        raise NotWithdrawn(f"My Job Applications lists this job {len(found)} times: not withdrawn.")
    if len(found[0]) != 1:
        raise NotWithdrawn("Its application on NUworks has no Withdraw (the posting may have closed): not withdrawn.")
    log.write(f"Found the application on My Job Applications (id {found[0][0]}).")
    return found[0][0]


def withdraw_first(page, row, log):
    """A row being re-applied: withdraw its first application on NUworks (My Job Applications: its Withdraw link and
    NUworks' own confirm), then make sure NUworks no longer has it. Only in a real run (you there, a terminal or the
    GUI); never on a row whose application isn't found exactly once."""
    app_id = find_application(page, row, log)
    link = page.locator(f"a[onclick*=\"confirmWithdraw('{app_id}')\"]:visible").first
    said = []

    def on_dialog(d):
        said.append(d.message)
        d.accept() if d.message.strip() == WITHDRAW_CONFIRM else d.dismiss()
    page.once("dialog", on_dialog)
    log.screenshot(page, "before_withdraw")
    log.write(f"Withdrawing the first application (re-apply with the new resume): {row.company} | {row.title}")
    try:
        with page.expect_navigation(timeout=20000):
            link.click(timeout=10000)
    except PlaywrightTimeout:
        pass  # no page load (the confirm was dismissed?): what NUworks has is checked below
    browser.pause(page, 2, 3)
    log.screenshot(page, "after_withdraw")
    if said and said[0].strip() != WITHDRAW_CONFIRM:
        raise NotWithdrawn(f"NUworks asked {said[0]!r}, not its usual withdraw question: not withdrawn.")
    for _ in range(3):
        if not still_applied(page, row):
            log.write("NUworks no longer has the first application: withdrawn.")
            return
        browser.pause(page, 2, 4)
    raise NotWithdrawn("Clicked Withdraw, but NUworks still has the application: check My Job Applications.")


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


# The orders a run can go in (`--order`, the GUI's Sort menu on the NUworks tab): the list there IS the run's order.
# An order is a list of steps: rows are compared on the first step, a tie goes to the next, and so on (a row missing a
# step's value comes after those that have it; the last ties stay in sheet order). Steps:
STEPS = {"urgent": "closing within a week first", "featured": "Featured first", "age": "by posting week",
         "newest": "newest posting first", "score": "best score", "match": "best match %", "closes": "closing soonest",
         "pay": "best pay per hour"}
# Named orders (the Sort menu; `--order <name>`); any other order is its steps with commas: `--order featured,age,score`.
ORDERS = {"default": ("closing within a week first, then score", ["urgent", "score"]),
          "score": ("score (match + bonuses)", ["score"]), "match": ("match %", ["match"]),
          "closes": ("closing soonest", ["closes"]), "pay": ("pay per hour", ["pay"]),
          "featured": ("Featured first, then score", ["featured", "score"]),
          "newest": ("newest posting first", ["newest", "score"])}
AGE_WEEKS = (7, 14, 21)  # "age": under 1 week, under 2, under 3, older (the Apply rows' green / yellow / grey chips)


def order_steps(order):
    """An order's steps: a named one (ORDERS) or steps with commas. ValueError for anything else."""
    if order in ORDERS:
        return list(ORDERS[order][1])
    steps = [x.strip() for x in str(order or "").split(",") if x.strip()]
    bad = [x for x in steps if x not in STEPS]
    if not steps or bad or len(set(steps)) != len(steps):
        raise ValueError(f"Unknown order {order!r}: one of {', '.join(ORDERS)}, or steps with commas "
                         f"({', '.join(STEPS)}), each once.")
    return steps


def apply_order(rows, order="default"):
    """The rows in this order (order_steps). urgent: closing within jobs.URGENT_DAYS first, soonest first (real postings
    often close early once they find someone). featured: NUworks' Featured Posting tag at the last update (data/list.json).
    age: under 1, 2, 3 weeks since posting, then older. newest: by posting day. score: pool rank (match + bonuses).
    pay: the top of the hourly range (insights.hourly). A row missing the value (not in the pool, not scored, no
    deadline, no posting day, pay unclear) comes after the rest at that step; ties in sheet order."""
    steps = order_steps(order)
    rank = {r["id"]: r["rank"] for r in jobs.load("pool.json", [])}
    scores = jobs.load("scores.json", {}) if "match" in steps else {}
    listed = jobs.load("list.json", {}) if {"featured", "age", "newest"} & set(steps) else {}
    today = date.today()
    from nuauto import insights

    def step(x, r):  # (0, value) sorts before (1, 0): the value, smaller first; (1, 0) = missing
        i = jobs.job_id(r.url)
        if x == "urgent":
            day = row_closes(r)
            return (0, day.toordinal()) if day is not None and (day - today).days <= jobs.URGENT_DAYS else (1, 0)
        if x == "featured":
            return (0, 0) if (listed.get(i) or {}).get("featured") else (1, 0)
        if x in ("age", "newest"):
            day = jobs.posted(listed.get(i))
            if day is None:
                return (1, 0)
            return (0, -day.toordinal()) if x == "newest" else (0, sum((today - day).days >= w for w in AGE_WEEKS))
        if x == "closes":
            day = row_closes(r)
            return (0, day.toordinal()) if day is not None else (1, 0)
        if x == "score":
            v = rank.get(i)
        elif x == "match":
            v = (scores.get(i) or {}).get("match")
        else:  # pay
            hour = insights.hourly(row_details(r).get("pay"))
            v = hour[1] if hour else None
        return (0, -v) if v is not None else (1, 0)  # best first
    return sorted(rows, key=lambda r: tuple(step(x, r) for x in steps) + (r.number,))


def queued(path):
    """The rows nuauto gui queued behind this run (each row's Apply while it runs), in the order you clicked them.
    The GUI owns the file; it takes a row out once the run starts it."""
    if not path:
        return []
    try:
        return [int(n) for n in json.loads(open(path).read())]
    except (OSError, ValueError, TypeError):
        return []


def more_queued(args, rows, seen):
    """The queued rows still Approved and not handled in this run."""
    approved = {r.number for r in sheet.approved(rows)}
    return [n for n in queued(args.queue) if n in approved and n not in seen]


def pick_row(rows, row_number, order="default"):
    candidates = apply_order(sheet.approved(rows), order)
    if row_number is not None:
        candidates = [r for r in candidates if r.number == row_number]
        if not candidates:
            sys.exit(f"Row {row_number} is not an Approved row.")
    if not candidates:
        sys.exit("No Approved rows.")
    return candidates[0]


SUBMIT_RESULT = [("u", "Not sure"), ("y", "Yes, NUworks confirmed it"), ("n", "No, it failed")]  # first = safest


def fill_popup(page, context, row, resume_label, entries, io, log, blocked, company_site_done=False, withdraw=False):
    """Open the Apply popup and select the resume. Returns the dialog locator. withdraw: a row being re-applied whose
    first application NUworks still has is withdrawn first (a real run; else it is skipped)."""
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
    if sheet.reapplying(row) and still_applied(page, row):
        if not withdraw:
            raise NotWithdrawn("NUworks still has your first application (a dry run never withdraws).")
        withdraw_first(page, row, log)
        if blocked:
            raise NeedsHuman(f"Withdrawing led off the allowed domain: {blocked}")
        page.goto(row.url, wait_until="domcontentloaded")
        browser.pause(page)

    btn = apply_button(page)
    if btn.count() != 1:
        log.screenshot(page, "no_apply_button")
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


def submit_flow(page, dialog, ws, row, resume_label, log, state, io):
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
        result = io.menu("Did NUworks confirm the submission? [y = yes / n = it failed / u = unsure]: ", SUBMIT_RESULT, "u")
    if result == "y":
        hint = None if sheet.reapplying(row) else jobs.external_hint(row_details(row))  # (re-applied: done the first time)
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


# Written to Notes BEFORE the NUworks-side Submit click, so a crash or kill after the click still leaves it and
# `nuauto assist nuworks <row>` refuses to submit a second time (assist.nuworks_side_blocked).
NUWORKS_CLICK_MARK = "NUworks side: Submit clicked, result unknown; check NUworks."


def submit_nuworks_side(row, ws, io=None):
    """The company-site application is submitted and the row is Applied (nuauto assist): submit the same
    job on NUworks too, with the same checks as apply_one. The outcome goes into Notes; the row stays
    Applied (same job, so the weekly count does not change). Returns the note."""
    log = browser.RunLog(f"row{row.number}_nuworks_side")
    try:
        browser.lock_profile("nuauto assist (NUworks side)")  # launch() below then reuses this lock
    except browser.ProfileBusy as e:  # another run has the NUworks browser: nothing opened, the row goes on the retry list
        note = (f"NUworks side NOT submitted: {str(e).rstrip('.')}: Apply > Company sites, or `nuauto assist "
                f"nuworks {row.number}`.")
        log.write(note)
        sheet.append_note(ws, row.number, row.url, note)
        return note
    resume_label = load_resume_label()
    entries = answers.load()
    io = io or (answers.TerminalIO() if sys.stdin.isatty() else answers.NoTerminalIO())
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
            sheet.append_note(ws, row.number, row.url, NUWORKS_CLICK_MARK)
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
            dialog = fill_popup(page, context, row, resume_label, entries, io, log, blocked, withdraw=args.submit)
            if args.submit:
                submit_flow(page, dialog, ws, row, resume_label, log, state, io)
                if state.get("unresolved"):
                    outcome = "stop"
            else:
                log.write("DRY RUN: stopped before Submit. Row stays Approved.")
        except NeedsHuman as e:
            reason = str(e)
        except NotWithdrawn as e:
            log.write(f"SKIP: {e} Row {row.number} stays Approved (to be re-applied).")
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


def refuse_unattended(ui):
    """Real submits need you there: a terminal you are at, or `nuauto gui` (it started this process after your Start,
    also in a window an agent opened for you). Never an agent's own command: Claude Code sets CLAUDECODE in every
    shell it runs. Demo mode submits nothing real."""
    if config.DEMO:
        return
    if ui == "json":  # a child of the running GUI (its pid in gui.lock), whoever opened its window
        if not answers.json_ui_allowed():
            sys.exit("--ui json is only for nuauto gui.")
    elif os.environ.get("CLAUDECODE"):
        sys.exit("This submits real applications, so it never runs inside a Claude Code session. "
                 "Run it yourself (a terminal or `nuauto gui`); agents use `nuauto gui --demo`.")
    elif not sys.stdin.isatty():
        sys.exit("apply submits real applications; run it yourself in a terminal.")


def order_arg(value):
    try:
        order_steps(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e))
    return value


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", type=int, metavar="N", help="apply to at most N jobs this run")
    ap.add_argument("--order", type=order_arg, default="default", metavar="ORDER",
                    help=("the order to go in: " + "; ".join(f"{k} = {v[0]}" for k, v in ORDERS.items()) +
                          ". Or your own, steps with commas (featured,age,score): " +
                          "; ".join(f"{k} = {v}" for k, v in STEPS.items())).replace("%", "%%"))
    ap.add_argument("--row", type=int, help=argparse.SUPPRESS)
    ap.add_argument("--dry-run", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--ui", choices=["json"], help=argparse.SUPPRESS)  # questions as JSON lines, for nuauto gui
    ap.add_argument("--queue", help=argparse.SUPPRESS)  # (nuauto gui) a file of rows to do next, added to while this runs
    args = ap.parse_args(argv)
    args.submit = not args.dry_run
    if args.submit:
        refuse_unattended(args.ui)
        from nuauto import assist
        if assist.nuworks_session():  # one application at a time on NUworks
            sys.exit(assist.NUWORKS_BUSY)

    resume_label = load_resume_label()
    entries = answers.load()
    io = answers.JsonIO() if args.ui == "json" else answers.TerminalIO() if sys.stdin.isatty() else answers.NoTerminalIO()
    browser.RunLog.events = io.event
    ws = sheet.open_worksheet()
    from nuauto import remote
    try:  # one machine submits on NUworks at a time (with NUauto on the homelab, both can run Apply)
        with remote.nuworks_turn() if args.submit else contextlib.nullcontext():
            run_rows(args, ws, resume_label, entries, io)
    except remote.NuworksBusy as e:
        sys.exit(f"Not now: {e}")


def run_rows(args, ws, resume_label, entries, io):
    """The run: Approved rows in order (or just --row), the limits checked again before each one. Past a limit only rows
    being re-applied go on (sheet.reapplying: the same applications, sending them again adds nothing)."""
    seen = set()  # rows already handled in this run (a row you answered "n" to stays Approved)
    first = True
    while True:
        rows = sheet.read_rows(ws)
        try:
            week, total = sheet.check_limits(rows)
            limit = None
        except sheet.LimitReached as e:
            week, total = sheet.check_limits_safe(rows)
            limit = e
        pool = rows if limit is None else [r for r in rows if sheet.reapplying(r)]
        print(f"Applied {sheet.week_window()[1]}: {week}/{sheet.max_per_week()}. Total: {total}/{sheet.MAX_TOTAL}.")
        if first:
            if limit is not None and not [r for r in sheet.approved(pool) if args.row in (None, r.number)]:
                sys.exit(f"Refusing to run: {limit}")
            if limit is not None:
                print(f"{limit} Only rows being re-applied go.")
            row = pick_row(pool, args.row, args.order)
        else:
            approved = {r.number: r for r in sheet.approved(pool) if r.number not in seen}
            ahead = more_queued(args, pool, seen)  # queued rows first, in the order you queued them
            rest = [] if args.row is not None or (args.n and len(seen) >= args.n) else apply_order(
                [r for n, r in approved.items() if n not in ahead], args.order)
            left = [approved[n] for n in ahead] + rest
            if not left:
                print(f"Stopping: {limit}" if limit is not None else "No more Approved rows.")
                break
            row = left[0]
        first = False
        seen.add(row.number)
        day = row_closes(row)
        if day is not None and day < date.today():
            print(f"--- Row {row.number}: {row.company} | {row.title}: deadline passed ({day}). Needs Human.")
            io.event("row_done", row=row.number, outcome="deadline")
            sheet.set_status(ws, row.number, "Needs Human", notes=f"deadline passed ({day}); not attempted")
            if args.row is not None and not more_queued(args, rows, seen):
                break
            continue
        print(f"--- Row {row.number}: {row.company} | {row.title} | closes {jobs.closes_text(day)}")
        io.event("row", row=row.number, company=row.company, title=row.title)
        outcome = apply_one(row, ws, args, resume_label, entries, io)
        io.event("row_done", row=row.number, outcome=outcome)
        last = args.row is not None or (args.n and len(seen) >= args.n)
        if outcome == "stop" or last and not more_queued(args, rows, seen):
            break
        wait = random.uniform(*BETWEEN) * config.DEMO_PACE  # DEMO_PACE is 1 outside demo mode
        io.event("wait", seconds=round(wait))
        time.sleep(wait)  # slow, human-like pacing between applications


if __name__ == "__main__":
    main()
