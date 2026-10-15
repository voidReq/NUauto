"""Offline checks for review.py and the homelab's app (gui.serve_remote: the assistant's screen on your phone). Run: python tests/test_review.py

No ssh, no network beyond 127.0.0.1: the real gui.Handler runs in REMOTE mode on a free local port (paired devices only), the
sessions are lock files in a temp folder, and the terminal is a real tmux session when tmux is installed (else those checks
are skipped). review.py's JSON (view, act, queue_view, start_now) is checked through the app's own endpoints; the page itself
(#/assistant/<slot>) runs in headless Firefox when Playwright has one.
"""
import http.client
import io
import json
import os
import random
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlencode, urlparse

import tempfile as _tempfile  # noqa: E402
os.environ["NUAUTO_STATE_DIR"] = _tempfile.mkdtemp(prefix="nuauto-test-")  # never the real local/ (logins, sheet)
from nuauto import config

tmp = tempfile.mkdtemp()
os.environ["TMUX_TMPDIR"] = tmp  # a private tmux server for every tmux check here
os.environ.pop("TMUX", None)
config.LOCAL_DIR = os.path.join(tmp, "local")  # devices, pairing links, slot locks: never the real local/
config.LOGS_DIR = os.path.join(tmp, "logs")
os.makedirs(config.LOCAL_DIR, mode=0o700)
os.makedirs(config.LOGS_DIR)

from nuauto import accounts  # noqa: E402
from nuauto import assist  # noqa: E402
from nuauto import review  # noqa: E402

config.LOCAL = {**config.LOCAL, "web_listen_host": "127.0.0.1"}
# the homelab's app: on, as nuauto-web would run it (no homelab sessions are read over ssh: IS_SERVER)
config.IS_SERVER, config.REMOTE_ASSIST, config.REMOTE_APP = True, True, True
HAVE_TMUX = shutil.which("tmux") is not None


def device_count():
    return len(assist.read_json(review.DEVICES, {}))


# ---- pairing: one use, ten minutes
review.PORT = 8766  # (the link's port; the real server's is set below)
link = review.make_pairing("phone")
assert link.startswith("http://127.0.0.1:8766/pair?c=")
code = parse_qs(urlparse(link).query)["c"][0]
assert len(code) >= 20 and code not in open(review.PAIRING).read()           # only its hash is kept
assert oct(os.stat(review.PAIRING).st_mode & 0o777) == "0o600"
key = review.pair(code, "My phone")
assert key and len(key) >= 32 and review.pair(code) is None                  # second time: None
assert oct(os.stat(review.DEVICES).st_mode & 0o777) == "0o600" and key not in open(review.DEVICES).read()
dev = review.device(key)
assert dev["name"] == "My phone" and dev["paired"]
assert review.device("wrong") is None and review.device("") is None and review.device(None) is None
assert review.pair("not-a-code") is None and review.pair(None) is None and review.pair("") is None
old = parse_qs(urlparse(review.make_pairing("tablet")).query)["c"][0]
with review._locked(review.PAIRING) as p:                                     # expired
    for v in p.values():
        v["until"] = time.time() - 1
assert review.pair(old) is None
code2 = parse_qs(urlparse(review.make_pairing("x" * 100)).query)["c"][0]
key2 = review.pair(code2)                                                     # no name given: the link's, cut short
assert len(review.device(key2)["name"]) == 40

# a device's id: a short stable name for the app's list, made from the key's hash, never the key itself
assert re.fullmatch(r"[0-9a-f]{12}", dev["id"]) and dev["id"] == review.device(key)["id"] == review.device_id(review._hash(key))
assert dev["id"] != review.device(key2)["id"] and key not in dev["id"] and key2 not in review.device(key2)["id"]
listing = review.devices()
assert [d_["name"] for d_ in listing] == ["My phone", "x" * 40] or [d_["name"] for d_ in listing] == ["x" * 40, "My phone"]
assert {d_["id"] for d_ in listing} == {dev["id"], review.device(key2)["id"]} and all(d_["paired"] for d_ in listing)
assert all(set(d_) == {"id", "name", "paired"} for d_ in listing)
assert key not in json.dumps(listing) and key2 not in json.dumps(listing) and review._hash(key) not in json.dumps(listing)
assert [d_["paired"] for d_ in listing] == sorted(d_["paired"] for d_ in listing)   # oldest first
# unpair: by id, by name, "--all"; what is not there is zero
assert review.unpair("nobody") == 0 and review.unpair("") == 0 and review.unpair("0" * 12) == 0 and len(review.devices()) == 2
assert review.unpair(dev["id"]) == 1 and review.device(key) is None and review.device(key2) is not None
assert review.unpair("x" * 40) == 1 and review.device(key2) is None and review.devices() == [] and device_count() == 0
k3, k4 = (review.pair(parse_qs(urlparse(review.make_pairing(n_)).query)["c"][0]) for n_ in ("three", "four"))
assert review.unpair("--all") == 2 and review.device(k3) is None and review.device(k4) is None and review.devices() == []
key2 = None

# ---- the app: a real server in REMOTE mode, a fake session
from nuauto import gui  # noqa: E402

gui.REMOTE = True
srv = ThreadingHTTPServer(("127.0.0.1", 0), gui.Handler)
srv.daemon_threads = True
PORT = srv.server_address[1]
gui.APP.port, gui.APP.remote_host = PORT, "127.0.0.1"
review.PORT = PORT
HOST = f"127.0.0.1:{PORT}"
ORIGIN = f"http://{HOST}"
threading.Thread(target=srv.serve_forever, daemon=True).start()
assert review.make_pairing("probe").startswith(f"{ORIGIN}/pair?c=")
review.unpair("--all")


def req(method, path, headers=None, form=None, cookie=None, host=HOST, body=None):
    """Any request; form = an urlencoded body (the pairing page's), body = a JSON object. -> (status, headers, bytes)."""
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
    h = {"Host": host, **(headers or {})}
    if cookie:
        h["Cookie"] = f"{review.COOKIE}={cookie}"
    data = None
    if form is not None:
        data = urlencode(form)
        h["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body)
        h.setdefault("Content-Type", "application/json")
    conn.request(method, path, body=data, headers={k: v for k, v in h.items() if v is not None})
    r = conn.getresponse()
    out = (r.status, {k.lower(): v for k, v in r.getheaders()}, r.read())
    conn.close()
    return out


def J(resp):
    return json.loads(resp[2])


def get(path, cookie=None, **kw):
    return req("GET", path, cookie=cookie, **kw)


def post(path, body, cookie=None, origin=ORIGIN, headers=None, **kw):
    """A POST the way the page sends it (JSON, X-NUauto, its Origin); a header set to None is left out."""
    return req("POST", path, headers={"X-NUauto": "1", "Origin": origin, **(headers or {})}, body=body, cookie=cookie, **kw)


run = os.path.join(config.LOGS_DIR, "20261011-120000_assist_row7")
os.makedirs(run)
other_run = os.path.join(tmp, "elsewhere", "run")   # outside logs/: never shown
os.makedirs(other_run)
TMUX_NAME = f"nuautotest-{random.randrange(10**8)}"
state = {"company": "Acme <b>Robotics</b>", "job_title": "Firmware Intern", "where": "Other jobs row 7", "password_sites": [],
         "slot": 1, "tmux": TMUX_NAME if HAVE_TMUX else None}
approval = {"id": "ap1", "what": "click 'Submit'", "url": "https://acme.example/apply", "why": "NUAUTO: guesses: Graduation = May 2029",
            "since": time.time(), "waiting": True}
EVIL = "<script>alert(1)</script>"
for r_, st in ((run, state), (other_run, {**state, "company": "Hidden Co"})):
    assist.write_private(os.path.join(r_, "state.json"), st)
assist.write_private(os.path.join(run, assist.APPROVAL), approval)
assist.write_private(os.path.join(run, assist.MESSAGE), f"Please confirm these guesses {EVIL}".encode())
assist.write_private(os.path.join(run, assist.ATTENTION), {"kind": "approve", "since": time.time(), "time": time.time(), "said": ""})
assist.write_private(os.path.join(other_run, assist.MESSAGE), b"hidden message")

holders = []


def hold_slot(n, run_dir):
    f = os.path.join(config.LOCAL_DIR, f"assist_slot{n}.lock")
    h = subprocess.Popen([sys.executable, "-c", f"""
import fcntl, json, time
f = open({f!r}, "a+"); fcntl.flock(f, fcntl.LOCK_EX); f.seek(0); f.truncate()
f.write(json.dumps({{"slot": {n}, "row": {n + 6}, "tab": "other", "run": {run_dir!r}}})); f.flush(); print("held", flush=True); time.sleep(120)"""],
        stdout=subprocess.PIPE, text=True)
    assert h.stdout.readline().strip() == "held"
    holders.append(h)


try:
    hold_slot(1, run)
    hold_slot(2, other_run)

    # who may talk to it: this server's own Host, and only paired devices
    assert get("/", host="evil.example")[0] == 403 and req("POST", "/pair", form={"c": "x"}, host="evil.example")[0] == 403
    assert post("/api/assistant", {"slot": 1}, host="evil.example")[0] == 403
    assert get("/", host=f"localhost:{PORT}")[0] == 403                        # exactly the Tailscale address, like the Mark page
    assert get("/static/app.js", host=f"localhost:{PORT}")[0] == 403 and get("/pair?c=x", host="evil.example")[0] == 403
    for path in ("/api/assistant/1", "/api/assistant/1/shot.png", "/api/devices", "/api/assistant/2", "/api/nope"):
        r = get(path)
        assert r[0] == 403 and J(r)["pair"] is True and J(r)["error"], path   # unpaired: the page is told to show the pairing help
        assert get(path, cookie="not-a-key")[0] == 403, path
    assert get("/")[0] == 200 and get("/static/app.js")[0] == 200 and get("/static/app.css")[0] == 200   # the page's own code: public
    assert get("/static/nope.js")[0] == 404 and get("/static/..%2fgui.py")[0] == 404 and get("/static/app.py")[0] == 404
    r = post("/api/assistant", {"slot": 1, "do": "approve", "id": "ap1"})
    assert r[0] == 403 and J(r)["pair"] is True                                # a POST with no cookie: not paired

    # pairing: GET only shows a form; the code is used up by the POST
    code = parse_qs(urlparse(review.make_pairing("phone")).query)["c"][0]
    r = get(f"/pair?c={code}")
    assert r[0] == 200 and r[1]["content-type"].startswith("text/html")
    assert "<form method=post action=/pair" in r[2].decode() and f"value='{code}'" in r[2].decode() and "set-cookie" not in r[1]
    assert get(f"/pair?c={code}")[0] == 200 and device_count() == 0            # still unused
    assert req("POST", "/pair", form={"c": "wrong"})[0] == 403 and device_count() == 0
    assert req("POST", "/pair", headers={"Origin": "http://evil.example"}, form={"c": code})[0] == 403 and device_count() == 0
    assert req("POST", "/pair", headers={"Origin": "null"}, form={"c": code})[0] == 403 and device_count() == 0
    r = req("POST", "/pair", form={"c": code, "name": "Phone"})
    assert r[0] == 200 and "Paired" in r[2].decode() and device_count() == 1
    cookie_line = r[1]["set-cookie"]
    assert cookie_line.startswith(f"{review.COOKIE}=") and "HttpOnly" in cookie_line and "SameSite=Strict" in cookie_line
    assert "Path=/" in cookie_line
    KEY = cookie_line.split(";")[0].split("=", 1)[1]
    assert review.device(KEY)["name"] == "Phone"
    assert req("POST", "/pair", form={"c": code, "name": "Phone"})[0] == 403 and device_count() == 1   # used up
    assert "Not paired" in req("POST", "/pair", form={"c": code})[2].decode()
    for m in ("GET", "POST"):  # an old pairing link opened again on a paired device: straight to the page
        r = get(f"/pair?c={code}", cookie=KEY) if m == "GET" else req(m, "/pair", form={"c": code}, cookie=KEY)
        assert r[0] == 303 and r[1]["location"] == "/" and device_count() == 1, (m, r[0])
    # the form posted from the page's own origin pairs too (a browser sends its Origin on a form post)
    code_o = parse_qs(urlparse(review.make_pairing("tablet")).query)["c"][0]
    r = req("POST", "/pair", headers={"Origin": ORIGIN}, form={"c": code_o, "name": "Tab"})
    assert r[0] == 200 and "set-cookie" in r[1] and device_count() == 2
    assert review.unpair("Tab") == 1 and device_count() == 1

    # headers on every answer
    for r in (get("/", cookie=KEY), get("/"), get("/pair?c=x"), get("/static/app.js"), get("/api/assistant/1", cookie=KEY),
              get("/api/assistant/1"), get("/api/nope", cookie=KEY), post("/api/assistant", {"slot": 1}, cookie=KEY)):
        h = r[1]
        assert h["cache-control"] == "no-store" and h["x-frame-options"] == "DENY" and h["x-content-type-options"] == "nosniff"
        assert "default-src 'self'" in h["content-security-policy"] and "frame-ancestors 'none'" in h["content-security-policy"]
        assert h["referrer-policy"] == "same-origin"   # (no-referrer makes Firefox send "Origin: null" on form posts)

    # the old review page's links (in Discord messages) land on the assistant's screen, with or without the cookie
    for p_ in ("/s/1", "/s/1/v", "/s/2"):
        r = get(p_)
        assert r[0] == 303 and r[1]["location"] == f"/#/assistant/{p_[3]}", p_
    assert get("/s/0")[0] != 303 and get("/s/1x")[0] != 303

    # the session's view: this session only (the other's run folder is outside logs/); raw text, the page escapes it
    r = get("/api/assistant/1", cookie=KEY)
    assert r[0] == 200 and r[1]["content-type"].startswith("application/json")
    v = J(r)
    assert v["slot"] == 1 and v["tab"] == "other" and v["row"] == 7 and v["status"] == "approve", v
    assert v["company"] == "Acme <b>Robotics</b>" and v["title"] == "Firmware Intern" and v["where"] == "Other jobs row 7"
    assert v["text"] == "Ready to submit: click 'Submit'", v["text"]
    assert v["message"] == f"Please confirm these guesses {EVIL}", "the agent's message comes back as it is"
    a_ = v["approval"]
    assert a_ == {"id": "ap1", "what": "click 'Submit'", "url": "https://acme.example/apply",
                  "why": "NUAUTO: guesses: Graduation = May 2029", "waiting": True, "shot": False, "answered": None, "by": None}, a_
    assert v["browser"] is None and "terminal" in v and v["tmux"] == (TMUX_NAME if HAVE_TMUX else "")
    for p_ in ("/api/assistant/2", "/api/assistant/3", "/api/assistant/0", "/api/assistant/1/other", "/api/assistant/2/shot.png",
               "/api/assistant/x", "/api/nope"):
        assert get(p_, cookie=KEY)[0] == 404, p_
    assert "Hidden Co" not in json.dumps(v) and "hidden message" not in json.dumps(v)
    r = post("/api/assistant", {"slot": 2, "do": "approve", "id": "ap1"}, cookie=KEY)    # the hidden session: refused
    assert r[0] == 409 and "ended" in J(r)["error"] and not os.path.exists(os.path.join(other_run, assist.DECISION))
    assert post("/api/assistant", {"slot": 7, "do": "approve", "id": "ap1"}, cookie=KEY)[0] == 409
    assert post("/api/assistant", {"slot": "x", "do": "approve"}, cookie=KEY)[0] == 409

    # the app's own endpoints that name the paired device: the list (no keys, which one asks), pairing another, unpairing
    r = get("/api/devices", cookie=KEY)
    assert r[0] == 200 and J(r)["this"] == review.device(KEY)["id"] and [d_["name"] for d_ in J(r)["devices"]] == ["Phone"]
    assert KEY not in r[2].decode() and review._hash(KEY) not in r[2].decode()
    r = post("/api/action", {"kind": "pair_device", "args": {"name": "  my   laptop "}}, cookie=KEY)
    assert r[0] == 200 and J(r)["link"].startswith(f"{ORIGIN}/pair?c=") and J(r)["minutes"] == 10, r[2]
    code_l = parse_qs(urlparse(J(r)["link"]).query)["c"][0]
    assert review.pair(code_l) and [d_["name"] for d_ in review.devices()].count("my laptop") == 1   # (name tidied)
    assert post("/api/action", {"kind": "pair_device", "args": {"name": "x" * 99}}, cookie=KEY)[0] == 200
    assert post("/api/action", {"kind": "pair_device"})[0] == 403                                        # no cookie
    other_id = next(d_["id"] for d_ in review.devices() if d_["name"] == "my laptop")
    r = post("/api/action", {"kind": "unpair", "args": {"id": review.device(KEY)["id"]}}, cookie=KEY)  # this very device
    assert r[0] == 409 and "this device" in J(r)["error"] and review.device(KEY)
    for bad in ("", "nope", "../x", review._hash(KEY)):
        assert post("/api/action", {"kind": "unpair", "args": {"id": bad}}, cookie=KEY)[0] == 409, bad
    r = post("/api/action", {"kind": "unpair", "args": {"id": other_id}}, cookie=KEY)
    assert r[0] == 200 and J(r)["gone"] == 1 and "my laptop" not in [d_["name"] for d_ in J(r)["devices"]]
    review.unpair("x" * 40)
    assert [d_["name"] for d_ in review.devices()] == ["Phone"]

    # the queue (remote.py's inputs faked): each row's group, what it says, whether you can start it now
    from nuauto import remote  # noqa: E402

    def cand(row, host):
        return {"tab": "other", "row": row, "url": "https://x.example/a", "row_url": "https://x.example/a", "host": host,
                "company": "Q Co", "title": "T", "closes": None, "match": 50}
    real_queue = remote.queue_inputs
    remote.queue_inputs = lambda: ([cand(7, "acme.example"), cand(9, "b.example"), cand(11, "c.example")],
                                   [{"tab": "other", "row": 7, "host": "acme.example"}],           # slot 1's session (row 7)
                                   [{"tab": "other", "row": 9, "host": "b.example"}], {}, 2, None)  # the laptop's claim on row 9
    real_claims, real_start, real_record = remote.live_claims, remote.start, remote.record_started
    remote.live_claims = lambda *a, **k: [{"tab": "other", "row": 9, "host": "b.example"}]
    started, recorded = [], []
    remote.start = lambda tab, row: started.append((tab, row)) or None
    remote.record_started = lambda c, auto=True: recorded.append((c["row"], auto))
    try:
        q = review.queue_view()
        rows = q["rows"]
        assert rows["other-7"]["group"] == "running" and rows["other-7"]["slot"] == 1 and rows["other-7"]["start"] is False
        assert rows["other-9"]["group"] == "running" and rows["other-9"]["text"] == "the laptop's assistant is working on it"
        assert rows["other-9"]["slot"] is None and rows["other-9"]["start"] is False
        assert [k for k, r_ in rows.items() if r_["start"]] == ["other-11"] and rows["other-11"]["group"] in ("next", "ready")
        assert q["free"] == 2 and [g["key"] for g in q["groups"]][0] == "running" and q["hours"] == list(remote.hours())
        assert [rows[k]["order"] for k in ("other-7", "other-9", "other-11")] == [0, 1, 2]

        # Start now: only a row that can start; the answer says why not (HTTP 409); the paired device's name goes in the log
        def start_now(row, tab="other", cookie=KEY, **kw):
            return post("/api/action", {"kind": "assist", "args": {"row": row, "tab": tab}}, cookie=cookie, **kw)
        assert start_now(11, cookie=None)[0] == 403 and started == []
        r = start_now(11)
        assert r[0] == 200 and J(r)["started"] is True and J(r)["message"].startswith("Started Q Co"), r[2]
        assert started == [("other", 11)] and recorded == [(11, False)]
        assert "Phone started other row 11 (Q Co)" in open(os.path.join(config.LOGS_DIR, "review.log")).read()
        for row_ in (7, 9):                                                                   # a session / the laptop has it
            r = start_now(row_)
            assert r[0] == 409 and J(r)["error"].startswith("Not started:"), (row_, r[2])
        r = start_now(99)
        assert r[0] == 409 and "not in the queue" in J(r)["error"]
        r = start_now(11, tab="jobs")                                                         # the other tab has no row 11
        assert r[0] == 409 and "not in the queue" in J(r)["error"]
        assert post("/api/action", {"kind": "assist", "args": {"row": "abc", "tab": "other"}}, cookie=KEY)[0] == 409
        remote.start = lambda tab, row: "tmux is not installed"
        r = start_now(11)
        assert r[0] == 409 and J(r)["error"] == "Not started: tmux is not installed", r[2]
        assert started == [("other", 11)] and recorded == [(11, False)]                      # nothing more was started

        # Drop (not applying): the row becomes Failed through sheet.drop with the reason; never one a session or the laptop has
        from nuauto import sheet  # noqa: E402
        dropped, real = [], (sheet.open_other, sheet.drop)
        sheet.open_other = lambda interactive=True: "ws"

        def fake_drop(ws, n, url, why):
            if url != "https://x.example/a":
                raise sheet.SheetError(f"Row {n} holds another job now.")
            dropped.append((ws, n, url, why))
        sheet.drop = fake_drop
        try:
            f = {"action": "drop", "tab": "other", "row": 11, "url": "https://x.example/a", "why": "duplicate"}
            assert post("/api/mark", f, cookie=None)[0] == 403 and dropped == []
            r = post("/api/mark", f, cookie=KEY)
            assert r[0] == 200 and dropped == [("ws", 11, "https://x.example/a", "duplicate")], r[2]
            r = post("/api/mark", {**f, "row": 7}, cookie=KEY)                                # slot 1's session
            assert r[0] == 409 and "assistant is working" in J(r)["error"]
            r = post("/api/mark", {**f, "row": 9}, cookie=KEY)                                # the laptop's claim
            assert r[0] == 409 and "laptop's assistant" in J(r)["error"]
            r = post("/api/mark", {**f, "url": "https://other.example/"}, cookie=KEY)          # the row holds another job
            assert r[0] == 409 and "another job" in J(r)["error"]
            r = post("/api/mark", {**f, "why": "bored"}, cookie=KEY)
            assert r[0] == 409 and J(r)["error"] == "Unknown reason." and len(dropped) == 1
            assert post("/api/mark", {**f, "action": "nope"}, cookie=KEY)[0] == 409 and len(dropped) == 1
        finally:
            sheet.open_other, sheet.drop = real
    finally:
        remote.queue_inputs = real_queue
        remote.live_claims, remote.start, remote.record_started = real_claims, real_start, real_record

    # answering: a paired device, the page's headers (X-NUauto, JSON), this server's Origin
    answer = {"slot": 1, "do": "approve", "id": "ap1"}
    decision_path = os.path.join(run, assist.DECISION)
    assert post("/api/assistant", answer, cookie=KEY, headers={"X-NUauto": None})[0] == 403          # not sent by the page
    assert post("/api/assistant", answer, cookie=KEY, headers={"X-NUauto": "0"})[0] == 403
    assert req("POST", "/api/assistant", headers={"Origin": ORIGIN, "X-NUauto": "1", "Content-Type": "text/plain"},
               body=answer, cookie=KEY)[0] == 403                                                      # a form's kind of body
    assert req("POST", "/api/assistant", headers={"Origin": ORIGIN, "X-NUauto": "1", "Content-Type": None},
               body=answer, cookie=KEY)[0] == 403
    assert post("/api/assistant", answer, origin="http://evil.example", cookie=KEY)[0] == 403
    assert post("/api/assistant", answer, origin="null", cookie=KEY)[0] == 403
    assert post("/api/assistant", answer, origin=f"http://localhost:{PORT}", cookie=KEY)[0] == 403   # exactly this server's own
    assert post("/api/assistant", answer, cookie=None)[0] == 403                                      # no cookie
    assert post("/api/assistant", answer, cookie="not-a-key")[0] == 403
    assert not os.path.exists(decision_path)                                                          # none of those did anything
    r = post("/api/assistant", answer, cookie=KEY)
    assert r[0] == 200 and J(r)["message"] == "Approved." and "closing" not in J(r), r[2]
    dec = assist.read_json(decision_path)
    assert dec["id"] == "ap1" and dec["decision"] == "approve" and dec["by"] == "Phone", dec
    assert "REVIEW PAGE approve by Phone" in open(os.path.join(run, "actions.log")).read()
    v = J(get("/api/assistant/1", cookie=KEY))                                    # until the guard picks it up: said so, once
    assert v["approval"]["answered"] == "approve" and v["approval"]["by"] == "Phone"
    os.remove(decision_path)
    r = post("/api/assistant", {**answer, "do": "decline", "note": "wrong\nphone  number"}, cookie=KEY)
    dec = assist.read_json(decision_path)
    assert r[0] == 200 and J(r)["message"] == "Declined." and dec["decision"] == "decline" and dec["by"] == "Phone"
    assert dec["note"] == "wrong phone number", dec
    assert J(get("/api/assistant/1", cookie=KEY))["approval"]["answered"] == "decline"
    os.remove(decision_path)
    r = post("/api/assistant", {**answer, "id": "old-id"}, cookie=KEY)              # another question: gone
    assert r[0] == 200 and "gone" in J(r)["message"] and not os.path.exists(decision_path)
    r = post("/api/assistant", {**answer, "id": ""}, cookie=KEY)
    assert "gone" in J(r)["message"] and not os.path.exists(decision_path)
    r = post("/api/assistant", {**answer, "do": "maybe"}, cookie=KEY)              # not approve / decline: nothing
    assert r[0] == 200 and J(r)["message"] == "Nothing to do." and not os.path.exists(decision_path)
    assert J(post("/api/assistant", {"slot": 1}, cookie=KEY))["message"] == "Nothing to do."          # no "do"
    assert post("/api/assistant", [1, 2], cookie=KEY)[0] == 400                      # not a JSON object
    assert req("POST", "/api/assistant", headers={"X-NUauto": "1", "Origin": ORIGIN, "Content-Type": "application/json"},
               cookie=KEY, form=None, body=None)[0] == 409                           # no body at all = {}: no slot, refused
    assert not os.path.exists(decision_path)

    # the poll: one version string that changes with the message and the question
    v1 = J(get("/api/assistant/1", cookie=KEY))["version"]
    assert re.fullmatch(r"[0-9a-f]{16}", v1) and J(get("/api/assistant/1", cookie=KEY))["version"] == v1
    assist.write_private(os.path.join(run, assist.MESSAGE), b"a new question?")
    v2 = J(get("/api/assistant/1", cookie=KEY))["version"]
    assert v2 != v1 and J(get("/api/assistant/1", cookie=KEY))["message"] == "a new question?"
    assist.write_private(os.path.join(run, assist.APPROVAL), {**approval, "waiting": False})
    assert J(get("/api/assistant/1", cookie=KEY))["version"] != v2
    assert J(get("/api/assistant/1", cookie=KEY))["approval"]["waiting"] is False
    assist.write_private(os.path.join(run, assist.APPROVAL), approval)

    # the screen when it asked
    assert get("/api/assistant/1/shot.png", cookie=KEY)[0] == 404
    PNG = b"\x89PNG\r\n\x1a\nfake"
    assist.write_private(os.path.join(run, assist.SHOT), PNG)
    r = get("/api/assistant/1/shot.png?a=ap1", cookie=KEY)
    assert r[0] == 200 and r[1]["content-type"] == "image/png" and r[2] == PNG
    assert get("/api/assistant/1/shot.png", cookie=None)[0] == 403
    assert J(get("/api/assistant/1", cookie=KEY))["approval"]["shot"] is True
    assert get("/api/assistant/1/shot.png?live=1", cookie=KEY)[0] == 404        # live: the screen itself (none in a test)
    assert get("/api/assistant/2/shot.png", cookie=KEY)[0] == 404

    # the browser's link and password only for a paired device (the JSON of an unpaired one is the pairing answer)
    assist.write_private(os.path.join(run, "view.json"), {"url": "http://100.64.0.1:6081/vnc.html", "password": "pw123456"})
    v = J(get("/api/assistant/1", cookie=KEY))
    assert v["browser"] == {"url": "http://100.64.0.1:6081/vnc.html", "password": "pw123456"}
    for r in (get("/api/assistant/1"), get("/"), get("/api/assistant/1", cookie="not-a-key"), get("/s/1")):
        assert "pw123456" not in r[2].decode() and "100.64.0.1" not in r[2].decode()
    os.remove(os.path.join(run, "view.json"))

    # no tmux for the session: a reply says so, nothing is typed
    assist.write_private(os.path.join(run, "state.json"), {**state, "tmux": None})
    r = post("/api/assistant", {"slot": 1, "do": "reply", "text": "hi"}, cookie=KEY)
    assert r[0] == 200 and "tmux" in J(r)["message"] and J(r)["message"].startswith("Could not"), r[2]
    r = post("/api/assistant", {"slot": 1, "do": "exit"}, cookie=KEY)
    assert "tmux" in J(r)["message"] and J(r)["message"].startswith("Could not")
    assert "tmux" in J(post("/api/assistant", {"slot": 1, "do": "interrupt"}, cookie=KEY))["message"]
    assist.write_private(os.path.join(run, "state.json"), state)
    for text_ in ("  ", "", None):
        assert J(post("/api/assistant", {"slot": 1, "do": "reply", "text": text_}, cookie=KEY))["message"] == "Nothing to send."
    for bad in ("evil; rm", "a b", "", "x:y"):
        try:
            review.target({"state": {"tmux": bad}})
        except ValueError:
            pass
        else:
            raise AssertionError(f"tmux name {bad!r} accepted")
    assert review.target({"state": {"tmux": "nuauto1"}}) == "=nuauto1:"

    # the real terminal
    if not HAVE_TMUX:
        print("(skipped the tmux checks: tmux is not installed here)")
    else:
        subprocess.run(["tmux", "new-session", "-d", "-s", TMUX_NAME, "-x", "200", "-y", "50", "cat"], check=True)
        for _ in range(50):  # tmux starts it through your shell: until then the pane shows the shell (never typed into)
            if subprocess.run(["tmux", "display-message", "-p", "-t", f"={TMUX_NAME}:", "#{pane_current_command}"],
                              capture_output=True, text=True).stdout.strip() == "cat":
                break
            time.sleep(0.1)
        try:
            sess = review.session(1)
            review.type_into(sess, "hello from the phone")
            review.type_into(sess, "line one\nline two")
            time.sleep(0.5)
            pane = subprocess.run(["tmux", "capture-pane", "-p", "-J", "-t", f"={TMUX_NAME}:"], capture_output=True, text=True).stdout
            assert "hello from the phone" in pane and "line one" in pane and "line two" in pane, pane
            review.type_into(sess, "$(echo injected) ; `x` -t 'quote\"")            # sent literally, never run or parsed
            time.sleep(0.3)
            assert "$(echo injected) ; `x` -t 'quote\"" in review.terminal(sess)
            assert "hello from the phone" in review.terminal(sess)
            assert "hello from the phone" in J(get("/api/assistant/1", cookie=KEY))["terminal"]   # the screen's terminal
            # the screen's Reply, Finish and keys
            r = post("/api/assistant", {"slot": 1, "do": "reply", "text": "typed by reply"}, cookie=KEY)
            assert r[0] == 200 and J(r)["message"] == "Sent."
            r = post("/api/assistant", {"slot": 1, "do": "finish", "answer": "y"}, cookie=KEY)    # not at the finish question
            assert J(r)["message"] == "Nothing to do." and "closing" not in J(r)
            assist.write_private(os.path.join(run, assist.ATTENTION), {"kind": "finish", "since": 1, "time": 1, "said": ""})
            v = J(get("/api/assistant/1", cookie=KEY))
            assert v["status"] == "finish" and v["text"] == "The session ended: did it go through?" and v["message"] == ""
            r = post("/api/assistant", {"slot": 1, "do": "finish", "answer": "y"}, cookie=KEY)
            assert J(r)["closing"] is True and "Thanks" in J(r)["message"]                       # back to the list at once
            # one tap is enough: the screen says your answer (not the question again) while the run closes
            assert assist.read_json(os.path.join(run, assist.ATTENTION))["kind"] == "answered"
            v = J(get("/api/assistant/1", cookie=KEY))
            assert v["status"] == "answered" and "marking it Applied" in v["text"] and v["message"] == ""
            r = post("/api/assistant", {"slot": 1, "do": "finish", "answer": "y"}, cookie=KEY)
            assert "Already answered" in J(r)["message"] and J(r)["closing"] is True
            r = post("/api/assistant", {"slot": 1, "do": "exit"}, cookie=KEY)
            assert "Ending" in J(r)["message"]
            time.sleep(0.5)
            pane = review.terminal(review.session(1))
            assert "typed by reply" in pane and "/exit" in pane and pane.count("\ny") >= 1, pane
            log = open(os.path.join(run, "actions.log")).read()
            assert "REVIEW PAGE reply by Phone (14 characters)" in log and "did it go through: y (by Phone)" in log
            assert "ended the session (by Phone)" in log
            assert J(post("/api/assistant", {"slot": 1, "do": "interrupt"}, cookie=KEY))["message"] == "Interrupted."
            # a site password the run used never shows in the terminal text
            real_passwords = accounts.passwords
            accounts.passwords = lambda sites: ["S3cretPw!x"] if sites else []
            try:
                review.type_into(sess, "my password is S3cretPw!x ok")
                time.sleep(0.3)
                assert "S3cretPw!x" in review.terminal({**sess, "state": {**sess["state"], "password_sites": []}})
                hidden = review.terminal({**sess, "state": {**sess["state"], "password_sites": ["acme.example"]}})
                assert "S3cretPw!x" not in hidden and assist.HIDDEN in hidden and "my password is" in hidden
                assist.write_private(os.path.join(run, "state.json"), {**state, "password_sites": ["acme.example"]})
                r = get("/api/assistant/1", cookie=KEY)                              # nor in the screen's JSON
                assert "S3cretPw!x" not in r[2].decode() and assist.HIDDEN in J(r)["terminal"]
            finally:
                accounts.passwords = real_passwords
                assist.write_private(os.path.join(run, "state.json"), state)
            subprocess.run(["tmux", "kill-session", "-t", f"={TMUX_NAME}"])
            assert review.terminal(sess) == ""                                  # a gone session: nothing, no error
            assert J(get("/api/assistant/1", cookie=KEY))["terminal"] == ""
        finally:
            subprocess.run(["tmux", "kill-session", "-t", f"={TMUX_NAME}"], capture_output=True)

    # "do it on the laptop": End and No write laptop.json, mark the sheet row (the real sheet.mark_on_laptop on an in-memory
    # worksheet), then type /exit or n; a failure to mark is a message, never an error
    from nuauto import sheet  # noqa: E402

    class CellWS:
        """An in-memory worksheet: update() writes the one cell it is given (E7 = column E, row 7)."""
        def __init__(self, rows):
            self.values = [list(sheet.HEADERS)] + [list(r) for r in rows]
            self.calls = []

        def get_all_values(self):
            return [list(r) for r in self.values]

        def update(self, range_name, values, value_input_option=None):
            self.calls.append(range_name)
            self.values[int(range_name[1:]) - 1][ord(range_name[0]) - ord("A")] = values[0][0]

    ROW_URL = "https://jobs.example.org/the-row"

    def fresh_sheet(status="Approved", url=ROW_URL, company="Acme <b>Robotics</b>", notes="match 90%"):
        filler = [[f"https://x/{i}", "Co", "Job", "Approved", "", ""] for i in range(1, 6)]  # rows 2-6
        return CellWS(filler + [[url, company, "Firmware Intern", status, notes, ""]])               # row 7

    real_sheet = (sheet.open_other, sheet.open_worksheet)
    real_type_into = review.type_into
    typed_laptop, opened = [], []
    laptop_file = os.path.join(run, assist.LAPTOP_FILE)
    ws_now = [fresh_sheet()]

    def fake_open(tab):
        def f(*a, **k):
            opened.append((tab, k.get("interactive")))
            if isinstance(ws_now[0], Exception):
                raise ws_now[0]
            return ws_now[0]
        return f

    def fake_type(sess, text_):  # the file is there before the terminal is touched
        typed_laptop.append((sess["slot"], text_, os.path.exists(laptop_file)))

    def reset(attention, approval_on=False, **state_over):
        for name in (assist.LAPTOP_FILE,):
            if os.path.exists(os.path.join(run, name)):
                os.remove(os.path.join(run, name))
        assist.write_private(os.path.join(run, "state.json"), {**state, "tab": "other", "row_url": ROW_URL, **state_over})
        assist.write_private(os.path.join(run, assist.ATTENTION), {"kind": attention, "since": 1, "time": 1, "said": ""})
        if approval_on:
            assist.write_private(os.path.join(run, assist.APPROVAL), approval)
        elif os.path.exists(os.path.join(run, assist.APPROVAL)):
            os.remove(os.path.join(run, assist.APPROVAL))
        typed_laptop.clear(), opened.clear()
        ws_now[0] = fresh_sheet()

    def tap(body, cookie=KEY, **kw):
        return post("/api/assistant", {"slot": 1, **body}, cookie=cookie, **kw)

    MARK = assist.LAPTOP_MARK
    sheet.open_other, sheet.open_worksheet = fake_open("other"), fake_open("jobs")
    review.type_into = fake_type
    real_end = review.end_session
    review.end_session = lambda sess: fake_type(sess, "/exit")  # (End session types /exit itself, with its pause and retry)
    try:
        # which state each one is in decides what the screen offers (its buttons) and what a tap does
        for kind, approval_on, word in (("waiting", False, "waiting"), ("finish", False, "finish"), ("approve", False, "prompt"),
                                        ("approve", True, "approve"), ("answered", False, "answered")):
            reset(kind, approval_on)
            assert J(get("/api/assistant/1", cookie=KEY))["status"] == word, (kind, approval_on)
        # a Claude Code permission prompt: no reply, no End (Enter would pick its first option)
        reset("approve")
        assert J(tap({"do": "reply", "text": "x"}))["message"].startswith("Not sent: Claude Code is asking")
        assert J(tap({"do": "exit"}))["message"].startswith("Not sent: Claude Code is asking") and typed_laptop == []

        # End: do it on the laptop (the session's working or waiting for you): marked, then /exit typed
        reset("waiting")
        r = tap({"do": "laptop"})
        assert r[0] == 200 and "Left for the laptop: marked in your sheet" in J(r)["message"], r[2]
        assert typed_laptop == [(1, "/exit", True)], typed_laptop
        assert oct(os.stat(laptop_file).st_mode & 0o777) == "0o600" and assist.read_json(laptop_file)["by"] == "Phone"
        assert ws_now[0].values[6][4] == f"match 90% | {MARK} (the site showed it a sign-in page with no way in)", ws_now[0].values[6]
        assert ws_now[0].values[6][3] == "Approved" and ws_now[0].calls == ["E7"] and opened == [("other", False)]
        assert "do it on the laptop (by Phone)" in open(os.path.join(run, "actions.log")).read()
        r = tap({"do": "laptop"})    # again: still marked once, /exit again is harmless
        assert ws_now[0].calls == ["E7"] and ws_now[0].values[6][4].count(MARK) == 1
        # a Submit waiting for approval is not a permission prompt: it still works
        reset("approve", approval_on=True)
        assert "Left for the laptop" in J(tap({"do": "laptop"}))["message"]
        assert typed_laptop == [(1, "/exit", True)]
        # not while Claude Code asks its own question (Enter would pick its first option): nothing written, nothing typed
        reset("approve")
        r = tap({"do": "laptop"})
        assert J(r)["message"] == "Nothing to do." and typed_laptop == [] and not os.path.exists(laptop_file)
        assert ws_now[0].calls == [] and opened == []
        # not at the finish question either (that one has its own No button)
        reset("finish")
        assert J(tap({"do": "laptop"}))["message"] == "Nothing to do." and typed_laptop == [] and not os.path.exists(laptop_file)
        # the NUworks tab's session marks the NUworks tab (state tab "jobs")
        reset("waiting", tab="jobs")
        tap({"do": "laptop"})
        assert opened == [("jobs", False)] and ws_now[0].calls == ["E7"]
        # an unpaired device, a missing header or another origin: nothing
        reset("waiting")
        assert tap({"do": "laptop"}, cookie=None)[0] == 403
        assert tap({"do": "laptop"}, cookie="not-a-key")[0] == 403
        assert tap({"do": "laptop"}, headers={"X-NUauto": None})[0] == 403
        assert tap({"do": "laptop"}, origin="http://evil.example")[0] == 403
        assert tap({"do": "laptop"}, origin="null")[0] == 403
        assert typed_laptop == [] and not os.path.exists(laptop_file) and ws_now[0].calls == []

        # No: do it on the laptop (the finish question): marked, then n typed (so the run records "laptop", never Applied)
        reset("finish")
        r = tap({"do": "finish", "answer": "laptop"})
        assert r[0] == 200 and "Left for the laptop: marked in your sheet" in J(r)["message"] and J(r)["closing"] is True
        assert typed_laptop == [(1, "n", True)] and assist.read_json(laptop_file)["by"] == "Phone"
        assert ws_now[0].values[6][4].count(MARK) == 1 and ws_now[0].values[6][3] == "Approved"
        att = assist.read_json(os.path.join(run, assist.ATTENTION))
        assert att["kind"] == "answered" and att["answer"] == "n"
        assert "leaving the row as it was" in J(get("/api/assistant/1", cookie=KEY))["text"]
        for kind in ("waiting", "approve"):                                   # only at the finish question
            reset(kind)
            r = tap({"do": "finish", "answer": "laptop"})
            assert J(r)["message"] == "Nothing to do." and typed_laptop == [] and not os.path.exists(laptop_file), kind
            assert ws_now[0].calls == [], kind
        # a wrong answer at the finish question: nothing typed
        reset("finish")
        assert J(tap({"do": "finish", "answer": "maybe"}))["message"] == "Nothing to do." and typed_laptop == []

        # mark_laptop on its own: what it tells you, and that it never raises
        def mark(**over):
            reset("waiting", **over)
            return review.mark_laptop(review.session(1), "Phone")
        LEFT = "Left for the laptop: marked in your sheet, and the homelab won't start it again."
        assert mark() == LEFT and ws_now[0].values[6][4].startswith("match 90% | " + MARK)
        ws_now[0] = fresh_sheet(status="Needs Human", notes="External application: acme.example -> https://acme.example/j")
        assert review.mark_laptop(review.session(1), "Phone") == LEFT
        assert ws_now[0].values[6][4].startswith("External application: acme.example") and MARK in ws_now[0].values[6][4]
        # the row now holds another job (url differs): not touched, you are told, the file is still written
        reset("waiting")
        ws_now[0] = fresh_sheet(url="https://jobs.example.org/another")
        msg = review.mark_laptop(review.session(1), "Phone")
        assert msg == "Left for the laptop, but its sheet row has changed: mark it yourself." and ws_now[0].calls == []
        assert os.path.exists(laptop_file)
        # no row_url in the state (an older session): the company must match
        reset("waiting", row_url="")
        ws_now[0] = fresh_sheet(company="Someone Else")
        assert "sheet row has changed" in review.mark_laptop(review.session(1), "Phone") and ws_now[0].calls == []
        reset("waiting", row_url="")
        assert review.mark_laptop(review.session(1), "Phone") == LEFT and ws_now[0].calls == ["E7"]
        # a row that is gone (cleared / shifted), or no row number for the session
        reset("waiting")
        ws_now[0] = CellWS([])
        assert "sheet row has changed" in review.mark_laptop(review.session(1), "Phone")
        reset("waiting")
        sess = review.session(1)
        sess["info"] = {**sess["info"], "row": None}
        assert "sheet row has changed" in review.mark_laptop(sess, "Phone")
        # a row that is not Approved / Needs Human (Applied, say), or a sheet that can't be read: a message, not an exception
        reset("waiting")
        ws_now[0] = fresh_sheet(status="Applied")
        msg = review.mark_laptop(review.session(1), "Phone")
        assert msg == "Left for the laptop; the sheet could not be marked (SheetError)." and ws_now[0].calls == []
        reset("waiting")
        ws_now[0] = RuntimeError("Google is down")
        msg = review.mark_laptop(review.session(1), "Phone")
        assert msg == "Left for the laptop; the sheet could not be marked (RuntimeError)." and os.path.exists(laptop_file)
        assert "Google" not in msg
        # and through the app the same failure is a normal answer: the session is still ended
        reset("waiting")
        ws_now[0] = RuntimeError("Google is down")
        r = tap({"do": "laptop"})
        assert r[0] == 200 and "could not be marked" in J(r)["message"] and typed_laptop == [(1, "/exit", True)]
        assert "Google" not in r[2].decode()
    finally:
        sheet.open_other, sheet.open_worksheet = real_sheet
        review.type_into = real_type_into
        review.end_session = real_end
        for name in (laptop_file,):
            if os.path.exists(name):
                os.remove(name)
        assist.write_private(os.path.join(run, "state.json"), state)

    # a POST whose body can't be read as JSON: refused or answered, never a crash; the server keeps serving
    def raw_post(path, headers, cookie=None, body=b"", timeout=5):
        conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=timeout)
        conn.putrequest("POST", path, skip_host=True, skip_accept_encoding=True)
        for k, v in {"Host": HOST, **headers}.items():
            conn.putheader(k, v)
        if cookie:
            conn.putheader("Cookie", f"{review.COOKIE}={cookie}")
        conn.endheaders(body)
        r = conn.getresponse()
        out = (r.status, r.read())
        conn.close()
        return out

    page_headers = {"X-NUauto": "1", "Origin": ORIGIN, "Content-Type": "application/json"}
    for bad in ("abc", "-5", "", "1e3", " "):                                        # no crash, treated as no body
        # (no readable length: not an object, 400; an empty length is no body = {}: no slot, 409)
        assert raw_post("/api/assistant", {**page_headers, "Content-Length": bad}, cookie=KEY)[0] == (409 if not bad.strip() else 400), bad
        assert raw_post("/api/assistant", {**page_headers, "Content-Length": bad})[0] == 403, bad              # (not paired)
        assert raw_post("/pair", {"Content-Length": bad})[0] == 403, bad
    assert raw_post("/api/assistant", {**page_headers, "Content-Length": "9"}, cookie=KEY, body=b"{not json")[0] == 400
    assert raw_post("/api/assistant", {**page_headers, "Content-Length": "2"}, cookie=KEY, body=b"[]")[0] == 400
    assert raw_post("/api/assistant", {**page_headers, "Content-Length": "5000000"}, cookie=KEY)[0] == 400   # too big: not read
    # a client that promises a body and goes away leaves nothing stuck: the next request is answered
    with socket.create_connection(("127.0.0.1", PORT), timeout=5) as sk:
        head = "".join(f"{k}: {v}\r\n" for k, v in {"Host": HOST, **page_headers, "Content-Length": "5000",
                                                    "Cookie": f"{review.COOKIE}={KEY}"}.items())
        sk.sendall(f"POST /api/assistant HTTP/1.1\r\n{head}\r\n".encode())
        sk.shutdown(socket.SHUT_WR)                                                    # ... and sends none of it
        assert sk.recv(200).startswith(b"HTTP/1.1 409")                                # (an empty body: no slot)
    assert get("/api/assistant/1", cookie=KEY)[0] == 200                               # still serving

    # the page in a real browser: Firefox's own form posts and fetches (Origin, cookies, redirects), not hand-made requests
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        try:
            browser = pw.firefox.launch(headless=True)
        except Exception:
            pw.stop()
            raise
    except Exception as e_:
        print(f"(skipped the browser checks: Firefox could not start: {type(e_).__name__})")
        browser = None
    if browser:
        from http.server import BaseHTTPRequestHandler, HTTPServer
        for name in (assist.DECISION, assist.SHOT):
            if os.path.exists(os.path.join(run, name)):
                os.remove(os.path.join(run, name))
        assist.write_private(os.path.join(run, assist.APPROVAL), approval)
        assist.write_private(os.path.join(run, assist.ATTENTION), {"kind": "waiting", "since": time.time(), "time": time.time(), "said": ""})
        assist.write_private(os.path.join(run, assist.MESSAGE), b"Please answer this question <b>now</b>")
        typed_in_browser = []
        real_type_into = review.type_into
        review.type_into = lambda sess, text_: typed_in_browser.append((sess["slot"], text_))   # no tmux for the browser checks
        evil = None
        try:
            page = browser.new_context().new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            # the page before pairing: its code is public, its data is not (it says so)
            page.goto(f"{ORIGIN}/")
            page.wait_for_selector("[data-testid=gone]")    # the page knows it is not paired (it got the 403)
            page.wait_for_timeout(1500)
            assert "not paired" in page.inner_text("#view")                 # the pairing help stays on screen
            page.goto(f"{ORIGIN}/#/assistant/1")                   # (an old Discord link, opened before pairing)
            page.wait_for_timeout(500)
            assert page.locator("[data-testid=as-approve]").count() == 0 and "Acme" not in page.inner_text("body")
            assert "not paired" in page.inner_text("#view")
            page.goto("about:blank")
            page.goto(f"{ORIGIN}/#/assistant/1")                   # opened fresh: the same
            page.wait_for_selector("[data-testid=gone]")
            page.wait_for_timeout(1000)
            assert "not paired" in page.inner_text("#view") and page.locator("[data-testid=as-gone]").count() == 0
            # pairing through the form: GET shows it, the button pairs this browser
            before = device_count()
            page.goto(review.make_pairing("tablet"))
            assert page.locator("input[name=name]").count() == 1 and device_count() == before
            page.fill("input[name=name]", "Test Browser")
            page.click("button:has-text('Pair')")
            page.wait_for_selector("text=Paired")
            assert device_count() == before + 1 and "Test Browser" in [d_["name"] for d_ in assist.read_json(review.DEVICES).values()]
            cookies = {c_["name"]: c_ for c_ in page.context.cookies()}
            assert cookies[review.COOKIE]["httpOnly"] and cookies[review.COOKIE]["sameSite"] == "Strict"
            # the assistant's screen, then Approve (a real fetch: its Origin and X-NUauto must be accepted)
            page.goto(f"{ORIGIN}/#/assistant/1")
            page.wait_for_selector("[data-testid=as-approve]")
            assert page.inner_text("[data-testid=as-status]") == "Ready to submit: click 'Submit'"
            body_text = page.inner_text("[data-testid=as-message]")
            assert "<b>now</b>" in body_text                                       # the agent's text is shown as text
            assert "Acme <b>Robotics</b>" in page.inner_text("#view") and page.locator("#view b", has_text="Robotics").count() == 0
            assert page.locator("#view b", has_text="now").count() == 0                # (no markup came out of any text)
            assert page.locator("[data-testid=as-approval]").inner_text().count("guesses: Graduation = May 2029") == 1
            assert not os.path.exists(os.path.join(run, assist.DECISION))
            page.click("[data-testid=as-approve]")
            page.wait_for_selector("text=Approved: it is submitting now")
            dec = assist.read_json(os.path.join(run, assist.DECISION))
            assert dec["decision"] == "approve" and dec["by"] == "Test Browser" and dec["id"] == "ap1", dec
            # until the guard picks your answer up, the screen says so: no second Approve to tap
            assert page.locator("[data-testid=as-approve]").count() == 0 and "Test Browser" in page.inner_text("[data-testid=as-approval]")
            assert "REVIEW PAGE approve by Test Browser" in open(os.path.join(run, "actions.log")).read()
            os.remove(os.path.join(run, assist.DECISION))                         # (the guard took it)
            page.reload()
            page.wait_for_selector("[data-testid=as-approve]")
            # Decline with a note, the same way
            page.fill("[data-testid=as-note]", "not this one")
            page.click("[data-testid=as-decline]")
            page.wait_for_selector("text=Declined: it will not submit")
            dec = assist.read_json(os.path.join(run, assist.DECISION))
            assert dec["decision"] == "decline" and dec["note"] == "not this one" and dec["by"] == "Test Browser"
            os.remove(os.path.join(run, assist.DECISION))
            # Reply: typed into the terminal (recorded here)
            page.reload()
            page.wait_for_selector("[data-testid=as-reply]")
            page.fill("[data-testid=as-reply]", "hello from firefox")
            page.click("[data-testid=as-send]")
            page.wait_for_selector("[data-testid=toast]:has-text('Sent.')")
            assert typed_in_browser == [(1, "hello from firefox")], typed_in_browser
            assert "REVIEW PAGE reply by Test Browser (18 characters)" in open(os.path.join(run, "actions.log")).read()
            # a Claude Code permission prompt of its own: no reply box, no Approve
            assist.write_private(os.path.join(run, assist.ATTENTION), {"kind": "approve", "since": 1, "time": 1, "said": ""})
            os.remove(os.path.join(run, assist.APPROVAL))
            page.reload()
            page.wait_for_selector("[data-testid=as-prompt]")
            assert page.locator("[data-testid=as-send]").count() == 0 and page.locator("[data-testid=as-approve]").count() == 0
            # the end question: the three answers, and "do it on the laptop" next to End while it works
            assist.write_private(os.path.join(run, assist.ATTENTION), {"kind": "finish", "since": 1, "time": 1, "said": ""})
            page.reload()
            page.wait_for_selector("[data-testid=as-yes]")
            assert page.locator("[data-testid=as-no]").count() == 1 and page.locator("[data-testid=as-no-laptop]").count() == 1
            assert page.locator("[data-testid=as-laptop]").count() == 0 and page.locator("[data-testid=as-send]").count() == 0
            assist.write_private(os.path.join(run, assist.ATTENTION), {"kind": "waiting", "since": 1, "time": 1, "said": ""})
            page.reload()
            page.wait_for_selector("[data-testid=as-exit]")
            assert page.locator("[data-testid=as-laptop]").count() == 1 and page.locator("[data-testid=as-interrupt]").count() == 1
            # a slot nobody runs in
            page.goto(f"{ORIGIN}/#/assistant/3")
            page.wait_for_selector("[data-testid=as-gone]")
            # an old Discord link lands on the screen
            page.goto(f"{ORIGIN}/s/1")
            page.wait_for_selector("[data-testid=as-status]")
            assert page.url.endswith("/#/assistant/1")
            # a page on another origin cannot press the buttons, even with this browser's cookie (the same site to SameSite):
            # a form can't send JSON, and a fetch with the page's headers is stopped by the browser (no CORS answer)
            assist.write_private(os.path.join(run, assist.APPROVAL), approval)
            assist.write_private(os.path.join(run, assist.ATTENTION), {"kind": "waiting", "since": 1, "time": 1, "said": ""})
            attack_html = (f"<form id=f method=post enctype='text/plain' action='{ORIGIN}/api/assistant'>"
                           "<input name='{\"slot\":1,\"do\":\"approve\",\"id\":\"ap1\",\"x\":\"' value='\"}'>"
                           "<button id=go>go</button></form>"
                           f"<script>window.result = 'pending'; fetch('{ORIGIN}/api/assistant', {{method: 'POST', credentials: 'include',"
                           " headers: {'Content-Type': 'application/json', 'X-NUauto': '1'},"
                           " body: JSON.stringify({slot: 1, do: 'approve', id: 'ap1'})})"
                           ".then(() => { window.result = 'sent'; }).catch(() => { window.result = 'blocked'; });</script>").encode()

            class Evil(BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Content-Length", str(len(attack_html)))
                    self.end_headers()
                    self.wfile.write(attack_html)

                def log_message(self, *a):
                    pass
            evil = HTTPServer(("127.0.0.1", 0), Evil)
            threading.Thread(target=evil.serve_forever, daemon=True).start()
            page.goto(f"http://127.0.0.1:{evil.server_address[1]}/")
            page.wait_for_function("window.result !== 'pending'")
            assert page.evaluate("window.result") == "blocked" and not os.path.exists(os.path.join(run, assist.DECISION))
            with page.expect_navigation():
                page.click("#go")
            assert "forbidden" in page.inner_text("body") and not os.path.exists(os.path.join(run, assist.DECISION))
            # a page that is not a POST at all (a link followed) never changes anything
            page.goto(f"{ORIGIN}/api/assistant/1")
            assert not os.path.exists(os.path.join(run, assist.DECISION))
            assert not [e_ for e_ in errors if "assistant" in e_], errors
        finally:
            review.type_into = real_type_into
            browser.close()
            pw.stop()
            if evil:
                evil.shutdown()

    # type_into goes only to the assistant's own pane, and never into a shell (real tmux, private socket)
    if HAVE_TMUX:
        tm = lambda *a: subprocess.run(["tmux", *a], capture_output=True, text=True)  # noqa: E731
        S1, S2, S3 = (f"nuautotest-{random.randrange(10**8)}" for _ in range(3))
        try:
            def pane_text(target_):
                return tm("capture-pane", "-p", "-J", "-t", target_).stdout

            assert tm("new-session", "-d", "-s", S1, "-x", "120", "-y", "30", "sh -i").returncode == 0          # a shell
            shell_s = {"state": {"tmux": S1}, "run": run}
            time.sleep(0.5)
            try:
                review.type_into(shell_s, "rm -rf /tmp/never")
                raise AssertionError("typed into a shell")
            except ValueError as e:
                assert "sh now" in str(e) and "nothing was typed" in str(e), e
            time.sleep(0.3)
            assert "never" not in pane_text(f"={S1}:")
            assert "sh" in review.SHELLS and "bash" in review.SHELLS and "zsh" in review.SHELLS
            # a program that ended (the pane is kept dead): refused too
            assert tm("new-session", "-d", "-s", S2, "sleep 1", ";", "set-option", "-t", f"={S2}:", "remain-on-exit", "on").returncode == 0
            time.sleep(2)
            assert tm("display-message", "-p", "-t", f"={S2}:", "#{pane_dead}").stdout.strip() == "1"
            try:
                review.type_into({"state": {"tmux": S2}, "run": run}, "hello")
                raise AssertionError("typed into a dead pane")
            except ValueError as e:
                assert "nothing was typed" in str(e)
            # two windows: the shell is the active one, the assistant (cat) is the other: its recorded pane is the target
            assert tm("new-session", "-d", "-s", S3, "-x", "120", "-y", "30", "cat").returncode == 0
            cat_pane = tm("list-panes", "-t", f"={S3}:0", "-F", "#{pane_id}").stdout.strip()
            assert re.fullmatch(r"%\d+", cat_pane)
            assert tm("new-window", "-t", f"={S3}:", "sh -i").returncode == 0                                    # now the active window
            shell_pane = tm("list-panes", "-t", f"={S3}:1", "-F", "#{pane_id}").stdout.strip()
            assert shell_pane != cat_pane
            time.sleep(0.5)
            by_name = {"state": {"tmux": S3}, "run": run}                                                         # no pane recorded
            assert review.target(by_name) == f"={S3}:"
            try:
                review.type_into(by_name, "into the shell?")
                raise AssertionError("typed into the active pane (a shell)")
            except ValueError:
                pass
            pinned = {"state": {"tmux": S3, "tmux_pane": cat_pane}, "run": run}
            assert review.target(pinned) == cat_pane
            for _ in range(50):  # started through your shell: wait until the pane runs cat itself
                if tm("display-message", "-p", "-t", cat_pane, "#{pane_current_command}").stdout.strip() == "cat":
                    break
                time.sleep(0.1)
            review.type_into(pinned, "for the assistant")
            time.sleep(0.4)
            assert "for the assistant" in pane_text(cat_pane) and "for the assistant" not in pane_text(shell_pane)
            assert "for the assistant" in review.terminal(pinned) and "for the assistant" not in review.terminal(by_name)
            assert "into the shell" not in pane_text(shell_pane)
            # an odd pane value is ignored (falls back to the session), a gone pane refuses
            assert review.target({"state": {"tmux": S3, "tmux_pane": "%1; kill-server"}, "run": run}) == f"={S3}:"
            assert review.target({"state": {"tmux": S3, "tmux_pane": "x"}, "run": run}) == f"={S3}:"
            try:
                review.type_into({"state": {"tmux": S3, "tmux_pane": "%99999"}, "run": run}, "x")
                raise AssertionError("typed into a pane that is not there")
            except ValueError:
                pass
            # and through the app (the pinned pane): Reply reaches the assistant only
            assist.write_private(os.path.join(run, assist.ATTENTION), {"kind": "waiting", "since": 1, "time": 1, "said": ""})
            assist.write_private(os.path.join(run, "state.json"), {**state, "tmux": S3, "tmux_pane": cat_pane})
            r = post("/api/assistant", {"slot": 1, "do": "reply", "text": "via the page"}, cookie=KEY)
            assert r[0] == 200 and J(r)["message"] == "Sent."
            time.sleep(0.4)
            assert "via the page" in pane_text(cat_pane) and "via the page" not in pane_text(shell_pane)
            assist.write_private(os.path.join(run, "state.json"), {**state, "tmux": S1})
            r = post("/api/assistant", {"slot": 1, "do": "reply", "text": "to the shell"}, cookie=KEY)
            assert r[0] == 200 and J(r)["message"].startswith("Could not do that") and "nothing was typed" in J(r)["message"], r[2]
            assist.write_private(os.path.join(run, "state.json"), {**state, "tmux": None})
            assist.write_private(os.path.join(run, "state.json"), state)
        finally:
            for n_ in (S1, S2, S3):
                tm("kill-session", "-t", f"={n_}")

    # unpairing: the cookie stops working (by hand on the homelab: review.main; the app's list follows)
    out, sys.stdout = sys.stdout, io.StringIO()
    try:
        review.main(["unpair", "Phone"])
        said = sys.stdout.getvalue()
        review.main(["devices"])
        listing = sys.stdout.getvalue()
        review.main(["unpair", "--all"])
        review.main(["devices"])
        empty = sys.stdout.getvalue()
        review.main(["pair", "tablet"])
        paired = sys.stdout.getvalue()
    finally:
        sys.stdout = out
    assert "Forgot 1 device." in said and (not browser or "Test Browser" in listing) and "Forgot" in empty
    assert "No paired devices" in empty and f"{ORIGIN}/pair?c=" in paired
    assert device_count() == 0
    r = get("/api/assistant/1", cookie=KEY)
    assert r[0] == 403 and J(r)["pair"] is True
    assert post("/api/assistant", {"slot": 1, "do": "approve", "id": "ap1"}, cookie=KEY)[0] == 403
    assert get("/api/devices", cookie=KEY)[0] == 403 and post("/api/action", {"kind": "pair_device"}, cookie=KEY)[0] == 403
    # (a cookie that was valid and is not now: the page is told to pair again; the public code still loads)
    assert get("/", cookie=KEY)[0] == 200 and get(f"/pair?c={parse_qs(urlparse(paired.split('  ')[-1].strip()).query)['c'][0]}", cookie=KEY)[0] == 200
finally:
    for h in holders:
        h.kill()
        h.wait()
    srv.shutdown()
    subprocess.run(["tmux", "kill-session", "-t", f"={TMUX_NAME}"], capture_output=True) if HAVE_TMUX else None

# a long reply still sitting in Claude Code's input box (its Enter taken as part of a paste) is seen, so it is sent again
real_terminal = review.terminal
try:
    box = ("● Earlier answer\n─────────\n❯ type: I have basic experience with C through a couple\n  projects in cybersecurity\n"
           "─────────\n  ⏸ manual mode on")
    review.terminal = lambda s_: box
    assert review.still_typed({}, "type: I have basic experience")
    assert not review.still_typed({}, "Earlier answer")                   # above the box: already sent
    review.terminal = lambda s_: "● Done\n─────────\n❯ \n─────────\n  ⏸ manual mode on"
    assert not review.still_typed({}, "type: I have basic experience")   # the box is empty: it went
    review.terminal = lambda s_: "Did you press Submit for row 1? [y = yes / n = no]: "
    assert not review.still_typed({}, "y")                                 # no Claude Code prompt: nothing to resend
finally:
    review.terminal = real_terminal

print("All review checks passed.")
