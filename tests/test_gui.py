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

    # Other jobs: the sheet's Other jobs tab (not NUworks). Add, mark applied, start the assistant; no caps
    other_file = os.path.join(STATE, "local", "demo_sheet_DEMO-SHEET.other.json")
    other = get("/api/other")
    assert other["exists"] and [(x["row"], x["company"], x["host"]) for x in other["ready"]] == [(2, "Granite Robotics", "careers.example.com")]
    assert [x["company"] for x in other["applied"]] == ["Northwind Labs"] and other["rest"] == []
    for bad in ({"url": "https://northeastern-csm.symplicity.com/students/app/jobs/detail/1", "company": "A", "title": "B"},
                {"url": "http://jobs.example.net/1", "company": "A", "title": "B"},
                {"url": "https://jobs.example.net/1", "company": "", "title": "B"},
                {"url": "https://careers.example.com/jobs/4821", "company": "Again", "title": "Duplicate"}):
        post("/api/other/add", bad, expect=409)
    week_before = get("/api/state")["week"]
    added = post("/api/other/add", {"url": " https://jobs.example.net/1 ", "company": "Basalt Bio", "title": "Lab Automation Intern"})
    assert added["row"] == 4
    rows = json.load(open(other_file))["rows"]
    assert rows[3] == ["https://jobs.example.net/1", "Basalt Bio", "Lab Automation Intern", "Approved", "", ""], rows
    post("/api/mark", {"action": "site", "tab": "other", "row": 4, "url": "https://jobs.example.net/1"}, expect=409)
    post("/api/mark", {"action": "applied", "tab": "other", "row": 4, "url": "https://wrong.example/1"}, expect=409)
    post("/api/mark", {"action": "applied", "tab": "other", "row": 4, "url": "https://jobs.example.net/1"})
    assert json.load(open(other_file))["rows"][3][3] == "Applied"
    assert get("/api/state")["week"] == week_before  # the Other jobs tab has no weekly / total cap
    r = post("/api/action", {"kind": "assist", "args": {"row": 2, "tab": "other"}})
    assert r["demo"] and r["command"].endswith("assist other 2"), r
    # no tab yet: adding the first job makes it (headers, then the job as row 2)
    os.remove(other_file)
    assert get("/api/other") == {"exists": False, "tab": "Other jobs", "ready": [], "applied": [], "rest": []}
    assert post("/api/other/add", {"url": "https://jobs.example.net/2", "company": "Cinder", "title": "Intern"})["row"] == 2
    rows = json.load(open(other_file))["rows"]
    assert rows[0] == ["URL", "Company", "Title", "Status", "Notes", "Date"] and rows[1][3] == "Approved", rows
    assert get("/api/other")["ready"][0]["company"] == "Cinder"

    # the Sheet screen: add to either tab by hand, move between the tabs, remove (cells cleared, no row renumbered)
    main_file = os.path.join(STATE, "local", "demo_sheet_DEMO-SHEET.json")
    NU = "https://northeastern-csm.symplicity.com/students/app/jobs/detail/"
    main_rows = lambda: json.load(open(main_file))["rows"]
    tabs = get("/api/sheet")["tabs"]
    assert tabs["other"]["exists"] and [r["company"] for r in tabs["other"]["rows"]] == ["Cinder"]
    applied_row = next(r for r in tabs["nuworks"]["rows"] if r["status"] == "Applied")
    assert applied_row["locked"] and all(r["locked"] is None for r in tabs["nuworks"]["rows"] if r["status"] == "Approved")
    assert NU + "900107" not in [r["url"] for r in tabs["nuworks"]["rows"]]
    info = get("/api/sheet/lookup?url=" + NU + "900107?from=search")
    assert info["tab"] == "nuworks" and info["known"] and info["in_pool"] and info["company"] == "Beacon Web Studio", info
    assert get("/api/sheet/lookup?url=" + NU + "999999") == {"tab": "nuworks", "url": NU + "999999", "known": False}
    assert get("/api/sheet/lookup?url=https://jobs.example.net/9")["tab"] == "other"
    assert get("/api/sheet/lookup?url=http://jobs.example.net/9")["tab"] is None
    for bad in ({"tab": "nuworks", "url": "https://jobs.example.net/9", "company": "A", "title": "B"},   # not NUworks
                {"tab": "nuworks", "url": NU + "999999"},                                         # unknown: needs names
                {"tab": "nuworks", "url": NU + "900101"},                                         # already there
                {"tab": "other", "url": NU + "999999", "company": "A", "title": "B"},              # NUworks in Other
                {"tab": "nuworks", "url": NU + "999999", "company": "A", "title": "B", "status": "Applied"},
                {"tab": "elsewhere", "url": "https://jobs.example.net/9", "company": "A", "title": "B"}):
        post("/api/sheet", {"action": "add", **bad}, expect=409)
    known = post("/api/sheet", {"action": "add", "tab": "nuworks", "url": NU + "900107?from=search", "status": "Proposed"})
    assert known["tab"] == "nuworks"
    assert main_rows()[known["row"] - 1][:4] == [NU + "900107", "Beacon Web Studio", "Web Developer Co-op", "Proposed"]
    assert "match 72%" in main_rows()[known["row"] - 1][4]
    unknown = post("/api/sheet", {"action": "add", "tab": "nuworks", "url": NU + "999999", "company": "Ash", "title": "Co-op"})
    assert main_rows()[unknown["row"] - 1][3:5] == ["Approved", "not in your job data; added by hand"]
    count = len(main_rows())
    post("/api/sheet", {"action": "remove", "tab": "nuworks", "row": applied_row["row"], "url": applied_row["url"]}, expect=409)
    post("/api/sheet", {"action": "remove", "tab": "nuworks", "row": known["row"], "url": NU + "999999"}, expect=409)  # wrong job
    post("/api/sheet", {"action": "remove", "tab": "nuworks", "row": known["row"], "url": NU + "900107"})
    assert main_rows()[known["row"] - 1] == [""] * 6 and len(main_rows()) == count  # cleared, nothing shifted
    assert main_rows()[unknown["row"] - 1][1] == "Ash"
    post("/api/sheet", {"action": "move", "tab": "nuworks", "row": unknown["row"], "url": NU + "999999"}, expect=409)  # NUworks link
    moved = post("/api/sheet", {"action": "move", "tab": "nuworks", "row": unknown["row"], "url": NU + "999999",
                                "new_url": "https://ash.example.com/jobs/1"})
    assert moved["tab"] == "other" and main_rows()[unknown["row"] - 1] == [""] * 6
    row = json.load(open(other_file))["rows"][moved["row"] - 1]
    assert row[:4] == ["https://ash.example.com/jobs/1", "Ash", "Co-op", "Approved"] and "Moved from the NUworks tab" in row[4], row
    post("/api/sheet", {"action": "move", "tab": "other", "row": moved["row"], "url": "https://ash.example.com/jobs/1"}, expect=409)
    back = post("/api/sheet", {"action": "move", "tab": "other", "row": moved["row"], "url": "https://ash.example.com/jobs/1",
                               "new_url": NU + "999999"})
    assert back["tab"] == "nuworks" and main_rows()[back["row"] - 1][:4] == [NU + "999999", "Ash", "Co-op", "Approved"]
    post("/api/sheet", {"action": "remove", "tab": "nuworks", "row": back["row"], "url": NU + "999999"})
    assert get("/api/state")["week"] == week_before

    # the Other jobs answers: their own bank; answers.json marks NUworks-only entries (default list or own flag)
    main_bank = get("/api/answers")
    flags = {e["question"]: e["nuworks_only"] for e in main_bank["entries"]}
    assert flags["available start date"] and flags["co-op term"] and not flags["email"], flags
    other_bank = get("/api/answers?bank=other")
    assert other_bank["bank"] == "other" and [(e["question"], e["answer"]) for e in other_bank["entries"]] == [("available start date", "2027-05-24")]
    main_text = open(os.path.join(STATE, "local", "answers.json")).read()
    entry = {"question": "Graduation Month", "answer": "May 2028", "field_type": "text", "always_ask": False, "aliases": []}
    blank = {"question": "middle name", "answer": "", "field_type": "", "always_ask": False, "aliases": [], "leave_blank": True}
    saved = post("/api/answers", {"bank": "other", "entries": other_bank["entries"] + [entry, blank], "version": other_bank["version"]})
    assert [e["question"] for e in saved["entries"]] == ["available start date", "graduation month", "middle name"]
    assert saved["entries"][2]["leave_blank"] and open(os.path.join(STATE, "local", "answers.json")).read() == main_text
    edited = [dict(e, nuworks_only=(e["question"] == "email") or e["nuworks_only"]) for e in main_bank["entries"]]
    saved = post("/api/answers", {"entries": edited, "version": main_bank["version"]})
    on_disk = {e["question"]: e for e in json.load(open(os.path.join(STATE, "local", "answers.json")))}
    assert on_disk["email"]["nuworks_only"] is True and "nuworks_only" not in on_disk["available start date"]  # stored only when not the default

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
        assert page.locator("thead th", has_text="NUworks only").count() == 1
        page.click("[data-testid=bank-other]")
        page.wait_for_selector("[data-testid=bank-other][aria-pressed=true]")
        questions = [x.input_value() for x in page.locator("tbody input[aria-label=Question]").all()]
        assert questions == ["available start date", "graduation month", "middle name"], questions
        assert page.locator("thead th", has_text="NUworks only").count() == 0
        page.click("[data-testid=bank-nuworks]")
        page.wait_for_selector("[data-testid=bank-nuworks][aria-pressed=true]")
        # Other jobs: Add a job opens the Sheet screen on the Other jobs tab; the job shows up ready for the assistant
        page.click("[data-testid=nav-other]")
        page.wait_for_selector("[data-testid=other-2]")
        page.click("[data-testid=other-add]")
        page.wait_for_selector("[data-testid=add-tab-other][aria-pressed=true]")
        page.fill("[data-testid=add-url]", "https://jobs.example.net/3")
        page.fill("[data-testid=add-company]", "Dune Optics")
        page.fill("[data-testid=add-title]", "Optics Intern")
        page.click("[data-testid=add-submit]")
        page.wait_for_selector("[data-testid=sheet-other] li:has-text('Dune Optics')")
        assert page.input_value("[data-testid=add-url]") == ""
        # a NUworks link: the tab switches and company / title come from the job data; then remove it again
        page.click("[data-testid=add-tab-other]")
        page.fill("[data-testid=add-url]", "https://northeastern-csm.symplicity.com/students/app/jobs/detail/900106")
        page.press("[data-testid=add-url]", "Tab")
        page.wait_for_selector("[data-testid=add-tab-nuworks][aria-pressed=true]")
        assert page.input_value("[data-testid=add-company]") == "Fern Data Co"
        page.click("[data-testid=add-status-Proposed]")
        page.click("[data-testid=add-submit]")
        item = page.locator("[data-testid=sheet-nuworks] li:has-text('Fern Data Co')")
        item.wait_for()
        assert "Proposed" in item.inner_text()
        item.locator("button:has-text('Remove')").click()
        page.click("[data-testid=confirm-yes]")
        item.wait_for(state="detached")
        page.click("[data-testid=nav-other]")
        dune = page.locator("li:has-text('Dune Optics')")
        dune.wait_for()
        dune.locator("button:has-text('Start assistant')").click()
        page.wait_for_selector("[data-testid=terminal-command]")
        assert page.inner_text("[data-testid=terminal-command]").endswith("assist other 3")
        page.keyboard.press("Escape")
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
