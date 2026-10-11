"""The GUI (gui.py + gui_static/) in demo mode. Run: python test_gui.py

Starts `nuauto gui --demo`-style (a demo folder, then `nuauto gui --no-open`) and checks: the local server's
security rules, its API, the single-instance lock, and the main flows in headless Firefox (Review -> approve,
Apply -> a question -> submitted, Answers, Settings, Quit, Review's internship list -> the Other jobs tab; the last
also starts a second server with internships off). No network: everything runs on demo.py's fakes.
"""
import http.client
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse

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


@__import__("contextlib").contextmanager
def hold_slot(n, info):
    """Hold assistant slot n's lock, as a running `nuauto assist` does (assist.take_slot), with what runs there."""
    import fcntl
    os.makedirs(os.path.join(STATE, "local"), exist_ok=True)
    with open(os.path.join(STATE, "local", f"assist_slot{n}.lock"), "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        f.truncate()
        f.write(json.dumps({**info, "slot": n, "pid": os.getpid(), "since": "2026-10-10T12:00"}))
        f.flush()
        yield


def eventually(check, what, seconds=20):
    """check() until it returns something truthy (the page writes a decision behind the screen); that value."""
    end = time.time() + seconds
    while time.time() < end:
        got = check()
        if got:
            return got
        time.sleep(0.2)
    raise AssertionError(f"timed out waiting for: {what}")


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
    assert applying and all({"closes", "closes_text", "past", "soon", "company_site", "match", "pay", "pay_hour", "score", "score_text"} <= set(r) for r in applying), applying
    assert all(isinstance(r["match"], int) for r in applying) and any(r["pay"] for r in applying), applying
    post("/api/action", {"kind": "apply", "args": {"row": 1}}, expect=409)       # the header row: not an Approved row
    post("/api/action", {"kind": "apply", "args": {"row": "2; rm"}}, expect=409)
    checks = {c["id"]: c for c in get("/api/health")["checks"]}
    assert checks["google"]["status"] == "ok" and checks["files"]["status"] == "ok", checks
    rev = get("/api/review?mode=approve")
    # search and kind-of-work filter: only narrow what is shown; the counts are of the whole queue
    assert rev["total"] == len(rev["jobs"]) and sum(c["count"] for c in rev["categories"]) == rev["total"], rev
    cat0 = rev["categories"][0]["key"]
    sec = get(f"/api/review?mode=approve&category={cat0}")
    assert sec["jobs"] and all(j["category"] == cat0 for j in sec["jobs"]) and sec["total"] == rev["total"], sec
    one = rev["jobs"][0]
    hit = get("/api/review?mode=approve&q=" + urllib.parse.quote(f'"{one["company"].upper()}"'))["jobs"]
    assert one["id"] in [j["id"] for j in hit] and len(hit) < len(rev["jobs"]), hit
    assert get("/api/review?mode=approve&q=zzqxnotaword")["jobs"] == []
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

    # internships (intern.py): Review's second list, from Simplify's list. Read-only here; approving one is in the page below
    assert get("/api/state")["internships"] == {"on": True, "term": "Summer 2027", "review": 5}  # 4 scored + 1 unreadable
    irev = get("/api/review?mode=approve&source=intern")
    assert irev["source"] == "intern" and rev["source"] == "nuworks", (irev["source"], rev["source"])
    inames = [j["title"] for j in irev["jobs"]]
    assert irev["total"] == len(inames) == 5 and inames[0] == "Firmware Engineering Intern", inames
    assert inames[-1] == "Robotics Software Intern" and irev["jobs"][-1]["match"] is None, irev["jobs"][-1]  # unreadable: last
    assert all(isinstance(j["match"], int) for j in irev["jobs"][:-1]) and "could not be read" in irev["jobs"][-1]["order"], irev["jobs"]
    HARBOR, PINE = "https://jobs.example-ats.com/harbor/4101", "https://jobs.example-ats.com/pine/4106"
    icard = get("/api/job/" + irev["jobs"][0]["id"])
    assert icard["source"] == "simplify" and icard["url"] == HARBOR and icard["match"] == 82 and not icard["unread"], icard
    assert icard["external"] is None and icard["posted_text"].startswith("posted ") and icard["year_text"] == "", icard
    iunread = get("/api/job/" + irev["jobs"][-1]["id"])  # no match, no threshold, nothing to read: the page must cope
    assert iunread["unread"] and iunread["url"] == PINE and iunread["match"] is None and iunread["threshold"] is None, iunread
    assert iunread["description"] == [] and iunread["qualifications"] == [] and iunread["score"].startswith("not scored"), iunread
    # search, kind of work and rate mode work on it like on the co-ops
    assert [j["title"] for j in get("/api/review?mode=approve&source=intern&q=" + urllib.parse.quote('"harbor embedded"'))["jobs"]] == ["Firmware Engineering Intern"]
    assert [j["title"] for j in get("/api/review?mode=approve&source=intern&category=security")["jobs"]] == ["Security Engineering Intern"]
    irate = get("/api/review?mode=rate&source=intern")
    assert irate["source"] == "intern" and irate["jobs"] and "Robotics Software Intern" not in [j["title"] for j in irate["jobs"]], irate
    assert get("/api/review?mode=approve")["source"] == "nuworks"  # no source = the co-ops (their list is the server's again)
    post("/api/decide", {"id": irev["jobs"][0]["id"], "decision": "approve", "mode": "approve"}, expect=409)  # not in this list

    bank = get("/api/answers")
    entries = bank["entries"]
    post("/api/answers", {"entries": entries + [dict(entries[0])], "version": bank["version"]}, expect=409)  # duplicate
    post("/api/answers", {"entries": entries, "version": bank["version"] - 1}, expect=409)              # stale
    edited = [dict(e) for e in entries]
    edited[0]["answer"] = "Demo S. Student"
    saved = post("/api/answers", {"entries": edited, "version": bank["version"]})
    assert saved["entries"][0]["answer"] == "Demo S. Student"
    post("/api/settings", {"week_start": "next tuesday"}, expect=409)
    # the company-site assistant: account email, effort, the folders it may read; its logins as Bitwarden's import file
    s0 = get("/api/settings")
    assert s0["logins"] == {"sites": 0, "new": 0} and s0["efforts"] == ["low", "medium", "high"], s0
    post("/api/settings", {"accounts_email": "not an email"}, expect=409)
    post("/api/settings", {"assist_effort": "max"}, expect=409)
    s1 = post("/api/settings", {"accounts_email": " demo.student@example.com ", "assist_effort": "medium",
                                "assist_read_paths": ["/tmp/notes", " "]})["settings"]
    assert (s1["accounts_email"], s1["assist_effort"], s1["assist_read_paths"]) == ("demo.student@example.com", "medium", ["/tmp/notes"]), s1
    post("/api/logins/export", {}, expect=409)  # nothing to export yet
    FAKE_PW = "Xy9!demo-not-real"
    with open(os.path.join(STATE, "local", "accounts.json"), "w") as f:
        json.dump({"acme.wd5.myworkdayjobs.com": {"site": "acme.wd5.myworkdayjobs.com", "url": "https://acme.wd5.myworkdayjobs.com/",
                                                  "email": "demo.student@example.com", "password": FAKE_PW, "company": "Acme",
                                                  "created": "2026-10-10T12:00", "jobs": [], "exported": False}}, f)
    s2 = get("/api/settings")
    assert s2["logins"] == {"sites": 1, "new": 1} and FAKE_PW not in json.dumps(s2)  # counts only: the page never gets a password
    exp = post("/api/logins/export", {})
    assert exp["count"] == 1 and exp["logins"] == {"sites": 1, "new": 0} and exp["path"].startswith(STATE), exp  # demo: never ~/Downloads
    assert os.stat(exp["path"]).st_mode & 0o777 == 0o600 and json.load(open(exp["path"]))["items"][0]["login"]["password"] == FAKE_PW
    post("/api/logins/export", {}, expect=409)  # exported already
    assert post("/api/logins/export", {"every": True})["count"] == 1
    post("/api/settings", {"accounts_email": "", "assist_effort": "low", "assist_read_paths": []})  # back as they were
    # assistants side by side: a running one (its slot's lock held, as by a live `nuauto assist`) shows on its row
    assert get("/api/company")["sessions"] == [] and get("/api/company")["slots"] == 3 and get("/api/state")["assistants"] == ""
    with hold_slot(1, {"row": 7, "tab": "other", "company": "Basalt Bio", "host": "jobs.example.net"}):
        assert [(s["slot"], s["row"], s["tab"]) for s in get("/api/other")["sessions"]] == [(1, 7, "other")]
        assert get("/api/state")["assistants"] == "other-7:1"
    assert get("/api/other")["sessions"] == [] and get("/api/state")["assistants"] == ""  # it ended: the slot is free
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
    assert get("/api/other") == {"exists": False, "tab": "Other jobs", "ready": [], "applied": [], "rest": [], "sessions": [], "slots": 3}
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
    # an Applied Other job: never removed, but it may move to the NUworks tab (there it counts toward the limits)
    data = json.load(open(other_file))
    data["rows"].append(["https://jobs.example.net/5", "Pumice", "Intern", "Applied", "", "2020-01-02"])
    json.dump(data, open(other_file, "w"))
    n = len(data["rows"])
    entry = next(r for r in get("/api/sheet")["tabs"]["other"]["rows"] if r["row"] == n)
    assert entry["locked"] is None and "Applied" in entry["remove_locked"], entry
    post("/api/sheet", {"action": "remove", "tab": "other", "row": n, "url": "https://jobs.example.net/5"}, expect=409)
    moved = post("/api/sheet", {"action": "move", "tab": "other", "row": n, "url": "https://jobs.example.net/5",
                                "new_url": NU + "999998"})
    row = main_rows()[moved["row"] - 1]
    assert row[:4] == [NU + "999998", "Pumice", "Intern", "Applied"] and row[5] == "2020-01-02", row
    assert get("/api/state")["week"]["total"] == week_before["total"] + 1
    entry = next(r for r in get("/api/sheet")["tabs"]["nuworks"]["rows"] if r["row"] == moved["row"])
    assert entry["locked"] and entry["remove_locked"]  # in the NUworks tab it stays put
    post("/api/sheet", {"action": "move", "tab": "nuworks", "row": moved["row"], "url": NU + "999998",
                        "new_url": "https://jobs.example.net/5"}, expect=409)
    data = json.load(open(main_file))
    data["rows"][moved["row"] - 1] = [""] * 6  # undo by hand, so the counts below are as before
    json.dump(data, open(main_file, "w"))
    post("/api/health/run", {"groups": ["sheet"]})  # the GUI keeps rows for a minute: read them again now
    for _ in range(100):
        if get("/api/state")["week"] == week_before:
            break
        time.sleep(0.2)
    assert get("/api/state")["week"] == week_before

    # an approved internship's Undo still works after the list moved on (approved = in the sheet = not in the next list)
    beacon = next(j for j in get("/api/review?mode=approve&source=intern")["jobs"] if j["title"] == "Frontend Engineering Intern")
    done = post("/api/decide", {"id": beacon["id"], "decision": "approve", "mode": "approve"})
    assert done["tab"] == "other" and done["row"], done
    assert beacon["id"] not in [j["id"] for j in get("/api/review?mode=approve&source=intern")["jobs"]]  # the list moved on
    post("/api/undo", {"id": beacon["id"], "row": done["row"], "previous": done["previous"], "tab": "other"})
    assert json.load(open(other_file))["rows"][done["row"] - 1][3] == "Proposed"
    post("/api/undo", {"id": "s" + "0" * 32, "row": done["row"], "previous": None, "tab": "other"}, expect=409)  # not a listing
    post("/api/sheet", {"action": "remove", "tab": "other", "row": done["row"], "url": "https://jobs.example-ats.com/beacon/4105"})
    assert get("/api/state")["internships"]["review"] == 5  # back in Review (its row cleared), counted again

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
        # a status chip: its popover's Check now runs that check again and shows the new result in place
        page.click("[data-testid=health-sheet]")
        page.click("[data-testid=recheck-sheet]")
        page.wait_for_selector("[data-testid=recheck-sheet]:not([disabled])", timeout=30000)
        assert page.locator("[data-testid=health-popover]").count() == 1
        page.keyboard.press("Escape")
        page.click("h1, #page-title")
        page.wait_for_selector("[data-testid=health-popover]", state="detached")

        page.click("[data-testid=nav-review]")
        page.wait_for_selector("[data-testid=job-card]")
        page.fill("[data-testid=review-search]", "zzqxnotaword")  # a search with no hits, then cleared
        page.wait_for_selector("[data-testid=review-nomatch]")
        page.click("[data-testid=review-nomatch] button")
        page.wait_for_selector("[data-testid=job-card]")
        # a search with hits shows every match as its own card (buttons on each); clearing it comes back to the job you were on
        first_title = page.inner_text("[data-testid=job-title]")
        first_company = page.inner_text("[data-testid=job-card] .meta").split(" · ")[0]
        hits = get("/api/review?mode=approve&q=" + urllib.parse.quote(f'"{first_company}"'))["jobs"]
        page.fill("[data-testid=review-search]", f'"{first_company}"')
        page.wait_for_selector("[data-testid=review-list] [data-testid=job-card]")
        assert page.locator("[data-testid=review-list] .slot").count() == min(len(hits), 8)
        page.wait_for_function("n => document.querySelectorAll('[data-testid=review-list] [data-testid=btn-approve]').length === n", arg=min(len(hits), 8))
        assert page.locator("[data-testid=review-list] [data-testid=job-expand]").count() == min(len(hits), 8)  # long postings are cut short
        page.fill("[data-testid=review-search]", "")
        page.wait_for_selector("[data-testid=review-list]", state="detached")
        page.wait_for_selector("[data-testid=job-card]")
        assert page.inner_text("[data-testid=job-title]") == first_title
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

        page.evaluate("location.hash = '#/company'")  # Company sites is a part of Apply now: old links land there
        page.wait_for_function("() => location.hash === '#/apply'")
        page.wait_for_selector("[data-testid=company-agent] li:has-text('Iron Valley Medical')")  # lands on the Company sites tab
        assert page.get_attribute("[data-testid=tab-company]", "aria-selected") == "true"
        assert page.locator("[data-testid=nav-company]").count() == 0 and page.locator("[data-testid=nav-other]").count() == 0
        assert "match" in page.inner_text("[data-testid=company-agent] [data-testid=job-facts] >> nth=0")  # match / pay there too
        page.wait_for_selector("[data-testid=other-jobs]")  # Other jobs is a card in Company sites
        page.click("[data-testid=tab-manual]")  # rows only you can finish on NUworks
        if get("/api/company")["other"]:
            page.wait_for_selector("[data-testid=manual-nuworks]")
        else:
            page.wait_for_selector("[data-testid=apply-pane] .empty")
        assert page.locator("[data-testid=approved-list]").count() == 0 and page.locator("[data-testid=company-agent]").count() == 0
        page.click("[data-testid=tab-nuworks]")
        page.wait_for_selector("[data-testid=btn-start-apply]:not([disabled])")
        assert page.locator("[data-testid=approved-list] li").count() == 4
        assert page.locator("[data-testid=approved-list] [data-testid=due]").count() == 4  # each row shows its due date
        assert page.locator("[data-testid=approved-list] button[data-testid^=btn-apply-row-]").count() == 4  # one Apply each
        assert "% match" in page.inner_text("[data-testid=approved-list] [data-testid=job-facts] >> nth=0")
        # the order menu IS the order Start goes: the server sorts (apply.apply_order), the numbers follow it
        start_order = page.locator("[data-testid=approved-list] li").evaluate_all("ls => ls.map(l => l.dataset.testid)")
        assert page.locator("[data-testid=apply-sort] option[value=default]").inner_text().startswith("closing within a week")
        assert "Apply in this order" in page.inner_text("[data-testid=apply-pane] .row.end")
        page.select_option("[data-testid=apply-sort]", "match")
        by_match = ["approved-%d" % r["row"] for r in get("/api/apply?order=match")["rows"]]
        page.wait_for_function("ids => [...document.querySelectorAll('[data-testid=approved-list] li')].map(l => l.dataset.testid).join() === ids.join()", arg=by_match)
        facts = page.locator("[data-testid=approved-list] [data-testid=job-facts]").all_inner_texts()
        found = [int(re.search(r"(\d+)% match", t).group(1)) for t in facts]
        assert found == sorted(found, reverse=True), facts
        numbers = page.locator("[data-testid=approved-list] li .what > span.muted").all_inner_texts()
        assert numbers == [f"{k}. " for k in range(1, len(numbers) + 1)] or [n.strip() for n in numbers] == [f"{k}." for k in range(1, len(numbers) + 1)], numbers
        closes = [r["closes"] for r in get("/api/apply?order=closes")["rows"]]
        assert [c for c in closes if c] == sorted(c for c in closes if c) and closes[:len([c for c in closes if c])] == [c for c in closes if c], closes
        page.select_option("[data-testid=apply-sort]", "default")
        page.wait_for_function("ids => [...document.querySelectorAll('[data-testid=approved-list] li')].map(l => l.dataset.testid).join() === ids.join()", arg=start_order)
        # one kind of work: only those rows show (Start still goes through all of them); "All" brings the rest back
        kinds = page.locator("[data-testid=apply-kind] option").evaluate_all("os => os.map(o => [o.value, o.textContent])")
        assert kinds[0][0] == "" and kinds[0][1].endswith(f"({len(start_order)})"), kinds
        first = kinds[1][0]
        want = int(re.search(r"\((\d+)\)$", kinds[1][1]).group(1))
        page.select_option("[data-testid=apply-kind]", first)
        page.wait_for_timeout(200)
        assert page.locator("[data-testid=approved-list] li").count() == want
        if want < len(start_order):
            assert "Start still goes through all" in page.inner_text("[data-testid=apply-filter-note]")
        page.select_option("[data-testid=apply-kind]", "")
        page.wait_for_timeout(200)
        assert page.locator("[data-testid=approved-list] li").count() == len(start_order)
        page.fill("[data-testid=apply-n]", "7")  # what you type in "At most" stays when you click elsewhere / the page redraws
        page.click("h1, #page-title")
        page.wait_for_timeout(3500)
        assert page.input_value("[data-testid=apply-n]") == "7"
        page.fill("[data-testid=apply-n]", "")
        with page.expect_request(lambda r: r.url.endswith("/api/action") and r.method == "POST") as started:
            page.click("[data-testid=btn-start-apply]")
        assert started.value.post_data_json["args"]["order"] == "default", started.value.post_data_json  # Start sends the order shown
        page.wait_for_selector("[data-testid=question]", timeout=90000)
        assert "18 years old" in page.inner_text("[data-testid=q-label]")
        assert "Select" not in page.locator("[data-testid=question] .choices").inner_text()  # placeholder hidden
        page.get_by_role("button", name="Yes", exact=True).click()
        page.wait_for_function("() => document.body.innerText.includes('Last run: done') || "
                               "document.querySelector('[data-testid=q-menu-u]')", timeout=120000)
        assert page.locator("[data-testid=q-menu-u]").count() == 0, "unexpected: NUworks' confirmation was not seen"
        assert page.locator("[data-testid=apply-shot]").count() == 1
        # the finished rows leave the Approved list by themselves (no reopening): Applied / Needs Human rows are not Approved
        page.wait_for_function("() => !document.querySelector('[data-testid=approved-list]')", timeout=20000)
        rows = {r[1]: r[3] for r in json.load(open(os.path.join(STATE, "local", "demo_sheet_DEMO-SHEET.json")))["rows"][1:]}
        assert rows["Harbor Embedded"] == rows["Lumen Security"] == rows[approved_company] == "Applied", rows
        # Cobalt was approved, then undone (API part above): it is a Proposed row again, so Review showed it first and
        # this click approved that same row (no second row), and it was applied
        assert approved_company == "Cobalt Systems" and rows["Cobalt Systems"] == "Applied", (approved_company, rows)
        all_rows = json.load(open(os.path.join(STATE, "local", "demo_sheet_DEMO-SHEET.json")))["rows"][1:]
        assert sum(r[1] == "Cobalt Systems" for r in all_rows) == 1, all_rows
        assert rows["Quarry Hardware"] == "Needs Human", rows
        # the external job stopped (nothing submitted) and now waits on the same page, ready for the assistant
        page.click("[data-testid=tab-company]")
        page.wait_for_selector("[data-testid=company-agent] li:has-text('Quarry Hardware') button:has-text('Start assistant')")
        assert page.locator("[data-testid=approved-list]").count() == 0  # not on this tab

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

        page.click("[data-testid=nav-apply]")
        page.click("[data-testid=tab-company]")
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
        # Other jobs (in Apply > Company sites; old #/other links land there): Add a job opens the Sheet screen on the
        # Other jobs tab; the job shows up ready for the assistant
        page.evaluate("location.hash = '#/other'")
        page.wait_for_function("() => location.hash === '#/apply'")
        page.wait_for_selector("[data-testid=other-2]")
        page.click("[data-testid=other-add]")
        page.wait_for_selector("[data-testid=add-tab-other][aria-pressed=true]")
        page.fill("[data-testid=add-url]", "https://jobs.example.net/3")
        page.fill("[data-testid=add-company]", "Dune Optics")
        page.fill("[data-testid=add-title]", "Optics Intern")
        page.click("[data-testid=add-submit]")
        page.wait_for_selector("[data-testid=sheet-other] li:has-text('Dune Optics')")
        assert page.input_value("[data-testid=add-url]") == ""
        assert page.locator("[data-testid=sheet-nuworks]").count() == 0  # one tab at a time, like Apply
        page.click("[data-testid=sheet-tab-nuworks]")
        page.wait_for_selector("[data-testid=sheet-nuworks]")
        assert page.locator("[data-testid=sheet-other]").count() == 0
        page.click("[data-testid=sheet-tab-other]")
        page.wait_for_selector("[data-testid=sheet-other] li:has-text('Dune Optics')")
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
        page.click("[data-testid=nav-apply]")
        page.click("[data-testid=tab-company]")
        dune = page.locator("li:has-text('Dune Optics')")
        dune.wait_for()
        dune.locator("button:has-text('Start assistant')").click()
        page.wait_for_selector("[data-testid=terminal-command]")
        assert page.inner_text("[data-testid=terminal-command]").endswith("assist other 3")
        page.keyboard.press("Escape")

        # ---- Review's second list: the term's internships (Simplify's list). Approve adds a row to the Other jobs tab (not
        # the NUworks one); the posting is the company's own page ("Open posting"). Search, kinds of work, keys, list mode
        # and rate mode work as on the co-ops; the one whose posting could not be read is last and has nothing to score.
        def other_row(url):  # the Other jobs tab's row for a link, read fresh from the sheet
            tab = get("/api/other")
            return next((r for r in tab["ready"] + tab["applied"] + tab["rest"] if r["url"] == url), None)

        def review_again():  # a new Review screen: its cards are fetched again, its list is the server's current one
            page.click("[data-testid=nav-home]")
            page.click("[data-testid=nav-review]")
        other_rows = lambda: json.load(open(other_file))["rows"]
        filled = lambda: sum(1 for r in other_rows()[1:] if any(c.strip() for c in r))  # rows with something in them (cleared ones are blank)
        ratings_now = lambda: json.load(open(os.path.join(STATE, "data", "ratings.json")))
        iid = {j["title"]: j["id"] for j in irev["jobs"]}
        harbor_first = "() => document.querySelector('[data-testid=job-title]')?.innerText === 'Firmware Engineering Intern'"
        no_junk = lambda text: not re.search(r"\b(null|undefined|NaN)\b", text)
        page.click("[data-testid=nav-review]")  # Review opens on the co-ops; the switch next to Approve / Rate only shows the internships
        page.wait_for_selector("[data-testid=source-nuworks][aria-pressed=true]")
        assert page.get_attribute("[data-testid=source-intern]", "aria-pressed") == "false"
        assert page.inner_text("[data-testid=source-nuworks]") == "NUworks co-ops"
        assert page.inner_text("[data-testid=source-intern]") == "Summer 2027 internships"
        page.wait_for_selector("[data-testid=job-card]")
        assert page.inner_text("[data-testid=btn-open]").startswith("Open on NUworks")
        page.click("[data-testid=source-intern]")
        page.wait_for_selector("[data-testid=source-intern][aria-pressed=true]")
        page.wait_for_function(harbor_first)
        assert page.get_attribute("[data-testid=source-nuworks]", "aria-pressed") == "false"
        assert page.evaluate("sessionStorage.getItem('reviewSource')") == "intern"
        # its card: the posting is on the company's own site; the facts say when it was posted
        assert page.get_attribute("[data-testid=btn-open]", "href") == HARBOR
        assert page.inner_text("[data-testid=btn-open]").startswith("Open posting") and "NUworks" not in page.inner_text(".decide")
        facts = page.inner_text("[data-testid=job-card] .facts")
        assert f"{icard['match']}% · needs {icard['threshold']}%" in facts and "Posted" in facts, facts
        assert icard["posted_text"].removeprefix("posted ") in facts and "Who can apply" not in facts, facts
        assert no_junk(page.inner_text("[data-testid=job-card]"))

        # "Who can apply": the posting's own words, a row only when it has some. The demo's postings have none, so the page's
        # own jobCard() builds this card twice, as the server sent it and with some added (a card is only DOM nodes)
        built = page.evaluate("""async (id) => {
            const c = await window.NUauto.api.get('/api/job/' + id);
            const labels = (card) => [...card.querySelectorAll('.facts dt')].map((n) => n.textContent);
            const withYear = window.jobCard({ ...c, year_text: "Rising seniors pursuing a Bachelor's degree" }, {}, null);
            const dt = [...withYear.querySelectorAll('.facts dt')].find((n) => n.textContent === 'Who can apply');
            return { plain: labels(window.jobCard(c, {}, null)), added: labels(withYear), words: dt && dt.nextElementSibling.textContent };
        }""", icard["id"])
        assert "Who can apply" not in built["plain"] and built["plain"][:2] == ["Match", "Score"], built
        assert built["words"] == "Rising seniors pursuing a Bachelor's degree", built
        assert built["added"].index("Closes") < built["added"].index("Who can apply") < built["added"].index("Pay"), built
        page.set_viewport_size({"width": 420, "height": 900})  # phone width: the two switches wrap, nothing sticks out sideways
        page.wait_for_timeout(200)
        assert page.evaluate("document.documentElement.scrollWidth") <= 420
        page.set_viewport_size({"width": 1200, "height": 900})
        # Today: a button for the internships; it opens Review on them even when the co-ops were the last list shown
        # (the co-ops' button stays as it was)
        page.click("[data-testid=source-nuworks]")
        page.wait_for_selector("[data-testid=source-nuworks][aria-pressed=true]")
        page.click("[data-testid=nav-home]")
        assert get("/api/state")["internships"]["review"] == 5  # 4 scored + the unreadable one
        page.wait_for_function("() => document.querySelector('[data-testid=go-review-intern]')?.innerText === 'Review 5 internships'")
        page.click("[data-testid=go-review-intern]")
        page.wait_for_selector("[data-testid=source-intern][aria-pressed=true]")
        page.wait_for_function(harbor_first)

        # Approve (button): the row goes to the Other jobs tab, behind the screen; the next job shows at once
        assert other_row(HARBOR) is None
        n_before = filled()
        page.click("[data-testid=btn-approve]")
        approved = page.locator("[data-testid=toast]", has_text="Approved: adding it to the Other jobs tab.")
        approved.wait_for()
        page.wait_for_function("() => document.querySelector('[data-testid=job-title]').innerText !== 'Firmware Engineering Intern'")
        added = eventually(lambda: other_row(HARBOR), "the approved internship in the Other jobs tab")
        assert added["status"] == "Approved" and added["notes"].startswith("Summer 2027 internship (Simplify's list)"), added
        assert (added["company"], added["title"]) == ("Harbor Embedded", "Firmware Engineering Intern"), added
        harbor_row = added["row"]
        assert filled() == n_before + 1 and other_rows()[harbor_row - 1][0] == HARBOR
        assert HARBOR not in [r["url"] for r in get("/api/sheet")["tabs"]["nuworks"]["rows"]]  # the Other jobs tab, not the NUworks one
        assert HARBOR in [r["url"] for r in get("/api/other")["ready"]]  # ready for the assistant
        page.wait_for_function("() => window.NUauto.S.state.internships.review === 4")  # the page has the answer (the row) by now
        page.keyboard.press("u")  # back: the decided job says where its row is
        page.wait_for_function(harbor_first)
        assert page.inner_text("[data-testid=job-card] .chip.ok") == f"approved · Other jobs row {harbor_row}"
        # Undo (the toast): the same row goes back to Proposed, and the request says it is in the Other jobs tab
        with page.expect_request(lambda r: r.url.endswith("/api/undo") and r.method == "POST") as undo:
            approved.locator("button", has_text="Undo").click()
        assert undo.value.post_data_json == {"id": icard["id"], "row": harbor_row, "previous": None, "tab": "other"}, undo.value.post_data_json
        page.locator("[data-testid=toast]", has_text="Undone: the row is back to Proposed (Other jobs tab).").wait_for()
        eventually(lambda: (r := other_row(HARBOR)) and r["status"] == "Proposed" and r["row"] == harbor_row, "the row back to Proposed")
        assert filled() == n_before + 1
        # a new Review: it shows first, flagged; Approve (y) turns that same row Approved again (no second row)
        review_again()
        page.wait_for_function(harbor_first)
        facts = page.inner_text("[data-testid=job-card] .facts")
        assert f"already Proposed (Other jobs row {harbor_row})" in facts and "your Proposed row in the Other jobs tab" in facts, facts
        page.keyboard.press("y")
        page.locator("[data-testid=toast]", has_text="Approved: adding it to the Other jobs tab.").last.wait_for()
        again = eventually(lambda: (r := other_row(HARBOR)) and r["status"] == "Approved" and r, "the same row Approved again")
        assert again["row"] == harbor_row and filled() == n_before + 1, (again, filled(), n_before)
        assert sum(r[0] == HARBOR for r in other_rows()) == 1  # no second row for it
        assert not any(r["url"] == HARBOR and r["row"] != harbor_row for r in get("/api/other")["ready"])

        # the one whose posting could not be read: no match to show, no margin math, nothing about it throws; it is last
        page.select_option("[data-testid=review-category]", "uncategorized")
        page.wait_for_function("() => document.querySelector('[data-testid=job-title]')?.innerText === 'Robotics Software Intern'")
        facts = page.inner_text("[data-testid=job-card] .facts")
        assert "not scored (the posting could not be read)" in facts and "Score" not in facts and "needs" not in facts, facts
        assert "Could not read it:" in page.inner_text("[data-testid=job-card] .why") and no_junk(page.inner_text("[data-testid=job-card]"))
        assert page.get_attribute("[data-testid=btn-open]", "href") == PINE
        page.select_option("[data-testid=review-category]", "")

        # a search shows every match as its own card, with its own buttons; the unreadable one is last, with nothing to cut short
        listed = [j["title"] for j in get("/api/review?mode=approve&source=intern&q=intern")["jobs"]]
        assert listed[-1] == "Robotics Software Intern" and "Firmware Engineering Intern" not in listed, listed  # Approved: in the sheet
        page.fill("[data-testid=review-search]", "intern")
        page.wait_for_function("n => document.querySelectorAll('[data-testid=review-list] [data-testid=btn-approve]').length === n", arg=len(listed))
        assert page.locator("[data-testid=review-list] [data-testid=job-title]").all_inner_texts() == listed
        assert set(page.locator("[data-testid=review-list] [data-testid=btn-open]").all_inner_texts()) == {"Open posting"}
        assert page.locator("[data-testid=review-list] [data-testid=btn-open]").evaluate_all(
            "links => links.every(a => a.href.startsWith('https://jobs.example-ats.com/'))")
        assert page.locator("[data-testid=review-list] [data-testid=job-expand]").count() == len(listed) - 1
        page.set_viewport_size({"width": 420, "height": 900})
        page.wait_for_timeout(200)
        assert page.evaluate("document.documentElement.scrollWidth") <= 420
        page.set_viewport_size({"width": 1200, "height": 900})
        # ...Approve there, then change your mind (Not for me): that undo says "other" too; the row is Proposed, rated no
        sec_slot = page.locator(f"[data-testid=review-list] .slot[data-job='{iid['Security Engineering Intern']}']")
        sec_url = get("/api/job/" + iid["Security Engineering Intern"])["url"]
        sec_slot.locator("[data-testid=btn-approve]").click()
        sec_slot.locator("[data-testid=list-decided]").wait_for()
        sec_row = eventually(lambda: other_row(sec_url), "Security approved in the Other jobs tab")
        assert sec_row["status"] == "Approved" and sec_row["notes"].startswith("Summer 2027 internship (Simplify's list)"), sec_row
        sec_slot.locator("button", has_text="Change").click()
        with page.expect_request(lambda r: r.url.endswith("/api/undo") and r.method == "POST") as undo:
            sec_slot.locator("[data-testid=btn-no]").click()
        assert undo.value.post_data_json["tab"] == "other" and undo.value.post_data_json["row"] == sec_row["row"], undo.value.post_data_json
        eventually(lambda: (r := other_row(sec_url)) and r["status"] == "Proposed", "Security back to Proposed")
        eventually(lambda: ratings_now().get(iid["Security Engineering Intern"], {}).get("label") == 0, "Security rated no")
        # rate mode: yes / no ratings only, the sheet stays as it is
        page.click("[data-testid=mode-rate]")
        page.wait_for_selector("[data-testid=review-list] [data-testid=btn-yes]")
        rate_slot = page.locator("[data-testid=review-list] .slot").first
        rated = rate_slot.get_attribute("data-job")
        assert rated in iid.values() and rated not in (iid["Robotics Software Intern"], iid["Firmware Engineering Intern"])
        sheet_now = other_rows()
        rate_slot.locator("[data-testid=btn-yes]").click()
        eventually(lambda: ratings_now().get(rated, {}).get("label") == 1, "an internship rated yes")
        assert other_rows() == sheet_now
        page.click("[data-testid=mode-approve]")
        # ...and when the list is done: where the approved ones are
        page.fill("[data-testid=review-search]", "")
        page.wait_for_selector("[data-testid=review-list]", state="detached")
        left = len(get("/api/review?mode=approve&source=intern")["jobs"])
        page.click("h1, #page-title")  # focus off the search box: the keys work
        for _ in range(left + 2):
            page.keyboard.press("s")
            page.wait_for_timeout(150)
        page.wait_for_selector("[data-testid=review-empty]")
        assert page.inner_text("[data-testid=review-empty] p") == "Approved internships are in the Other jobs tab. Apply to them in Apply > Company sites."
        assert page.get_attribute("[data-testid=review-empty] a", "href") == "#/company"
        page.click("[data-testid=review-empty] a")  # the Company sites tab of Apply: the Other jobs card is there
        page.wait_for_function("() => location.hash === '#/apply'")
        page.wait_for_selector("[data-testid=tab-company][aria-selected=true]")
        page.wait_for_selector(f"[data-testid=other-{harbor_row}]")
        # the internship's row shows its match % and pay; with an assistant on it (another window), the row says so
        # instead of offering a second one, and offers Start again once that one ends
        assert "82% match" in page.inner_text(f"[data-testid=other-{harbor_row}] [data-testid=job-facts]")
        with hold_slot(2, {"row": harbor_row, "tab": "other", "company": "Harbor Embedded", "host": "jobs.example-ats.com"}):
            page.wait_for_selector(f"[data-testid=other-assist-{harbor_row}-running]", timeout=20000)
            assert page.inner_text(f"[data-testid=other-assist-{harbor_row}-running]") == "assistant running (slot 2)"
            assert "1 of 3 assistants running" in page.inner_text("[data-testid=assistants-running]")
        page.wait_for_selector(f"[data-testid=other-assist-{harbor_row}]", timeout=20000)
        # Start: greyed out for a few seconds (a second click would start the same job twice), then back
        start = page.locator(f"[data-testid=other-assist-{harbor_row}]")
        start.click()
        assert start.is_disabled() and start.inner_text() == "Starting…"
        page.locator("[data-testid=terminal-command]").wait_for()  # demo: the command it would run in a terminal
        assert page.inner_text("[data-testid=terminal-command]").endswith(f"assist other {harbor_row}")
        page.keyboard.press("Escape")
        page.wait_for_function(f"() => !document.querySelector('[data-testid=other-assist-{harbor_row}]').disabled", timeout=10000)
        # Today's co-op button opens the co-ops (the choice is remembered, so it says which); the internships stay one click away
        page.click("[data-testid=nav-home]")
        page.wait_for_function("() => document.querySelector('[data-testid=go-review-intern]')?.innerText === 'Review 3 internships'")
        page.click("[data-testid=go-review]")
        page.wait_for_selector("[data-testid=source-nuworks][aria-pressed=true]")
        assert page.evaluate("sessionStorage.getItem('reviewSource')") == "nuworks"
        coops = get("/api/state")["counts"]["review"]
        assert coops, "the demo still has co-ops to review"
        page.wait_for_selector("[data-testid=job-card]")
        assert page.inner_text("[data-testid=btn-open]").startswith("Open on NUworks")
        facts = page.inner_text("[data-testid=job-card] .facts")
        assert "Posted" not in facts and "Who can apply" not in facts and "needs" in facts, facts
        page.click("h1, #page-title")  # ...and when their list is done: their own wording and link, not the internships'
        for _ in range(coops + 2):
            page.keyboard.press("s")
            page.wait_for_timeout(150)
        page.wait_for_selector("[data-testid=review-empty]")
        assert page.inner_text("[data-testid=review-empty] p") == "Approved jobs are in your sheet. Apply to them next."
        assert page.get_attribute("[data-testid=review-empty] a", "href") == "#/apply"
        page.click("[data-testid=source-intern]")  # the choice is kept for the session
        page.wait_for_selector("[data-testid=source-intern][aria-pressed=true]")
        page.reload()
        page.wait_for_selector("[data-testid=source-intern][aria-pressed=true]")
        page.click("[data-testid=source-nuworks]")
        # internships off (no "internships" in local_config.json): no switch, no Today button, the server refuses the list, and a
        # remembered "intern" is ignored: always the co-ops. Its own demo folder and server
        off_state = tempfile.mkdtemp(prefix="nuauto-test-gui-off-")
        off_env = {**ENV, "NUAUTO_STATE_DIR": off_state}
        subprocess.run([PY, "-m", "nuauto.demo", "setup"], env=off_env, check=True, cwd=ROOT)
        off_config = os.path.join(off_state, "local", "local_config.json")
        config = json.load(open(off_config))
        del config["internships"]
        json.dump(config, open(off_config, "w"))
        off = subprocess.Popen([PY, "-m", "nuauto", "gui", "--no-open"], env=off_env, cwd=ROOT, stdout=subprocess.PIPE,
                               stderr=open(os.path.join(off_state, "gui-stderr.log"), "w"), text=True)
        try:
            off_page = page.context.new_page()
            off_page.goto(json.loads(off.stdout.readline())["url"])
            off_page.wait_for_selector("[data-testid=week]")
            assert off_page.evaluate("window.NUauto.S.state.internships") == {"on": False, "term": "Summer 2027", "review": 0}
            assert off_page.locator("[data-testid=go-review-intern]").count() == 0
            assert off_page.evaluate("fetch('/api/review?mode=approve&source=intern').then((r) => r.status)") == 409
            off_page.evaluate("sessionStorage.setItem('reviewSource', 'intern')")  # as if remembered from when they were on
            with off_page.expect_request(lambda r: "/api/review?" in r.url) as asked:
                off_page.click("[data-testid=nav-review]")
            assert "source=nuworks" in asked.value.url and "source=intern" not in asked.value.url, asked.value.url
            off_page.wait_for_selector("[data-testid=mode-approve]")
            assert off_page.locator("[data-testid=source-intern]").count() == 0 and off_page.locator("[data-testid=source-nuworks]").count() == 0
            off_page.close()
        finally:
            off.terminate()
            off.wait(timeout=30)

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
