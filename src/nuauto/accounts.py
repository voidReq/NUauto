"""Logins the company-site agent makes for you (nuauto assist): one per site (a company's Workday, iCIMS, Oracle...
site), with your account email and a random password NUauto makes. The agent never sees a password: it types a
placeholder ({{NEW_PASSWORD}} to create an account, {{PASSWORD}} to sign in) and the guard hook types the real one,
for that site only (assist.py). Kept in local/accounts.json (mode 600, never synced, never printed).

  nuauto accounts                    the sites you have a login on (no passwords)
  nuauto accounts export [<file>]    Bitwarden's import file (.json) with the logins not exported yet (default
                                     ~/Downloads/nuauto-logins-<date>.json). Import it in Bitwarden: the web vault,
                                     Tools > Import data > "Bitwarden (json)". Then delete the file.
  nuauto accounts export --all       every login again (e.g. into another password manager's copy)
  nuauto accounts export --bitwarden straight into your Bitwarden vault, no file: the logins not there yet, through
                                     Bitwarden's command line (`bw`, which you unlock yourself first:
                                     export BW_SESSION=$(bw unlock --raw)), in the folder "NUauto job sites"; one
                                     already there is not added twice. With a homelab, both machines' logins.
  nuauto accounts import <file>|- --only <site> [--only <site>...]
                                     YOUR OWN logins from Bitwarden, for sites where you have an account already (your
                                     Workday ones, Amazon jobs...): only the logins whose address contains one of the
                                     --only texts. The agent then signs in with them and never makes an account there.
                                     Best without exporting your whole vault, with Bitwarden's command line:
                                       bw list items --search myworkday | nuauto accounts import - --only myworkday
                                     (or a Bitwarden .json export file: delete it afterwards). Never exported back.

The account email: local_config.json "accounts_email" (else your answer bank's email).
"""
import base64
import fcntl
import json
import os
import re
import secrets
import shutil
import string
import subprocess
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


# One domain, many companies: a login there is for its exact host only (each company's Workday is its own site). On any
# other domain, your own login counts for the whole domain (amazon.jobs: its sign-in pages live on account.amazon.jobs).
SHARED = ("myworkdayjobs.com", "myworkdaysite.com", "icims.com", "oraclecloud.com", "taleo.net", "successfactors.com",
          "successfactors.eu", "sapsf.com", "greenhouse.io", "lever.co", "smartrecruiters.com", "ashbyhq.com",
          "jobvite.com", "workable.com", "eightfold.ai", "brassring.com", "ultipro.com", "paylocity.com", "adp.com",
          "dayforcehcm.com", "applytojob.com", "bamboohr.com", "recruitee.com", "teamtailor.com", "jazzhr.com")


class NoAccount(Exception):
    """No saved login for that site."""


def domain(host):
    """What one of your own logins counts for: the exact host on a shared job-site domain (SHARED), else the domain
    (its last two labels: account.amazon.jobs -> amazon.jobs)."""
    key = site(host)
    h = host_of(key)
    if any(h == d or h.endswith("." + d) for d in SHARED):
        return key
    return ".".join(h.split(".")[-2:])


def find(data, where):
    """The login for this page (or site): the one saved for exactly this site, else one saved for its whole host (a
    login from before sites knew companies' paths, or yours saved without one), else your own login for its domain."""
    key = site(where)
    if key in data:
        return data[key]
    if host_of(key) in data:
        return data[host_of(key)]
    return next((a for a in data.values() if a.get("own") and a.get("match") == "domain" and domain(a["site"]) == domain(key)),
                None)


def make_password(length=LENGTH):
    """Random: letters, digits and a symbol or two; at least one of each kind (most sites ask for that)."""
    pools = [string.ascii_lowercase, string.ascii_uppercase, string.digits, SYMBOLS]
    alphabet = string.ascii_letters + string.digits + SYMBOLS
    while True:
        p = "".join(secrets.choice(alphabet) for _ in range(length))
        if all(any(c in pool for c in p) for pool in pools):
            return p


# Workday's shared hosts carry the company in the path (wd1.myworkdaysite.com/en-US/recruiting/<company>/...): a login
# there is the company's own, so the site is host/<company>.
TENANT_HOSTS = ("myworkdaysite.com",)
TENANT_PATH = re.compile(r"^/(?:[a-z]{2}(?:-[a-z]{2})?/)?recruiting/([^/?#]+)", re.I)


def site(url_or_host):
    """The site a login belongs to: the host (a company's Workday is <company>.wd5.myworkdayjobs.com), or host/<company>
    on a host companies share by path (TENANT_HOSTS). Takes a page's address, a host, or a site this returned."""
    from urllib.parse import urlparse
    s = str(url_or_host or "").strip()
    if "://" in s:
        u = urlparse(s)
        h = (u.hostname or "").lower()
        m = TENANT_PATH.match(u.path or "") if h.endswith(TENANT_HOSTS) else None
        return f"{h}/{m.group(1).lower()}" if m else h
    h, _, rest = s.lower().partition("/")
    h = h.split(":")[0]
    tenant = rest.strip("/")
    return f"{h}/{tenant}" if h.endswith(TENANT_HOSTS) and tenant and "/" not in tenant else h


def host_of(key):
    """The host part of a site (site())."""
    return key.split("/")[0]


def never(host):
    """Why NUauto never fills a password on this host (an identity provider, Northeastern), or None."""
    h = host_of(site(host))
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
        a = find(data, h)
        if a is not None and a.get("own") and placeholder == NEW:
            raise NoAccount(f"the user has their own account on this site (as {a.get('email')}): sign in with that "
                            f"email and {SIGN_IN}, never make a new account here")
        if a is None:
            if placeholder == SIGN_IN:
                raise NoAccount(f"NUauto has no saved login for {h}: create an account ({NEW}), or ask the user")
            a = data[h] = {"site": h, "url": site_url(h), "email": account_email(), "password": make_password(),
                           "company": company, "created": datetime.now().isoformat(timespec="minutes"), "jobs": [],
                           "exported": False}
        if job and job.get("url") and job["url"] not in [j.get("url") for j in a["jobs"]]:
            a["jobs"].append({"title": job.get("title", ""), "url": job["url"], "date": date.today().isoformat()})
        if company and not a.get("company"):
            a["company"] = company
        return a["password"]


def site_url(key):
    """An address for a site (Bitwarden matches the login on its host)."""
    h, _, tenant = key.partition("/")
    return f"https://{h}/recruiting/{tenant}/" if tenant else f"https://{h}/"


def passwords(hosts):
    """The saved passwords of these hosts (for the guard's redaction), longest first."""
    try:
        with open(PATH) as f:
            data = json.load(f)
    except FileNotFoundError:
        return []
    found = set()
    for h in hosts:
        a = find(data, h)
        if a:  # and the other password a merge kept (sync.merge_accounts): it may be the one typed
            found.update(p for p in [a.get("password"), *(a.get("other_passwords") or [])] if p)
    return sorted(found, key=len, reverse=True)


BITWARDEN_REGEX_MATCH = 4   # Bitwarden's URI match "Regular expression": a company's own path on a shared Workday host


def bitwarden_uri(a):
    """Where Bitwarden offers this login: its host; on a host companies share by path, only under that company's path."""
    h, _, tenant = a["site"].partition("/")
    if tenant:
        return {"match": BITWARDEN_REGEX_MATCH,
                "uri": f"^https://{re.escape(h)}/([a-z]{{2}}-[A-Za-z]{{2}}/)?recruiting/{re.escape(tenant)}(/|$)"}
    return {"match": BITWARDEN_HOST_MATCH, "uri": a["url"]}


def bitwarden_item(a, folder):
    """One login as a Bitwarden item (its .json import format, and `bw create item`)."""
    jobs = "\n".join(f"- {j.get('title') or 'job'}: {j['url']} ({j.get('date', '')})" for j in a.get("jobs", []))
    return {"organizationId": None, "folderId": folder, "type": 1, "reprompt": 0,
            "name": f"{a.get('company') or a['site']} (job site)", "favorite": False, "fields": [], "collectionIds": None,
            "notes": f"Made by NUauto on {a['created'][:10]} for job applications on {a['site']}." + (f"\nJobs:\n{jobs}" if jobs else ""),
            "login": {"uris": [bitwarden_uri(a)], "username": a["email"], "password": a["password"], "totp": None}}


def bitwarden(accounts):
    """Bitwarden's .json import format: a folder, and one login item per site (matched on that site's host only)."""
    folder = str(uuid.uuid4())
    items = [{"id": str(uuid.uuid4()), **bitwarden_item(a, folder)} for a in accounts]
    return {"encrypted": False, "folders": [{"id": folder, "name": FOLDER}], "items": items}


class BitwardenError(Exception):
    """Bitwarden's command line is missing, locked, or refused (the message says what to do)."""


def _bw(*args, stdin=None):
    """Bitwarden's command line (`bw`), with your BW_SESSION; its output, or BitwardenError. Never prints it."""
    exe = shutil.which("bw") or config.tool_path("bw")
    if not exe:
        raise BitwardenError("Bitwarden's command line is not installed: npm install -g @bitwarden/cli, then bw login")
    try:
        r = subprocess.run([exe, *args, "--nointeraction"], input=stdin, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        raise BitwardenError(f"bw {args[0]} did not answer in 2 minutes") from None
    if r.returncode:
        raise BitwardenError(f"bw {args[0]}: {(r.stderr or r.stdout).strip()[:200] or 'failed'}")
    return r.stdout


def to_bitwarden():
    """Put the logins NUauto made that are not in Bitwarden yet straight into your vault (no file), in FOLDER: (added,
    already there). Yours (imported from there) never. Needs `bw` unlocked by you (BW_SESSION): NUauto never sees your
    master password. The accounts lock is not held while bw talks to Bitwarden's server."""
    try:
        status = json.loads(_bw("status")).get("status")
    except ValueError:
        status = None
    if status != "unlocked":
        raise BitwardenError("Bitwarden is " + (status or "not readable") + ": unlock it in this terminal first "
                             "(export BW_SESSION=$(bw unlock --raw)), then run this again")
    _bw("sync")
    folders = json.loads(_bw("list", "folders"))
    fid = next((f["id"] for f in folders if f.get("name") == FOLDER), None)
    if not fid:
        fid = json.loads(_bw("create", "folder", stdin=base64.b64encode(json.dumps({"name": FOLDER}).encode()).decode()))["id"]
    there = set()
    for it in json.loads(_bw("list", "items", "--folderid", fid)):
        login = it.get("login") or {}
        for u in login.get("uris") or []:
            there.add((str(u.get("uri") or ""), login.get("username") or ""))
    with locked() as data:
        todo = [dict(a) for a in data.values() if not a.get("own") and not a.get("exported")]
    added, skipped = [], []
    for a in todo:
        item = bitwarden_item(a, fid)
        if (item["login"]["uris"][0]["uri"], a["email"]) in there:
            skipped.append(a["site"])
            continue
        _bw("create", "item", stdin=base64.b64encode(json.dumps(item).encode()).decode())  # stdin: never in argv
        added.append(a["site"])
    with locked() as data:
        for key in added + skipped:
            if key in data:
                data[key]["exported"] = date.today().isoformat()
    return added, skipped


def export(path=None, every=False):
    """Write Bitwarden's import file (mode 600) with the logins not exported yet (every: all); mark them exported.
    Returns (path, count); count 0 writes nothing."""
    with locked() as data:
        todo = [a for a in data.values() if not a.get("own") and (every or not a.get("exported"))]  # yours stay yours
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


def own_login(host):
    """Your own login (imported) for this host, or None: {"site", "email", "company"} (never the password)."""
    try:
        with open(PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    a = find(data, host)
    return {k: a.get(k) for k in ("site", "email", "company")} if a and a.get("own") else None


def bitwarden_logins(data, only):
    """[(host, {"name", "username", "password", "uri"})] from a Bitwarden export ({"items": [...]}) or `bw list items`
    ([...]): login items with a username and a password, whose address contains one of the only texts (any case),
    never one of NEVER's sign-ins. Pure."""
    if isinstance(data, dict) and data.get("encrypted"):
        raise ValueError("that export is encrypted: export it as plain .json (or use `bw list items`)")
    items = data.get("items", []) if isinstance(data, dict) else data if isinstance(data, list) else []
    only = [o.lower() for o in only if o.strip()]
    out = []
    for it in items:
        login = (it or {}).get("login") or {}
        user, pw = login.get("username") or "", login.get("password") or ""
        if (it or {}).get("type", 1) != 1 or not user or not pw:
            continue
        for u in login.get("uris") or []:
            uri = str((u or {}).get("uri") or "").strip()
            h = site(uri if "://" in uri else "https://" + uri)
            if h and any(o in uri.lower() for o in only) and not never(h):
                out.append((h, {"name": it.get("name") or h, "username": user, "password": pw, "uri": uri}))
    return out


def import_own(entries):
    """Save your own logins: [(host, {"name", "username", "password", "uri"})]. Your login replaces one NUauto made
    on that site (kept as "replaced_nuauto", in case it is the right one after all). Returns the sites saved."""
    saved, seen = [], {}
    for h, e in entries:  # two of yours for one site: the last one counts (said)
        if h in seen and seen[h]["password"] != e["password"]:
            print(f"(two of your logins are for {h}: kept {e['name']!r}; give the other its job's full address in "
                  "Bitwarden if it is another company's)")
        seen[h] = e
    entries = list(seen.items())
    with locked() as data:
        for h, e in entries:
            old = data.get(h)
            a = {"site": h, "url": e["uri"] if "://" in e["uri"] else site_url(h), "email": e["username"],
                 "password": e["password"], "company": e["name"], "own": True,
                 "match": "host" if domain(h) == h else "domain", "created": datetime.now().isoformat(timespec="minutes"),
                 "jobs": (old or {}).get("jobs", []), "exported": True}
            if old and not old.get("own") and old.get("password") != e["password"]:
                a["replaced_nuauto"] = {k: old.get(k) for k in ("email", "password", "created")}
            data[h] = a
            saved.append(h)
    return saved


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
                  f"{len(a.get('jobs', []))} job(s)  " + ("yours (imported)" if a.get("own") else
                                                          f"exported {a['exported']}" if a.get("exported") else "not exported"))
        return
    if argv[0] == "import" and len(argv) >= 2:
        rest, only = argv[1:], []
        while "--only" in rest:
            i = rest.index("--only")
            if i + 1 >= len(rest):
                sys.exit("--only needs a text (e.g. --only myworkday).")
            only.append(rest[i + 1])
            del rest[i:i + 2]
        if len(rest) != 1 or not only:
            sys.exit("Say which logins: nuauto accounts import <file>|- --only <site> [--only <site>...]\n"
                     "(only logins whose address contains one of those texts are taken, e.g. --only myworkday)")
        src = rest[0]
        try:
            data = json.load(sys.stdin if src == "-" else open(os.path.expanduser(src)))
            entries = bitwarden_logins(data, only)
        except (OSError, ValueError) as e:
            sys.exit(f"Could not read that: {e}")
        if not entries:
            return print("No logins matched (nothing saved).")
        for h in import_own(entries):
            a = next(e for x, e in entries if x == h)
            print(f"  {h:45} {a['name'][:30]:30} as {a['username']}")
        print(f"Saved {len(entries)} of your own login{'s' if len(entries) != 1 else ''}: the assistant signs in with "
              "them and never makes an account on those sites." + (f"\nNow delete the export file: rm {src}"
                                                                  if src != "-" else ""))
        return
    if argv[:2] == ["export", "--bitwarden"] and len(argv) == 2:
        from nuauto import sync
        laptop = config.REMOTE_ASSIST and not config.IS_SERVER
        if laptop:
            sync.share()  # the logins the homelab's assistant made too
        try:
            added, skipped = to_bitwarden()
        except BitwardenError as e:
            sys.exit(str(e))
        if laptop:
            sync.share()  # the homelab's copy learns they are in Bitwarden now
        for key in added:
            print(f"  added   {key}")
        for key in skipped:
            print(f"  already there  {key}")
        return print(f"{len(added)} login{'s' if len(added) != 1 else ''} added to Bitwarden (folder {FOLDER!r})"
                     + (f", {len(skipped)} were there already" if skipped else "") + ". No file was written.")
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
