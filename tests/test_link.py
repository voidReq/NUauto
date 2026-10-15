"""The laptop app's link to NUauto's page on the homelab (link.py, gui.homelab and the /api/homelab/ routes).
Run: python tests/test_link.py

The homelab's page is `nuauto gui --demo --remote` (the same server and page as the homelab's, on 127.0.0.1, on demo data
with made-up assistant sessions); the laptop's app is the real gui.Handler in this process (laptop mode, a temp state
folder), linked to it the way Settings > Link this app links it (a one-time pairing code, here made by the demo page
instead of over ssh). Checks: pairing keeps the key in a 600 file and nowhere else; calls carry the page's own refusals;
the app's proxy routes (a session's screen, a tap on it, Start on the homelab, an Apply run there with its question and
log); an unpairing on the homelab forgets the key here. No ssh, no network beyond 127.0.0.1.
"""
import http.client
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = tempfile.mkdtemp(prefix="nuauto-test-link-")
ENV = {k: v for k, v in os.environ.items() if k != "NUAUTO_STATE_DIR"}
ENV.update({"NUAUTO_DEMO_HEADLESS": "1", "NUAUTO_DEMO_PACE": "0.02", "PYTHONUNBUFFERED": "1", "TMPDIR": STATE})
os.environ["NUAUTO_STATE_DIR"] = os.path.join(STATE, "laptop")  # this process: the laptop's app, its own temp folder

from nuauto import config  # noqa: E402

assert config.LOCAL_DIR == os.path.join(STATE, "laptop", "local") and not config.DEMO
os.makedirs(config.LOCAL_DIR, mode=0o700, exist_ok=True)

from nuauto import gui, link, review  # noqa: E402

srv = subprocess.Popen([sys.executable, "-m", "nuauto", "gui", "--demo", "--remote", "--no-open"], env=ENV, cwd=ROOT,
                       stdout=subprocess.PIPE, stderr=open(os.path.join(STATE, "stderr.log"), "w"), text=True)
ready = json.loads(srv.stdout.readline())
PORT, LINK = ready["port"], ready["url"]
ORIGIN = f"http://127.0.0.1:{PORT}"


def there(method, path, body=None, cookie=None, form=None):
    """A request to the homelab's page as a browser would make it."""
    h = {"Host": f"127.0.0.1:{PORT}", **({"Cookie": f"nuauto_review={cookie}"} if cookie else {})}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form)
        h.update({"Content-Type": "application/x-www-form-urlencoded", "Origin": ORIGIN})
    elif body is not None:
        data = json.dumps(body)
        h.update({"Content-Type": "application/json", "X-NUauto": "1", "Origin": ORIGIN})
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=60)
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    out = r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
    c.close()
    return out


def code_of(url):
    return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["c"][0]


def eventually(check, what, seconds=60):
    end = time.time() + seconds
    while time.time() < end:
        got = check()
        if got:
            return got
        time.sleep(0.2)
    raise AssertionError(f"timed out waiting for: {what}")


# the laptop's app, in this process: laptop mode, with a homelab whose page is on
config.REMOTE_APP, config.REMOTE_ASSIST, config.IS_SERVER = True, True, False
from nuauto import remote  # noqa: E402

ssh_reads = []
remote.homelab_sessions = lambda: ssh_reads.append(1) or []  # the read without a link: never ssh in a test
gui.REMOTE = False
link.address = lambda: ("127.0.0.1", PORT)
lsrv = ThreadingHTTPServer(("127.0.0.1", 0), gui.Handler)
lsrv.daemon_threads = True
gui.APP.port = lsrv.server_address[1]
threading.Thread(target=lsrv.serve_forever, daemon=True).start()
HERE = f"127.0.0.1:{gui.APP.port}"


def here(method, path, body=None):
    """A request to the laptop's app as its own page makes it."""
    h = {"Host": HERE, "Cookie": f"nuauto_{gui.APP.port}={gui.APP.token}"}
    data = None
    if body is not None:
        data = json.dumps(body)
        h.update({"Content-Type": "application/json", "X-NUauto": "1", "Origin": f"http://{HERE}"})
    c = http.client.HTTPConnection("127.0.0.1", gui.APP.port, timeout=60)
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    out = r.status, r.getheader("Content-Type", ""), r.read()
    c.close()
    return out


def here_json(method, path, body=None, expect=200):
    status, _, data = here(method, path, body)
    assert status == expect, (path, status, data[:300])
    return json.loads(data)


try:
    # a first device (a phone), paired with the page's own startup link: it makes the codes the "ssh" hands out
    status, headers, _ = there("POST", "/pair", form={"c": code_of(LINK), "name": "phone"})
    assert status == 200
    PHONE = headers["set-cookie"].split(";")[0].split("=", 1)[1]

    def pair_code(name):
        status, _, data = there("POST", "/api/action", {"kind": "pair_device", "args": {"name": name}}, cookie=PHONE)
        assert status == 200, data
        return json.loads(data)["link"]

    # ---- not linked: nothing is asked, and the app says why
    assert not link.linked() and link.key() is None
    try:
        link.call("GET", "/api/state")
        raise AssertionError("called without a key")
    except link.NoLink as e:
        assert "not linked" in str(e)
    assert gui.link_state()["on"] is False
    assert here("GET", "/api/homelab/assistant/1")[0] == 409
    for off in ("DEMO", "IS_SERVER", "REMOTE_APP"):  # the demo, the homelab itself, no homelab page: no link at all
        was = getattr(config, off)
        setattr(config, off, not was)
        try:
            assert gui.link_state() is None and not link.linked(), off
            try:
                gui.act({"kind": "homelab_link", "args": {}})
                raise AssertionError(f"linked with {off}")
            except gui.Refused:
                pass
        finally:
            setattr(config, off, was)

    # ---- Link this app: the code from the homelab, used up at once; the key in a 600 file only
    asked = []
    review.pair_link_from_homelab = lambda name: (asked.append(name), pair_code(name))[1]
    r = gui.act({"kind": "homelab_link", "args": {}})
    assert r["message"].startswith("Linked as NUauto on ") and asked and asked[0].startswith("NUauto on "), (r, asked)
    k = link.key()
    assert k and link.linked() and stat.S_IMODE(os.stat(link.PATH).st_mode) == 0o600
    assert k not in json.dumps(r) and k not in json.dumps(gui.link_state())
    devs = json.loads(there("GET", "/api/devices", cookie=PHONE)[2])["devices"]
    assert [d["name"] for d in devs] == ["phone", asked[0]], devs
    review.pair_link_from_homelab = lambda name: None  # an ssh that fails: said, nothing saved over the key
    try:
        link.pair()
        raise AssertionError("paired without a code")
    except link.NoLink as e:
        assert "ssh" in str(e) and link.key() == k
    review.pair_link_from_homelab = lambda name: pair_code(name).replace("c=", "c=x")  # a code that doesn't work
    try:
        link.pair()
        raise AssertionError("paired with a bad code")
    except link.NoLink as e:
        assert "did not pair" in str(e) and link.key() == k

    # ---- calls: the page's answers, and its refusals as its own words
    st = link.call("GET", "/api/state")
    assert st["where"] == "homelab" and st["demo"]
    try:
        link.call("POST", "/api/action", {"kind": "login_google"})
        raise AssertionError("not refused")
    except link.Refused as e:
        assert "laptop" in str(e)
    try:
        gui.homelab("GET", "/api/assistant/7")
        raise AssertionError("found")
    except gui.NotFound:
        pass

    # ---- the background read: its sessions, its queue, its running task, through the link
    eventually(lambda: not gui._homelab["busy"], "an earlier read (started before the link) to end")
    gui._homelab.update({"at": 0.0, "busy": False})
    gui.homelab_assistants()
    eventually(lambda: not gui._homelab["busy"] and gui._homelab["at"] > 0, "the read")
    assert len(gui.homelab_assistants()) == 3 and gui._homelab["error"] is None, (gui._homelab, ssh_reads)
    assert ssh_reads == [], ssh_reads  # (no homelab set here: never ssh)
    assert gui._homelab["queue"] and gui._homelab["queue"]["free"] == 0
    todo = gui.assistant_todo()
    assert [t["screen"] for t in todo if t["status"] == "approve"] == ["#/assistant/h1"], todo   # opens in this app

    # ---- the app's routes: a session's screen, a tap on it, Start on the homelab
    a1 = here_json("GET", "/api/homelab/assistant/1")
    assert a1["company"] == "Acme Robotics" and a1["approval"]["id"] == "demo1"
    assert here("GET", "/api/homelab/assistant/7")[0] == 404
    assert here("GET", "/api/homelab/assistant/1/shot.png")[0] == 404                       # (no screen in the demo)
    assert here("GET", "/api/homelab/elsewhere")[0] == 404                                  # only these routes
    r = here_json("POST", "/api/homelab/assistant", {"slot": 1, "do": "approve", "id": "demo1"})
    assert "pproved" in json.dumps(r), r
    queue = gui._homelab["queue"]["rows"]
    waits = next(k_ for k_, v in queue.items() if v["group"] == "wait")
    tab, row = waits.split("-")
    r = here_json("POST", "/api/action", {"kind": "assist", "args": {"row": int(row), "tab": "other" if tab == "other" else "",
                                                                     "on": "homelab"}}, expect=409)
    assert r["error"].startswith("Not started:"), r                                        # the homelab's own reason

    # ---- Run on: the homelab: its page runs Apply (demo pages), this app follows its log, answers its question
    r = here_json("POST", "/api/action", {"kind": "apply", "args": {"n": 1, "on": "homelab"}})
    assert r["task"]["where"] == "homelab", r
    tid = r["task"]["id"]

    def ended():
        t = here_json("GET", "/api/homelab/task?after=0")["task"]
        if t and t.get("question"):  # (the demo's first row asks nothing; answered the way the page answers)
            q = t["question"]
            here_json("POST", "/api/homelab/answer", {"task": tid, "id": q["id"],
                                                      "reply": q["options"][0] if q.get("options") else "Demo answer"})
        return t if t and t["state"] != "running" else None
    t = eventually(ended, "the run's end", 120)
    assert t["where"] == "homelab" and t["id"] == tid and t["state"] == "done", t
    assert any("marked Applied" in line for line in t["log"]), t["log"][-10:]          # its log, here
    assert t["screenshot"].startswith("/api/homelab/file?"), t["screenshot"]          # its pictures through this app
    status, ctype, data = here("GET", t["screenshot"])
    assert status == 200 and ctype == "image/png" and data[:4] == b"\x89PNG", (status, ctype)
    assert here("GET", "/api/homelab/file?path=/etc/passwd")[0] in (403, 404, 409)      # the homelab's own rule: logs only
    r = here_json("POST", "/api/homelab/stop", {"task": tid, "force": False}, expect=409)
    assert r["error"] == "Nothing is running.", r                                      # its own words
    assert gui.link_state()["on"] is True

    # ---- unpaired on the homelab: forgotten here at the next call, and the app says how to link again
    mine = next(d["id"] for d in json.loads(there("GET", "/api/devices", cookie=PHONE)[2])["devices"] if d["name"] == asked[0])
    assert there("POST", "/api/action", {"kind": "unpair", "args": {"id": mine}}, cookie=PHONE)[0] == 200
    try:
        link.call("GET", "/api/state")
        raise AssertionError("still linked")
    except link.NoLink as e:
        assert "unpaired" in str(e)
    assert link.key() is None and not os.path.exists(link.PATH) and not link.linked()
    status, _, data = here("GET", "/api/homelab/assistant/1")
    assert status == 409 and b"not linked" in data
finally:
    srv.terminate()
    srv.wait(timeout=30)
    lsrv.shutdown()

# ---- the same in a browser: `nuauto gui --demo --linked` (the laptop's demo app, linked to a demo homelab page of its own)
from playwright.sync_api import sync_playwright  # noqa: E402

import screens  # noqa: E402

app = subprocess.Popen([sys.executable, "-m", "nuauto", "gui", "--demo", "--linked", "--no-open"], env=ENV, cwd=ROOT,
                       stdout=subprocess.PIPE, stderr=open(os.path.join(STATE, "stderr-linked.log"), "w"), text=True)
errors = []
try:
    ready = json.loads(app.stdout.readline())
    base = ready["url"].split("/?")[0]
    with sync_playwright() as p:
        b = p.firefox.launch(headless=True)
        page = b.new_context(viewport={"width": 1200, "height": 900}).new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(ready["url"])
        page.wait_for_selector("[data-testid=week]")
        # Today: the homelab's waiting sessions first, each opening its screen in this app
        page.wait_for_selector("[data-testid^=todo-assistant-]", timeout=30000)
        assert page.locator("[data-testid^=todo-assistant-]").count() == 3
        assert page.locator("[data-testid^=todo-assistant-] a[href='#/assistant/h1']").count() == 1
        page.click("[data-testid^=todo-assistant-] a[href='#/assistant/h1']")
        page.wait_for_selector("[data-testid=as-approval]")
        assert "on the homelab" in page.inner_text("#view")
        page.click("[data-testid=as-approve]")                                  # approved from the laptop's window
        page.wait_for_selector("[data-testid=as-approval]:has-text('Approved: it is submitting now')", timeout=30000)
        # Company sites: the homelab's queue on each row, Start on the homelab next to Start here (the Other jobs card too)
        page.goto(base + "/#/company")
        page.wait_for_selector("[data-testid=company-agent]")
        page.wait_for_selector("[data-testid$=-homelab-start]")
        assert page.locator("[data-testid=company-agent] [data-testid$=-homelab-start]").count() >= 1
        assert page.locator("[data-testid=other-jobs] [data-testid$=-homelab-start]").count() >= 1
        assert page.locator("[data-testid=company-agent] [data-testid$=-queue]").count() >= 1   # why it waits there
        # Apply: Run on: the homelab, its log here
        page.goto(base + "/#/apply")
        page.click("[data-testid=run-on-homelab]")
        assert page.inner_text("[data-testid=btn-start-apply]") == "Start on the homelab"
        page.fill("[data-testid=apply-n]", "1")
        page.click("[data-testid=btn-start-apply]")
        for c in page.locator("[data-testid=dialog] button:has-text('Start')").all():
            c.click()
        page.wait_for_selector("text=Last run on the homelab: done", timeout=120000)
        assert "marked Applied" in page.inner_text("#view")
        # Settings: linked; Unlink, then Link again
        page.goto(base + "/#/settings")
        page.wait_for_selector("[data-testid=ha-link-status]:has-text('linked')")
        assert page.inner_text("[data-testid=ha-link-status]") == "linked"
        page.click("[data-testid=ha-link-toggle]")
        page.wait_for_selector("[data-testid=ha-link-status]:has-text('not linked')")
        page.goto(base + "/#/company")
        page.wait_for_selector("[data-testid=company-agent]")
        assert page.locator("[data-testid$=-homelab-start]").count() == 0                     # not linked: no Start there
        page.goto(base + "/#/settings")
        page.click("[data-testid=ha-link-toggle]")
        page.wait_for_selector("[data-testid=ha-link-status]:text-is('linked')")
        # every screen, as on the other two pages
        nav, bad = screens.visit_all(page, base)
        assert nav == screens.NAV and not bad, (nav, bad)
        b.close()
    assert not errors, errors
finally:
    app.terminate()
    app.wait(timeout=30)

print("All link checks passed.")
