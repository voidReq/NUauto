"""The GUI (gui.py + gui_static/) in demo mode. Run: python test_gui.py

Starts `nuauto gui --demo`-style (a demo folder, then `nuauto gui --no-open`) and checks: the local server's
security rules, its API, the single-instance lock, and the main flows in headless Firefox (Review -> approve,
Apply -> a question -> submitted, Answers, Settings, Quit, Review's internship list -> the Other jobs tab; the last
also starts a second server with internships off). No network: everything runs on demo.py's fakes.
"""
import base64
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

import screens

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
    # quick: the list made before the decision comes at once (stale: a new one is on its way), without the job just
    # decided here; the next ask without quick waits for the new list
    quick = get("/api/review?mode=approve&quick=1")
    assert quick["stale"] and first not in [j["id"] for j in quick["jobs"]], quick
    # Review's list is kept between loads and made again when what it comes from changes (here: the sheet, a rating)
    fresh = get("/api/review?mode=approve")
    assert not fresh["stale"] and first not in [j["id"] for j in fresh["jobs"]]
    assert get("/api/review?mode=approve&quick=1")["stale"] is False  # nothing changed since: the kept list, fresh
    post("/api/undo", {"id": first, "row": r["row"], "previous": None})
    rows = json.load(open(os.path.join(STATE, "local", "demo_sheet_DEMO-SHEET.json")))["rows"]
    assert rows[r["row"] - 1][3] == "Proposed" and first not in json.load(open(os.path.join(STATE, "data", "ratings.json")))
    assert first in [j["id"] for j in get("/api/review?mode=approve")["jobs"]]  # your Proposed row again
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
    # the transcript the assistant may upload: a PDF that exists, or empty
    post("/api/settings", {"transcript_path": "/nowhere/transcript.pdf"}, expect=409)
    tpdf = os.path.join(STATE, "transcript.pdf")
    open(tpdf, "w").write("%PDF-1.4")
    post("/api/settings", {"transcript_path": tpdf.replace(".pdf", ".txt")}, expect=409)
    assert post("/api/settings", {"transcript_path": f" {tpdf} "})["settings"]["transcript_path"] == tpdf
    assert post("/api/settings", {"transcript_path": ""})["settings"]["transcript_path"] == ""
    # your files: a PDF given to NUauto is kept as its own copy in local/ (mode 600) and used from then on
    b64 = lambda data: base64.b64encode(data).decode()
    with open(os.path.join(STATE, "Demo_Student_Resume.pdf"), "rb") as f:
        demo_resume = f.read()
    post("/api/files", {"kind": "cover_letter", "data": b64(demo_resume)}, expect=409)
    for bad in (b64(b"not a pdf"), "%%%", b64(b"%PDF-1.4"), b64(b"%PDF-" + b"0" * 10_000_000)):  # no text / over 10 MB
        post("/api/files", {"kind": "resume", "data": bad}, expect=409)
    kept = os.path.join(STATE, "local", "resume.pdf")
    assert not os.path.exists(kept) and not os.path.exists(kept + ".tmp")  # a refused one leaves nothing
    given = post("/api/files", {"kind": "resume", "data": b64(demo_resume)})
    assert given["settings"]["resume_path"] == kept and given["files"]["resume"]["size"] == len(demo_resume), given["files"]
    with open(kept, "rb") as f:
        assert f.read() == demo_resume and os.stat(kept).st_mode & 0o777 == 0o600
    post("/api/files", {"kind": "resume", "remove": True}, expect=409)  # a resume is only ever replaced
    tkept = os.path.join(STATE, "local", "transcript.pdf")
    given = post("/api/files", {"kind": "transcript", "data": b64(demo_resume)})
    assert given["settings"]["transcript_path"] == tkept and given["files"]["transcript"]["name"] == "transcript.pdf"
    given = post("/api/files", {"kind": "transcript", "remove": True})
    assert given["settings"]["transcript_path"] == "" and given["files"]["transcript"] is None and not os.path.exists(tkept)
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
    # the password manager (Bitwarden, optional): off to start, in settings with its counts only (never a password); what
    # is accepted; the homelab assistant's settings need a homelab (demo has none); the terminal commands it opens
    # (demo: only shown) hold no password, and the site texts are checked before they reach a shell
    local_config = os.path.join(STATE, "local", "local_config.json")
    pm0, ha0 = s0["password_manager"], s0["homelab_assistant"]
    assert (pm0["on"], pm0["waiting"], pm0["own"], pm0["bw"], pm0["only"]) == (False, 0, 0, False, ["myworkday"]), pm0
    assert ha0["available"] is False and ha0["on"] is False, ha0
    for bad in ("keepass", "Bitwarden", 1, True, None, ["bitwarden"]):
        post("/api/settings", {"password_manager": bad}, expect=409)
    for bad in ("myworkday", ["a"], ["x" * 61], ["two words"], ["a/b"], ["a;rm -rf"], ["$(id)"], ["ok.site", "bad site"], None):
        post("/api/settings", {"bitwarden_import": bad}, expect=409)
    assert "bitwarden_import" not in json.load(open(local_config))
    for body in ({"remote_assist": True}, {"remote_auto": True}, {"remote_hours": [9, 22]},
                 {"remote_assist": "yes"}, {"remote_hours": [22, 9]}):
        post("/api/settings", body, expect=409)  # no homelab (server_hostname): refused before anything is saved
    cfg_now = json.load(open(local_config))
    assert not any(k in cfg_now for k in ("remote_assist", "remote_auto", "remote_hours")), cfg_now
    s3 = post("/api/settings", {"password_manager": "bitwarden", "bitwarden_import": [" myworkday ", "", "amazon.jobs"]})
    assert s3["password_manager"]["on"] and s3["password_manager"]["only"] == ["myworkday", "amazon.jobs"], s3["password_manager"]
    assert json.load(open(local_config))["password_manager"] == "bitwarden"
    with open(os.path.join(STATE, "local", "accounts.json"), "w") as f:  # waiting = made by NUauto, not exported; own never counts
        json.dump({"a.example.com": {"site": "a.example.com", "password": FAKE_PW},
                   "b.example.com": {"site": "b.example.com", "exported": True},
                   "c.example.com": {"site": "c.example.com", "own": True, "password": FAKE_PW}}, f)
    pm1 = get("/api/settings")["password_manager"]
    assert (pm1["on"], pm1["waiting"], pm1["own"]) == (True, 1, 1) and FAKE_PW not in json.dumps(get("/api/settings")), pm1
    assert pm1["check"]["id"] == "bitwarden"
    os.remove(os.path.join(STATE, "local", "accounts.json"))
    r = post("/api/action", {"kind": "bitwarden_add", "args": {}})
    assert r["opened"] is False and r["demo"] and "accounts export --bitwarden" in r["command"], r
    assert r["command"].startswith('BW_SESSION="$(bw unlock --raw)" && export BW_SESSION && '), r["command"]
    assert "--session" not in r["command"] and FAKE_PW not in r["command"]
    r = post("/api/action", {"kind": "bitwarden_login", "args": {}})
    assert r["demo"] and r["command"] == "bw login", r
    r = post("/api/action", {"kind": "bitwarden_install", "args": {}})
    assert r["demo"] and r["command"] == "npm install -g @bitwarden/cli && bw login", r
    # import: the saved texts when none come with the click; bad or no texts refused; the texts are saved
    post("/api/settings", {"bitwarden_import": []})
    post("/api/action", {"kind": "bitwarden_import", "args": {}}, expect=409)  # none saved, none given
    post("/api/action", {"kind": "bitwarden_import", "args": {"only": [" ", ""]}}, expect=409)
    for bad in (["bad text"], ["a;rm -rf /"], ["x"], ["ok.site", "$(id)"], ["a|b"], ["`id`"], ["x" * 61]):
        post("/api/action", {"kind": "bitwarden_import", "args": {"only": bad}}, expect=409)
    assert json.load(open(local_config)).get("bitwarden_import") == []
    r = post("/api/action", {"kind": "bitwarden_import", "args": {"only": [" myworkday ", "amazon.jobs"]}})
    assert r["opened"] is False and r["demo"], r
    exe = re.search(r"\| (\S+) accounts import - --only myworkday", r["command"]).group(1)  # this install's nuauto command
    assert r["command"] == ('BW_SESSION="$(bw unlock --raw)" && export BW_SESSION && bw sync && '
                            f"bw list items --search myworkday | {exe} accounts import - --only myworkday && "
                            f"bw list items --search amazon.jobs | {exe} accounts import - --only amazon.jobs"), r["command"]
    assert json.load(open(local_config))["bitwarden_import"] == ["myworkday", "amazon.jobs"]
    r2 = post("/api/action", {"kind": "bitwarden_import", "args": {}})  # no texts given: the saved ones
    assert r2["command"] == r["command"]
    post("/api/settings", {"password_manager": "", "bitwarden_import": ["myworkday"]})  # off again (the page part below turns it on)
    assert get("/api/settings")["password_manager"]["on"] is False
    # assistants side by side: a running one (its slot's lock held, as by a live `nuauto assist`) shows on its row
    # (the state string is "<this computer's>|<the homelab's>": empty on both sides without remote_assist; each
    # session with what it waits for, so the open screen updates when that changes)
    assert get("/api/company")["sessions"] == [] and get("/api/company")["slots"] == 3 and get("/api/state")["assistants"] == "|"
    with hold_slot(1, {"row": 7, "tab": "other", "company": "Basalt Bio", "host": "jobs.example.net"}):
        assert [(s["slot"], s["row"], s["tab"]) for s in get("/api/other")["sessions"]] == [(1, 7, "other")]
        assert get("/api/state")["assistants"] == "other-7:1:|"
    assert get("/api/other")["sessions"] == [] and get("/api/state")["assistants"] == "|"  # it ended: the slot is free
    # the laptop's page: nothing is the laptop's only (it is the laptop); the homelab's own parts are not here
    st = get("/api/state")
    assert st["where"] == "laptop" and st["laptop_only"] == {} and st["remote_app"] is None
    assert not [t for t in st["todo"] if t["kind"] == "assistant"]               # no assistant waits for you here
    with hold_slot(1, {"row": 7, "tab": "other", "company": "Basalt Bio", "host": "jobs.example.net", "run": STATE}):
        # one waiting for you (its attention file, as Claude Code's hooks write it): Today's first to-do, in its window
        att = os.path.join(STATE, "attention.json")
        json.dump({"kind": "approve", "since": time.time(), "said": "Ready"}, open(att, "w"))
        try:
            first = get("/api/state")["todo"][0]
            assert first["kind"] == "assistant" and first["status"] == "approve" and first["screen"] is None, first
        finally:
            os.remove(att)
    assert get("/api/company")["queue"] is None
    assert request("GET", "/api/assistant/1")[0] == 404 and request("GET", "/api/assistant/1/shot.png")[0] == 404
    post("/api/assistant", {"slot": 1, "do": "approve", "id": "x"}, expect=409)
    post("/api/action", {"kind": "pair_device", "args": {"name": "x"}}, expect=409)
    post("/api/action", {"kind": "unpair", "args": {"id": "0" * 12}}, expect=409)
    post("/api/devices", {}, expect=404)
    assert request("GET", "/pair?c=x")[0] == 404 and request("GET", "/s/1")[0] == 404            # (the homelab's only)
    # Settings > Homelab: check one (the demo answers with made-up facts, no ssh), then what can be saved
    assert get("/api/settings")["homelab"]["available"] and not get("/api/settings")["homelab"]["on"]
    probe = post("/api/homelab", {"action": "check", "ssh": "homelab"})
    assert probe["ok"] and probe["tailscale"] == "100.64.0.42"
    post("/api/homelab", {"action": "save", "ssh": "homelab", "hostname": "lab", "folder": "projects/auto", "listen": "0.0.0.0"},
         expect=409)
    post("/api/homelab", {"action": "setup"}, expect=409)                       # no homelab used yet
    post("/api/homelab", {"action": "deploy", "on": True}, expect=409)
    post("/api/homelab", {"action": "nope"}, expect=409)
    # what a user sets for themselves, all in Settings (or setup): the writeups, assistants at once (no homelab needed),
    # the Mark-done page's address, the Gmail login, Summer internships (on / off, term, bar, lists)
    s = post("/api/settings", {"assist_writeups": [" ~/notes/writeups ", ""], "assist_slots": 2})
    assert s["settings"]["assist_writeups"] == ["~/notes/writeups"] and s["assist_slots"] == 2
    assert json.load(open(local_config))["assist_slots"] == 2
    post("/api/settings", {"assist_slots": 3})
    for bad in ("http://x.example", "nope", "https://x example"):
        post("/api/settings", {"web_base_url": bad}, expect=409)
    assert post("/api/settings", {"web_base_url": "https://mark.example.com/"})["settings"]["web_base_url"] == "https://mark.example.com"
    post("/api/settings", {"web_base_url": ""})
    assert "Demo" in post("/api/action", {"kind": "login_gmail"}, expect=409)["error"]
    assert get("/api/settings")["gmail"] == {"ready": False, "days_left": None}
    was_interns = json.load(open(local_config)).get("internships")
    for bad in ({"on": "yes"}, {"on": True, "term": ""}, {"on": True, "threshold": 0}, {"on": True, "threshold": "x"},
                {"on": True, "lists": {"nope": ""}}, {"on": True, "lists": {"simplify": "http://plain.example/x"}}):
        post("/api/settings", {"internships": bad}, expect=409)
    ii = post("/api/settings", {"internships": {"on": True, "term": " Summer  2028 ", "threshold": "62",
                                                "lists": {"simplify": "https://raw.example.com/S2028/listings.json", "vansh": ""}}})["internships"]
    assert ii["on"] and ii["term"] == "Summer 2028" and ii["threshold"] == 62 and ii["lists"]["vansh"] == ""
    assert get("/api/state")["internships"]["term"] == "Summer 2028"                  # this process took it at once
    assert post("/api/settings", {"internships": {"on": True, "threshold": ""}})["internships"]["threshold"] is None
    assert post("/api/settings", {"internships": {"on": False}})["internships"]["on"] is False
    assert json.load(open(local_config))["internships"] is False
    post("/api/settings", {"internships": {"on": True}})
    cfg = json.load(open(local_config))
    cfg["internships"] = was_interns
    json.dump(cfg, open(local_config, "w"))
    get("/api/settings")
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
    # Drop (not applying): Failed with the reason in Notes, so it leaves the list; an Applied row is never dropped
    added = post("/api/other/add", {"url": "https://jobs.example.net/2", "company": "Shale Co", "title": "Dup Intern"})
    post("/api/mark", {"action": "drop", "why": "bored", "tab": "other", "row": added["row"], "url": "https://jobs.example.net/2"}, expect=409)
    post("/api/mark", {"action": "drop", "why": "closed", "tab": "other", "row": 4, "url": "https://jobs.example.net/1"}, expect=409)
    post("/api/mark", {"action": "drop", "why": "duplicate", "tab": "other", "row": added["row"], "url": "https://jobs.example.net/2"})
    row = json.load(open(other_file))["rows"][added["row"] - 1]
    assert row[3] == "Failed" and row[4].startswith("Dropped ") and "duplicate" in row[4], row
    assert [x["company"] for x in get("/api/other")["ready"]] == ["Granite Robotics"]
    assert get("/api/state")["week"] == week_before  # the Other jobs tab has no weekly / total cap
    r = post("/api/action", {"kind": "assist", "args": {"row": 2, "tab": "other"}})
    assert r["demo"] and r["command"].endswith("assist other 2"), r
    # a row marked "do it on the laptop" (assist.LAPTOP_MARK at the end of its Notes) says so on every list that shows rows
    LAPTOP = "DO ON THE LAPTOP: the homelab's browser was refused"
    data = json.load(open(other_file))
    assert all(x["on_laptop"] is False for x in other["ready"] + other["applied"])
    # the Apply screen is answered from the rows read last; opening it (recheck) reads both tabs again behind it, and
    # sheet_version moves only when a read finds a change (then the open screen reloads its lists)
    get("/api/jobs?tab=other&recheck=1")
    time.sleep(1.5)  # (whatever changed before this is read now)
    version = get("/api/state")["sheet_version"]
    get("/api/jobs?tab=other&recheck=1")
    time.sleep(1.5)
    assert get("/api/state")["sheet_version"] == version  # read again, nothing changed
    data["rows"][1][4] = f"match 90% | {LAPTOP} (the site showed it a sign-in page with no way in)"
    json.dump(data, open(other_file, "w"))
    get("/api/jobs?tab=other&recheck=1")
    for _ in range(50):
        if get("/api/state")["sheet_version"] != version:
            break
        time.sleep(0.1)
    assert [x["on_laptop"] for x in get("/api/jobs?tab=other")["sections"]["ready"] if x["row"] == 2] == [True]
    marked = get("/api/other")
    assert [(x["row"], x["on_laptop"]) for x in marked["ready"]] == [(2, True)] and marked["applied"][0]["on_laptop"] is False, marked
    assert marked["ready"][0]["notes"].startswith("match 90% | DO ON THE LAPTOP")
    data["rows"][1][4] = ""
    json.dump(data, open(other_file, "w"))
    assert get("/api/other")["ready"][0]["on_laptop"] is False
    # no tab yet: adding the first job makes it (headers, then the job as row 2)
    os.remove(other_file)
    assert get("/api/other") == {"exists": False, "tab": "Other jobs", "ready": [], "applied": [], "rest": [], "sessions": [], "slots": 3, "homelab": [], "homelab_review": None, "linked": False}
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

    # a row that stopped on NUworks for something only you can give: the assistant may work it there (hand); one whose
    # Submit click is unresolved may not, and says why
    data = json.load(open(main_file))
    n_hand = len(data["rows"]) + 1
    data["rows"] += [[NU + "999990", "Basalt", "Intern", "Needs Human", "Popup requires Cover Letter: attach by hand on NUworks.", ""],
                     [NU + "999991", "Gneiss", "Intern", "Needs Human", "SUBMIT CLICKED - confirm the outcome on NUworks", ""]]
    json.dump(data, open(main_file, "w"))
    post("/api/health/run", {"groups": ["sheet"]})
    for _ in range(100):
        manual = {r["company"]: r for r in get("/api/jobs?tab=nuworks")["sections"]["manual"]}
        if "Basalt" in manual:
            break
        time.sleep(0.2)
    assert manual["Basalt"]["hand"] and manual["Basalt"]["row"] == n_hand and manual["Basalt"]["hand_why"] is None, manual
    assert not manual["Gneiss"]["hand"] and "unresolved" in manual["Gneiss"]["hand_why"], manual
    data["rows"] = data["rows"][:n_hand - 1]
    json.dump(data, open(main_file, "w"))
    post("/api/health/run", {"groups": ["sheet"]})
    for _ in range(100):
        if not any(r["company"] == "Basalt" for r in get("/api/jobs?tab=nuworks")["sections"]["manual"]):
            break
        time.sleep(0.2)

    # what you heard back: Accepted / Rejected on a sent row (its own section; it still counts toward the limits: it went
    # out), back to Applied again; never on a row that wasn't sent
    week_now = get("/api/state")["week"]
    sent = get("/api/jobs?tab=nuworks")["sections"]["applied"][0]
    post("/api/mark", {"action": "outcome", "outcome": "Rejected", "row": sent["row"], "url": sent["url"]})
    secs = get("/api/jobs?tab=nuworks")["sections"]
    assert [r["row"] for r in secs["rejected"]] == [sent["row"]] and sent["row"] not in [r["row"] for r in secs["applied"]]
    assert get("/api/state")["week"] == week_now and secs["rejected"][0]["notes"].startswith("Rejected (marked ")
    post("/api/mark", {"action": "outcome", "outcome": "Applied", "row": sent["row"], "url": sent["url"]})
    assert sent["row"] in [r["row"] for r in get("/api/jobs?tab=nuworks")["sections"]["applied"]]
    approved_one = get("/api/jobs?tab=nuworks")["sections"]["approved"][0]
    post("/api/mark", {"action": "outcome", "outcome": "Accepted", "row": approved_one["row"], "url": approved_one["url"]}, expect=409)
    post("/api/mark", {"action": "outcome", "outcome": "Failed", "row": sent["row"], "url": sent["url"]}, expect=409)
    # the job's hiring stage at the last check (demo.setup's answers; sheet.maybe_filled): the chips on Applied rows
    stage = {r["row"]: (r["nuworks"]["hiring"], r["nuworks"]["done"])
             for r in get("/api/jobs?tab=nuworks")["sections"]["applied"] if r.get("nuworks")}
    assert stage == {2: ("Interviewing", None), 3: ("Available", "stopped interviewing"), 4: ("Filled", "filled")}, stage
    # NUworks' answer about a sent row (the twice-daily update: data/applications.json): "not on NUworks" when it has none
    with open(os.path.join(STATE, "data", "applications.json"), "w") as f:
        json.dump({sent["url"].rstrip("/").split("/")[-1]: {"applied": False, "status": None, "expired": False,
                   "interviewing": False, "checked": "2026-10-16T08:10", "row": sent["row"], "fingerprint": "x"}}, f)
    nu = next(r for r in get("/api/jobs?tab=nuworks")["sections"]["applied"] if r["row"] == sent["row"])["nuworks"]
    assert nu["applied"] is False and nu["current"] is False  # (about an earlier version of the row: it still counts)
    os.remove(os.path.join(STATE, "data", "applications.json"))

    # an approved internship's Undo still works after the list moved on (approved = in the sheet = not in the next list)
    beacon = next(j for j in get("/api/review?mode=approve&source=intern")["jobs"] if j["title"] == "Frontend Engineering Intern")
    done = post("/api/decide", {"id": beacon["id"], "decision": "approve", "mode": "approve"})
    assert done["tab"] == "other" and done["row"], done
    assert beacon["id"] not in [j["id"] for j in get("/api/review?mode=approve&source=intern")["jobs"]]  # the list moved on
    # its row on Apply says when it closes (its posting's own date: demo.intern_setup), the others when they were posted
    ready = {x["row"]: x for x in get("/api/jobs?tab=other&recheck=1")["sections"]["ready"]}
    assert ready[done["row"]]["closes"] and not ready[done["row"]]["soon"] and "(in 20 days)" in ready[done["row"]]["closes_text"], ready
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

    # other wordings: a wording with commas stays whole; one that does nothing (its own question, a repeat, another
    # entry's above, which wins) is left out without a word; a question that is another entry's wording is refused
    other_bank = get("/api/answers?bank=other")
    worded = {"question": "visa", "answer": "No", "field_type": "", "always_ask": False,
              "aliases": ["Do you need sponsorship (e.g. OPT, CPT, or STEM OPT)?", "visa", "VISA ", "graduation month",
                          "do you need sponsorship (e.g. opt, cpt, or stem opt)?"]}
    saved = post("/api/answers", {"bank": "other", "entries": other_bank["entries"] + [worded], "version": other_bank["version"]})
    assert saved["entries"][-1]["aliases"] == ["do you need sponsorship (e.g. opt, cpt, or stem opt)?"], saved["entries"][-1]
    clash = {"question": "Do you need sponsorship (e.g. OPT, CPT, or STEM OPT)?", "answer": "Yes", "field_type": "",
             "always_ask": False, "aliases": []}
    body = post("/api/answers", {"bank": "other", "entries": saved["entries"] + [clash], "version": saved["version"]}, expect=409)
    assert "another wording of 'visa'" in body["error"], body
    post("/api/answers", {"bank": "other", "entries": saved["entries"][:-1], "version": saved["version"]})  # (as it was)

    # a second start opens a window on this one instead (here: --no-open, so it just says so and exits)
    second = subprocess.run([PY, "-m", "nuauto", "gui", "--no-open"], env=ENV, cwd=ROOT, capture_output=True, text=True,
                            timeout=60)
    assert second.returncode != 0 and "already running" in second.stderr, second.stderr
    # from a Claude Code shell the real app opens its own window only: --no-open would print the agent its secret
    real = {k: v for k, v in ENV.items() if k != "NUAUTO_DEMO"} | {"NUAUTO_STATE_DIR": tempfile.mkdtemp(), "CLAUDECODE": "1"}
    r = subprocess.run([PY, "-m", "nuauto", "gui", "--no-open"], env=real, cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert r.returncode != 0 and "opens its own window only" in r.stderr, r.stderr
    # ...and the line it prints there names its address, never the start's secret (demo and view mode: all of it)
    from nuauto import gui as gui_mod
    secret_url = "http://127.0.0.1:5/?t=SECRET"
    was = (os.environ.get("CLAUDECODE"), gui_mod.config.DEMO, gui_mod.VIEW)
    try:
        os.environ["CLAUDECODE"] = "1"
        gui_mod.config.DEMO, gui_mod.VIEW = False, False
        assert gui_mod.printed_url(secret_url) == "http://127.0.0.1:5/"
        gui_mod.config.DEMO = True
        assert gui_mod.printed_url(secret_url) == secret_url
        gui_mod.config.DEMO = False
        os.environ.pop("CLAUDECODE")
        assert gui_mod.printed_url(secret_url) == secret_url  # your own terminal: the whole address, as before
    finally:
        os.environ.pop("CLAUDECODE", None)
        if was[0] is not None:
            os.environ["CLAUDECODE"] = was[0]
        gui_mod.config.DEMO, gui_mod.VIEW = was[1], was[2]

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
        # every screen, as test_homelab_app.py opens them on the homelab's page: the same menu, none empty or failing
        nav, bad = screens.visit_all(page, URL.split("/?")[0])
        assert nav == screens.NAV and not bad, (nav, bad)
        page.goto(URL.split("/?")[0] + "/#/home")
        page.wait_for_selector("[data-testid=week]")

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
        page.wait_for_selector("[data-testid=section-nuworks-agent] li:has-text('Iron Valley Medical')")  # the NUworks tab's Company site needed
        assert page.get_attribute("[data-testid=tab-nuworks]", "aria-selected") == "true"
        assert all(page.locator(f"[data-testid=nav-{n}]").count() == 0 for n in ("company", "other", "sheet"))  # no screens of their own
        # (a row with no match and no pay known shows no facts line; Quarry Hardware, after the run below, has both)
        assert page.locator("[data-testid=section-nuworks-agent] li:has-text('Iron Valley Medical') [data-testid=job-facts]").count() == 0
        # three tabs: the NUworks co-ops, the Other jobs tab (the internships' name), the assistants
        assert [page.inner_text(f"[data-testid=tab-{t}]").rsplit("\n", 1)[0] for t in ("nuworks", "other", "assistants")] == \
            ["NUworks co-ops", "Summer 2027 internships", "Assistants"]
        assert page.locator("[data-testid=tab-company]").count() == 0 and page.locator("[data-testid=tab-manual]").count() == 0
        # to-do sections are open, the finished ones folded until clicked (their rows are counted, not shown)
        assert page.get_attribute("[data-testid=section-nuworks-agent]", "open") is not None
        applied = page.locator("[data-testid=section-nuworks-applied]")
        assert applied.count() == 1 and applied.get_attribute("open") is None
        applied.locator("summary").click()
        assert applied.get_attribute("open") is not None and applied.locator("li").first.is_visible()
        # More: the same four for every row; Escape closes it; an Applied NUworks row can be neither moved nor removed
        applied_li = applied.locator("li").first
        applied_row = applied_li.get_attribute("data-testid").split("-")[-1]
        page.click(f"[data-testid=more-nuworks-{applied_row}]")
        page.wait_for_selector("div.menu")
        assert [b.inner_text() for b in page.locator("div.menu button").all()] == \
            ["Mark accepted", "Mark rejected", "I applied myself", "Drop (not applying)…", "Move to Summer 2027 internships…",
             "Remove from the sheet…"]  # (what you heard back: only on a sent row)
        for key in ("mark", "drop", "move", "remove"):
            assert page.locator(f"[data-testid=more-nuworks-{applied_row}-{key}]").is_disabled(), key  # (with a reason in its title)
        assert page.get_attribute(f"[data-testid=more-nuworks-{applied_row}-remove]", "title")
        assert page.locator(f"[data-testid=more-nuworks-{applied_row}-rejected]").is_enabled()
        page.keyboard.press("Escape")
        page.wait_for_selector("div.menu", state="detached")
        applied.locator("summary").click()
        page.click("[data-testid=tab-other]")
        page.wait_for_selector("[data-testid=section-other-ready]")  # the Other jobs tab is the second tab of Apply
        # a row marked "do it on the laptop" shows a chip next to its Apply button (the others do not)
        ready_now = get("/api/other")["ready"]
        assert ready_now, "the demo's Other jobs tab has an Approved row here"
        target = ready_now[0]["row"]
        other_data = json.load(open(other_file))
        old_notes = other_data["rows"][target - 1][4]
        other_data["rows"][target - 1][4] = (old_notes + " | " if old_notes else "") + LAPTOP + " (the site showed it a sign-in page with no way in)"
        json.dump(other_data, open(other_file, "w"))
        try:
            page.reload()
            page.click("[data-testid=tab-other]")
            page.wait_for_selector(f"[data-testid=other-assist-{target}-laptop]")
            chip = page.locator(f"[data-testid=other-assist-{target}-laptop]")
            assert chip.inner_text() == "do it on the laptop" and "warn" in chip.get_attribute("class")
            assert page.locator(f"[data-testid=job-other-{target}] [data-testid=other-assist-{target}]").count() == 1  # Start is still there
            assert page.locator("[data-testid^=other-assist-][data-testid$=-laptop]").count() == 1                # only the marked row
        finally:
            other_data["rows"][target - 1][4] = old_notes
            json.dump(other_data, open(other_file, "w"))
        page.reload()
        page.click("[data-testid=tab-other]")
        page.wait_for_selector("[data-testid=section-other-ready]")
        # (the first answer may be the rows read a moment ago; the sheet is read again behind it and the list follows)
        page.wait_for_selector("[data-testid$=-laptop]", state="detached")
        page.click("[data-testid=tab-nuworks]")
        manual = get("/api/jobs?tab=nuworks")["sections"]["manual"]  # rows only you can finish on NUworks: a section of the same tab
        assert page.locator("[data-testid=section-nuworks-manual] li").count() == len(manual)
        assert page.locator("[data-testid=section-nuworks-manual]").count() == (1 if manual else 0)  # none: no section
        page.wait_for_selector("[data-testid=btn-start-apply]:not([disabled])")
        assert page.locator("[data-testid=approved-list] li").count() == 4
        assert page.locator("[data-testid=approved-list] [data-testid=due]").count() == 4  # each row shows its due date
        assert page.locator("[data-testid=approved-list] button[data-testid^=btn-apply-row-]").count() == 4  # one Apply each
        assert "% match" in page.inner_text("[data-testid=approved-list] [data-testid=job-facts] >> nth=0")
        # /api/jobs: the same Approved rows, in Start's order, as /api/apply; every section is there for both tabs
        jobs = get("/api/jobs?tab=nuworks")
        assert [r["row"] for r in jobs["sections"]["approved"]] == [r["row"] for r in get("/api/apply")["rows"]]
        every = {"approved", "agent", "manual", "owed", "ready", "applied", "accepted", "rejected", "proposed", "closed", "rest"}  # (both tabs: the other tab's stay empty)
        assert set(jobs["sections"]) == every and jobs["sections"]["ready"] == [], list(jobs["sections"])
        assert jobs["tab"] == "nuworks" and jobs["order"] == "default" and jobs["week"] == get("/api/apply")["week"] and "history" in jobs
        assert [r["company"] for r in jobs["sections"]["agent"]] == ["Iron Valley Medical"], jobs["sections"]["agent"]
        assert get("/api/jobs?tab=nuworks&order=match")["order"] == "match"
        oj = get("/api/jobs?tab=other")
        assert oj["tab"] == "other" and oj["exists"] and set(oj["sections"]) == every, list(oj["sections"])
        assert not any(oj["sections"][k] for k in ("approved", "agent", "manual", "owed"))
        assert [(x["row"], x["company"]) for x in oj["sections"]["ready"]] == [(x["row"], x["company"]) for x in get("/api/other")["ready"]]
        assert get("/api/jobs?tab=elsewhere")["tab"] == "nuworks"  # anything but "other" is the NUworks tab
        # the order menu IS the order Start goes: the server sorts (apply.apply_order), the numbers follow it
        start_order = page.locator("[data-testid=approved-list] li").evaluate_all("ls => ls.map(l => l.dataset.testid)")
        assert start_order == [f"job-nuworks-{r['row']}" for r in jobs["sections"]["approved"]], start_order
        assert page.locator("[data-testid=apply-sort] option[value=default]").inner_text().startswith("Sort: closing within a week")
        assert page.get_attribute("[data-testid=apply-sort]", "aria-label") == "Apply in this order"
        assert page.get_attribute("[data-testid=apply-search]", "placeholder") == "Search this tab (Ctrl+F)"
        page.select_option("[data-testid=apply-sort]", "match")
        by_match = ["job-nuworks-%d" % r["row"] for r in get("/api/apply?order=match")["rows"]]
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
        assert kinds[0] == ["", "All kinds of work"] and all(re.search(r"\(\d+\)$", k[1]) for k in kinds[1:]), kinds  # (counts: every row of the tab)
        first = kinds[1][0]
        want = sum(1 for r in jobs["sections"]["approved"] if (r["category"] or "-") == first)  # Approved rows of that kind
        page.select_option("[data-testid=apply-kind]", first)
        page.wait_for_timeout(200)
        assert page.locator("[data-testid=approved-list] li").count() == want
        if 0 < want < len(start_order):
            assert f"Start still goes through all {len(start_order)}" in page.inner_text("[data-testid=apply-filter-note]")
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
        page.click("[data-testid=btn-run-close]")  # a finished run's box closes (and stays closed when the screen redraws)
        page.wait_for_selector("[data-testid=apply-run]", state="detached")
        page.wait_for_timeout(2500)
        assert page.locator("[data-testid=apply-run]").count() == 0
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
        page.click("[data-testid=tab-nuworks]")
        page.wait_for_selector("[data-testid=section-nuworks-agent] li:has-text('Quarry Hardware') button:text-is('Apply')")
        assert page.locator("[data-testid=approved-list]").count() == 0  # nothing approved is left
        quarry = page.inner_text("[data-testid=section-nuworks-agent] li:has-text('Quarry Hardware') [data-testid=job-facts]")
        assert "68% match" in quarry and "pay " in quarry, quarry  # match / pay on the company-site rows too

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
        page.click("[data-testid=tab-nuworks]")
        page.wait_for_selector("[data-testid=job-nuworks-10]")
        assert page.locator("[data-testid=job-nuworks-10] [data-testid=due]").count() == 1  # due date on the company-site row
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
        # internships are on in the demo: a switch to the internships' numbers (their pool, the Other jobs tab's statuses)
        assert ins["source"] == "nuworks" and page.locator("[data-testid=ins-source]").count() == 1
        assert page.get_attribute("[data-testid=ins-source-nuworks]", "aria-pressed") == "true"
        iins = get("/api/insights?source=intern")
        assert iins["source"] == "intern" and iins["internships"] == {"on": True, "term": "Summer 2027"} and iins["pool"]["count"] != ins["pool"]["count"], iins["pool"]
        page.click("[data-testid=ins-source-intern]")
        page.wait_for_selector("[data-testid=ins-source-intern][aria-pressed=true]")
        page.wait_for_function(f"() => document.querySelector('[data-testid=ins-pool]').innerText.includes('Your pool ({iins['pool']['count']})')")
        assert "internship applications" in page.inner_text("[data-testid=ins-status] h2")
        assert page.locator("[data-testid=ins-status] svg circle").count() == len(iins["statuses"])
        page.click("[data-testid=ins-source-nuworks]")
        page.wait_for_function(f"() => document.querySelector('[data-testid=ins-pool]').innerText.includes('Your pool ({ins['pool']['count']})')")
        assert "internship" not in page.inner_text("[data-testid=ins-status] h2")
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
        # Add a job (Apply's toolbar; old #/other links land on the internships tab): the job goes in the Other jobs tab
        page.evaluate("location.hash = '#/other'")
        page.wait_for_function("() => location.hash === '#/apply'")
        page.wait_for_selector("[data-testid=tab-other][aria-selected=true]")
        page.wait_for_selector("[data-testid=job-other-2]")
        page.click("[data-testid=add-job]")
        page.wait_for_selector("[data-testid=add-dialog] [data-testid=add-tab-other][aria-pressed=true]")  # it opens on this tab's kind
        page.fill("[data-testid=add-url]", "https://jobs.example.net/3")
        page.fill("[data-testid=add-company]", "Dune Optics")
        page.fill("[data-testid=add-title]", "Optics Intern")
        page.click("[data-testid=add-submit]")
        page.wait_for_selector("[data-testid=add-dialog]", state="detached")
        page.wait_for_selector(".toast:has-text('Added as row'):has-text('(Approved)')")
        page.wait_for_selector("[data-testid=section-other-ready] li:has-text('Dune Optics')")  # ready for the assistant
        assert page.locator("[data-testid=section-nuworks-agent]").count() == 0  # one tab at a time
        # a NUworks link: the dialog's kind switches and company / title come from the job data; the Proposed row goes to
        # the NUworks tab (folded: Proposed is not to do), and the page switches to that tab; remove it again from More
        page.click("[data-testid=add-job]")
        page.click("[data-testid=add-tab-other]")
        page.fill("[data-testid=add-url]", "https://northeastern-csm.symplicity.com/students/app/jobs/detail/900106")
        page.press("[data-testid=add-url]", "Tab")
        page.wait_for_selector("[data-testid=add-tab-nuworks][aria-pressed=true]")
        assert page.input_value("[data-testid=add-company]") == "Fern Data Co"
        page.click("[data-testid=add-status-Proposed]")
        page.click("[data-testid=add-submit]")
        page.wait_for_selector("[data-testid=add-dialog]", state="detached")
        page.wait_for_selector("[data-testid=tab-nuworks][aria-selected=true]")
        item = page.locator("[data-testid=section-nuworks-proposed] li:has-text('Fern Data Co')")
        item.wait_for(state="attached")
        assert page.get_attribute("[data-testid=section-nuworks-proposed]", "open") is None  # folded until clicked
        page.click("[data-testid=section-nuworks-proposed] summary")
        item.wait_for()
        fern = item.get_attribute("data-testid").split("-")[-1]
        page.click(f"[data-testid=more-nuworks-{fern}]")
        assert page.locator(f"[data-testid=more-nuworks-{fern}-remove]").is_enabled()
        assert page.locator(f"[data-testid=more-nuworks-{fern}-mark]").is_disabled()  # Proposed: not "I applied myself"
        page.click(f"[data-testid=more-nuworks-{fern}-remove]")
        page.click("[data-testid=confirm-yes]")
        item.wait_for(state="detached")
        # Move (from the More menu): Ember Labs goes from the Other jobs tab to the NUworks one, with a NUworks link
        page.click("[data-testid=tab-other]")
        page.click("[data-testid=add-job]")
        page.fill("[data-testid=add-url]", "https://jobs.example.net/4")
        page.fill("[data-testid=add-company]", "Ember Labs")
        page.fill("[data-testid=add-title]", "Thermal Intern")
        page.click("[data-testid=add-submit]")
        page.wait_for_selector("[data-testid=section-other-ready] li:has-text('Ember Labs')")
        ember = page.locator("[data-testid=section-other-ready] li:has-text('Ember Labs')").get_attribute("data-testid").split("-")[-1]
        page.click(f"[data-testid=more-other-{ember}]")
        page.click(f"[data-testid=more-other-{ember}-move]")
        page.wait_for_selector("[data-testid=move-dialog]")
        page.fill("[data-testid=move-url]", "https://northeastern-csm.symplicity.com/students/app/jobs/detail/999997")
        page.click("[data-testid=move-yes]")
        page.wait_for_selector("[data-testid=move-dialog]", state="detached")
        page.wait_for_selector("[data-testid=section-other-ready] li:has-text('Ember Labs')", state="detached")
        page.click("[data-testid=tab-nuworks]")
        moved_li = page.locator("[data-testid=approved-list] li:has-text('Ember Labs')")
        moved_li.wait_for()
        moved_row = moved_li.get_attribute("data-testid").split("-")[-1]
        page.click(f"[data-testid=more-nuworks-{moved_row}]")  # ...and removed again (its cells cleared)
        page.click(f"[data-testid=more-nuworks-{moved_row}-remove]")
        page.click("[data-testid=confirm-yes]")
        moved_li.wait_for(state="detached")
        # Drop (More > Drop): the same reasons as before; the row leaves the list and sits in Dropped or failed
        page.click("[data-testid=tab-other]")
        dune_row = page.locator("[data-testid=section-other-ready] li:has-text('Dune Optics')").get_attribute("data-testid").split("-")[-1]
        page.click(f"[data-testid=more-other-{dune_row}]")
        page.click(f"[data-testid=more-other-{dune_row}-drop]")
        page.wait_for_selector("[data-testid=drop-duplicate]")
        assert all(page.locator(f"[data-testid=drop-{w}]").count() == 1 for w in ("closed", "duplicate", "location", "other"))
        page.keyboard.press("Escape")
        page.wait_for_selector("[data-testid=drop-duplicate]", state="detached")
        assert page.locator("[data-testid=section-other-ready] li:has-text('Dune Optics')").count() == 1  # Escape dropped nothing
        # Ctrl+F: the screen's own search box (Apply: apply-search; Escape clears it), not the browser's find bar
        page.click("h1, #page-title")
        page.keyboard.press("Control+f")
        assert page.evaluate("document.activeElement && document.activeElement.dataset.testid") == "apply-search"
        page.keyboard.type("Dune")
        page.wait_for_function("() => document.querySelectorAll('[data-testid=section-other-ready] li').length === 1")
        page.keyboard.press("Escape")
        page.wait_for_function("() => document.querySelector('[data-testid=apply-search]').value === ''")
        page.click("[data-testid=nav-answers]")
        page.wait_for_selector("[data-testid=answers-search]")
        page.click("h1, #page-title")
        page.keyboard.press("Control+f")
        assert page.evaluate("document.activeElement && document.activeElement.dataset.testid") == "answers-search"
        page.click("[data-testid=nav-apply]")
        page.click("[data-testid=tab-other]")
        # the Assistants tab: the list of what runs now (nothing here yet)
        page.click("[data-testid=tab-assistants]")
        page.wait_for_selector("[data-testid=assistants-list]")
        assert page.locator("[data-testid=assistants-bar]").count() == 0  # (the line only shows once one runs, here or on the homelab)
        assert "No assistant is running" in page.inner_text("[data-testid=assistants-list] .empty")
        assert page.locator("[data-testid=apply-toolbar]").count() == 0 and page.locator("[data-testid=apply-week]").count() == 0
        page.click("[data-testid=tab-other]")
        dune = page.locator("[data-testid=section-other-ready] li:has-text('Dune Optics')")
        dune.wait_for()
        dune.locator("button:text-is('Apply')").click()
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
        # (the list made last may come first, its card from before the undo: the new list's card follows)
        page.locator("[data-testid=job-card] .facts", has_text=f"already Proposed (Other jobs row {harbor_row})").wait_for()
        facts = page.inner_text("[data-testid=job-card] .facts")
        assert "your Proposed row in the Other jobs tab" in facts, facts
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
        assert page.inner_text("[data-testid=review-empty] p") == "Approved internships wait in Apply > Summer 2027 internships."
        assert page.get_attribute("[data-testid=review-empty] a", "href") == "#/other"
        page.click("[data-testid=review-empty] a")  # Apply's internships tab (the Other jobs tab): they are in its Ready to apply
        page.wait_for_function("() => location.hash === '#/apply'")
        page.wait_for_selector("[data-testid=tab-other][aria-selected=true]")
        page.wait_for_selector(f"[data-testid=job-other-{harbor_row}]")
        # the internship's row shows its match % and pay; with an assistant on it (another window), the row says so
        # instead of offering a second one, and offers Start again once that one ends
        assert "82% match" in page.inner_text(f"[data-testid=job-other-{harbor_row}] [data-testid=job-facts]")
        with hold_slot(2, {"row": harbor_row, "tab": "other", "company": "Harbor Embedded", "host": "jobs.example-ats.com"}):
            page.wait_for_selector(f"[data-testid=other-assist-{harbor_row}-running]", timeout=20000)
            assert page.inner_text(f"[data-testid=other-assist-{harbor_row}-running]") == "assistant running (slot 2)"
            page.click("[data-testid=tab-assistants]")   # the one line and the list are on the Assistants tab
            assert "Assistants: 1 of 3 running here" in page.inner_text("[data-testid=assistants-running]")
            assert page.locator("[data-testid=assistants-bar]").count() == 1      # one line for the whole tab
            assert page.locator("[data-testid=assistants-list] li").count() == 1 and page.locator("[data-testid=assistant-here-2]").count() == 1
            assert page.inner_text("[data-testid=tab-assistants] .count") == "1"
            assert "in its terminal window" in page.inner_text("[data-testid=assistant-here-2]")  # a laptop session: no screen to open
            page.click("[data-testid=tab-other]")
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
            # ...Insights has no switch (and asking for the internships' numbers gives the co-ops'), Apply names its tab "Other jobs"
            assert off_page.evaluate("fetch('/api/insights?source=intern').then((r) => r.json()).then((d) => d.source)") == "nuworks"
            off_page.click("[data-testid=nav-insights]")
            off_page.wait_for_selector("[data-testid=ins-pool]")
            assert off_page.locator("[data-testid=ins-source]").count() == 0
            off_page.click("[data-testid=nav-apply]")
            off_page.wait_for_selector("[data-testid=tab-other]")
            assert off_page.inner_text("[data-testid=tab-other]").rsplit("\n", 1)[0] == "Other jobs"
            off_page.close()
        finally:
            off.terminate()
            off.wait(timeout=30)

        page.click("[data-testid=nav-settings]")
        page.wait_for_selector("[data-testid=check-google]")
        # Settings > Password manager: off to start (no status chip), Use Bitwarden turns it on (saved; demo has no bw, so
        # Install it shows and Add / Import are greyed out), the status strip gets its chip, Stop using it hides it again.
        # No homelab in demo: no Assistant on your homelab card.
        local_config = os.path.join(STATE, "local", "local_config.json")
        page.click("[data-testid=settings-tab-assistant]")
        page.wait_for_selector("[data-testid=pm-settings]")
        assert page.url.endswith("#/settings/assistant")
        assert page.locator("[data-testid=ha-settings]").count() == 0
        assert page.inner_text("[data-testid=pm-toggle]") == "Use Bitwarden"
        assert page.locator("[data-testid=pm-install]").count() == 0 and page.locator("[data-testid=health-bitwarden]").count() == 0
        page.click("[data-testid=pm-toggle]")
        page.wait_for_selector("[data-testid=pm-toggle]:has-text('Stop using it')")
        page.wait_for_selector("[data-testid=pm-install]")
        assert page.is_disabled("[data-testid=pm-add]") and page.is_disabled("[data-testid=pm-import]")
        assert json.load(open(local_config))["password_manager"] == "bitwarden"
        page.wait_for_selector("[data-testid=health-bitwarden]", timeout=20000)
        assert "warn" in page.get_attribute("[data-testid=health-bitwarden]", "class")  # demo: no bw
        assert eventually(lambda: get("/api/settings")["password_manager"]["check"]["fix"] == "bitwarden_install", "bw check")
        # the terminal commands: Install asks first; Add and Import show the command (demo opens nothing). Their buttons
        # are greyed out here (no bw), so enable them to go through the real click handlers
        page.click("[data-testid=pm-install]")
        page.click("[data-testid=confirm-yes]")
        page.wait_for_selector("[data-testid=terminal-command]")
        assert "npm install -g @bitwarden/cli" in page.inner_text("[data-testid=terminal-command]")
        page.click("[role=dialog] button:has-text('Close')")
        page.eval_on_selector("[data-testid=pm-add]", "(b) => { b.disabled = false; }")
        page.click("[data-testid=pm-add]")
        page.wait_for_selector("[data-testid=terminal-command]")
        cmd = page.inner_text("[data-testid=terminal-command]")
        assert "accounts export --bitwarden" in cmd and "bw unlock --raw" in cmd, cmd
        page.click("[role=dialog] button:has-text('Close')")
        page.fill("[data-testid=pm-only]", "myworkday, amazon.jobs, ")
        page.eval_on_selector("[data-testid=pm-import]", "(b) => { b.disabled = false; }")
        page.click("[data-testid=pm-import]")
        page.wait_for_selector("[data-testid=terminal-command]")
        cmd = page.inner_text("[data-testid=terminal-command]")
        assert "accounts import - --only myworkday" in cmd and "accounts import - --only amazon.jobs" in cmd, cmd
        page.click("[role=dialog] button:has-text('Close')")
        assert json.load(open(local_config))["bitwarden_import"] == ["myworkday", "amazon.jobs"]
        page.click("[data-testid=pm-toggle]")
        page.wait_for_selector("[data-testid=pm-toggle]:has-text('Use Bitwarden')")
        assert page.locator("[data-testid=pm-install]").count() == 0
        assert json.load(open(local_config))["password_manager"] == ""
        page.wait_for_selector("[data-testid=health-bitwarden]", state="detached", timeout=20000)
        page.click("[data-testid=settings-tab-app]")
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
