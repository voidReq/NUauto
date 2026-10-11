"""Offline checks for the internship pool (intern.py) and the posting reader (postings.py). Run: python tests/test_intern.py

A temp state folder; job sites are stand-ins (JSON fixtures, a local HTTP server, a fake hidden browser); Claude's
batch outputs are written here. No network, no sheet, no Claude.
"""
import http.server
import json
import os
import shutil
import tempfile
import threading
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = tempfile.mkdtemp(prefix="nuauto-test-intern-")
os.environ["NUAUTO_STATE_DIR"] = STATE
os.makedirs(os.path.join(STATE, "local"), mode=0o700)
with open(os.path.join(ROOT, "local_config.example.json")) as f:
    cfg = json.load(f)
cfg["internships"] = {"fetch_per_run": 50, "browser_per_run": 1}
with open(os.path.join(STATE, "local", "local_config.json"), "w") as f:
    json.dump(cfg, f)

from nuauto import config  # noqa: E402
from nuauto import intern  # noqa: E402
from nuauto import jobs  # noqa: E402
from nuauto import postings  # noqa: E402
from nuauto import sheet  # noqa: E402

TODAY = date(2026, 10, 10)
assert intern.ENABLED and intern.SETTINGS["fetch_per_run"] == 50 and intern.SETTINGS["score_per_run"] == 120
assert intern.DIR == os.path.join(STATE, "data", "intern")

# ---------------------------------------------------------------- postings: text, pay, closed
t = postings.text_of("<p>About</p><ul><li>Build <b>firmware</b></li><li>Test it</li></ul><script>x=1</script>Pay&nbsp;$40")
assert t == "About\n- Build firmware\n- Test it\nPay $40", repr(t)
assert postings.text_of("&lt;p&gt;Escaped &amp;amp; fine&lt;/p&gt;") == "Escaped & fine"
assert postings.pay_from_text("Pay: $38 - $46 per hour, plus housing") == "$38-$46 per hour"
assert postings.pay_from_text("$45/hr") == "$45 per hour"
assert postings.pay_from_text("The range is $100k to $120k a year") == "$100k-$120k per year"
assert postings.pay_from_text("Team of 40 people, $5 million budget") == ""
assert postings.looks_closed("Sorry, this job is no longer accepting applications.")
assert not postings.looks_closed("x" * 3000 + " no longer accepting applications")
from nuauto import insights  # noqa: E402  (the Insights screen reads these pay texts)
assert insights.hourly("$38-$46 per hour") == (38, 46) and insights.hourly("$100k-$120k per year")[0] > 40

# ---------------------------------------------------------------- postings: the job sites' own data (links -> API)
A = postings.api_request
assert A("https://acme.wd5.myworkdayjobs.com/en-US/External/job/Boston-MA/Firmware-Intern_R123") == \
    ("workday", "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/External/job/Boston-MA/Firmware-Intern_R123")
assert A("https://acme.wd1.myworkdayjobs.com/Careers/job/Austin/Intern_JR9/apply") == \
    ("workday", "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/Careers/job/Austin/Intern_JR9")
assert A("https://wd3.myworkdaysite.com/recruiting/acme/Careers/job/New-York/Intern_JR1")[1] == \
    "https://wd3.myworkdaysite.com/wday/cxs/acme/Careers/job/New-York/Intern_JR1"
assert A("https://job-boards.greenhouse.io/acme/jobs/7101234")[1] == "https://boards-api.greenhouse.io/v1/boards/acme/jobs/7101234"
assert A("https://boards.greenhouse.io/embed/job_app?for=acme&token=7101234")[1] == \
    "https://boards-api.greenhouse.io/v1/boards/acme/jobs/7101234"
assert A("https://job-boards.eu.greenhouse.io/acme/jobs/55")[1].startswith("https://boards-api.eu.greenhouse.io/")
assert A("https://jobs.lever.co/acme/0f6a1b2c-1111-2222-3333-444455556666/apply")[1] == \
    "https://api.lever.co/v0/postings/acme/0f6a1b2c-1111-2222-3333-444455556666"
assert A("https://jobs.eu.lever.co/acme/abc")[1] == "https://api.eu.lever.co/v0/postings/acme/abc"
assert A("https://jobs.ashbyhq.com/acme/0f6a/application?embed=js")[1] == \
    "https://api.ashbyhq.com/posting-api/job-board/acme?includeCompensation=true"
assert A("https://jobs.smartrecruiters.com/Acme1/744000012345678-software-intern")[1] == \
    "https://api.smartrecruiters.com/v1/companies/Acme1/postings/744000012345678"
assert "finder=ById;Id=%2212345%22,siteNumber=CX_1" in A("https://e.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/12345")[1]
assert A("https://acme.eightfold.ai/careers/job/563000012345")[1] == "https://acme.eightfold.ai/api/apply/v2/jobs/563000012345"
assert A("https://acme.eightfold.ai/careers?pid=563000012345&domain=acme.com")[1].endswith("/jobs/563000012345")
assert A("https://apply.workable.com/acme/j/AB12CD34EF/apply")[1] == "https://apply.workable.com/api/v2/accounts/acme/jobs/AB12CD34EF"
assert A("https://careers-acme.icims.com/jobs/12345/job?mobile=true")[1] == "https://careers-acme.icims.com/jobs/12345/job?in_iframe=1"
for odd in ("https://careers.acme.com/jobs/123", "https://acme.wd5.myworkdayjobs.com/External/details/x_R1",
            "https://jobs.smartrecruiters.com/Acme1", "https://e.fa.us2.oraclecloud.com/hcmUI/x", "https://evilgreenhouse.io/a/jobs/1"):
    assert A(odd) is None, odd
assert postings.kind("https://notmyworkdayjobs.com/x") == "other" and postings.kind("https://acme.wd5.myworkdayjobs.com/") == "workday"

LONG = "<p>Responsibilities: build and test firmware for our sensors.</p>" * 12
P = postings.parse_api
w = P("workday", {"jobPostingInfo": {"title": "Intern", "jobDescription": LONG + "<p>$40 - $50 per hour</p>",
                                     "location": "Boston, MA", "additionalLocations": ["NYC"], "canApply": True}}, "u")
assert w["pay"] == "$40-$50 per hour" and w["location"] == "Boston, MA; NYC" and not w["closed"] and len(w["description"]) > 300
assert P("workday", {"jobPostingInfo": {"jobDescription": LONG, "canApply": False}}, "u")["closed"]
assert P("greenhouse", {"title": "T", "content": LONG.replace("<", "&lt;").replace(">", "&gt;"), "location": {"name": "SF"}},
         "u")["description"].startswith("Responsibilities")
lv = P("lever", {"text": "T", "description": "<p>Intro</p>", "lists": [{"text": "What you'll do", "content": "<li>Build</li>"}],
                 "additional": "<p>More</p>", "salaryRange": {"min": 40, "max": 50, "interval": "per-hour-wage"}}, "u")
assert lv["description"] == "Intro\nWhat you'll do\n- Build\nMore" and lv["pay"] == "$40-$50 per hour", lv
ash = {"jobs": [{"id": "j1", "title": "A", "descriptionHtml": LONG, "compensation": {"compensationTierSummary": "$30 – $35 per hour"}},
                {"id": "j2", "title": "B", "descriptionHtml": "<p>other</p>"}]}
assert P("ashby", ash, "https://jobs.ashbyhq.com/acme/j1")["pay"] == "$30-$35 per hour"
assert P("ashby", ash, "https://jobs.ashbyhq.com/acme/j9") is None
sr = P("smartrecruiters", {"name": "T", "active": True, "jobAd": {"sections": {"jobDescription": {"title": "Role", "text": LONG}}},
                           "location": {"city": "Austin", "region": "TX"}}, "u")
assert sr["location"] == "Austin, TX" and sr["description"].startswith("Role")
assert P("oracle", {"items": []}, "u") is None
assert "Qualifications" in P("oracle", {"items": [{"Title": "T", "ExternalDescriptionStr": LONG, "ExternalQualificationsStr": "<p>C</p>"}]}, "u")["description"]
assert P("eightfold", {"name": "T", "job_description": LONG}, "u")["description"]
assert P("workable", {"title": "T", "description": LONG, "requirements": "<p>C</p>", "location": {"city": "Boston", "region": "MA"}},
         "u")["location"] == "Boston, MA"
assert P("workday", ["not", "a", "dict"], "u") is None

# schema.org JobPosting (most careers sites)
LD = {"@context": "https://schema.org", "@type": "JobPosting", "title": "Firmware Intern", "description": LONG,
      "validThrough": "2026-11-30T23:59:59Z", "jobLocation": {"address": {"addressLocality": "Boston", "addressRegion": "MA"}},
      "baseSalary": {"currency": "USD", "value": {"minValue": 40, "maxValue": 48.5, "unitText": "HOUR"}}}
PAGE = f'<html><head><script type="application/ld+json">{json.dumps({"@graph": [{"@type": "Organization"}, LD]})}</script></head>' \
       "<body><nav>Menu</nav></body></html>"
got = postings.from_page(PAGE, "u")
assert got["deadline"] == "2026-11-30" and got["pay"] == "$40-$48.5 per hour" and got["location"] == "Boston, MA", got
assert postings.from_page("<html><body><div id=app></div><script>render()</script></body></html>", "u") is None
assert postings.from_page("<p>" + "We are hiring. Responsibilities include things. Qualifications: C. " * 30 + "</p>", "u")
assert postings.greenhouse_hosted("https://acme.com/careers?gh_jid=123",
                                  '<script src="https://boards.greenhouse.io/embed/job_board/js?for=acme"></script>') == ("acme", "123")
assert postings.greenhouse_hosted("https://acme.com/careers", "greenhouse.io/embed/job_board/js?for=acme") is None

# never an address inside your network (a listing can link anywhere), never a non-web link
for h in ("127.0.0.1", "10.1.2.3", "192.168.1.5", "100.100.1.1", "169.254.169.254", "::1"):
    assert not postings.allowed_host(h), h
assert postings.allowed_host("8.8.8.8") and not postings.allowed_host("")
for bad in ("file:///etc/passwd", "ftp://x.com/a", "http://127.0.0.1:8765/mark", "http://192.168.1.1/"):
    try:
        postings.check_url(bad)
        raise AssertionError(bad)
    except postings.Unreadable:
        pass

# ---------------------------------------------------------------- postings: reading over HTTP (a local server)
SERVED = {"/ld": PAGE, "/text": "<p>" + "Responsibilities: test boards. Qualifications: C and Python. " * 40 + "</p>",
          "/js": "<html><body><div id=root></div></body></html>", "/closed": "<h1>This job is no longer available</h1>",
          "/hop": None}


class Site(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/hop":  # a redirect into "your network" (another loopback address)
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.2:9/secret")
            self.end_headers()
            return
        body = SERVED.get(self.path)
        self.send_response(200 if body else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write((body or "nope").encode())


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_port}"
client = postings.Client(spacing=0)
try:
    postings.read_plain(base + "/ld", client)
    raise AssertionError("a private address was read")
except postings.Unreadable as e:
    assert "not a public address" in str(e), e
postings.ALLOW_PRIVATE = True
assert postings.read_plain(base + "/ld", client)["how"] == "page data"
assert postings.read_plain(base + "/text", client)["how"] == "page text"
assert postings.read_plain(base + "/closed", client)["closed"]
for p in ("/js", "/missing"):
    try:
        postings.read_plain(base + p, client)
        raise AssertionError(p)
    except postings.Unreadable as e:
        assert str(e).startswith("page: "), e


class FakeBrowser:
    def __init__(self, text=None):
        self.text, self.calls = text, []

    def read(self, url):
        self.calls.append(url)
        if self.text is None:
            return None
        return {"title": "", "description": self.text, "location": "", "pay": "", "deadline": None}


b = FakeBrowser("Rendered: " + "Responsibilities and qualifications. " * 20)
assert postings.read(base + "/js", client, b)["how"] == "browser" and b.calls == [base + "/js"]
assert postings.read(base + "/ld", client, b)["how"] == "page data" and len(b.calls) == 1  # plain first
try:
    postings.read(base + "/js", client, FakeBrowser(None))
    raise AssertionError
except postings.Unreadable as e:
    assert "page: " in str(e) and "browser: no posting text" in str(e), e
try:  # a redirect is checked again: pretend the first host is public; the redirect target (127.0.0.2) is not
    postings.ALLOW_PRIVATE = False
    postings._PUBLIC["127.0.0.1"] = True
    client.text(base + "/hop")
    raise AssertionError("followed a redirect into the network")
except postings.Unreadable as e:
    assert "127.0.0.2 is not a public address" in str(e), e
finally:
    postings._PUBLIC.pop("127.0.0.1", None)
    postings.ALLOW_PRIVATE = True

# ---------------------------------------------------------------- Simplify's list: ids, links, merge, rules
assert intern.listing_id("F41E35B6-44a6-49c2-abfe-054b4a3bbdf0") == "sf41e35b644a649c2abfe054b4a3bbdf0"
assert intern.clean_url("https://x.com/a?utm_source=Simplify&ref=Simplify&gh_jid=55") == "https://x.com/a?gh_jid=55"
assert intern.clean_url("https://x.com/a?ref=friend") == "https://x.com/a?ref=friend"
assert intern.states_of(["Boston, MA", "NYC", "Toronto, ON, Canada", "Austin, TX, USA", "Remote in USA", "SF"]) == \
    ["US-MA", "US-NY", "US-TX", "US-CA"]


def sim(n, title="Firmware Intern", term="Summer 2027", active=True, degrees=("Bachelor's",), visible=True, url=None,
        places=("Boston, MA",), days=2, sponsorship="Other"):
    ts = int((date.today() - timedelta(days=days)).strftime("%s"))
    return {"id": f"00000000-0000-4000-8000-{n:012d}", "company_name": f"Co{n}", "title": title, "terms": [term],
            "active": active, "is_visible": visible, "url": url or f"https://jobs.example.com/{n}", "locations": list(places),
            "category": "Hardware", "degrees": list(degrees), "sponsorship": sponsorship, "date_posted": ts,
            "date_updated": ts}


ID = lambda n: intern.listing_id(f"00000000-0000-4000-8000-{n:012d}")  # noqa: E731
raw = [sim(1), sim(2, term="Fall 2027"), sim(3, visible=False), sim(4, url="javascript:alert(1)"), sim(5, degrees=["PhD"]),
       sim(6, title="Security Intern", places=("Cambridge, MA", "NYC")), sim(7, active=False), sim(8, days=90),
       sim(9, degrees=[]), sim(10, title="Embedded Intern", sponsorship="U.S. Citizenship is Required")]
listings, new, closed = intern.merge({}, raw, TODAY)
assert set(listings) == {ID(n) for n in (1, 5, 6, 7, 8, 9, 10)} and closed == [], (listings.keys(), closed)
assert sorted(new) == sorted(ID(n) for n in (1, 5, 6, 8, 9, 10)), new  # 7 is already closed: kept, not "new"
assert listings[ID(1)]["first_seen"] == TODAY.isoformat() and listings[ID(1)]["company"] == "Co1"
assert intern.rules(listings[ID(5)]) == (False, "degree PhD") and intern.rules(listings[ID(7)])[0] is False
assert intern.rules(listings[ID(9)])[0] and intern.rules(listings[ID(1)])[0]
again, new2, closed2 = intern.merge(listings, [sim(1, active=False)] + raw[1:], TODAY + timedelta(days=1))
assert new2 == [] and closed2 == [ID(1)] and again[ID(1)]["first_seen"] == TODAY.isoformat()
gone, _, closed3 = intern.merge(listings, raw[5:], TODAY)  # 1 dropped from the file entirely
assert ID(1) in closed3 and gone[ID(1)]["active"] is False and gone[ID(1)]["gone"]
assert "rising junior" in intern.standing() and "2029" in intern.standing()
for name in ("INTERN_TRIAGE_PROMPT.md", "INTERN_SCORE_PROMPT.md"):
    with open(jobs.render_prompt(name, intern.prompt_fields())) as f:
        text = f.read()
    assert "{{" not in text and "Summer 2027" in text, name
assert "rising junior" in open(os.path.join(config.WORK_DIR, "INTERN_SCORE_PROMPT.md")).read()

# ---------------------------------------------------------------- the pipeline (Claude's outputs written here)
intern.save("listings.json", listings)
work = config.WORK_DIR
config.RESUME_PATH = os.path.join(STATE, "resume.pdf")
from nuauto import demo  # noqa: E402
with open(config.RESUME_PATH, "wb") as f:
    f.write(demo.tiny_pdf(["Test Student", "C, Python, Linux"]))


def answer(kind, fn):
    """Claude's part: an output file per batch file, from fn(job) -> the job's result."""
    for name in sorted(os.listdir(work)):
        if name.startswith(f"{kind}_in_"):
            with open(os.path.join(work, name)) as f:
                items = json.load(f)
            with open(os.path.join(work, name.replace("_in_", "_out_")), "w") as f:
                json.dump([{"id": j["id"], **fn(j)} for j in items], f)


files = intern.triage_export()
assert len(files) == 1
with open(files[0]) as f:
    batch = json.load(f)
assert {j["id"] for j in batch} == {ID(n) for n in (1, 6, 8, 9, 10)}  # PhD-only and closed are never sent
assert set(batch[0]) == {"id", "title", "company", "locations", "category"}
answer("itriage", lambda j: {"keep": "Marketing" not in j["title"], "fit": "medium", "why": "t"})
with open(os.path.join(work, "itriage_out_001.json")) as f:
    rows_ = json.load(f)
next(r for r in rows_ if r["id"] == ID(6))["fit"] = "excellent"  # not one of FITS
next(r for r in rows_ if r["id"] == ID(8))["id"] = ID(8)[:-1] + "x"  # a mis-copied id (Claude does that now and then)
rows_.append("not a row")
with open(os.path.join(work, "itriage_out_001.json"), "w") as f:
    json.dump(rows_, f)
intern.triage_import()  # the good rows go in; the two bad ones are only reported, and tried again next run
triage = intern.load("triage.json", {})
assert sorted(triage) == sorted(ID(n) for n in (1, 9, 10)), sorted(triage)
files = intern.triage_export()
with open(files[0]) as f:
    assert sorted(j["id"] for j in json.load(f)) == sorted([ID(6), ID(8)])  # only the two left
with open(os.path.join(work, "itriage_out_001.json"), "w") as f:
    json.dump([{"id": "nope", "keep": True}], f)
try:  # nothing usable at all: the step stops and says why
    intern.triage_import()
    raise AssertionError("imported nothing without saying so")
except SystemExit as e:
    assert "Not imported" in str(e) and "ids missing" in str(e), e
answer("itriage", lambda j: {"keep": True, "fit": "high" if j["id"] == ID(6) else "medium", "why": "t"})
intern.triage_import()
triage = intern.load("triage.json", {})
assert triage[ID(6)]["fit"] == "high" and intern.triage_export() == []  # nothing left to triage

# read the postings: a fake job site (plain) and a fake hidden browser
config.DEMO = False
TEXT = "Responsibilities: firmware in C. Qualifications: Python, Linux. Rising juniors and seniors welcome. " * 6
plain_ok = {f"https://jobs.example.com/{n}" for n in (6, 9, 10)}
real_plain, real_browser = postings.read_plain, postings.Browser
browser_reads = []


class StubBrowser:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def read(self, url):
        browser_reads.append(url)
        return None  # renders nothing usable


def fake_plain(url, client):
    if url in plain_ok:
        return postings.result({"title": "", "description": TEXT, "location": "", "pay": "$40 per hour", "deadline": None},
                               "page data")
    raise postings.Unreadable("page: no posting text (built by JavaScript?)")


postings.read_plain, postings.Browser = fake_plain, StubBrowser
# order: fit high (6) first, then the newest; 1 and 8 can't be read by plain requests. The hidden browser may read
# one per run (browser_per_run 1): 1 gets it (a try counted), 8 waits for the next run (no try counted).
ok, bad = intern.fetch(lambda m: None)
assert (ok, bad) == (3, 2), (ok, bad)
assert browser_reads == ["https://jobs.example.com/1"], browser_reads
failed = intern.load("failed.json", {})
assert list(failed) == [ID(1)] and failed[ID(1)]["tries"] == 1 and failed[ID(1)]["last"] == date.today().isoformat(), failed
assert sorted(intern.load_details()) == sorted(ID(n) for n in (6, 9, 10))
d6 = intern.load(f"details/{ID(6)}.json", None)
assert d6["url"] == "https://jobs.example.com/6" and d6["states"] == ["US-MA", "US-NY"] and d6["pay"] == "$40 per hour"
assert d6["position_types"] == ["Internship"] and d6["source"] == "simplify" and jobs.in_home_state(d6)
assert intern.fetch(lambda m: None) == (0, 1)  # same day: 1 is not tried again; 8 gets the browser now
assert browser_reads[-1] == "https://jobs.example.com/8" and sorted(intern.load("failed.json", {})) == sorted([ID(1), ID(8)])
assert intern.fetch(lambda m: None) == (0, 0)  # both tried today
first = ID(1)
for k in range(intern.TRIES - 1):  # later runs: tried every way each time, then given up on
    failed = intern.load("failed.json", {})
    failed[first]["last"] = "2000-01-01"
    intern.save("failed.json", failed)
    assert intern.fetch(lambda m: None) == (0, 1)
failed = intern.load("failed.json", {})
assert intern.unreadable(failed, first) and not intern.unreadable(failed, ID(8)), failed
failed[first]["last"] = "2000-01-01"
intern.save("failed.json", failed)
assert intern.fetch(lambda m: None) == (0, 0)  # given up on: never tried again
postings.read_plain, postings.Browser = real_plain, real_browser


class NoBrowser:  # Playwright's Firefox missing: plain requests only for the rest of the run, no try counted
    def __enter__(self):
        raise RuntimeError("Executable doesn't exist")


said = []
postings.read_plain, postings.Browser = fake_plain, NoBrowser
failed = intern.load("failed.json", {})
failed[ID(8)]["last"] = "2000-01-01"
intern.save("failed.json", failed)
assert intern.fetch(said.append) == (0, 1) and any("could not start" in m for m in said), said
assert intern.load("failed.json", {})[ID(8)]["tries"] == 1  # not counted: the browser never ran
postings.read_plain, postings.Browser = real_plain, real_browser

# score: who may apply decides the bar; bad output is refused
files = intern.score_export()
with open(files[0]) as f:
    items = json.load(f)
assert {j["id"] for j in items} == {ID(n) for n in (6, 9, 10)} and items[0]["id"] == ID(6)  # high fit first
assert TEXT[:50] in items[0]["description"]
answer("iscore", lambda j: {"match": 70, "year_req": "maybe", "met": [], "partial": [], "missing": []})
try:
    intern.score_import()
    raise AssertionError("bad year_req imported")
except SystemExit as e:
    assert "year_req" in str(e)
REQ = {ID(6): ("ok", 62), ID(9): ("one_year_up", 85), ID(10): ("grad", 99)}
answer("iscore", lambda j: {"match": REQ[j["id"]][1], "year_req": REQ[j["id"]][0], "year_text": "Rising seniors" * 30,
                            "met": ["C", "Python"], "partial": ["Linux"], "missing": ["FPGA"], "why": "fits"})
intern.score_import()
scores = intern.load("scores.json", {})
assert len(scores[ID(9)]["year_text"]) == 300 and scores[ID(6)]["scored"] == date.today().isoformat()
files = intern.cat_export()
answer("icat", lambda j: {"category": "security" if j["id"] == ID(6) else "embedded"})
intern.cat_import()

pool = intern.build_pool()
by = {r["id"]: r for r in pool}
# 6: security's lower bar (60) lets 62% in; 9: a year up -> threshold_above (90): 85% stays out; 10: graduate only: out
assert set(by) == {ID(6)}, by.keys()
assert by[ID(6)]["threshold"] == 60 and by[ID(6)]["url"] == "https://jobs.example.com/6" and by[ID(6)]["source"] == "simplify"
assert by[ID(6)]["rank"] == 62 + 10 + 20  # match + Boston + security
assert "apply on company site too?" not in by[ID(6)]["flags"]
jobs.save("ratings.json", {ID(9): {"label": 1, "date": "2026-10-01"}})  # rated yes: kept below the bar
pool = intern.build_pool()
assert {r["id"] for r in pool} == {ID(6), ID(9)} and "kept: rated yes" in {r["id"]: r for r in pool}[ID(9)]["flags"]
assert "for students a year further along (in text)" in {r["id"]: r for r in pool}[ID(9)]["flags"]
jobs.save("ratings.json", {})
ok_, thr, flags = intern.entry_rules(listings[ID(8)], {"description": "x", "qualifications": ""}, {"year_req": "ok"}, "other")
assert ok_ and thr == 65 and "posted 90 days ago" in flags and "vague posting, low confidence" in flags, flags
ok_, thr, flags = intern.entry_rules(listings[ID(10)], {"description": "graduating in 2027", "qualifications": ""},
                                     {"year_req": "ok", "met": ["a"] * 4}, "embedded", TODAY)
assert ok_ and "US citizens only" in flags and "mentions graduation 2027" in flags, flags
assert intern.entry_rules(listings[ID(10)], {"description": "", "qualifications": ""}, {"year_req": "two_years_up"},
                          "embedded")[0] is False

# Review: the Other jobs tab's rows decide what is new; a Proposed row comes first and keeps its row
assert [r["id"] for r in intern.save_pool()] == [ID(6)]
assert intern.load("unread.json", None) == [{"id": ID(1), "url": "https://jobs.example.com/1"}]  # for the GUI's count
R = lambda n, url, status: sheet.Row(n, url, "Co", "T", status, "", "")  # noqa: E731
todo, urgent = intern.review_queue("approve", {}, [])
ids = [r["id"] for r in todo]
assert ids[0] == ID(6) and ids[-1] == first and todo[-1]["unread"] and todo[-1]["match"] is None, ids
todo, _ = intern.review_queue("approve", {}, [R(2, "https://jobs.example.com/6?utm_source=x", "Approved")])
assert ID(6) not in [r["id"] for r in todo]  # already in the tab (tracking parameters don't matter)
todo, _ = intern.review_queue("approve", {}, [R(7, "https://jobs.example.com/6", "Proposed")])
assert todo[0]["id"] == ID(6) and todo[0]["row"] == 7 and "already Proposed (Other jobs row 7)" in todo[0]["flags"]
todo, _ = intern.review_queue("approve", {ID(6): {"label": 0, "date": "x"}}, [])
assert ID(6) not in [r["id"] for r in todo]
todo, _ = intern.review_queue("rate", {}, [])
assert [r["id"] for r in todo] == [ID(6)]  # rate: scored ones only
view = intern.job_view(todo[0], intern.details_for(todo[0]))
assert view["url"] == "https://jobs.example.com/6" and view["source"] == "simplify" and view["external"] is None
assert view["posted_text"] == "posted 2 days ago" and view["match"] == 62
unread = [r for r in intern.review_queue("approve", {}, [])[0] if r.get("unread")][0]
uv = intern.job_view(unread, intern.details_for(unread))
assert uv["match"] is None and uv["unread"] and uv["description"] == [] and "could not be read" in uv["score"]
item = intern.sheet_item(todo[0])
assert item["url"] == "https://jobs.example.com/6" and item["notes"].startswith("Summer 2027 internship (Simplify's list); match 62%")
assert "not scored" in intern.sheet_item(unread)["notes"]

# the taste model learns from both: enough ratings -> taste ordering, cached between Review loads
ratings = {f"x{k}": {"label": k % 2, "date": "x"} for k in range(12)}
nuworks = {f"x{k}": {"id": f"x{k}", "title": "Firmware" if k % 2 else "Sales", "skills": [], "function": [],
                     "description": ("firmware C embedded " if k % 2 else "sales marketing ") * 20} for k in range(12)}
real_load = jobs.load_details
jobs.load_details = lambda: nuworks
pool, trained = intern.ranked_pool(ratings, [])
assert trained and all("taste" in r for r in pool) and len(intern._TASTE) == 1
pool2, _ = intern.ranked_pool(ratings, [])
assert [r["taste"] for r in pool2] == [r["taste"] for r in pool] and len(intern._TASTE) == 1
jobs.load_details = real_load

# summary for the morning message; stats run
intern.save("scans.json", [{"time": "2026-10-10T06:00", "listed": 4, "pool": 1}, {"time": "2026-10-08T06:00", "listed": 9, "pool": 9}])
from datetime import datetime  # noqa: E402
assert intern.summary(datetime(2026, 10, 10, 8, 0)) == \
    "Internships (Summer 2027): 4 new on Simplify's list, 1 made your pool (Review > Summer internships)"
intern.stats()

# the whole update, Claude's calls stood in for, the list read from the demo file
from nuauto import daily  # noqa: E402
sent = []
daily.notify = lambda title, body="": sent.append((title, body))
daily.claude_ready = lambda: True
daily.LOG_PATH = os.path.join(STATE, "logs", "test.txt")


def fake_claude(prompt_file, batch, extra=None):
    kind = os.path.basename(batch).split("_in_")[0]
    with open(batch) as f:
        items = json.load(f)
    out = {"itriage": {"keep": True, "fit": "high"},
           "iscore": {"match": 91, "year_req": "ok", "met": ["C", "Python", "Linux"], "partial": ["x"], "missing": [], "why": "w"},
           "icat": {"category": "embedded"}}[kind]
    assert extra == (intern.prompt_fields() if kind != "icat" else None), (kind, extra)
    with open(batch.replace("_in_", "_out_"), "w") as f:
        json.dump([{"id": j["id"], **out} for j in items], f)


daily.run_claude = fake_claude
config.DEMO = True
intern.save("demo_listings.json", raw + [sim(11, title="Robotics Intern", places=("Waltham, MA",))])
intern.save("demo_postings.json", {"https://jobs.example.com/11": TEXT})
assert intern.update() == 0
pool = intern.load("pool.json", [])
assert ID(11) in {r["id"] for r in pool} and sent and sent[-1][0] == "1 new internship in your pool", sent
assert "Robotics Intern" in sent[-1][1]
# an approved internship that Simplify closes: a message (the sheet is only read)
real_rows = intern.other_rows
intern.other_rows = lambda interactive=True: [R(4, "https://jobs.example.com/11", "Approved"), R(5, "https://jobs.example.com/6", "Applied")]
sent.clear()
intern.closed_notice([ID(11), ID(6)], daily)
assert len(sent) == 1 and sent[0][0] == "1 approved internship closed on Simplify's list" and "Other jobs row 4" in sent[0][1], sent
intern.other_rows = lambda interactive=True: (_ for _ in ()).throw(sheet.NotLoggedIn("expired"))
sent.clear()
intern.closed_notice([ID(11)], daily)  # the sheet can't be read: no message, no crash
assert sent == []
intern.other_rows = real_rows
# a crash in the 2-hourly run: Discord says so, the unit fails
real_update = intern.update
intern.update = lambda: 1 / 0
try:
    intern.main(["update"])
    raise AssertionError("no crash")
except ZeroDivisionError:
    pass
assert sent and sent[-1][0] == "NUauto: internship update failed" and "ZeroDivisionError" in sent[-1][1], sent
intern.update = lambda: 0
for argv, code in ((["update", "--here"], 0), (["update", "--now"], None), (["stats", "x"], None)):
    try:
        intern.main(argv)
        raise AssertionError(argv)
    except SystemExit as e:  # --here: this machine even with a homelab; anything else extra: the usage text
        assert (e.code == 0) if code == 0 else "nuauto intern update" in str(e.code), (argv, e.code)
intern.update = real_update
lock = intern.run_lock()  # one update at a time
assert lock is not None and intern.run_lock() is None
assert intern.update() == 0  # busy: says so, does nothing
lock.close()
config.DEMO = False

shutil.rmtree(STATE, ignore_errors=True)
srv.shutdown()
print("All internship checks passed.")
