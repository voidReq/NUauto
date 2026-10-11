"""Offline checks for accounts.py's sites and your own logins (imported from Bitwarden). Run: python tests/test_accounts.py

A temp state folder: never the real local/accounts.json. No network.
"""
import io
import json
import os
import subprocess
import sys
import tempfile

os.environ["NUAUTO_STATE_DIR"] = tempfile.mkdtemp(prefix="nuauto-test-accounts-")  # never the real local/ (logins)

from nuauto import config  # noqa: E402

os.makedirs(config.LOCAL_DIR, mode=0o700, exist_ok=True)
from nuauto import accounts as A  # noqa: E402
from nuauto import sync  # noqa: E402

# sites: the host, with the company on Workday's shared hosts (in the address's path)
assert A.site("https://acme.wd5.myworkdayjobs.com/en-US/careers/job/X_1") == "acme.wd5.myworkdayjobs.com"
assert A.site("https://wd1.myworkdaysite.com/en-US/recruiting/wf/WellsFargoJobs/job/1") == "wd1.myworkdaysite.com/wf"
assert A.site("https://wd1.myworkdaysite.com/recruiting/Acme/careers") == "wd1.myworkdaysite.com/acme"
assert A.site("https://wd1.myworkdaysite.com/") == "wd1.myworkdaysite.com"           # no company in it: the host
assert A.site("wd1.myworkdaysite.com/wf") == "wd1.myworkdaysite.com/wf" == A.site(A.site("wd1.myworkdaysite.com/wf"))
assert A.site("https://example.com/recruiting/x") == "example.com"                     # only Workday's shared hosts
assert A.site("ACME.wd5.MyWorkdayJobs.com:443") == "acme.wd5.myworkdayjobs.com"
assert A.domain("account.amazon.jobs") == "amazon.jobs" == A.domain("https://passport.amazon.jobs/x")
assert A.domain("acme.wd5.myworkdayjobs.com") == "acme.wd5.myworkdayjobs.com"            # shared: the exact host only
assert A.domain("https://wd1.myworkdaysite.com/recruiting/wf/x") == "wd1.myworkdaysite.com/wf"
assert A.never("https://accounts.google.com/x") and not A.never("wd1.myworkdaysite.com/wf")

# Bitwarden's items: only logins whose address has one of the --only texts, with a username and a password, never an
# identity provider's; an encrypted export is refused
items = [
    {"type": 1, "name": "Wells Fargo", "login": {"username": "me@school.edu", "password": "pw-wf",
                                                 "uris": [{"uri": "https://wd1.myworkdaysite.com/en-US/recruiting/wf/WellsFargoJobs/login"}]}},
    {"type": 1, "name": "Acme (Workday)", "login": {"username": "me@gmail.example", "password": "pw-acme",
                                                    "uris": [{"uri": "https://acme.wd5.myworkdayjobs.com/careers"}]}},
    {"type": 1, "name": "Amazon jobs", "login": {"username": "me@gmail.example", "password": "pw-amz",
                                                 "uris": [{"uri": "account.amazon.jobs/en-US/login"}]}},
    {"type": 1, "name": "Google", "login": {"username": "me", "password": "g", "uris": [{"uri": "https://accounts.google.com/myworkday"}]}},
    {"type": 1, "name": "Bank", "login": {"username": "me", "password": "b", "uris": [{"uri": "https://mybank.example"}]}},
    {"type": 1, "name": "No password", "login": {"username": "me", "password": "", "uris": [{"uri": "https://x.myworkdayjobs.com"}]}},
    {"type": 2, "name": "A note", "notes": "myworkday"},
]
got = A.bitwarden_logins({"encrypted": False, "items": items}, ["myworkday", "amazon.jobs"])
assert [h for h, _ in got] == ["wd1.myworkdaysite.com/wf", "acme.wd5.myworkdayjobs.com", "account.amazon.jobs"], got
assert A.bitwarden_logins(items, ["MYWORKDAY"])[0][0] == "wd1.myworkdaysite.com/wf"     # `bw list items` (a list), any case
assert A.bitwarden_logins(items, ["nothing-matches"]) == [] and A.bitwarden_logins({"items": items}, [""]) == []
try:
    A.bitwarden_logins({"encrypted": True, "items": []}, ["x"])
    raise AssertionError("encrypted export accepted")
except ValueError as e:
    assert "encrypted" in str(e)

# a login NUauto made on Acme's site before: yours replaces it (kept aside, in case)
nuauto_pw = A.password_for("acme.wd5.myworkdayjobs.com", A.NEW, company="Acme")
assert A.import_own(got) == ["wd1.myworkdaysite.com/wf", "acme.wd5.myworkdayjobs.com", "account.amazon.jobs"]
data = json.load(open(A.PATH))
assert oct(os.stat(A.PATH).st_mode)[-3:] == "600"
acme = data["acme.wd5.myworkdayjobs.com"]
assert acme["own"] and acme["password"] == "pw-acme" and acme["replaced_nuauto"]["password"] == nuauto_pw
assert data["account.amazon.jobs"]["match"] == "domain" and data["acme.wd5.myworkdayjobs.com"]["match"] == "host"

# yours are used: on their site (Amazon: the whole amazon.jobs domain; Workday: that company only), never a new one
assert A.password_for("passport.amazon.jobs", A.SIGN_IN) == "pw-amz"
assert A.password_for("https://wd1.myworkdaysite.com/en-US/recruiting/wf/WellsFargoJobs/login", A.SIGN_IN) == "pw-wf"
for where in ("https://account.amazon.jobs/x", "acme.wd5.myworkdayjobs.com", "wd1.myworkdaysite.com/wf"):
    try:
        A.password_for(where, A.NEW)
        raise AssertionError(f"a new account on {where}, where you have your own")
    except A.NoAccount as e:
        assert "own account" in str(e) and A.SIGN_IN in str(e)
other = A.password_for(A.site("https://wd1.myworkdaysite.com/recruiting/other/x"), A.NEW)  # another company there
assert other not in ("pw-wf", "pw-acme") and not json.load(open(A.PATH))["wd1.myworkdaysite.com/other"].get("own")
assert A.own_login("https://amazon.jobs/en/jobs/123") == {"site": "account.amazon.jobs", "email": "me@gmail.example",
                                                          "company": "Amazon jobs"}
assert A.own_login("https://wd1.myworkdaysite.com/recruiting/other/x") is None and A.own_login("https://other.example") is None
# redaction knows yours too, and the one a merge kept aside
assert {"pw-wf", "pw-amz"} <= set(A.passwords(["wd1.myworkdaysite.com/wf", "account.amazon.jobs"]))
# yours are never exported back to Bitwarden (they came from there)
path, n = A.export(os.path.join(config.STATE_DIR, "export.json"), every=True)
exported = json.load(open(path))["items"]
assert n == len(exported) and all(i["login"]["password"] not in ("pw-wf", "pw-acme", "pw-amz") for i in exported)

# a merge with the other machine: your own login stays the site's login, even against an older one NUauto made there
mine = {"x.wd1.myworkdayjobs.com": {"site": "x.wd1.myworkdayjobs.com", "password": "nuauto-old", "created": "2026-10-01T10:00"}}
theirs = {"x.wd1.myworkdayjobs.com": {"site": "x.wd1.myworkdayjobs.com", "password": "yours", "own": True,
                                      "created": "2026-10-11T10:00"}}
for a, b in ((mine, theirs), (theirs, mine)):
    merged, clashes = sync.merge_accounts(a, b)
    assert merged["x.wd1.myworkdayjobs.com"]["password"] == "yours" and "nuauto-old" in merged["x.wd1.myworkdayjobs.com"]["other_passwords"]

# the command line: --only is required; a file or stdin; nothing matched; a bad file
cli = [sys.executable, "-m", "nuauto.accounts"]
env = {**os.environ}
r = subprocess.run(cli + ["import", "-"], input="[]", capture_output=True, text=True, env=env)
assert r.returncode and "--only" in r.stderr
r = subprocess.run(cli + ["import", "-", "--only", "nothing"], input=json.dumps(items), capture_output=True, text=True, env=env)
assert r.returncode == 0 and "No logins matched" in r.stdout
f = os.path.join(config.STATE_DIR, "bitwarden_export.json")
json.dump({"encrypted": False, "items": items}, open(f, "w"))
r = subprocess.run(cli + ["import", f, "--only", "amazon.jobs"], capture_output=True, text=True, env=env)
assert r.returncode == 0 and "account.amazon.jobs" in r.stdout and "pw-amz" not in r.stdout and f"rm {f}" in r.stdout
r = subprocess.run(cli + ["import", "/nonexistent.json", "--only", "x"], capture_output=True, text=True, env=env)
assert r.returncode and "Could not read" in r.stderr
r = subprocess.run(cli, capture_output=True, text=True, env=env)
assert r.returncode == 0 and "yours (imported)" in r.stdout and "pw-" not in r.stdout   # the list: never a password

print("All accounts checks passed.")
