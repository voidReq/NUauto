"""The NUauto window: `nuauto gui`. A small local web app (docs/GUI.md), laptop only.

  nuauto gui                      open it (starting it again just opens another window on the running one)
  nuauto gui --demo [--demo-state a,b]
                                  everything fake (demo.py) in a temp folder: for trying it out, and for agents
  nuauto gui --view               READ-ONLY on your real data, for agents (and a quick look): no window, no lock file,
                                  the server refuses every action (see VIEW), answers and run screenshots are hidden
  options: --port N (default: any free port), --no-open (print the URL instead of opening a window),
           --browser (a normal browser tab instead of an app window),
           --screenshots DIR (demo only: every screen, light and dark, wide and narrow, saved as PNGs; then quits)

How it is safe to run:
- It listens on 127.0.0.1 only. A secret made at each start is in the URL it opens once; the page swaps it for a
  cookie. Every request needs that cookie and a Host header naming this server; every POST also needs the header
  X-NUauto: 1 (other websites can't send it) and JSON. Secrets (tokens, cookies, the webhook) never reach the page.
- Long or browser work runs as the same `nuauto ...` commands, one at a time, in child processes (Task). Their
  questions come over answers.JsonIO and show as dialogs; Stop sends SIGINT to the child (the KeyboardInterrupt
  path Ctrl+C takes). If this process dies, the child's stdin closes and it stops the same way.
- Real mode refuses to start inside a Claude Code shell (CLAUDECODE): agents use --demo (fake data) or --view
  (real data, read-only).
"""
import hmac
import json
import mimetypes
import os
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from nuauto import answers
from nuauto import config
from nuauto import health
from nuauto import jobs
from nuauto import sheet
from nuauto import window

STATIC = os.path.join(config.SRC_DIR, "gui_static")
IDLE_EXIT = 30 * 60   # no page has asked anything for this long and nothing runs: quit
LOG_KEEP = 4000       # log lines kept per task
HISTORY = 20          # finished tasks kept


def now():
    return time.time()


# ---------------------------------------------------------------- tasks (child processes)

class Task:
    """One `nuauto ...` child process. Plain output lines are its log; answers.JsonIO lines are questions and
    progress events. Reads stdout in a thread; answer() and stop() are called from request threads."""

    def __init__(self, kind, label, cmd, browser, background=False, env=None, on_done=None, on_row=None):
        self.id = secrets.token_hex(4)
        self.kind, self.label, self.cmd, self.browser, self.background = kind, label, cmd, browser, background
        self.log, self.events, self.question, self.dropped = [], [], None, 0
        self.row = self.screenshot = self.last_shot = self.wait_until = None
        self.done_rows = []
        self.state, self.code, self.stopping = "running", None, False
        self.settling = False  # ended, its on_done (the sheet read again) not finished yet: the page still sees running
        self.started, self.ended = now(), None
        self.on_done, self.on_row = on_done, on_row
        env = {**os.environ, **(env or {}), "PYTHONUNBUFFERED": "1"}
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, bufsize=1, env=env, cwd=config.STATE_DIR, start_new_session=True)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        mark = answers.JsonIO.MARK
        for line in self.proc.stdout:
            line = line.rstrip("\n")
            if not line.startswith(mark):
                self.log.append(line)
                if len(self.log) > LOG_KEEP:
                    self.dropped += len(self.log) - LOG_KEEP
                    del self.log[:-LOG_KEEP]
                continue
            try:
                msg = json.loads(line[len(mark):])
            except ValueError:
                continue
            if msg.get("t") == "ask":
                self.question = msg
            elif msg.get("t") == "event":
                self._event(msg)
        self.code = self.proc.wait()
        self.ended, self.question, self.wait_until = now(), None, None
        self.settling = bool(self.on_done)  # so a finished run never shows its lists from before the sheet was re-read
        self.state = "stopped" if self.stopping else "done" if self.code == 0 else "failed"
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            if self.on_done:
                self.on_done(self)
        finally:
            self.settling = False

    def _event(self, msg):
        self.events.append(msg)
        del self.events[:-200]
        kind = msg.get("kind")
        if kind == "row":
            self.row, self.screenshot, self.wait_until = msg, None, None
        elif kind == "row_done":
            self.done_rows.append(msg)
            if self.on_row:
                self.on_row()
        elif kind == "screenshot":
            self.screenshot = self.last_shot = msg.get("path")
        elif kind == "wait":
            self.wait_until = now() + float(msg.get("seconds") or 0)

    def answer(self, qid, reply):
        q = self.question
        if q is None or q.get("id") != qid or self.state != "running":
            return False
        self.question = None
        try:
            self.proc.stdin.write(json.dumps({**reply, "id": qid}) + "\n")
            self.proc.stdin.flush()
        except OSError:
            return False
        return True

    def stop(self, force=False):
        """SIGINT to the run's own process: the KeyboardInterrupt path Ctrl+C takes, while the browser stays up to be
        closed cleanly. force: SIGKILL to the whole process group (browser included), for a run that won't stop."""
        if self.state != "running":
            return
        self.stopping = True
        try:
            if force:
                os.killpg(self.proc.pid, signal.SIGKILL)
            else:
                os.kill(self.proc.pid, signal.SIGINT)
        except (ProcessLookupError, PermissionError):  # it just ended (macOS: EPERM while it is not yet reaped)
            pass

    def total(self):
        return self.dropped + len(self.log)

    def view(self, after=0):
        """after: how many log lines the page already has (counted from the first line ever)."""
        return {"id": self.id, "kind": self.kind, "label": self.label, "state": "running" if self.settling else self.state,
                "code": self.code,
                "started": self.started, "ended": self.ended, "log_total": self.total(),
                "log": self.log[max(0, after - self.dropped):], "question": self.question,
                "row": self.row, "done_rows": self.done_rows, "wait_until": self.wait_until,
                "screenshot": shot_url(self.screenshot if self.state == "running" else self.last_shot),
                "browser": self.browser, "background": self.background}


def shot_url(path):
    return f"/api/file?path={path}" if path else None


# ---------------------------------------------------------------- the app (one per process)

class App:
    def __init__(self):
        self.token = secrets.token_urlsafe(24)
        self.open_secret = secrets.token_urlsafe(16)
        self.port = None
        self.lock = threading.RLock()
        self.task = None           # the running or most recent user task
        self.background = None     # the running background check (the NUworks browser check)
        self.nuworks_owed = False  # that check gave the browser up, or could not start: run it once the browser is free
        self.history = []
        self.results = {}          # (check id, group) -> health.Check
        self.running_groups = set()
        self.next_run = {}         # group -> time
        self.notified = {}         # check id -> (status, time) of the last desktop notification
        self.first_round_done = False
        self.rows, self.rows_at, self.rows_error, self.rows_try_at = None, 0, None, 0
        self.queue = {}            # job id -> pool entry, from the last review queue
        self.last_request = now()
        self.stopping = False
        self.window_kind = "none"  # "mac" / "gtk" (NUauto's own window), "app" / "tab" (a browser), "none"

    # ---- tasks

    def start(self, kind, label, cmd, browser=True, background=False, env=None, on_done=None):
        if VIEW:  # no child process of any kind: nothing runs, nothing is sent
            raise Refused(VIEW_MESSAGE)
        with self.lock:
            current = self.task if self.task and self.task.state == "running" else None
            if current and not background:
                raise Busy(f"{current.label} is still running. Wait for it, or stop it first.")
            if background and (current or (self.background and self.background.state == "running")):
                return None
            if not background and self.background and self.background.state == "running" and browser:
                self.background.stop()  # your action first: the background check gives the browser up
                try:
                    self.background.proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    self.background.stop(force=True)
                    self.background.proc.wait(timeout=10)
            holder = None
            if browser:
                from nuauto import browser as browser_
                holder = browser_.profile_holder()
            if holder:
                raise Busy(f"The NUworks browser is in use by another nuauto run ({holder.get('what')}, "
                           f"pid {holder.get('pid')}). Try again when it is done.")
            task = Task(kind, label, cmd, browser, background, env, on_done=self._done(on_done),
                        on_row=None if background else self.refresh_soon)
            if background:
                self.background = task
            else:
                self.task = task
            return task

    def _done(self, extra):
        def done(task):
            with self.lock:
                if not task.background:
                    self.history.insert(0, task)
                    del self.history[HISTORY:]
            if extra:
                extra(task)
            if not task.background:
                if self.nuworks_owed:  # the NUworks check waited for this run: the browser is free now
                    self.nuworks_owed = False
                    self.run_now(["nuworks"])
                self.refresh_rows(force=True)
        return done

    # ---- sheet rows (cached)

    def refresh_rows(self, force=False):
        if not force and self.rows is not None and now() - self.rows_at < 60:
            return
        for _ in range(150):  # a read already under way (e.g. the monitor's): wait for it, up to 30 s
            if "sheet" not in self.running_groups:
                break
            time.sleep(0.2)
        else:
            return
        if force or self.rows is None or now() - self.rows_at >= 60:
            self.run_group("sheet")

    def refresh_soon(self, max_age=0):
        """Read the sheet again in the background when the rows are older than max_age seconds, so the lists and counts
        follow changes made elsewhere (a row finished in a run, an assistant in a terminal, the sheet itself). A failed
        read is not tried again for 30 s."""
        if self.rows is None or "sheet" in self.running_groups:
            return
        if now() - max(self.rows_at, self.rows_try_at) < max(max_age, 1):
            return
        self.rows_try_at = now()
        threading.Thread(target=self.refresh_rows, kwargs={"force": True}, daemon=True).start()

    # ---- health

    GROUPS = {  # group -> (check names, seconds between runs, first run after)
        "quick": (["files", "resume", "google", "nuworks_saved", "updates"], 60, 0),
        "sheet": (["sheet"], 900, 0),
        "claude": (["claude"], 900, 1),
        "firefox": (["firefox"], 86400, 2),
        "discord": (["discord"], 6 * 3600, 3),
        "homelab": (["homelab"], 900, 3),
        "nuworks": (["nuworks"], 6 * 3600, 8),
        "claude_live": (["claude_live"], 86400, 45),
        "app": (["app_update"], 86400, 30),
    }

    def run_group(self, group):
        if VIEW and group not in VIEW_GROUPS:
            return
        if group == "nuworks":
            return self._nuworks_check()
        with self.lock:
            if group in self.running_groups:
                return
            self.running_groups.add(group)
        try:
            if group == "sheet":
                checks, rows = health.sheet_and_limits()
                with self.lock:
                    if rows is not None:
                        self.rows, self.rows_at, self.rows_error = rows, now(), None
                    else:
                        self.rows_error = checks[0].detail
            else:
                checks = health.run(self.GROUPS[group][0])
            self._store(group, checks)
        finally:
            with self.lock:
                self.running_groups.discard(group)
                self.next_run[group] = now() + self.GROUPS[group][1]

    def _nuworks_check(self):
        """The hidden-browser check runs as its own process (it uses the browser profile, one at a time). Marked
        running until that process ends."""
        with self.lock:
            if "nuworks" in self.running_groups:
                return
            self.running_groups.add("nuworks")
            self.next_run["nuworks"] = now() + self.GROUPS["nuworks"][1]

        def done(task):
            try:
                lines = [line for line in task.log if line.startswith("[")]
                checks = [health.Check(**c) for c in json.loads(lines[-1])]
            except (ValueError, IndexError, TypeError):
                checks = [health.check("nuworks", "NUworks", health.WARN, "The NUworks check did not finish.")]
            keep_old = False
            with self.lock:
                self.running_groups.discard("nuworks")
                if task.stopping:  # it gave the browser up to something you started: again when that ends
                    self.nuworks_owed = True
                if checks and checks[0].status == health.BUSY:
                    self.next_run["nuworks"] = now() + 120  # try again soon
                    keep_old = ("nuworks", "nuworks") in self.results  # keep the last real answer
            if not keep_old and not task.stopping:
                self._store("nuworks", checks)
        try:
            task = self.start("nuworks_check", "Checking NUworks", config.self_cmd("health", "--json", "nuworks"),
                              background=True, on_done=done)
        except Busy:
            task = None
        if task is None:  # something else uses the browser: when your run ends, else (another nuauto) in a few minutes
            with self.lock:
                self.running_groups.discard("nuworks")
                self.nuworks_owed = True
                self.next_run["nuworks"] = now() + 300

    def _store(self, group, checks):
        with self.lock:
            for key in [k for k in self.results if k[1] == group]:
                del self.results[key]
            for c in checks:
                self.results[(c.id, group)] = c
        self.notify_changes()

    def checks(self):
        """One check per id. The quick (files-only) answer shows only while nothing better is known, or when it
        is a failure; otherwise the newest answer from a real check wins."""
        with self.lock:
            by_id = {}
            for (cid, group), c in self.results.items():
                by_id.setdefault(cid, []).append((group, c))
        out = []
        for cid, found in by_id.items():
            quick = [c for g, c in found if g == "quick"]
            live = sorted((c for g, c in found if g != "quick"), key=lambda c: c.at)
            if quick and quick[0].status == health.FAIL:
                out.append(quick[0])
            elif live:
                out.append(live[-1])
            else:
                out.append(quick[0])
        order = ["files", "google", "sheet", "nuworks", "claude", "resume", "limits", "updates", "firefox",
                 "discord", "homelab", "app"]
        return sorted(out, key=lambda c: order.index(c.id) if c.id in order else 99)

    def notify_changes(self):
        """A desktop notification when a check turns bad (not for what was already bad at start)."""
        if not self.first_round_done or VIEW:
            return
        for c in self.checks():
            last = self.notified.get(c.id)
            bad = c.status in (health.WARN, health.FAIL)
            if bad and (last is None or last[0] != c.status or now() - last[1] > 6 * 3600):
                if last is not None or c.status == health.FAIL:
                    health.desktop_notify(f"NUauto: {c.title}", c.detail)
                self.notified[c.id] = (c.status, now())
            elif not bad:
                self.notified[c.id] = (c.status, now())

    def monitor(self):
        start = now()
        groups = [g for g in self.GROUPS if not VIEW or g in VIEW_GROUPS]
        for group in groups:
            self.next_run[group] = start + self.GROUPS[group][2]
        while not self.stopping:
            for group in groups:
                if now() >= self.next_run.get(group, 0) and group not in self.running_groups:
                    self.next_run[group] = now() + self.GROUPS[group][1]
                    threading.Thread(target=self.run_group, args=(group,), daemon=True).start()
            if not self.first_round_done and now() - start > 15:
                self.first_round_done = True
                for c in self.checks():
                    self.notified[c.id] = (c.status, now())
            idle = now() - self.last_request > IDLE_EXIT
            busy = self.task is not None and self.task.state == "running"
            if idle and not busy:
                self.quit()
            time.sleep(2)

    def run_now(self, groups):
        for g in groups:
            if g in self.GROUPS and (not VIEW or g in VIEW_GROUPS):
                threading.Thread(target=self.run_group, args=(g,), daemon=True).start()

    def quit(self):
        if self.stopping:
            return
        self.stopping = True
        for t in (self.task, self.background):
            if t and t.state == "running":
                t.stop()
                try:
                    t.proc.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    t.stop(force=True)
        if not VIEW:
            remove_lock()
        threading.Thread(target=SERVER.shutdown, daemon=True).start()
        try:
            if VIEW:
                raise RuntimeError  # no windows of its own: never close the real GUI's
            window.close_all()  # Quit from the page: NUauto's own windows close too
        except Exception:
            pass


class Busy(Exception):
    pass


class Refused(Exception):
    """A request the GUI turns down, with a message for the page (HTTP 409)."""


APP = App()
SERVER = None


# ---------------------------------------------------------------- what the pages show

def week_info(rows):
    week, total = sheet.check_limits_safe(rows) if rows is not None else (0, 0)
    first, label = sheet.week_window()
    nxt = (first + timedelta(days=7)) if config.WEEK_START and date.today() >= config.WEEK_START else None
    return {"applied": week, "max": sheet.max_per_week(), "label": label, "total": total, "max_total": sheet.MAX_TOTAL,
            "next": f"{nxt:%a %b} {nxt.day}" if nxt else None, "room": max(0, sheet.max_per_week() - week)}


def row_view(r, **extra):
    return {"row": r.number, "company": r.company, "title": r.title, "url": r.url, "status": r.status,
            "notes": r.notes, "date": r.date, **extra}


def closing(r):
    """When the row's NUworks posting closes (from the saved job data): the date, its text, whether it is past or
    within a week. All empty/false when the job is unknown or lists no deadline."""
    from nuauto import apply
    day = apply.row_closes(r)
    left = (day - date.today()).days if day else None
    return {"closes": day.isoformat() if day else None, "closes_text": jobs.closes_text(day),
            "past": left is not None and left < 0, "soon": left is not None and 0 <= left <= jobs.URGENT_DAYS}


NUWORKS_SIDE_NOT_SENT = ("NUworks side NOT submitted", "before Submit; not submitted")


def facts_data():
    """What job_facts reads, loaded once per list: the scores, the pool by job id, the kinds of work."""
    cats, sets = jobs.load("categories.json", {}), jobs.load("category_sets.json", {})
    known = {i: c for i, c in cats.items() if jobs.categorized(cats, sets, i)}  # sorted into your current kinds
    return jobs.load("scores.json", {}), {p["id"]: p for p in jobs.load("pool.json", [])}, known


def job_facts(r, data, d=None):
    """The match %, score, pay and kind of work the Apply lists show (and sort / filter by) for a NUworks row (None
    when not scored / not in the pool / not listed). score is the pool rank Start orders by (match + bonuses);
    pay_hour the top of the hourly range, only when the pay reads as one (insights.hourly)."""
    from nuauto import apply, insights
    scores, pool, categories = data
    d = apply.row_details(r) if d is None else d
    i = jobs.job_id(r.url)
    p = pool.get(i)
    pay = d.get("pay") or None
    hour = insights.hourly(pay)
    cat = p["category"] if p else categories.get(i)
    cat = cat if cat in jobs.CATEGORIES else None  # not sorted into your current kinds yet
    return {"match": (scores.get(i) or {}).get("match"), "pay": pay, "pay_hour": hour[1] if hour else None,
            "score": p["rank"] if p else None, "score_text": jobs.rank_text(p) if p else None,
            "category": cat, "category_label": jobs.category_label(cat) if cat else None}


def company_rows(rows, facts=False):
    """agent: Needs Human rows for the assistant; other: Needs Human rows only you can finish; site: Applied rows
    whose company site still wants you; retry: Applied rows whose NUworks side did not go out (nuauto assist nuworks).
    facts: each row also gets its match % and pay (the Apply tabs; the menu counts skip reading them)."""
    from nuauto import assist
    data = facts_data() if facts else None

    def view(r, **extra):
        return row_view(r, **closing(r), **(job_facts(r, data) if facts else {}), **extra)
    agent, other, site, retry = [], [], [], []
    for r in rows or []:
        if r.status == "Needs Human":
            url, why = assist.assist_target(r.notes)
            if url:
                agent.append(view(r, target=url, host=urlparse(url).hostname or url))
            else:
                other.append(view(r, why=why))
        elif r.status == "Applied" and r.notes.startswith(sheet.SITE_MARK):
            site.append(view(r))
        elif r.status == "Applied" and any(m in r.notes for m in NUWORKS_SIDE_NOT_SENT) \
                and assist.nuworks_side_blocked(r.notes) is None:
            retry.append(view(r))
    return agent, other, site, retry


def review_counts(rows):
    """Cheap counts for the menu (no ranking model): pool jobs not in the sheet and not rated no."""
    pool = jobs.load("pool.json", [])
    ratings = jobs.load("ratings.json", {})
    in_sheet = {jobs.job_id(r.url) for r in rows or []}
    todo = [r for r in pool if r["id"] not in in_sheet and ratings.get(r["id"], {}).get("label") != 0]
    today = date.today()
    soon = [r for r in todo if r.get("closes") and 0 <= (date.fromisoformat(r["closes"]) - today).days <= 3]
    return todo, soon


def setup_needed():
    """From the files only (the page asks every few seconds); the Setup screen checks each step for real."""
    cfg = read_local()
    return not (cfg.get("tos_ack") and cfg.get("sheet_id") not in (None, "", health.PLACEHOLDER_SHEET)
                and cfg.get("preferences") and os.path.isfile(os.path.expanduser(cfg.get("resume_path") or ""))
                and health.resume_label() and os.path.exists(config.TOKEN_PATH))


def read_local():
    try:
        with open(config.LOCAL_CONFIG_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def state():
    rows = APP.rows
    todo, soon = review_counts(rows)
    agent, other, site, retry = company_rows(rows)
    approved = [r for r in rows or [] if r.status == "Approved"]
    scans = jobs.load("scans.json", [])
    cfg = read_local()
    sheet_id = cfg.get("sheet_id") or ""
    task = APP.task
    return {
        "demo": config.DEMO, "view": VIEW, "version": __import__("nuauto").__version__,
        "mode": "homelab" if config.HAS_SERVER else "local", "setup_needed": setup_needed(),
        "rows_loaded": rows is not None, "rows_at": APP.rows_at, "rows_error": APP.rows_error, "week": week_info(rows),
        "counts": {"review": len(todo), "approved": len(approved), "company": len(agent) + len(retry), "site": len(site),
                   "apply": len(approved) + len(agent) + len(retry),  # the Apply menu item: its rows + Company sites
                   "needs_human": len(agent) + len(other)},
        "todo": sorted(([{"kind": "site", **x} for x in site] + [{"kind": "company", **x} for x in agent]
                        + [{"kind": "urgent", "id": r["id"], "title": r["title"], "company": r["company"],
                            "closes": r["closes"], "match": r["match"], "url": jobs.job_url(r["id"]),
                            "closes_text": jobs.closes_text(date.fromisoformat(r["closes"])), "past": False, "soon": True}
                           for r in soon]),
                       key=lambda t: t["closes"] or "9999"),  # soonest deadline first; no deadline last
        "last_update": scans[-1] if scans else None,
        "sheet_url": f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit" if sheet_id and not config.DEMO else None,
        "task": task.view(task.total()) if task else None,
    }


def review(mode, q="", category=""):
    """The review queue; q (words or "quoted phrases", all must appear) and category narrow what is shown.
    Decisions work on any job of the whole queue."""
    rows = need_rows()
    details, ratings = jobs.load_details(), jobs.load("ratings.json", {})
    todo, urgent = jobs.review_queue(mode, details, ratings, rows)
    APP.queue = {r["id"]: r for r in todo}
    counts = {}
    for r in todo:
        counts[r["category"]] = counts.get(r["category"], 0) + 1
    terms = jobs.search_terms(q)
    shown = [r for r in todo if (not category or r["category"] == category)
             and jobs.search_match(r, details[r["id"]], terms)]
    out, urgent_ids = [], {r["id"] for r in urgent}
    for r in shown:
        d = details[r["id"]]
        day = jobs.closes(d)
        why = "rate" if mode == "rate" else "urgent" if r["id"] in urgent_ids else "proposed" if "row" in r else "pool"
        out.append({"id": r["id"], "title": r["title"], "company": r["company"], "category": r["category"],
                    "category_label": jobs.category_label(r["category"]),
                    "order": jobs.order_text(r, why),
                    "match": r["match"], "closes_text": jobs.closes_text(day),
                    "soon": day is not None and (day - date.today()).days <= jobs.URGENT_DAYS,
                    "rating": (ratings.get(r["id"]) or {}).get("label")})
    return {"jobs": out, "urgent": [r["id"] for r in urgent], "mode": mode, "total": len(todo),
            "categories": [{"key": k, "label": jobs.category_label(k), "count": counts[k]}
                           for k in sorted(counts, key=lambda k: (-counts[k], k))]}


def job(job_id):
    if not re.fullmatch(r"[A-Za-z0-9]{1,64}", job_id):
        raise NotFound()
    r = APP.queue.get(job_id) or next((x for x in jobs.load("pool.json", []) if x["id"] == job_id), None)
    details = os.path.join(config.DATA_DIR, "details", f"{job_id}.json")
    if r is None or not os.path.exists(details):
        raise NotFound()
    with open(details) as f:
        return jobs.job_view(r, json.load(f))


def need_rows():
    APP.refresh_rows()
    if APP.rows is None:
        raise Refused(APP.rows_error or "Can't read the Google Sheet right now.")
    return APP.rows


class NoOtherTab(Refused):
    """The sheet has no Other jobs tab yet (adding the first job makes it)."""


def worksheet(other=False):
    """The main tab, or (other) the Other jobs tab. Never a login page."""
    try:
        return sheet.open_other(interactive=False) if other else sheet.open_worksheet(interactive=False)
    except sheet.NotLoggedIn as e:
        raise Refused(f"{e} Log in to Google first (Settings, or the Google dot at the top).")
    except sheet.NoOtherTab as e:
        raise NoOtherTab(str(e))
    except sheet.SheetError as e:
        raise Refused(str(e))


def decide(body):
    """Review answers. approve: the job goes in the sheet as Approved now (and counts as a yes for the ranking);
    yes / no: a rating only (no is skipped by Review from now on). Returns the new row for approve."""
    job_id, decision, mode = str(body.get("id", "")), body.get("decision"), body.get("mode")
    r = APP.queue.get(job_id)
    if r is None:
        raise Refused("That job is not in the current review list; reload it.")
    if decision not in ("approve", "yes", "no") or (decision == "approve" and mode != "approve"):
        raise Refused("Unknown decision.")
    ratings = jobs.load("ratings.json", {})
    previous = ratings.get(job_id)
    out = {"previous": previous}
    if decision == "approve":
        ws = worksheet()
        if r.get("row"):  # one of your Proposed rows: that row becomes Approved
            try:
                sheet.approve_proposed(ws, r["row"], jobs.job_url(job_id))
            except sheet.SheetError as e:
                raise Refused(str(e))
        elif sheet.add_proposed(ws, [jobs.sheet_item(r)], status="Approved") != 1:
            raise Refused("That job is already in the sheet.")
        APP.refresh_rows(force=True)
        out["row"] = next((x.number for x in APP.rows or [] if x.url == jobs.job_url(job_id)), None)
    ratings[job_id] = {"label": int(decision != "no"), "date": date.today().isoformat()}
    jobs.save("ratings.json", ratings)
    return out


def undo(body):
    job_id, previous = str(body.get("id", "")), body.get("previous")
    if body.get("row"):
        ws = worksheet()
        try:
            sheet.unapprove(ws, int(body["row"]), jobs.job_url(job_id))
        except (sheet.SheetError, ValueError) as e:
            raise Refused(str(e))
        APP.refresh_rows(force=True)
    ratings = jobs.load("ratings.json", {})
    if isinstance(previous, dict) and previous.get("label") in (0, 1):
        ratings[job_id] = {"label": previous["label"], "date": str(previous.get("date", ""))[:10]}
    else:
        ratings.pop(job_id, None)
    jobs.save("ratings.json", ratings)
    return {}


def review_done():
    """Like the end of `nuauto approve` / `nuauto rate`: ratings (and a fresh token) go to the homelab."""
    if config.HAS_SERVER and not config.IS_SERVER:
        from nuauto import sync
        threading.Thread(target=sync.push, daemon=True).start()
    return {}


def insights_get():
    """Pay, places, kinds of work (pool and applied) and the sheet's statuses. The pool numbers work without the sheet."""
    from nuauto import insights
    try:
        APP.refresh_rows()
    except Exception:
        pass
    return insights.collect(APP.rows)


def apply_list(order="default"):
    """The Approved rows in the order a run goes (order: apply.ORDERS, the Sort menu), the week, why Start can't run."""
    from nuauto import apply
    if order not in apply.ORDERS:
        order = "default"
    rows = need_rows()
    week = week_info(rows)
    out = []
    data = facts_data()
    for r in apply.apply_order(sheet.approved(rows), order):
        d = apply.row_details(r)
        hint = jobs.external_hint(d)  # the posting may want the company's own site too
        out.append(row_view(r, **closing(r), company_site=hint, **job_facts(r, data, d)))
    why = None
    if not out:
        why = "Nothing is Approved. Approve jobs in Review first."
    elif week["applied"] >= week["max"]:
        why = f"Weekly limit reached ({week['applied']}/{week['max']} {week['label']}). Approved rows wait for next week."
    elif week["total"] >= week["max_total"]:
        why = f"Total limit reached ({week['total']}/{week['max_total']})."
    elif not health.resume_label():
        why = "The NUworks resume label is not set (Settings)."
    return {"rows": out, "week": week, "why_not": why, "history": [t.view(t.total()) for t in APP.history[:5]]}


# ---------------------------------------------------------------- actions (buttons that start something)

def demo_cmd(*args):
    return config.self_cmd("demo", *args)


VISIBLE = {"AUTO_HEADLESS": ""}  # runs you watch get a visible browser, whatever this process's environment says


def act(body):
    kind = body.get("kind")
    args = body.get("args") or {}
    if kind == "apply":
        from nuauto import apply
        why = apply_list()["why_not"]
        if why:
            raise Refused(why)
        n, row, order = args.get("n"), args.get("row"), args.get("order") or "default"
        if order not in apply.ORDERS:
            raise Refused("Unknown order (reload the page).")
        cmd = config.self_cmd("apply", "--ui", "json", "--order", order)
        if row not in (None, ""):  # just this one Approved row (its Apply button)
            if not str(row).isdigit() or int(row) not in {r["row"] for r in apply_list()["rows"]}:
                raise Refused("That row is not Approved (reload the list).")
            cmd += ["--row", str(int(row))]
        elif n not in (None, ""):
            if not str(n).isdigit() or int(n) < 1:
                raise Refused("At most how many? A whole number, 1 or more.")
            cmd += ["-n", str(int(n))]
        task = APP.start("apply", "Applying", cmd, env=VISIBLE, on_done=lambda t: APP.run_now(["quick"]))
    elif kind == "update":
        cmd = demo_cmd("update") if config.DEMO else config.self_cmd("update")
        task = APP.start("update", "Checking for new jobs", cmd, browser=not config.HAS_SERVER,
                         on_done=lambda t: APP.run_now(["quick"]))
    elif kind == "login_nuworks":
        cmd = demo_cmd("login_nuworks") if config.DEMO else config.self_cmd("login")
        task = APP.start("login_nuworks", "NUworks login", cmd, env=VISIBLE,
                         on_done=lambda t: APP.run_now(["quick", "nuworks"]))
    elif kind == "login_google":
        task = APP.start("login_google", "Google login", config.self_cmd("login", "google"), browser=False,
                         on_done=lambda t: APP.run_now(["quick", "sheet"]))
    elif kind == "check_nuworks":
        APP.run_now(["nuworks"])
        return {}
    elif kind == "install_firefox":
        task = APP.start("install_firefox", "Installing the browser", config.self_cmd("_playwright", "install", "firefox"),
                         browser=False, on_done=lambda t: APP.run_now(["firefox"]))
    elif kind == "install_claude":
        cmd = (demo_cmd("install_claude") if config.DEMO
               else ["bash", "-c", "curl -fsSL https://claude.ai/install.sh | bash"])
        task = APP.start("install_claude", "Installing Claude Code", cmd, browser=False,
                         on_done=lambda t: APP.run_now(["claude"]))
    elif kind == "login_claude":
        if config.DEMO:
            task = APP.start("login_claude", "Claude login", demo_cmd("login_claude"), browser=False,
                             on_done=lambda t: APP.run_now(["claude"]))
        else:
            claude = config.tool_path("claude") or "claude"
            return open_terminal(f"{shlex.quote(claude)} auth login", "Log in to Claude Code in this window.")
    elif kind == "nuworks_side":
        row = args.get("row")
        if not str(row).isdigit():
            raise Refused("Which row?")
        task = APP.start("nuworks_side", f"NUworks side of row {row}",
                         config.self_cmd("assist", "nuworks", str(int(row)), "--ui", "json"), env=VISIBLE)
    elif kind == "assist":  # args.tab "other": a row of the Other jobs tab
        row = args.get("row")
        if not str(row).isdigit():
            raise Refused("Which row?")
        tab = "other " if args.get("tab") == "other" else ""
        return open_terminal(f"{shlex.quote(config.self_exe())} assist {tab}{int(row)}",
                             "The assistant runs here. It asks you before anything is submitted.")
    elif kind == "selftest":  # demo mode end to end in its own temp folder: safe while anything else runs
        task = APP.start("selftest", "Self-test", config.self_cmd("selftest"), browser=False)
    elif kind == "fix_permissions":
        health.fix_permissions()
        APP.run_now(["quick"])
        return {}
    else:
        raise Refused(f"Unknown action {kind!r}.")
    return {"task": task.view()}


def answer(body):
    task = APP.task
    if task is None or task.id != body.get("task"):
        raise Refused("That run is over.")
    reply = body.get("reply")
    if not isinstance(reply, dict) or not task.answer(body.get("id"), reply):
        raise Refused("That question is no longer open.")
    return {}


def stop(body):
    task = APP.task
    if task is None or task.id != body.get("task") or task.state != "running":
        raise Refused("Nothing is running.")
    task.stop(force=bool(body.get("force")))
    return {}


# ---------------------------------------------------------------- company sites

def company():
    rows = need_rows()
    agent, other, site, retry = company_rows(rows, facts=True)
    return {"agent": agent, "other": other, "site": site, "retry": retry}


def mark(body):
    action, row, url = body.get("action"), body.get("row"), str(body.get("url", ""))
    other = body.get("tab") == "other"  # a row of the Other jobs tab (only "applied" there)
    if action not in ("applied", "site") or not str(row).isdigit() or (other and action != "applied"):
        raise Refused("Unknown action.")
    ws = worksheet(other)
    try:
        if action == "site":
            sheet.mark_site_done(ws, int(row), url)
        else:
            sheet.mark_applied_by_hand(ws, int(row), url, how="by hand (marked in NUauto)")
    except sheet.SheetError as e:
        raise Refused(str(e))
    if not other:
        APP.refresh_rows(force=True)
    return {}


# ---------------------------------------------------------------- other jobs (the sheet's Other jobs tab)

def other_list():
    """The Other jobs tab, read fresh: ready (Approved: for the assistant), applied (newest first), the rest.
    exists is False until the first job is added (that makes the tab)."""
    from nuauto import assist
    try:
        rows = sheet.read_rows(worksheet(other=True))
    except NoOtherTab:
        return {"exists": False, "tab": sheet.OTHER_TAB, "ready": [], "applied": [], "rest": []}
    ready, applied, rest = [], [], []
    for r in rows:
        if r.status == "Approved":
            url, why = assist.other_target(r)
            ready.append(row_view(r, target=url, host=urlparse(url).hostname if url else None, why=why))
        elif r.status == "Applied":
            applied.append(row_view(r))
        else:
            rest.append(row_view(r))
    applied.sort(key=lambda x: x["date"], reverse=True)
    return {"exists": True, "tab": sheet.OTHER_TAB, "ready": ready, "applied": applied, "rest": rest}


def other_add(body):
    """Add a job that is not on NUworks: the Other jobs tab, Approved (made if missing)."""
    from nuauto import assist
    fields = [str(body.get(k) or "") for k in ("url", "company", "title")]
    try:
        return {"row": assist.add_other(*fields, interactive=False)}
    except sheet.NotLoggedIn as e:
        raise Refused(f"{e} Log in to Google first (Settings, or the Google dot at the top).")
    except sheet.SheetError as e:
        raise Refused(str(e))


# ---------------------------------------------------------------- the Sheet screen: add / move / remove by hand

def sheet_get():
    """Both tabs, read fresh: every row, and why it can't be moved or removed (None = it can)."""
    from nuauto import manage
    out = {}
    for tab in manage.TABS:
        try:
            rows = sheet.read_rows(worksheet(other=tab == "other"))
        except NoOtherTab:
            out[tab] = {"exists": False, "rows": []}
            continue
        out[tab] = {"exists": True, "rows": [row_view(r, locked=manage.movable(r, tab, "move"),
                                                      remove_locked=manage.movable(r, tab, "remove")) for r in rows]}
    return {"tabs": out, "names": manage.TABS, "busy": running("apply", "nuworks_side")}


def sheet_lookup(url):
    from nuauto import manage
    return manage.lookup(url or "")


def sheet_change(body):
    """add {tab, url, company, title, status} / move {tab, row, url, new_url} / remove {tab, row, url}."""
    from nuauto import manage
    action, tab = body.get("action"), body.get("tab")
    if action not in ("add", "move", "remove") or tab not in manage.TABS:
        raise Refused("Unknown sheet change.")
    if action != "add" and running("apply", "nuworks_side"):
        raise Refused("A run is using the sheet: move or remove rows when it is done.")
    row, url = body.get("row"), str(body.get("url") or "")
    if action != "add" and not (isinstance(row, int) and not isinstance(row, bool) and row >= 2):
        raise Refused("Which row?")
    try:
        if action == "add":
            out = {"tab": tab, "row": manage.add(tab, url, body.get("company"), body.get("title"),
                                                 str(body.get("status") or "Approved"), interactive=False)}
        elif action == "move":
            to, n = manage.move(tab, row, url, str(body.get("new_url") or "") or None, interactive=False)
            out = {"tab": to, "row": n}
        else:
            manage.remove(tab, row, url, interactive=False)
            out = {}
    except sheet.NotLoggedIn as e:
        raise Refused(f"{e} Log in to Google first (Settings, or the Google dot at the top).")
    except sheet.SheetError as e:
        raise Refused(str(e))
    APP.refresh_rows(force=True)
    return out


TERMINALS = [  # (command, how it takes a command to run)
    ("x-terminal-emulator", ["-e"]), ("gnome-terminal", ["--"]), ("ptyxis", ["--"]), ("kgx", ["--"]),
    ("konsole", ["-e"]), ("xfce4-terminal", ["-x"]), ("mate-terminal", ["-x"]), ("tilix", ["-e"]),
    ("kitty", []), ("alacritty", ["-e"]), ("wezterm", ["start", "--"]), ("foot", []), ("terminator", ["-x"]),
    ("xterm", ["-e"]),
]


def open_terminal(command, intro):
    """Run a shell command in a new terminal window (you type there: the assistant's questions, Claude's login).
    Returns {"opened": bool, "command": what to paste if no terminal app was found}."""
    script = (f"cd {shlex.quote(config.STATE_DIR)}; echo {shlex.quote(intro)}; echo; {command}; "
              "echo; read -rp 'Done. Press Enter to close this window. ' _")
    if config.DEMO:
        return {"opened": False, "command": command, "demo": True}
    if sys.platform == "darwin":
        apple = f'tell application "Terminal" to do script {json.dumps("bash -lc " + shlex.quote(script))}'
        ok = subprocess.run(["osascript", "-e", apple, "-e", 'tell application "Terminal" to activate'],
                            capture_output=True, env=window.system_env()).returncode == 0
        return {"opened": ok, "command": command}
    candidates = ([(os.environ["TERMINAL"], ["-e"])] if os.environ.get("TERMINAL") else []) + TERMINALS
    for name, flag in candidates:
        path = shutil.which(name)
        if path:
            subprocess.Popen([path, *flag, "bash", "-lc", script], start_new_session=True, env=window.system_env(),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"opened": True, "command": command}
    return {"opened": False, "command": command}


# ---------------------------------------------------------------- answers, logs, files

def bank_path(bank):
    """bank "other": the Other jobs tab's answers (answers_other.json); anything else: answers.json."""
    return config.OTHER_ANSWERS_PATH if bank == "other" else config.ANSWERS_PATH


def answers_get(bank="nuworks"):
    """One bank's entries. answers.json entries carry nuworks_only as it applies (the default list, or the entry's own)."""
    path = bank_path(bank)
    entries = answers.load(path)
    if bank != "other":
        entries = [{**e, "nuworks_only": answers.nuworks_only(e)} for e in entries]
    if VIEW:  # your saved answers are personal (address, phone...): the table shows, the values don't
        entries = [{**e, "answer": "(hidden in view-only mode)" if e.get("answer") else "", "aliases": []} for e in entries]
    try:
        version = os.path.getmtime(path)
    except OSError:
        version = 0
    return {"bank": "other" if bank == "other" else "nuworks", "entries": entries, "version": version,
            "locked": bank != "other" and running("apply", "nuworks_side")}


def running(*kinds):
    t = APP.task
    return bool(t and t.state == "running" and t.kind in kinds)


def answers_put(body):
    bank = "other" if body.get("bank") == "other" else "nuworks"
    if bank == "nuworks" and running("apply", "nuworks_side"):
        raise Refused("A run is using the answer bank. Edit it when the run is over.")
    if body.get("version") != answers_get(bank)["version"]:
        raise Refused("The answer bank changed since you opened it (a run saved an answer). Reload, then edit again.")
    entries = body.get("entries")
    if not isinstance(entries, list):
        raise Refused("Expected a list of entries.")
    clean, seen = [], set()
    for e in entries:
        q = answers.norm(str((e or {}).get("question", "")))
        if not q:
            raise Refused("Every entry needs a question.")
        keys = [q] + [answers.norm(str(a)) for a in e.get("aliases") or [] if str(a).strip()]
        dup = [k for k in keys if k in seen]
        if dup:
            raise Refused(f"{dup[0]!r} appears twice. Each question (and alias) once.")
        seen.update(keys)
        ft = e.get("field_type", "")
        if ft not in ("", "text", "select"):
            raise Refused(f"{q!r}: field type must be text or select.")
        entry = {"question": q, "aliases": keys[1:], "answer": str(e.get("answer", "")).strip(),
                 "field_type": ft, "date_added": str(e.get("date_added") or date.today().isoformat())[:10],
                 "always_ask": bool(e.get("always_ask"))}
        if e.get("leave_blank") and not entry["answer"]:  # "always leave blank" (the assistant's), until you answer it
            entry["leave_blank"] = True
        if bank == "nuworks" and bool(e.get("nuworks_only")) != (q in answers.NUWORKS_ONLY):
            entry["nuworks_only"] = bool(e.get("nuworks_only"))  # only when it differs from the default list
        clean.append(entry)
    answers.save(clean, bank_path(bank))
    return answers_get(bank)


def logs():
    out = []
    if os.path.isdir(config.LOGS_DIR):
        for name in sorted(os.listdir(config.LOGS_DIR), reverse=True)[:60]:
            path = os.path.join(config.LOGS_DIR, name)
            if os.path.isdir(path):
                files = sorted(os.listdir(path))
                out.append({"name": name, "files": [f for f in files if f.endswith((".png", ".log", ".json", ".txt"))
                                                    and f not in ("mcp.json", "settings.json", "state.json")]})
    return {"runs": out}


def log_file(path):
    """A screenshot or log under logs/ (nothing else: real paths, no ..)."""
    real = os.path.realpath(path or "")
    root = os.path.realpath(config.LOGS_DIR)
    if not real.startswith(root + os.sep) or not real.endswith((".png", ".log", ".txt")) or not os.path.isfile(real):
        raise NotFound()
    return real


# ---------------------------------------------------------------- settings

PUBLIC_SETTINGS = ["sheet_id", "resume_path", "week_start", "max_per_week", "assist_read_paths", "server_hostname", "server_ssh",
                   "server_dir", "web_base_url", "web_listen_host", "tos_ack"]


def settings_get():
    cfg = read_local()
    return {"settings": {k: cfg.get(k, "" if k != "assist_read_paths" else []) for k in PUBLIC_SETTINGS},
            "max_per_week": sheet.max_per_week(), "max_per_week_ceiling": sheet.MAX_PER_WEEK_CEILING,
            "resume_label": health.resume_label(), "discord": os.path.exists(config.DISCORD_WEBHOOK_PATH),
            "client_json": health.client_json_found(), "google_login": os.path.exists(config.TOKEN_PATH),
            "tools": {name: config.tool_path(name) for name in ("claude", "npx")},
            "paths": {"state": config.STATE_DIR, "logs": config.LOGS_DIR}, "demo": config.DEMO}


def write_local(updates):
    cfg = read_local()
    cfg.update(updates)
    os.makedirs(config.LOCAL_DIR, mode=0o700, exist_ok=True)
    tmp = config.LOCAL_CONFIG_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, config.LOCAL_CONFIG_PATH)
    restart_needed = {"sheet_id", "resume_path", "week_start", "max_per_week", "server_hostname", "server_ssh", "server_dir"} & set(updates)
    if restart_needed:  # config.py reads local_config.json once, at import
        apply_config(cfg)
    return cfg


def apply_config(cfg):
    """Update this process's config after a settings change (child processes read the file themselves)."""
    config.LOCAL = cfg
    config.SHEET_ID = cfg.get("sheet_id", "")
    config.WEEK_START = date.fromisoformat(cfg["week_start"]) if cfg.get("week_start") else None
    config.LAPTOP_RESUME = os.path.expanduser(cfg.get("resume_path") or "")
    config.RESUME_PATH = config.LAPTOP_RESUME if os.path.exists(config.LAPTOP_RESUME) else \
        os.path.join(config.LOCAL_DIR, "resume.pdf")


def settings_put(body):
    updates = {}
    if "week_start" in body:
        v = str(body["week_start"] or "").strip()
        if v:
            try:
                date.fromisoformat(v)
            except ValueError:
                raise Refused("Week start must be a date like 2026-10-06, or empty.")
        updates["week_start"] = v
    if "max_per_week" in body:
        v = body["max_per_week"]
        if isinstance(v, str) and v.strip().isdigit():
            v = int(v)
        if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= sheet.MAX_PER_WEEK_CEILING:
            raise Refused(f"The weekly limit must be a whole number from 1 to {sheet.MAX_PER_WEEK_CEILING}.")
        updates["max_per_week"] = v
    if "resume_label" in body:
        label = str(body["resume_label"] or "").strip()
        if not label:
            raise Refused("The resume label can't be empty.")
        write_json_secret(config.PROFILE_PATH, {**read_json(config.PROFILE_PATH), "resume_label": label})
    if "assist_read_paths" in body:
        paths = body["assist_read_paths"]
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            raise Refused("Expected a list of folders.")
        updates["assist_read_paths"] = [p.strip() for p in paths if p.strip()]
    if updates:
        write_local(updates)
    APP.run_now(["quick"])
    return settings_get()


def read_json(path):
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json_secret(path, data):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


# ---------------------------------------------------------------- setup wizard (onboard.py does the work)

def setup_get():
    from nuauto import onboard
    found = onboard.steps({c.id: c for c in APP.checks()})
    return {"steps": found, "complete": onboard.complete(found), "demo": config.DEMO}


def setup_post(body):
    from nuauto import onboard
    from nuauto import sheet as sheet_
    a = body.get("action")
    try:
        if a == "ack":
            onboard.save_config({"tos_ack": date.today().isoformat()})
        elif a == "client_upload":
            onboard.save_client(str(body.get("text", "")))
            APP.run_now(["quick"])
        elif a == "client_downloads":
            path = onboard.downloads_client()
            if not path:
                raise Refused("No client_secret_*.json in your Downloads folder.")
            with open(path) as f:
                onboard.save_client(f.read())
            APP.run_now(["quick"])
        elif a == "sheet_create":
            new_id = sheet_.create_sheet("NUauto jobs")
            onboard.open_and_prepare(new_id)
            onboard.save_config({"sheet_id": new_id})
            APP.refresh_rows(force=True)
        elif a == "sheet_link":
            sid = onboard.sheet_id_from(body.get("text"))
            onboard.open_and_prepare(sid)
            onboard.save_config({"sheet_id": sid})
            APP.refresh_rows(force=True)
        elif a == "resume_pick":
            return {"path": onboard.pick_file()}
        elif a == "resume_set":
            path = os.path.abspath(os.path.expanduser(str(body.get("path", "")).strip()))
            ok, text = onboard.resume_preview(path)
            if not ok:
                raise Refused(text)
            onboard.save_config({"resume_path": path})
            APP.run_now(["quick"])
        elif a == "labels_read":
            task = APP.start("onboard_labels", "Reading your resumes on NUworks", config.self_cmd("onboard", "resume-labels"))
            return {"task": task.view()}
        elif a == "terms_read":
            task = APP.start("onboard_terms", "Reading the term list on NUworks", config.self_cmd("onboard", "terms"))
            return {"task": task.view()}
        elif a == "label_set":
            label = str(body.get("label", "")).strip()
            if not label:
                raise Refused("Pick a resume.")
            write_json_secret(config.PROFILE_PATH, {**read_json(config.PROFILE_PATH), "resume_label": label})
            APP.run_now(["quick"])
        elif a == "prefs_save":
            onboard.save_config({"preferences": onboard.check_prefs(body.get("prefs"))})
            # the pool at once by the new preferences (bars, bonuses, kinds of work: jobs sorted into kinds you no longer
            # have show as not sorted yet until the next update sorts them), as the update's pool step builds it
            jobs.save("pool.json", jobs.build_pool())
            review_done()  # homelab mode: the homelab's next update uses them too
        elif a == "discord_save":
            onboard.save_webhook(body.get("url"))
            APP.run_now(["discord"])
        elif a == "discord_test":
            return {"message": onboard.test_webhook()}
        elif a == "discord_remove":
            if os.path.exists(config.DISCORD_WEBHOOK_PATH):
                os.remove(config.DISCORD_WEBHOOK_PATH)
            APP.run_now(["discord"])
        elif a == "schedule_on":
            onboard.scheduler_enable()
        elif a == "schedule_off":
            onboard.scheduler_disable()
        elif a == "launcher":
            onboard.launcher_create()
        else:
            raise Refused(f"Unknown setup action {a!r}.")
    except onboard.Refused as e:
        raise Refused(str(e))
    except sheet_.SheetError as e:
        raise Refused(str(e))
    return setup_get()


# ---------------------------------------------------------------- HTTP

class NotFound(Exception):
    pass


# `nuauto gui --view`: the real sheet and job data, read-only. Nothing can be changed or started: every POST except
# these is refused by the server (so a button, or a route added later, can't act), the monitor runs only the
# read-only checks, there is no window, no lock file, no homelab sync, no desktop notification.
VIEW = False
VIEW_POSTS = {"/api/quit"}
VIEW_GROUPS = ("quick", "sheet")
VIEW_MESSAGE = "View-only mode: nothing can be changed or started here."


GET_ROUTES = {
    "/api/state": lambda q: (APP.refresh_soon(30), state())[1],
    "/api/health": lambda q: {"checks": [c.to_dict() for c in APP.checks()], "running": sorted(APP.running_groups)},
    "/api/review": lambda q: review(q.get("mode", "approve") if q.get("mode") in ("approve", "rate") else "approve",
                                    q.get("q", "")[:200], q.get("category", "")[:40]),
    "/api/apply": lambda q: apply_list(q.get("order", "default")),
    "/api/insights": lambda q: insights_get(),
    "/api/company": lambda q: company(),
    "/api/task": lambda q: {"task": APP.task.view(int(q.get("after", 0) or 0)) if APP.task else None},
    "/api/answers": lambda q: answers_get(q.get("bank", "nuworks")),
    "/api/other": lambda q: other_list(),
    "/api/sheet": lambda q: sheet_get(),
    "/api/sheet/lookup": lambda q: sheet_lookup(q.get("url")),
    "/api/logs": lambda q: logs(),
    "/api/settings": lambda q: settings_get(),
    "/api/setup": lambda q: setup_get(),
}
POST_ROUTES = {
    "/api/action": act,
    "/api/answer": answer,
    "/api/stop": stop,
    "/api/decide": decide,
    "/api/undo": undo,
    "/api/review/done": lambda body: review_done(),
    "/api/mark": mark,
    "/api/other/add": other_add,
    "/api/sheet": sheet_change,
    "/api/answers": answers_put,
    "/api/settings": settings_put,
    "/api/setup": setup_post,
    "/api/health/run": lambda body: APP.run_now(body.get("groups") or ["quick", "sheet", "claude"]) or {},
    "/api/quit": lambda body: (threading.Timer(0.3, APP.quit).start(), {})[1],
}


class Handler(BaseHTTPRequestHandler):
    server_version = "nuauto"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass  # URLs can hold the start secret: no access log

    # ---- checks every request goes through

    def _host_ok(self):
        return self.headers.get("Host", "") in (f"127.0.0.1:{APP.port}", f"localhost:{APP.port}")

    def _cookie(self):
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == f"nuauto_{APP.port}":
                return value
        return ""

    def _authed(self):
        return hmac.compare_digest(self._cookie().encode(), APP.token.encode())

    def _send(self, code, body, ctype="application/json", headers=None):
        data = body if isinstance(body, bytes) else (json.dumps(body) if ctype == "application/json" else body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith(("text/", "application/j")) else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; "
                         "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self._host_ok():
            return self._error(403, "wrong host")
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/" and "t" in q:  # the start URL: swap the secret for a cookie, then drop it from the address
            if not hmac.compare_digest(q["t"].encode(), APP.token.encode()):
                return self._send(403, PAGE_GONE, "text/html")
            return self._send(303, b"", "text/plain", {
                "Location": "/", "Set-Cookie": f"nuauto_{APP.port}={APP.token}; HttpOnly; SameSite=Strict; Path=/"})
        if not self._authed():
            return self._send(403, PAGE_GONE, "text/html")
        APP.last_request = now()
        if u.path == "/":
            return self._static("index.html")
        if u.path.startswith("/static/"):
            return self._static(u.path[len("/static/"):])
        if u.path == "/api/file":
            if VIEW:  # screenshots of filled forms and run logs hold personal details
                return self._error(403, "Run logs and screenshots are hidden in view-only mode.")
            try:
                path = log_file(q.get("path"))
            except NotFound:
                return self._error(404, "not found")
            with open(path, "rb") as f:
                return self._send(200, f.read(), mimetypes.guess_type(path)[0] or "application/octet-stream")
        if u.path.startswith("/api/job/"):
            return self._call(lambda: job(u.path[len("/api/job/"):]))
        route = GET_ROUTES.get(u.path)
        if route is None:
            return self._error(404, "not found")
        self._call(lambda: route(q))

    MAX_BODY = 2_000_000

    def _read_body(self):
        """The request body, read before anything else: a rejected request must not leave it on a kept-alive
        connection, where it would be read as the start of the next request. None if missing or too big."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if not 0 <= length <= self.MAX_BODY:
            self.close_connection = True
            return None
        return self.rfile.read(length)

    def _error(self, code, msg):
        if code >= 400 and code != 404 and code != 409:
            self.close_connection = True  # nothing more on this connection after a refusal
        self._send(code, {"error": msg})

    def do_POST(self):
        raw = self._read_body()
        if not self._host_ok():
            return self._error(403, "wrong host")
        u = urlparse(self.path)
        if VIEW and u.path not in VIEW_POSTS:
            return self._error(403, VIEW_MESSAGE)
        if u.path == "/api/window":  # a second `nuauto gui` asks this one to open a window
            if hmac.compare_digest(self.headers.get("X-NUauto-Open", "").encode(), APP.open_secret.encode()):
                window.another(start_url(), APP.window_kind, APP.quit)
                return self._send(200, {})
            return self._error(403, "forbidden")
        origin = self.headers.get("Origin")
        if (not self._authed() or self.headers.get("X-NUauto") != "1"
                or not self.headers.get("Content-Type", "").startswith("application/json")
                or (origin and origin not in (f"http://127.0.0.1:{APP.port}", f"http://localhost:{APP.port}"))):
            return self._error(403, "forbidden")
        APP.last_request = now()
        try:
            body = json.loads(raw or b"{}") if raw is not None else None
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return self._error(400, "expected a JSON object")
        route = POST_ROUTES.get(u.path)
        if route is None:
            return self._error(404, "not found")
        self._call(lambda: route(body))

    def _call(self, fn):
        try:
            return self._send(200, fn())
        except NotFound:
            return self._error(404, "not found")
        except (Refused, Busy) as e:
            return self._error(409, str(e))
        except Exception as e:  # never a secret: only the error type and a short message
            return self._error(500, f"{type(e).__name__}: {str(e)[:200]}")

    def _static(self, name):
        if not re.fullmatch(r"[a-z0-9_-]+\.(html|css|js|svg|png|ico)", name):
            return self._error(404, "not found")
        path = os.path.join(STATIC, name)
        if not os.path.isfile(path):
            return self._error(404, "not found")
        with open(path, "rb") as f:
            data = f.read()
        ctype = {"html": "text/html", "css": "text/css", "js": "text/javascript", "svg": "image/svg+xml",
                 "png": "image/png", "ico": "image/x-icon"}[name.rsplit(".", 1)[1]]
        self._send(200, data, ctype)


PAGE_GONE = """<!doctype html><meta charset=utf-8><title>NUauto</title>
<body style="font:16px system-ui;max-width:30rem;margin:4rem auto;padding:0 1rem">
<h1>NUauto</h1><p>This link is from an earlier start, or it is not yours. Open NUauto from its icon or run
<code>nuauto gui</code>.</p></body>"""


# ---------------------------------------------------------------- start, single instance, window

def start_url():
    return f"http://127.0.0.1:{APP.port}/?t={APP.token}"


def write_lock():
    os.makedirs(config.LOCAL_DIR, mode=0o700, exist_ok=True)
    write_json_secret(config.GUI_LOCK_PATH, {"pid": os.getpid(), "port": APP.port, "open": APP.open_secret,
                                             "demo": config.DEMO})


def remove_lock():
    try:
        if read_json(config.GUI_LOCK_PATH).get("pid") == os.getpid():
            os.remove(config.GUI_LOCK_PATH)
    except OSError:
        pass


def running_instance():
    """The lock of a GUI already running on this machine (and answering), or None."""
    lock = read_json(config.GUI_LOCK_PATH)
    try:
        os.kill(int(lock["pid"]), 0)
    except (KeyError, ValueError, TypeError, OSError):
        return None
    return lock


def ask_instance_to_open(lock):
    import urllib.request
    req = urllib.request.Request(f"http://127.0.0.1:{lock['port']}/api/window", data=b"{}", method="POST",
                                 headers={"X-NUauto-Open": lock["open"], "Host": f"127.0.0.1:{lock['port']}"})
    try:
        urllib.request.urlopen(req, timeout=5).close()
        return True
    except OSError:
        return False


SCREENS = ["home", "review", "apply", "sheet", "insights", "answers", "settings", "setup", "logs"]


def screenshots(folder, url):
    """Every screen in headless Firefox, light and dark, desktop and phone width: <screen>-<scheme>-<width>.png.
    For reviewing the design (agents too). Demo mode only, so no real data ends up in a picture."""
    from playwright.sync_api import sync_playwright
    os.makedirs(folder, exist_ok=True)
    base = url.split("/?")[0]
    with sync_playwright() as p:
        b = p.firefox.launch(headless=True)
        for scheme in ("light", "dark"):
            for width, label in ((1280, "wide"), (420, "narrow")):
                ctx = b.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
                page = ctx.new_page()
                page.goto(url)  # the start URL: sets the cookie
                for name in SCREENS:
                    page.goto(f"{base}/#/{name}")
                    page.wait_for_timeout(1500)
                    page.screenshot(path=os.path.join(folder, f"{name}-{scheme}-{label}.png"), full_page=True)
                ctx.close()
        b.close()
    print(json.dumps({"screenshots": folder, "count": len(SCREENS) * 4}), flush=True)


def pull_loop():
    """Homelab mode: the job data comes from the homelab (like the pull before `nuauto approve`), now and every
    30 minutes. Nothing is pushed on a timer: pushes happen after Review and the Google login, as in the CLI."""
    from nuauto import sync
    while not APP.stopping:
        if sync.pull():
            APP.run_now(["quick"])
        time.sleep(1800)


def start_demo(argv):
    """`nuauto gui --demo`: make the demo folder, then run the GUI again in demo mode (config.py reads the
    environment at import, so this needs a fresh process)."""
    states = ""
    if "--demo-state" in argv:
        i = argv.index("--demo-state")
        states = argv[i + 1] if i + 1 < len(argv) else ""
        del argv[i:i + 2]
    folder = tempfile.mkdtemp(prefix="nuauto-demo-")
    env = {**os.environ, "NUAUTO_STATE_DIR": folder, "NUAUTO_DEMO": "1"}
    r = subprocess.run(config.self_cmd("demo", "setup", states), env=env, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(r.stderr.strip() or r.stdout.strip())
    os.execve(sys.executable, config.self_cmd("gui", *[a for a in argv if a != "--demo"]), env)


def main(argv):
    global VIEW
    argv = list(argv)
    if "--demo" in argv and not config.DEMO:
        return start_demo(argv)
    if config.IS_SERVER:
        sys.exit("nuauto gui runs on your laptop, not the homelab.")
    VIEW = "--view" in argv
    if VIEW and config.DEMO:
        sys.exit("--view is for your real data; --demo is already fake. Use one of them.")
    if os.environ.get("CLAUDECODE") and not config.DEMO and not VIEW:
        sys.exit("The real NUauto window never starts inside a Claude Code session (it can submit applications). "
                 "Agents use `nuauto gui --demo` (fake data) or `nuauto gui --view` (your real data, read-only).")
    port = 0
    if "--port" in argv:
        i = argv.index("--port")
        if i + 1 >= len(argv) or not argv[i + 1].isdigit():
            sys.exit(__doc__)
        port = int(argv[i + 1])
    no_open = "--no-open" in argv or VIEW  # a view has no window of its own: it prints its URL
    shots = None
    if "--screenshots" in argv:
        i = argv.index("--screenshots")
        if not config.DEMO or i + 1 >= len(argv):
            sys.exit("--screenshots DIR works only with --demo.")
        shots, no_open = os.path.abspath(argv[i + 1]), True
    lock = None if VIEW else running_instance()  # a view never touches the running GUI's lock
    if lock and not port:
        if no_open:
            sys.exit(f"NUauto is already running (pid {lock['pid']}). Use its window, or quit it first.")
        if ask_instance_to_open(lock):
            print("NUauto is already running: opened another window.")
            return
    global SERVER
    SERVER = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    SERVER.daemon_threads = True
    APP.port = SERVER.server_address[1]
    if not VIEW:
        write_lock()
    threading.Thread(target=APP.monitor, daemon=True).start()
    if config.HAS_SERVER and not VIEW:  # a view copies and changes no files
        threading.Thread(target=pull_loop, daemon=True).start()
    url = start_url()
    print(json.dumps({"url": url, "port": APP.port, "demo": config.DEMO, "view": VIEW, "state_dir": config.STATE_DIR}), flush=True)
    served = threading.Thread(target=SERVER.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True)
    served.start()  # the main thread is for the window (macOS needs it there)
    if shots:
        def shoot():
            try:
                screenshots(shots, url)
            finally:
                APP.quit()
        threading.Thread(target=shoot, daemon=True).start()

    def bye(*_):
        threading.Thread(target=APP.quit, daemon=True).start()
    signal.signal(signal.SIGTERM, bye)
    APP.window_kind = "none" if no_open else window.kind(browser_tab="--browser" in argv)
    try:
        if APP.window_kind == "mac":
            window.run_mac(url)  # until its window is closed: that quits NUauto
            APP.quit()
        elif APP.window_kind == "gtk":
            window.open_gtk(url, on_last_close=APP.quit)
        elif APP.window_kind in ("app", "tab"):
            window.open_browser(url, browser_tab=APP.window_kind == "tab")
        while served.is_alive():
            served.join(timeout=0.5)
    except KeyboardInterrupt:
        APP.quit()
        served.join(timeout=30)
    finally:
        if not VIEW:
            remove_lock()


if __name__ == "__main__":
    main(sys.argv[1:])
