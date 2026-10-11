"""The review page: the assistant on the homelab, from your phone (Phase 3b; docs/REMOTE.md).

Runs inside `nuauto web` (a second listener, only with "remote_assist") on web_listen_host:8766, the homelab's
Tailscale IP: only your own devices reach it, and never through the public Mark-done tunnel (that one forwards 8765).
Each running assistant session (assist.sessions) has a page, /s/<slot>:
  - a Submit waiting for you: what it will click and on which page, the guesses you haven't confirmed, what you
    changed, anything typed with no answer behind it, the screen when it asked; Approve / Decline (with a note).
    The guard waits up to assist.WAIT for you; a later Approve is a pass for that one action (assist.take_grant), and
    the page tells the agent to do it now.
  - waiting for you (a question, the end list, a captcha): its whole message and a reply box. Your reply is typed into
    its terminal (tmux), as if you typed it there. The browser view's link and password are on the page: a captcha you
    solve there yourself, then reply "done".
  - the session ended: "Did it go through?" Yes (Applied) / No, typed into its terminal's question.
  - always: the screen now, its terminal's last lines, Interrupt (Esc), End session (/exit).
Only paired devices. `nuauto review pair` makes a one-time link (10 minutes); open it on your phone, press Pair: a long
random key goes in a cookie there (HttpOnly, SameSite=Strict) and the homelab keeps only its hash
(local/review_devices.json). Every request needs that cookie and this server's own Host; every POST also a form token
made from the key, and a matching Origin when the browser sends one. Discord links only point here: no power by
themselves. Every answer goes in the run's actions.log with the device's name.

  nuauto review pair [<name>] [--discord]   a pairing link (printed; --discord also posts it)
  nuauto review devices                     the paired devices
  nuauto review unpair <name>|--all         forget a device (its cookie stops working)
  nuauto review demo [<port>]               the page with made-up sessions, on this computer (prints a pairing link)
On the laptop these run on the homelab over ssh (all but demo).
"""
import hashlib
import hmac
import html
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

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
    try:
        base = f"http://{listen_host()}:{PORT}"
    except Exception:
        return "(the review page: web_listen_host is not set)"
    return base + (f"/s/{slot}" if slot else "/")


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
    return key


def device(key):
    """The paired device with this key ({"name", "paired"}), or None."""
    if not key:
        return None
    try:
        with open(DEVICES) as f:
            return json.load(f).get(_hash(key))
    except (OSError, ValueError):
        return None


def form_token(key):
    return hmac.new(key.encode(), b"nuauto-review-form", hashlib.sha256).hexdigest()[:32]


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


def target(s):
    name = s["state"].get("tmux")
    if not name or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise ValueError("this session is not in a tmux session (start it as docs/REMOTE.md says)")
    return f"={name}:"


def type_into(s, text):
    """Type text into the session's terminal and press Enter (a newline between lines: Ctrl+J, Claude Code's own)."""
    t = target(s)
    lines = text.replace("\r", "").split("\n")
    for i, line in enumerate(lines):
        if line:
            r = _tmux("send-keys", "-t", t, "-l", "--", line)
            if r.returncode:
                raise ValueError(f"tmux: {r.stderr.strip()[:200]}")
        if i < len(lines) - 1:
            _tmux("send-keys", "-t", t, "C-j")
    _tmux("send-keys", "-t", t, "Enter")


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
        if a.get("waiting"):
            assist.write_private(os.path.join(d, assist.DECISION), {"id": a["id"], "decision": decision, "by": by,
                                                                   "note": note, "at": time.time()})
            message = None
        else:
            assist.remove(d, assist.APPROVAL, assist.SHOT)
            if decision == "approve":
                assist.write_private(os.path.join(d, assist.GRANT), {"what": a["what"], "url": a["url"], "id": a["id"],
                                                                     "by": by, "until": time.time() + assist.GRANT_TTL})
                message = (f"The user approved on the review page: {a['what']} on {a['url']}. Take a new snapshot, check "
                           "you are still on that page, then do exactly that action now.")
            else:
                message = ("The user declined the submit on the review page" + (f": {note}" if note else "") +
                           ". Don't submit. Ask them what to change.")
    assist.log(d, f"REVIEW PAGE {decision} by {by}: {a['what']}" + (f" ({note})" if note else ""))
    if message:
        type_into(s, message)
    return "Approved." if decision == "approve" else "Declined."


# ---------- the pages ----------

CSS = """body{font:16px/1.45 system-ui,sans-serif;max-width:46rem;margin:0 auto;padding:1rem;color:#1d1d1f;background:#fafafa}
a{color:#0b5cad}.card{background:#fff;border:1px solid #ddd;border-radius:.6rem;padding:.8rem 1rem;margin:.8rem 0}
.chip{display:inline-block;padding:.1rem .5rem;border-radius:1rem;font-size:.8rem;background:#eee}
.approve,.prompt{background:#fde2e1}.waiting{background:#fff1c2}.finish{background:#dff3e3}
button{font:inherit;padding:.6rem 1.1rem;border:0;border-radius:.5rem;background:#0b5cad;color:#fff;margin:.2rem .3rem .2rem 0}
button.no{background:#777}button.go{background:#1a7f37}textarea,input[type=text]{font:inherit;width:100%;box-sizing:border-box;padding:.5rem}
pre{white-space:pre-wrap;word-break:break-word;background:#f3f3f3;padding:.6rem;border-radius:.4rem;font-size:.85rem}
img{max-width:100%;border:1px solid #ccc;border-radius:.3rem}.dim{color:#777;font-size:.9rem}
@media (prefers-color-scheme:dark){body{background:#151515;color:#eee}.card{background:#1f1f1f;border-color:#333}
pre{background:#262626}.chip{background:#333}.approve{background:#5a1f1d}.waiting{background:#4d3f0d}.finish{background:#1e4227}a{color:#7ab4ff}}"""

POLL_JS = """const v = document.body.dataset.v, u = document.body.dataset.poll;
if (u) setInterval(async () => {
  if ([...document.querySelectorAll("textarea,input[type=text]")].some(e => e.value)) return;
  try { const r = await fetch(u, {cache: "no-store"}); if (r.ok && (await r.text()).trim() !== v) location.reload(); } catch (e) {}
}, 5000);"""


def esc(x):
    return html.escape(str(x or ""))


def layout(title, body, v="", poll=""):
    return (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<meta name=robots content=noindex><title>{esc(title)}</title><style>{CSS}</style>"
            f"<body data-v='{esc(v)}' data-poll='{esc(poll)}'>{body}<script src='/poll.js'></script></body>")


def form(action, token, inner, **hidden):
    fields = "".join(f"<input type=hidden name={esc(k)} value='{esc(v)}'>" for k, v in {"t": token, **hidden}.items())
    return f"<form method=post action='{esc(action)}'>{fields}{inner}</form>"


def queue_part(token):
    """The queue (remote.py): what starts by itself next, and why the others wait; Start now for a row that waits for
    you (it still needs a free slot and its job site free)."""
    from nuauto import remote
    try:
        go, rest = remote.queue_now()
    except Exception as e:
        return f"<p class=dim>Queue not readable now ({esc(type(e).__name__)}).</p>"
    a, b = remote.hours()
    auto = "on" if config.LOCAL.get("remote_auto") else "off (\"remote_auto\" in local_config.json)"
    lines = []
    for c, why in (go + rest)[:12]:
        startable = why is None or why.startswith("started by itself once")
        button = form("/start", token, "<button>Start now</button>", tab=c["tab"], row=c["row"]) if startable else ""
        lines.append(f"<div class=card><b>{esc(c['company'])}</b> <span class=dim>{esc(c['title'])} | "
                     f"{'Other jobs' if c['tab'] == 'other' else 'NUworks'} row {c['row']} | {esc(c['host'])}</span>"
                     f"<div class=dim>{esc(why or 'starts next')}</div>{button}</div>")
    return (f"<h3>Queue</h3><p class=dim>Starts by itself: {esc(auto)}, {a}:00-{b}:00 New York time.</p>"
            + ("".join(lines) or "<p class=dim>Nothing approved is waiting.</p>"))


def index_page(token):
    items = []
    for s in sessions():
        word, text = status(s)
        st = s["state"]
        items.append(f"<div class=card><a href='/s/{s['slot']}'><b>{esc(st.get('company'))}</b></a> "
                     f"<span class='chip {word}'>{esc(text)}</span><div class=dim>{esc(st.get('job_title'))} | "
                     f"{esc(st.get('where'))} | slot {s['slot']}</div></div>")
    body = "<h2>NUauto assistants</h2>" + ("".join(items) or "<p class=dim>No assistant is running on the homelab.</p>")
    return layout("NUauto assistants", body + queue_part(token))


def session_page(s, token, flash=""):
    st, a, word = s["state"], s["approval"], status(s)[0]
    slot = s["slot"]
    parts = [f"<p><a href='/'>&larr; all</a></p><h2>{esc(st.get('company'))}</h2>"
             f"<div class=dim>{esc(st.get('job_title'))} | {esc(st.get('where'))} | slot {slot}</div>"]
    if flash:
        parts.append(f"<div class=card><b>{esc(flash)}</b></div>")
    if word == "finish":
        parts.append("<div class='card finish'><b>The session ended. Did you approve Submit and did the site confirm "
                     "the application?</b><p class=dim>Yes marks the row Applied (dated today).</p>"
                     + form(f"/s/{slot}/finish", token, "<button class=go name=a value=y>Yes, it went through</button>"
                            "<button class=no name=a value=n>No</button>") + "</div>")
    if a:
        late = "" if a.get("waiting") else ("<p class=dim>The assistant stopped waiting for this; Approve tells it to "
                                            "do it now (good for 10 minutes, this action on this page only).</p>")
        parts.append(f"<div class='card approve'><b>Ready to submit</b><p>It wants to <b>{esc(a.get('what'))}</b><br>"
                     f"<span class=dim>on {esc(a.get('url'))}</span></p><pre>{esc(a.get('why'))}</pre>"
                     + (f"<img src='/s/{slot}/shot.png?a={esc(a['id'])}' alt='the screen when it asked'>"
                        if os.path.exists(os.path.join(s["run"], "approval.png")) else "")
                     + late + form(f"/s/{slot}/approve", token,
                                   "<button class=go name=d value=approve>Approve: submit</button>"
                                   "<input type=text name=note placeholder='Note for the agent (when you decline)'>"
                                   "<button class=no name=d value=decline>Decline</button>", id=a["id"]) + "</div>")
    if word == "prompt":
        parts.append("<div class='card approve'><b>Claude Code is asking something in its terminal</b> (a permission "
                     "question, not a Submit of the guard's). The reply box is off until it is answered, because Enter "
                     "would pick its first option. Answer it in the terminal: "
                     f"<code>ssh -t homelab tmux attach -t {esc(st.get('tmux') or '?')}</code></div>")
    if s["message"] and word != "finish":
        parts.append(f"<div class='card {'waiting' if word == 'waiting' else ''}'><b>Its last message</b>"
                     f"<pre>{esc(s['message'])}</pre></div>")
    if word not in ("finish", "prompt"):
        parts.append("<div class=card><b>Reply</b> <span class=dim>(typed into its terminal)</span>"
                     + form(f"/s/{slot}/reply", token, "<textarea name=text rows=4 maxlength=4000></textarea>"
                            "<button>Send</button>") + "</div>")
    view = s["view"]
    if view.get("url"):
        parts.append(f"<div class=card><b>The browser</b> <span class=dim>(captchas: you solve them here, then reply "
                     f"\"done\")</span><p><a href='{esc(view['url'])}' target=_blank rel=noopener>Open it</a> &middot; "
                     f"password <code>{esc(view.get('password'))}</code></p>"
                     f"<img src='/s/{slot}/shot.png?live={int(time.time())}' alt='the screen now'></div>")
    parts.append(f"<div class=card><b>Its terminal</b><pre>{esc(terminal(s)) or '(not readable)'}</pre>"
                 + form(f"/s/{slot}/key", token, "<button class=no name=k value=interrupt>Interrupt (Esc)</button>"
                        "<button class=no name=k value=exit>End session (/exit)</button>") + "</div>")
    return layout(f"NUauto: {st.get('company') or 'assistant'}", "".join(parts), version(s), f"/s/{slot}/v")


def pair_page(code, done=None):
    if done is True:
        return layout("Paired", "<h2>Paired</h2><p>This device can use the review page now.</p><p><a href='/'>Open it</a></p>")
    if done is False:
        return layout("Not paired", "<h2>Not paired</h2><p>That link is used up or expired. Make a new one: "
                      "<code>nuauto review pair</code></p>")
    return layout("Pair this device", "<h2>Pair this device with NUauto?</h2><p class=dim>It can then answer the "
                  "assistant and approve its Submit. Only pair your own phone or computer.</p>"
                  f"<form method=post action='/pair'><input type=hidden name=c value='{esc(code)}'>"
                  "<input type=text name=name placeholder='Name (e.g. phone)' maxlength=40><button>Pair</button></form>")


class Handler(BaseHTTPRequestHandler):
    server_version = "nuauto"

    def log_message(self, *args):
        pass

    def _send(self, code, body, kind="text/html; charset=utf-8", headers=()):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")  # no-referrer would make form posts say "Origin: null"
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; "
                                                    "img-src 'self'; connect-src 'self'; form-action 'self'; "
                                                    "frame-ancestors 'none'; base-uri 'none'")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, where):
        self.send_response(303)
        self.send_header("Location", where)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _host_ok(self):
        return self.headers.get("Host", "") == f"{listen_host()}:{PORT}"

    def _key(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE:
                return v
        return None

    def _who(self):
        """(key, device) for a paired device, else (None, None)."""
        key = self._key()
        dev = device(key)
        return (key, dev) if dev else (None, None)

    def _form(self):
        n = min(int(self.headers.get("Content-Length") or 0), 20000)
        return {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode("utf-8", "replace")).items()}

    def _session(self, path):
        m = re.fullmatch(r"/s/([1-9])(/[a-z.]+)?", path)
        return (int(m.group(1)), m.group(2) or "") if m else (None, None)

    def do_GET(self):
        if not self._host_ok():
            return self._send(400, "Wrong host.", "text/plain")
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/poll.js":
            return self._send(200, POLL_JS, "text/javascript")
        if u.path == "/pair":
            return self._send(200, pair_page(q.get("c", "")))
        key, dev = self._who()
        if not dev:
            return self._send(403, layout("Not paired", "<h2>Not paired</h2><p>This device is not paired with NUauto. "
                                          "On the laptop: <code>nuauto review pair</code>, then open its link here.</p>"))
        if u.path == "/":
            flash = f"<div class=card><b>{esc(q['m'])}</b></div>" if q.get("m") else ""
            return self._send(200, index_page(form_token(key)).replace("<h2>NUauto assistants</h2>",
                                                                       "<h2>NUauto assistants</h2>" + flash, 1))
        slot, sub = self._session(u.path)
        s = session(slot) if slot else None
        if not s:
            return self._send(404, layout("Gone", "<h2>No session there</h2><p><a href='/'>All sessions</a></p>"))
        if sub == "":
            return self._send(200, session_page(s, form_token(key), q.get("m", "")))
        if sub == "/v":
            return self._send(200, version(s), "text/plain")
        if sub == "/shot.png":
            from nuauto import assist
            from nuauto import screen
            png = None
            if not q.get("live"):
                try:
                    with open(os.path.join(s["run"], assist.SHOT), "rb") as f:
                        png = f.read()
                except OSError:
                    png = None
            png = png or screen.screenshot(s["run"])
            return self._send(200, png, "image/png") if png else self._send(404, "No screen.", "text/plain")
        return self._send(404, "Not found.", "text/plain")

    def do_POST(self):
        if not self._host_ok():
            return self._send(400, "Wrong host.", "text/plain")
        origin = self.headers.get("Origin")
        if origin and origin != f"http://{listen_host()}:{PORT}":
            return self._send(403, "Wrong origin.", "text/plain")
        u = urlparse(self.path)
        f = self._form()
        if u.path == "/pair":
            key = pair(f.get("c", ""), f.get("name", ""))
            if not key:
                return self._send(403, pair_page("", False))
            return self._send(200, pair_page("", True), headers=[
                ("Set-Cookie", f"{COOKIE}={key}; HttpOnly; SameSite=Strict; Path=/; Max-Age=31536000")])
        key, dev = self._who()
        if not dev or not hmac.compare_digest(f.get("t", ""), form_token(key)):
            return self._send(403, "Not allowed.", "text/plain")
        if u.path == "/start":
            return self._redirect("/?m=" + quote(start_now(f, dev["name"])))
        slot, sub = self._session(u.path)
        s = session(slot) if slot else None
        if not s:
            return self._redirect("/")
        by = dev["name"]
        from nuauto import assist
        try:
            if sub == "/approve" and f.get("d") in ("approve", "decline"):
                msg = answer_approval(s, f.get("id", ""), f["d"], by, f.get("note", ""))
            elif sub == "/reply" and status(s)[0] == "prompt":
                msg = "Not sent: Claude Code is asking something in its terminal (answer it there)."
            elif sub == "/reply" and f.get("text", "").strip():
                type_into(s, f["text"][:MAX_REPLY])
                assist.log(s["run"], f"REVIEW PAGE reply by {by} ({len(f['text'])} characters)")
                msg = "Sent."
            elif sub == "/finish" and f.get("a") in ("y", "n") and s["attention"].get("kind") == "finish":
                type_into(s, f["a"])
                assist.log(s["run"], f"REVIEW PAGE did it go through: {f['a']} (by {by})")
                msg = "Thanks." if f["a"] == "y" else "Left as it was."
            elif sub == "/key" and f.get("k") == "interrupt":
                _tmux("send-keys", "-t", target(s), "Escape")
                msg = "Interrupted."
            elif sub == "/key" and f.get("k") == "exit" and status(s)[0] == "prompt":
                msg = "Not sent: Claude Code is asking something in its terminal (answer it there)."
            elif sub == "/key" and f.get("k") == "exit":
                type_into(s, "/exit")
                assist.log(s["run"], f"REVIEW PAGE ended the session (by {by})")
                msg = "Ending the session."
            else:
                msg = "Nothing to do."
        except (ValueError, OSError, subprocess.TimeoutExpired) as e:
            msg = f"Could not do that: {e}"
        return self._redirect(f"/s/{slot}?m={quote(msg)}")


def start_now(f, by):
    """Start now (the index's queue): one row the queue lists, when a slot and its job site are free."""
    from nuauto import remote
    tab, row = f.get("tab"), f.get("row", "")
    if tab not in ("jobs", "other") or not row.isdigit():
        return "Nothing to do."
    go, rest = remote.queue_now()
    c = next((c for c, why in go + rest if c["tab"] == tab and c["row"] == int(row)
              and (why is None or why.startswith("started by itself once"))), None)
    if not c:
        return "That row can't start now (no free slot, its site is busy, or it is not in the queue)."
    if c not in [x for x, _ in go]:  # it waits for you (started by itself once): still needs a free slot
        go_any, _ = remote.plan([c], *remote_state())
        if not go_any:
            return "No free slot, or its job site is busy: try again when a session ends."
    why = remote.start(c["tab"], c["row"])
    if why:
        return f"Not started: {why}"
    remote.record_started(c)
    return f"Started {c['company']}."


def remote_state():
    """(running, claims, started={}, free) for remote.plan: a row you start by hand ignores the start-once rule."""
    from nuauto import assist
    from nuauto import remote
    running = assist.sessions()
    return running, remote.live_claims(), {}, max(0, assist.slots() - len(running))


def serve():
    """The review page's server (web.py starts it in a thread). Never on every address."""
    ThreadingHTTPServer((listen_host(), PORT), Handler).serve_forever()


# ---------- demo: the page with made-up sessions, on this computer (for looking at it; nothing real) ----------

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
    {"company": "Cobalt Labs", "job_title": "Security Engineering Co-op", "where": "row 31", "finish": True},
]


def demo(port=0):
    """`nuauto review demo`: the review page on 127.0.0.1 with DEMO_SESSIONS, in a temp folder; prints a link that
    pairs the browser that opens it. Nothing real is read or sent (no tmux: Reply etc. say they could not)."""
    import tempfile
    if not os.environ.get("NUAUTO_REVIEW_DEMO"):
        state = tempfile.mkdtemp(prefix="nuauto-review-demo-")
        os.makedirs(os.path.join(state, "local"), mode=0o700)
        with open(os.path.join(state, "local", "local_config.json"), "w") as f:
            json.dump({**config.LOCAL, "web_listen_host": "127.0.0.1", "server_hostname": "", "remote_assist": False}, f)
        env = {**os.environ, "NUAUTO_STATE_DIR": state, "NUAUTO_REVIEW_DEMO": "1"}
        os.execve(sys.executable, [sys.executable, "-m", "nuauto.review", "demo", str(port)], env)
    import fcntl
    import socket
    from nuauto import assist
    global PORT
    if not port:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
    PORT = port
    locks = []
    for n, x in enumerate(DEMO_SESSIONS, 1):
        d = os.path.join(config.LOGS_DIR, f"demo_assist_{n}")
        os.makedirs(d, mode=0o700, exist_ok=True)
        assist.write_private(os.path.join(d, "state.json"), {"company": x["company"], "job_title": x["job_title"],
                                                             "where": x["where"], "slot": n, "tmux": None})
        if x.get("message"):
            assist.write_private(os.path.join(d, assist.MESSAGE), x["message"].encode())
        if x.get("approval"):
            assist.write_private(os.path.join(d, assist.APPROVAL), {**x["approval"], "id": f"demo{n}", "waiting": True,
                                                                     "since": time.time()})
        kind = "finish" if x.get("finish") else "approve" if x.get("approval") else "waiting"
        assist.write_private(os.path.join(d, assist.ATTENTION), {"kind": kind, "since": time.time(), "said": ""})
        f = open(os.path.join(config.LOCAL_DIR, f"assist_slot{n}.lock"), "a+")
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(json.dumps({"slot": n, "row": 10 + n, "tab": "other", "company": x["company"], "host": "demo",
                            "run": d, "since": "10:00"}))
        f.flush()
        locks.append(f)
    import nuauto.remote as remote
    remote.queue_now = lambda: ([], [])  # no sheet here
    link = make_pairing("demo")
    print(json.dumps({"url": link, "port": port, "state_dir": config.STATE_DIR}), flush=True)
    serve()


# ---------- command line ----------

def main(argv):
    if argv[:1] == ["demo"]:  # anywhere, nothing real: the page with made-up sessions
        return demo(int(argv[1]) if len(argv) > 1 and argv[1].isdigit() else 0)
    if not config.IS_SERVER:  # the laptop: the same command on the homelab (the devices live there)
        if not config.HAS_SERVER:
            sys.exit("The review page runs on the homelab (docs/REMOTE.md).")
        from nuauto import sync
        sys.exit(sync.ssh(f"cd {shlex.quote(config.SERVER_DIR)} && .venv/bin/nuauto review "
                          + " ".join(shlex.quote(a) for a in argv)))
    if not config.REMOTE_ASSIST:
        sys.exit("Turn on \"remote_assist\" in local_config.json first (docs/REMOTE.md).")
    if argv[:1] == ["pair"]:
        rest = [a for a in argv[1:] if a != "--discord"]
        link = make_pairing(rest[0] if rest else "phone")
        print(f"Open this on the device to pair (once, within {PAIR_TTL // 60} minutes; Tailscale on):\n  {link}")
        if "--discord" in argv:
            from nuauto import daily
            daily.discord(f"**NUauto: pair a device**\nOpen on your phone (once, {PAIR_TTL // 60} minutes): {link}")
        return
    if argv == ["devices"]:
        try:
            with open(DEVICES) as f:
                devs = json.load(f)
        except (OSError, ValueError):
            devs = {}
        if not devs:
            return print("No paired devices. `nuauto review pair` makes a link.")
        for d in devs.values():
            print(f"{d['name']:20} paired {d['paired']}")
        return
    if argv[:1] == ["unpair"] and len(argv) == 2:
        with _locked(DEVICES) as d:
            gone = [k for k, v in d.items() if argv[1] == "--all" or v["name"] == argv[1]]
            for k in gone:
                del d[k]
        return print(f"Forgot {len(gone)} device{'s' if len(gone) != 1 else ''}.")
    sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
