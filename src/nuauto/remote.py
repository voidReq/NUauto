"""The homelab assistant's queue and claims (Phase 3c; docs/REMOTE.md).

THE QUEUE (homelab; `nuauto assist auto`, run by nuauto-assist.timer every 10 minutes)
Sessions start by themselves from your approved rows: the Other jobs tab's Approved rows and the Needs Human rows
stopped at a company site (assist.assist_target), in order: closing within a week first, then match %, then sheet
order. Only with "remote_assist" and "remote_auto" true in local_config.json, only during "remote_hours" (default
[9, 22]: 09:00 to 22:00 New York time, so no pings at night), only into free slots (assist.slots()), one session per
job site, NUworks rows only under the weekly cap, never a row the laptop has claimed. Each row starts by itself once
(local/assist_auto.json): if that session ends without "Applied", the row waits for you (start it from the review
page's queue, or by hand). Each session runs in its own tmux session, nuauto-<tab>-<row>, in its own systemd scope
(it outlives the timer's run); attach with `ssh -t homelab tmux attach -t nuauto-other-12`.

CLAIMS (both machines)
A laptop session first asks the homelab, over ssh, for a claim on its row and job site (`nuauto assist claim ...`
there): refused when the homelab runs that row or that site, or the laptop already holds it. While the laptop session
runs, the claim (local/assist_claims/ on the homelab; ignored after CLAIM_HOURS) keeps the homelab's queue and its own
sessions off that row and site; the laptop lets go when its session ends. Homelab unreachable: the laptop goes ahead
and says so.

  nuauto assist auto | queue                 start what the queue allows now / show what it would start
  nuauto assist start <nuworks|other> <row> [--again]   (homelab) start one row's session now, in tmux (--again: also
                                             after an approved Submit whose outcome nobody gave)
  nuauto assist sessions                     (homelab) the running sessions, as JSON (the laptop's GUI reads it)
  nuauto assist claim|unclaim <nuworks|other> <row> <host>   (homelab) what the laptop's sessions run over ssh
"""
import fcntl
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import date, datetime

from nuauto import config

AUTO_PATH = os.path.join(config.LOCAL_DIR, "assist_auto.json")      # rows the queue started: never again by itself
CLAIMS_DIR = os.path.join(config.LOCAL_DIR, "assist_claims")       # the laptop's running sessions (on the homelab)
CLAIM_HOURS = 12
HOURS = (9, 22)
STARTUP_CHECK = 8  # seconds after starting: still running?
TABS = {"nuworks": "jobs", "other": "other"}                        # the command's word -> assist's tab name


def tab_word(tab):
    return "other" if tab == "other" else "nuworks"


def hours():
    """(start hour, end hour) from "remote_hours" ([9, 22] or "9-22"), New York time (the units' TZ)."""
    h = config.LOCAL.get("remote_hours", HOURS)
    try:
        a, b = (int(x) for x in (h.split("-") if isinstance(h, str) else h))
        if 0 <= a < b <= 24:
            return a, b
    except (TypeError, ValueError):
        pass
    return HOURS


def in_hours(now=None):
    a, b = hours()
    return a <= (now or datetime.now()).hour < b


def match_of(notes):
    m = re.search(r"match (\d{1,3})%", notes or "")
    return int(m.group(1)) if m else -1


def candidates(main_rows, other_rows, closes=None, today=None):
    """The rows the queue may start, best first: [{"tab", "row", "url", "host", "company", "title", "closes", "match"}].
    closes(row) -> its closing date or None (NUworks rows; apply.row_closes). Pure apart from closes."""
    from nuauto import assist
    from nuauto import jobs
    today = today or date.today()
    out = []
    for r in main_rows or []:
        if r.status != "Needs Human":
            continue
        url, _ = assist.assist_target(r.notes)
        if url:
            day = closes(r) if closes else None
            if day and day < today:
                continue  # past its deadline
            out.append({"tab": "jobs", "row": r.number, "url": url, "host": assist.host(url), "company": r.company,
                        "title": r.title, "closes": day, "match": match_of(r.notes), "laptop": assist.LAPTOP_MARK in r.notes})
    for r in other_rows or []:
        url, _ = assist.other_target(r)
        if url:
            out.append({"tab": "other", "row": r.number, "url": url, "host": assist.host(url), "company": r.company,
                        "title": r.title, "closes": None, "match": match_of(r.notes), "laptop": assist.LAPTOP_MARK in r.notes})
    urgent = lambda c: c["closes"] is not None and (c["closes"] - today).days <= jobs.URGENT_DAYS  # noqa: E731
    return sorted(out, key=lambda c: (not urgent(c), c["closes"] if urgent(c) else date.max, -c["match"],
                                      c["tab"] != "jobs", c["row"]))


OUTCOME_UNKNOWN = ("a Submit was approved in its last session and nobody said whether it went through: check the site, "
                   "then mark the row (Applied), or start it again by hand: nuauto assist start <nuworks|other> <row> --again")


def plan(cands, running, claims, records, free, jobs_left=None, manual=False):
    """Which of cands to start now: [(candidate, None)] to start and [(candidate, why not)] for the rest. running: the
    homelab's sessions (assist.sessions); claims: the laptop's live claims; records: what the queue knows of earlier
    sessions ({"<tab>-<row>": {"url", "auto", "approved", "answer"}}); free: free slots; jobs_left: NUworks-tab
    applications still allowed (week and total; None = no limit to check). manual: you start it (Start now): the
    start-once rule doesn't apply, the others do. Pure (tests/test_remote.py)."""
    busy_rows = {(s.get("tab"), s.get("row")) for s in running} | {(c.get("tab"), c.get("row")) for c in claims}
    busy_hosts = {s.get("host") for s in running} | {c.get("host") for c in claims}
    go, rest = [], []
    for c in cands:
        rec = records.get(f"{c['tab']}-{c['row']}") or {}
        same = rec.get("url") == c["url"]  # a row reused for another job starts fresh
        if (c["tab"], c["row"]) in busy_rows:
            why = "a session is on it"
        elif c.get("laptop") or (same and rec.get("answer") == "laptop"):
            why = "marked: do it on the laptop (the homelab's browser was refused there)"
        elif same and rec.get("approved") and rec.get("answer") not in ("y", "n", "laptop"):
            why = OUTCOME_UNKNOWN
        elif same and not manual:  # any earlier session (yours or the queue's): never again by itself
            why = "had a session already: yours to start now"
        elif c["tab"] == "jobs" and jobs_left is not None and jobs_left <= 0:
            why = "this week's (or the total) NUworks applications are used up"
        elif c["host"] in busy_hosts:
            why = f"another session is on {c['host']}"
        elif len(go) >= free:
            why = "waiting for a free slot"
        else:
            go.append((c, None))
            busy_hosts.add(c["host"])
            if c["tab"] == "jobs" and jobs_left is not None:
                jobs_left -= 1
            continue
        rest.append((c, why))
    return go, rest


# ---------- claims (on the homelab) ----------

@contextmanager
def _claims_lock():
    os.makedirs(CLAIMS_DIR, mode=0o700, exist_ok=True)
    with open(os.path.join(CLAIMS_DIR, ".lock"), "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def _claim_path(tab, row):
    return os.path.join(CLAIMS_DIR, f"{tab}-{int(row)}.json")


def live_claims(now=None):
    """The laptop's claims that still count: [{"tab", "row", "host", "since"}]."""
    now = now or time.time()
    out = []
    try:
        names = os.listdir(CLAIMS_DIR)
    except OSError:
        return out
    for n in names:
        if n.endswith(".json"):
            try:
                with open(os.path.join(CLAIMS_DIR, n)) as f:
                    c = json.load(f)
            except (OSError, ValueError):
                continue
            if now - c.get("since", 0) < CLAIM_HOURS * 3600:
                out.append(c)
    return out


def claim(tab, row, host):
    """(homelab) A claim for a laptop session: (True, None), or (False, why)."""
    from nuauto import assist
    with _claims_lock():
        for s in assist.sessions():
            if s.get("tab") == tab and s.get("row") == row:
                return False, f"the homelab's assistant is on this row now (slot {s.get('slot')})"
            if s.get("host") == host:
                return False, f"the homelab's assistant is on {host} now (two could make the same account there at once)"
        for c in live_claims():
            if (c["tab"], c["row"]) != (tab, row) and c.get("host") == host:
                return False, f"another laptop session is on {host}"
        path = _claim_path(tab, row)
        with open(path + ".tmp", "w") as f:  # whole or not at all: a reader never sees half a claim
            json.dump({"tab": tab, "row": row, "host": host, "since": time.time()}, f)
        os.replace(path + ".tmp", path)
    return True, None


def unclaim(tab, row):
    with _claims_lock():
        try:
            os.remove(_claim_path(tab, row))
        except OSError:
            pass


def claimed(tab, row, host):
    """(homelab) Why a session must not start here: the laptop holds this row or job site; else None. Under the claims'
    lock, after the session took its slot: a laptop claim either came first (seen here) or sees that slot."""
    with _claims_lock():
        for c in live_claims():
            if (c["tab"], c["row"]) == (tab, row):
                return "the laptop's assistant is on this row now"
            if c.get("host") == host:
                return f"the laptop's assistant is on {host} now"
    return None


# ---------- claims (from the laptop) ----------

def _homelab(*args, timeout=30):
    """`nuauto assist <args>` on the homelab: (exit code, output), or (None, "") when it can't be reached."""
    from nuauto import sync
    cmd = sync.SSH + [config.SERVER, f"cd {shlex.quote(config.SERVER_DIR)} && .venv/bin/nuauto assist "
                      + " ".join(shlex.quote(str(a)) for a in args)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None, ""
    return (r.returncode, r.stdout) if r.returncode in (0, 3) else (None, "")


def claim_on_homelab(tab, row, host):
    """(laptop) (True, note) to go ahead, or (False, why). note: None, or why the homelab couldn't be asked."""
    code, out = _homelab("claim", tab_word(tab), row, host)
    if code is None:
        return True, "the homelab could not be reached, so nothing there knows this row is taken"
    try:
        r = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return True, "the homelab gave no answer about this row"
    return (True, None) if r.get("ok") else (False, r.get("why") or "the homelab refused")


def unclaim_on_homelab(tab, row):
    _homelab("unclaim", tab_word(tab), row, "-")


def homelab_sessions():
    """(laptop) The homelab's running sessions ([] when it can't be reached), for the GUI."""
    code, out = _homelab("sessions", timeout=20)
    try:
        return json.loads(out) if code == 0 else []
    except ValueError:
        return []


# ---------- starting sessions (homelab) ----------

def tmux_name(tab, row):
    return f"nuauto-{tab_word(tab)}-{int(row)}"


def start(tab, row):
    """Start one row's session in its own tmux session (in its own systemd scope, so it outlives whoever started it).
    Returns None, or why it did not start."""
    if not shutil.which("tmux"):
        return "tmux is not installed"
    name = tmux_name(tab, row)
    if subprocess.run(["tmux", "has-session", "-t", f"={name}"], capture_output=True).returncode == 0:
        if not ended(name):
            return f"tmux session {name} exists already"
        subprocess.run(["tmux", "kill-session", "-t", f"={name}"], capture_output=True)  # an earlier run's, ended
    cmd = config.self_cmd("assist", *(["other"] if tab == "other" else []), str(int(row)))
    shell = f"cd {shlex.quote(config.PROJECT_DIR)} && exec {' '.join(shlex.quote(c) for c in cmd)}"
    # remain-on-exit: when the session's program ends, its last screen stays (attach to read why it stopped)
    argv = ["tmux", "new-session", "-d", "-s", name, "-x", "160", "-y", "48", shell, ";",
            "set-option", "-t", f"={name}:", "remain-on-exit", "on"]
    if shutil.which("systemd-run"):
        argv = ["systemd-run", "--user", "--scope", "--quiet", "--collect",
                f"--unit=nuauto-assist-{tab_word(tab)}-{int(row)}-{int(time.time())}", *argv]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=30, env={**os.environ, "TERM": "xterm-256color"})
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"could not start tmux ({type(e).__name__})"
    return None if r.returncode == 0 else f"tmux: {(r.stderr or r.stdout).strip()[:200]}"


def ended(name):
    """True if this tmux session's program has ended (its last screen kept by remain-on-exit), or it is gone."""
    r = subprocess.run(["tmux", "display-message", "-p", "-t", f"={name}:", "#{pane_dead}"], capture_output=True, text=True)
    return r.returncode != 0 or r.stdout.strip() == "1"


class RecordError(Exception):
    """local/assist_auto.json can't be read: the queue starts nothing (it could start a row twice)."""


def _read_auto():
    try:
        with open(AUTO_PATH) as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        raise RecordError(f"{AUTO_PATH} can't be read ({type(e).__name__}): fix or remove it; the queue starts nothing "
                          "meanwhile") from None
    if not isinstance(data, dict):
        raise RecordError(f"{AUTO_PATH} is not what the queue wrote: fix or remove it")
    return data


@contextmanager
def _records():
    """The queue's records of earlier sessions, read and written under a lock (the timer, Start now and the sessions'
    ends write them)."""
    os.makedirs(config.LOCAL_DIR, mode=0o700, exist_ok=True)
    with open(AUTO_PATH + ".lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        data = _read_auto()
        yield data
        from nuauto import assist
        assist.write_private(AUTO_PATH, data)


def record_outcome(tab, row, url, approved, answer):
    """(homelab) How a session ended: whether you approved a Submit in it, and your answer to "Did it go through?"
    (y / n / None: nobody said). Never fails the end of a session."""
    try:
        with _records() as data:
            key = f"{tab}-{int(row)}"
            old = data.get(key) if (data.get(key) or {}).get("url") == url else {}
            data[key] = {**(old or {}), "url": url, "approved": bool(approved), "answer": answer,
                         "ended": datetime.now().isoformat(timespec="minutes")}
    except (RecordError, OSError) as e:
        print(f"(could not record how this session ended: {e})")


def queue_inputs():
    """What the queue decides from (homelab): (candidates best first, running sessions, the laptop's claims, records,
    free slots, NUworks applications left). One sheet read."""
    from nuauto import apply
    from nuauto import assist
    from nuauto import sheet
    ws = sheet.open_worksheet(interactive=False)
    main_rows = sheet.read_rows(ws)
    try:
        other_rows = sheet.read_rows(sheet.open_other(interactive=False))
    except sheet.SheetError:
        other_rows = []
    running, claims = assist.sessions(), live_claims()
    free = max(0, assist.slots() - len(running))
    return (candidates(main_rows, other_rows, apply.row_closes), running, claims, _read_auto(), free,
            jobs_left(main_rows, running, claims))


def queue_now(manual=False):
    """(go, rest): what the queue would start now and why the others wait (homelab). manual: you start them."""
    return plan(*queue_inputs(), manual)


def can_start(c, inputs):
    """Why you can't start this one row by hand right now, or None: judged on its own (a free slot, its job site free,
    the caps, its records, the laptop mark), not behind the rows ahead of it in the queue."""
    _, running, claims, records, free, left = inputs
    go, rest = plan([c], running, claims, records, free, left, manual=True)
    return None if go else rest[0][1]


def jobs_left(main_rows, running, claims):
    """NUworks-tab applications still allowed now: the weekly cap and the total, minus the NUworks-tab sessions running
    on either machine (each may submit one)."""
    from nuauto import sheet
    dates = sheet.applied_dates(main_rows)
    left = min(sheet.max_per_week() - sheet.week_count(dates), sheet.MAX_TOTAL - len(dates))
    return left - sum(1 for x in [*running, *claims] if x.get("tab") == "jobs")


def record_started(c, auto=True):
    with _records() as data:
        key = f"{c['tab']}-{c['row']}"
        old = data.get(key) if (data.get(key) or {}).get("url") == c["url"] else {}
        data[key] = {**(old or {}), "url": c["url"], "company": c["company"], "auto": bool(auto or (old or {}).get("auto")),
                     "approved": False, "answer": None, "started": datetime.now().isoformat(timespec="minutes")}


def auto():
    """The timer's run: start what the queue allows now. Quiet when there is nothing to do."""
    if not (config.IS_SERVER and config.REMOTE_ASSIST and config.LOCAL.get("remote_auto")):
        return
    if not in_hours():
        return
    try:
        go, _ = queue_now()
    except RecordError as e:
        return print(e)
    started = []
    for c, _ in go:
        why = start(c["tab"], c["row"])
        if why:
            print(f"{c['company']} ({c['tab']} row {c['row']}): not started: {why}")
            continue
        record_started(c)
        started.append(c)
        print(f"started {c['company']} ({c['tab']} row {c['row']}) in tmux {tmux_name(c['tab'], c['row'])}")
    if started:
        time.sleep(STARTUP_CHECK)  # a session that stops at once (setup missing, a refused row) says why on its screen
        dead = [c for c in started if ended(tmux_name(c["tab"], c["row"]))]
        from nuauto import daily
        from nuauto import review
        lines = [f"{c['company']}: {c['title']}" + (f" (ended right away: ssh -t homelab tmux attach -t "
                                                     f"{tmux_name(c['tab'], c['row'])} shows why)" if c in dead else "")
                 for c in started]
        daily.discord("**NUauto: assistants started on the homelab**\n" + "\n".join(lines)
                      + f"\nThey ping you when they need you: {review.page_url()}")


def sessions_json():
    from nuauto import assist
    keep = ("slot", "row", "tab", "company", "host", "since", "attention")
    print(json.dumps([{k: s[k] for k in keep if k in s} for s in assist.sessions()]))


def main(argv):
    if argv[:1] in (["claim"], ["unclaim"]) and len(argv) == 4 and argv[1] in TABS and argv[2].isdigit():
        if not config.IS_SERVER:
            sys.exit("claim / unclaim run on the homelab (the laptop's sessions call them over ssh).")
        tab, row = TABS[argv[1]], int(argv[2])
        if argv[0] == "unclaim":
            return unclaim(tab, row)
        ok, why = claim(tab, row, argv[3])
        print(json.dumps({"ok": ok, "why": why}))
        sys.exit(0 if ok else 3)
    if not config.IS_SERVER:
        sys.exit("The queue runs on the homelab (docs/REMOTE.md).")
    if argv == ["sessions"]:
        return sessions_json()
    if argv == ["auto"]:  # the timer's: quietly nothing while remote_assist / remote_auto are off
        return auto()
    if not config.REMOTE_ASSIST:
        sys.exit("Turn on \"remote_assist\" in local_config.json first (docs/REMOTE.md).")
    if argv == ["queue"]:
        inputs = queue_inputs()
        go, rest = plan(*inputs)
        by_itself = {(c["tab"], c["row"]): why for c, why in go + rest}
        a, b = hours()
        free = inputs[4]
        print(f"Starts by itself: {'on' if config.LOCAL.get('remote_auto') else 'off (remote_auto)'}, {a}:00-{b}:00"
              f"{'' if in_hours() else ' (outside those hours now)'}; {free} slot{'s' if free != 1 else ''} free")
        for c in inputs[0]:
            hand, auto_ = can_start(c, inputs), by_itself.get((c["tab"], c["row"]))
            text = ("next by itself" if auto_ is None and config.LOCAL.get("remote_auto") else "ready (start it yourself)") \
                if hand is None else hand
            print(f"  {c['company'][:28]:28} {c['tab']:5} row {c['row']:<4} {c['host'][:34]:34} {text}")
        return
    if argv[:1] == ["start"] and len(argv) in (3, 4) and argv[1] in TABS and argv[2].isdigit() and \
            argv[3:] in ([], ["--again"]):
        tab, row = TABS[argv[1]], int(argv[2])
        if argv[3:] != ["--again"]:
            rec = _read_auto().get(f"{tab}-{row}") or {}
            if rec.get("approved") and rec.get("answer") not in ("y", "n"):
                sys.exit(f"Not started: {OUTCOME_UNKNOWN}.")
        why = start(tab, row)
        if why:
            sys.exit(why)
        return print(f"Started in tmux {tmux_name(tab, row)}: ssh -t homelab tmux attach -t {tmux_name(tab, row)}")
    sys.exit(__doc__)
