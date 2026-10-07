"""The GUI (gui.py + gui_static/) in demo mode. Run: python test_gui.py

Starts `nuauto gui --demo`-style (a demo folder, then `nuauto gui --no-open`) and checks: the local server's
security rules, its API, the single-instance lock, and the main flows in headless Firefox (Review -> approve,
Apply -> a question -> submitted, Answers, Settings, Quit). No network: everything runs on demo.py's fakes.
"""
import http.client
import json
import os
import subprocess
import sys
import tempfile
import time

from playwright.sync_api import sync_playwright

STATE = tempfile.mkdtemp(prefix="nuauto-test-gui-")
ENV = {**os.environ, "NUAUTO_STATE_DIR": STATE, "NUAUTO_DEMO": "1", "NUAUTO_DEMO_PACE": "0.02", "NUAUTO_DEMO_HEADLESS": "1", "PYTHONUNBUFFERED": "1"}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable

subprocess.run([PY, "-m", "nuauto.demo", "setup"], env=ENV, check=True, cwd=ROOT)
gui = subprocess.Popen([PY, "-m", "nuauto", "gui", "--no-open"], env=ENV, cwd=ROOT, stdout=subprocess.PIPE,
                       stderr=open(os.path.join(STATE, "gui-stderr.log"), "w"), text=True)  # a file: a full pipe would freeze it
ready = json.loads(gui.stdout.readline())
URL, PORT = ready["url"], ready["port"]
TOKEN = URL.split("?t=")[1]
HOST = f"127.0.0.1:{PORT}"
assert ready["demo"] is True and ready["state_dir"] == STATE


def request(method, path, body=None, headers=None, cookie=True):
    h = {"Host": HOST}
    if cookie:
        h["Cookie"] = f"nuauto_{PORT}={TOKEN}"
    if method == "POST" and body is not None:
        h.update({"Content-Type": "application/json", "X-NUauto": "1"})
    h.update(headers or {})
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=60)
    c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, dict(r.getheaders()), data


def get(path):
    status, _, data = request("GET", path)
    assert status == 200, (path, status, data[:300])
    return json.loads(data)


def post(path, body, expect=200):
    status, _, data = request("POST", path, body)
    assert status == expect, (path, status, data[:300])
    return json.loads(data)


try:
    # ---------------------------------------------------------------- security of the local server
    assert request("GET", "/api/state", cookie=False)[0] == 403              # no cookie
    status, headers, _ = request("GET", "/?t=wrong", cookie=False)
    assert status == 403
    status, headers, _ = request("GET", f"/?t={TOKEN}", cookie=False)    # the start URL: secret -> cookie
    assert status == 303 and headers["Location"] == "/"
    cookie = headers["Set-Cookie"]
    assert cookie.startswith(f"nuauto_{PORT}={TOKEN}") and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert request("GET", "/api/state", headers={"Host": f"evil.example:{PORT}"})[0] == 403  # DNS rebinding
    assert request("POST", "/api/health/run", body=None, headers={"Content-Type": "application/json"})[0] == 403  # no X-NUauto
    assert request("POST", "/api/health/run", {}, headers={"Content-Type": "text/plain"})[0] == 403
    assert request("POST", "/api/health/run", {}, headers={"Origin": "http://evil.example"})[0] == 403
    assert request("POST", "/api/health/run", {}, cookie=False)[0] == 403
    assert request("POST", "/api/health/run", {})[0] == 200
    status, headers, _ = request("GET", "/static/app.js")
    assert status == 200 and "script-src 'self'" in headers["Content-Security-Policy"] and headers["X-Frame-Options"] == "DENY"
    for bad in ("/static/../gui.py", "/static/%2e%2e/gui.py", "/static/APP.JS", "/nope"):
        assert request("GET", bad)[0] == 404, bad
    for path in (f"{STATE}/local/token.json", f"{STATE}/logs/../local/token.json", "/etc/passwd"):
        assert request("GET", f"/api/file?path={path}")[0] == 404, path
    settings = get("/api/settings")
    secret = open(os.path.join(STATE, "local", "token.json")).read()
    assert secret not in json.dumps(settings) and settings["discord"] is False and settings["demo"] is True

    # ---------------------------------------------------------------- the API
    st = get("/api/state")
    assert st["demo"] and not st["setup_needed"] and st["week"]["applied"] == 5 and st["counts"]["approved"] == 3
    assert {t["kind"] for t in st["todo"]} >= {"site", "company"}
    # every to-do item says when it closes (or that no deadline is listed), soonest first, undated last
    assert all({"closes", "closes_text", "past", "soon"} <= set(t) for t in st["todo"]), st["todo"]
    dated = [t["closes"] for t in st["todo"] if t["closes"]]
    assert dated == sorted(dated) and [t["closes"] for t in st["todo"]][:len(dated)] == dated, st["todo"]
    assert all(t["closes_text"] == "no deadline listed" for t in st["todo"] if not t["closes"])
    ins = get("/api/insights")  # the demo: 8 pool jobs, one paid yearly (made hourly), one with no pay
    assert ins["pool"]["count"] == 8 and ins["pool"]["pay"]["listed"] == 7 and ins["rows"] == sum(x["count"] for x in ins["statuses"])
    assert ins["applied"]["count"] == {x["label"]: x["count"] for x in ins["statuses"]}["Applied"]
    applying = get("/api/apply")["rows"]
    assert applying and all({"closes", "closes_text", "past", "soon", "company_site"} <= set(r) for r in applying), applying
    checks = {c["id"]: c for c in get("/api/health")["checks"]}
    assert checks["google"]["status"] == "ok" and checks["files"]["status"] == "ok", checks
    rev = get("/api/review?mode=approve")
    first = rev["jobs"][0]["id"]
    card = get(f"/api/job/{first}")
    assert card["id"] == first and card["description"]
    assert request("GET", "/api/job/..%2Fx")[0] == 404
    r = post("/api/decide", {"id": first, "decision": "approve", "mode": "approve"})
    assert r["row"] and r["previous"] is None
    rows = json.load(open(os.path.join(STATE, "local", "demo_sheet_DEMO-SHEET.json")))["rows"]
    assert rows[r["row"] - 1][3] == "Approved"
    assert json.load(open(os.path.join(STATE, "data", "ratings.json")))[first]["label"] == 1
    post("/api/undo", {"id": first, "row": r["row"], "previous": None})
    rows = json.load(open(os.path.join(STATE, "local", "demo_sheet_DEMO-SHEET.json")))["rows"]
    assert rows[r["row"] - 1][3] == "Proposed" and first not in json.load(open(os.path.join(STATE, "data", "ratings.json")))
    post("/api/decide", {"id": "nope", "decision": "approve", "mode": "approve"}, expect=409)
    post("/api/decide", {"id": rev["jobs"][1]["id"], "decision": "approve", "mode": "rate"}, expect=409)

    bank = get("/api/answers")
    entries = bank["entries"]
    post("/api/answers", {"entries": entries + [dict(entries[0])], "version": bank["version"]}, expect=409)  # duplicate
    post("/api/answers", {"entries": entries, "version": bank["version"] - 1}, expect=409)              # stale
    edited = [dict(e) for e in entries]
    edited[0]["answer"] = "Demo S. Student"
    saved = post("/api/answers", {"entries": edited, "version": bank["version"]})
    assert saved["entries"][0]["answer"] == "Demo S. Student"
    post("/api/settings", {"week_start": "next tuesday"}, expect=409)
    for bad in (0, 31, "lots", 2.5, True, None):
        post("/api/settings", {"max_per_week": bad}, expect=409)
    assert post("/api/settings", {"max_per_week": "7"})["max_per_week"] == 7
    assert get("/api/state")["week"]["max"] == 7
    assert post("/api/settings", {"max_per_week": 11})["max_per_week"] == 11
    assert post("/api/settings", {"resume_label": "Demo Student | Resume"})["resume_label"] == "Demo Student | Resume"
    post("/api/action", {"kind": "rm -rf"}, expect=409)
    r = post("/api/action", {"kind": "assist", "args": {"row": 10}})
    assert r["opened"] is False and r["demo"] and "assist 10" in r["command"]  # demo: shows the command only

    # Company sites: the NUworks side of a company-site application, sent again from the GUI
    company = get("/api/company")
    assert [x["company"] for x in company["agent"]] == ["Iron Valley Medical"] and [x["company"] for x in company["site"]] == ["Willow Devices"]
    retry = company["retry"][0]
    assert retry["company"] == "Maple Controls"
    task = post("/api/action", {"kind": "nuworks_side", "args": {"row": retry["row"]}})["task"]
    for _ in range(300):
        t = get("/api/task?after=0")["task"]
        if t["state"] != "running":
            break
        time.sleep(0.2)
    assert t["id"] == task["id"] and t["state"] == "done", t["log"][-6:]
    notes = {r[1]: r[4] for r in json.load(open(os.path.join(STATE, "local", "demo_sheet_DEMO-SHEET.json")))["rows"][1:]}
    assert "NUworks side submitted too (confirmed by NUworks page)." in notes["Maple Controls"], notes["Maple Controls"]
    assert get("/api/company")["retry"] == []  # never offered twice

    # a second start opens a window on this one instead (here: --no-open, so it just says so and exits)
    second = subprocess.run([PY, "-m", "nuauto", "gui", "--no-open"], env=ENV, cwd=ROOT, capture_output=True, text=True,
                            timeout=60)
    assert second.returncode != 0 and "already running" in second.stderr, second.stderr
    # the real window never starts inside a Claude Code shell
    real = {k: v for k, v in ENV.items() if k != "NUAUTO_DEMO"} | {"NUAUTO_STATE_DIR": tempfile.mkdtemp(), "CLAUDECODE": "1"}
    r = subprocess.run([PY, "-m", "nuauto", "gui", "--no-open"], env=real, cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert r.returncode != 0 and "never starts inside a Claude Code session" in r.stderr, r.stderr

    # ---------------------------------------------------------------- the page, in headless Firefox
    errors = []
    with sync_playwright() as p:
        b = p.firefox.launch(headless=True)
        page = b.new_context(viewport={"width": 1200, "height": 900}).new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(URL)
        page.wait_for_selector("[data-testid=week]")
        assert page.url.endswith("/") or "#" in page.url  # the secret is gone from the address bar
        body = page.inner_text("body")
        assert "null" not in body and "undefined" not in body and "Demo mode" in body
        page.wait_for_selector("[data-testid=health-google].ok")

        page.click("[data-testid=nav-review]")
        page.wait_for_selector("[data-testid=job-card]")
        title = page.inner_text("[data-testid=job-title]")
        approved_company = page.inner_text("[data-testid=job-card] .meta").split(" · ")[0]
        page.click("[data-testid=btn-approve]")
        page.wait_for_selector("[data-testid=toast]")
        page.wait_for_function(f"() => document.querySelector('[data-testid=job-title]').innerText !== {json.dumps(title)}")
        page.keyboard.press("j")  # vim keys scroll the card without deciding anything
        page.keyboard.press("G")
        page.wait_for_function("() => window.scrollY > 0")
        page.keyboard.press("g")
        page.wait_for_function("() => window.scrollY === 0")
        page.keyboard.press("n")  # keyboard works too: the next job is rated no
        page.wait_for_timeout(500)

        page.click("[data-testid=nav-apply]")
        page.wait_for_selector("[data-testid=btn-start-apply]:not([disabled])")
        assert page.locator("[data-testid=approved-list] li").count() == 4
        assert page.locator("[data-testid=approved-list] [data-testid=due]").count() == 4  # each row shows its due date
        page.click("[data-testid=btn-start-apply]")
        page.wait_for_selector("[data-testid=question]", timeout=90000)
        assert "18 years old" in page.inner_text("[data-testid=q-label]")
        assert "Select" not in page.locator("[data-testid=question] .choices").inner_text()  # placeholder hidden
        page.get_by_role("button", name="Yes", exact=True).click()
        page.wait_for_function("() => document.body.innerText.includes('Last run: done') || "
                               "document.querySelector('[data-testid=q-menu-u]')", timeout=120000)
        assert page.locator("[data-testid=q-menu-u]").count() == 0, "unexpected: NUworks' confirmation was not seen"
        assert page.locator("[data-testid=apply-shot]").count() == 1
        rows = {r[1]: r[3] for r in json.load(open(os.path.join(STATE, "local", "demo_sheet_DEMO-SHEET.json")))["rows"][1:]}
        assert rows["Harbor Embedded"] == rows["Lumen Security"] == rows[approved_company] == "Applied", rows
        # Cobalt was approved, then undone (API part above): it is a Proposed row again, so Review showed it first and
        # this click approved that same row (no second row), and it was applied
        assert approved_company == "Cobalt Systems" and rows["Cobalt Systems"] == "Applied", (approved_company, rows)
        all_rows = json.load(open(os.path.join(STATE, "local", "demo_sheet_DEMO-SHEET.json")))["rows"][1:]
        assert sum(r[1] == "Cobalt Systems" for r in all_rows) == 1, all_rows
        assert rows["Quarry Hardware"] == "Needs Human", rows

        # another site on this machine (another port: same site to the browser, so the cookie IS sent) tries to change
        # the sheet three ways. All must fail: no X-NUauto header / no JSON / a CORS preflight the server never allows.
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        target = f"http://127.0.0.1:{PORT}/api/mark"
        body = json.dumps({"action": "applied", "row": 10, "url": "https://northeastern-csm.symplicity.com/students/app/jobs/detail/900120"})
        evil_page = f"""<!doctype html><body><form id=f method=post action="{target}" enctype="text/plain">
<input name='{body[:-1]}' value='}}'></form><script>
const out = [];
fetch("{target}", {{method: "POST", credentials: "include", body: {json.dumps(body)}}})
  .then(r => out.push("simple:" + r.status), e => out.push("simple:blocked"))
  .then(() => fetch("{target}", {{method: "POST", credentials: "include", body: {json.dumps(body)},
                    headers: {{"Content-Type": "application/json", "X-NUauto": "1"}}}}))
  .then(r => out.push("preflighted:" + r.status), e => out.push("preflighted:blocked"))
  .then(() => {{ document.title = out.join(","); }});
</script>"""

        class Evil(BaseHTTPRequestHandler):
            def do_GET(self):
                data = evil_page.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass
        evil = ThreadingHTTPServer(("127.0.0.1", 0), Evil)
        threading.Thread(target=evil.serve_forever, daemon=True).start()
        sheet_file = os.path.join(STATE, "local", "demo_sheet_DEMO-SHEET.json")
        before = open(sheet_file).read()
        evil_tab = page.context.new_page()
        evil_tab.goto(f"http://127.0.0.1:{evil.server_address[1]}/")
        evil_tab.wait_for_function("() => document.title.includes('preflighted')", timeout=20000)
        results = evil_tab.title()
        assert "simple:403" in results or "simple:blocked" in results, results
        assert "preflighted:blocked" in results, results  # the browser never sends it: the server allows no CORS
        with evil_tab.expect_response(lambda r: r.url == target) as posted:  # a plain form post: no header, not JSON
            evil_tab.evaluate("document.getElementById('f').submit()")
        assert posted.value.status == 403, posted.value.status
        assert open(sheet_file).read() == before, "the sheet changed"
        evil_tab.close()
        evil.shutdown()

        page.click("[data-testid=nav-company]")
        page.wait_for_selector("[data-testid=company-10]")
        assert page.locator("[data-testid=company-10] [data-testid=due]").count() == 1  # due date on the company-site row
        page.click("[data-testid=nav-home]")
        page.wait_for_selector("[data-testid=todo-company-10]")
        assert page.locator("[data-testid=todo] [data-testid=due]").count() == len(get("/api/state")["todo"])
        # Insights: the status ring has one arc per status, bars have real sizes, the toggle switches to Applied
        ins = get("/api/insights")  # again: the runs above changed the sheet
        page.click("[data-testid=nav-insights]")
        page.wait_for_selector("[data-testid=ins-status] svg")
        assert page.locator("[data-testid=ins-status] svg circle").count() == len(ins["statuses"])
        assert page.locator("[data-testid=ins-status] .legend li").count() == len(ins["statuses"])
        assert page.locator("[data-testid=pay-bars] .vb").count() == 6
        heights = page.eval_on_selector_all("[data-testid=pay-bars] .vb-bar", "bs => bs.map(b => b.getBoundingClientRect().height)")
        assert max(heights) > 50 and sum(1 for h in heights if h > 0) == sum(1 for b in ins["pool"]["pay"]["buckets"] if b["count"]), heights
        widths = page.eval_on_selector_all("[data-testid=where-bars] .hb-bar", "bs => bs.map(b => b.getBoundingClientRect().width)")
        assert widths and min(widths) > 0 and widths[0] == max(widths), widths
        assert "$" in page.inner_text("[data-testid=tile-pay]") and "of 8" in page.inner_text("[data-testid=tile-ma]")
        page.click("[data-testid=ins-applied]")
        page.wait_for_selector("[data-testid=ins-applied][aria-pressed=true]")
        assert f"Applied ({ins['applied']['count']})" in page.inner_text("[data-testid=ins-applied]")
        assert page.locator("[data-testid=tile-listed]").count() == 1  # applied: "pay listed" instead of "closing"
        page.set_viewport_size({"width": 420, "height": 900})  # phone width: nothing sticks out sideways
        page.wait_for_timeout(200)
        assert page.evaluate("document.documentElement.scrollWidth") <= 420
        page.set_viewport_size({"width": 1200, "height": 900})
        page.click("[data-testid=nav-answers]")
        page.wait_for_selector("[data-testid=answers-save]")
        assert "are you at least 18 years old? *" in [x.input_value() for x in page.locator("tbody input[aria-label=Question]").all()]
        page.click("[data-testid=nav-settings]")
        page.wait_for_selector("[data-testid=check-google]")
        page.click("[data-testid=btn-quit]")
        page.click("[data-testid=confirm-yes]")
        page.wait_for_selector("[data-testid=gone]")
        b.close()
    assert not errors, errors
    gui.wait(timeout=60)  # Quit stops the server
    assert not os.path.exists(os.path.join(STATE, "local", "gui.lock"))
finally:
    if gui.poll() is None:
        gui.terminate()
        gui.wait(timeout=30)

print("All GUI checks passed.")
