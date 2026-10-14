"""The NUauto window: `nuauto gui`. A small local web app (docs/GUI.md). On the laptop; and the same app on the homelab.

  nuauto gui                      open it (starting it again just opens another window on the running one)
  nuauto gui --demo [--demo-state a,b] [--remote]
                                  everything fake (demo.py) in a temp folder: for trying it out, and for agents;
                                  --remote: as the homelab serves it (pairing, its assistant sessions; prints a pairing link)
  nuauto gui --view               READ-ONLY on your real data, for agents (and a quick look): no window, no lock file,
                                  the server refuses every action (see VIEW), answers and run screenshots are hidden
  options: --port N (default: any free port), --no-open (print the URL instead of opening a window),
           --browser (a normal browser tab instead of an app window),
           --screenshots DIR (demo only: every screen, light and dark, wide and narrow, saved as PNGs; then quits)
On the homelab (config.REMOTE_APP) `nuauto web` serves the same app for your phone: serve_remote, below.

How it is safe to run:
- On the laptop it listens on 127.0.0.1 only. A secret made at each start is in the URL it opens once; the page swaps
  it for a cookie. Every request needs that cookie and a Host header naming this server; every POST also needs the
  header X-NUauto: 1 (other websites can't send it) and JSON. Secrets (tokens, cookies, the webhook) never reach the page.
- On the homelab (REMOTE) it listens on web_listen_host:8766 only (the Tailscale IP), and the cookie is a paired
  device's (review.py: `nuauto review pair`); the same Host / X-NUauto / JSON / Origin rules. What needs the laptop
  itself (a login window, a terminal window, a file on it: LAPTOP_ONLY) is refused there and shown as "on your laptop".
- Long or browser work runs as the same `nuauto ...` commands, one at a time, in child processes (Task). Their
  questions come over answers.JsonIO and show as dialogs; Stop sends SIGINT to the child (the KeyboardInterrupt
  path Ctrl+C takes). If this process dies, the child's stdin closes and it stops the same way. On the homelab a run you
  watch (Apply, the NUworks side) gets a virtual screen and its browser view (screen.py), like the assistant's.
- Real mode refuses to start inside a Claude Code shell (CLAUDECODE): agents use --demo (fake data) or --view
  (real data, read-only).
"""
import fcntl
import hmac
import html
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

    def __init__(self, kind, label, cmd, browser, background=False, env=None, on_done=None, on_row=None, screen=None):
        self.id = secrets.token_hex(4)
        self.kind, self.label, self.cmd, self.browser, self.background = kind, label, cmd, browser, background
        self.log, self.events, self.question, self.dropped = [], [], None, 0
        self.row = self.screenshot = self.last_shot = self.wait_until = None
        self.done_rows = []
        self.state, self.code, self.stopping = "running", None, False
        self.settling = False  # ended, its on_done (the sheet read again) not finished yet: the page still sees running
        self.started, self.ended = now(), None
        self.on_done, self.on_row = on_done, on_row
        self.screen = screen  # (homelab) the virtual screen its browser opens on (screen.Screen, started): stopped at the end
        env = {**os.environ, **(env or {}), "PYTHONUNBUFFERED": "1",
               **({"DISPLAY": screen.display} if screen else {})}
        try:
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         text=True, bufsize=1, env=env, cwd=config.STATE_DIR, start_new_session=True)
        except OSError:
            if screen:
                screen.stop()
            raise
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
        if self.screen:
            try:
                self.screen.stop()
            except Exception:  # the next run in its slot stops what is left (screen.Screen._clear_stale)
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
                "browser": self.browser, "background": self.background,
                # (homelab) its browser on the virtual screen, to watch and use from your phone: the view's link and the
                # password made for this run (paired devices only, like the assistant's)
                "browser_view": ({"url": self.screen.url, "password": self.screen.password}
                                 if self.screen and self.state == "running" and self.screen.url else None)}


def shot_url(path):
    return f"/api/file?path={path}" if path else None


# ---------------------------------------------------------------- the app (one per process)

class App:
    def __init__(self):
        self.token = secrets.token_urlsafe(24)
        self.open_secret = secrets.token_urlsafe(16)
        self.port = None
        self.remote_host = None    # (homelab) the address it listens on: web_listen_host
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
        self.other_urls = None     # the Other jobs tab's links (not Proposed), as last read: the internship count
        self.other_at = 0          # when they were last asked for
        self.other_cache = None    # (when, rows) of the Other jobs tab, for the internship Review (gui.other_rows)
        self.last_request = now()
        self.stopping = False
        self.window_kind = "none"  # "mac" / "gtk" (NUauto's own window), "app" / "tab" (a browser), "none"

    # ---- tasks

    def start(self, kind, label, cmd, browser=True, background=False, env=None, on_done=None, watch=False):
        """watch: a run you watch (its browser visible): on the homelab it gets a virtual screen and a browser view."""
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
            scr = run_screen(kind) if watch and REMOTE and not config.DEMO else None
            task = Task(kind, label, cmd, browser, background, env, on_done=self._done(on_done),
                        on_row=None if background else self.refresh_soon, screen=scr)
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
        "bitwarden": (["bitwarden"], 900, 4),
        "nuworks": (["nuworks"], 6 * 3600, 8),
        "claude_live": (["claude_live"], 86400, 45),
        "app": (["app_update"], 86400, 30),
    }

    def run_group(self, group):
        if not group_on(group):
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
        if not self.first_round_done or VIEW or REMOTE:  # the homelab has no desktop (Discord tells you there)
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
        groups = [g for g in self.GROUPS if group_on(g)]
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
            if idle and not busy and not REMOTE:  # the homelab's app is always there (nuauto-web)
                self.quit()
            if REMOTE and config.IS_SERVER:
                reload_config()  # settings saved on the laptop (sync) or here: this process reads them again
            time.sleep(2)

    def run_now(self, groups):
        for g in groups:
            if g in self.GROUPS and group_on(g):
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
    from nuauto import assist
    return {"row": r.number, "company": r.company, "title": r.title, "url": r.url, "status": r.status,
            "notes": r.notes, "date": r.date, "on_laptop": assist.LAPTOP_MARK in (r.notes or ""), **extra}


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


def intern_count():
    """Cheap count for the menu: internships in Review (the pool and the ones that could not be read) that are not in
    the Other jobs tab and not rated no. The tab's links are read in the background (at start, then every 10 minutes;
    each internship Review and decision updates them too)."""
    from nuauto import intern
    if not intern.ENABLED:
        return 0
    if now() - APP.other_at > (600 if APP.other_urls is not None else 60):
        APP.other_at = now()
        threading.Thread(target=read_other_quietly, daemon=True).start()
    ratings, seen = jobs.load("ratings.json", {}), APP.other_urls or set()
    return sum(1 for r in intern.load("pool.json", []) + intern.load("unread.json", [])
               if r["url"] not in seen and ratings.get(r["id"], {}).get("label") != 0)


def setup_needed():
    """From the files only (the page asks every few seconds); the Setup screen checks each step for real. Never on the
    homelab's app: setup is the laptop's (the homelab gets its settings from there)."""
    if REMOTE:
        return False
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
    from nuauto import intern
    return {
        "demo": config.DEMO, "view": VIEW, "version": __import__("nuauto").__version__,
        # which machine this page runs on: "laptop" (nuauto gui) or "homelab" (its app, serve_remote), and what only the
        # laptop can do (the homelab's page shows those as "on your laptop")
        "where": "homelab" if REMOTE else "laptop", "laptop_only": LAPTOP_ONLY if REMOTE else {},
        "remote_app": homelab_review(),  # (laptop) the homelab's NUauto page, when it has one
        "internships": {"on": intern.ENABLED, "term": intern.TERM, "review": intern_count()},
        # which assistant sessions run now and what they want (the open screens reload their lists when this changes)
        "assistants": ",".join(f"{s.get('tab')}-{s.get('row')}:{s.get('slot')}:{(s.get('attention') or {}).get('kind', '')}"
                               for s in assistants()["sessions"])
        + "|" + ",".join(f"{s.get('tab')}-{s.get('row')}:{(s.get('attention') or {}).get('kind', '')}"
                         for s in homelab_assistants()),
        # (homelab) the sessions that wait for you, for Today and the menu
        "waiting": waiting_sessions() if REMOTE else [],
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


OTHER_ROWS_FOR = 30  # seconds the internship Review reuses the Other jobs tab's rows (it asks on every search)


def other_rows(fresh=True):
    """The Other jobs tab's rows ([] before the tab exists); remembers its links for the menu's count. fresh=False:
    the rows read in the last OTHER_ROWS_FOR seconds will do (each read costs Google quota: about 60 a minute; every
    change NUauto makes to the tab forgets them)."""
    from nuauto import intern
    if not fresh and APP.other_cache and now() - APP.other_cache[0] < OTHER_ROWS_FOR:
        return APP.other_cache[1]
    try:
        rows = sheet.read_rows(worksheet(other=True))
    except NoOtherTab:
        rows = []
    APP.other_urls = intern.sheet_urls(rows)
    APP.other_cache = (now(), rows)
    return rows


def other_changed():
    """NUauto changed the Other jobs tab: the next internship Review reads it again."""
    APP.other_cache = None


def read_other_quietly():
    """other_rows in the background (for the count): a sheet that can't be read now is tried again later."""
    try:
        other_rows()
    except Exception:
        pass


def review(mode, q="", category="", source="nuworks"):
    """The review queue; q (words or "quoted phrases", all must appear) and category narrow what is shown.
    Decisions work on any job of the whole queue. source "intern": the internships (intern.py), whose Approve adds a
    row to the Other jobs tab."""
    rows = need_rows()
    ratings = jobs.load("ratings.json", {})
    if source == "intern":
        from nuauto import intern
        if not intern.ENABLED:
            raise Refused('Internships are off (add "internships": {} to local/local_config.json).')
        todo, urgent = intern.review_queue(mode, ratings, other_rows(fresh=False), rows)
        details = {r["id"]: intern.details_for(r) for r in todo}
    else:
        details = jobs.load_details()
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
        order = ("the posting could not be read: these come after the scored ones" if r.get("unread")
                 else "your Proposed row in the Other jobs tab (these come next)" if why == "proposed" and source == "intern"
                 else jobs.order_text(r, why))
        out.append({"id": r["id"], "title": r["title"], "company": r["company"], "category": r["category"],
                    "category_label": jobs.category_label(r["category"]),
                    "order": order,
                    "match": r["match"], "closes_text": jobs.closes_text(day),
                    "soon": day is not None and (day - date.today()).days <= jobs.URGENT_DAYS,
                    "rating": (ratings.get(r["id"]) or {}).get("label")})
    return {"jobs": out, "urgent": [r["id"] for r in urgent], "mode": mode, "source": source, "total": len(todo),
            "categories": [{"key": k, "label": jobs.category_label(k), "count": counts[k]}
                           for k in sorted(counts, key=lambda k: (-counts[k], k))]}


def job(job_id):
    if not re.fullmatch(r"[A-Za-z0-9]{1,64}", job_id):
        raise NotFound()
    r = APP.queue.get(job_id)
    if r is not None and r.get("source") == "simplify":  # an internship
        from nuauto import intern
        return intern.job_view(r, intern.details_for(r))
    r = r or next((x for x in jobs.load("pool.json", []) if x["id"] == job_id), None)
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
    if decision == "approve" and r.get("source") == "simplify":  # an internship: the Other jobs tab
        from nuauto import intern
        try:
            out["row"], out["tab"] = intern.approve(r, interactive=False), "other"
        except sheet.NotLoggedIn as e:
            raise Refused(f"{e} Log in to Google first (Settings, or the Google dot at the top).")
        except sheet.SheetError as e:
            raise Refused(str(e))
        APP.other_urls = (APP.other_urls or set()) | {r["url"]}
        other_changed()
    elif decision == "approve":
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
    jobs.set_rating(job_id, {"label": int(decision != "no"), "date": date.today().isoformat()})
    return out


def undo(body):
    job_id, previous = str(body.get("id", "")), body.get("previous")
    if body.get("row") and body.get("tab") == "other":  # an internship's row in the Other jobs tab: back to Proposed
        from nuauto import intern
        # its link from the listings, not the review list: an approved job leaves the list at the next reload
        listing = intern.load("listings.json", {}).get(job_id) if re.fullmatch(r"s[0-9a-z]{1,40}", job_id) else None
        if listing is None:
            raise Refused("That internship is not in your listings any more.")
        try:
            sheet.unapprove(worksheet(other=True), int(body["row"]), listing["url"])
        except (sheet.SheetError, ValueError) as e:
            raise Refused(str(e))
        (APP.other_urls or set()).discard(listing["url"])
        other_changed()
    elif body.get("row"):
        ws = worksheet()
        try:
            sheet.unapprove(ws, int(body["row"]), jobs.job_url(job_id))
        except (sheet.SheetError, ValueError) as e:
            raise Refused(str(e))
        APP.refresh_rows(force=True)
    jobs.set_rating(job_id, {"label": previous["label"], "date": str(previous.get("date", ""))[:10]}
                    if isinstance(previous, dict) and previous.get("label") in (0, 1) else None)
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


# The homelab's update is its own service (its timer runs it too): started there, never a second copy beside it.
HOMELAB_UPDATE = ("systemctl --user start nuauto-daily.service; "
                  'tail -n 8 "$(ls -t logs/daily-*.txt 2>/dev/null | head -1)" 2>/dev/null')


def act(body):
    kind = body.get("kind")
    args = body.get("args") or {}
    if REMOTE and kind in LAPTOP_ONLY:
        raise Refused(LAPTOP_ONLY[kind])
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
        task = APP.start("apply", "Applying", cmd, env=VISIBLE, on_done=lambda t: APP.run_now(["quick"]), watch=True)
    elif kind == "update":
        cmd = (demo_cmd("update") if config.DEMO else ["bash", "-c", HOMELAB_UPDATE] if config.IS_SERVER
               else config.self_cmd("update"))
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
                         config.self_cmd("assist", "nuworks", str(int(row)), "--ui", "json"), env=VISIBLE, watch=True)
    elif kind == "assist":  # args.tab "other": a row of the Other jobs tab
        row = args.get("row")
        if not str(row).isdigit():
            raise Refused("Which row?")
        if REMOTE:  # the homelab: its own tmux session (remote.start), answered on its Assistants screen here
            return remote_assist_start(args)
        tab = "other " if args.get("tab") == "other" else ""
        return open_terminal(f"{shlex.quote(config.self_exe())} assist {tab}{int(row)}",
                             "The assistant runs here. It asks you before anything is submitted.")
    elif kind == "selftest":  # demo mode end to end in its own temp folder: safe while anything else runs
        task = APP.start("selftest", "Self-test", config.self_cmd("selftest"), browser=False)
    elif kind == "fix_permissions":
        health.fix_permissions()
        APP.run_now(["quick"])
        return {}
    elif kind in ("bitwarden_add", "bitwarden_import", "bitwarden_login", "bitwarden_install"):
        # in a terminal window: Bitwarden asks for your master password itself (bw unlock); NUauto never sees it, and
        # the unlocked session stays in that window
        nuauto = shlex.quote(config.self_exe())
        unlock = 'BW_SESSION="$(bw unlock --raw)" && export BW_SESSION && '
        if kind == "bitwarden_install":
            return open_terminal("npm install -g @bitwarden/cli && bw login",
                                 "Installing Bitwarden's command line (npm), then logging in to it.")
        if kind == "bitwarden_login":
            return open_terminal("bw login", "Log in to Bitwarden's command line (your Bitwarden email and master password, "
                                 "typed into Bitwarden itself).")
        if kind == "bitwarden_add":
            return open_terminal(unlock + f"{nuauto} accounts export --bitwarden",
                                 "Unlock Bitwarden here: it asks for your master password itself (NUauto never sees it). "
                                 "Then NUauto adds the logins it made to your vault.")
        texts = [t.strip() for t in args.get("only") or read_local().get("bitwarden_import") or [] if str(t).strip()]
        if not texts or not all(IMPORT_TEXT.match(t) for t in texts):
            raise Refused("Which logins? One or more site texts like myworkday or amazon.jobs.")
        write_local({"bitwarden_import": texts})
        return open_terminal(unlock + "bw sync && " + " && ".join(
            f"bw list items --search {shlex.quote(t)} | {nuauto} accounts import - --only {shlex.quote(t)}" for t in texts),
            "Unlock Bitwarden here: it asks for your master password itself (NUauto never sees it). Then NUauto takes "
            "only your logins whose address has: " + ", ".join(texts))
    elif kind == "review_pair":  # Pair this computer: a one-time link from the homelab, opened here (you press Pair)
        if not homelab_review():
            raise Refused("NUauto on your homelab is off (Settings > NUauto on your homelab).")
        from nuauto import review
        link = review.pair_link_from_homelab("this computer")
        if not link:
            raise Refused("The homelab could not be asked for a pairing link (is it reachable?).")
        return {"url": link}
    elif kind == "pair_device":  # (homelab) a one-time link for another of your devices: you open it there, press Pair
        if not REMOTE:
            raise Refused("Pairing is for NUauto on your homelab (Settings > NUauto on your homelab).")
        from nuauto import review
        name = " ".join(str(args.get("name") or "").split())[:40] or "device"
        return {"link": review.make_pairing(name), "minutes": review.PAIR_TTL // 60}
    elif kind == "unpair":  # (homelab) forget one paired device (its cookie stops working)
        if not REMOTE:
            raise Refused("Pairing is for NUauto on your homelab.")
        from nuauto import review
        which = str(args.get("id") or "")
        if not re.fullmatch(r"[0-9a-f]{12}", which):
            raise Refused("Which device?")
        if which == (getattr(REQUEST, "device", None) or {}).get("id"):
            raise Refused("That is this device: unpair it from another one (or `nuauto review unpair` on the homelab).")
        return {"gone": review.unpair(which), "devices": review.devices()}
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
    return {"agent": agent, "other": other, "site": site, "retry": retry, **assistants(),
            "queue": queue_info() if REMOTE else None}


QUEUE_FOR = 20  # seconds the homelab's queue (two sheet reads) is reused between Company sites loads
_queue = {"at": 0.0, "data": None}


def queue_info(fresh=False):
    """(homelab) The queue's view of each company-site row (review.queue_view): its group, what it waits for, whether
    you can start it now; None if it can't be read now."""
    if not config.REMOTE_ASSIST:
        return None
    if fresh or not _queue["data"] or now() - _queue["at"] > QUEUE_FOR:
        from nuauto import review
        try:
            _queue["data"], _queue["at"] = review.queue_view(), now()
        except Exception as e:  # e.g. the sheet can't be read: the rows still show, without the queue's part
            return {"error": f"The queue can't be read now ({type(e).__name__})."}
    return _queue["data"]


def remote_assist_start(args):
    """(homelab) Start now: one row's assistant session in tmux (review.start_now: a free slot, its job site free, the
    caps, its records). Its screen here is #/assistant/<slot> once it runs."""
    if not config.REMOTE_ASSIST:
        raise Refused("The assistant on the homelab is off (the laptop's Settings > NUauto on your homelab).")
    from nuauto import review
    started, message = review.start_now({"tab": "other" if args.get("tab") == "other" else "jobs",
                                         "row": str(args.get("row"))}, device_name())
    if not started:
        raise Refused(message)
    _queue["at"] = 0  # read again next time: this row is running now
    return {"started": True, "message": message}


def mark(body):
    action, row, url = body.get("action"), body.get("row"), str(body.get("url", ""))
    other = body.get("tab") == "other"  # a row of the Other jobs tab (only "applied" and "drop" there)
    if action not in ("applied", "site", "drop") or not str(row).isdigit() or (other and action == "site"):
        raise Refused("Unknown action.")
    if action == "drop":
        if body.get("why") not in sheet.DROP_REASONS:
            raise Refused("Unknown reason.")
        tab = "other" if other else "jobs"
        if any(s.get("tab") == tab and s.get("row") == int(row) for s in assist_sessions()):
            raise Refused("An assistant is working on this row: end its session first.")
        if REMOTE and config.REMOTE_ASSIST:  # the laptop's sessions hold claims here
            from nuauto import remote
            if any(c.get("tab") == tab and c.get("row") == int(row) for c in remote.live_claims()):
                raise Refused("The laptop's assistant is working on this row: end its session first.")
    _queue["at"] = 0
    ws = worksheet(other)
    try:
        if action == "site":
            sheet.mark_site_done(ws, int(row), url)
        elif action == "drop":
            sheet.drop(ws, int(row), url, body["why"])
        else:
            sheet.mark_applied_by_hand(ws, int(row), url, how="by hand (marked in NUauto)")
    except sheet.SheetError as e:
        raise Refused(str(e))
    if other:
        other_changed()
    else:
        APP.refresh_rows(force=True)
    return {}


def assist_sessions():
    """Every assistant session running now: this computer's and (remote_assist) the homelab's."""
    from nuauto import assist
    return assist.sessions() + list(homelab_assistants())


# ---------------------------------------------------------------- other jobs (the sheet's Other jobs tab)

def assistants():
    """The assistant sessions running now (assist.sessions: slot, row, tab, company, host, since) and how many may;
    homelab: the homelab's own sessions (remote_assist), so a row it works on offers no Start here."""
    from nuauto import assist
    return {"sessions": assist.sessions(), "slots": assist.slots(), "homelab": homelab_assistants(),
            "homelab_review": homelab_review()}


def homelab_review():
    """The homelab assistant's review page (review.py), for the Homelab assistants card; None when there is none to
    show (no remote_assist, view or demo mode)."""
    if VIEW or config.DEMO or not config.REMOTE_ASSIST or config.IS_SERVER:
        return None
    from nuauto import review
    url = review.page_url()
    return url.rstrip("/") if url.startswith("http") else None


HOMELAB_EVERY = 60  # seconds between reads of the homelab's sessions (ssh, in the background)
_homelab = {"at": 0.0, "sessions": [], "busy": False}


def homelab_assistants():
    """The homelab's assistant sessions (remote.homelab_sessions over ssh), read at most every HOMELAB_EVERY seconds in
    the background (the last answer meanwhile). [] without remote_assist, and in view or demo mode (no ssh there)."""
    if VIEW or config.DEMO or not config.REMOTE_ASSIST or config.IS_SERVER:
        return []
    if time.time() - _homelab["at"] > HOMELAB_EVERY and not _homelab["busy"]:
        _homelab["busy"] = True

        def read():
            try:
                from nuauto import remote
                _homelab["sessions"] = remote.homelab_sessions()
            finally:
                _homelab["at"], _homelab["busy"] = time.time(), False
        threading.Thread(target=read, daemon=True).start()
    return _homelab["sessions"]


def intern_facts(urls):
    """For the Other jobs card: the match %, score and pay (from intern.py's data) of the internships among these links."""
    from nuauto import insights
    from nuauto import intern
    if not intern.ENABLED or not urls:
        return {}
    listings, scores = intern.load("listings.json", {}), intern.load("scores.json", {})
    pool = {r["id"]: r for r in intern.load("pool.json", [])}
    out = {}
    for i, x in listings.items():
        s, p = scores.get(i), pool.get(i)
        if s is None or x["url"] not in urls:
            continue
        pay = (intern.load(f"details/{i}.json", None) or {}).get("pay") or None
        hour = insights.hourly(pay)
        out[x["url"]] = {"match": s["match"], "score": p["rank"] if p else None, "score_text": jobs.rank_text(p) if p else None,
                         "pay": pay, "pay_hour": hour[1] if hour else None, "posted_text": intern.posted_text(x)}
    return out


def other_list():
    """The Other jobs tab, read fresh: ready (Approved: for the assistant), applied (newest first), the rest; the
    assistant sessions running now. exists is False until the first job is added (that makes the tab)."""
    from nuauto import assist
    from nuauto import intern
    try:
        rows = sheet.read_rows(worksheet(other=True))
    except NoOtherTab:
        return {"exists": False, "tab": sheet.OTHER_TAB, "ready": [], "applied": [], "rest": [], **assistants()}
    facts = intern_facts({intern.clean_url(r.url) for r in rows if r.status == "Approved"})
    ready, applied, rest = [], [], []
    for r in rows:
        if r.status == "Approved":
            url, why = assist.other_target(r)
            ready.append(row_view(r, target=url, host=urlparse(url).hostname if url else None, why=why,
                                  **facts.get(intern.clean_url(r.url), {})))
        elif r.status == "Applied":
            applied.append(row_view(r))
        else:
            rest.append(row_view(r))
    applied.sort(key=lambda x: x["date"], reverse=True)
    return {"exists": True, "tab": sheet.OTHER_TAB, "ready": ready, "applied": applied, "rest": rest, **assistants()}


def other_add(body):
    """Add a job that is not on NUworks: the Other jobs tab, Approved (made if missing)."""
    from nuauto import assist
    fields = [str(body.get(k) or "") for k in ("url", "company", "title")]
    try:
        out = {"row": assist.add_other(*fields, interactive=False)}
        other_changed()
        return out
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
    other_changed()
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
    script = (f"export PATH={shlex.quote(config.tools_path())}; cd {shlex.quote(config.STATE_DIR)}; "
              f"echo {shlex.quote(intro)}; echo; {command}; "
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
    # the assistants' answer-bank commands hold this lock while they save (assist.bank): the version check and the
    # save are one step, so an answer an assistant saves meanwhile is never overwritten
    os.makedirs(config.LOCAL_DIR, mode=0o700, exist_ok=True)
    with open(os.path.join(config.LOCAL_DIR, "answers.lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if body.get("version") != answers_get(bank)["version"]:
            raise Refused("The answer bank changed since you opened it (a run saved an answer). Reload, then edit again.")
        # what you deleted here is deleted on the homelab's copy too (sync.share never deletes by itself)
        answers.forget(bank_path(bank), {answers.norm(e["question"]) for e in answers.load(bank_path(bank))}
                       - {e["question"] for e in clean})
        answers.save(clean, bank_path(bank))
    return answers_get(bank)


def logs():
    out = []
    if os.path.isdir(config.LOGS_DIR):
        for name in sorted(os.listdir(config.LOGS_DIR), reverse=True)[:60]:
            path = os.path.join(config.LOGS_DIR, name)
            if os.path.isdir(path):
                files = sorted(os.listdir(path))
                shots = os.path.join(path, "browser")  # the assistant's browser tool keeps its screenshots there
                files += sorted(f"browser/{f}" for f in (os.listdir(shots) if os.path.isdir(shots) else [])
                                if f.endswith(".png"))
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
                   "server_dir", "web_base_url", "web_listen_host", "tos_ack", "accounts_email", "assist_effort",
                   "transcript_path"]
EFFORTS = ("low", "medium", "high")  # the assistant's effort choices on the Settings screen (assist.effort)


def logins_summary():
    """The sites the assistant made a login on: how many, how many not exported to a password manager yet. Never a
    password, never the file's content."""
    from nuauto import accounts
    try:
        with open(accounts.PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    return {"sites": len(data), "new": sum(1 for a in data.values() if not a.get("exported"))}


def logins_export(body):
    """Bitwarden's import file of the logins not exported yet (every: all of them), in ~/Downloads, mode 600."""
    from nuauto import accounts
    demo_path = os.path.join(config.STATE_DIR, f"nuauto-logins-{date.today():%Y-%m-%d}.json")  # demo: never ~/Downloads
    path, n = accounts.export(demo_path if config.DEMO else None, every=bool(body.get("every")))
    if not n:
        raise Refused("No new logins to export.")
    return {"path": path, "count": n, "logins": logins_summary()}


def settings_get():
    from nuauto import onboard
    cfg = read_local()
    return {"settings": {k: cfg.get(k, "" if k != "assist_read_paths" else []) for k in PUBLIC_SETTINGS},
            "max_per_week": sheet.max_per_week(), "max_per_week_ceiling": sheet.MAX_PER_WEEK_CEILING,
            "resume_label": health.resume_label(), "discord": os.path.exists(config.DISCORD_WEBHOOK_PATH),
            "client_json": health.client_json_found(), "google_login": os.path.exists(config.TOKEN_PATH),
            "tools": {name: config.tool_path(name) for name in ("claude", "npx")},
            "paths": {"state": config.STATE_DIR, "logs": config.LOGS_DIR}, "demo": config.DEMO,
            "efforts": list(EFFORTS), "logins": logins_summary(),
            "password_manager": onboard.password_manager({c.id: c for c in APP.checks()}),
            "homelab_assistant": onboard.homelab_assistant(here=REMOTE),
            "where": "homelab" if REMOTE else "laptop", "homelab": onboard.homelab_link() if not REMOTE else None,
            **(devices_get() if REMOTE else {})}


def write_local(updates):
    os.makedirs(config.LOCAL_DIR, mode=0o700, exist_ok=True)
    with config.settings_lock():  # (sync merges the other machine's copy into it)
        cfg = read_local()
        cfg.update(updates)
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
    if not (REMOTE and config.DEMO):  # the homelab demo has its made-up assistant on, whatever the file says
        config.REMOTE_ASSIST = bool(cfg.get("remote_assist")) and config.HAS_SERVER
        config.REMOTE_APP = config.HAS_SERVER and (config.REMOTE_ASSIST or bool(cfg.get("remote_app")))


IMPORT_TEXT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,59}$")  # Bitwarden's search and --only from Settings: one word,
# never starting with "-" (it would read as an option)


def app_on(cfg):
    """Whether these settings give the homelab its NUauto page (config.REMOTE_APP's rule)."""
    return bool(cfg.get("remote_assist") or cfg.get("remote_app"))


def remote_changed(old):
    """After the homelab's NUauto settings changed: the homelab gets them (sync.push), and its NUauto page starts or
    stops with them (nuauto-web reads them at its start). In the background: Settings answers at once. old: app_on before."""
    if not config.HAS_SERVER or config.IS_SERVER or config.DEMO:
        return
    from nuauto import sync

    def push():
        sync.push()
        if app_on(read_local()) != bool(old):
            sync.remote("systemctl --user restart nuauto-web", "restart the homelab's web page")
        APP.run_now(["homelab"])
    threading.Thread(target=push, daemon=True).start()


def settings_put(body):
    from nuauto import onboard
    updates = {}
    laptop_keys = REMOTE_SETTINGS_OFF & set(body) if REMOTE else set()
    if laptop_keys:
        raise Refused("Set this on your laptop: it names files or folders there, its password manager, or switches this "
                      f"page off ({', '.join(sorted(laptop_keys))}).")
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
        set_resume_label(label)
    if "assist_read_paths" in body:
        paths = body["assist_read_paths"]
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            raise Refused("Expected a list of folders.")
        updates["assist_read_paths"] = [p.strip() for p in paths if p.strip()]
    if "transcript_path" in body:  # the assistant uploads it where an application asks for a transcript
        path = str(body["transcript_path"] or "").strip()
        full = os.path.expanduser(path)
        if path and not (path.lower().endswith(".pdf") and os.path.isfile(full)):
            raise Refused(f"No PDF at {path}.")
        updates["transcript_path"] = path
    if "accounts_email" in body:
        email = str(body["accounts_email"] or "").strip()
        if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise Refused("The account email doesn't look like an email address.")
        updates["accounts_email"] = email
    if "assist_effort" in body:
        if body["assist_effort"] not in EFFORTS:
            raise Refused(f"The assistant's effort is one of {', '.join(EFFORTS)}.")
        updates["assist_effort"] = body["assist_effort"]
    if "password_manager" in body:
        if body["password_manager"] not in ("", "bitwarden"):
            raise Refused("The password manager is Bitwarden, or none.")
        updates["password_manager"] = body["password_manager"]
    if "bitwarden_import" in body:
        texts = body["bitwarden_import"]
        if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts) or \
                not all(IMPORT_TEXT.match(t.strip()) for t in texts if t.strip()):
            raise Refused("Each site text is one word like myworkday or amazon.jobs (letters, digits, dots, dashes).")
        updates["bitwarden_import"] = [t.strip() for t in texts if t.strip()]
    remote_keys = {"remote_assist", "remote_app", "remote_auto", "remote_hours", "assist_slots"} & set(body)
    if remote_keys and REMOTE and not config.REMOTE_ASSIST:
        raise Refused("The assistant on this homelab is off (the laptop's Settings > NUauto on your homelab).")
    if remote_keys and not REMOTE and not onboard.homelab_assistant()["available"]:
        raise Refused("NUauto on a homelab needs a homelab (Settings > Homelab, or server_hostname in local_config.json; "
                      "docs/DEPLOY.md).")
    if "remote_app" in body:
        if not isinstance(body["remote_app"], bool):
            raise Refused("remote_app is on or off.")
        updates["remote_app"] = body["remote_app"]
    if "remote_assist" in body:
        if not isinstance(body["remote_assist"], bool):
            raise Refused("remote_assist is on or off.")
        updates["remote_assist"] = body["remote_assist"]
    if "remote_auto" in body:  # true / false = both tabs; {"nuworks": bool, "other": bool} = each (a key left out: as it was)
        v = body["remote_auto"]
        if isinstance(v, dict) and v and set(v) <= {"nuworks", "other"} and all(isinstance(x, bool) for x in v.values()):
            from nuauto import remote
            was = remote.auto_tabs(read_local())
            updates["remote_auto"] = {k: v.get(k, remote.TABS[k] in was) for k in ("nuworks", "other")}
        elif isinstance(v, bool):
            updates["remote_auto"] = v
        else:
            raise Refused("remote_auto is on or off (or {\"nuworks\": on/off, \"other\": on/off}).")
    if "remote_hours" in body:
        h = body["remote_hours"]
        if not (isinstance(h, list) and len(h) == 2 and all(isinstance(x, int) and not isinstance(x, bool) for x in h)
                and 0 <= h[0] < h[1] <= 24):
            raise Refused("Hours are from and to, 0-24, from before to.")
        updates["remote_hours"] = h
    if "assist_slots" in body:
        n = body["assist_slots"]
        if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 3:
            raise Refused("Assistants at once: 1, 2 or 3.")
        updates["assist_slots"] = n
    old_remote = app_on(read_local())
    if updates:
        write_local(updates)
        apply_config(read_local())
    if remote_keys:
        remote_changed(old_remote)
    APP.run_now(["quick", "bitwarden"] if {"password_manager"} & set(updates) else ["quick"])
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


def set_resume_label(label):
    with config.settings_lock():  # (sync merges the other machine's profile.json into it)
        write_json_secret(config.PROFILE_PATH, {**read_json(config.PROFILE_PATH), "resume_label": label})


# ---------------------------------------------------------------- setup wizard (onboard.py does the work)

def setup_get():
    from nuauto import onboard
    found = onboard.steps({c.id: c for c in APP.checks()})
    if REMOTE:  # which homelab to use is the laptop's choice
        found = [s for s in found if s["id"] != "homelab"]
    return {"steps": found, "complete": onboard.complete(found), "demo": config.DEMO,
            "where": "homelab" if REMOTE else "laptop", "here": sorted(REMOTE_SETUP) if REMOTE else None}


def setup_post(body):
    from nuauto import onboard
    from nuauto import sheet as sheet_
    a = body.get("action")
    if REMOTE and a not in REMOTE_SETUP:
        raise Refused("This setup step is done on your laptop (it picks a file, logs in or makes something there). "
                      "Open NUauto on your laptop: Settings > Open setup.")
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
            set_resume_label(label)
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

# The homelab's app (serve_remote, inside nuauto-web; config.REMOTE_APP): the same screens, for your phone. Paired devices
# only (review.py's pairing), on web_listen_host:8766 only. What needs the laptop itself is refused here (LAPTOP_ONLY).
REMOTE = False
REMOTE_GROUPS_OFF = ("homelab", "bitwarden", "app")  # the laptop's checks: ssh to the homelab, Bitwarden, the app's update
REMOTE_POSTS_OFF = {"/api/quit": "NUauto on your homelab stays on (it is part of nuauto-web).",
                    "/api/logins/export": "The logins file for Bitwarden is written on your laptop (Settings there)."}
REQUEST = threading.local()  # the paired device a request comes from (its name goes in the run logs)
APP_SCREEN_SLOT = 0          # the virtual screen of a run you watch here (Apply, the NUworks side); 1-3: the assistant's
_TERMINAL = "It runs in a terminal window on your laptop (you type there, never into this page)."
LAPTOP_ONLY = {  # action -> why it is the laptop's (the homelab's page shows this instead of doing it)
    "login_google": "Google's login opens a browser on the computer that asks for it: log in from NUauto on your laptop. "
                    "It copies the login here.",
    "login_nuworks": "The NUworks login opens Firefox on the computer that asks for it: log in from NUauto on your laptop. "
                     "It copies the session here.",
    "login_claude": "Claude Code logs in in a terminal. For the homelab's: in NUauto on your laptop, Settings > Homelab > "
                    "Log in to Claude there.",
    "review_pair": "Pair this computer is the laptop's button. Here: Settings > Paired devices > Pair another device.",
    **{k: "Bitwarden asks for your master password in a terminal window on your laptop: do this from NUauto there."
       for k in ("bitwarden_add", "bitwarden_import", "bitwarden_login", "bitwarden_install")},
}
# setup steps the homelab's page can do itself (the rest pick files, log in or make things on the laptop)
REMOTE_SETUP = {"ack", "labels_read", "label_set", "terms_read", "prefs_save", "discord_save", "discord_test",
                "discord_remove"}
# settings that name things on the laptop (its files and folders, its password manager) or switch this page itself off
REMOTE_SETTINGS_OFF = {"resume_path", "transcript_path", "assist_read_paths", "password_manager", "bitwarden_import",
                       "remote_assist", "remote_app"}


def group_on(group):
    """Whether this server runs that group of health checks (view mode: the read-only ones; the homelab: not the laptop's)."""
    return (not VIEW or group in VIEW_GROUPS) and not (REMOTE and group in REMOTE_GROUPS_OFF)


def review_cookie():
    from nuauto import review
    return review.COOKIE


def device_name():
    """The paired device this request comes from (homelab), for the logs; "this computer" on the laptop."""
    return (getattr(REQUEST, "device", None) or {}).get("name") or ("a paired device" if REMOTE else "this computer")


_config_seen = {"mtime": None}


def reload_config():
    """(homelab) local_config.json changed (the laptop's sync, or Settings here): this process takes it again."""
    try:
        m = os.stat(config.LOCAL_CONFIG_PATH).st_mtime_ns
    except OSError:
        return
    if _config_seen["mtime"] is None:
        _config_seen["mtime"] = m
    elif m != _config_seen["mtime"]:
        _config_seen["mtime"] = m
        cfg = read_local()
        if cfg:
            apply_config(cfg)


def run_screen(kind):
    """(homelab) A virtual screen for a run you watch, and its browser view (screen.py, slot APP_SCREEN_SLOT): you watch
    and use its browser from your phone, as with the assistant's. Refused with what to install if it can't start."""
    from nuauto import screen
    run_dir = os.path.join(config.LOGS_DIR, f"{time.strftime('%Y%m%d-%H%M%S')}_{kind}_screen")
    os.makedirs(run_dir, mode=0o700, exist_ok=True)
    try:
        return screen.Screen(APP_SCREEN_SLOT, run_dir).start()
    except screen.ScreenError as e:
        raise Refused(f"{e} A run here shows its browser on a screen you watch from your phone.")


def waiting_sessions():
    """(homelab) The assistant sessions that wait for you: a Submit to approve, a question, the end question."""
    from nuauto import review
    return [x for x in (review.summary(s) for s in review.sessions())
            if x["status"] in ("approve", "waiting", "finish", "prompt")]


def assistant_get(slot):
    """(homelab) One assistant session's screen: review.view."""
    from nuauto import review
    s = review.session(int(slot)) if REMOTE and str(slot).isdigit() else None
    if not s:
        raise NotFound()
    return review.view(s)


def assistant_post(body):
    """(homelab) A tap on an assistant session's screen: Approve / Decline, Reply, Interrupt, End, the end question."""
    from nuauto import review
    if not REMOTE:
        raise Refused("An assistant on this computer runs in its own terminal window: answer it there.")
    slot = body.get("slot")
    s = review.session(int(slot)) if str(slot).isdigit() else None
    if not s:
        raise Refused("That session has ended.")
    return review.act(s, body, device_name())


DEMO_PROBE = {"ok": True, "hostname": "demo-homelab", "tailscale": "100.64.0.42", "dir": True, "venv": True, "uv": True,
              "claude": True, "linger": True, "units": {"nuauto-daily.timer": "enabled", "nuauto-web.service": "enabled"},
              "assist_tools_missing": ["Xvfb", "x11vnc", "websockify"], "folder": "projects/auto"}


def homelab_post(body):
    """Settings > Homelab (the laptop, a source install): check a homelab over ssh (read-only), use it, stop using it,
    set it up (a terminal window: docs/DEPLOY.md's steps, each asked first), log in to Claude Code there."""
    from nuauto import onboard
    if REMOTE or config.FROZEN:
        raise Refused("The homelab is chosen and set up in NUauto on your laptop (a source install: the packaged app "
                      "always runs on its own).")
    a = body.get("action")
    ssh, folder = str(body.get("ssh") or "").strip(), str(body.get("folder") or onboard.HOMELAB_DIR).strip().strip("/")
    if a == "check":
        return DEMO_PROBE if config.DEMO else onboard.homelab_probe(ssh, folder)
    if a in ("save", "off"):
        try:
            link = onboard.homelab_save(ssh, "" if a == "off" else str(body.get("hostname") or "").strip(), folder,
                                        str(body.get("listen") or "").strip())
        except onboard.Refused as e:
            raise Refused(str(e))
        return {"homelab": link, "restart": link["saved"] != config.HAS_SERVER or a == "save"}
    if not config.HAS_SERVER:
        raise Refused("Use a homelab first (Check, then Use this homelab), and open NUauto again.")
    if a == "setup":
        return open_terminal(f"{shlex.quote(config.self_exe())} onboard homelab-setup",
                             "NUauto sets up your homelab here, one step at a time (each asked first). sudo asks for "
                             "the homelab's password in this window; NUauto never sees it.")
    if a == "claude":
        there = f"cd {shlex.quote(config.SERVER_DIR)} && export PATH=$HOME/.local/bin:$PATH && claude"
        return open_terminal(f"ssh -t {shlex.quote(config.SERVER)} {shlex.quote(there)}",
                             "Claude Code on the homelab: log in if it asks, answer Yes to trust the folder, then type /exit.")
    raise Refused("Unknown homelab action.")


def devices_get():
    """(homelab) The paired devices (never their keys), and which one asks."""
    from nuauto import review
    if not REMOTE:
        raise Refused("Paired devices are NUauto on your homelab's (its Settings).")
    return {"devices": review.devices(), "this": (getattr(REQUEST, "device", None) or {}).get("id")}


def page(title, body):
    """A small page of its own (pairing): the app's stylesheet, no script."""
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width, "
            f"initial-scale=1'><meta name=color-scheme content='light dark'><meta name=robots content=noindex>"
            f"<title>{html.escape(title)} · NUauto</title><link rel=icon href=/static/icon.svg type=image/svg+xml>"
            f"<link rel=stylesheet href=/static/app.css></head><body><main class=narrow><div class='card stack'>"
            f"<div class=brand><img src=/static/icon.svg alt='' width=28 height=28><span>NUauto</span></div>{body}"
            "</div></main></body></html>")


NOT_PAIRED = ("This browser is not paired with NUauto on your homelab. Make a pairing link (on your laptop: Settings > "
              "NUauto on your homelab > Pair this computer; for a phone, `nuauto review pair --discord`) and open it in "
              "this browser. Paired a moment ago? A link opened from Discord may have opened in Discord's own browser, "
              "which keeps its own cookies: copy the link into your phone's browser (Safari, Chrome) and pair there.")


def pair_page(code):
    return page("Pair this device", "<h1>Pair this device with NUauto?</h1><p class=muted>It can then use NUauto on your "
                "homelab: review jobs, apply, answer the assistant and approve its Submit. Only pair your own phone or "
                "computer.</p><form method=post action=/pair class=stack>"
                f"<input type=hidden name=c value='{html.escape(code, quote=True)}'><label class=field>Name"
                "<input type=text name=name placeholder='e.g. phone' maxlength=40></label>"
                "<div><button class='btn primary'>Pair</button></div></form>")


def paired_page(ok):
    if ok:
        return page("Paired", "<h1>Paired</h1><p>This device can use NUauto on your homelab now. The pairing link was "
                    "for once: to come back, open this address again (bookmark it, or Add to Home Screen). Discord's "
                    "messages link here too.</p><p><a class='btn primary' href='/'>Open NUauto</a></p>")
    return page("Not paired", "<h1>Not paired</h1><p>That link is used up or expired (they work once, for "
                "10 minutes). Make a new one: on your laptop, Settings > NUauto on your homelab > Pair this computer; "
                "or <code>nuauto review pair</code>.</p>")


def serve_remote():
    """`nuauto web` on the homelab (config.REMOTE_APP): the NUauto app for your phone, on web_listen_host:8766 (the
    Tailscale IP) only, paired devices only. Runs in a thread of nuauto-web; its runs are that process's children."""
    global REMOTE, SERVER
    from nuauto import review
    REMOTE = True
    host = review.listen_host()  # never every address: refused when web_listen_host is not set
    SERVER = ThreadingHTTPServer((host, review.PORT), Handler)
    SERVER.daemon_threads = True
    APP.port, APP.remote_host = SERVER.server_address[1], host
    write_lock()  # `nuauto apply --ui json` checks that this process started it (answers.json_ui_allowed)
    threading.Thread(target=APP.monitor, daemon=True).start()
    SERVER.serve_forever(poll_interval=0.5)


GET_ROUTES = {
    "/api/state": lambda q: (APP.refresh_soon(30), state())[1],
    "/api/health": lambda q: {"checks": [c.to_dict() for c in APP.checks()], "running": sorted(APP.running_groups)},
    "/api/review": lambda q: review(q.get("mode", "approve") if q.get("mode") in ("approve", "rate") else "approve",
                                    q.get("q", "")[:200], q.get("category", "")[:40],
                                    "intern" if q.get("source") == "intern" else "nuworks"),
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
    "/api/devices": lambda q: devices_get(),
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
    "/api/logins/export": logins_export,
    "/api/setup": setup_post,
    "/api/assistant": assistant_post,
    "/api/homelab": homelab_post,
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
        if REMOTE:  # the homelab: its own address only (a rebinding DNS name never matches)
            return self.headers.get("Host", "") == f"{APP.remote_host}:{APP.port}"
        return self.headers.get("Host", "") in (f"127.0.0.1:{APP.port}", f"localhost:{APP.port}")

    def _origin_ok(self, origin):
        if REMOTE:
            return origin == f"http://{APP.remote_host}:{APP.port}"
        return origin in (f"http://127.0.0.1:{APP.port}", f"http://localhost:{APP.port}")

    def _cookie(self):
        want = review_cookie() if REMOTE else f"nuauto_{APP.port}"
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == want:
                return value
        return ""

    def _authed(self):
        if REMOTE:  # a paired device's key (review.py keeps only its hash)
            from nuauto import review
            REQUEST.device = review.device(self._cookie())
            return REQUEST.device is not None
        return hmac.compare_digest(self._cookie().encode(), APP.token.encode())

    def _not_paired(self):
        return self._send(403, {"error": NOT_PAIRED, "pair": True})

    def _send(self, code, body, ctype="application/json", headers=None):
        data = body if isinstance(body, bytes) else (json.dumps(body) if ctype == "application/json" else body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith(("text/", "application/j")) else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        # the homelab: same-origin (with no-referrer a phone's browser sends "Origin: null" on the pairing form's POST)
        self.send_header("Referrer-Policy", "same-origin" if REMOTE else "no-referrer")
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
        if REMOTE:
            # The page and its own files are public code: served before the cookie is checked, because a link tapped in
            # another app (Discord) arrives without the SameSite=Strict cookie; the page's own requests then carry it.
            # Everything else needs a paired device.
            if u.path == "/":
                return self._static("index.html")
            if u.path.startswith("/static/"):
                return self._static(u.path[len("/static/"):])
            old = re.fullmatch(r"/s/([1-9])(/.*)?", u.path)  # the old review page's links (in Discord messages)
            if old:
                return self._send(303, b"", "text/plain", {"Location": f"/#/assistant/{old.group(1)}"})
            if u.path == "/pair":
                if self._authed():
                    return self._send(303, b"", "text/plain", {"Location": "/"})
                return self._send(200, pair_page(q.get("c", "")), "text/html")
            if not self._authed():
                return self._not_paired()
        elif u.path == "/" and "t" in q:  # the start URL: swap the secret for a cookie, then drop it from the address
            if not hmac.compare_digest(q["t"].encode(), APP.token.encode()):
                return self._send(403, PAGE_GONE, "text/html")
            return self._send(303, b"", "text/plain", {
                "Location": "/", "Set-Cookie": f"nuauto_{APP.port}={APP.token}; HttpOnly; SameSite=Strict; Path=/"})
        if not REMOTE and not self._authed():
            return self._send(403, PAGE_GONE, "text/html")
        APP.last_request = now()
        if u.path == "/":
            return self._static("index.html")
        if u.path.startswith("/static/"):
            return self._static(u.path[len("/static/"):])
        found = re.fullmatch(r"/api/assistant/([1-9])(/shot\.png)?", u.path)
        if found:  # (homelab) one assistant session's screen, and its picture
            if not found.group(2):
                return self._call(lambda: assistant_get(found.group(1)))
            from nuauto import review
            s = review.session(int(found.group(1))) if REMOTE else None
            png = review.shot(s, live=bool(q.get("live"))) if s else None
            return self._send(200, png, "image/png") if png else self._error(404, "no screen")
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
        if REMOTE and urlparse(self.path).path != "/pair" and not (self._host_ok() and self._authed()):
            # on the homelab a body is read only for a paired device: nobody else can hold a request open by promising
            # one (closed after the answer, so an unread body is never read as the next request)
            self.close_connection = True
            return self._not_paired() if self._host_ok() else self._error(403, "wrong host")
        raw = self._read_body()
        if not self._host_ok():
            return self._error(403, "wrong host")
        u = urlparse(self.path)
        if VIEW and u.path not in VIEW_POSTS:
            return self._error(403, VIEW_MESSAGE)
        origin = self.headers.get("Origin")
        if REMOTE and u.path == "/pair":  # the pairing page's form (no script on it): a one-time code -> this device's key
            return self._pair(raw, origin)
        if u.path == "/api/window" and not REMOTE:  # a second `nuauto gui` asks this one to open a window
            if hmac.compare_digest(self.headers.get("X-NUauto-Open", "").encode(), APP.open_secret.encode()):
                window.another(start_url(), APP.window_kind, APP.quit)
                return self._send(200, {})
            return self._error(403, "forbidden")
        if REMOTE and not self._authed():
            return self._not_paired()
        if (not self._authed() or self.headers.get("X-NUauto") != "1"
                or not self.headers.get("Content-Type", "").startswith("application/json")
                or (origin and not self._origin_ok(origin))):
            return self._error(403, "forbidden")
        if REMOTE and u.path in REMOTE_POSTS_OFF:
            return self._error(409, REMOTE_POSTS_OFF[u.path])
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

    def _pair(self, raw, origin):
        from nuauto import review
        if origin and not self._origin_ok(origin):
            return self._error(403, "forbidden")
        if self._authed():
            return self._send(303, b"", "text/plain", {"Location": "/"})
        form = {k: v[0] for k, v in parse_qs((raw or b"").decode("utf-8", "replace")).items()}
        key = review.pair(form.get("c", ""), form.get("name", ""))
        if not key:
            return self._send(403, paired_page(False), "text/html")
        return self._send(200, paired_page(True), "text/html", {
            "Set-Cookie": f"{review.COOKIE}={key}; HttpOnly; SameSite=Strict; Path=/; Max-Age=31536000"})

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

# Review's internship list (when internships are on), as extra pictures: the first card, the one whose posting could not
# be read (a kind-of-work choice that holds only it), and a search (every match as its own card). Each is made by
# setting what the screen remembers (sessionStorage), then opening it again.
REVIEW_INTERN_SHOTS = [("review-intern", {}), ("review-intern-unread", {"reviewCat": "uncategorized"}),
                       ("review-intern-list", {"reviewQ": "intern"})]


def review_intern_shots(page, base, folder, scheme, label):
    """The extra Review pictures of one browser; returns how many it made (0: internships are off)."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    if not page.evaluate("Boolean(window.NUauto.S.state && window.NUauto.S.state.internships "
                         "&& window.NUauto.S.state.internships.on)"):
        return 0
    made = 0
    for name, store in REVIEW_INTERN_SHOTS:
        page.evaluate("([source, store]) => { sessionStorage.clear(); sessionStorage.setItem('reviewSource', source);"
                      " for (const [k, v] of Object.entries(store)) sessionStorage.setItem(k, v); }", ["intern", store])
        page.goto(f"{base}/#/home")
        page.goto(f"{base}/#/review")
        try:
            page.wait_for_selector("[data-testid=job-card]", timeout=20000)
        except PlaywrightTimeout:  # e.g. the demo has no such job any more: no picture, the rest still come
            continue
        page.wait_for_timeout(800)
        page.screenshot(path=os.path.join(folder, f"{name}-{scheme}-{label}.png"), full_page=True)
        made += 1
    page.evaluate("sessionStorage.clear()")
    return made


REMOTE_SCREENS = ["home", "apply", "company", "assistant/1", "assistant/2", "assistant/3", "settings", "setup"]


def screenshots(folder, url, remote=False):
    """Every screen in headless Firefox, light and dark, desktop and phone width: <screen>-<scheme>-<width>.png,
    plus Review's internship list (review-intern*). For reviewing the design (agents too). Demo mode only, so no real
    data ends up in a picture. remote: the homelab's app (`--demo --remote`): homelab-<screen>-...png, each browser
    paired first (a pairing link works once)."""
    from playwright.sync_api import sync_playwright
    os.makedirs(folder, exist_ok=True)
    base = url.split("/?")[0].split("/pair")[0]
    count = 0
    with sync_playwright() as p:
        b = p.firefox.launch(headless=True)
        for scheme in ("light", "dark"):
            for width, label in ((1280, "wide"), (420, "narrow")):
                ctx = b.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
                page = ctx.new_page()
                if remote:
                    from nuauto import review
                    page.goto(review.make_pairing("screenshots"))
                    page.click("button")
                    page.wait_for_selector("text=Paired")
                else:
                    page.goto(url)  # the start URL: sets the cookie
                for name in REMOTE_SCREENS if remote else SCREENS:
                    page.goto(f"{base}/#/{name}")
                    page.wait_for_timeout(1500)
                    shot = ("homelab-" if remote else "") + name.replace("/", "-")
                    page.screenshot(path=os.path.join(folder, f"{shot}-{scheme}-{label}.png"), full_page=True)
                    count += 1
                if not remote:
                    count += review_intern_shots(page, base, folder, scheme, label)
                ctx.close()
        b.close()
    print(json.dumps({"screenshots": folder, "count": count}), flush=True)


def pull_loop():
    """Homelab mode: the job data comes from the homelab (like the pull before `nuauto approve`), now and every
    30 minutes. Nothing is pushed on a timer: pushes happen after Review and the Google login, as in the CLI."""
    from nuauto import sync
    while not APP.stopping:
        if sync.pull():
            if config.REMOTE_APP:  # settings changed on the homelab's page come back with the pull (sync.share)
                apply_config(read_local())
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
    if "--remote" in argv:  # the homelab's app: its company-site rows for the made-up sessions
        states = ",".join(filter(None, [states, "homelab"]))
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
    if "--remote" in argv:
        if not config.DEMO:
            sys.exit("--remote is for the demo (`nuauto gui --demo --remote`). On the homelab, NUauto's page runs inside "
                     "nuauto web (docs/REMOTE.md).")
        return main_remote_demo(argv)
    if config.IS_SERVER:
        sys.exit("nuauto gui runs on your laptop. On the homelab, NUauto's page for your phone runs inside nuauto web "
                 "(Settings > NUauto on your homelab, on the laptop).")
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


def main_remote_demo(argv):
    """`nuauto gui --demo --remote` (also `nuauto review demo`): the homelab's app on 127.0.0.1, on demo data with made-up
    assistant sessions (review.demo_setup). Prints {"url": a pairing link, ...}: open it, press Pair. Nothing is real."""
    global REMOTE, SERVER
    from nuauto import review
    REMOTE = True
    port = 0
    if "--port" in argv:
        i = argv.index("--port")
        if i + 1 >= len(argv) or not argv[i + 1].isdigit():
            sys.exit(__doc__)
        port = int(argv[i + 1])
    config.LOCAL["web_listen_host"] = "127.0.0.1"
    config.REMOTE_ASSIST = config.REMOTE_APP = True
    SERVER = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    SERVER.daemon_threads = True
    APP.port, APP.remote_host = SERVER.server_address[1], "127.0.0.1"
    review.PORT = APP.port
    locks = review.demo_setup()  # noqa: F841 (held while it runs)
    write_lock()
    threading.Thread(target=APP.monitor, daemon=True).start()
    link = review.make_pairing("demo")
    print(json.dumps({"url": link, "port": APP.port, "demo": True, "remote": True, "state_dir": config.STATE_DIR}),
          flush=True)
    shots = None
    if "--screenshots" in argv:
        i = argv.index("--screenshots")
        shots = os.path.abspath(argv[i + 1]) if i + 1 < len(argv) else None
    served = threading.Thread(target=SERVER.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True)
    served.start()
    if shots:
        def shoot():
            try:
                screenshots(shots, link, remote=True)
            finally:
                APP.stopping = True
                threading.Thread(target=SERVER.shutdown, daemon=True).start()
        threading.Thread(target=shoot, daemon=True).start()
    elif "--no-open" not in argv:
        window.open_browser(link, browser_tab=True)
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=SERVER.shutdown, daemon=True).start())
    try:
        while served.is_alive():
            served.join(timeout=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        remove_lock()


if __name__ == "__main__":
    main(sys.argv[1:])
