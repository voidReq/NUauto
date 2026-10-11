"""Read-only Gmail for the company-site agent (assist.py `email`): the code or link a job site just emailed you,
nothing else.

  nuauto login gmail      log in once (Google's read-only Gmail permission); `nuauto login google` renews it too

The agent never sees your inbox. Code reads the mail that arrived in the last WINDOW seconds, keeps the messages that
name the site the agent is on (its company, or a link to its own host), and hands back one code, or one link to that
same site. Nothing else from any message reaches the agent or the logs. Never for Google / Microsoft / Apple /
LinkedIn / GitHub / Northeastern sign-ins (accounts.never).
The login lives in local/gmail_token.json (mode 600, never synced, never shown), apart from the Sheets login: that one
can't read mail.
"""
import base64
import html
import json
import os
import re
import time
from datetime import date, datetime
from urllib.parse import urlparse

from nuauto import accounts
from nuauto import config

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
TOKEN = os.path.join(config.LOCAL_DIR, "gmail_token.json")
LOGIN_DATE = os.path.join(config.LOCAL_DIR, "gmail_login.txt")  # when the 7-day login was made
API = "https://gmail.googleapis.com/gmail/v1/users/me"
WINDOW = 15 * 60  # mail older than this is never used
WAIT = 75         # how long `email` waits for the mail to arrive (the Bash tool gives a command 2 minutes)
POLL = 8
# Gmail search: the words a verification email has (the rest is checked here, on each message)
QUERY = '(code OR passcode OR verify OR verification OR confirm OR activate OR "one-time" OR otp OR security OR pin)'
CODE_WORDS = re.compile(r"\b(codes?|passcode|pass code|one[- ]time|otp|pin)\b", re.I)
VERIFY_WORDS = re.compile(r"verif|confirm|activat|validat|\bcodes?\b|passcode|one[- ]time", re.I)
CODE_AFTER = re.compile(r"\b(?:codes?|passcode|pin|otp)\b(?:\s+(?:is|was))?\s*[:\-]?\s*([A-Za-z0-9]{4,10})\b", re.I)
DIGITS = re.compile(r"(?<![\w.,/-])(\d{4,8})(?![\w.,/-])")
MIXED = re.compile(r"(?<![\w.,/-])((?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{6,10})(?![\w.,/-])")
YEAR = re.compile(r"^(19|20)\d\d$")
LINK_WORDS = re.compile(r"verif|confirm|activat|validat|registr|set.?password|reset|sign.?in|log.?in|magic|token", re.I)
SKIP_LINK = re.compile(r"unsubscrib|privacy|terms|cookie|help|support|preferenc|opt.?out|\.(png|jpe?g|gif|css)\b", re.I)
HREF = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.I)
URL = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
TAG = re.compile(r"<[^>]+>")
SUFFIXES = re.compile(r"[,.]?\s+(inc|llc|ltd|co|corp|corporation|company|group|plc|lp|holdings)\.?$", re.I)


class NoAccess(Exception):
    """No Gmail login (never made, or expired): the agent asks the user instead."""


# ---------- reading one message (pure, offline-tested) ----------

def _decode(data):
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")


def parts(payload):
    """(plain text, html) of a Gmail API message payload (format=full), every part joined."""
    plain, rich = [], []
    stack = [payload or {}]
    while stack:
        p = stack.pop()
        stack.extend(p.get("parts") or [])
        data = (p.get("body") or {}).get("data")
        if data:
            (rich if p.get("mimeType") == "text/html" else plain if p.get("mimeType", "").startswith("text/") else []).append(_decode(data))
    return "\n".join(plain), "\n".join(rich)


def header(payload, name):
    return next((h.get("value", "") for h in (payload or {}).get("headers") or [] if h.get("name", "").lower() == name), "")


def text_of(plain, rich):
    """The message's readable text: the plain part, else the HTML without its tags."""
    if plain.strip():
        return plain
    return html.unescape(TAG.sub(" ", re.sub(r"(?is)<(script|style).*?</\1>", " ", rich)))


def links(plain, rich):
    found = [html.unescape(u) for u in HREF.findall(rich)] + URL.findall(plain) + URL.findall(TAG.sub(" ", rich))
    out = []
    for u in found:
        u = u.strip().rstrip(".,;")
        if u.lower().startswith(("http://", "https://")) and u not in out:
            out.append(u)
    return out


def site_of(h):
    """The registrable part of a host, roughly: acme.wd5.myworkdayjobs.com -> myworkdayjobs.com."""
    labels = (h or "").lower().split(".")
    return ".".join(labels[-2:]) if len(labels) >= 2 else (h or "").lower()


def company_names(company):
    """How a message may name the company: 'Acme Robotics, Inc.' -> ['acme robotics', 'acme']."""
    full = SUFFIXES.sub("", (company or "").strip()).lower()
    names = [full] if len(full) >= 3 else []
    first = re.split(r"[\s,&/|-]+", full)[0] if full else ""
    if len(first) >= 3 and first not in names and first not in ("the", "university"):
        names.append(first)
    return names


def about_site(msg, site_host, company):
    """Whether a message is about this site: it names the company, or links to the very host the agent is on."""
    words = f"{msg['from']} {msg['subject']} {msg['text']}".lower()
    if any(re.search(rf"\b{re.escape(n)}\b", words) for n in company_names(company)):
        return True
    return any(urlparse(u).hostname == site_host for u in msg["links"])


def find_code(text):
    """The one code in a verification message, or None (none, or more than one: the user then reads it)."""
    if not CODE_WORDS.search(text):
        return None
    explicit = {m.group(1) for m in CODE_AFTER.finditer(text)
                if not YEAR.match(m.group(1)) and (m.group(1).isdigit() or MIXED.fullmatch(m.group(1)))}
    if len(explicit) == 1:
        return explicit.pop()
    lines = [ln.strip() for ln in text.splitlines()]
    alone = {ln for ln in lines if (DIGITS.fullmatch(ln) or MIXED.fullmatch(ln)) and not YEAR.match(ln)}
    if len(alone) == 1:
        return alone.pop()
    near = set()  # a code within two lines after a line that says "code"
    for k, ln in enumerate(lines):
        if CODE_WORDS.search(ln):
            for nxt in lines[k:k + 3]:
                near |= {c for c in DIGITS.findall(nxt) + MIXED.findall(nxt) if not YEAR.match(c)}
    return near.pop() if len(near) == 1 else None


def find_link(urls, site_host, job_host=""):
    """The one verification link to this site (its host, or the same site as it or the job's): or None."""
    hosts = {h for h in (site_host, job_host) if h}
    sites = {site_of(h) for h in hosts}
    ours = [u for u in urls if (urlparse(u).hostname or "").lower() in hosts | sites
            or site_of(urlparse(u).hostname) in sites]
    ours = [u for u in ours if not SKIP_LINK.search(u)]
    best = [u for u in ours if LINK_WORDS.search(u)] or (ours if len(ours) == 1 else [])
    return best[0] if len(set(best)) == 1 else None


def pick(messages, site_host, company, job_host="", want="", used=(), now=None):
    """From the messages (newest first): the code or link the site just sent, as {"status": "code" | "link", ...}; or
    {"status": "none"} / {"status": "ambiguous"}. Only messages from the last WINDOW seconds that are about this site
    and not used before in this run."""
    now = now or time.time()
    fresh = [m for m in messages if now - m["time"] <= WINDOW and m["id"] not in used and about_site(m, site_host, company)]
    for m in sorted(fresh, key=lambda m: -m["time"]):
        code = None if want == "link" else find_code(m["text"])
        link = None if want == "code" else find_link(m["links"], site_host, job_host)
        sender = re.sub(r"\s*<.*", "", m["from"]).strip().strip('"') or m["from"]
        when = datetime.fromtimestamp(m["time"]).strftime("%H:%M")
        if code:  # want "code", or no want: a code first (the page asks for it), else the link
            return {"status": "code", "code": code, "from": sender, "received": when, "id": m["id"]}
        if link:
            return {"status": "link", "link": link, "from": sender, "received": when, "id": m["id"]}
        if VERIFY_WORDS.search(f"{m['subject']} {m['text']}"):  # it is the email, but no single code / link in it
            return {"status": "ambiguous", "from": sender, "received": when, "id": m["id"]}
    return {"status": "none"}


# ---------- Gmail (read-only) ----------

def ready():
    return os.path.exists(TOKEN)


def session():
    """An authorized requests session for the Gmail API. NoAccess when there is no login or it expired."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import AuthorizedSession, Request
    from google.oauth2.credentials import Credentials
    if not ready():
        raise NoAccess("Gmail access is not set up (nuauto login gmail)")
    creds = Credentials.from_authorized_user_file(TOKEN, SCOPES)
    if not creds.valid:
        try:
            creds.refresh(Request())
        except RefreshError:
            raise NoAccess("the Gmail login expired (it lasts 7 days): nuauto login gmail") from None
        _save(creds)
    return AuthorizedSession(creds)


def _save(creds):
    fd = os.open(TOKEN, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(creds.to_json())
    os.chmod(TOKEN, 0o600)


def recent(s, now=None, limit=15):
    """The messages of the last WINDOW seconds that look like verification mail, newest first, as
    {"id", "time", "from", "subject", "text", "links"} (never shown to the agent: pick() takes from them)."""
    after = int((now or time.time()) - WINDOW)
    q = f"after:{after} {QUERY} in:anywhere -in:trash -in:sent -from:me"  # spam too: verification mail lands there
    r = s.get(f"{API}/messages", params={"q": q, "maxResults": limit},
              timeout=20)
    r.raise_for_status()
    out = []
    for ref in r.json().get("messages") or []:
        m = s.get(f"{API}/messages/{ref['id']}", params={"format": "full"}, timeout=20)
        m.raise_for_status()
        msg = m.json()
        payload = msg.get("payload") or {}
        plain, rich = parts(payload)
        out.append({"id": msg["id"], "time": int(msg.get("internalDate", "0")) / 1000, "from": header(payload, "from"),
                    "subject": header(payload, "subject"), "text": text_of(plain, rich), "links": links(plain, rich)})
    return sorted(out, key=lambda m: -m["time"])


def lookup(site_host, company, job_host="", want="", used=(), wait=WAIT, s=None, sleep=time.sleep):
    """What the site emailed: pick() over the recent mail, checking again every POLL seconds for up to `wait` seconds
    (the email may take a moment). Refused for identity providers and Northeastern."""
    why = accounts.never(site_host)
    if why:
        return {"status": "refused", "why": why}
    s = s or session()
    deadline = time.time() + wait
    while True:
        found = pick(recent(s), site_host, company, job_host, want, used)
        if found["status"] != "none" or time.time() >= deadline:
            return found
        sleep(POLL)


def login():
    """Google's read-only Gmail permission, with your own OAuth client (the one the Sheets login uses): opens the
    browser. Lasts 7 days while the Google Cloud app is in Testing mode."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(config.find_client_json(), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)
    os.makedirs(config.LOCAL_DIR, mode=0o700, exist_ok=True)
    _save(creds)
    with open(LOGIN_DATE, "w") as f:
        f.write(date.today().isoformat() + "\n")
    print("Gmail login done (read-only). The assistant can now read the codes and links job sites email you.")


def days_left():
    try:
        with open(LOGIN_DATE) as f:
            return 7 - (date.today() - date.fromisoformat(f.read().strip())).days
    except (OSError, ValueError):
        return None


def main(argv):
    if argv == ["login"]:
        return login()
    if argv == ["check"]:  # is the login there and working? (reads nothing from any message)
        try:
            s = session()
            r = s.get(f"{API}/profile", timeout=20)
            r.raise_for_status()
            return print(json.dumps({"ok": True, "days_left": days_left()}))
        except NoAccess as e:
            return print(json.dumps({"ok": False, "why": str(e)}))
    raise SystemExit(__doc__)


if __name__ == "__main__":
    import sys
    main(sys.argv[1:])
