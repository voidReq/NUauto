"""Offline checks for review.py (the phone review page for the assistant on the homelab). Run: python tests/test_review.py

No ssh, no network beyond 127.0.0.1: the real HTTP handler runs on a free local port, the sessions are lock files in a
temp folder, and the terminal is a real tmux session when tmux is installed (else those checks are skipped).
"""
import http.client
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

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    PORT = sk.getsockname()[1]
review.PORT = PORT
config.LOCAL = {**config.LOCAL, "web_listen_host": "127.0.0.1"}
HOST = f"127.0.0.1:{PORT}"
ORIGIN = f"http://{HOST}"
HAVE_TMUX = shutil.which("tmux") is not None


def device_count():
    return len(assist.read_json(review.DEVICES, {}))


# ---- pairing: one use, ten minutes
link = review.make_pairing("phone")
assert link.startswith(f"{ORIGIN}/pair?c=")
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
assert review.form_token(key) == review.form_token(key) and review.form_token(key) != review.form_token(key2)
assert len(review.form_token(key)) == 32 and key not in review.form_token(key)
with review._locked(review.DEVICES) as d:
    d.clear()
assert review.device(key) is None

# ---- the page: a real server, a fake session
srv = ThreadingHTTPServer(("127.0.0.1", PORT), review.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()


def req(method, path, headers=None, form=None, cookie=None, host=HOST):
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
    h = {"Host": host, **(headers or {})}
    if cookie:
        h["Cookie"] = f"{review.COOKIE}={cookie}"
    body = None
    if form is not None:
        body = urlencode(form)
        h["Content-Type"] = "application/x-www-form-urlencoded"
    conn.request(method, path, body=body, headers=h)
    r = conn.getresponse()
    data = r.read()
    out = (r.status, {k.lower(): v for k, v in r.getheaders()}, data)
    conn.close()
    return out


def text(resp):
    return resp[2].decode()


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
    assert req("GET", "/", host="evil.example")[0] == 400 and req("POST", "/pair", form={"c": "x"}, host="evil.example")[0] == 400
    assert req("GET", "/", host=f"localhost:{PORT}")[0] == 400                 # exactly the Tailscale address, like the Mark page
    assert req("GET", "/")[0] == 403 and req("GET", "/s/1")[0] == 403 and req("GET", "/s/1/v")[0] == 403
    assert req("GET", "/s/1/shot.png")[0] == 403 and req("GET", "/", cookie="not-a-key")[0] == 403
    assert req("GET", "/poll.js")[0] == 200                                    # the page's own script: nothing in it

    # pairing: GET only shows a form; the code is used up by the POST
    code = parse_qs(urlparse(review.make_pairing("phone")).query)["c"][0]
    r = req("GET", f"/pair?c={code}")
    assert r[0] == 200 and "<form method=post" in text(r) and f"value='{code}'" in text(r) and "Set-Cookie" not in r[1]
    assert req("GET", f"/pair?c={code}")[0] == 200 and device_count() == 0     # still unused
    assert req("POST", "/pair", form={"c": "wrong"})[0] == 403 and device_count() == 0
    assert req("POST", "/pair", headers={"Origin": "http://evil.example"}, form={"c": code})[0] == 403 and device_count() == 0
    r = req("POST", "/pair", form={"c": code, "name": "Phone"})
    assert r[0] == 200 and "Paired" in text(r) and device_count() == 1
    cookie_line = r[1]["set-cookie"]
    assert cookie_line.startswith(f"{review.COOKIE}=") and "HttpOnly" in cookie_line and "SameSite=Strict" in cookie_line
    KEY = cookie_line.split(";")[0].split("=", 1)[1]
    assert review.device(KEY)["name"] == "Phone"
    assert req("POST", "/pair", form={"c": code, "name": "Phone"})[0] == 403 and device_count() == 1   # used up
    assert "Not paired" in text(req("POST", "/pair", form={"c": code}))
    for m in ("GET", "POST"):  # an old pairing link opened again on a paired device: straight to the page
        r = req(m, f"/pair?c={code}", cookie=KEY) if m == "GET" else req(m, "/pair", form={"c": code}, cookie=KEY)
        assert r[0] == 303 and r[1]["location"] == "/" and device_count() == 1, (m, r[0])

    # headers on every answer
    for r in (req("GET", "/", cookie=KEY), req("GET", "/"), req("GET", "/pair?c=x"), req("GET", "/poll.js")):
        h = r[1]
        assert h["cache-control"] == "no-store" and h["x-frame-options"] == "DENY" and h["x-content-type-options"] == "nosniff"
        assert "default-src 'none'" in h["content-security-policy"] and "frame-ancestors 'none'" in h["content-security-policy"]
        assert h["referrer-policy"] == "same-origin"   # (no-referrer makes Firefox send "Origin: null" on form posts)

    # the list: this session only (the other's run folder is outside logs/), names escaped
    r = req("GET", "/", cookie=KEY)
    page = text(r)
    assert r[0] == 200 and "Firmware Intern" in page and "/s/1" in page and "Ready to submit: click &#x27;Submit&#x27;" in page
    assert "Hidden Co" not in page and "/s/2" not in page and "<b>Robotics</b>" not in page and "&lt;b&gt;Robotics&lt;/b&gt;" in page
    assert req("GET", "/s/2", cookie=KEY)[0] == 404 and req("GET", "/s/3", cookie=KEY)[0] == 404
    assert req("GET", "/s/0", cookie=KEY)[0] == 404 and req("GET", "/nope", cookie=KEY)[0] == 404
    assert req("GET", "/s/1/other", cookie=KEY)[0] == 404 and req("GET", "/s/2/v", cookie=KEY)[0] == 404

    # the list reloads by itself when sessions change (/v); a queue row a session here works on links to it, in green
    assert "data-poll='/v'" in text(req("GET", "/", cookie=KEY))
    assert text(req("GET", "/v", cookie=KEY)).strip() == review.index_version() and req("GET", "/v")[0] == 403
    from nuauto import remote  # noqa: E402

    def cand(row, host):
        return {"tab": "other", "row": row, "url": "https://x.example/a", "host": host, "company": "Q Co", "title": "T",
                "closes": None, "match": 50}
    real_queue = remote.queue_inputs
    remote.queue_inputs = lambda: ([cand(7, "acme.example"), cand(9, "b.example"), cand(11, "c.example")],
                                   [{"tab": "other", "row": 7, "host": "acme.example"}],           # slot 1's session (row 7)
                                   [{"tab": "other", "row": 9, "host": "b.example"}], {}, 2, None)  # the laptop's claim on row 9
    try:
        page_q = text(req("GET", "/", cookie=KEY))
        assert "<a class=ok href='/s/1'>A session is working on it" in page_q    # row 7 is slot 1's session
        assert "The laptop's assistant is working on it" in page_q              # row 9: a laptop claim, no session here
        assert page_q.count("Start now") == 1 and "name=row value='11'" in page_q   # only row 11 can start
        assert "1 slot free" not in page_q and "2 slots free" in page_q
    finally:
        remote.queue_inputs = real_queue

    # the session page: what it will do, the message (escaped)
    r = req("GET", "/s/1", cookie=KEY)
    page = text(r)
    # while it works or waits on a Submit, the screen and terminal refresh in place (/s/1/term: paired devices only)
    assert "data-live='/s/1'" in page and "id=live-term" in page    # (live-shot only with a browser view)
    assert req("GET", "/s/1/term", cookie=KEY)[0] == 200 and req("GET", "/s/1/term")[0] == 403
    # like a chat, newest at the bottom: the terminal, the latest message, the Submit question right under it, then
    # the reply box with End session; opens at the Submit question
    assert page.index("Its terminal") < page.index("Its latest message") < page.index("Ready to submit") < page.index("id=reply")
    assert page.index("id=reply") < page.index(">End session<") and "data-scroll='approve'" in page
    assert r[0] == 200 and "Ready to submit" in page and "click &#x27;Submit&#x27;" in page and "https://acme.example/apply" in page
    assert "guesses: Graduation = May 2029" in page and "Approve: submit" in page and "Decline" in page
    assert EVIL not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page, "the agent's message is escaped"
    assert page.count("<script") == 1 and "<script src='/poll.js'>" in page   # only the page's own script
    assert "shot.png" not in page                                              # no screenshot of that question yet
    token = re.search(r"name=t value='([0-9a-f]+)'", page).group(1)
    assert token == review.form_token(KEY)

    # answering: a paired device's form token, this server's Origin
    answer = {"t": token, "id": "ap1", "d": "approve"}
    assert req("POST", "/s/1/approve", form={k: v for k, v in answer.items() if k != "t"}, cookie=KEY)[0] == 403
    assert req("POST", "/s/1/approve", form={**answer, "t": "0" * 32}, cookie=KEY)[0] == 403
    assert req("POST", "/s/1/approve", form={**answer, "t": review.form_token(key2)}, cookie=KEY)[0] == 403
    assert req("POST", "/s/1/approve", form=answer)[0] == 403                                 # no cookie
    assert req("POST", "/s/1/approve", headers={"Origin": "http://evil.example"}, form=answer, cookie=KEY)[0] == 403
    assert req("POST", "/s/1/approve", headers={"Origin": "null"}, form=answer, cookie=KEY)[0] == 403
    assert not os.path.exists(os.path.join(run, assist.DECISION))
    r = req("POST", "/s/1/approve", headers={"Origin": ORIGIN}, form=answer, cookie=KEY)
    assert r[0] == 303 and r[1]["location"].startswith("/s/1?m=Approved")
    dec = assist.read_json(os.path.join(run, assist.DECISION))
    assert dec["id"] == "ap1" and dec["decision"] == "approve" and dec["by"] == "Phone", dec
    assert "REVIEW PAGE approve by Phone" in open(os.path.join(run, "actions.log")).read()
    os.remove(os.path.join(run, assist.DECISION))
    r = req("POST", "/s/1/approve", form={**answer, "d": "decline", "note": "wrong\nphone  number"}, cookie=KEY)
    dec = assist.read_json(os.path.join(run, assist.DECISION))
    assert r[0] == 303 and dec["decision"] == "decline" and dec["note"] == "wrong phone number" and dec["by"] == "Phone"
    os.remove(os.path.join(run, assist.DECISION))
    r = req("POST", "/s/1/approve", form={**answer, "id": "old-id"}, cookie=KEY)              # another question: gone
    assert r[0] == 303 and "gone" in r[1]["location"] and not os.path.exists(os.path.join(run, assist.DECISION))
    assert req("POST", "/s/1/approve", form={**answer, "d": "maybe"}, cookie=KEY)[0] == 303   # not approve / decline: nothing
    assert not os.path.exists(os.path.join(run, assist.DECISION))
    assert req("POST", "/s/2/approve", form=answer, cookie=KEY)[1]["location"] == "/"         # the hidden session: nothing

    # the poll: one version string that changes with the message and the question
    v1 = text(req("GET", "/s/1/v", cookie=KEY))
    assert re.fullmatch(r"[0-9a-f]{16}", v1) and v1 in page and text(req("GET", "/s/1/v", cookie=KEY)) == v1
    assist.write_private(os.path.join(run, assist.MESSAGE), b"a new question?")
    v2 = text(req("GET", "/s/1/v", cookie=KEY))
    assert v2 != v1
    assist.write_private(os.path.join(run, assist.APPROVAL), {**approval, "waiting": False})
    assert text(req("GET", "/s/1/v", cookie=KEY)) != v2
    assist.write_private(os.path.join(run, assist.APPROVAL), approval)

    # the screen when it asked
    assert req("GET", "/s/1/shot.png", cookie=KEY)[0] == 404
    PNG = b"\x89PNG\r\n\x1a\nfake"
    assist.write_private(os.path.join(run, assist.SHOT), PNG)
    r = req("GET", "/s/1/shot.png?a=ap1", cookie=KEY)
    assert r[0] == 200 and r[1]["content-type"] == "image/png" and r[2] == PNG
    assert "shot.png?a=ap1" in text(req("GET", "/s/1", cookie=KEY))
    assert req("GET", "/s/1/shot.png?live=1", cookie=KEY)[0] == 404            # live: the screen itself (none in a test)
    assert req("GET", "/s/2/shot.png", cookie=KEY)[0] == 404

    # the browser's link and password only on the page for a paired device
    assist.write_private(os.path.join(run, "view.json"), {"url": "http://100.64.0.1:6081/vnc.html", "password": "pw123456"})
    page = text(req("GET", "/s/1", cookie=KEY))
    assert "http://100.64.0.1:6081/vnc.html" in page and "pw123456" in page and "shot.png?live=" in page
    assert "pw123456" not in text(req("GET", "/")) and "pw123456" not in text(req("GET", "/", cookie=KEY))
    os.remove(os.path.join(run, "view.json"))

    # no tmux for the session: a reply says so, nothing is typed
    assist.write_private(os.path.join(run, "state.json"), {**state, "tmux": None})
    r = req("POST", "/s/1/reply", form={"t": token, "text": "hi"}, cookie=KEY)
    assert r[0] == 303 and "tmux" in r[1]["location"] and "Could" in r[1]["location"], r[1]["location"]
    assist.write_private(os.path.join(run, "state.json"), state)
    assert "Nothing" in req("POST", "/s/1/reply", form={"t": token, "text": "  "}, cookie=KEY)[1]["location"]
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
            # the page's Reply, Finish and keys
            r = req("POST", "/s/1/reply", form={"t": token, "text": "typed by reply"}, cookie=KEY)
            assert r[0] == 303 and "Sent" in r[1]["location"]
            r = req("POST", "/s/1/finish", form={"t": token, "a": "y"}, cookie=KEY)              # not at the finish question
            assert "Nothing" in r[1]["location"]
            assist.write_private(os.path.join(run, assist.ATTENTION), {"kind": "finish", "since": 1, "time": 1, "said": ""})
            r = req("POST", "/s/1/finish", form={"t": token, "a": "y"}, cookie=KEY)
            assert "Thanks" in r[1]["location"]
            page = text(req("GET", "/s/1", cookie=KEY))
            assert "The session ended" in page and "Yes, it went through" in page and "Reply" not in page   # no reply box then
            assert "did it go through?" in text(req("GET", "/", cookie=KEY))
            r = req("POST", "/s/1/key", form={"t": token, "k": "exit"}, cookie=KEY)
            assert "Ending" in r[1]["location"]
            time.sleep(0.5)
            pane = review.terminal(review.session(1))
            assert "typed by reply" in pane and "/exit" in pane and pane.count("\ny") >= 1, pane
            log = open(os.path.join(run, "actions.log")).read()
            assert "REVIEW PAGE reply by Phone (14 characters)" in log and "did it go through: y (by Phone)" in log
            assert "ended the session (by Phone)" in log
            assert req("POST", "/s/1/key", form={"t": token, "k": "interrupt"}, cookie=KEY)[0] == 303
            # a site password the run used never shows in the terminal text
            real_passwords = accounts.passwords
            accounts.passwords = lambda sites: ["S3cretPw!x"] if sites else []
            try:
                review.type_into(sess, "my password is S3cretPw!x ok")
                time.sleep(0.3)
                assert "S3cretPw!x" in review.terminal({**sess, "state": {**sess["state"], "password_sites": []}})
                hidden = review.terminal({**sess, "state": {**sess["state"], "password_sites": ["acme.example"]}})
                assert "S3cretPw!x" not in hidden and assist.HIDDEN in hidden and "my password is" in hidden
            finally:
                accounts.passwords = real_passwords
            subprocess.run(["tmux", "kill-session", "-t", f"={TMUX_NAME}"])
            assert review.terminal(sess) == ""                                  # a gone session: nothing, no error
        finally:
            subprocess.run(["tmux", "kill-session", "-t", f"={TMUX_NAME}"], capture_output=True)

    # "do it on the laptop": the page's End and No buttons write laptop.json, mark the sheet row (the real
    # sheet.mark_on_laptop on an in-memory worksheet), then type /exit or n; a failure to mark is a message, never an error
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

    MARK = assist.LAPTOP_MARK
    sheet.open_other, sheet.open_worksheet = fake_open("other"), fake_open("jobs")
    review.type_into = fake_type
    real_end = review.end_session
    review.end_session = lambda sess: fake_type(sess, "/exit")  # (End session types /exit itself, with its pause and retry)
    try:
        # the buttons: the reply card (End session next to End: do it on the laptop), the finish card, none while a prompt waits
        reset("waiting")
        page = text(req("GET", "/s/1", cookie=KEY))
        assert "End: do it on the laptop" in page and "name=k value=laptop" in page and ">End session<" in page
        assert "the homelab never starts it again" in page
        reset("finish")
        page = text(req("GET", "/s/1", cookie=KEY))
        assert "No: do it on the laptop" in page and "name=a value=laptop" in page and "Yes, it went through" in page
        assert "value=laptop data-busy=Saving" in page and "End: do it on the laptop" not in page      # (no reply card then)
        reset("approve")                                                     # Claude Code's own permission question
        page = text(req("GET", "/s/1", cookie=KEY))
        assert "value=laptop" not in page and "do it on the laptop" not in page

        # End: do it on the laptop (the session's working or waiting for you): marked, then /exit typed
        reset("waiting")
        r = req("POST", "/s/1/key", form={"t": token, "k": "laptop"}, cookie=KEY)
        assert r[0] == 303 and "Left for the laptop: marked in your sheet" in unquote(r[1]["location"]), r[1]["location"]
        assert typed_laptop == [(1, "/exit", True)], typed_laptop
        assert oct(os.stat(laptop_file).st_mode & 0o777) == "0o600" and assist.read_json(laptop_file)["by"] == "Phone"
        assert ws_now[0].values[6][4] == f"match 90% | {MARK} (the site showed it a sign-in page with no way in)", ws_now[0].values[6]
        assert ws_now[0].values[6][3] == "Approved" and ws_now[0].calls == ["E7"] and opened == [("other", False)]
        assert "do it on the laptop (by Phone)" in open(os.path.join(run, "actions.log")).read()
        r = req("POST", "/s/1/key", form={"t": token, "k": "laptop"}, cookie=KEY)    # again: still marked once, /exit again is harmless
        assert ws_now[0].calls == ["E7"] and ws_now[0].values[6][4].count(MARK) == 1
        # a Submit waiting for approval is not a permission prompt: it still works
        reset("approve", approval_on=True)
        assert "Left for the laptop" in unquote(req("POST", "/s/1/key", form={"t": token, "k": "laptop"}, cookie=KEY)[1]["location"])
        assert typed_laptop == [(1, "/exit", True)]
        # not while Claude Code asks its own question (Enter would pick its first option): nothing written, nothing typed
        reset("approve")
        r = req("POST", "/s/1/key", form={"t": token, "k": "laptop"}, cookie=KEY)
        assert "Nothing" in r[1]["location"] and typed_laptop == [] and not os.path.exists(laptop_file)
        assert ws_now[0].calls == [] and opened == []
        # the NUworks tab's session marks the NUworks tab (state tab "jobs")
        reset("waiting", tab="jobs")
        req("POST", "/s/1/key", form={"t": token, "k": "laptop"}, cookie=KEY)
        assert opened == [("jobs", False)] and ws_now[0].calls == ["E7"]
        # a missing / forged token or an unpaired device: nothing
        reset("waiting")
        assert req("POST", "/s/1/key", form={"t": "0" * 32, "k": "laptop"}, cookie=KEY)[0] == 403
        assert req("POST", "/s/1/key", form={"t": token, "k": "laptop"})[0] == 403
        assert req("POST", "/s/1/key", form={"t": token, "k": "laptop"}, cookie=KEY, headers={"Origin": "http://evil.example"})[0] == 403
        assert typed_laptop == [] and not os.path.exists(laptop_file) and ws_now[0].calls == []

        # No: do it on the laptop (the finish question): marked, then n typed (so the run records "laptop", never Applied)
        reset("finish")
        r = req("POST", "/s/1/finish", form={"t": token, "a": "laptop"}, cookie=KEY)
        assert r[0] == 303 and "Left for the laptop: marked in your sheet" in unquote(r[1]["location"])
        assert typed_laptop == [(1, "n", True)] and assist.read_json(laptop_file)["by"] == "Phone"
        assert ws_now[0].values[6][4].count(MARK) == 1 and ws_now[0].values[6][3] == "Approved"
        for kind in ("waiting", "approve"):                                   # only at the finish question
            reset(kind)
            r = req("POST", "/s/1/finish", form={"t": token, "a": "laptop"}, cookie=KEY)
            assert "Nothing" in r[1]["location"] and typed_laptop == [] and not os.path.exists(laptop_file) and ws_now[0].calls == [], kind

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
        # and through the page the same failure is a normal answer: the session is still ended
        reset("waiting")
        ws_now[0] = RuntimeError("Google is down")
        r = req("POST", "/s/1/key", form={"t": token, "k": "laptop"}, cookie=KEY)
        assert r[0] == 303 and "could not be marked" in unquote(r[1]["location"]) and typed_laptop == [(1, "/exit", True)]
    finally:
        sheet.open_other, sheet.open_worksheet = real_sheet
        review.type_into = real_type_into
        review.end_session = real_end
        for name in (laptop_file,):
            if os.path.exists(name):
                os.remove(name)
        assist.write_private(os.path.join(run, "state.json"), state)

    # the body of a POST is read only for a paired device (no waiting on a body from anyone else)
    def raw_post(path, headers, cookie=None, body=b""):
        conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=5)
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

    t0 = time.time()
    assert raw_post("/s/1/approve", {"Content-Length": "5000"})[0] == 403          # promises a body, sends none: refused at once
    assert raw_post("/start", {"Content-Length": "5000"}, cookie="not-a-key")[0] == 403
    assert time.time() - t0 < 3
    for bad in ("abc", "-5", "", "1e3", " "):                                       # no crash, treated as no body
        assert raw_post("/s/1/approve", {"Content-Length": bad}, cookie=KEY)[0] == 403, bad     # (no token without a body)
        assert raw_post("/pair", {"Content-Length": bad})[0] == 403, bad
    assert req("GET", "/", cookie=KEY)[0] == 200                                       # still serving

    # the page in a real browser: Firefox's own form posts (Origin, cookies, redirects), not just hand-made requests
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
        decision_path = os.path.join(run, assist.DECISION)
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
            # the session page, then Approve (a real form post: its Origin must be accepted)
            page.goto(f"{ORIGIN}/s/1")
            page.wait_for_selector("text=Approve: submit")
            assert "&lt;b&gt;" not in page.inner_text("body") and "<b>now</b>" in page.inner_text("body")   # shown as text
            page.click("text=Approve: submit")
            page.wait_for_selector("text=Approved.")
            dec = assist.read_json(decision_path)
            assert dec["decision"] == "approve" and dec["by"] == "Test Browser" and dec["id"] == "ap1", dec
            # until the guard picks your answer up, the page says so: no second Approve to tap
            assert "Approved: it is submitting now" in page.inner_text("body") and page.locator("text=Approve: submit").count() == 0
            os.remove(decision_path)                                    # (the guard took it)
            page.goto(f"{ORIGIN}/s/1")
            # Decline with a note, the same way
            page.fill("input[name=note]", "not this one")
            page.click("button:has-text('Decline')")
            page.wait_for_selector("text=Declined.")
            dec = assist.read_json(decision_path)
            assert dec["decision"] == "decline" and dec["note"] == "not this one" and dec["by"] == "Test Browser"
            os.remove(decision_path)
            # Reply: typed into the terminal (recorded here)
            page.goto(f"{ORIGIN}/s/1")
            page.fill("textarea[name=text]", "hello from firefox")
            page.click("button:has-text('Send')")
            page.wait_for_selector("text=Sent.")
            assert typed_in_browser == [(1, "hello from firefox")], typed_in_browser
            assert "REVIEW PAGE reply by Test Browser (18 characters)" in open(os.path.join(run, "actions.log")).read()
            # the index and its Start now flash come back through the browser too
            page.goto(f"{ORIGIN}/")
            assert page.locator("a[href='/s/1']").count() == 1
            # a page on another origin cannot press the buttons, even with this browser's cookie (and a valid token)
            attack_html = (f"<form method=post action='{ORIGIN}/s/1/approve'><input name=t value='{token}'>"
                           "<input name=id value='ap1'><input name=d value='approve'><button id=go>go</button></form>").encode()

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
            assert not os.path.exists(decision_path)
            with page.expect_navigation():
                page.click("#go")
            assert "Wrong origin" in page.inner_text("body") and not os.path.exists(decision_path)
            # a page that is not a form post at all (a link followed) never changes anything
            page.goto(f"{ORIGIN}/s/1/approve")
            assert not os.path.exists(decision_path)
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
            # and through the page (the pinned pane): Reply reaches the assistant only
            assist.write_private(os.path.join(run, "state.json"), {**state, "tmux": S3, "tmux_pane": cat_pane})
            r = req("POST", "/s/1/reply", form={"t": token, "text": "via the page"}, cookie=KEY)
            assert r[0] == 303 and "Sent" in r[1]["location"]
            time.sleep(0.4)
            assert "via the page" in pane_text(cat_pane) and "via the page" not in pane_text(shell_pane)
            assist.write_private(os.path.join(run, "state.json"), {**state, "tmux": S1})
            r = req("POST", "/s/1/reply", form={"t": token, "text": "to the shell"}, cookie=KEY)
            assert "nothing+was+typed" in r[1]["location"] or "nothing%20was%20typed" in r[1]["location"], r[1]["location"]
            assist.write_private(os.path.join(run, "state.json"), state)
        finally:
            for n_ in (S1, S2, S3):
                tm("kill-session", "-t", f"={n_}")

    # unpairing: the cookie stops working
    config.IS_SERVER, config.REMOTE_ASSIST = True, True
    import io
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
    assert req("GET", "/", cookie=KEY)[0] == 403 and req("POST", "/s/1/approve", form=answer, cookie=KEY)[0] == 403
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
