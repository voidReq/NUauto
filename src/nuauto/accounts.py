"""Logins the company-site agent makes for you (nuauto assist): one per site (a company's Workday, iCIMS, Oracle...
site), with your account email and a random password NUauto makes. The agent never sees a password: it types a
placeholder ({{NEW_PASSWORD}} to create an account, {{PASSWORD}} to sign in) and the guard hook types the real one,
for that site only (assist.py). Kept in local/accounts.json (mode 600, never synced, never printed).

  nuauto accounts                    the sites you have a login on (no passwords)
  nuauto accounts export [<file>]    Bitwarden's import file (.json) with the logins not exported yet (default
                                     ~/Downloads/nuauto-logins-<date>.json). Import it in Bitwarden: the web vault,
                                     Tools > Import data > "Bitwarden (json)". Then delete the file.
  nuauto accounts export --all       every login again (e.g. into another password manager's copy)

The account email: local_config.json "accounts_email" (else your answer bank's email).
"""
import fcntl
import json
import os
import secrets
import string
import sys
import uuid
from contextlib import contextmanager
from datetime import date, datetime

from nuauto import config

PATH = os.path.join(config.LOCAL_DIR, "accounts.json")
LOCK = os.path.join(config.LOCAL_DIR, "accounts.lock")
NEW = "{{NEW_PASSWORD}}"      # create an account here: a new password for this site (the same one again if it has one)
SIGN_IN = "{{PASSWORD}}"      # sign in here: this site's saved password
PLACEHOLDERS = (NEW, SIGN_IN)
SYMBOLS = "!@#$%*-_+="        # symbols the sites' password rules accept (no quotes, spaces or backslashes)
LENGTH = 20
# Sign-ins that are never NUauto's: identity providers (your Google / Microsoft / Apple / LinkedIn / GitHub account)
# and Northeastern's own. A site can still offer "Sign in with Google": that button is yours.
NEVER = ("google.com", "gmail.com", "microsoftonline.com", "live.com", "microsoft.com", "apple.com", "icloud.com",
         "linkedin.com", "github.com", "facebook.com", "yahoo.com", "neu.edu", "northeastern.edu", "symplicity.com",
         "duosecurity.com", "login.gov", "bitwarden.com")
BITWARDEN_HOST_MATCH = 1      # Bitwarden's URI match "Host": the login is offered on that site only
FOLDER = "NUauto job sites"


class NoAccount(Exception):
    """No saved login for that site."""


def make_password(length=LENGTH):
    """Random: letters, digits and a symbol or two; at least one of each kind (most sites ask for that)."""
    pools = [string.ascii_lowercase, string.ascii_uppercase, string.digits, SYMBOLS]
    alphabet = string.ascii_letters + string.digits + SYMBOLS
    while True:
        p = "".join(secrets.choice(alphabet) for _ in range(length))
        if all(any(c in pool for c in p) for pool in pools):
            return p


def site(url_or_host):
    """The site a login belongs to: the host (a company's Workday is <company>.wd5.myworkdayjobs.com)."""
    from urllib.parse import urlparse
    s = str(url_or_host or "").strip().lower()
    return (urlparse(s).hostname or "") if "://" in s else s.split("/")[0].split(":")[0]


def never(host):
    """Why NUauto never fills a password on this host (an identity provider, Northeastern), or None."""
    h = site(host)
    if not h:
        return "no site (the page's address is unknown: take a snapshot first)"
    for d in NEVER:
        if h == d or h.endswith("." + d):
            return f"{h} is a sign-in that is yours (your own account), never a password NUauto made"
    return None


def account_email():
    e = (config.LOCAL.get("accounts_email") or "").strip()
    if e:
        return e
    from nuauto import answers
    entry = answers.find(answers.load(), "email") if os.path.exists(config.ANSWERS_PATH) else None
    return (entry or {}).get("answer", "")


@contextmanager
def locked():
    """accounts.json, read and written under a lock (assistants may run side by side). Yields the dict."""
    os.makedirs(config.LOCAL_DIR, mode=0o700, exist_ok=True)
    with open(LOCK, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            with open(PATH) as f:
                data = json.load(f)
        except FileNotFoundError:
            data = {}
        before = json.dumps(data, sort_keys=True)
        yield data
        if json.dumps(data, sort_keys=True) != before:
            tmp = PATH + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=1)
            os.chmod(tmp, 0o600)
            os.replace(tmp, PATH)


def password_for(host, placeholder, company="", job=None):
    """The real password for a placeholder on this host. NEW: this site's saved password, or a new one (saved now).
    SIGN_IN: the saved one, else NoAccount. job: {"title", "url"} the login is used for (kept in its notes)."""
    h = site(host)
    why = never(h)
    if why:
        raise NoAccount(why)
    if placeholder not in PLACEHOLDERS:
        raise ValueError(placeholder)
    with locked() as data:
        a = data.get(h)
        if a is None:
            if placeholder == SIGN_IN:
                raise NoAccount(f"NUauto has no saved login for {h}: create an account ({NEW}), or ask the user")
            a = data[h] = {"site": h, "url": f"https://{h}/", "email": account_email(), "password": make_password(),
                           "company": company, "created": datetime.now().isoformat(timespec="minutes"), "jobs": [],
                           "exported": False}
        if job and job.get("url") and job["url"] not in [j.get("url") for j in a["jobs"]]:
            a["jobs"].append({"title": job.get("title", ""), "url": job["url"], "date": date.today().isoformat()})
        if company and not a.get("company"):
            a["company"] = company
        return a["password"]


def passwords(hosts):
    """The saved passwords of these hosts (for the guard's redaction), longest first."""
    try:
        with open(PATH) as f:
            data = json.load(f)
    except FileNotFoundError:
        return []
    return sorted({data[site(h)]["password"] for h in hosts if site(h) in data}, key=len, reverse=True)


def bitwarden(accounts):
    """Bitwarden's .json import format: a folder, and one login item per site (matched on that site's host only)."""
    folder = str(uuid.uuid4())
    items = []
    for a in accounts:
        jobs = "\n".join(f"- {j.get('title') or 'job'}: {j['url']} ({j.get('date', '')})" for j in a.get("jobs", []))
        items.append({
            "id": str(uuid.uuid4()), "organizationId": None, "folderId": folder, "type": 1, "reprompt": 0,
            "name": f"{a.get('company') or a['site']} (job site)", "favorite": False, "fields": [], "collectionIds": None,
            "notes": f"Made by NUauto on {a['created'][:10]} for job applications on {a['site']}." + (f"\nJobs:\n{jobs}" if jobs else ""),
            "login": {"uris": [{"match": BITWARDEN_HOST_MATCH, "uri": a["url"]}], "username": a["email"],
                      "password": a["password"], "totp": None}})
    return {"encrypted": False, "folders": [{"id": folder, "name": FOLDER}], "items": items}


def export(path=None, every=False):
    """Write Bitwarden's import file (mode 600) with the logins not exported yet (every: all); mark them exported.
    Returns (path, count); count 0 writes nothing."""
    with locked() as data:
        todo = [a for a in data.values() if every or not a.get("exported")]
        if not todo:
            return None, 0
        path = os.path.expanduser(path or f"~/Downloads/nuauto-logins-{date.today():%Y-%m-%d}.json")
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(bitwarden(todo), f, indent=1)
        for a in todo:
            a["exported"] = date.today().isoformat()
        return path, len(todo)


def main(argv):
    if not argv:
        try:
            with open(PATH) as f:
                data = json.load(f)
        except FileNotFoundError:
            data = {}
        if not data:
            return print("No logins yet. The assistant makes one when a company's site needs an account.")
        for a in sorted(data.values(), key=lambda a: a["created"]):
            print(f"{a['created'][:10]}  {a['site']:45} {a.get('company', '')[:25]:25} {a['email']}  "
                  f"{len(a.get('jobs', []))} job(s)  {'exported ' + a['exported'] if a.get('exported') else 'not exported'}")
        return
    if argv[0] == "export" and len(argv) <= 3:
        rest = [a for a in argv[1:] if a != "--all"]
        path, n = export(rest[0] if rest else None, every="--all" in argv)
        if not n:
            return print("Nothing new to export (`nuauto accounts export --all` writes every login again).")
        return print(f"Wrote {n} login{'s' if n > 1 else ''} to {path} (only you can read it).\n"
                     "Import it in Bitwarden: the web vault > Tools > Import data > \"Bitwarden (json)\", then delete "
                     f"the file:\n  rm {path}")
    sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
