"""NUauto's page on the homelab (gui.serve_remote), end to end in demo mode. Run: python tests/test_homelab_app.py

Starts `nuauto gui --demo --remote --no-open` (the same server and page as the homelab's, on 127.0.0.1, on demo data with
made-up assistant sessions: review.demo_setup) and checks: pairing is the only way in; the page itself is public but no
data is; what needs the laptop is refused with a reason; the homelab's own parts (its sessions' screens, its queue, its
paired devices); then the same in headless Firefox (pair, Today's waiting list, an assistant's screen, Approve). No
network: everything runs on demo.py's fakes. (The handler's rules in detail: tests/test_review.py.)
"""
import http.client
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse

from playwright.sync_api import sync_playwright

import screens

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = tempfile.mkdtemp(prefix="nuauto-test-homelab-app-")
ENV = {**os.environ, "NUAUTO_DEMO_HEADLESS": "1", "NUAUTO_DEMO_PACE": "0.02", "PYTHONUNBUFFERED": "1",
       "TMPDIR": STATE}  # the demo makes its folder under TMPDIR: removed with this test's
srv = subprocess.Popen([sys.executable, "-m", "nuauto", "gui", "--demo", "--remote", "--no-open"], env=ENV, cwd=ROOT,
                       stdout=subprocess.PIPE, stderr=open(os.path.join(STATE, "stderr.log"), "w"), text=True)
ready = json.loads(srv.stdout.readline())
PORT, LINK, DEMO_DIR = ready["port"], ready["url"], ready["state_dir"]
HOST = f"127.0.0.1:{PORT}"
ORIGIN = f"http://{HOST}"
assert ready["remote"] is True and ready["demo"] is True and LINK.startswith(f"{ORIGIN}/pair?c=")


def request(method, path, body=None, cookie=None, headers=None, form=None):
    h = {"Host": HOST, **({"Cookie": f"nuauto_review={cookie}"} if cookie else {})}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form)
        h["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body)
        h.update({"Content-Type": "application/json", "X-NUauto": "1", "Origin": ORIGIN})
    h.update(headers or {})
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=60)
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    out = r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
    c.close()
    return out


def pair(name):
    """A fresh pairing link (made the way Settings > Paired devices makes one), used: the new device's key."""
    code = urllib.parse.parse_qs(urllib.parse.urlparse(LINK).query)["c"][0] if name == "first" else None
    if code is None:
        r = request("POST", "/api/action", {"kind": "pair_device", "args": {"name": name}}, cookie=KEY)
        assert r[0] == 200, r
        code = urllib.parse.parse_qs(urllib.parse.urlparse(json.loads(r[2])["link"]).query)["c"][0]
    status, headers, _ = request("POST", "/pair", form={"c": code, "name": name}, headers={"Origin": ORIGIN})
    assert status == 200 and headers["set-cookie"].startswith("nuauto_review="), (status, headers)
    return headers["set-cookie"].split(";")[0].split("=", 1)[1]


def get(path, key=None):
    status, _, data = request("GET", path, cookie=key or KEY)
    assert status == 200, (path, status, data[:300])
    return json.loads(data)


def post(path, body, expect=200, key=None):
    status, _, data = request("POST", path, body, cookie=key or KEY)
    assert status == expect, (path, status, data[:300])
    return json.loads(data)


def eventually(check, what, seconds=20):
    end = time.time() + seconds
    while time.time() < end:
        got = check()
        if got:
            return got
        time.sleep(0.2)
    raise AssertionError(f"timed out waiting for: {what}")


KEY = None
try:
    # ---- the way in: the page is public code, nothing else is; pairing gives a device its key
    assert request("GET", "/")[0] == 200 and request("GET", "/static/app.js")[0] == 200
    status, _, data = request("GET", "/api/state")
    assert status == 403 and json.loads(data)["pair"] is True and "not paired" in json.loads(data)["error"]
    assert request("GET", "/api/state", headers={"Host": f"localhost:{PORT}"})[0] == 403        # its own address only
    assert request("GET", "/s/2")[1]["location"] == "/#/assistant/2"                            # old Discord links
    KEY = pair("first")
    st = get("/api/state")
    assert st["where"] == "homelab" and st["demo"] and not st["setup_needed"]                  # setup is the laptop's
    assert {"login_google", "login_nuworks", "login_claude", "bitwarden_add", "review_pair"} <= set(st["laptop_only"])
    waiting = [t for t in st["todo"] if t["kind"] == "assistant"]                    # Today's list, first
    assert [t["status"] for t in waiting] == ["approve", "waiting", "finish"] and st["todo"][:3] == waiting
    assert waiting[0]["screen"] == "#/assistant/1"
    # each once: the NUworks row the third session is on is its assistant's item, not also a company-site item
    assert not [t for t in st["todo"] if t["kind"] == "company" and t["row"] == waiting[2]["row"]]
    assert st["assistants"].startswith("other-") and ":approve" in st["assistants"]

    # ---- what needs the laptop: refused here, with the reason the page shows
    for kind in ("login_google", "login_nuworks", "login_claude", "bitwarden_add", "bitwarden_import", "review_pair"):
        assert "laptop" in post("/api/action", {"kind": kind}, expect=409)["error"], kind
    assert "stays on" in post("/api/quit", {}, expect=409)["error"]
    assert "laptop" in post("/api/logins/export", {}, expect=409)["error"]
    assert "laptop" in post("/api/homelab", {"action": "check", "ssh": "homelab"}, expect=409)["error"]
    for setting in ({"resume_path": "/x.pdf"}, {"transcript_path": ""}, {"assist_read_paths": []},
                    {"password_manager": "bitwarden"}, {"remote_assist": False}, {"remote_app": False}):
        assert "on your laptop" in post("/api/settings", setting, expect=409)["error"], setting
    assert "laptop" in post("/api/setup", {"action": "sheet_create"}, expect=409)["error"]
    assert all(s["id"] != "homelab" for s in get("/api/setup")["steps"]) and get("/api/setup")["where"] == "homelab"
    # ... and what it can do here: its own settings, the queue's settings, preferences
    assert post("/api/settings", {"max_per_week": 9})["max_per_week"] == 9
    assert post("/api/settings", {"remote_hours": [8, 21]})["homelab_assistant"]["hours"] == [8, 21]
    assert get("/api/settings")["where"] == "homelab" and get("/api/settings")["homelab"] is None

    # ---- its assistant sessions: the screen of each, a tap on one, its picture
    a1 = get("/api/assistant/1")
    assert a1["company"] == "Acme Robotics" and a1["status"] == "approve" and a1["approval"]["id"] == "demo1"
    assert "Earliest start date" in a1["approval"]["why"] and a1["approval"]["answered"] is None
    assert get("/api/assistant/3")["status"] == "finish" and request("GET", "/api/assistant/4", cookie=KEY)[0] == 404
    assert request("GET", "/api/assistant/1/shot.png", cookie=KEY)[0] == 404                    # no screen in the demo
    r = post("/api/assistant", {"slot": 2, "do": "reply", "text": "OK"})
    assert "Could not do that" in r["message"]                                                 # (no tmux in the demo)
    assert "ended" in post("/api/assistant", {"slot": 4, "do": "exit"}, expect=409)["error"]

    # ---- its queue (Company sites): every slot is taken by the made-up sessions, so rows wait; Start now says why
    co = get("/api/company")
    q = co["queue"]
    assert q["free"] == 0 and q["auto"] == ["other"] and len(co["sessions"]) == 3
    groups = {v["group"] for v in q["rows"].values()}
    assert {"running", "wait", "check", "laptop"} <= groups, groups
    waiting = next(k for k, v in q["rows"].items() if v["group"] == "wait")
    tab, row = waiting.split("-")
    r = post("/api/action", {"kind": "assist", "args": {"row": int(row), "tab": "other" if tab == "other" else ""}}, expect=409)
    assert r["error"].startswith("Not started:"), r
    running = next(k for k, v in q["rows"].items() if v["group"] == "running")
    tab, row = running.split("-")
    r = post("/api/mark", {"action": "drop", "why": "duplicate", "row": int(row), "url": "x", **({"tab": "other"} if tab == "other" else {})},
             expect=409)
    assert "assistant is working on this row" in r["error"]

    # ---- paired devices: another one, list, unpair (never this one from itself)
    k2 = pair("tablet")
    devs = get("/api/devices")
    assert [d["name"] for d in devs["devices"]] == ["first", "tablet"] and devs["this"] == devs["devices"][0]["id"]
    assert all(KEY not in json.dumps(devs) and k2 not in json.dumps(devs) for _ in [0])
    assert "this device" in post("/api/action", {"kind": "unpair", "args": {"id": devs["this"]}}, expect=409)["error"]
    gone = post("/api/action", {"kind": "unpair", "args": {"id": devs["devices"][1]["id"]}})
    assert gone["gone"] == 1 and [d["name"] for d in gone["devices"]] == ["first"]
    assert request("GET", "/api/state", cookie=k2)[0] == 403                                     # its cookie stops working
    assert post("/api/action", {"kind": "unpair", "args": {"id": "nope"}}, expect=409)

    # ---- the same in a browser: pair, Today's waiting list, an assistant's screen, Approve
    errors = []
    with sync_playwright() as p:
        b = p.firefox.launch(headless=True)
        page = b.new_context(viewport={"width": 420, "height": 900}).new_page()   # a phone's width
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{ORIGIN}/#/home")                                            # not paired yet: it says so
        page.wait_for_selector("[data-testid=gone]:has-text('not paired')")
        r = post("/api/action", {"kind": "pair_device", "args": {"name": "Firefox"}})
        page.goto(r["link"])
        page.fill("input[name=name]", "Firefox")
        page.click("button:has-text('Pair')")
        page.wait_for_selector("text=Paired")
        page.click("text=Open NUauto")
        page.wait_for_selector("[data-testid=todo] [data-testid^=todo-assistant-]")
        assert page.locator("[data-testid^=todo-assistant-]").count() == 3
        page.click("[data-testid^=todo-assistant-] >> nth=0 >> a")
        page.wait_for_selector("[data-testid=as-approval]")
        assert "click 'Submit Application'" in page.inner_text("[data-testid=as-approval]")
        page.click("[data-testid=as-approve]")
        decision = os.path.join(DEMO_DIR, "logs", "demo_assist_1", "decision.json")
        got = eventually(lambda: os.path.exists(decision) and json.load(open(decision)), "the approval")
        assert got["decision"] == "approve" and got["by"] == "Firefox" and got["id"] == "demo1", got
        page.wait_for_selector("[data-testid=as-approval]:has-text('Approved: it is submitting now')")
        # a laptop-only button explains instead of acting
        page.goto(f"{ORIGIN}/#/settings")
        page.wait_for_selector("[data-testid=devices]")
        page.click("button:has-text('Log in again')")
        page.wait_for_selector("[data-testid=laptop-only]:has-text('on your laptop')")
        page.keyboard.press("Escape")
        assert page.locator("[data-testid=btn-quit]").count() == 0 and page.locator("[data-testid=hl-panel]").count() == 0
        # one card for the homelab (its paired devices and its assistant), no Bitwarden card (the laptop's)
        assert page.locator("[data-testid=homelab-settings]").count() == 1 and page.locator("[data-testid=pm-settings]").count() == 0
        assert page.locator("[data-testid=homelab-settings] [data-testid=devices]").count() == 1
        # Apply > Assistants: one line for the assistants and the queue; each session opened from its own row
        page.goto(f"{ORIGIN}/#/apply")
        page.click("[data-testid=tab-assistants]")
        page.wait_for_selector("[data-testid=assistants-bar]")
        assert page.locator("[data-testid=assistants-list] li").count() == 3
        assert page.locator("[data-testid=assistants-bar]").count() == 1 and page.locator("[data-testid=queue-line]").count() == 1
        assert "3 of 3 running" in page.inner_text("[data-testid=assistants-running]")
        assert page.locator("[data-testid=assistants-list] a[href^='#/assistant/']").count() == 3
        # ...and each from its own row, on the tab that row is in
        rows_running = 0
        for tab in ("nuworks", "other"):
            page.click(f"[data-testid=tab-{tab}]")
            page.wait_for_selector("[data-testid=apply-toolbar]")
            rows_running += page.locator("a[data-testid$=-running][href^='#/assistant/']").count()
        assert rows_running == 3, rows_running
        # every screen, as test_gui.py opens them on the laptop's app: the same menu, none empty or failing
        nav, bad = screens.visit_all(page, ORIGIN)
        assert nav == screens.NAV and not bad, (nav, bad)
        b.close()
    assert not errors, errors
finally:
    srv.terminate()
    srv.wait(timeout=30)

print("All homelab app checks passed.")
