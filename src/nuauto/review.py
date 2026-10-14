"""The homelab side of NUauto on your phone: pairing your devices, and the assistant sessions running there (docs/REMOTE.md).

The page itself is the NUauto app (gui.serve_remote: the same screens as `nuauto gui`), served inside `nuauto web` on
web_listen_host:8766, the homelab's Tailscale IP: only your own devices reach it, and never through the public
Mark-done tunnel (that one forwards 8765). This module holds what the app's homelab parts are made of:
  - pairing: `nuauto review pair` makes a one-time link (10 minutes); open it on your phone, press Pair: a long random
    key goes in a cookie there (HttpOnly, SameSite=Strict) and the homelab keeps only its hash
    (local/review_devices.json). Discord links only point to the page: no power by themselves.
  - each running assistant session (assist.sessions), for the app's Assistants screen (#/assistant/<slot>):
    a Submit waiting for you (what it will click and on which page, the guesses you haven't confirmed, what you changed,
    anything typed with no answer behind it, the screen when it asked; Approve / Decline with a note: the guard waits up
    to assist.WAIT for you, a later Approve is a pass for that one action, assist.take_grant); its whole last message and
    a reply typed into its terminal (tmux), as if you typed it there; the browser view's link and password (captchas:
    you solve them there, then reply "done"); "Did it go through?" when it ended; Interrupt (Esc), End session (/exit).
    Every answer goes in the run's actions.log with the device's name.
  - the queue (remote.py) for the app's Company sites: each row's group and what it waits for; Start now.

  nuauto review pair [<name>] [--discord]   a pairing link (printed; --discord also posts it)
  nuauto review devices                     the paired devices
  nuauto review unpair <name>|--all         forget a device (its cookie stops working)
  nuauto review demo [<port>]               the homelab's app with made-up sessions, on this computer (prints a pairing
                                            link; the same as `nuauto gui --demo --remote --no-open`)
On the laptop these run on the homelab over ssh (all but demo).
"""
import hashlib
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime

from nuauto import config

PORT = 8766
COOKIE = "nuauto_review"
DEVICES = os.path.join(config.LOCAL_DIR, "review_devices.json")
PAIRING = os.path.join(config.LOCAL_DIR, "review_pair.json")
PAIR_TTL = 600         # seconds a pairing link works (once)
MAX_REPLY = 4000       # characters typed into a session at once
TERMINAL_LINES = 60


def listen_host():
    from nuauto import screen
    return screen.listen_host()


def page_url(slot=None):
    """The homelab's NUauto page (gui.serve_remote), or one assistant session's screen in it."""
    try:
        base = f"http://{listen_host()}:{PORT}"
    except Exception:
        return "(the homelab's NUauto page: web_listen_host is not set)"
    return base + (f"/#/assistant/{slot}" if slot else "/")


def _log(msg):
    """logs/review.log: pairings and sessions started from the page (each session's own answers go in its actions.log)."""
    os.makedirs(config.LOGS_DIR, exist_ok=True)
    with open(os.path.join(config.LOGS_DIR, "review.log"), "a") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}\n")


def _hash(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


@contextmanager
def _locked(path):
    """A JSON object file (mode 600), read and written under its own lock. Yields the dict."""
    import fcntl
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    with open(path + ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        before = json.dumps(data, sort_keys=True)
        yield data
        if json.dumps(data, sort_keys=True) != before:
            from nuauto import assist
            assist.write_private(path, data)


# ---------- devices ----------

def make_pairing(name="phone"):
    """A one-time pairing link (PAIR_TTL)."""
    code = secrets.token_urlsafe(18)
    now = time.time()
    with _locked(PAIRING) as p:
        for k in [k for k, v in p.items() if v.get("until", 0) < now]:
            del p[k]
        p[_hash(code)] = {"name": name[:40], "until": now + PAIR_TTL}
    return f"http://{listen_host()}:{PORT}/pair?c={code}"


def pair(code, name=""):
    """Use up a pairing code: a new device key (for its cookie), or None (wrong, used or expired)."""
    with _locked(PAIRING) as p:
        entry = p.pop(_hash(code or ""), None)
    if not entry or entry.get("until", 0) < time.time():
        return None
    key = secrets.token_urlsafe(32)
    with _locked(DEVICES) as d:
        d[_hash(key)] = {"name": (name.strip() or entry["name"])[:40], "paired": datetime.now().isoformat(timespec="minutes")}
    _log(f"paired a device: {(name.strip() or entry['name'])[:40]}")
    return key


def device(key):
    """The paired device with this key ({"name", "paired", "id"}), or None."""
    if not key:
        return None
    try:
        with open(DEVICES) as f:
            found = json.load(f).get(_hash(key))
    except (OSError, ValueError):
        return None
    return {**found, "id": device_id(_hash(key))} if found else None


def device_id(key_hash):
    """A short, stable name for one paired device in the app's list (part of its key's hash: never the key)."""
    return key_hash[:12]


def devices():
    """The paired devices, oldest first: [{"id", "name", "paired"}]."""
    try:
        with open(DEVICES) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    return sorted(({"id": device_id(h), "name": d.get("name", ""), "paired": d.get("paired", "")}
                   for h, d in data.items()), key=lambda d: d["paired"])


def unpair(which):
    """Forget devices: by id (devices()), by name, or "--all". Their cookies stop working. Returns how many."""
    with _locked(DEVICES) as d:
        gone = [h for h, v in d.items() if which == "--all" or which in (v.get("name"), device_id(h))]
        for h in gone:
            del d[h]
    if gone:
        _log(f"unpaired {len(gone)} device(s): {which}")
    return len(gone)


# ---------- sessions ----------

def session(slot):
    """What the page shows for the session in this slot, or None. Only run folders under logs/."""
    from nuauto import assist
    logs = os.path.realpath(config.LOGS_DIR)
    for s in assist.sessions():
        d = os.path.realpath(s.get("run") or "")
        if s.get("slot") != slot or not d.startswith(logs + os.sep) or not os.path.isdir(d):
            continue
        st = assist.read_json(os.path.join(d, "state.json"), {})
        try:
            with open(os.path.join(d, assist.MESSAGE), encoding="utf-8", errors="replace") as f:
                message = f.read()
        except OSError:
            message = ""
        return {"slot": slot, "run": d, "state": st, "info": s, "message": message,
                "attention": assist.read_json(os.path.join(d, assist.ATTENTION)) or {},
                "approval": assist.read_json(os.path.join(d, assist.APPROVAL)),
                "view": assist.read_json(os.path.join(d, "view.json")) or {}}
    return None


def sessions():
    from nuauto import assist
    return [x for x in (session(s.get("slot")) for s in assist.sessions()) if x]


def status(s):
    """One word for the session's state, and what it wants from you."""
    a, att = s["approval"], s["attention"].get("kind")
    if att == "finish":
        return "finish", "The session ended: did it go through?"
    if att == "answered":  # your Yes / No is typed in; the launcher is marking the sheet and closing the run
        return "answered", ("You said it went through: marking it Applied" if s["attention"].get("answer") == "y"
                            else "You said it didn't go through: leaving the row as it was")
    if a:
        return "approve", f"Ready to submit: {a.get('what', '')}"
    if att == "approve":  # Claude Code's own permission prompt (its Notification hook), not a Submit of the guard's
        return "prompt", "Claude Code asks something in its terminal: answer it there"
    if att == "waiting":
        return "waiting", "Waiting for your answer"
    return "working", "Working"


def version(s):
    """Changes whenever what the page shows changes (the page polls it and reloads)."""
    a = s["approval"] or {}
    return hashlib.sha256(json.dumps([s["attention"], a.get("id"), a.get("waiting"), len(s["message"]),
                                      s["message"][-200:]], sort_keys=True).encode()).hexdigest()[:16]


def _tmux(*args, input_=None):
    return subprocess.run(["tmux", *args], input=input_, capture_output=True, text=True, timeout=10)


SHELLS = {"bash", "zsh", "sh", "dash", "fish", "ksh", "tcsh", "csh", "-bash", "-zsh", "-sh"}


def target(s):
    """The tmux pane the session runs in (recorded at its start), else its tmux session's active pane."""
    pane = s["state"].get("tmux_pane")
    if pane and re.fullmatch(r"%\d+", pane):
        return pane
    name = s["state"].get("tmux")
    if not name or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise ValueError("this session is not in a tmux session (start it as docs/REMOTE.md says)")
    return f"={name}:"


def type_into(s, text):
    """Type text into the session's terminal and press Enter (a newline between lines: Ctrl+J, Claude Code's own).
    Never into a shell: if the pane shows one (the assistant ended), nothing is typed."""
    t = target(s)
    r = _tmux("display-message", "-p", "-t", t, "#{pane_dead} #{pane_current_command}")
    dead, _, cmd = (r.stdout.strip() if r.returncode == 0 else "1 ").partition(" ")
    if dead == "1" or not cmd or cmd in SHELLS:
        raise ValueError(f"its terminal shows {cmd or 'nothing'} now, not the assistant: nothing was typed")
    lines = text.replace("\r", "").split("\n")
    for i, line in enumerate(lines):
        if line:
            r = _tmux("send-keys", "-t", t, "-l", "--", line)
            if r.returncode:
                raise ValueError(f"tmux: {r.stderr.strip()[:200]}")
        if i < len(lines) - 1:
            _tmux("send-keys", "-t", t, "C-j")
    # A long reply arrives as fast as a paste, and Claude Code then takes an Enter right after it as part of the paste
    # (a new line, not "send"): let it land first, and press Enter again while the text still sits in its input box.
    time.sleep(min(1.5, 0.3 + len(text) / 1500))
    snippet = next((x.strip() for x in lines if x.strip()), "")[:30]
    for _ in range(3):
        _tmux("send-keys", "-t", t, "Enter")
        time.sleep(0.8)
        if not snippet or not still_typed(s, snippet):
            return


def still_typed(s, snippet):
    """True if Claude Code's input box (from its last ❯ line down to the rule under it) still holds this text."""
    rows = terminal(s).splitlines()[-25:]
    at = max((i for i, x in enumerate(rows) if x.lstrip().startswith("❯")), default=None)
    if at is None:
        return False  # not Claude Code's prompt (e.g. the launcher's "Did you press Submit?"): an Enter there answers it
    box = []
    for x in rows[at:]:
        if box and x.strip().startswith("─"):
            break
        box.append(x)
    return snippet in " ".join(" ".join(box).split())


ENDED = ("Did you press Submit", "left for the laptop", "Not submitted")  # what its terminal shows once Claude Code ended


def end_session(s, wait=4.0):
    """/exit in its terminal: Claude Code's command menu can take the first Enter (it picks the command), so a pause
    before Enter, and one more Enter if the session has not ended a few seconds later."""
    t = target(s)
    r = _tmux("display-message", "-p", "-t", t, "#{pane_dead} #{pane_current_command}")
    dead, _, cmd = (r.stdout.strip() if r.returncode == 0 else "1 ").partition(" ")
    if dead == "1" or not cmd or cmd in SHELLS:
        raise ValueError(f"its terminal shows {cmd or 'nothing'} now, not the assistant: nothing was typed")
    _tmux("send-keys", "-t", t, "-l", "--", "/exit")
    time.sleep(0.7)
    _tmux("send-keys", "-t", t, "Enter")
    deadline = time.time() + wait
    while time.time() < deadline:
        time.sleep(0.5)
        if any(m in terminal(s) for m in ENDED):
            return
    _tmux("send-keys", "-t", t, "Enter")  # (harmless if it ended meanwhile: "Did you press Submit?" just asks again)


def terminal(s):
    """The terminal's last lines, with this run's site passwords taken out."""
    from nuauto import accounts
    try:
        r = _tmux("capture-pane", "-p", "-J", "-t", target(s), "-S", f"-{TERMINAL_LINES}")
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return ""
    text = r.stdout if r.returncode == 0 else ""
    from nuauto import assist
    for p in accounts.passwords(s["state"].get("password_sites") or []):
        text = text.replace(p, assist.HIDDEN)
    return text.rstrip()


def answered(s, answer):
    """Your answer to "Did it go through?" is typed in: the page says so (not the question again) until the run ends."""
    from nuauto import assist
    assist.write_private(os.path.join(s["run"], assist.ATTENTION), {"kind": "answered", "answer": answer,
                                                                      "since": time.time(), "said": ""})


def st_company(s):
    return s["state"].get("company") or "the job"


def mark_laptop(s, by):
    """You chose "do it on the laptop": the run won't ask "Did it go through?" (assist.LAPTOP_FILE), and the sheet row
    gets the mark the homelab's queue keeps off (sheet.mark_on_laptop). Returns what to tell you."""
    from nuauto import assist
    from nuauto import sheet
    st = s["state"]
    assist.write_private(os.path.join(s["run"], assist.LAPTOP_FILE), {"by": by, "at": time.time()})
    assist.log(s["run"], f"REVIEW PAGE: do it on the laptop (by {by})")
    try:
        ws = sheet.open_other(interactive=False) if st.get("tab") == "other" else sheet.open_worksheet(interactive=False)
        number = s["info"].get("row")
        row = next((r for r in sheet.read_rows(ws) if r.number == number), None)
        if row is None or (st.get("row_url") and row.url != st["row_url"]) or \
                (not st.get("row_url") and row.company != st.get("company")):
            return "Left for the laptop, but its sheet row has changed: mark it yourself."
        sheet.mark_on_laptop(ws, number, row.url, "the site showed it a sign-in page with no way in")
    except Exception as e:  # the queue still knows (its record): never again by itself
        return f"Left for the laptop; the sheet could not be marked ({type(e).__name__})."
    return "Left for the laptop: marked in your sheet, and the homelab won't start it again."


def hook_alive(pid):
    """True while the guard's hook that asked is still waiting (that process, still the hook). No pid: assume so."""
    if not pid:
        return True
    if os.path.isdir("/proc/self"):  # Linux (the homelab): that process, and still the hook
        try:
            with open(f"/proc/{int(pid)}/cmdline", "rb") as f:
                argv = f.read().split(b"\0")
            return b"hook" in argv and b"pre" in argv
        except OSError:
            return False
    try:  # no /proc (macOS): only whether the process is there
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return True


def answer_approval(s, approval_id, decision, by, note=""):
    """Your Approve / Decline. The guard still waiting: it gets the answer. It stopped waiting: Approve becomes a pass for
    that one action and the agent is told to do it; Decline tells it not to. Returns what happened, for the page."""
    from nuauto import assist
    d = s["run"]
    note = " ".join(note.split())[:300]
    with assist.approval_lock(d):
        a = assist.read_json(os.path.join(d, assist.APPROVAL))
        if not a or a.get("id") != approval_id:
            return "That question is gone (answered, or a newer one replaced it)."
        if a.get("waiting") and hook_alive(a.get("pid")):
            assist.write_private(os.path.join(d, assist.DECISION), {"id": a["id"], "decision": decision, "by": by,
                                                                   "note": note, "at": time.time()})
            message = None
        else:
            assist.remove(d, assist.APPROVAL, assist.SHOT)
            if decision == "approve":
                assist.write_private(os.path.join(d, assist.GRANT), {"what": a["what"], "url": a["url"], "id": a["id"],
                                                                     "fp": a.get("fp"), "by": by,
                                                                     "until": time.time() + assist.GRANT_TTL})
                message = (f"The user approved on the review page: {a['what']} on {a['url']}. Take a new snapshot, check "
                           "you are still on that page, then do exactly that action now.")
            else:
                message = ("The user declined the submit on the review page" + (f": {note}" if note else "") +
                           ". Don't submit. Ask them what to change.")
    assist.log(d, f"REVIEW PAGE {decision} by {by}: {a['what']}" + (f" ({note})" if note else ""))
    if message:
        type_into(s, message)
    return "Approved." if decision == "approve" else "Declined."


# ---------- what the app shows (gui.py serves it: the Assistants screen, Company sites' queue) ----------

def read_answer(s):
    """Your answer to its open Submit question, before the guard has picked it up (decision.json), or None."""
    from nuauto import assist
    return assist.read_json(os.path.join(s["run"], assist.DECISION))


def summary(s):
    """One running session, for the lists (Company sites, Today)."""
    word, text = status(s)
    st, info = s["state"], s["info"]
    return {"slot": s["slot"], "tab": info.get("tab"), "row": info.get("row"),
            "company": st.get("company") or info.get("company") or "", "title": st.get("job_title") or "",
            "where": st.get("where") or "", "host": info.get("host") or "", "since": info.get("since"),
            "status": word, "text": text}


def view(s):
    """Everything the Assistants screen shows for one session (it asks again every few seconds): what it wants from you
    (a Submit to approve with the list of what you haven't confirmed, its last message, the end question), the browser
    view's link and password, its terminal's last lines."""
    from nuauto import assist
    word, _ = status(s)
    a = s["approval"]
    out = {**summary(s), "message": s["message"] if word not in ("finish", "answered") else "",
           "terminal": terminal(s), "tmux": s["state"].get("tmux") or "", "version": version(s), "approval": None,
           "browser": ({"url": s["view"]["url"], "password": s["view"].get("password") or ""}
                       if s["view"].get("url") else None)}
    if a:
        dec = read_answer(s) or {}
        mine = a.get("waiting") and dec.get("id") == a.get("id")  # answered; the guard has not picked it up yet
        out["approval"] = {"id": a.get("id") or "", "what": a.get("what") or "", "url": a.get("url") or "",
                           "why": a.get("why") or "", "waiting": bool(a.get("waiting")),
                           "shot": os.path.exists(os.path.join(s["run"], assist.SHOT)),
                           "answered": dec.get("decision") if mine else None, "by": dec.get("by") if mine else None}
    return out


def shot(s, live=False):
    """The screen when it asked (its Submit question), else the screen now (live: always now). PNG bytes or None."""
    from nuauto import assist
    from nuauto import screen
    if not live:
        try:
            with open(os.path.join(s["run"], assist.SHOT), "rb") as f:
                return f.read()
        except OSError:
            pass
    return screen.screenshot(s["run"])


def act(s, body, by):
    """One tap on the Assistants screen (by: the paired device's name, for the run's actions.log). Returns {"message"},
    with "closing": True when the session is ending (the screen goes back to the list)."""
    from nuauto import assist
    do, word = body.get("do"), status(s)[0]
    kind = s["attention"].get("kind")
    busy = "Not sent: Claude Code is asking something in its terminal (answer it there)."
    try:
        if do in ("approve", "decline"):
            return {"message": answer_approval(s, str(body.get("id") or ""), do, by, str(body.get("note") or ""))}
        if do == "reply":
            text = str(body.get("text") or "")
            if word == "prompt":
                return {"message": busy}
            if not text.strip():
                return {"message": "Nothing to send."}
            type_into(s, text[:MAX_REPLY])
            assist.log(s["run"], f"REVIEW PAGE reply by {by} ({len(text)} characters)")
            return {"message": "Sent."}
        if do == "interrupt":
            _tmux("send-keys", "-t", target(s), "Escape")
            return {"message": "Interrupted."}
        if do == "exit":
            if word == "prompt":
                return {"message": busy}
            end_session(s)
            assist.log(s["run"], f"REVIEW PAGE ended the session (by {by})")
            return {"message": "Ending the session."}
        if do == "laptop" and word != "prompt" and kind != "finish":  # End: do it on the laptop
            message = mark_laptop(s, by)
            end_session(s)
            return {"message": message}
        if do == "finish" and kind == "answered":
            return {"message": "Already answered: its session is closing.", "closing": True}
        if do == "finish" and kind == "finish" and body.get("answer") == "laptop":
            message = mark_laptop(s, by)
            type_into(s, "n")
            answered(s, "n")
            return {"message": message, "closing": True}
        if do == "finish" and kind == "finish" and body.get("answer") in ("y", "n"):
            a = body["answer"]
            type_into(s, a)
            answered(s, a)
            assist.log(s["run"], f"REVIEW PAGE did it go through: {a} (by {by})")
            return {"message": (f"Thanks: {st_company(s)} is marked Applied." if a == "y" else
                                f"{st_company(s)}: left as it was."), "closing": True}
    except (ValueError, OSError, subprocess.TimeoutExpired) as e:
        return {"message": f"Could not do that: {e}"}
    return {"message": "Nothing to do."}


# the queue's groups, in this order (a row's group: queue_state)
GROUPS = (("running", "Working on it now", ""), ("next", "Starts next by itself", ""),
          ("ready", "Ready: start it yourself", ""), ("wait", "Waiting", "a slot, its job site or the weekly cap"),
          ("check", "Check first", "a Submit was approved and nobody said whether it went through"),
          ("laptop", "Do it on the laptop", "the homelab's browser was refused there"))


def queue_state(c, inputs, by_itself, here, tabs):
    """(group, what to say, whether you can start it now) for one queue row: judged on its own (a free slot, its job site
    free, the caps, its records, the laptop mark), not behind the rows ahead of it."""
    from nuauto import remote
    key = (c["tab"], c["row"])
    why = remote.can_start(c, inputs)
    if here.get(key):
        return "running", "a session is working on it", False
    if why == "a session is on it":
        return "running", "the laptop's assistant is working on it", False
    if why is None:
        first = by_itself.get(key)
        if c["tab"] in tabs and first is None:
            return "next", "starts next by itself", True
        return "ready", ("had a session already: start it yourself" if first and first.startswith("had a session")
                         else "ready to start"), True
    return ("laptop" if why == remote.LAPTOP_WHY else "check" if why == remote.OUTCOME_UNKNOWN else "wait"), why, False


def queue_view():
    """The homelab's queue (remote.py) for the app's Company sites: per row ("jobs-12", "other-3") its group, what it waits
    for and whether you can start it now; which tabs start by themselves, the hours, free slots, NUworks applications
    left. One read of the sheet."""
    from nuauto import assist
    from nuauto import remote
    inputs = remote.queue_inputs()
    tabs = remote.auto_tabs()
    go_auto, rest_auto = remote.plan(*inputs, tabs=tabs)  # what the timer would start (it hands the free slots out in order)
    by_itself = {(c["tab"], c["row"]): why for c, why in go_auto + rest_auto}
    here = {(x.get("tab"), x.get("row")): x.get("slot") for x in assist.sessions()}
    rows = {}
    for n, c in enumerate(inputs[0]):
        group, text, can = queue_state(c, inputs, by_itself, here, tabs)
        rows[f"{c['tab']}-{c['row']}"] = {"group": group, "text": text, "start": can, "order": n,
                                          "slot": here.get((c["tab"], c["row"]))}
    a, b = remote.hours()
    return {"rows": rows, "groups": [{"key": k, "title": t, "what": w} for k, t, w in GROUPS],
            "auto": sorted(tabs), "hours": [a, b], "free": inputs[4], "left": inputs[5]}


def start_now(f, by):
    """Start now: one row you can start right now (remote.can_start: a free slot, its job site free, the caps, its
    records, the laptop mark; the start-once rule doesn't apply to you). Returns (started, what to tell you)."""
    from nuauto import remote
    tab, row = f.get("tab"), str(f.get("row", ""))
    if tab not in ("jobs", "other") or not row.isdigit():
        return False, "Nothing to do."
    inputs = remote.queue_inputs()
    c = next((x for x in inputs[0] if x["tab"] == tab and x["row"] == int(row)), None)
    if not c:
        return False, "Not started: it is not in the queue (not Approved, or not a company-site application)."
    why = remote.can_start(c, inputs)
    if why:
        return False, f"Not started: {why}."
    why = remote.start(c["tab"], c["row"])
    if why:
        return False, f"Not started: {why}"
    remote.record_started(c, auto=False)
    _log(f"{by} started {c['tab']} row {c['row']} ({c['company']})")
    return True, f"Started {c['company']}: it shows up here in a few seconds."


def pair_link_from_homelab(name):
    """(laptop) A one-time pairing link made on the homelab (`nuauto review pair` there over ssh), or None."""
    from nuauto import sync
    cmd = sync.SSH + [config.SERVER, f"cd {shlex.quote(config.SERVER_DIR)} && .venv/bin/nuauto review pair "
                      + shlex.quote(name)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    links = re.findall(r"https?://\S+/pair\?c=[A-Za-z0-9_-]+", r.stdout)
    return links[-1] if r.returncode == 0 and links else None


# ---------- demo: made-up sessions on the demo sheet's rows (`nuauto gui --demo --remote`; nothing real) ----------

DEMO_SESSIONS = [
    {"company": "Acme Robotics", "job_title": "Embedded Software Intern", "where": "Other jobs row 12",
     "approval": {"what": "click 'Submit Application'", "url": "https://acme.wd1.myworkdayjobs.com/en-US/careers/job/apply",
                  "why": "clicking 'Submit Application' may SUBMIT the application. Approve only after the user has "
                         "reviewed every page.\nNOT CONFIRMED BY YOU (the agent's guesses: say No and ask it for its list):"
                         "\n  - Earliest start date = 'May 2027' (the posting's term)"},
     "message": "Everything is filled in. It is ready for your review on the last page (Review)."},
    {"company": "Beacon Systems", "job_title": "Hardware Test Intern", "where": "Other jobs row 15",
     "message": "Please confirm these guesses (reply with changes, or OK):\n1. Graduation date = May 2029 (resume)\n"
                "2. Willing to relocate = Yes (no info)\n3. Essay \"Why Beacon?\" (draft, 120 words):\n   I have built ..."},
    {"company": "Cobalt Labs", "job_title": "Security Engineering Co-op", "where": "NUworks row", "finish": True},
]
DEMO_RECORDS = {"Iris Security": {"auto": True},                                    # had a session: yours to start
                "Maple Grid": {"auto": True, "approved": True, "answer": None}}     # a Submit approved, outcome not given


def demo_setup():
    """(demo only: `nuauto gui --demo --remote`, with demo.py's homelab rows) DEMO_SESSIONS as running sessions on their
    companies' rows (their slot locks held by this process: keep the returned files open; three sessions fill every
    slot, so the queue's rows wait) and DEMO_RECORDS as the queue's records. The queue itself is the real one, on the
    demo sheet. Starting a session changes nothing; there is no tmux, so Reply and End say they could not."""
    import fcntl
    from nuauto import assist
    from nuauto import remote
    from nuauto import sheet
    assert config.DEMO, "demo only"
    rows = {r.company: ("jobs", r) for r in sheet.read_rows(sheet.open_worksheet(interactive=False))}
    rows.update({r.company: ("other", r) for r in sheet.read_rows(sheet.open_other(interactive=False))})
    locks = []
    for n, x in enumerate(DEMO_SESSIONS, 1):
        tab, r = rows[x["company"]]
        d = os.path.join(config.LOGS_DIR, f"demo_assist_{n}")
        os.makedirs(d, mode=0o700, exist_ok=True)
        where = f"{'Other jobs row' if tab == 'other' else 'row'} {r.number}"
        assist.write_private(os.path.join(d, "state.json"), {"company": x["company"], "job_title": x["job_title"],
                                                             "where": where, "slot": n, "tmux": None, "tab": tab})
        if x.get("message"):
            assist.write_private(os.path.join(d, assist.MESSAGE), x["message"].encode())
        if x.get("approval"):
            assist.write_private(os.path.join(d, assist.APPROVAL), {**x["approval"], "id": f"demo{n}", "waiting": True,
                                                                     "since": time.time()})
        kind = "finish" if x.get("finish") else "approve" if x.get("approval") else "waiting"
        assist.write_private(os.path.join(d, assist.ATTENTION), {"kind": kind, "since": time.time(), "said": ""})
        f = open(os.path.join(config.LOCAL_DIR, f"assist_slot{n}.lock"), "a+")
        fcntl.flock(f, fcntl.LOCK_EX)
        f.truncate(0)
        host = assist.site_key(assist.assist_target(r.notes)[0] if tab == "jobs" else r.url)
        f.write(json.dumps({"slot": n, "row": r.number, "tab": tab, "company": x["company"], "host": host,
                            "run": d, "since": "10:00"}))
        f.flush()
        locks.append(f)
    records = {}
    for company, rec in DEMO_RECORDS.items():
        tab, r = rows[company]
        records[f"{tab}-{r.number}"] = {**rec, "url": r.url, "company": company}
    assist.write_private(remote.AUTO_PATH, records)
    remote.start = lambda tab, row: "this is the demo: nothing starts"
    return locks


# ---------- command line ----------

def main(argv):
    if argv[:1] == ["demo"]:  # anywhere, nothing real: the homelab's app with made-up sessions
        from nuauto import gui
        port = ["--port", argv[1]] if len(argv) > 1 and argv[1].isdigit() else []
        return gui.main(["--demo", "--remote", "--no-open", *port])
    if not config.IS_SERVER:  # the laptop: the same command on the homelab (the devices live there)
        if not config.HAS_SERVER:
            sys.exit("NUauto's page for your phone runs on a homelab (docs/REMOTE.md).")
        from nuauto import sync
        sys.exit(sync.ssh(f"cd {shlex.quote(config.SERVER_DIR)} && .venv/bin/nuauto review "
                          + " ".join(shlex.quote(a) for a in argv)))
    if not config.REMOTE_APP:
        sys.exit("Turn on NUauto on your homelab first: the laptop's Settings > NUauto on your homelab (\"remote_app\" in "
                 "local_config.json; docs/REMOTE.md).")
    if argv[:1] == ["pair"]:
        rest = [a for a in argv[1:] if a != "--discord"]
        link = make_pairing(rest[0] if rest else "phone")
        print(f"Open this on the device to pair (once, within {PAIR_TTL // 60} minutes; Tailscale on):\n  {link}")
        if "--discord" in argv:
            from nuauto import daily
            daily.discord(f"**NUauto: pair a device**\nCopy this into your phone's browser (Safari, Chrome) rather than "
                          "tapping it: Discord's own browser keeps its own cookies. Once, within "
                          f"{PAIR_TTL // 60} minutes, then bookmark the page there: {link}")
        return
    if argv == ["devices"]:
        devs = devices()
        if not devs:
            return print("No paired devices. `nuauto review pair` makes a link.")
        for d in devs:
            print(f"{d['name']:20} paired {d['paired']}")
        return
    if argv[:1] == ["unpair"] and len(argv) == 2:
        n = unpair(argv[1])
        return print(f"Forgot {n} device{'s' if n != 1 else ''}.")
    sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
