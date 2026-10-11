"""Read a job posting from the company's own job site, without logging in (for the internship pool, intern.py).

In order: the job site's public job data where it has one (Workday, Greenhouse, Lever, Ashby, SmartRecruiters,
Oracle, Eightfold, Workable, iCIMS), else the schema.org JobPosting data most careers pages carry for search engines,
else the page's own text, else the page opened in a hidden browser (pages built by JavaScript: TikTok, ByteDance...).
Read-only: plain GET requests without cookies; the hidden browser only loads the page (nothing typed or clicked, no
downloads, no images). At most one request a second to any one site, and never to an address inside your own network
(a listing can link anywhere; checked again after every redirect).

  python -m nuauto.postings <url> [--browser]    what it reads from one posting: how, how much, the first lines
"""
import html
import http.client
import ipaddress
import json
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlparse

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"
MIN_TEXT = 300          # characters: anything shorter is an error page or a cookie banner, not a posting
PAGE_TEXT = 1500        # a page's own text counts only when it is this long and reads like a posting (JOB_WORDS)
MAX_BYTES = 8_000_000   # per response
SPACING = 1.0           # seconds between two requests to the same site
TIMEOUT = 25
ALLOW_PRIVATE = False   # tests serve pages from 127.0.0.1; never set in real runs
JOB_WORDS = re.compile(r"responsibilit|qualification|requirement|what you('ll| will)|you will|about the role|"
                       r"minimum|preferred|experience with|internship", re.I)
CLOSED = re.compile(r"no longer (available|accepting|open|active|posted)|"
                    r"(position|job|posting|requisition|opening) (has been|is|was) (filled|closed|removed|expired)|"
                    r"this (job|position|posting|requisition|opening) (has )?(expired|closed|been (removed|filled))|"
                    r"\bjob (not found|closed|expired)\b|page (you('re| are) looking for|not found)", re.I)


class Unreadable(Exception):
    """Nothing usable came back (the reasons, one per way tried)."""


# ---------------------------------------------------------------- text

def text_of(s):
    """HTML (or plain text) -> text with line breaks: paragraphs and headers on their own lines, list items as "- "."""
    s = s or ""
    if "&lt;" in s and "<" not in s:  # Greenhouse sends its HTML escaped
        s = html.unescape(s)
    s = re.sub(r"(?is)<(script|style|noscript|svg|head)\b.*?</\1\s*>", " ", s)
    s = re.sub(r"(?i)<li\b[^>]*>", "\n- ", s)
    s = re.sub(r"(?i)</?(br|p|div|h[1-6]|tr|ul|ol|li|section|article|table|header|footer)\b[^>]*>", "\n", s)
    s = html.unescape(re.sub(r"<[^>]+>", " ", s)).replace("\xa0", " ").replace("​", "")
    lines = (re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in s.split("\n"))
    return "\n".join(line for line in lines if line and line != "-")


def looks_closed(text):
    """A short page that says the job is gone ("no longer accepting applications"...). A long posting that only
    mentions such words is not."""
    return len(text) < 2500 and bool(CLOSED.search(text))


def pay_from_text(text):
    """The first hourly (or yearly) dollar pay range in a posting, as "$40-$55 per hour", or ""."""
    m = re.search(r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)\s*(k\b)?(?:\s*(?:-|–|—|to)\s*\$?\s?(\d[\d,]*(?:\.\d{1,2})?)\s*(k\b)?)?"
                  r"\s*(?:USD\s*)?(?:/|per|an|a)\s*(hour|hr|year|yr)\b", text or "", re.I)
    if not m:
        return ""
    lo, hi = m.group(1) + (m.group(2) or ""), (m.group(3) + (m.group(4) or "")) if m.group(3) else ""
    unit = "hour" if m.group(5).lower() in ("hour", "hr") else "year"
    return f"${lo}-${hi} per {unit}" if hi else f"${lo} per {unit}"


# ---------------------------------------------------------------- schema.org JobPosting

LDJSON = re.compile(r"(?is)<script[^>]*application/ld\+json[^>]*>(.*?)</script>")


def _walk(obj):
    if isinstance(obj, list):
        for x in obj:
            yield from _walk(x)
    elif isinstance(obj, dict):
        yield obj
        if isinstance(obj.get("@graph"), list):
            yield from _walk(obj["@graph"])


def jsonld(page):
    """The page's schema.org JobPosting (a dict), or None."""
    for m in LDJSON.finditer(page or ""):
        try:
            obj = json.loads(m.group(1).strip(), strict=False)
        except ValueError:
            continue
        for o in _walk(obj):
            kinds = o.get("@type")
            if "JobPosting" in (kinds if isinstance(kinds, list) else [kinds]):
                return o
    return None


def _ld_pay(o):
    sal = o.get("baseSalary") or {}
    if not isinstance(sal, dict):
        return ""
    v = sal.get("value") if isinstance(sal.get("value"), dict) else sal
    lo, hi, unit = v.get("minValue") or v.get("value"), v.get("maxValue"), str(v.get("unitText") or "").lower()
    if lo in (None, "") or unit not in ("hour", "year"):
        return ""
    fmt = lambda x: f"{float(x):,.2f}".rstrip("0").rstrip(".")  # noqa: E731
    return f"${fmt(lo)}-${fmt(hi)} per {unit}" if hi not in (None, "", lo) else f"${fmt(lo)} per {unit}"


def _ld_place(o):
    places = o.get("jobLocation")
    out = []
    for p in places if isinstance(places, list) else [places]:
        a = (p or {}).get("address") if isinstance(p, dict) else None
        if isinstance(a, dict):
            out.append(", ".join(x for x in (a.get("addressLocality"), a.get("addressRegion")) if isinstance(x, str) and x))
    return "; ".join(x for x in out if x)


def from_jsonld(o):
    desc = text_of(o.get("description"))
    end = str(o.get("validThrough") or "")[:10]
    return {"title": str(o.get("title") or ""), "description": desc, "location": _ld_place(o),
            "pay": _ld_pay(o) or pay_from_text(desc), "deadline": end if re.fullmatch(r"\d{4}-\d\d-\d\d", end) else None}


# ---------------------------------------------------------------- the job sites' own data (pure: offline-tested)

def kind(url):
    h = (urlparse(url).hostname or "").lower()
    for k, mark in (("workday", "myworkdayjobs.com"), ("workday", "myworkdaysite.com"), ("greenhouse", "greenhouse.io"),
                    ("lever", "lever.co"), ("ashby", "ashbyhq.com"), ("smartrecruiters", "smartrecruiters.com"),
                    ("oracle", "oraclecloud.com"), ("eightfold", "eightfold.ai"), ("workable", "workable.com"),
                    ("icims", "icims.com")):
        if h == mark or h.endswith("." + mark):
            return k
    return "other"


def api_request(url):
    """(kind, the job site's own data URL for this posting) or None when there is none (or the link's shape is new)."""
    u = urlparse(url)
    h = (u.hostname or "").lower()
    seg = [s for s in u.path.split("/") if s]
    q = parse_qs(u.query)
    k = kind(url)
    try:
        if k == "workday" and "job" in seg:
            i = seg.index("job")
            if h.endswith("myworkdaysite.com") and seg[:1] == ["recruiting"]:  # wd3.myworkdaysite.com/recruiting/<t>/<site>/job/...
                tenant, site = seg[1], seg[2]
            else:  # <tenant>.wd5.myworkdayjobs.com/[en-US/]<site>/job/...
                tenant, site = h.split(".")[0], seg[i - 1]
            rest = [s for s in seg[i + 1:] if s not in ("apply", "applyManually", "autofillWithResume")]
            if rest and not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", site):
                return k, f"https://{h}/wday/cxs/{tenant}/{site}/job/{'/'.join(rest)}"
        if k == "greenhouse":
            api = "boards-api.eu.greenhouse.io" if ".eu." in h else "boards-api.greenhouse.io"
            if "for" in q and "token" in q:  # .../embed/job_app?for=<board>&token=<id>
                return k, f"https://{api}/v1/boards/{q['for'][0]}/jobs/{q['token'][0]}"
            if "jobs" in seg and len(seg) > seg.index("jobs") + 1 and seg[0] not in ("embed", "jobs"):
                jid = seg[seg.index("jobs") + 1]
                if jid.isdigit():
                    return k, f"https://{api}/v1/boards/{seg[0]}/jobs/{jid}"
        if k == "lever" and len(seg) >= 2:
            api = "api.eu.lever.co" if ".eu." in h else "api.lever.co"
            return k, f"https://{api}/v0/postings/{seg[0]}/{seg[1]}"
        if k == "ashby" and len(seg) >= 2:  # the board's list (one request per company per run): the posting is in it
            return k, f"https://api.ashbyhq.com/posting-api/job-board/{seg[0]}?includeCompensation=true"
        if k == "smartrecruiters" and len(seg) >= 2:
            jid = seg[1].split("-")[0]
            if jid.isdigit():
                return k, f"https://api.smartrecruiters.com/v1/companies/{seg[0]}/postings/{jid}"
        if k == "oracle" and "sites" in seg and "job" in seg:
            site, jid = seg[seg.index("sites") + 1], seg[seg.index("job") + 1]
            if jid.isdigit():
                return k, (f"https://{h}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?expand=all"
                           f"&onlyData=true&finder=ById;Id=%22{jid}%22,siteNumber={site}")
        if k == "eightfold":
            pid = (q.get("pid") or [seg[-1] if seg[-2:-1] == ["job"] else ""])[0]
            if pid.isdigit():
                return k, f"https://{h}/api/apply/v2/jobs/{pid}"
        if k == "workable" and "j" in seg:
            return k, f"https://apply.workable.com/api/v2/accounts/{seg[0]}/jobs/{seg[seg.index('j') + 1]}"
        if k == "icims" and "jobs" in seg:
            jid = seg[seg.index("jobs") + 1]
            if jid.isdigit():
                return k, f"https://{h}/jobs/{jid}/job?in_iframe=1"
    except (IndexError, ValueError):
        return None
    return None


def parse_api(k, data, url):
    """The posting from a job site's own data (what api_request's URL returned: JSON, or iCIMS's page). None if it
    is not in there."""
    if k == "icims":
        o = jsonld(data)
        if o:
            return from_jsonld(o)
        body = re.split(r"iCIMS_JobContent|iCIMS_InfoMsg_Job|iCIMS_JobHeaderGroup", data, maxsplit=1)
        desc = text_of(body[1] if len(body) > 1 else "")
        return {"title": "", "description": desc, "location": "", "pay": pay_from_text(desc), "deadline": None}
    if not isinstance(data, dict):
        return None
    if k == "workday":
        info = data.get("jobPostingInfo") or {}
        desc = text_of(info.get("jobDescription"))
        place = "; ".join([info.get("location") or ""] + list(info.get("additionalLocations") or []))
        end = str(info.get("endDate") or "")[:10]
        return {"title": info.get("title") or "", "description": desc, "location": place.strip("; "),
                "pay": pay_from_text(desc), "deadline": end if re.fullmatch(r"\d{4}-\d\d-\d\d", end) else None,
                "closed": info.get("canApply") is False}
    if k == "greenhouse":
        desc = text_of(data.get("content"))
        return {"title": data.get("title") or "", "description": desc, "location": (data.get("location") or {}).get("name") or "",
                "pay": pay_from_text(desc), "deadline": None}
    if k == "lever":
        parts = [data.get("description") or data.get("descriptionPlain") or ""]
        for block in data.get("lists") or []:
            parts += [f"<h3>{block.get('text') or ''}</h3>", block.get("content") or ""]
        parts.append(data.get("additional") or "")
        desc = text_of("\n".join(parts))
        sal = data.get("salaryRange") or {}
        pay = ""
        if sal.get("min") and str(sal.get("interval", "")).lower().startswith("per-hour"):
            pay = f"${sal['min']}-${sal.get('max') or sal['min']} per hour"
        return {"title": data.get("text") or "", "description": desc,
                "location": (data.get("categories") or {}).get("location") or "", "pay": pay or pay_from_text(desc),
                "deadline": None}
    if k == "ashby":
        jid = [s for s in urlparse(url).path.split("/") if s][1]
        job = next((j for j in data.get("jobs") or [] if j.get("id") == jid), None)
        if job is None:
            return None
        desc = text_of(job.get("descriptionHtml") or job.get("descriptionPlain"))
        comp = (job.get("compensation") or {}).get("compensationTierSummary") or ""
        return {"title": job.get("title") or "", "description": desc, "location": job.get("location") or "",
                "pay": pay_from_text(comp) or pay_from_text(desc), "deadline": None}
    if k == "smartrecruiters":
        sections = ((data.get("jobAd") or {}).get("sections") or {})
        parts = []
        for key in ("jobDescription", "qualifications", "additionalInformation", "companyDescription"):
            s = sections.get(key) or {}
            if s.get("text"):
                parts += [f"<h3>{s.get('title') or ''}</h3>", s["text"]]
        desc = text_of("\n".join(parts))
        loc = data.get("location") or {}
        return {"title": data.get("name") or "", "description": desc,
                "location": ", ".join(x for x in (loc.get("city"), loc.get("region")) if x), "pay": pay_from_text(desc),
                "deadline": None, "closed": data.get("active") is False}
    if k == "oracle":
        items = data.get("items") or []
        if not items:
            return None
        it = items[0]
        parts = []
        for key, title in (("ExternalDescriptionStr", ""), ("ExternalResponsibilitiesStr", "Responsibilities"),
                           ("ExternalQualificationsStr", "Qualifications")):
            if it.get(key):
                parts += [f"<h3>{title}</h3>" if title else "", it[key]]
        desc = text_of("\n".join(parts))
        return {"title": it.get("Title") or "", "description": desc, "location": it.get("PrimaryLocation") or "",
                "pay": pay_from_text(desc), "deadline": None}
    if k == "eightfold":
        desc = text_of(data.get("job_description"))
        return {"title": data.get("name") or "", "description": desc, "location": data.get("location") or "",
                "pay": pay_from_text(desc), "deadline": None}
    if k == "workable":
        desc = text_of("\n".join(data.get(x) or "" for x in ("description", "requirements", "benefits")))
        loc = data.get("location") or {}
        return {"title": data.get("title") or "", "description": desc,
                "location": ", ".join(x for x in (loc.get("city"), loc.get("region")) if x) if isinstance(loc, dict) else "",
                "pay": pay_from_text(desc), "deadline": None}
    return None


GH_BOARD = re.compile(r"greenhouse\.io/(?:embed/job_board(?:/js)?\?for=|embed/job_app\?for=|v1/boards/)([A-Za-z0-9_-]+)")


def greenhouse_hosted(url, page):
    """A careers site that shows a Greenhouse posting (?gh_jid=<id>): (board, id) when its page names the board."""
    jid = (parse_qs(urlparse(url).query).get("gh_jid") or [""])[0]
    m = GH_BOARD.search(page or "")
    return (m.group(1), jid) if jid.isdigit() and m else None


def from_page(page, url):
    """The posting from a page's HTML: its JobPosting data, or the page's own text when it is long enough and reads
    like a posting. None if neither."""
    o = jsonld(page)
    if o:
        got = from_jsonld(o)
        if len(got["description"]) >= MIN_TEXT:
            return got
    text = text_of(page)
    if (len(text) >= PAGE_TEXT and len(JOB_WORDS.findall(text)) >= 2) or looks_closed(text):
        return {"title": "", "description": text, "location": "", "pay": pay_from_text(text), "deadline": None}
    return None


# ---------------------------------------------------------------- fetching

_PUBLIC = {}  # host -> whether every address it resolves to is public (one lookup per host per process)


def allowed_host(host):
    """Only hosts on the public internet (never this machine, your network, Tailscale or link-local addresses)."""
    if not host:
        return False
    if ALLOW_PRIVATE:
        return True
    if host not in _PUBLIC:
        try:
            ips = {a[4][0] for a in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)}
            _PUBLIC[host] = bool(ips) and all(ipaddress.ip_address(ip.split("%")[0]).is_global for ip in ips)
        except (OSError, ValueError):
            _PUBLIC[host] = False
    return _PUBLIC[host]


def check_url(url):
    u = urlparse(url)
    if u.scheme not in ("http", "https"):
        raise Unreadable(f"not a web link ({u.scheme or 'no scheme'})")
    if not allowed_host(u.hostname):
        raise Unreadable(f"{u.hostname or 'no host'} is not a public address")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Client:
    """Plain GET requests: a browser's User-Agent, no cookies, at most one request per SPACING seconds to a site."""

    def __init__(self, spacing=SPACING):
        self.spacing, self.last, self.cache = spacing, {}, {}
        self.opener = urllib.request.build_opener(_SafeRedirect)

    def get(self, url, accept):
        check_url(url)
        h = urlparse(url).hostname
        wait = self.last.get(h, 0) + self.spacing - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept, "Accept-Language": "en-US,en;q=0.8"})
        try:
            with self.opener.open(req, timeout=TIMEOUT) as r:
                charset = r.headers.get_content_charset() or "utf-8"
                try:
                    body = r.read(MAX_BYTES + 1)
                except http.client.IncompleteRead as e:  # the server closed early: what came is usually the page
                    body = e.partial
        finally:
            self.last[h] = time.monotonic()
        if len(body) > MAX_BYTES:
            raise Unreadable("the page is too big")
        return body.decode(charset, "replace")

    def json(self, url):
        if url not in self.cache:  # Ashby: one board list serves every posting of that company
            self.cache[url] = json.loads(self.get(url, "application/json"))
        return self.cache[url]

    def text(self, url):
        return self.get(url, "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8")


class Browser:
    """A hidden Firefox for pages built by JavaScript: fresh and empty (no profile, nothing kept), images, fonts and
    media not loaded, requests to non-public addresses blocked, downloads refused. Use as a context manager."""

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.firefox.launch(headless=True)
        self._ctx = self._browser.new_context(user_agent=UA, accept_downloads=False, locale="en-US")
        self._ctx.route("**/*", self._route)
        return self

    def __exit__(self, *exc):
        for close in (self._ctx.close, self._browser.close, self._pw.stop):
            try:
                close()
            except Exception:
                pass

    @staticmethod
    def _route(route):
        r = route.request
        u = urlparse(r.url)
        if r.resource_type in ("image", "media", "font") or (u.scheme in ("http", "https") and not allowed_host(u.hostname)):
            return route.abort()
        return route.continue_()

    def read(self, url):
        check_url(url)
        page = self._ctx.new_page()
        page.on("dialog", lambda d: d.dismiss())
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            try:
                page.wait_for_load_state("networkidle", timeout=12000)
            except Exception:
                pass
            o = jsonld(page.content())  # the rendered page's JobPosting data, when the script added it
            if o and len(from_jsonld(o)["description"]) >= MIN_TEXT:
                return from_jsonld(o)
            text = text_of(page.inner_text("body"))
            if (len(text) >= MIN_TEXT and JOB_WORDS.search(text)) or looks_closed(text):
                return {"title": "", "description": text, "location": "", "pay": pay_from_text(text), "deadline": None}
            return None
        finally:
            page.close()


def _why(e):
    if isinstance(e, urllib.error.HTTPError):
        return f"HTTP {e.code}"
    if isinstance(e, Unreadable):
        return str(e)
    return type(e).__name__


def result(got, how):
    """A posting as read() returns it: how it was read, and closed when the site or the page says so."""
    got = {"closed": False, **got, "how": how}
    got["closed"] = bool(got["closed"]) or looks_closed(got["description"])
    return got


def read_plain(url, client):
    """The posting by plain requests: the job site's own data, else the page (its JobPosting data or its text).
    Unreadable with one reason per way tried."""
    tried = []
    req = api_request(url)
    if req:
        try:
            got = parse_api(req[0], client.text(req[1]) if req[0] == "icims" else client.json(req[1]), url)
            if got and (len(got["description"]) >= MIN_TEXT or got.get("closed")):
                return result(got, req[0])
            tried.append(f"{req[0]}: no posting in its data")
        except Exception as e:
            tried.append(f"{req[0]}: {_why(e)}")
    try:
        page = client.text(url)
        gh = greenhouse_hosted(url, page)
        if gh:
            api = f"https://boards-api.greenhouse.io/v1/boards/{gh[0]}/jobs/{gh[1]}"
            got = parse_api("greenhouse", client.json(api), api)
            if got and len(got["description"]) >= MIN_TEXT:
                return result(got, "greenhouse")
        got = from_page(page, url)
        if got:
            return result(got, "page data" if jsonld(page) else "page text")
        tried.append("page: no posting text (built by JavaScript?)")
    except Exception as e:
        tried.append(f"page: {_why(e)}")
    raise Unreadable("; ".join(tried))


def read_browser(url, browser):
    """The posting from the page opened in the hidden browser (a Browser). Unreadable if it shows none."""
    try:
        got = browser.read(url)
    except Unreadable:
        raise
    except Exception as e:
        raise Unreadable(f"browser: {_why(e)}")
    if not got:
        raise Unreadable("browser: no posting text")
    return result(got, "browser")


def read(url, client, browser=None):
    """{"how", "title", "description", "location", "pay", "deadline", "closed"} for one posting, or Unreadable (with
    why, one reason per way tried). browser: a Browser for pages built by JavaScript (None: plain requests only)."""
    try:
        return read_plain(url, client)
    except Unreadable as e:
        if browser is None:
            raise
        try:
            return read_browser(url, browser)
        except Unreadable as e2:
            raise Unreadable(f"{e}; {e2}")


def main(argv):
    if not argv or argv[0].startswith("-"):
        sys.exit(__doc__)
    url, use_browser = argv[0], "--browser" in argv
    client = Client()
    try:
        if use_browser:
            with Browser() as b:
                got = read(url, client, b)
        else:
            got = read(url, client)
    except Unreadable as e:
        sys.exit(f"Could not read it: {e}")
    print(f"how: {got['how']} | {len(got['description'])} characters | pay: {got['pay'] or 'not listed'} | "
          f"closes: {got['deadline'] or 'not listed'}{' | LOOKS CLOSED' if got['closed'] else ''}")
    print("\n".join(got["description"].split("\n")[:15]))


if __name__ == "__main__":
    main(sys.argv[1:])
