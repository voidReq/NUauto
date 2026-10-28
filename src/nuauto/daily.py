"""Pool update: list -> Claude triage -> details -> Claude score -> pool -> notifications (Discord + desktop).

Runs on the homelab (systemd user timer nuauto-daily.timer, 08:00 and 18:00 New York time).
  nuauto daily          # the update; the morning run also sends the one message a day (morning())
  nuauto daily weekly   # nuauto-weekly.timer (Sunday): nothing to send since 2026-10-26
Messages: the morning message, and a problem when it happens (alert(): at most once a day each).
From the laptop: `nuauto update` starts the homelab run and then syncs.
Uses the hidden (headless) browser; never applies to anything and only reads the sheet.
Claude runs as `claude -p` (Sonnet) with only Read/Write tools, one call per batch.
"""
import json
import os
import re
import subprocess
import sys
import time
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
LAST_RUN = "last_run.json"  # data/: how the last update went and why it stopped (the GUI's New jobs check, with a fix)
LATER = "later.json"  # data/: what the next morning message adds (later(): {kind: [items]}), cleared once it is sent
TOLD = "told.json"  # data/: when each problem was last told (alert(): at most once a day each)
TELL_AGAIN = timedelta(hours=20)  # (a problem still there the next morning is told again)
QUIET = []  # set during the morning run's scan: its problems go in the morning message (wrong_line), not their own
RETRY_WAIT = 120  # seconds before the job list is read once more: NUworks is sometimes slow for a while
STOPPED = []  # this run's stops, in order: (reason, what stopped); main() keeps the first in LAST_RUN
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


def alert(title, body=""):
    """A problem worth a message now (a login to renew, a run that stopped). The same title at most once a day: the
    2-hourly internship update and the twice-daily update would otherwise repeat it every run."""
    told, now = jobs.load(TOLD, {}), datetime.now()
    last = told.get(title)
    if last and now - datetime.fromisoformat(last) < TELL_AGAIN:
        return log(f"ALERT (told at {last}): {title} | {body}")
    told[title] = now.isoformat(timespec="minutes")
    jobs.save(TOLD, {k: v for k, v in told.items() if now - datetime.fromisoformat(v) < timedelta(days=7)})
    if QUIET:
        return log(f"ALERT (in the morning message): {title} | {body}")
    notify(title, body)


def later(kind, items):
    """Things for the next morning message instead of a message now (kinds: "recorded", "closed", "deployed")."""
    kept = jobs.load(LATER, {})
    kept[kind] = [*kept.get(kind, []), *(i for i in items if i not in kept.get(kind, []))][-10:]
    jobs.save(LATER, kept)


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


def assistant_discord_on():
    """Settings > Company-site assistant > "Message me on Discord" (local_config "assist_discord", on unless set to false).
    Read from the file each time: the homelab's web page and its sessions run for days."""
    try:
        with open(config.LOCAL_CONFIG_PATH) as f:
            return json.load(f).get("assist_discord", True) is not False
    except (OSError, ValueError):
        return config.LOCAL.get("assist_discord", True) is not False


def discord_assistant(text):
    """discord() for what the company-site assistant says (needs you, ready to submit, a session ended or started): off
    when you check the app yourself. Everything else (the morning message, alerts, deploys) is not affected."""
    if assistant_discord_on():  # (off: say nothing at all: the guard hook calls this, and its stdout must stay pure JSON)
        discord(text)


def stop(reason, title, body, said=None):
    """A step stopped the run: tell you, and keep why for the GUI (LAST_RUN; health.updates turns it into a fix).
    reason: nuworks_login (NUworks wants you to log in), nuworks (NUworks did not answer: the next run usually works),
    claude (Claude Code logged out or missing), failed (anything else). said: what stopped, in a few words."""
    STOPPED.append((reason, said or title.removeprefix("NUauto: ")))
    alert(title, body)


def record_run(code):
    """How this run went, for the GUI: when, ok, and the first stop's reason and words."""
    reason, said = (STOPPED[0] if STOPPED else ("failed", "the update stopped (its log says why)")) if code else (None, "")
    jobs.save(LAST_RUN, {"time": datetime.now().isoformat(timespec="minutes"), "ok": not code, "reason": reason,
                         "said": said})


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
    # Shut in work/: the batches hold text from the web (job postings, the internship lists), so what a posting says can
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
        stop("claude", "NUauto: Claude Code not found", f"Scoring can't run. Install Claude Code on {where()} (README.md, Install).")
        return False
    if status and not status.get("loggedIn"):
        stop("claude", "NUauto: Claude Code is logged out", f"Scoring can't run. On {where()} run: claude auth login")
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
            stop("claude", "NUauto: Claude Code is logged out",
                 f"Scoring can't run. On {where()} run: claude auth login\nClaude said: {err}")
            return False
        if err:
            errors.append(f"{os.path.basename(b)}: {err}")
    ok, msg = step(import_fn)
    if not ok:
        said = "Claude failed on " + "; ".join(errors) + "\n" if errors else ""
        stop("failed", title, (said + msg)[:600])
        return False
    return True


def step(fn, *args):
    """Run a jobs.py step; a SystemExit from it becomes (False, message)."""
    try:
        return True, fn(*args)
    except SystemExit as e:
        return False, str(e)


def main():
    """The scan and the applications check, then (morning run) the morning message. A failed scan still sends it, from
    the sheet and the last saved pool."""
    before = {r["id"] for r in jobs.load("pool.json", [])}
    STOPPED.clear()
    is_morning = datetime.now().hour < 12  # the one message a day, with the morning run: it carries this run's problems
    if is_morning:
        QUIET.append(True)
    try:
        try:
            code = scan(before)
        except Exception as e:  # e.g. NUworks too slow, or the browser profile is busy because apply.py is running
            traceback.print_exc()
            what = f"{type(e).__name__}: {str(e)[:150]}"
            # Playwright's TimeoutError: a NUworks page did not load (Claude's calls time out as TimeoutExpired)
            stop("nuworks" if type(e).__name__ == "TimeoutError" else "failed", "NUauto: daily update failed", what,
                 said="NUworks did not load in time" if type(e).__name__ == "TimeoutError" else what[:120])
            code = 1
        try:
            applications()
        except Exception as e:  # never stops the morning message
            log(f"applications check failed: {type(e).__name__}: {str(e)[:150]}")
    finally:
        QUIET.clear()
    if is_morning:
        reminders(before, ok=code == 0)
    code = internships() or code
    record_run(code)
    return code


def applications():
    """Every run: ask NUworks about each sent row (jobs.check_applications), so the total limit counts what NUworks has.
    Never changes the run's result: a sheet or NUworks that can't be read now is asked again next run. What to withdraw
    (Rejected rows NUworks still has, jobs that look filled) goes in the log here and in the morning message."""
    from nuauto import setup_sheet
    from nuauto import sheet
    try:
        ws = sheet.open_worksheet(interactive=False)
        rows = sheet.read_rows(ws)
        setup_sheet.ensure_dropdown(ws)  # Accepted / Rejected in the Status dropdown of a sheet made before them
    except Exception as e:  # (the morning reminders say so when it is the Google login)
        return log(f"applications check skipped: the sheet can't be read ({type(e).__name__})")
    log("applications on NUworks")
    ok, seen = step(jobs.check_applications, rows)
    if not ok:
        return log(f"applications check stopped: {seen}")
    missing = sorted(sheet.not_on_nuworks(rows, seen))
    log(f"applications: {sheet.total_count(rows, seen)} count toward the total"
        + (f"; NUworks has none for row{'s' if len(missing) > 1 else ''} {', '.join(map(str, missing))}" if missing else ""))
    waiting = sheet.rejected_not_withdrawn(rows, seen)
    if waiting:
        log(f"rejected, not withdrawn on NUworks: row{'s' if len(waiting) > 1 else ''} {', '.join(str(r.number) for r in waiting)}")
    done = sheet.maybe_filled(rows, seen)
    if done:
        log("job looks filled: " + ", ".join(f"row {r.number} ({why})" for r, why in done))


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
        stop("failed", "NUauto: internship update failed", f"{type(e).__name__}: {str(e)[:150]}")
        return 1


def nuworks_stopped(msg):
    """The list or details step stopped: NUworks wants you to log in, or it did not answer (nothing to do: the next
    run usually works). Returns 1, the scan's result."""
    if msg == jobs.NOT_LOGGED_IN:
        copies = " on the laptop; it copies the session to the homelab" if config.IS_SERVER else ""
        stop("nuworks_login", "NUauto: NUworks needs you to log in",
             f"The one-click re-login did not do it, so the update stopped. In NUauto: Today > Log in to NUworks "
             f"(or run `nuauto login`{copies}).")
    else:
        stop("nuworks", "NUauto: NUworks did not answer",
             f"{msg} Tried twice, {RETRY_WAIT // 60} min apart: NUworks was slow or down for a while. Nothing to do: the "
             "next update runs by itself (or in NUauto: Today > Run it again).", said="NUworks did not send the job list")
    return 1


def scan(before):
    """list -> triage -> details -> score -> category -> pool. Returns 0, or 1 if a step stopped (already notified)."""
    listed_before = set(jobs.load("list.json", {}))
    log("list")
    ok, msg = step(jobs.cmd_list)
    if not ok and msg != jobs.NOT_LOGGED_IN:  # NUworks slow (no job list came): once more, a little later
        log(f"list: {msg} Trying again in {RETRY_WAIT} s.")
        time.sleep(RETRY_WAIT)
        ok, msg = step(jobs.cmd_list)
    if not ok:
        return nuworks_stopped(msg)

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
        return nuworks_stopped(msg)

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
    best = max(new, key=lambda r: r["effective"], default=None)  # (the morning message names it)
    record_scan(len(set(jobs.load("list.json", {})) - listed_before), len(new),
                best and {"score": best["effective"], "text": f"{best['title'][:40]} | {best['company'][:22]}"})
    return 0


def reminders(before, ok):
    """The morning message. After a good scan, first records jobs applied to by hand (needs the fresh list)."""
    rows = sheet_rows(tell=False)  # (the message itself says when the sheet can't be read)
    if rows is not None and ok:
        try:
            rows = record_hand_applications(before, rows) or rows
        except Exception as e:  # never let this block the message
            log(f"hand applications check failed: {type(e).__name__}: {str(e)[:150]}")
    notify("NUauto morning", morning(rows, jobs.load("pool.json", []), other=other_rows()))
    jobs.save(LATER, {})


def other_rows():
    """The Other jobs tab's rows when internships are on (their Approved ones can close too), else None."""
    from nuauto import intern
    if not intern.ENABLED:
        return None
    try:
        return intern.other_rows(interactive=False)
    except BaseException as e:  # gspread's login flow can sys.exit; the message still goes out
        log(f"could not read the Other jobs tab ({type(e).__name__})")
        return None


def morning(rows, pool, now=None, other=None):
    """The one message a day (since 2026-10-26: before, each of these was a message of its own, several a day). Short:
    a line for each thing that applies: new jobs, what closes, what only you can do, what to withdraw, what was recorded
    or updated, this week's count, what is wrong. rows: None when the sheet can't be read; other: the Other jobs
    tab's."""
    now = now or datetime.now()
    lines = []
    for name, fn in [("new", lambda: new_line(now)), ("closing", lambda: closing_line(rows, pool, now.date(), other)),
                     ("todo", lambda: todo_line(rows)), ("withdraw", lambda: withdraw_line(rows)),
                     ("later", later_lines), ("week", lambda: week_line(rows)), ("wrong", lambda: wrong_line(rows))]:
        try:
            got = fn()
        except Exception as e:  # one broken line must not stop the message
            log(f"{name} line failed: {type(e).__name__}: {str(e)[:150]}")
            continue
        lines += got if isinstance(got, list) else [got] if got else []
    if config.REMOTE_APP:
        from nuauto import review
        lines.append(review.page_url())
    return "\n".join(lines)


def names(items, most=3, width=22):
    """A few short names: "A, B, C +2"."""
    items = [i[:width] for i in items]
    return ", ".join(items[:most]) + (f" +{len(items) - most}" if len(items) > most else "")


def scan_part(scans, now, what, hours=24):
    """How many jobs made a pool in the last `hours` (each run's counts: record_scan), with the best of them."""
    recent = [s for s in scans if now - datetime.fromisoformat(s["time"]) <= timedelta(hours=hours)]
    n = sum(s["pool"] for s in recent)
    best = max((s["best"] for s in recent if s.get("best")), key=lambda b: b["score"], default=None)
    return f"{n} {what}{'s' if n != 1 else ''}" + (f" (best: {best['text']})" if best and n else "")


def new_line(now):
    from nuauto import intern
    parts = [scan_part(jobs.load(SCANS, []), now, "co-op")]
    if intern.ENABLED:
        parts.append(scan_part(intern.load("scans.json", []), now, "internship"))
    return "New: " + " · ".join(parts)


def closing_line(rows, pool, today, other=None, days=3):
    """Approved rows on both tabs that close within a week (by name and day, soonest first: apply to those first; an
    internship's date is its posting's, when it gives one), and how many pool jobs (co-ops, internships) close within
    `days` that are not in the sheet yet and not rated no."""
    from nuauto import intern
    details, dates = jobs.load_details(), intern.closing_dates() if other is not None else {}
    due = [(jobs.closes(d), r) for r in rows or [] if r.status == "Approved"
           and (d := details.get(jobs.job_id(r.url))) and jobs.closes(d)]
    due += [(dates[intern.real_url(r.url)], r) for r in other or [] if r.status == "Approved"
            and intern.real_url(r.url) in dates]
    due = sorted(((day, r) for day, r in due if 0 <= (day - today).days <= jobs.URGENT_DAYS), key=lambda x: x[0])
    in_sheet = {jobs.job_id(r.url) for r in rows or []} | {intern.real_url(r.url) for r in other or []}
    ratings = jobs.load("ratings.json", {})
    pools = pool + (intern.load("pool.json", []) if other is not None else [])
    listed = lambda r: r["id"] in in_sheet or bool(r.get("url")) and intern.real_url(r["url"]) in in_sheet  # noqa: E731
    soon = [r for r in pools if r.get("closes") and not listed(r) and ratings.get(r["id"], {}).get("label") != 0
            and 0 <= (datetime.fromisoformat(r["closes"]).date() - today).days <= days]
    log(f"approved closing within {jobs.URGENT_DAYS} days: {len(due)}; pool closing within {days} days: {len(soon)}")

    def when(day):
        n = (day - today).days
        return "today" if n == 0 else "tomorrow" if n == 1 else f"{day:%a}"
    parts = ([names([f"{r.company[:22]} {when(day)}" for day, r in due], 4, 40)] if due else []) \
        + ([f"{len(soon)} pool job{'s' if len(soon) != 1 else ''} within {days} days (Review)"] if soon else [])
    return "Closing soon: " + " · ".join(parts) if parts else None


def todo_line(rows):
    """What only you can finish: company-site applications still owed and external jobs (Needs Human), the counts and
    the first few: each name links to its job, its ✓ to the Mark-done page (when it is set up)."""
    from nuauto import sheet
    from nuauto import web
    site = [r for r in rows if r.status == "Applied" and r.notes.startswith(sheet.SITE_MARK)] if rows else []
    ext = [r for r in rows if r.status == "Needs Human" and r.notes.startswith("External application")] if rows else []
    log(f"todo: {len(site)} company-site, {len(ext)} external")
    if not site and not ext:
        return None

    def item(r, action):  # no Mark-done page configured (web_base_url "") = no ✓
        mark = f" [✓]({web.link(action, r.number, jobs.job_id(r.url))})" if web.BASE_URL else ""
        return f"[{r.company[:22]}]({r.url}){mark}"
    counts = ([f"{len(site)} company site{'s' if len(site) != 1 else ''} owed"] if site else []) \
        + ([f"{len(ext)} external application{'s' if len(ext) != 1 else ''}"] if ext else [])
    items = [item(r, "site") for r in site] + [item(r, "applied") for r in ext]
    return (f"To do: {', '.join(counts)}: " + " · ".join(items[:3]) + (f" +{len(items) - 3}" if len(items) > 3 else "")
            + (" (✓ marks one done)" if web.BASE_URL else ""))


def withdraw_line(rows):
    """Applications NUworks still has that you may withdraw there, so they stop counting toward the total: Rejected
    rows, and Applied rows whose job looks filled (the applications check just before: sheet.maybe_filled)."""
    from nuauto import sheet
    if rows is None:
        return None
    seen = sheet.nuworks_seen()
    rejected = [r.company for r in sheet.rejected_not_withdrawn(rows, seen)]
    filled = [r.company for r, _ in sheet.maybe_filled(rows, seen)]
    parts = ([f"rejected {names(rejected)}"] if rejected else []) + ([f"may be filled {names(filled)}"] if filled else [])
    return "Withdraw on NUworks to free room: " + " · ".join(parts) if parts else None


LATER_TEXT = {"recorded": "Recorded what you applied to on NUworks", "closed": "Approved internships now closed",
              "deployed": "Updated"}


def later_lines():
    """What other runs kept for this message (later())."""
    return [f"{LATER_TEXT.get(k, k)}: {names(v, 2, 60) if k == 'deployed' else names(v)}"
            for k, v in jobs.load(LATER, {}).items() if v]


def week_line(rows):
    from nuauto import sheet
    if rows is None:
        return None
    week, total = sheet.check_limits_safe(rows)
    waiting = sum(1 for r in rows if r.status == "Approved")
    return f"Week: {week}/{sheet.max_per_week()} applied · total {total}/{sheet.MAX_TOTAL} · {waiting} Approved waiting"


FIX = {"nuworks_login": "run `nuauto login` on the laptop", "claude": "run `claude auth login` on {where}",
       "nuworks": "NUworks was slow; the next run usually works", "failed": "the update's log on {where} says why"}


def wrong_line(rows):
    """What is wrong now: this run stopped (its message was held for this one: alert, QUIET), the sheet can't be read,
    or the Google login runs out."""
    from nuauto import sheet
    parts = [f"the update stopped: {said} ({FIX.get(reason, FIX['failed']).format(where=where())})"
             for reason, said in STOPPED[:1]]
    if rows is None:
        parts.append("can't read the sheet (the Google login? on the laptop run `nuauto login google`)")
    else:
        age = sheet.google_login_age()
        if age is not None and age >= config.GOOGLE_LOGIN_DAYS - 1:
            parts.append(("the Google login has expired" if age >= config.GOOGLE_LOGIN_DAYS else
                          "the Google login expires today") + ": on the laptop run `nuauto login google`")
    return "Wrong: " + " · ".join(parts) if parts else None


def record_hand_applications(before, rows):
    """Jobs that left the NUworks list (it hides jobs you applied to) and that NUworks says you applied to:
    mark their sheet row Applied, or add one. Returns fresh rows if anything changed."""
    from nuauto import sheet
    listed = set(jobs.load("list.json", {}))
    by_id = {jobs.job_id(r.url): r for r in rows}
    applied = {i for i, r in by_id.items() if sheet.counted(r)}  # (a row being re-applied: NUworks has it until you withdraw)
    candidates = (set(before) | {i for i, r in by_id.items() if r.status not in sheet.SENT}) - listed - applied
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
        done.append(d.get("company") or d.get("title", i))
    log(f"hand applications: recorded {', '.join(done)}")
    later("recorded", done)
    return sheet.read_rows(ws)


def record_scan(listed, pooled, best=None):
    """Remember this run's counts (new NUworks postings, new pool jobs) and its best new job ({"score", "text"}) for the
    morning message."""
    scans = jobs.load(SCANS, [])
    scans.append({"time": datetime.now().isoformat(timespec="minutes"), "listed": listed, "pool": pooled,
                  **({"best": best} if best else {})})
    jobs.save(SCANS, scans[-60:])


def sheet_rows(tell=True):
    """Sheet rows, or None if the sheet can't be read (e.g. the Google token expired). tell: a message about it (the
    morning message says it in its own line)."""
    try:
        from nuauto import sheet
        return sheet.read_rows(sheet.open_worksheet())
    except BaseException as e:  # gspread's login flow can sys.exit
        log(f"could not read the sheet ({type(e).__name__})")
        if tell:
            alert("NUauto: can't read the Google sheet",
                  "Google login probably expired (it lasts 7 days). On the laptop run: `nuauto login google`")
        return None


def weekly():
    """`nuauto daily weekly` (Sunday, nuauto-weekly.timer). Since 2026-10-26 it sends nothing: the morning message has
    this week's count every day. Kept so the installed timer still runs cleanly."""
    log("weekly: nothing to send (the morning message has the week's count)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(weekly() if sys.argv[1:] == ["weekly"] else main())
    except Exception as e:  # main() handles scan failures itself; this catches the rest
        alert("NUauto: daily update failed", f"{type(e).__name__}: {str(e)[:150]}")
        raise
