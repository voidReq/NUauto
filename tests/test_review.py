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
from urllib.parse import parse_qs, urlencode, urlparse

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

    # the session page: what it will do, the message (escaped)
    r = req("GET", "/s/1", cookie=KEY)
    page = text(r)
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
            os.remove(decision_path)
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

print("All review checks passed.")
