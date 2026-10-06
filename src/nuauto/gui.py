"""The NUauto window: `nuauto gui`. A small local web app (docs/GUI.md), laptop only.

  nuauto gui                      open it (starting it again just opens another window on the running one)
  nuauto gui --demo [--demo-state a,b]
                                  everything fake (demo.py) in a temp folder: for trying it out, and for agents
  options: --port N (default: any free port), --no-open (print the URL instead of opening a window),
           --browser (a normal browser tab instead of an app window)

How it is safe to run:
- It listens on 127.0.0.1 only. A secret made at each start is in the URL it opens once; the page swaps it for a
  cookie. Every request needs that cookie and a Host header naming this server; every POST also needs the header
  X-NUauto: 1 (other websites can't send it) and JSON. Secrets (tokens, cookies, the webhook) never reach the page.
- Long or browser work runs as the same `nuauto ...` commands, one at a time, in child processes (Task). Their
  questions come over answers.JsonIO and show as dialogs; Stop sends SIGINT to the child's process group, the
  signal Ctrl+C sends. If this process dies, the child's stdin closes and it stops the same way.
- Real mode refuses to start inside a Claude Code shell (CLAUDECODE): agents use --demo.
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
import webbrowser
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from nuauto import answers
from nuauto import config
from nuauto import health
from nuauto import jobs
from nuauto import sheet

STATIC = os.path.join(config.SRC_DIR, "gui_static")
PY = sys.executable
NUAUTO = os.path.join(os.path.dirname(sys.executable), "nuauto")  # the installed command, for terminal windows
IDLE_EXIT = 30 * 60   # no page has asked anything for this long and nothing runs: quit
LOG_KEEP = 4000       # log lines kept per task
HISTORY = 20          # finished tasks kept


def now():
    return time.time()


# ---------------------------------------------------------------- tasks (child processes)

class Task:
    """One `nuauto ...` child process. Plain output lines are its log; answers.JsonIO lines are questions and
    progress events. Reads stdout in a thread; answer() and stop() are called from request threads."""

    def __init__(self, kind, label, cmd, browser, background=False, env=None, on_done=None):
        self.id = secrets.token_hex(4)
        self.kind, self.label, self.cmd, self.browser, self.background = kind, label, cmd, browser, background
        self.log, self.events, self.question, self.dropped = [], [], None, 0
        self.row = self.screenshot = self.last_shot = self.wait_until = None
        self.done_rows = []
        self.state, self.code, self.stopping = "running", None, False
        self.started, self.ended = now(), None
        self.on_done = on_done
        env = {**os.environ, **(env or {}), "PYTHONUNBUFFERED": "1"}
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, bufsize=1, env=env, cwd=config.PROJECT_DIR, start_new_session=True)
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
        self.state = "stopped" if self.stopping else "done" if self.code == 0 else "failed"
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        if self.on_done:
            self.on_done(self)

    def _event(self, msg):
        self.events.append(msg)
        del self.events[:-200]
        kind = msg.get("kind")
        if kind == "row":
            self.row, self.screenshot, self.wait_until = msg, None, None
        elif kind == "row_done":
            self.done_rows.append(msg)
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
        """SIGINT to the process group (what Ctrl+C sends); force: SIGKILL, for a run that will not stop."""
        if self.state != "running":
            return
        self.stopping = True
        try:
            os.killpg(self.proc.pid, signal.SIGKILL if force else signal.SIGINT)
        except ProcessLookupError:
            pass

    def total(self):
        return self.dropped + len(self.log)

    def view(self, after=0):
        """after: how many log lines the page already has (counted from the first line ever)."""
        return {"id": self.id, "kind": self.kind, "label": self.label, "state": self.state, "code": self.code,
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
        self.history = []
        self.results = {}          # (check id, group) -> health.Check
        self.running_groups = set()
        self.next_run = {}         # group -> time
        self.notified = {}         # check id -> (status, time) of the last desktop notification
        self.first_round_done = False
        self.rows, self.rows_at, self.rows_error = None, 0, None
        self.queue = {}            # job id -> pool entry, from the last review queue
        self.last_request = now()
        self.stopping = False

    # ---- tasks

    def start(self, kind, label, cmd, browser=True, background=False, env=None, on_done=None):
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
            task = Task(kind, label, cmd, browser, background, env, on_done=self._done(on_done))
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
    }

    def run_group(self, group):
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
                if checks and checks[0].status == health.BUSY:
                    self.next_run["nuworks"] = now() + 120  # try again soon
                    keep_old = ("nuworks", "nuworks") in self.results  # keep the last real answer
            if not keep_old and not task.stopping:
                self._store("nuworks", checks)
        try:
            task = self.start("nuworks_check", "Checking NUworks", [PY, "-m", "nuauto.health", "--json", "nuworks"],
                              background=True, on_done=done)
        except Busy:
            task = None
        if task is None:  # something else uses the browser: try again in a few minutes
            with self.lock:
                self.running_groups.discard("nuworks")
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
                 "discord", "homelab"]
        return sorted(out, key=lambda c: order.index(c.id) if c.id in order else 99)

    def notify_changes(self):
        """A desktop notification when a check turns bad (not for what was already bad at start)."""
        if not self.first_round_done:
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
        for group, (_, _, delay) in self.GROUPS.items():
            self.next_run[group] = start + delay
        while not self.stopping:
            for group in list(self.GROUPS):
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
            if g in self.GROUPS:
                threading.Thread(target=self.run_group, args=(g,), daemon=True).start()

    def quit(self):
        self.stopping = True
        for t in (self.task, self.background):
            if t and t.state == "running":
                t.stop()
                try:
                    t.proc.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    t.stop(force=True)
        remove_lock()
        threading.Thread(target=SERVER.shutdown, daemon=True).start()


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
    return {"applied": week, "max": sheet.MAX_PER_WEEK, "label": label, "total": total, "max_total": sheet.MAX_TOTAL,
            "next": f"{nxt:%a %b} {nxt.day}" if nxt else None, "room": max(0, sheet.MAX_PER_WEEK - week)}


def row_view(r, **extra):
    return {"row": r.number, "company": r.company, "title": r.title, "url": r.url, "status": r.status,
            "notes": r.notes, "date": r.date, **extra}


def company_rows(rows):
    from nuauto import assist
    agent, other, site = [], [], []
    for r in rows or []:
        if r.status == "Needs Human":
            url, why = assist.assist_target(r.notes)
            if url:
                agent.append(row_view(r, target=url, host=urlparse(url).hostname or url))
            else:
                other.append(row_view(r, why=why))
        elif r.status == "Applied" and r.notes.startswith(sheet.SITE_MARK):
            site.append(row_view(r))
    return agent, other, site


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
    if not os.path.exists(config.LOCAL_CONFIG_PATH):
        return True
    cfg = read_local()
    return not (cfg.get("sheet_id") and cfg.get("sheet_id") != health.PLACEHOLDER_SHEET and cfg.get("tos_ack"))


def read_local():
    try:
        with open(config.LOCAL_CONFIG_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def state():
    rows = APP.rows
    todo, soon = review_counts(rows)
    agent, other, site = company_rows(rows)
    approved = [r for r in rows or [] if r.status == "Approved"]
    scans = jobs.load("scans.json", [])
    cfg = read_local()
    sheet_id = cfg.get("sheet_id") or ""
    task = APP.task
    return {
        "demo": config.DEMO, "version": __import__("nuauto").__version__,
        "mode": "homelab" if config.HAS_SERVER else "local", "setup_needed": setup_needed(),
        "rows_loaded": rows is not None, "rows_error": APP.rows_error, "week": week_info(rows),
        "counts": {"review": len(todo), "approved": len(approved), "company": len(agent), "site": len(site),
                   "needs_human": len(agent) + len(other)},
        "todo": ([{"kind": "site", **x} for x in site] + [{"kind": "company", **x} for x in agent]
                 + [{"kind": "urgent", "id": r["id"], "title": r["title"], "company": r["company"],
                     "closes": r["closes"], "match": r["match"], "url": jobs.job_url(r["id"])} for r in soon]),
        "last_update": scans[-1] if scans else None,
        "sheet_url": f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit" if sheet_id and not config.DEMO else None,
        "task": task.view(task.total()) if task else None,
    }


def review(mode):
    rows = need_rows()
    details, ratings = jobs.load_details(), jobs.load("ratings.json", {})
    todo, urgent = jobs.review_queue(mode, details, ratings, rows)
    APP.queue = {r["id"]: r for r in todo}
    out = []
    for r in todo:
        d = details[r["id"]]
        day = jobs.closes(d)
        out.append({"id": r["id"], "title": r["title"], "company": r["company"], "category": r["category"],
                    "match": r["match"], "closes_text": jobs.closes_text(day),
                    "soon": day is not None and (day - date.today()).days <= jobs.URGENT_DAYS,
                    "rating": (ratings.get(r["id"]) or {}).get("label")})
    return {"jobs": out, "urgent": [r["id"] for r in urgent], "mode": mode}


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


def worksheet():
    try:
        return sheet.open_worksheet(interactive=False)
    except sheet.NotLoggedIn as e:
        raise Refused(f"{e} Log in to Google first (Settings, or the Google dot at the top).")


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
        if sheet.add_proposed(ws, [jobs.sheet_item(r)], status="Approved") != 1:
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


def apply_list():
    from nuauto import apply
    rows = need_rows()
    week = week_info(rows)
    out = []
    for r in apply.apply_order(sheet.approved(rows)):
        day = apply.row_closes(r)
        out.append(row_view(r, closes_text=jobs.closes_text(day), past=day is not None and day < date.today(),
                            soon=day is not None and (day - date.today()).days <= jobs.URGENT_DAYS))
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
    return [PY, "-m", "nuauto.demo", *args]


def act(body):
    kind = body.get("kind")
    args = body.get("args") or {}
    if kind == "apply":
        why = apply_list()["why_not"]
        if why:
            raise Refused(why)
        n = args.get("n")
        cmd = [PY, "-m", "nuauto", "apply", "--ui", "json"]
        if n not in (None, ""):
            if not str(n).isdigit() or int(n) < 1:
                raise Refused("At most how many? A whole number, 1 or more.")
            cmd += ["-n", str(int(n))]
        task = APP.start("apply", "Applying", cmd, on_done=lambda t: APP.run_now(["quick"]))
    elif kind == "update":
        cmd = demo_cmd("update") if config.DEMO else [PY, "-m", "nuauto", "update"]
        task = APP.start("update", "Checking for new jobs", cmd, browser=not config.HAS_SERVER,
                         on_done=lambda t: APP.run_now(["quick"]))
    elif kind == "login_nuworks":
        cmd = demo_cmd("login_nuworks") if config.DEMO else [PY, "-m", "nuauto", "login"]
        task = APP.start("login_nuworks", "NUworks login", cmd, on_done=lambda t: APP.run_now(["quick", "nuworks"]))
    elif kind == "login_google":
        task = APP.start("login_google", "Google login", [PY, "-m", "nuauto", "login", "google"], browser=False,
                         on_done=lambda t: APP.run_now(["quick", "sheet"]))
    elif kind == "check_nuworks":
        APP.run_now(["nuworks"])
        return {}
    elif kind == "install_firefox":
        task = APP.start("install_firefox", "Installing the browser", [PY, "-m", "playwright", "install", "firefox"],
                         browser=False, on_done=lambda t: APP.run_now(["firefox"]))
    elif kind == "install_claude":
        cmd = ([PY, "-c", "print('Demo: Claude Code would be installed here.')"] if config.DEMO
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
                         [PY, "-m", "nuauto", "assist", "nuworks", str(int(row)), "--ui", "json"])
    elif kind == "assist":
        row = args.get("row")
        if not str(row).isdigit():
            raise Refused("Which row?")
        return open_terminal(f"{shlex.quote(NUAUTO)} assist {int(row)}",
                             "The assistant runs here. It asks you before anything is submitted.")
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
    agent, other, site = company_rows(rows)
    return {"agent": agent, "other": other, "site": site}


def mark(body):
    action, row, url = body.get("action"), body.get("row"), str(body.get("url", ""))
    if action not in ("applied", "site") or not str(row).isdigit():
        raise Refused("Unknown action.")
    ws = worksheet()
    try:
        if action == "site":
            sheet.mark_site_done(ws, int(row), url)
        else:
            sheet.mark_applied_by_hand(ws, int(row), url, how="by hand (marked in NUauto)")
    except sheet.SheetError as e:
        raise Refused(str(e))
    APP.refresh_rows(force=True)
    return {}


TERMINALS = [  # (command, how it takes a command to run)
    ("x-terminal-emulator", ["-e"]), ("gnome-terminal", ["--"]), ("ptyxis", ["--"]), ("kgx", ["--"]),
    ("konsole", ["-e"]), ("xfce4-terminal", ["-x"]), ("mate-terminal", ["-x"]), ("tilix", ["-e"]),
    ("kitty", []), ("alacritty", ["-e"]), ("wezterm", ["start", "--"]), ("foot", []), ("terminator", ["-x"]),
    ("xterm", ["-e"]),
]


def open_terminal(command, intro):
    """Run a shell command in a new terminal window (you type there: the assistant's questions, Claude's login).
    Returns {"opened": bool, "command": what to paste if no terminal app was found}."""
    script = (f"cd {shlex.quote(config.PROJECT_DIR)}; echo {shlex.quote(intro)}; echo; {command}; "
              "echo; read -rp 'Done. Press Enter to close this window. ' _")
    if config.DEMO:
        return {"opened": False, "command": command, "demo": True}
    if sys.platform == "darwin":
        apple = f'tell application "Terminal" to do script {json.dumps("bash -lc " + shlex.quote(script))}'
        ok = subprocess.run(["osascript", "-e", apple, "-e", 'tell application "Terminal" to activate'],
                            capture_output=True).returncode == 0
        return {"opened": ok, "command": command}
    candidates = ([(os.environ["TERMINAL"], ["-e"])] if os.environ.get("TERMINAL") else []) + TERMINALS
    for name, flag in candidates:
        path = shutil.which(name)
        if path:
            subprocess.Popen([path, *flag, "bash", "-lc", script], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"opened": True, "command": command}
    return {"opened": False, "command": command}


# ---------------------------------------------------------------- answers, logs, files

def answers_get():
    entries = answers.load()
    try:
        version = os.path.getmtime(config.ANSWERS_PATH)
    except OSError:
        version = 0
    return {"entries": entries, "version": version, "locked": running("apply", "nuworks_side")}


def running(*kinds):
    t = APP.task
    return bool(t and t.state == "running" and t.kind in kinds)


def answers_put(body):
    if running("apply", "nuworks_side"):
        raise Refused("A run is using the answer bank. Edit it when the run is over.")
    if body.get("version") != answers_get()["version"]:
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
        clean.append({"question": q, "aliases": keys[1:], "answer": str(e.get("answer", "")).strip(),
                      "field_type": ft, "date_added": str(e.get("date_added") or date.today().isoformat())[:10],
                      "always_ask": bool(e.get("always_ask"))})
    answers.save(clean)
    return answers_get()


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

PUBLIC_SETTINGS = ["sheet_id", "resume_path", "week_start", "assist_read_paths", "server_hostname", "server_ssh",
                   "server_dir", "web_base_url", "web_listen_host", "tos_ack"]


def settings_get():
    cfg = read_local()
    return {"settings": {k: cfg.get(k, "" if k != "assist_read_paths" else []) for k in PUBLIC_SETTINGS},
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
    restart_needed = {"sheet_id", "resume_path", "week_start", "server_hostname", "server_ssh", "server_dir"} & set(updates)
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


# ---------------------------------------------------------------- HTTP

class NotFound(Exception):
    pass


GET_ROUTES = {
    "/api/state": lambda q: state(),
    "/api/health": lambda q: {"checks": [c.to_dict() for c in APP.checks()], "running": sorted(APP.running_groups)},
    "/api/review": lambda q: review(q.get("mode", "approve") if q.get("mode") in ("approve", "rate") else "approve"),
    "/api/apply": lambda q: apply_list(),
    "/api/company": lambda q: company(),
    "/api/task": lambda q: {"task": APP.task.view(int(q.get("after", 0) or 0)) if APP.task else None},
    "/api/answers": lambda q: answers_get(),
    "/api/logs": lambda q: logs(),
    "/api/settings": lambda q: settings_get(),
}
POST_ROUTES = {
    "/api/action": act,
    "/api/answer": answer,
    "/api/stop": stop,
    "/api/decide": decide,
    "/api/undo": undo,
    "/api/review/done": lambda body: review_done(),
    "/api/mark": mark,
    "/api/answers": answers_put,
    "/api/settings": settings_put,
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
        self.end_headers()
        self.wfile.write(data)

    def _error(self, code, msg):
        self._send(code, {"error": msg})

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

    def do_POST(self):
        if not self._host_ok():
            return self._error(403, "wrong host")
        u = urlparse(self.path)
        if u.path == "/api/window":  # a second `nuauto gui` asks this one to open a window
            if hmac.compare_digest(self.headers.get("X-NUauto-Open", "").encode(), APP.open_secret.encode()):
                open_window(start_url(), browser_tab="--browser" in sys.argv)
                return self._send(200, {})
            return self._error(403, "forbidden")
        origin = self.headers.get("Origin")
        if (not self._authed() or self.headers.get("X-NUauto") != "1"
                or not self.headers.get("Content-Type", "").startswith("application/json")
                or (origin and origin not in (f"http://127.0.0.1:{APP.port}", f"http://localhost:{APP.port}"))):
            return self._error(403, "forbidden")
        APP.last_request = now()
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}") if length <= 2_000_000 else None
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


CHROMES = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "brave-browser", "microsoft-edge"]
MAC_CHROMES = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
               "/Applications/Chromium.app/Contents/MacOS/Chromium",
               "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
               "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]


def open_window(url, browser_tab=False):
    """An app window (Chrome/Chromium --app: no tabs or address bar) when one is installed, else a browser tab."""
    if not browser_tab:
        found = [p for p in MAC_CHROMES if os.path.exists(p)] if sys.platform == "darwin" else \
            [shutil.which(c) for c in CHROMES if shutil.which(c)]
        if found:
            subprocess.Popen([found[0], f"--app={url}", "--new-window"], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
    webbrowser.open(url)


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
    r = subprocess.run([PY, "-m", "nuauto.demo", "setup", states], env=env, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(r.stderr.strip() or r.stdout.strip())
    os.execve(PY, [PY, "-m", "nuauto", "gui", *[a for a in argv if a != "--demo"]], env)


def main(argv):
    argv = list(argv)
    if "--demo" in argv and not config.DEMO:
        return start_demo(argv)
    if config.IS_SERVER:
        sys.exit("nuauto gui runs on your laptop, not the homelab.")
    if os.environ.get("CLAUDECODE") and not config.DEMO:
        sys.exit("The real NUauto window never starts inside a Claude Code session (it can submit applications). "
                 "Start it yourself from your apps menu or a normal terminal; agents use `nuauto gui --demo`.")
    port = 0
    if "--port" in argv:
        i = argv.index("--port")
        if i + 1 >= len(argv) or not argv[i + 1].isdigit():
            sys.exit(__doc__)
        port = int(argv[i + 1])
    no_open = "--no-open" in argv
    lock = running_instance()
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
    write_lock()
    threading.Thread(target=APP.monitor, daemon=True).start()
    if config.HAS_SERVER:
        threading.Thread(target=pull_loop, daemon=True).start()
    url = start_url()
    print(json.dumps({"url": url, "port": APP.port, "demo": config.DEMO, "state_dir": config.STATE_DIR}), flush=True)
    if not no_open:
        open_window(url, browser_tab="--browser" in argv)

    def bye(*_):
        threading.Thread(target=APP.quit, daemon=True).start()
    signal.signal(signal.SIGTERM, bye)
    try:
        SERVER.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        APP.quit()
    finally:
        remove_lock()


if __name__ == "__main__":
    main(sys.argv[1:])
