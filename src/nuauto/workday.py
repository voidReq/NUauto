"""Your application statuses on Workday, read from each company's Candidate Home (read-only). Many companies mark you
"Not Selected" there and never email. For every Workday login in local/accounts.json (the ones the assistant made and
your own you imported) it signs in, opens Candidate Home and reads My Applications (Active and Inactive), then lists
what each says. For a rejection it can tie to one sent row of the sheet, it asks y/n to mark that row Rejected. A
Rejected NUworks row still counts toward the total until you withdraw it on NUworks (My Job Applications).

  nuauto workday                 every Workday login, one site after another (a visible browser)
  nuauto workday <text>          only the logins whose site or company contains <text>
  nuauto workday --hidden        no window

Read-only on Workday: it fills only the sign-in form, with that site's own saved login, on that site's own address
(may_sign_in; never on accounts.NEVER), clicks only Sign In and the Inactive tab, never Withdraw. One sign-in try per
site (no lockouts). A fresh private browser per site: nothing is kept on disk. Passwords are never printed or logged.
Log and screenshots: logs/<stamp>_workday/.
"""
import json
import re
import sys
from urllib.parse import urlparse

from nuauto import accounts

HOSTS = ("myworkdayjobs.com", "myworkdaysite.com")
# What a status says (Workday's words differ per company: "Not Selected", "No longer under consideration", "Inactive")
WITHDRAWN = re.compile(r"withdr[ae]wn?", re.I)
REJECTED = re.compile(r"not selected|no longer (being )?(under )?consider|not (been )?moving forward|not been selected|"
                      r"unsuccessful|\brejected\b", re.I)
CLOSED = re.compile(r"\binactive\b|\bclosed\b|\bfilled\b|cancel+ed|no longer available|expired", re.I)
ACTIVE = re.compile(r"submitted|in progress|under (review|consideration)|review|interview|screen|assessment|offer|"
                    r"\bactive\b|received|considered", re.I)
STOP_PARTS = {"job", "details", "apply", "userhome", "login", "jobs"}
LANG = re.compile(r"^[a-z]{2}(-[A-Z]{2})?$")  # en, en-US (not a site named "AB")


def is_workday(host):
    h = (host or "").lower()
    return any(h == d or h.endswith("." + d) for d in HOSTS)


def kind(status):
    """rejected | closed | withdrawn | active | unknown: what a Workday status means for you."""
    s = status or ""
    for name, rx in (("withdrawn", WITHDRAWN), ("rejected", REJECTED), ("closed", CLOSED), ("active", ACTIVE)):
        if rx.search(s):
            return name
    return "unknown"


def home_url(job_url):
    """Candidate Home for the career site a posting is on (https://<host>/[<lang>/]<site>/userHome; on myworkdaysite.com
    .../recruiting/<company>/<site>/userHome), or None."""
    u = urlparse(job_url or "")
    h = (u.hostname or "").lower()
    if u.scheme != "https" or not is_workday(h):
        return None
    keep = []
    for p in (p for p in u.path.split("/") if p):
        if p.lower() in STOP_PARTS:
            break
        keep.append(p)
    rest = keep[1:] if keep and LANG.match(keep[0]) else keep
    if h.endswith("myworkdaysite.com"):
        if len(rest) != 3 or rest[0].lower() != "recruiting":
            return None
    elif len(rest) != 1:
        return None
    return f"https://{h}/{'/'.join(keep)}/userHome"


def posting(job_url):
    """(title words, requisition id) from a posting's address (.../job/<place>/<Title-Words>_<R12345>)."""
    last = urlparse(job_url or "").path.rstrip("/").split("/")[-1]
    title, _, req = last.rpartition("_")
    if not title or not re.search(r"\d", req):
        title, req = last, ""
    return norm(title.replace("-", " ")), req.lower()


def norm(text):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split())


def saved():
    """accounts.json (never printed)."""
    try:
        with open(accounts.PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def logins(only="", rows=()):
    """The Workday logins in accounts.json (filtered by `only`), each with the Candidate Home to open. Their jobs: the
    ones the assistant saved with the login, plus the company-site links in sent rows' Notes that are this login's
    (your own imported logins have none saved)."""
    from nuauto import sheet
    data = saved()
    links = [(r, u.rstrip(".,;)")) for r in rows if r.status in sheet.SENT for u in re.findall(r"https://\S+", r.notes or "")]
    out = []
    for a in data.values():
        host = accounts.host_of(a.get("site", ""))
        if not is_workday(host) or accounts.never(host) or not a.get("password"):
            continue
        if only and only.lower() not in f"{a['site']} {a.get('company', '')}".lower():
            continue
        jobs = list(a.get("jobs") or [])
        known = {j.get("url") for j in jobs}
        jobs += [{"title": r.title, "url": u} for r, u in links if u not in known and may_sign_in(u, a, data)]
        homes = [home_url(j.get("url")) for j in jobs]
        out.append({**a, "jobs": jobs, "home": next((h for h in homes if h and may_sign_in(h, a, data)), None)})
    return sorted(out, key=lambda a: a["site"])


def may_sign_in(url, a, data=None):
    """Only this login's own site gets its password: a Workday page (https) for which this is the login NUauto would
    pick (accounts.find: the same host, and company on Workday's shared hosts)."""
    u = urlparse(url or "")
    if u.scheme != "https" or not is_workday(u.hostname) or accounts.never(u.hostname or ""):
        return False
    found = accounts.find(saved() if data is None else data, url)
    return bool(found) and found.get("site") == a["site"]


def entry(heads, cells):
    """One application row of Candidate Home's table: {"title", "status", "text"} (columns by their headers, else the
    first cell is the title and the status is the cell that reads like one)."""
    cells = [c.strip() for c in cells]
    heads = [h.strip().lower() for h in heads] if len(heads) == len(cells) else []
    pick = lambda rx: next((cells[i] for i, h in enumerate(heads) if re.search(rx, h)), None)  # noqa: E731
    title = pick(r"title|job|position") or (cells[0] if cells else "")
    status = pick(r"status") or next((c for c in cells[1:] if kind(c) != "unknown"), "")
    return {"title": title, "status": status, "text": " | ".join(cells)}


def which_job(e, a, entries):
    """The saved job (accounts.json's jobs) this application is, or None when it is not certain: its requisition id
    is in the row, or its title words are the row's; else the login's only job when the site lists only one."""
    jobs = a.get("jobs") or []
    found = []
    for j in jobs:
        words, req = posting(j.get("url"))
        if (req and f" {norm(req)} " in f" {norm(e['text'])} ") or (words and words == norm(e["title"])):
            found.append(j)
    if len(found) == 1:
        return found[0]
    if not found and len(jobs) == 1 and len(entries) == 1:
        return jobs[0]
    return None


def which_row(j, a, rows):
    """The one sent row (sheet.SENT) of the sheet for this job: its company-site link is in the row's Notes, else the
    row's company and title are the ones the assistant saved with the login. None if not exactly one."""
    from nuauto import sheet
    sent = [r for r in rows if r.status in sheet.SENT]
    by_link = [r for r in sent if j.get("url") and j["url"] in r.notes]
    if len(by_link) == 1:
        return by_link[0]
    by_name = [r for r in sent if r.title == j.get("title") and (not a.get("company") or r.company == a["company"])]
    return by_name[0] if len(by_name) == 1 else None


# ---------- the browser (one fresh private Firefox per site) ----------

def _visible(loc):
    try:
        return loc.count() > 0 and loc.first.is_visible()
    except Exception:
        return False


def sign_in(page, a, log):
    """Sign in with this site's saved login (once). Returns None, or why not."""
    from nuauto import browser
    email = page.locator('input[data-automation-id="email"]')
    if not _visible(email):
        for sel in ('[data-automation-id="signInLink"]', '[data-automation-id="utilityButtonSignIn"]'):
            if _visible(page.locator(sel)):
                page.locator(sel).first.click()
                browser.pause(page, 1.5, 3)
                break
    if _visible(page.locator('input[data-automation-id="verifyPassword"]')) and \
            _visible(page.locator('[data-automation-id="signInLink"]')):  # the create-account form: go to sign-in
        page.locator('[data-automation-id="signInLink"]').first.click()
        browser.pause(page, 1.5, 3)
    password = page.locator('input[data-automation-id="password"]')
    if not (_visible(email) and _visible(password)):
        return "no sign-in form found"
    if not may_sign_in(page.url, a):
        return f"the sign-in is on {urlparse(page.url).hostname}, not this login's site: not signing in"
    email.first.fill(a["email"])
    browser.pause(page, 0.5, 1.2)
    password.first.fill(a["password"])
    browser.pause(page, 0.5, 1.2)
    # Workday lays a click catcher (click_filter) over its buttons: a forced click lands on it, as a person's does
    page.locator('[data-automation-id="signInSubmitButton"]').first.click(force=True)
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    browser.pause(page, 2, 4)
    if _visible(page.locator('input[data-automation-id="password"]')):
        return "sign-in didn't work (wrong password, or the site wants something else)"
    return None


def read_table(page):
    """Every application row on the page: [entry()]."""
    out = []
    for t in page.locator("table").all():
        heads = [h.inner_text() for h in t.locator("thead th, thead td").all()]
        for tr in t.locator("tbody tr").all():
            cells = [c.inner_text() for c in tr.locator("td, th").all()]
            if len(cells) >= 2:
                out.append(entry(heads, cells))
    return out


def check_site(p, a, log, headless):
    """{"site", "company", "error" | "apps": [entry + {"kind", "tab"}]}."""
    from nuauto import browser
    res = {"site": a["site"], "company": a.get("company", ""), "apps": []}
    if not a.get("home"):
        res["error"] = "no saved job link to find its Candidate Home"
        return res
    b = p.firefox.launch(headless=headless)
    try:
        ctx = b.new_context()

        def lock(route):  # the main page stays on Workday: no other site's sign-in ever gets this login
            req = route.request
            if req.is_navigation_request() and req.frame.parent_frame is None and not is_workday(urlparse(req.url).hostname):
                log.write(f"{a['site']}: blocked a move to {urlparse(req.url).hostname}")
                return route.abort()
            return route.continue_()
        ctx.route("**/*", lock)
        page = ctx.new_page()
        page.goto(a["home"], wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        browser.pause(page, 1.5, 3)
        why = sign_in(page, a, log)
        if why:
            res["error"] = why
            log.screenshot(page, f"{norm(a['site']).replace(' ', '_')}_stopped")
            return res
        name = norm(a["site"]).replace(" ", "_")
        if "userhome" not in page.url.lower():
            page.goto(a["home"], wait_until="domcontentloaded", timeout=45000)
            browser.pause(page, 2, 4)
        log.screenshot(page, f"{name}_active")
        res["apps"] = [{**e, "tab": "Active"} for e in read_table(page)]
        tab = page.get_by_role("tab", name=re.compile(r"^\s*inactive", re.I))
        if _visible(tab):
            tab.first.click()
            browser.pause(page, 1.5, 3)
            log.screenshot(page, f"{name}_inactive")
            seen = {e["text"] for e in res["apps"]}
            res["apps"] += [{**e, "tab": "Inactive"} for e in read_table(page) if e["text"] not in seen]
        for e in res["apps"]:
            e["kind"] = kind(e["status"])
            if e["tab"] == "Inactive" and e["kind"] in ("active", "unknown"):
                e["kind"] = "closed"  # on the Inactive tab, whatever its status still says
        if not res["apps"]:
            with open(f"{log.dir}/{name}.txt", "w") as f:  # what the page said, to see why nothing was read
                f.write(page.locator("body").inner_text())
            res["error"] = "signed in, but read no applications (see the screenshot)"
        return res
    except Exception as e:
        text = str(e).splitlines()[0][:120] if str(e) else ""
        for pw in [a.get("password"), *(a.get("other_passwords") or [])]:
            text = text.replace(pw, "<password>") if pw else text  # an error never shows it
        res["error"] = f"stopped: {type(e).__name__}: {text}"
        return res
    finally:
        b.close()


# ---------- the command ----------

def offers(results, sites, rows_by_tab):
    """[(tab, row, entry)]: rejections (and closed ones) tied to exactly one sent row that isn't Rejected yet."""
    out = []
    by_site = {a["site"]: a for a in sites}
    for res in results:
        a = by_site.get(res["site"])
        apps = res.get("apps") or []
        for e in apps:
            if e.get("kind") not in ("rejected", "closed") or a is None:
                continue
            j = which_job(e, a, apps)
            for tab, rows in rows_by_tab.items():
                r = which_row(j, a, rows) if j else None
                if r and r.status != "Rejected" and (tab, r.number) not in [(t, x.number) for t, x, _ in out]:
                    out.append((tab, r, e))
    return out


def main(argv):
    from nuauto import browser
    from nuauto import sheet
    headless = "--hidden" in argv or not browser.has_display()
    rest = [x for x in argv if x != "--hidden"]
    if rest and rest[0].startswith("-"):
        sys.exit(__doc__)
    ws_main = sheet.open_worksheet()
    tabs = {"NUworks": (ws_main, sheet.read_rows(ws_main))}
    try:
        ws_other = sheet.open_other()
        tabs["Other jobs"] = (ws_other, sheet.read_rows(ws_other))
    except sheet.SheetError:
        pass
    sites = logins(" ".join(rest), [r for _, rows in tabs.values() for r in rows])
    if not sites:
        sys.exit("No Workday logins" + (f" matching {' '.join(rest)!r}" if rest else "") + " in accounts.json.")
    from playwright.sync_api import sync_playwright
    log = browser.RunLog("workday")
    results = []
    try:
        with sync_playwright() as p:
            for i, a in enumerate(sites, 1):
                print(f"\n[{i}/{len(sites)}] {a.get('company') or a['site']}")
                res = check_site(p, a, log, headless)
                results.append(res)
                log.write(f"{a['site']}: " + (res.get("error") or f"{len(res['apps'])} application(s)"))
    except KeyboardInterrupt:
        print("\nStopped. What was read so far:")
    print("\nWORKDAY STATUSES")
    for res in results:
        print(f"\n{res['company'] or res['site']}  ({res['site']})")
        if res.get("error"):
            print(f"  ! {res['error']}")
        for e in res.get("apps") or []:
            print(f"  {e['kind']:9} {e['status'][:40]:40} {e['title'][:60]}")
    todo = offers(results, sites, {t: rows for t, (_, rows) in tabs.items()})
    if not todo:
        return print("\nNothing to mark: no rejection here is tied to one sent row that isn't Rejected yet.")
    if not sys.stdin.isatty():
        print("\nRejections tied to a row (mark them Rejected in the GUI, a row's More menu):")
        for tab, r, e in todo:
            print(f"  {tab} row {r.number}: {r.company}, {r.title}  (Workday: {e['status']})")
        return
    marked = []
    for tab, r, e in todo:
        ans = input(f"\n{tab} row {r.number}: {r.company}, {r.title}\n  Workday says {e['status']!r} "
                    f"(as {e['title']!r}). Mark it Rejected? [y/N] ").strip().lower()
        if ans == "y":
            sheet.set_outcome(tabs[tab][0], r.number, r.url, "Rejected")
            log.write(f"marked {tab} row {r.number} Rejected (Workday: {e['status']})")
            marked.append((tab, r))
    owed = [r for tab, r in marked if tab == "NUworks"]
    if owed:
        print("\nWithdraw these on NUworks (My Job Applications) so they stop counting toward the total:")
        for r in owed:
            print(f"  row {r.number}: {r.company}, {r.title}")


if __name__ == "__main__":
    main(sys.argv[1:])
