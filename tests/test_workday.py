"""Offline checks for workday.py (your statuses on each company's Workday Candidate Home).
Run: python test_workday.py

No browser, no Workday, no sheet: made-up logins, table rows and sheet rows.
"""
import json
import os
import tempfile

os.environ["NUAUTO_STATE_DIR"] = tempfile.mkdtemp(prefix="nuauto-test-workday-")  # never the real local/ (its logins)

from nuauto import accounts  # noqa: E402
from nuauto import sheet  # noqa: E402
from nuauto import workday  # noqa: E402

tmp = tempfile.mkdtemp()
accounts.PATH = os.path.join(tmp, "accounts.json")
ACME = "acme.wd5.myworkdayjobs.com"
POST = f"https://{ACME}/en-US/AcmeCareers/job/Boston-MA/Software-Engineering-Co-op_R12345"
POST2 = f"https://{ACME}/AcmeCareers/details/Data-Analyst-Intern_JR-998"
SHARED = "https://wd1.myworkdaysite.com/recruiting/bank/BankJobs/job/Boston/Analyst-Co-op_R77"


def row(n, company, title, status="Applied", notes=""):
    return sheet.Row(n, f"https://nuworks.example/jobs/{n}", company, title, status, notes, "2026-10-10")


# ---------- Candidate Home's address ----------
assert workday.home_url(POST) == f"https://{ACME}/en-US/AcmeCareers/userHome"
assert workday.home_url(POST2) == f"https://{ACME}/AcmeCareers/userHome"
assert workday.home_url("https://ab.wd5.myworkdayjobs.com/AB/job/Place/Title_R1") == \
    "https://ab.wd5.myworkdayjobs.com/AB/userHome"  # a site named like a language is still the site
assert workday.home_url(SHARED) == "https://wd1.myworkdaysite.com/recruiting/bank/BankJobs/userHome"
assert workday.home_url(f"https://{ACME}/") is None  # no career site in the address
assert workday.home_url(f"http://{ACME}/AcmeCareers/job/x_R1") is None  # https only
assert workday.home_url("https://boards.greenhouse.io/acme/jobs/1") is None  # not Workday
assert workday.home_url("https://wd1.myworkdaysite.com/en-US/recruiting/bank/BankJobs/job/x_R1") == \
    "https://wd1.myworkdaysite.com/en-US/recruiting/bank/BankJobs/userHome"

# ---------- what a status means ----------
for s, k in [("Not Selected", "rejected"), ("No longer under consideration", "rejected"),
             ("Not Selected: No longer under consideration", "rejected"), ("We are not moving forward", "rejected"),
             ("Withdrawn", "withdrawn"), ("Inactive", "closed"), ("Position Filled", "closed"),
             ("Job Posting Closed", "closed"), ("Submitted", "active"), ("In Progress", "active"),
             ("Under Review", "active"), ("Not Submitted", "draft"), ("", "unknown"), ("Boston, MA", "unknown")]:
    assert workday.kind(s) == k, (s, workday.kind(s))

# ---------- reading a table row ----------
e = workday.entry(["Job Title", "Job Req", "Status", "Date Submitted"],
                  ["Software Engineering Co-op", "R12345", "Not Selected", "10/01/2026"])
assert e["title"] == "Software Engineering Co-op" and e["status"] == "Not Selected" and "R12345" in e["text"]
e2 = workday.entry([], ["Data Analyst Intern", "10/02/2026", "No longer under consideration"])  # no headers
assert e2["title"] == "Data Analyst Intern" and e2["status"] == "No longer under consideration"
assert workday.entry(["Title"], ["A", "B"])["title"] == "A"
assert workday.entry(["Job Title", "Status"], ["IT Analyst", "Not Submitted\nCreated on October 12, 2026"])["status"] == \
    "Not Submitted"  # a cell's first line only
assert workday.TABS.match("Inactive (1)").group(2) == "1" and workday.TABS.match("Active (0)")  # headers that don't line up are ignored

# ---------- logins, and where a password may go ----------
data = {
    ACME: {"site": ACME, "email": "me@example.com", "password": "pw1", "company": "Acme",
           "jobs": [{"title": "SWE Co-op", "url": POST}, {"title": "Data Intern", "url": POST2}]},
    "wd1.myworkdaysite.com/bank": {"site": "wd1.myworkdaysite.com/bank", "email": "me@example.com", "password": "pw2",
                                   "company": "Bank", "jobs": []},
    "wd1.myworkdaysite.com/other": {"site": "wd1.myworkdaysite.com/other", "email": "me@example.com",
                                    "password": "pw3", "company": "Other", "jobs": []},
    "jobs.lever.co": {"site": "jobs.lever.co", "email": "me@example.com", "password": "pw4", "jobs": []},
    "beta.wd1.myworkdayjobs.com": {"site": "beta.wd1.myworkdayjobs.com", "email": "me@example.com", "password": "pw5",
                                   "own": True, "jobs": []},
}
with open(accounts.PATH, "w") as f:
    json.dump(data, f)
rows = [row(2, "Acme", "SWE Co-op"), row(3, "Acme", "Data Intern"),
        row(4, "Bank", "Analyst Co-op", notes=f"External application: wd1.myworkdaysite.com -> {SHARED} | match 80%"),
        row(5, "Beta", "Intern", status="Approved",
            notes="External application: beta.wd1.myworkdayjobs.com -> https://beta.wd1.myworkdayjobs.com/B/job/x/I_R5")]
got = {a["site"]: a for a in workday.logins("", rows)}
assert set(got) == {ACME, "wd1.myworkdaysite.com/bank", "wd1.myworkdaysite.com/other", "beta.wd1.myworkdayjobs.com"}
assert got[ACME]["home"] == f"https://{ACME}/en-US/AcmeCareers/userHome"
assert got["wd1.myworkdaysite.com/bank"]["home"] == "https://wd1.myworkdaysite.com/recruiting/bank/BankJobs/userHome"
assert got["wd1.myworkdaysite.com/other"]["home"] is None  # another company on the same host: not its link
assert got["beta.wd1.myworkdayjobs.com"]["home"] is None  # a row not sent yet gives no link
assert [a["site"] for a in workday.logins("bank", rows)] == ["wd1.myworkdaysite.com/bank"]
a = got[ACME]
assert workday.may_sign_in(f"https://{ACME}/en-US/AcmeCareers/userHome", a)
assert not workday.may_sign_in("https://evil.example/AcmeCareers/userHome", a)
assert not workday.may_sign_in("https://other.wd5.myworkdayjobs.com/x/userHome", a)
assert not workday.may_sign_in(f"http://{ACME}/x/userHome", a)
assert not workday.may_sign_in("https://wd1.myworkdaysite.com/recruiting/other/X/userHome", got["wd1.myworkdaysite.com/bank"])
assert not workday.may_sign_in("https://accounts.google.com/x", a)

# ---------- which saved job, which sheet row ----------
apps = [workday.entry(["Job Title", "Job Req", "Status"], ["Software Engineering Co-op", "R12345", "Not Selected"]),
        workday.entry(["Job Title", "Status"], ["Data Analyst Intern", "No longer under consideration"]),
        workday.entry(["Job Title", "Status"], ["Something Else", "Not Selected"])]
assert workday.which_job(apps[0], a, apps)["url"] == POST  # by requisition id
assert workday.which_job(apps[1], a, apps)["url"] == POST2  # by the posting's title words
assert workday.which_job(apps[2], a, apps) is None  # not certain: never guessed
one = {**a, "jobs": [a["jobs"][0]]}
assert workday.which_job(apps[2], one, [apps[2]])["url"] == POST  # the login's only job, the site's only application
assert workday.which_job(apps[2], one, apps) is None
assert workday.which_row(a["jobs"][0], a, rows).number == 2
twice = rows + [row(9, "Acme", "SWE Co-op")]
assert workday.which_row(a["jobs"][0], a, twice) is None  # two rows could be it
bank = got["wd1.myworkdaysite.com/bank"]
assert workday.which_row(bank["jobs"][0], bank, rows).number == 4  # by the link in its Notes
assert workday.which_row(a["jobs"][0], a, [row(2, "Acme", "SWE Co-op", status="Needs Human")]) is None  # not sent

# ---------- what it offers to mark ----------
for x, tab in zip(apps, ("Active", "Inactive", "Inactive")):
    x["tab"], x["kind"] = tab, workday.kind(x["status"])
results = [{"site": ACME, "apps": apps}, {"site": "wd1.myworkdaysite.com/bank", "error": "sign-in didn't work"}]
todo = workday.offers(results, list(got.values()), {"NUworks": rows})
assert [(t, r.number) for t, r, _ in todo] == [("NUworks", 2), ("NUworks", 3)]
done = [row(2, "Acme", "SWE Co-op", status="Rejected"), rows[1]]
assert [r.number for _, r, _ in workday.offers(results, list(got.values()), {"NUworks": done})] == [3]  # Rejected already
dup = results + [{"site": ACME, "apps": [apps[1]]}]
assert [r.number for _, r, _ in workday.offers(dup, list(got.values()), {"NUworks": rows})] == [2, 3]  # once each
apps[0]["kind"] = "active"
assert [r.number for _, r, _ in workday.offers(results, list(got.values()), {"NUworks": rows})] == [3]

# the command is reachable and its module reaches the homelab
from nuauto import cli, sync  # noqa: E402
assert cli.TOOLS["workday"] == "workday" and "workday.py" in sync.PACKAGE

print("All Workday checks passed.")
