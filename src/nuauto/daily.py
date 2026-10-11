"""Pool update: list -> Claude triage -> details -> Claude score -> pool -> notifications (Discord + desktop).

Runs on the homelab (systemd user timer nuauto-daily.timer, 08:00 and 18:00 New York time).
  nuauto daily          # the update; the morning run also sends deadline reminders
  nuauto daily weekly   # Sunday summary (nuauto-weekly.timer)
From the laptop: `nuauto update` starts the homelab run and then syncs.
Uses the hidden (headless) browser; never applies to anything and only reads the sheet.
Claude runs as `claude -p` (Sonnet) with only Read/Write tools, one call per batch.
"""
import json
import os
import re
import subprocess
import sys
import traceback
import urllib.request
from datetime import datetime, timedelta

os.environ["AUTO_HEADLESS"] = "1"

from nuauto import config  # noqa: E402
from nuauto import jobs  # noqa: E402

# config.tool_path searches ~/.local/bin and the usual install folders: systemd units run with a short PATH
CLAUDE = config.tool_path("claude") or "claude"
# Claude's error when its login is gone (e.g. "Failed to authenticate: OAuth session expired", "Please run /login")
AUTH_RE = re.compile(r"authenticat|logged out|/login|log in|oauth|401", re.I)
SCANS = "scans.json"  # per-run counts for the morning summary (data/)
LOG_PATH = os.path.join(config.LOGS_DIR, f"daily-{datetime.now():%Y%m%d-%H%M}.txt")


def log(msg):
    line = f"{datetime.now():%H:%M:%S}  {msg}"
    print(line, flush=True)
    os.makedirs(config.LOGS_DIR, exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def notify(title, body=""):
    from nuauto import health
    log(f"NOTIFY: {title} | {body}")
    health.desktop_notify(title, body)  # Linux desktop / macOS (local mode); nothing on a headless homelab
    discord(f"**{title}**\n{body}" if body else f"**{title}**")


def discord(text):
    """Post to the Discord webhook in discord_webhook.txt (if present). Never logs the URL."""
    try:
        with open(config.DISCORD_WEBHOOK_PATH) as f:
            url = f.read().strip()
    except OSError:
        return
    req = urllib.request.Request(url, data=json.dumps({"content": text[:1900]}).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "nuauto-helper"})
    try:
        urllib.request.urlopen(req, timeout=20).close()
    except Exception as e:
        log(f"discord post failed: {type(e).__name__}")


def run_claude(prompt_file, batch_file, extra=None):
    """One Claude call on one batch file. Returns None, or Claude's error text if it exited with an error.
    extra: more {{name}} values for the prompt (jobs.render_prompt)."""
    name = os.path.basename(batch_file)
    w = config.WORK_DIR
    prompt = (f"Follow {jobs.render_prompt(prompt_file, extra)} exactly: read it first, then "
              f"{w}/resume.txt. Process only {w}/{name} and "
              f"write {w}/{name.replace('_in_', '_out_')} with exactly one entry per "
              "input job, keyed by the job id (copy each id exactly). Read every job fully and judge each one "
              f"individually. Write only that one output file. Everything you need is in {w}: nothing outside it "
              "can be read or written.")
    log(f"claude: {name}")
    os.makedirs(w, exist_ok=True)
    # Shut in work/: the batches hold text from the web (job postings, Simplify's list), so what a posting says can
    # never make this session read or write anything else (local/ with your logins, the code, your home). It runs
    # in work/, reads and edits only there (Edit rules cover Write; acceptEdits stays inside the working folder; in
    # -p mode anything that would need a prompt is refused), and has no shell, web or MCP tools.
    r = subprocess.run(
        [CLAUDE, "-p", prompt, "--model", "sonnet",
         "--strict-mcp-config",  # no MCP servers / claude.ai connectors
         "--tools", "Read", "Write", "Edit",
         "--allowedTools", "Read(./**)", "Edit(./**)",
         "--disallowedTools", "Bash", "WebFetch", "WebSearch",
         "--permission-mode", "acceptEdits"],
        cwd=w, capture_output=True, text=True, timeout=1800)
    out = (r.stdout.strip() or r.stderr.strip())[-200:]
    log(f"claude: {name} exit {r.returncode} {out!r}")
    return None if r.returncode == 0 else out or f"exit {r.returncode}"


def where():
    return "the homelab" if config.IS_SERVER else "this machine"


def claude_ready():
    """Before the first Claude batch: is Claude Code there and logged in? Otherwise every batch would fail one by
    one. An unclear answer from `claude auth status` does not block the run."""
    from nuauto import health
    status = health.claude_status()
    if status is None:
        notify("NUauto: Claude Code not found", f"Scoring can't run. Install Claude Code on {where()} (README.md, Setup).")
        return False
    if status and not status.get("loggedIn"):
        notify("NUauto: Claude Code is logged out", f"Scoring can't run. On {where()} run: claude auth login")
        return False
    return True


def claude_step(ready, prompt_file, batches, import_fn, title, extra=None):
    """Claude on each batch file, then import the results. Returns False if it stopped (already notified).
    A login error stops at once with the "logged out" message: `claude auth status` can still say logged in
    while the saved login can no longer be renewed. Other Claude errors go in front of the import's problems."""
    if not batches:
        return True
    if not ready():
        return False
    errors = []
    for b in batches:
        err = run_claude(prompt_file, b, extra)
        if err and AUTH_RE.search(err):
            notify("NUauto: Claude Code is logged out",
                   f"Scoring can't run. On {where()} run: claude auth login\nClaude said: {err}")
            return False
        if err:
            errors.append(f"{os.path.basename(b)}: {err}")
    ok, msg = step(import_fn)
    if not ok:
        said = "Claude failed on " + "; ".join(errors) + "\n" if errors else ""
        notify(title, (said + msg)[:600])
        return False
    return True


def step(fn, *args):
    """Run a jobs.py step; a SystemExit from it becomes (False, message)."""
    try:
        return True, fn(*args)
    except SystemExit as e:
        return False, str(e)


def main():
    """The scan, then (morning run) the reminders. A failed scan still sends the reminders, from the sheet
    and the last saved pool."""
    before = {r["id"] for r in jobs.load("pool.json", [])}
    try:
        code = scan(before)
    except Exception as e:  # e.g. NUworks too slow, or the browser profile is busy because apply.py is running
        traceback.print_exc()
        notify("NUauto: daily update failed", f"{type(e).__name__}: {str(e)[:150]}")
        code = 1
    if datetime.now().hour < 12:  # reminders once a day, with the morning run
        reminders(before, ok=code == 0)
    return internships() or code


def internships():
    """The internship update (intern.py: its own timer runs it every 2 hours too), when it is on. Returns 0 or 1."""
    from nuauto import intern
    if not intern.ENABLED:
        return 0
    log("internships")
    try:
        return intern.update()
    except Exception as e:  # never hides the NUworks run's own result
        traceback.print_exc()
        notify("NUauto: internship update failed", f"{type(e).__name__}: {str(e)[:150]}")
        return 1


def scan(before):
    """list -> triage -> details -> score -> category -> pool. Returns 0, or 1 if a step stopped (already notified)."""
    listed_before = set(jobs.load("list.json", {}))
    log("list")
    ok, msg = step(jobs.cmd_list)
    if not ok:
        notify("NUauto: update stopped", f"{msg}. Run `nuauto login` on your laptop (it copies the session to the homelab).")
        return 1

    checked = []

    def ready():  # claude_ready(), asked once per run, only when there is something for Claude to do
        if not checked:
            checked.append(claude_ready())
        return checked[0]

    if not claude_step(ready, "TRIAGE_PROMPT.md", jobs.cmd_triage_export(), jobs.cmd_triage_import,
                       "NUauto: triage results incomplete"):
        return 1

    log("details")
    ok, msg = step(jobs.cmd_details)
    if not ok:
        notify("NUauto: update stopped", f"{msg}. Run `nuauto login` on your laptop (it copies the session to the homelab).")
        return 1

    if not claude_step(ready, "SCORE_PROMPT.md", jobs.cmd_score_export(), jobs.cmd_score_import,
                       "NUauto: scoring incomplete"):
        return 1

    # a failure here still builds the pool; those jobs show 'uncategorized'
    claude_step(ready, "CATEGORY_PROMPT.md", jobs.cmd_cat_export(), jobs.cmd_cat_import, "NUauto: categories incomplete")

    pool = jobs.build_pool()
    jobs.save("pool.json", pool)
    new = [r for r in pool if r["id"] not in before]
    gone = len(before - {r["id"] for r in pool})
    log(f"pool {len(pool)} ({len(new)} new, {gone} closed or dropped)")
    record_scan(len(set(jobs.load("list.json", {})) - listed_before), len(new))
    if new:
        top = "\n".join(f"{r['effective']}%  {r['title'][:45]} | {r['company'][:25]}" for r in new[:5])
        notify(f"{len(new)} new job{'s' if len(new) > 1 else ''} in your NUworks pool",
               top + "\nRun: nuauto approve")
    return 0


def reminders(before, ok):
    """Morning reminders. After a good scan, first records jobs applied to by hand (needs the fresh list)."""
    rows = sheet_rows()
    if rows is not None and ok:
        try:
            rows = record_hand_applications(before, rows) or rows
        except Exception as e:  # never let this block the reminders
            log(f"hand applications check failed: {type(e).__name__}: {str(e)[:150]}")
    pool = jobs.load("pool.json", [])
    summary = scan_summary(jobs.load(SCANS, []), datetime.now())
    from nuauto import intern
    if intern.ENABLED:
        summary += "\n" + intern.summary()
    for name, fn in [("approved", lambda: approved_notice(rows)), ("urgent", lambda: urgent_notice(pool, rows)),
                     ("todo", lambda: todo_notice(rows, summary if ok else summary + " (this morning's scan failed)")),
                     ("google", google_notice)]:
        try:
            fn()
        except Exception as e:  # one broken reminder must not stop the others
            log(f"{name} reminder failed: {type(e).__name__}: {str(e)[:150]}")


def google_notice():
    """Heads-up the day before the 7-day Google login runs out."""
    from nuauto import sheet
    age = sheet.google_login_age()
    if age is not None and age >= config.GOOGLE_LOGIN_DAYS - 1:
        left = config.GOOGLE_LOGIN_DAYS - age
        title = "NUworks: Google login has expired" if left <= 0 else "NUworks: Google login expires today"
        notify(title, "On the laptop run: `nuauto login google`")


def record_hand_applications(before, rows):
    """Jobs that left the NUworks list (it hides jobs you applied to) and that NUworks says you applied to:
    mark their sheet row Applied, or add one. Returns fresh rows if anything changed."""
    from nuauto import sheet
    listed = set(jobs.load("list.json", {}))
    by_id = {jobs.job_id(r.url): r for r in rows}
    applied = {i for i, r in by_id.items() if r.status == "Applied"}
    candidates = (set(before) | {i for i, r in by_id.items() if r.status != "Applied"}) - listed - applied
    ok, found = step(jobs.check_applied, sorted(candidates))
    if not ok or not found:
        log(f"hand applications: checked {len(candidates)}, found {len(found) if ok else 'error: ' + str(found)}")
        return None
    ws = sheet.open_worksheet()
    details, done = jobs.load_details(), []
    for i in found:
        d = details.get(i, {})
        if i in by_id:
            sheet.mark_applied_by_hand(ws, by_id[i].number, by_id[i].url, how="(seen on NUworks)")
        else:
            n = len(ws.get_all_values()) + 1
            ws.update(range_name=f"A{n}:F{n}", values=[[jobs.job_url(i), d.get("company", ""), d.get("title", ""), "Applied",
                      "Applied by hand (seen on NUworks)", datetime.now().strftime(sheet.DATE_FMT)]], value_input_option="RAW")
        done.append(f"{d.get('title', i)[:40]} | {d.get('company', '')[:22]}")
    notify(f"Recorded {len(done)} application{'s' if len(done) > 1 else ''} you made on NUworks", "\n".join(done))
    return sheet.read_rows(ws)


def record_scan(listed, pooled):
    """Remember this run's counts (new NUworks postings, new pool jobs) for the morning summary."""
    scans = jobs.load(SCANS, [])
    scans.append({"time": datetime.now().isoformat(timespec="minutes"), "listed": listed, "pool": pooled})
    jobs.save(SCANS, scans[-60:])


def scan_summary(scans, now, hours=24):
    recent = [s for s in scans if now - datetime.fromisoformat(s["time"]) <= timedelta(hours=hours)]
    listed, pooled = sum(s["listed"] for s in recent), sum(s["pool"] for s in recent)
    return (f"Last {hours}h ({len(recent)} scan{'s' if len(recent) != 1 else ''}): {listed} new posting{'s' if listed != 1 else ''}"
            f" on NUworks, {pooled} made your pool" + (" (nuauto approve)" if pooled else ""))


def todo_notice(rows, summary):
    """Morning message, always sent: scan counts, then things only you can do: company-site applications
    still owed, and external jobs (Needs Human)."""
    from nuauto import sheet
    from nuauto import web
    site = [r for r in rows if r.status == "Applied" and r.notes.startswith(sheet.SITE_MARK)] if rows else []
    ext = [r for r in rows if r.status == "Needs Human" and r.notes.startswith("External application")] if rows else []
    if not site and not ext:
        log("todo: nothing owed")
        return notify("NUauto morning", summary + ("\nNothing only you need to finish." if rows is not None else ""))
    lines = [summary, "Only you can finish:"]
    def mark(label, action, r):  # no Mark-done page configured (web_base_url "") = no link
        return f" · [{label}]({web.link(action, r.number, jobs.job_id(r.url))})" if web.BASE_URL else ""
    for r in site:
        lines.append(f"• {r.company[:25]}: apply on their site too ([job]({r.url})){mark('Mark done', 'site', r)}")
    for r in ext:
        lines.append(f"• {r.company[:25]}: external application ([job]({r.url})){mark('Mark applied', 'applied', r)}")
    log(f"todo: {len(site)} company-site, {len(ext)} external")
    notify("NUauto morning", "\n".join(lines))


def sheet_rows():
    """Sheet rows, or None if the sheet can't be read (e.g. the Google token expired)."""
    try:
        from nuauto import sheet
        return sheet.read_rows(sheet.open_worksheet())
    except BaseException as e:  # gspread's login flow can sys.exit
        log(f"could not read the sheet ({type(e).__name__})")
        notify("NUauto: can't read the Google sheet",
               "Google login probably expired (it lasts 7 days). On the laptop run: `nuauto login google`")
        return None


def approved_notice(rows):
    """Approved rows (not applied yet) that close today or tomorrow."""
    if rows is None:
        return
    details = jobs.load_details()
    today = datetime.now().date()
    due = []
    for r in rows:
        d = details.get(jobs.job_id(r.url)) if r.status == "Approved" else None
        day = jobs.closes(d) if d else None
        if day is not None and 0 <= (day - today).days <= 1:
            due.append((day, r))
    log(f"approved closing today/tomorrow: {len(due)}")
    if due:
        body = "\n".join(f"{jobs.closes_text(day)}  {r.title[:40]} | {r.company[:22]}" for day, r in sorted(due, key=lambda x: x[0]))
        notify(f"{len(due)} Approved job{'s' if len(due) > 1 else ''} close today or tomorrow", body + "\nRun: nuauto apply")


def urgent_notice(pool, rows, days=3):
    """Pool jobs closing within `days` that are not in the sheet yet and not rated no."""
    in_sheet = {jobs.job_id(r.url) for r in rows} if rows is not None else set()
    ratings = jobs.load("ratings.json", {})
    today = datetime.now().date()
    soon = sorted((r for r in pool if r.get("closes") and r["id"] not in in_sheet
                   and ratings.get(r["id"], {}).get("label") != 0
                   and 0 <= (datetime.fromisoformat(r["closes"]).date() - today).days <= days),
                  key=lambda r: r["closes"])
    log(f"closing within {days} days, not in sheet: {len(soon)}")
    if soon:
        body = "\n".join(f"{r['closes'][5:]}  {r['match']}%  {r['title'][:40]} | {r['company'][:22]}" for r in soon[:5])
        notify(f"{len(soon)} pool job{'s' if len(soon) > 1 else ''} close within {days} days", body + "\nRun: nuauto approve")


def weekly():
    """Sunday summary: this week's applications, Approved rows waiting, jobs to look at."""
    from nuauto import sheet
    rows = sheet_rows()
    if rows is None:
        return 1
    week, total = sheet.check_limits_safe(rows)
    waiting = [r for r in rows if r.status == "Approved"]
    details, ratings = jobs.load_details(), jobs.load("ratings.json", {})
    today = datetime.now().date()
    soon = [r for r in waiting if (d := details.get(jobs.job_id(r.url))) and jobs.closes(d)
            and jobs.closes(d) <= today + timedelta(days=7)]
    in_sheet = {jobs.job_id(r.url) for r in rows}
    unseen = [r for r in jobs.load("pool.json", []) if r["id"] not in in_sheet and r["id"] not in ratings]
    room = max(0, sheet.max_per_week() - week)
    lines = [f"Applied {sheet.week_window()[1]}: {week}/{sheet.max_per_week()} (total {total}/{sheet.MAX_TOTAL}).",
             f"Approved and waiting: {len(waiting)}" + (f" ({len(soon)} close within 7 days)" if soon else "") + ".",
             f"Pool jobs you haven't looked at: {len(unseen)}."]
    if waiting:
        lines.append(f"Run `nuauto apply` (room for {room} this week).")
    elif room:
        lines.append(f"Nothing Approved. Run `nuauto approve` to pick up to {room}.")
    notify("NUauto weekly check-in", "\n".join(lines))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(weekly() if sys.argv[1:] == ["weekly"] else main())
    except Exception as e:  # main() handles scan failures itself; this catches the rest
        notify("NUauto: daily update failed", f"{type(e).__name__}: {str(e)[:150]}")
        raise
