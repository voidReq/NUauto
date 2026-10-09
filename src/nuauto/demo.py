"""Demo mode: everything fake, so the GUI (and agents testing it) can run every flow with no real account.

`nuauto gui --demo` makes a temp folder, sets NUAUTO_STATE_DIR + NUAUTO_DEMO=1 (config.py) and calls setup(). Then:
- the Google Sheet is a JSON file in the demo folder (FakeClient / FakeWorksheet: the real sheet.py code runs on it);
- NUworks is a few fake pages served inside Playwright (serve_nuworks): the real apply.py fills and submits them;
  the browser gets a proxy that does not exist, so nothing can reach the network;
- `claude` is a small fake script (tools.claude in the demo's local_config.json);
- the update is fake too (`python -m nuauto.demo update`): it adds a few held-back jobs to the pool.

Forced problems (--demo-state, saved in local/demo_state.json so child processes see them too):
  fresh               nothing set up yet: the setup wizard from the start
  google-expired      the Google login is 8 days old (expired)
  claude-logged-out   `claude auth status` says logged out
  nuworks-relogin     NUworks session expired; the one-click SSO re-login works
  nuworks-password    NUworks session expired and SSO asks for a password (you must log in)
  cap-reached         11 applications already this week

  nuauto demo setup [state,...] | update | login_nuworks | login_claude | claude ... | state <name> on|off
"""
import fcntl
import json
import os
import re
import sys
import time
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

import gspread

from nuauto import config

STATES = {"fresh", "google-expired", "claude-logged-out", "nuworks-relogin", "nuworks-password", "cap-reached"}
SHEET_ID = "DEMO-SHEET"
RESUME_LABEL = "Demo Student | Resume"
HOST = "https://northeastern-csm.symplicity.com"


def _need_demo():
    if not config.DEMO:
        raise RuntimeError("demo.py runs only in demo mode (NUAUTO_DEMO=1 with its own NUAUTO_STATE_DIR)")


def _path(*parts):
    return os.path.join(config.LOCAL_DIR, *parts)


def _write(path, data, mode=0o600):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as f:
        f.write(data if isinstance(data, str) else json.dumps(data, indent=1))
    os.replace(tmp, path)


# ---------------------------------------------------------------- forced states

def states():
    try:
        with open(_path("demo_state.json")) as f:
            return set(json.load(f).get("states", []))
    except (OSError, ValueError):
        return set()


def set_state(name, on):
    if name not in STATES:
        raise ValueError(f"unknown demo state {name!r}; one of {sorted(STATES)}")
    now = states()
    now = now | {name} if on else now - {name}
    _write(_path("demo_state.json"), {"states": sorted(now)})


# ---------------------------------------------------------------- the fake sheet

class FakeHTTP:
    """gspread's http_client, for sheet.create_sheet (POST spreadsheets)."""

    def request(self, method, endpoint, json=None, **kw):
        _need_demo()
        new_id = f"DEMO-{int(time.time() * 1000) % 10**8}"
        _write(_path(f"demo_sheet_{new_id}.json"), {"title": (json or {}).get("properties", {}).get("title", ""),
                                                     "rows": []})
        return FakeResponse({"spreadsheetId": new_id})


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class FakeClient:
    """Stands in for gspread.Client. Logging in = a fresh token file; the login expires after 7 days, like a real
    Testing-mode Google app (google-expired makes it 8 days old)."""

    def __init__(self, interactive=True):
        _need_demo()
        from nuauto import sheet
        if not os.path.exists(config.TOKEN_PATH):
            if not interactive:
                raise sheet.NotLoggedIn("Not logged in to Google yet.")
            google_login()  # what the browser login would do
        self.http_client = FakeHTTP()

    def open_by_key(self, key):
        from google.auth.exceptions import RefreshError
        age = google_age()
        if age is None or age >= config.GOOGLE_LOGIN_DAYS:
            raise RefreshError("demo: the Google login expired")
        if not os.path.exists(_path(f"demo_sheet_{key}.json")):
            raise FakeNotFound(f"no demo sheet {key!r}")
        return FakeSpreadsheet(key)


class FakeNotFound(gspread.exceptions.SpreadsheetNotFound):
    """No demo sheet with that ID (gspread's own error, so callers handle both alike)."""


def google_login():
    _write(config.TOKEN_PATH, {"demo": True})
    _write(config.GOOGLE_LOGIN_PATH, date.today().isoformat() + "\n", mode=0o644)


def google_age():
    try:
        with open(config.GOOGLE_LOGIN_PATH) as f:
            return (date.today() - date.fromisoformat(f.read().strip())).days
    except (OSError, ValueError):
        return None


class FakeSpreadsheet:
    """The main tab is demo_sheet_<id>.json; the Other jobs tab (sheet.OTHER_TAB), once made, demo_sheet_<id>.other.json."""

    def __init__(self, key):
        self.id = key
        self.sheet1 = FakeWorksheet(key)
        self._other = _path(f"demo_sheet_{key}.other.json")

    def worksheet(self, title):
        from nuauto import sheet
        if title == self.sheet1.title:
            return self.sheet1
        if title == sheet.OTHER_TAB and os.path.exists(self._other):
            return FakeWorksheet(self.id, other=True)
        raise gspread.exceptions.WorksheetNotFound(title)

    def add_worksheet(self, title, rows=1000, cols=26):
        from nuauto import sheet
        if title != sheet.OTHER_TAB or os.path.exists(self._other):
            raise ValueError(f"demo: can't add a tab named {title!r}")
        _write(self._other, {"rows": []})
        return FakeWorksheet(self.id, other=True)

    def batch_update(self, body):
        return {}

    def fetch_sheet_metadata(self):
        from nuauto import sheet
        tabs = [(0, "Jobs")] + ([(1, sheet.OTHER_TAB)] if os.path.exists(self._other) else [])
        return {"sheets": [{"properties": {"sheetId": i, "title": t, "gridProperties": {"columnCount": 6}}} for i, t in tabs]}


CELL = re.compile(r"^([A-Z]+)(\d+)$")


def _cell(a1):
    m = CELL.match(a1)
    col = 0
    for ch in m.group(1):
        col = col * 26 + ord(ch) - 64
    return int(m.group(2)) - 1, col - 1


def _range(a1):
    first, _, last = a1.partition(":")
    (r0, c0), (r1, c1) = _cell(first), _cell(last or first)
    return r0, c0, r1, c1


class FakeWorksheet:
    """The worksheet calls sheet.py makes, on local/demo_sheet_<id>.json (other=True: the Other jobs tab's file). A file
    lock keeps the GUI and a child process (apply) from writing at the same time."""

    def __init__(self, key, other=False):
        from nuauto import sheet
        self.path = _path(f"demo_sheet_{key}{'.other' if other else ''}.json")
        self.id, self.title = (1, sheet.OTHER_TAB) if other else (0, "Jobs")

    def _locked(self):
        fd = os.open(self.path + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd

    def _load(self):
        with open(self.path) as f:
            return json.load(f)

    def get_all_values(self):
        rows = [list(r) for r in self._load()["rows"]]
        while rows and not any(c.strip() for c in rows[-1]):
            rows.pop()
        return rows

    def get(self, a1):
        r0, c0, r1, c1 = _range(a1)
        rows = self._load()["rows"]
        out = [[c for c in row[c0:c1 + 1]] for row in rows[r0:r1 + 1]]
        return [r for r in out if any(c.strip() for c in r)]

    def update(self, range_name=None, values=None, value_input_option=None, **kw):
        r0, c0, _, _ = _range(range_name)
        fd = self._locked()
        try:
            data = self._load()
            rows = data["rows"]
            for i, vals in enumerate(values):
                while len(rows) <= r0 + i:
                    rows.append([])
                row = rows[r0 + i]
                while len(row) < c0 + len(vals):
                    row.append("")
                for j, v in enumerate(vals):
                    row[c0 + j] = str(v)
            _write(self.path, data)
        finally:
            os.close(fd)

    def freeze(self, rows=None, cols=None):
        pass


# ---------------------------------------------------------------- demo data

def _job(i, title, company, location, state, category, match, closes_in, why, popup, **over):
    return {"id": i, "title": title, "company": company, "location": location, "state": state, "category": category,
            "match": match, "closes_in": closes_in, "why": why, "popup": popup, **over}


DESCRIPTION = """About the role
{company} is looking for a co-op student to join the {team} team for six months.
Responsibilities:
- {duty1}
- {duty2}
- Write short reports on what you built and tested
Requirements:
- {req1}
- {req2}
- Comfortable working in a lab or on a small team
Nice to have:
- {nice}"""

# Pool jobs (the last two are held back until the first fake update)
JOBS = [
    _job("900101", "Embedded Firmware Co-op", "Harbor Embedded", "Boston, MA, USA", "US-MA", "embedded", 84, 2,
         "C and microcontroller projects match most of the firmware work.", "linkedin", skills=["C", "RTOS", "I2C"]),
    _job("900102", "Product Security Co-op", "Lumen Security", "Cambridge, MA, USA", "US-MA", "security", 76, 20,
         "Security coursework and CTF practice cover most requirements.", "question", skills=["Python", "Burp Suite"]),
    _job("900103", "Hardware Test Engineering Co-op", "Delta Test Labs", "Nashua, NH, USA", "US-NH", "robotics_test",
         71, 9, "Lab and test automation experience fits; LabVIEW is missing.", "plain", lengths=[],
         skills=["Python", "Oscilloscopes"]),
    _job("900104", "Systems Software Co-op", "Cobalt Systems", "Remote, USA", "", "systems", 92, 30,
         "Linux and C fit; distributed systems experience is thin.", "plain", class_levels=["Junior", "Senior"],
         pay="$62,000-$70,000 per year",
         skills=["C", "Linux"]),
    _job("900105", "FPGA Design Co-op", "Quarry Hardware", "Austin, TX, USA", "US-TX", "hardware", 68, 14,
         "Digital logic coursework matches; Verilog projects are limited.", "external",
         majors=["Khoury College of Computer Sciences/Computer Science"], skills=["Verilog"]),
    _job("900106", "Data Analyst Co-op", "Fern Data Co", "Providence, RI, USA", "US-RI", "data_ml", 67, 25,
         "Python and statistics match; SQL is partial.", "plain", skills=["Python", "SQL"], pay=""),
    _job("900107", "Web Developer Co-op", "Beacon Web Studio", "New York, NY, USA", "US-NY", "fullstack_web", 72, 18,
         "JavaScript projects fit, but the role is front-end focused.", "plain", skills=["JavaScript", "React"]),
    _job("900108", "IT Support Co-op", "Summit IT", "Burlington, MA, USA", "US-MA", "it", 66, 40,
         "Help desk and Linux admin experience apply.", "cover", skills=["Linux"], vague=True),
    _job("900109", "Robotics Controls Co-op", "Pine Robotics", "Waltham, MA, USA", "US-MA", "robotics_test", 79, 5,
         "Controls coursework and robot projects match well.", "plain", held_back=True, skills=["ROS", "C++"]),
    _job("900110", "Network Security Co-op", "Granite Networks", "Hartford, CT, USA", "US-CT", "security", 70, 21,
         "Networking labs and Linux fit; SIEM tools are missing.", "plain", held_back=True, skills=["Wireshark"]),
    # in the sheet only: applied on the company site, the NUworks side still to send (Company sites -> retry)
    _job("900140", "Firmware Test Co-op", "Maple Controls", "Lowell, MA, USA", "US-MA", "embedded", 75, 12,
         "Test automation and C fit.", "plain", sheet_only=True),
]
# In the sheet from the start (not in the pool): Approved ones for Apply, one external Needs Human for Company sites
SHEET_JOBS = {
    "900101": "Approved", "900102": "Approved", "900105": "Approved",
}
EXTERNAL_ROW = ("900120", "Iron Valley Medical", "Embedded Software Co-op",
                "External application: ivm.example-ats.com -> https://ivm.example-ats.com/jobs/4471")


def details(j, today):
    d = {"id": j["id"], "title": j["title"], "company": j["company"], "position_types": ["Co-op"],
         "term": ["2027 - Spring"], "lengths": j.get("lengths", ["6 Month"]), "class_levels": j.get("class_levels", []),
         "degree_levels": ["Undergraduate"], "majors": j.get("majors", []), "states": [j["state"]] if j["state"] else [],
         "location": j["location"], "skills": j.get("skills", []), "experience": [], "function": ["Engineering"],
         "pay": j.get("pay", f"${18 + int(j['id']) * 7 % 22}-${24 + int(j['id']) * 7 % 22} per hour"), "deadline": (today + timedelta(days=j["closes_in"])).isoformat(),
         "posting_end": (today + timedelta(days=j["closes_in"] + 10)).isoformat(), "expired": False, "applied": False,
         "description": DESCRIPTION.format(company=j["company"], team=j["category"].replace("_", " "),
                                           duty1=f"Build and test {j['title'].split(' Co-op')[0].lower()} features",
                                           duty2="Debug problems with senior engineers",
                                           req1=f"Coursework or projects in {', '.join(j.get('skills', ['engineering']))}",
                                           req2="Clear written communication", nice="A previous co-op or internship"),
         "qualifications": "Undergraduate students in engineering or computer science.",
         "fetched": today.isoformat()}
    if j["popup"] == "external":
        d["description"] += "\nHow to apply: you must also apply on our website."
    return d


def score(j, today):
    met = j.get("skills", [])[:2]
    return {"match": j["match"], "class_req": "none", "met": met, "partial": [] if j.get("vague") else ["testing"],
            "missing": [] if j.get("vague") else ["a previous co-op", "industry tools"], "why": j["why"],
            "scored": today.isoformat()}


def OTHER_ROWS(today):
    """The Other jobs tab: jobs that are not on NUworks."""
    return [("https://careers.example.com/jobs/4821", "Granite Robotics", "Summer Robotics Intern", "Approved", "", ""),
            ("https://jobs.example.org/posting/77", "Northwind Labs", "Security Research Intern", "Applied",
             "Applied on the company site (nuauto assist; you pressed Submit).", (today - timedelta(days=3)).isoformat())]


def _url(i):
    return f"{HOST}/students/app/jobs/detail/{i}"


def setup(state_names=()):
    """Fill the (empty) demo folder. state_names: see the module docstring."""
    _need_demo()
    unknown = set(state_names) - STATES
    if unknown:
        raise ValueError(f"unknown demo states {sorted(unknown)}; choose from {sorted(STATES)}")
    from nuauto import jobs
    today = date.today()
    os.makedirs(config.LOCAL_DIR, mode=0o700, exist_ok=True)
    os.chmod(config.LOCAL_DIR, 0o700)
    _write(_path("demo_state.json"), {"states": sorted(state_names)})
    _write_fake_claude()
    resume = os.path.join(config.STATE_DIR, "Demo_Student_Resume.pdf")
    with open(resume, "wb") as f:
        f.write(tiny_pdf(["Demo Student", "Electrical and Computer Engineering, 2nd year",
                          "Projects: STM32 data logger (C, I2C), Linux home server, CTF team",
                          "Skills: C, Python, Linux, Verilog basics, oscilloscopes"]))
    base = {"sheet_id": "", "resume_path": "", "server_hostname": "", "server_ssh": "", "server_dir": "",
            "web_base_url": "", "web_listen_host": "", "assist_read_paths": [], "week_start": "",
            "tools": {"claude": os.path.join(config.STATE_DIR, "bin", "claude")}}
    if "fresh" in state_names:  # the setup wizard from the start: only the fake tools exist
        _write(config.LOCAL_CONFIG_PATH, base, mode=0o644)
        _write(_path("demo_sheet_DEMO-SHEET.json"), {"title": "NUauto jobs (demo)", "rows": []})
        return
    _write(config.LOCAL_CONFIG_PATH, {**base, "sheet_id": SHEET_ID, "resume_path": resume, "tos_ack": today.isoformat(),
                                      "preferences": dict(jobs.DEFAULTS)}, mode=0o644)
    _write(config.PROFILE_PATH, {"resume_label": RESUME_LABEL})
    _write(_path("client_secret.json"), {"installed": {"client_id": "demo.apps.googleusercontent.com",
                                                       "client_secret": "demo", "redirect_uris": ["http://localhost"]}})
    google_login()
    if "google-expired" in state_names:
        _write(config.GOOGLE_LOGIN_PATH, (today - timedelta(days=8)).isoformat() + "\n", mode=0o644)
    _write(config.COOKIES_PATH, [{"name": "demo_session", "value": "1", "domain": "northeastern-csm.symplicity.com",
                                   "path": "/", "secure": True, "httpOnly": True, "sameSite": "Lax", "expires": -1}])
    os.makedirs(config.PROFILE_DIR, mode=0o700, exist_ok=True)
    from nuauto import answers
    bank = [answers.new_entry(q, always_ask=a) for q, a in answers.STARTERS]
    for e in bank:
        e["answer"] = {"name": "Demo Student", "email": "demo.student@example.com", "phone": "555-0100",
                       "school": "Northeastern University", "major": "Electrical and Computer Engineering",
                       "available start date": "2027-01-11"}.get(e["question"], "")
    bank.append(answers.new_entry("linkedin *", "https://www.linkedin.com/in/demo-student", "text"))
    answers.save(bank)
    answers.save([answers.new_entry("available start date", "2027-05-24", "text")], config.OTHER_ANSWERS_PATH)

    # job pool data (the update steps' outputs), minus the held-back jobs
    os.makedirs(os.path.join(config.DATA_DIR, "details"), exist_ok=True)
    listed, scores, cats = {}, {}, {}
    for j in JOBS:
        if j.get("held_back") or j.get("sheet_only"):
            continue
        with open(os.path.join(config.DATA_DIR, "details", f"{j['id']}.json"), "w") as f:
            json.dump(details(j, today), f, indent=1)
        listed[j["id"]] = {"id": j["id"], "title": j["title"], "company": j["company"], "location": j["location"],
                           "snippet": "", "source": "spring"}
        scores[j["id"]], cats[j["id"]] = score(j, today), j["category"]
    jobs.save("list.json", listed)
    jobs.save("triage.json", {i: {"keep": True, "why": "demo"} for i in listed})
    jobs.save("scores.json", scores)
    jobs.save("categories.json", cats)
    jobs.save("pool.json", jobs.build_pool())
    jobs.save("ratings.json", {})
    from datetime import datetime
    jobs.save("scans.json", [{"time": (datetime.now() - timedelta(hours=2)).isoformat(timespec="minutes"), "listed": 12,
                              "pool": 3}])

    # the sheet
    from nuauto import sheet
    rows = [list(sheet.HEADERS)]
    applied = 11 if "cap-reached" in state_names else 4
    for k in range(applied):
        day = today - timedelta(days=k % 6)
        rows.append([_url(f"9002{k:02d}"), f"Earlier Co {k + 1}", "Engineering Co-op", "Applied",
                     "Submitted via apply.py (confirmed by NUworks page)", day.isoformat()])
    rows.append([_url("900130"), "Willow Devices", "Firmware Co-op", "Applied",
                 f"{sheet.SITE_MARK} (posting: \"you must also apply on our careers site\"). Submitted via apply.py",
                 (today - timedelta(days=1)).isoformat()])
    by_id = {j["id"]: j for j in JOBS}
    for i, status in SHEET_JOBS.items():
        j = by_id[i]
        rows.append([_url(i), j["company"], j["title"], status, f"match {j['match']}%", ""])
    i, company, title, notes = EXTERNAL_ROW
    rows.append([_url(i), company, title, "Needs Human", notes, ""])
    rows.append([_url("900140"), "Maple Controls", "Firmware Test Co-op", "Applied",
                 "Applied on the company site (nuauto assist; you pressed Submit). NUworks side NOT submitted: "
                 "the session expired.", (today - timedelta(days=10)).isoformat()])
    _write(_path(f"demo_sheet_{SHEET_ID}.json"), {"title": "NUauto jobs (demo)", "rows": rows})
    _write(_path(f"demo_sheet_{SHEET_ID}.other.json"), {"rows": [list(sheet.HEADERS)] + [list(r) for r in OTHER_ROWS(today)]})


def update():
    """The fake `nuauto update`: the held-back jobs show up, like a scan that found new postings."""
    _need_demo()
    from nuauto import jobs
    today = date.today()
    new = [j for j in JOBS if j.get("held_back") and not os.path.exists(os.path.join(config.DATA_DIR, "details", f"{j['id']}.json"))]
    for step in ("list (demo: no network)", "triage", "details", "score", "category", "pool"):
        print(f"{time.strftime('%H:%M:%S')}  {step}", flush=True)
        time.sleep(0.3 * config.DEMO_PACE / 0.05)
    listed, scores, cats = jobs.load("list.json", {}), jobs.load("scores.json", {}), jobs.load("categories.json", {})
    for j in new:
        with open(os.path.join(config.DATA_DIR, "details", f"{j['id']}.json"), "w") as f:
            json.dump(details(j, today), f, indent=1)
        listed[j["id"]] = {"id": j["id"], "title": j["title"], "company": j["company"], "location": j["location"],
                           "snippet": "", "source": "spring"}
        scores[j["id"]], cats[j["id"]] = score(j, today), j["category"]
    jobs.save("list.json", listed)
    jobs.save("scores.json", scores)
    jobs.save("categories.json", cats)
    pool = jobs.build_pool()
    jobs.save("pool.json", pool)
    scans = jobs.load("scans.json", [])
    scans.append({"time": time.strftime("%Y-%m-%dT%H:%M"), "listed": len(new) + 5, "pool": len(new)})
    jobs.save("scans.json", scans[-60:])
    print(f"{time.strftime('%H:%M:%S')}  pool {len(pool)} ({len(new)} new)", flush=True)


def login_nuworks():
    """What `nuauto login` does once you have logged in: a fresh session (clears the NUworks problem states)."""
    _need_demo()
    for s in ("nuworks-relogin", "nuworks-password"):
        set_state(s, False)
    _write(config.COOKIES_PATH, [{"name": "demo_session", "value": "1", "domain": "northeastern-csm.symplicity.com",
                                   "path": "/", "secure": True, "httpOnly": True, "sameSite": "Lax", "expires": -1}])
    os.makedirs(config.PROFILE_DIR, mode=0o700, exist_ok=True)
    print("Demo: logged in to NUworks.")


# ---------------------------------------------------------------- fake claude

def fake_claude(argv):
    """The fake `claude` (demo mode): answers auth status / auth login / -p, nothing else."""
    logged_in = "claude-logged-out" not in states()
    if argv[:2] == ["auth", "status"]:
        print(json.dumps({"loggedIn": logged_in, "authMethod": "demo", "apiProvider": "demo", "subscriptionType": "demo"}))
    elif argv[:2] == ["auth", "login"]:
        set_state("claude-logged-out", False)
        print("Demo: logged in to Claude.")
    elif "-p" in argv:
        print(json.dumps({"type": "result", "is_error": not logged_in, "result": "OK" if logged_in else "Not logged in"}))
        sys.exit(0 if logged_in else 1)
    elif argv == ["--version"]:
        print("0.0.0 (demo Claude)")
    else:
        print("Demo: the Claude session would start here (nothing happens in demo mode).")


def _write_fake_claude():
    """bin/claude in the demo folder: a two-line script that runs fake_claude (through this program, so it works
    from a source install and from the packaged app alike)."""
    import shlex
    cmd = " ".join(shlex.quote(a) for a in config.self_cmd("demo", "claude"))
    _write(os.path.join(config.STATE_DIR, "bin", "claude"), f'#!/bin/sh\nexec {cmd} "$@"\n', mode=0o755)


# ---------------------------------------------------------------- fake NUworks (inside Playwright)

STYLE = ("<style>body{font:15px system-ui,sans-serif;margin:2rem auto;max-width:46rem;padding:0 1rem}"
         "[role=dialog]{border:1px solid #999;border-radius:8px;padding:1rem 1.2rem;margin:1rem 0;background:#fafafa}"
         "label{display:block;margin-top:.7rem;font-weight:600}button{margin:.8rem .5rem 0 0;padding:.4rem .9rem}"
         ".ok{color:#137333;font-weight:700}.demo{background:#fff3cd;padding:.3rem .6rem;border-radius:4px}</style>")


def _page(title, body):
    return (f"<!doctype html><html><head><meta charset=utf-8><title>{title}</title>{STYLE}</head><body>"
            f"<p class=demo>NUauto demo: a fake NUworks page</p>{body}</body></html>")


SIGN_IN = _page("Students: Sign-in method", """<h1>Sign In</h1><p>Please select a role</p>
<button type=button onclick="location.href='https://shibboleth-northeastern-csm.symplicity.com/sso/demo'">Current Students And Alumni</button>
<button type=button>Local Login - Not Supported</button>""")
SSO_PASSWORD = _page("Log in", """<h1>Northeastern sign in (demo)</h1>
<label for=u>Username</label><input id=u><label for=p>Password</label><input id=p type=password>""")
SSO_BACK = _page("Signing in", f"<p>Signing you in...</p><script>location.replace('{HOST}/students/app/jobs/discover?demo_relogin=1')</script>")
DISCOVER = _page("Jobs | NUworks", "<h1>Jobs</h1><p>Welcome back (demo).</p>")
SEARCH = _page("Search Jobs | NUworks", "<h1>Search jobs</h1><script>fetch('/api/v2/jobs/filters/students')"
               ".then(function (r) { return r.json(); })</script>")
# what the term picker reads (the real response's shape may differ: onboard.find_terms looks for terms anywhere)
FILTERS = {"models": [{"key": "el_work_term", "options": [
    {"_id": "d13c36bce4531e63c56c9b58b90dbb71", "_label": "2027 - Spring"},
    {"_id": "demo00000000000000000000fall2027", "_label": "2027 - Fall"},
    {"_id": "demo00000000000000000000fall2026", "_label": "2026 - Fall"}]},
    {"key": "job_type", "options": [{"_id": "5", "_label": "Co-op"}]}]}

POPUPS = {
    "plain": "",
    "linkedin": '<label for=li>LinkedIn *</label><input id=li type=text>',
    "question": ('<label for=q18>Are you at least 18 years old? *</label>'
                 '<select id=q18><option value="">Select</option><option>Yes</option><option>No</option></select>'),
    "external": '<p>How to Apply</p><p>Apply on the company site: <a href="https://quarry.example-ats.com/apply/88213">quarry.example-ats.com</a></p>',
    "cover": "<p>Cover Letter *</p><p>Add a new cover letter</p>",
}


def job_page(i):
    j = next((x for x in JOBS if x["id"] == i), None)
    if j is None:
        return _page("Job | NUworks", "<h1>This job is no longer available</h1>")
    dialog = (f"<h2>Apply to {j['company']}</h2><p>Submit Your Application</p>"
              f"<label for=resume>Resume *</label><select id=resume><option value=\"\">Select a resume</option>"
              f"<option>{RESUME_LABEL}</option></select><p>or add a new resume</p>{POPUPS[j['popup']]}"
              "<button type=button onclick=\"this.closest('[role=dialog]').remove()\">Cancel</button>"
              "<button type=button onclick=\"submitApply()\">Submit</button>")
    script = f"""<script>
function openApply() {{
  if (document.querySelector('[role=dialog]')) return;
  const d = document.createElement('div');
  d.setAttribute('role', 'dialog'); d.setAttribute('aria-label', 'Apply');
  d.innerHTML = {json.dumps(dialog)};
  document.body.appendChild(d);
}}
function submitApply() {{
  document.querySelector('[role=dialog]').remove();
  const p = document.createElement('p'); p.className = 'ok';
  p.textContent = 'Your application has been submitted'; document.body.appendChild(p);
}}
</script>"""
    return _page(f"{j['title']} | NUworks", f"<h1>{j['title']}</h1><p>{j['company']} · {j['location']}</p>"
                 f"<p>Applications close in {j['closes_in']} days.</p><button type=button onclick=\"openApply()\">Apply</button>"
                 + script)


def serve_nuworks(context):
    """Fake NUworks and SSO pages. Registered before the domain lock, so the lock still decides first."""
    def handler(route):
        req = route.request
        u = urlparse(req.url)
        try:
            if u.hostname in config.ALLOWED_HOSTS:
                return _nuworks(route, u, req)
            if u.hostname in config.SSO_HOSTS:
                return _html(route, SSO_PASSWORD if "nuworks-password" in states() else SSO_BACK)
        except Exception as e:  # a bug in a fake page must not leave the page hanging
            return route.fulfill(status=500, content_type="text/plain", body=f"demo error: {type(e).__name__}: {e}")
        route.abort()  # anything else never reaches the network
    context.route("**/*", handler)


def _html(route, body):
    route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)


def _nuworks(route, u, req):
    if not req.is_navigation_request() and req.resource_type not in ("document", "xhr", "fetch"):
        return route.fulfill(status=204, body="")  # favicons and the like
    if parse_qs(u.query).get("demo_relogin") == ["1"]:  # back from the one-click SSO re-login: session renewed
        set_state("nuworks-relogin", False)
    if {"nuworks-relogin", "nuworks-password"} & states():  # the session expired
        return _html(route, SIGN_IN)
    if u.path.startswith("/api/v2/jobs/filters"):
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(FILTERS))
    if u.path.startswith("/students/app/jobs/search"):
        return _html(route, SEARCH)
    if u.path.startswith("/students/app/jobs/detail/"):
        return _html(route, job_page(u.path.rstrip("/").split("/")[-1]))
    if u.path.startswith("/students/app/"):
        return _html(route, DISCOVER)
    return _html(route, SIGN_IN if u.path.rstrip("/") in ("", "/students") else _page("Not found", "<h1>Not found</h1>"))


# ---------------------------------------------------------------- a tiny real PDF (pypdf can read its text)

def tiny_pdf(lines):
    def esc(t):
        return t.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = "BT /F1 12 Tf 72 720 Td 16 TL " + " ".join(f"({esc(t)}) Tj T*" for t in lines) + " ET"
    objects = ["<< /Type /Catalog /Pages 2 0 R >>",
               "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> "
               "/Contents 5 0 R >>",
               "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
               f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream"]
    out, offsets = b"%PDF-1.4\n", []
    for n, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n{obj}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def main(argv):
    _need_demo()
    if argv[:1] == ["setup"]:
        setup([s for s in (argv[1] if len(argv) > 1 else "").split(",") if s])
    elif argv == ["update"]:
        update()
    elif argv == ["login_nuworks"]:
        login_nuworks()
    elif argv == ["login_claude"]:
        set_state("claude-logged-out", False)
        print("Demo: logged in to Claude.")
    elif argv[:1] == ["claude"]:
        fake_claude(argv[1:])
    elif argv == ["install_claude"]:
        print("Demo: Claude Code would be installed here.")
    elif len(argv) == 3 and argv[0] == "state" and argv[2] in ("on", "off"):
        set_state(argv[1], argv[2] == "on")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
