"""Summer 2027 internships from public lists (SimplifyJobs' github.com/SimplifyJobs/Summer2027-Internships, and
vanshb03's github.com/vanshb03/Summer2027-Internships; a job on both is kept once): a second pool, built like the
NUworks co-op pool (jobs.py), for jobs that are not on NUworks. Approved ones go in the sheet's Other
jobs tab (no weekly or total cap); `nuauto assist other <row>` applies on the company's site.

  nuauto intern update     check the lists (each downloaded only when it changed; the same job on two lists is
                           kept once, from the list that had it first), then: rules -> Claude triage (titles:
                           only clearly unrelated roles are dropped; the rest get an order) -> each posting read from
                           the company's own job site (postings.py) -> Claude score (match % vs your resume, and who may
                           apply) -> Claude category -> the pool. Only new work, a limited amount per run (the first
                           runs work through the backlog). The homelab runs it every 2 hours (nuauto-intern.timer) and
                           after each NUworks update; on a laptop with a homelab this starts it there (--here: run it
                           on this machine instead).
  nuauto intern approve    the viewer (like `nuauto approve`): y = an Approved row in the Other jobs tab
  nuauto intern rate       ratings only: they teach the same taste model as the co-ops
  nuauto intern stats      how far each step is
  nuauto intern pool       print the pool
  nuauto intern fetch <url> [--browser]   read one posting now (nothing saved)

On when local_config.json has "internships" (an object; {} = the defaults in DEFAULTS). Nothing here applies to
anything. Data in data/intern/; ratings share data/ratings.json (internship ids start with "s").
"""
import fcntl
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from nuauto import config
from nuauto import jobs

# The lists, in this order (a job both have, seen the same day, is kept from the earlier one): key, name, id prefix.
# Both use the same listings.json format; vanshb03's has no term per listing, only a season ("Summer"): its year is the
# one in the list's address.
LISTS = (("simplify", "Simplify's list", "s"), ("vansh", "vanshb03's list", "sv"))
NAMES = {k: name for k, name, _ in LISTS}
PREFIXES = {k: prefix for k, _, prefix in LISTS}
DEFAULTS = {
    "term": "Summer 2027",  # as Simplify's list names it
    "lists": {  # each list's file ("" = not read)
        "simplify": "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json",
        "vansh": "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/dev/.github/scripts/listings.json",
    },
    "fetch_per_run": 300,   # postings read per run by plain requests (about a second each)
    "browser_per_run": 40,  # ...and by the hidden browser, for pages built by JavaScript (10-20 s each)
    "score_per_run": 120,   # postings Claude scores per run (20 per call, about 3 minutes each)
    "threshold": None,      # match % to enter the pool; None = the co-ops' bar (preferences "threshold")
}
_CFG = config.LOCAL.get("internships")
ENABLED = isinstance(_CFG, dict) or _CFG is True
SETTINGS = {**DEFAULTS, **(_CFG if isinstance(_CFG, dict) else {})}
TERM = SETTINGS["term"]
LIST_URLS = {**DEFAULTS["lists"], **({"simplify": SETTINGS["source"]} if SETTINGS.get("source") else {}),  # (old key)
             **(SETTINGS["lists"] if isinstance(SETTINGS["lists"], dict) else {})}
DIR = os.path.join(config.DATA_DIR, "intern")
FITS = ("high", "medium", "low")              # triage's guess from the title: only the order postings are read in
YEAR_REQS = ("ok", "one_year_up", "two_years_up", "grad")  # the scorer's "who may apply", compared with you
TRIES = 3            # runs that read a posting every way (browser too) before it shows as unreadable
STALE_DAYS = 45      # older postings are flagged (internships fill on a rolling basis)
TRIAGE_BATCH = 250
SHORT_HOSTS = ("go.vansh.nyc",)  # link shorteners a list gives instead of the posting's link (vanshb03's)
SHORT_SPACING = 0.2  # seconds between two short links asked


# ---------------------------------------------------------------- storage

def path(*parts):
    return os.path.join(DIR, *parts)


def load(name, default):
    try:
        with open(path(name)) as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save(name, data):
    os.makedirs(os.path.dirname(path(name)), exist_ok=True)
    tmp = path(name + ".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path(name))


def load_details():
    out = {}
    folder = path("details")
    for name in os.listdir(folder) if os.path.isdir(folder) else []:
        if name.endswith(".json"):
            with open(os.path.join(folder, name)) as f:
                d = json.load(f)
            out[d["id"]] = d
    return out


# ---------------------------------------------------------------- the lists (pure parts: offline-tested)

def listing_id(list_id, prefix="s"):
    """Our id for a listing: the list's prefix ("s" Simplify's, "sv" vanshb03's) + its id without dashes (letters and
    digits only, like NUworks ids). Simplify's ids are hex, so no "s" id ever starts with "sv"."""
    return prefix + re.sub(r"[^0-9a-z]", "", str(list_id).lower())


def list_of(i):
    """Which list a listing id is from (the longest prefix that fits)."""
    return max((k for k, p in PREFIXES.items() if str(i).startswith(p)), key=lambda k: len(PREFIXES[k]), default="simplify")


def list_name(i):
    return NAMES[list_of(i)]


def clean_url(url):
    """The posting link without tracking parameters (utm_*, ref=Simplify); everything else stays (gh_jid...)."""
    u = urlparse(str(url or "").strip())
    q = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True)
         if not k.lower().startswith("utm_") and not (k.lower() == "ref" and v.lower() == "simplify")]
    return urlunparse(u._replace(query=urlencode(q)))


def _day(ts):
    try:
        return date.fromtimestamp(int(ts)).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def slim(sim, key="simplify"):
    """One listing of a list's file, as data/intern/listings.json keeps it."""
    return {"id": listing_id(sim["id"], PREFIXES[key]), "list": key, "list_id": str(sim["id"]),
            "company": str(sim.get("company_name") or "").strip(),
            "title": str(sim.get("title") or "").strip(), "locations": [str(x) for x in sim.get("locations") or []],
            "url": clean_url(sim.get("url")), "category": str(sim.get("category") or ""),
            "degrees": [str(x) for x in sim.get("degrees") or []], "sponsorship": str(sim.get("sponsorship") or ""),
            "posted": _day(sim.get("date_posted")), "updated": _day(sim.get("date_updated")),
            "active": bool(sim.get("active")), "terms": [str(x) for x in sim.get("terms") or []]}


def in_term(sim, year=None):
    """A listing for TERM: in its terms (Simplify's), or, on a list with no terms (vanshb03's), its season ("Summer",
    "Spring/Summer") on a list for TERM's year (year: the one in the list's address)."""
    if "terms" in sim:
        return TERM in (sim.get("terms") or [])
    season, term_year = TERM.split()[0].lower(), TERM.split()[-1]
    return year == term_year and season in [s.strip().lower() for s in str(sim.get("season") or "").split("/")]


def merge(listings, data, today=None, key="simplify", year=None):
    """One list's file (a list) into our listings: ones for TERM that the list shows. A listing that its list closes or
    drops stays, marked inactive. Returns (listings, ids new and open, ids closed now)."""
    today = (today or date.today()).isoformat()
    out, new, closed, seen = dict(listings), [], [], set()
    for sim in data:
        if not isinstance(sim, dict) or not in_term(sim, year) or not sim.get("is_visible", True) \
                or not sim.get("id") or not str(sim.get("url") or "").startswith(("https://", "http://")):
            continue
        x = slim(sim, key)
        old = out.get(x["id"])
        seen.add(x["id"])
        if old and old.get("list_url") == x["url"]:  # the same short link: where it leads is known (real_links)
            x.update(url=old["url"], list_url=old["list_url"])
        if old is None:
            if x["active"]:  # (the first download also has every listing Simplify already closed)
                new.append(x["id"])
        elif old.get("active") and not x["active"]:
            closed.append(x["id"])
        out[x["id"]] = {**x, "first_seen": (old or {}).get("first_seen", today)}
        if (old or {}).get("dup_of"):
            out[x["id"]]["dup_of"] = old["dup_of"]
    for i, x in out.items():
        if i not in seen and x.get("active") and x.get("list", "simplify") == key:  # no longer in the file at all
            out[i] = {**x, "active": False, "gone": today}
            closed.append(i)
    return out, new, closed


# ---------------------------------------------------------------- short links (vanshb03's go.vansh.nyc/...)

def short(url):
    return (urlparse(str(url or "")).hostname or "").lower() in SHORT_HOSTS


class _Stay(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None  # the redirect is the answer: the posting itself is not opened


def unshorten(url, hops=3):
    """Where a short link leads (its redirect; one to another short link is followed), without opening the posting.
    ValueError (or postings.Unreadable: not a public address) if it doesn't lead to a web link."""
    from nuauto import postings
    opener = urllib.request.build_opener(_Stay)
    for _ in range(hops):
        postings.check_url(url)
        try:
            with opener.open(urllib.request.Request(url, headers={"User-Agent": "NUauto (internship list check)"}),
                             timeout=30) as r:
                raise ValueError(f"no redirect (HTTP {r.status})")
        except urllib.error.HTTPError as e:
            e.close()
            if e.code not in (301, 302, 303, 307, 308) or not e.headers.get("Location"):
                raise ValueError(f"HTTP {e.code}")
            url = urljoin(url, e.headers["Location"])
        if not url.startswith(("https://", "http://")):
            raise ValueError(f"not a web link: {url[:80]!r}")
        if not short(url):
            return clean_url(url)
    raise ValueError("too many redirects")


_LINKS = {}  # links.json as last read: {"mtime", "links"}


def links():
    """{short link: the posting's own link} (links.json; real_links fills it)."""
    try:
        mtime = os.path.getmtime(path("links.json"))
    except OSError:
        return {}
    if _LINKS.get("mtime") != mtime:
        _LINKS.update(mtime=mtime, links=load("links.json", {}))
    return _LINKS["links"]


def real_url(url):
    """A link as the pool has it: a short link -> the posting's own link once known (links.json); any other link
    without its tracking parameters. For sheet rows added with a short link, and the assistant's start link
    (assist.other_target: one session per job site)."""
    url = clean_url(url)
    return links().get(url, url) if short(url) else url


def real_links(listings, log=print):
    """Each listing's short link -> the posting's own link (in place; list_url keeps the short one), so the job site's
    own data (postings.py), the same job on two lists (mark_dupes) and one assistant session per job site all see the
    real one. Each short link is asked once (links.json keeps the answers); one that fails is asked again next run.
    A listing whose link changes gets it in its read posting too, and is read again if no way could. Returns how many
    listings changed."""
    known = dict(links())
    todo = [] if config.DEMO else sorted({x["url"] for x in listings.values() if short(x["url"]) and x["url"] not in known})
    if todo:
        log(f"short links: asking where {len(todo)} lead")
    bad, in_row = 0, 0
    try:
        for n, u in enumerate(todo, 1):
            try:
                known[u] = unshorten(u)
                in_row = 0
            except Exception as e:
                bad, in_row = bad + 1, in_row + 1
                if in_row == 5:  # the shortener is down: the rest wait for the next run
                    log(f"short links: 5 in a row failed ({type(e).__name__}: {str(e)[:80]}); the rest wait for the next run")
                    break
            if n % 100 == 0:
                save("links.json", known)
            time.sleep(SHORT_SPACING)
    finally:
        if todo:
            save("links.json", known)
    if todo:
        log(f"short links: {len(todo) - bad} found" + (f", {bad} not yet" if bad else ""))
    changed, failed = 0, load("failed.json", {})
    for i, x in listings.items():
        if short(x["url"]) and x["url"] in known:
            listings[i] = {**x, "url": known[x["url"]], "list_url": x["url"]}
            changed += 1
            d = load(f"details/{i}.json", None)
            if d:
                save(f"details/{i}.json", {**d, "url": known[x["url"]]})
            failed.pop(i, None)
    if changed:
        save("failed.json", failed)
    return changed


# ---------------------------------------------------------------- the same job on two lists

JOB_PARAMS = {"gh_jid", "token", "for", "jr_id", "job", "jobid", "job_id", "jid", "id", "pid", "req", "reqid",
              "jobcode", "career_job_req_id"}  # parts of a link's query that name the job (the rest: tracking, layout)
COMPANY_SUFFIXES = {"inc", "llc", "ltd", "corp", "corporation", "co", "company", "plc", "lp", "llp", "the"}
TITLE_WORDS = {"internship": "intern", "engineering": "engineer", "development": "developer"}


def url_key(url):
    """A posting link as compared across lists: no www, case, trailing /apply, locale (/en-US/) or tracking."""
    u = urlparse(str(url or "").strip().lower())
    p = re.sub(r"^/[a-z]{2}-[a-z]{2}(?=/)", "", re.sub(r"/(apply|application)$", "", u.path.rstrip("/")))
    q = sorted((k, v) for k, v in parse_qsl(u.query) if k in JOB_PARAMS)
    return u.netloc.removeprefix("www.") + p + ("?" + urlencode(q) if q else "")


JOB_NUMBER_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|(?<![0-9a-z])\d{5,}(?![0-9a-z])")


def job_number(url):
    """The job's number in its link (the last 5+ digit number or id like 0f6a1b2c-1111-...), or "": the same posting
    on two hosts (careers.example.com/jobs/91180?icims=1, careers.example.com/careers-home/jobs/91180) has the same one."""
    found = JOB_NUMBER_RE.findall(url_key(url).split("/", 1)[-1])
    return found[-1] if found else ""


def words(text):
    text = str(text or "").lower().replace("c++", "cpp").replace("c#", "csharp").replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", text).split()


def company_words(name):
    return [w for w in words(name) if w not in COMPANY_SUFFIXES]


def title_key(title):
    """A title as compared across lists: no punctuation, season or year; "internship" = "intern", "engineering" =
    "engineer"."""
    return " ".join(TITLE_WORDS.get(w, w) for w in words(title)
                    if w not in ("summer", "fall", "autumn", "winter", "spring") and not re.fullmatch(r"20\d\d", w))


US_WIDE = re.compile(r"\b(united states|usa|us|remote)\b", re.I)


def same_place(a, b):
    """Two listings' places could be the same job's: a shared US state, or a place for the whole US ("United States",
    "Remote in USA") or none given on one of them, or the same place abroad. Chicago and London: not the same."""
    pa, pb = a["locations"], b["locations"]
    sa, sb = set(states_of(pa)), set(states_of(pb))
    if sa and sb:
        return bool(sa & sb)
    if not pa or not pb or any(US_WIDE.search(p) and not STATE_RE.search(p) for p in pa + pb):
        return True
    return not sa and not sb and bool({p.strip().lower() for p in pa} & {p.strip().lower() for p in pb})


def same_company(a, b):
    """"Acme Capital" = "Acme Capital University", "Acme" = "Acme, Inc.": one name's words start the other's."""
    a, b = company_words(a), company_words(b)
    n = min(len(a), len(b))
    return n > 0 and a[:n] == b[:n]


def mark_dupes(listings):
    """Each job once: a listing that another list had first (seen earlier; on the same day the earlier list in LISTS)
    gets dup_of = that listing's id, and rules() leaves it out. The same job = the same posting link or the same company
    and job number in the link (another host), except a link or number that several titles on one list share (a careers
    page, not a job); or the same company and title in the same place (same_place) while that listing is open (a closed one may be an old posting with the same title). Once a
    duplicate, always: a stale copy never comes back when the first one closes. Returns (listings, duplicates)."""
    out = {i: dict(x) for i, x in listings.items()}
    order = {k: n for n, (k, _, _) in enumerate(LISTS)}
    company = lambda x: " ".join(company_words(x["company"])[:1])  # noqa: E731  (same_company checks the rest)
    keys = lambda x: [url_key(x["url"])] + ([(company(x), job_number(x["url"]))] if job_number(x["url"]) else [])  # noqa: E731
    titles = {}
    for x in out.values():
        for k in keys(x):
            titles.setdefault((x.get("list", "simplify"), k), set()).add(title_key(x["title"]))
    shared = {k for (_, k), t in titles.items() if len(t) > 1}
    by_key, by_title = {}, {}  # the first listings only: link or (company, number) -> ids; title -> ids
    for x in sorted(out.values(), key=lambda x: (x.get("first_seen") or "", order.get(x.get("list", "simplify"), 99), x["id"])):
        if x.get("dup_of") in out:
            continue
        mine, ks, t = x.get("list", "simplify"), [k for k in keys(x) if k not in shared], title_key(x["title"])
        other = lambda i: out[i].get("list", "simplify") != mine  # noqa: E731
        hit = next((i for k in ks for i in by_key.get(k, []) if other(i)
                    and (isinstance(k, str) or same_company(out[i]["company"], x["company"]))), None)
        if hit is None:
            hit = next((i for i in by_title.get(t, []) if other(i) and out[i].get("active")
                        and same_company(out[i]["company"], x["company"]) and same_place(out[i], x)), None)
        if hit:
            x["dup_of"] = hit
        else:
            x.pop("dup_of", None)
            for k in ks:
                by_key.setdefault(k, []).append(x["id"])
            by_title.setdefault(t, []).append(x["id"])
    return out, sum(1 for x in out.values() if x.get("dup_of"))


def rules(listing):
    """(keep, why): not a job another list had first, its list still shows it as open, and it is open to
    undergraduates (no degree listed = open)."""
    if listing.get("dup_of"):
        return False, f"the same job as {listing['dup_of']} ({list_name(listing['dup_of'])})"
    if not listing.get("active"):
        return False, f"closed on {list_name(listing['id'])}"
    degrees = listing.get("degrees") or []
    if degrees and not any("bachelor" in d.lower() for d in degrees):
        return False, f"degree {'/'.join(degrees)}"
    return True, ""


STATE_RE = re.compile(r",\s*([A-Z]{2})(?:\s*,\s*(?:USA|US|United States))?\s*$")
CITY_STATES = {"NYC": "NY", "SF": "CA", "LA": "CA", "BAY AREA": "CA", "DC": "DC"}


def states_of(places):
    """US state codes ("US-MA") from places like "Boston, MA", "NYC", "SF", "Austin, TX, USA"."""
    from nuauto import insights
    out = []
    for p in places:
        p = str(p or "").strip()
        m = STATE_RE.search(p)
        code = m.group(1) if m and m.group(1) in insights.STATES else CITY_STATES.get(p.upper())
        if code and f"US-{code}" not in out:
            out.append(f"US-{code}")
    return out


def details_of(listing, got, today=None):
    """A read posting in the shape of a NUworks job's details (jobs.py's rules, viewers and model read these keys)."""
    places = listing["locations"] or ([got["location"]] if got.get("location") else [])
    return {"id": listing["id"], "title": listing["title"], "company": listing["company"], "url": listing["url"],
            "source": "simplify", "list": listing.get("list", "simplify"), "how": got["how"], "position_types": ["Internship"], "term": [TERM], "lengths": [],
            "class_levels": [], "degree_levels": listing["degrees"], "majors": [], "states": states_of(places),
            "location": "; ".join(places), "skills": [], "experience": [],
            "function": [listing["category"]] if listing["category"] else [], "pay": got.get("pay") or "",
            "deadline": got.get("deadline"), "posting_end": None, "expired": False, "applied": False,
            "description": got["description"], "qualifications": "", "posted": listing["posted"],
            "closed": bool(got.get("closed")), "fetched": (today or date.today()).isoformat()}


def standing():
    """Who you are for the scorer: your year now, the year you will be in during the internship, graduation."""
    year = jobs.PREFS["class_year"]
    up = (jobs.YEARS + ["graduate student"])[jobs.YEARS.index(year) + 1]
    return (f"a {year} now (the school year before the internship), so a rising {up} during {TERM}; "
            f"expects to graduate in {jobs.PREFS['grad_year']}")


def prompt_fields():
    return {"term": TERM, "standing": standing()}


# ---------------------------------------------------------------- 1. the list

def download(url, src, log, name):
    """A list's file, or None when it had not changed since src's ETag (src is updated in place)."""
    req = urllib.request.Request(url, headers={"User-Agent": "NUauto (internship list check)"})
    if src.get("etag") and src.get("url", url) == url:
        req.add_header("If-None-Match", src["etag"])
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            body, etag = r.read(), r.headers.get("ETag")
    except urllib.error.HTTPError as e:
        if e.code != 304:
            raise
        log(f"{name}: unchanged")
        return None
    data = json.loads(body)
    if not isinstance(data, list):
        raise ValueError(f"not a list of listings ({type(data).__name__})")
    src.update(etag=etag, url=url, changed=datetime.now().isoformat(timespec="minutes"))
    return data


def refresh(log=print):
    """Download each list when it changed (the server says so: ETag), merge it, then mark the jobs another list had
    first. A list that can't be read now is skipped (the others still count). Returns (new, closed) ids (duplicates
    left out), or None when no list had changed."""
    src = load("source.json", {})
    if "etag" in src or "checked" in src:  # before there were two lists it was Simplify's alone
        src = {"simplify": src}
    listings, new, closed, got = load("listings.json", {}), {}, {}, False
    for key, name, _ in LISTS:
        url, s = LIST_URLS.get(key), dict(src.get(key) or {})
        if not url:
            continue
        try:
            if config.DEMO:  # demo mode: a local file, never the network (Simplify's list only)
                if key != "simplify":
                    continue
                with open(path("demo_listings.json")) as f:
                    data = json.load(f)
            else:
                data = download(url, s, log, name)
        except Exception as e:
            log(f"{name}: could not check it ({type(e).__name__}: {str(e)[:120]})")
            continue
        finally:
            src[key] = {**s, "checked": datetime.now().isoformat(timespec="minutes")}
        if data is None:
            continue
        year = re.search(r"20\d\d", url)
        listings, new[key], closed[key] = merge(listings, data, key=key, year=year and year.group())
        got = True
    if real_links(listings, log) or got:
        listings, _ = mark_dupes(listings)
        save("listings.json", listings)
    save("source.json", src)
    if not got:
        return None
    first = lambda ids: [i for i in ids if not listings[i].get("dup_of")]  # noqa: E731
    for key in new:
        mine = [x for x in listings.values() if x.get("list", "simplify") == key and x["active"]]
        dups = sum(1 for x in mine if x.get("dup_of"))
        log(f"{NAMES[key]}: {len(mine) - dups} open for {TERM}" + (f" (and {dups} another list had first)" if dups else "")
            + f", {len(first(new[key]))} new, {len(first(closed[key]))} closed")
    return [i for v in new.values() for i in first(v)], [i for v in closed.values() for i in first(v)]


# ---------------------------------------------------------------- 2. triage (titles)

def triage_export():
    listings, triage = load("listings.json", {}), load("triage.json", {})
    todo = []
    for i, x in listings.items():
        if rules(x)[0] and jobs.needs_triage(triage, i):
            places = x["locations"][:4] + ([f"+{len(x['locations']) - 4} more"] if len(x["locations"]) > 4 else [])
            todo.append({"id": i, "title": x["title"], "company": x["company"], "locations": "; ".join(places),
                         "category": x["category"]})
    if not todo:
        return []
    jobs.resume_text()
    files = jobs.write_batches("itriage", todo, TRIAGE_BATCH)
    print(f"{len(todo)} internships in {len(files)} batch files: work/itriage_in_*.json (prompt: INTERN_TRIAGE_PROMPT.md)")
    return files


def take(kind, valid, what):
    """The rows of Claude's <kind> outputs that pass valid(row). A few missing or garbled rows (a mis-copied id, a
    batch that failed) are only reported: those jobs are tried again next run. Problems and nothing usable: SystemExit
    (the step stops and says why)."""
    results, problems = jobs.read_outputs(kind, lenient=True)
    good = {i: r for i, r in results.items() if valid(r)}
    if len(good) < len(results):
        problems.append(f"{len(results) - len(good)} rows without {what}")
    if problems and not good:
        sys.exit("Not imported:\n  " + "\n  ".join(problems))
    if problems:
        print("Imported the rest; these are tried again next run:\n  " + "\n  ".join(problems))
    return good


def triage_import():
    results = take("itriage", lambda r: isinstance(r.get("keep"), bool) and (not r["keep"] or r.get("fit") in FITS),
                   f"a true/false keep and, when kept, a fit in {list(FITS)}")
    triage, by = load("triage.json", {}), jobs.triage_set()
    triage.update({i: {"keep": r["keep"], "fit": r.get("fit") if r["keep"] else None, "why": str(r.get("why", ""))[:200],
                       "by": by} for i, r in results.items()})
    save("triage.json", triage)
    kept = sum(r["keep"] for r in results.values())
    print(f"Imported {len(results)} internship triage decisions: {kept} kept, {len(results) - kept} dropped.")


def priority(listing, triage):
    """Read and score order: the title's fit (high first), then the newest posting."""
    fit = (triage.get(listing["id"]) or {}).get("fit")
    posted = listing.get("posted") or "0000-00-00"
    return (FITS.index(fit) if fit in FITS else len(FITS), -int(posted.replace("-", "") or 0))


# ---------------------------------------------------------------- 3. read the postings

def unreadable(failed, i):
    return (failed.get(i) or {}).get("tries", 0) >= TRIES


def fetch_todo(listings, triage, have, failed, today=None):
    """Kept, open listings with no posting read yet: not given up on, not already tried today."""
    today = (today or date.today()).isoformat()
    todo = [x for i, x in listings.items() if rules(x)[0] and (triage.get(i) or {}).get("keep") and i not in have
            and not unreadable(failed, i) and (failed.get(i) or {}).get("last") != today]
    return sorted(todo, key=lambda x: priority(x, triage))


def fetch(log=print, limit=None, browser_limit=None):
    """Read postings from the companies' own job sites (postings.py). Each one is saved as it comes. Returns
    (read, failed) counts."""
    from nuauto import postings
    limit = SETTINGS["fetch_per_run"] if limit is None else limit
    browser_limit = SETTINGS["browser_per_run"] if browser_limit is None else browser_limit
    listings, triage, failed = load("listings.json", {}), load("triage.json", {}), load("failed.json", {})
    have = {n[:-5] for n in os.listdir(path("details")) if n.endswith(".json")} if os.path.isdir(path("details")) else set()
    todo = fetch_todo(listings, triage, have, failed)[:limit]
    if not todo:
        return 0, 0
    os.makedirs(path("details"), exist_ok=True)
    demo = load("demo_postings.json", {}) if config.DEMO else None
    client, browser, used, ok, bad = postings.Client(), None, 0, 0, 0
    log(f"reading {len(todo)} postings ({len(fetch_todo(listings, triage, have, failed))} waiting)")
    try:
        for n, x in enumerate(todo, 1):
            i, tried_all = x["id"], False
            try:
                if demo is not None:  # demo mode: the postings are in a local file, never the network
                    if x["url"] not in demo:
                        tried_all = True
                        raise postings.Unreadable("demo: no such posting")
                    got = postings.result({"description": demo[x["url"]], "location": "", "pay": "", "deadline": None}, "demo")
                else:
                    try:
                        got = postings.read_plain(x["url"], client)
                    except postings.Unreadable as plain:
                        if used >= browser_limit:
                            raise postings.Unreadable(f"{plain} (the hidden browser waits for the next run)")
                        if browser is None:
                            try:
                                browser = postings.Browser().__enter__()
                            except Exception as e:  # e.g. Playwright's Firefox missing: plain requests only this run
                                log(f"the hidden browser could not start ({type(e).__name__}: {str(e)[:120]})")
                                browser_limit = 0
                                raise postings.Unreadable(f"{plain} (the hidden browser could not start)")
                        used += 1
                        tried_all = True
                        try:
                            got = postings.read_browser(x["url"], browser)
                        except postings.Unreadable as e:
                            raise postings.Unreadable(f"{plain}; {e}")
                save(f"details/{i}.json", details_of(x, got))  # whole or not at all: the GUI may be reading
                failed.pop(i, None)
                ok += 1
            except postings.Unreadable as e:
                bad += 1
                if tried_all:
                    f = failed.get(i) or {"tries": 0}
                    failed[i] = {"tries": f["tries"] + 1, "last": date.today().isoformat(), "why": str(e)[:300]}
                    save("failed.json", failed)
            if n % 25 == 0:
                log(f"  postings {n}/{len(todo)}: {ok} read, {bad} not yet")
    except KeyboardInterrupt:
        log("Ctrl+C: stopping. The postings read so far are saved.")
    finally:
        if browser is not None:
            browser.__exit__(None, None, None)
        save("failed.json", failed)
    log(f"postings: {ok} read, {bad} could not be read yet ({sum(unreadable(failed, i) for i in failed)} given up on)")
    return ok, bad


# Job sites whose data has a close date NUauto reads only since 2026-10-26 (Oracle's ExternalPostedEndDate): their
# postings read before that are read once more, for the date alone (redate)
REDATE = {"oracle"}


def redate(log=print, limit=40):
    """Saved postings from a REDATE job site with no close date: read again once (plain requests only), keeping only the
    date they give (the text was scored as it was). Returns how many got one."""
    from nuauto import postings
    listings = load("listings.json", {})
    todo = [d for i, d in load_details().items()
            if d.get("how") in REDATE and not d.get("deadline") and not d.get("redated") and i in listings][:limit]
    if not todo:
        return 0
    client, dated = postings.Client(), 0
    for d in todo:
        try:
            day = postings.read_plain(d["url"], client).get("deadline")
        except Exception:  # (unreadable now, or the network): no date, and it is not tried again
            day = None
        save(f"details/{d['id']}.json", {**d, "deadline": day, "redated": date.today().isoformat()})
        dated += bool(day)
    log(f"postings read again for their close date: {len(todo)}, {dated} give one")
    return dated


def closing_dates():
    """{posting link (clean_url): the day it closes} for the internships whose posting gives one (jobs.closes)."""
    listings, out = load("listings.json", {}), {}
    for i, d in load_details().items():
        day = jobs.closes(d)
        if day and i in listings:
            out[clean_url(listings[i]["url"])] = day
    return out


# ---------------------------------------------------------------- 4. score, 4b. category

def score_export(limit=None):
    limit = SETTINGS["score_per_run"] if limit is None else limit
    listings, triage, scores = load("listings.json", {}), load("triage.json", {}), load("scores.json", {})
    details = load_details()
    todo = sorted((listings[i] for i, d in details.items() if i in listings and i not in scores and rules(listings[i])[0]
                   and not d.get("closed")), key=lambda x: priority(x, triage))
    if not todo:
        return []
    items = [{"id": x["id"], "title": x["title"], "company": x["company"], "location": details[x["id"]]["location"],
              "category": x["category"], "description": jobs.score_text(details[x["id"]])[:jobs.TEXT_CAP]}
             for x in todo[:limit]]
    jobs.resume_text()
    files = jobs.write_batches("iscore", items, jobs.SCORE_BATCH)
    print(f"{len(items)} internships ({len(todo)} waiting) in {len(files)} batch files: work/iscore_in_*.json "
          "(prompt: INTERN_SCORE_PROMPT.md)")
    return files


def score_import():
    results = take("iscore", lambda r: isinstance(r.get("match"), int) and not isinstance(r["match"], bool)
                   and 0 <= r["match"] <= 100 and r.get("year_req") in YEAR_REQS,
                   f"an integer match 0-100 and a year_req in {list(YEAR_REQS)}")
    scores = load("scores.json", {})
    for i, r in results.items():
        scores[i] = {k: r.get(k) for k in ("match", "year_req", "met", "partial", "missing", "why")}
        scores[i]["year_text"] = str(r.get("year_text") or "")[:300]
        scores[i]["scored"] = date.today().isoformat()
    save("scores.json", scores)
    print(f"Imported {len(results)} internship scores.")


def categorized(cats, sets, i):
    return cats.get(i) in jobs.CATEGORIES and sets.get(i) == jobs.CATEGORY_SET


def category(cats, sets, i):
    return cats[i] if categorized(cats, sets, i) else "uncategorized"


def cat_export():
    details, scores = load_details(), load("scores.json", {})
    cats, sets = load("categories.json", {}), load("category_sets.json", {})
    todo = [{"id": i, "title": d["title"], "company": d["company"], "function": d["function"], "skills": d["skills"],
             "excerpt": d["description"][:600]} for i, d in details.items() if i in scores and not categorized(cats, sets, i)]
    if not todo:
        return []
    files = jobs.write_batches("icat", todo, jobs.CAT_BATCH)
    print(f"{len(todo)} internships in {len(files)} batch files: work/icat_in_*.json (prompt: CATEGORY_PROMPT.md)")
    return files


def cat_import():
    results = take("icat", lambda r: r.get("category") in jobs.CATEGORIES, f"a category in {sorted(jobs.CATEGORIES)}")
    cats, sets = load("categories.json", {}), load("category_sets.json", {})
    cats.update({i: r["category"] for i, r in results.items()})
    sets.update({i: jobs.CATEGORY_SET for i in results})
    save("categories.json", cats)
    save("category_sets.json", sets)
    print(f"Imported {len(results)} internship categories.")


# ---------------------------------------------------------------- 5. the pool

def entry_rules(listing, d, score, cat, today=None):
    """(keep, threshold, flags) like jobs.pool_entry_rules, with "who may apply" read by the scorer against your
    standing during the internship (year_req): one year up = the higher bar, two or more / graduate only = dropped."""
    keep, _ = rules(listing)
    if not keep:
        return False, None, []
    base = jobs.PREFS["threshold"] if SETTINGS["threshold"] is None else int(SETTINGS["threshold"])
    threshold, flags = base, []
    if cat in jobs.PREFS["category_threshold"]:  # a lower bar for the kinds you care most about
        threshold = min(base, jobs.PREFS["category_threshold"][cat])
    req = score.get("year_req") or "ok"
    if req in ("two_years_up", "grad"):
        return False, None, []
    if req == "one_year_up":
        threshold = max(threshold, jobs.PREFS["threshold_above"])
        flags.append("for students a year further along (in text)")
    if d.get("closed"):
        flags.append("the posting looks closed")
    if len((score.get("met") or []) + (score.get("partial") or []) + (score.get("missing") or [])) < 4:
        flags.append("vague posting, low confidence")
    years = jobs.grad_years(jobs.score_text(d))
    if years:
        flags.append(f"mentions graduation {'/'.join(years)}")
    if "citizenship is required" in listing.get("sponsorship", "").lower():
        flags.append("US citizens only")
    age = age_days(listing, today)
    if age is not None and age >= STALE_DAYS:
        flags.append(f"posted {age} days ago")
    return True, threshold, flags


def age_days(listing, today=None):
    try:
        return ((today or date.today()) - date.fromisoformat(listing.get("posted") or "")).days
    except ValueError:
        return None


def posted_text(listing, today=None):
    n = age_days(listing, today)
    return "posting date not listed" if n is None else "posted today" if n <= 0 else \
        "posted yesterday" if n == 1 else f"posted {n} days ago"


def pool_entry(listing, d, score, cat, threshold, flags, today=None):
    e = jobs.pool_entry(listing["id"], d, score, cat, threshold, flags)
    e["flags"] = [f for f in e["flags"] if f != "apply on company site too?"]  # it IS on the company site
    e.update(source="simplify", url=listing["url"], posted=listing.get("posted"), year_text=score.get("year_text") or "")
    # (source "simplify" = an internship, whichever list it is from: the GUI and jobs.py tell them from co-ops by it)
    return e


def build_pool(today=None, details=None):
    listings, scores = load("listings.json", {}), load("scores.json", {})
    details = load_details() if details is None else details
    cats, sets = load("categories.json", {}), load("category_sets.json", {})
    liked = {i for i, r in jobs.load("ratings.json", {}).items() if r.get("label") == 1}
    pool = []
    for i, d in details.items():
        x = listings.get(i)
        if x is None or i not in scores:
            continue
        cat = category(cats, sets, i)
        keep, threshold, flags = entry_rules(x, d, scores[i], cat, today)
        if not keep:
            continue
        match = scores[i]["match"]
        if i in liked and match < threshold:
            flags, threshold = flags + ["kept: rated yes"], match
        if match >= threshold:
            pool.append(pool_entry(x, d, scores[i], cat, threshold, flags, today))
    pool.sort(key=jobs.pool_sort_key)
    return pool


def unread_entries(today=None):
    """Kept, open listings no way could read (TRIES runs): shown after the scored ones, so none is lost."""
    listings, triage, failed = load("listings.json", {}), load("triage.json", {}), load("failed.json", {})
    out = []
    for i, x in listings.items():
        if rules(x)[0] and (triage.get(i) or {}).get("keep") and unreadable(failed, i):
            out.append({"id": i, "title": x["title"], "company": x["company"], "location": "; ".join(x["locations"]),
                        "category": "uncategorized", "match": None, "rank": None, "threshold": None, "bonus": 0,
                        "tag": None, "closes": "", "why": "", "source": "simplify", "url": x["url"], "unread": True,
                        "posted": x.get("posted"), "year_text": "",
                        "flags": ["couldn't read the posting: open it to judge"] +
                                 ([f"posted {age_days(x, today)} days ago"] if (age_days(x, today) or 0) >= STALE_DAYS else []),
                        "unread_why": failed[i].get("why", "")})
    return sorted(out, key=lambda e: e["posted"] or "", reverse=True)


def save_pool(today=None):
    """Build the pool and save it, with the unreadable ones' links (unread.json: the GUI's count). Returns the pool."""
    pool = build_pool(today)
    save("pool.json", pool)
    save("unread.json", [{"id": r["id"], "url": r["url"]} for r in unread_entries(today)])
    return pool


def sheet_urls(rows):
    """The Other jobs tab's links, except Proposed rows (Review shows those first, like your Proposed NUworks rows)."""
    return {real_url(r.url) for r in rows or [] if r.url and r.status != "Proposed"}


def proposed_rows(rows):
    """{link: row number} of the Other jobs tab's Proposed rows (e.g. an approval you undid)."""
    return {real_url(r.url): r.number for r in rows or [] if r.url and r.status == "Proposed"}


def ranked_pool(ratings, other_rows, nuworks_rows=()):
    """The pool, ordered like the co-ops' (jobs.ranked_pool): by score until the taste model can train, then half
    score, half taste. The model learns from your co-op and internship decisions together. Returns (pool, trained)."""
    details = load_details()
    pool = build_pool(details=details)
    every = {**jobs.load_details(), **details}
    labels = jobs.my_labels(every, ratings, nuworks_rows)
    by_url = {d.get("url"): i for i, d in details.items()}
    for r in other_rows or []:
        i = by_url.get(real_url(r.url))
        if i and r.status in ("Approved", "Applied", "Accepted", "Rejected", "Needs Human", "Failed") and i not in ratings:
            labels[i] = 1
    taste = jobs.taste_for(every, labels, [r["id"] for r in pool])
    if taste:
        for r in pool:
            r["taste"] = taste[r["id"]]
        pool.sort(key=jobs.pool_sort_key)
    return pool, taste is not None


def review_queue(mode, ratings, other_rows, nuworks_rows=(), today=None):
    """What Review shows for internships, as (todo, urgent), like jobs.review_queue. approve: pool jobs not in the
    Other jobs tab and not rated no (closing within a week first, when the posting says when), then the ones that
    could not be read. rate: unrated pool jobs, in rating order."""
    in_sheet, proposed = sheet_urls(other_rows), proposed_rows(other_rows)
    pool, _ = ranked_pool(ratings, other_rows, nuworks_rows)
    if mode == "rate":
        return jobs.rating_order([r for r in pool if r["id"] not in ratings and r["url"] not in in_sheet
                                  and r["url"] not in proposed]), []
    keep = lambda r: r["url"] not in in_sheet and ratings.get(r["id"], {}).get("label") != 0  # noqa: E731
    todo = []
    for r in pool + unread_entries(today):
        if not keep(r):
            continue
        if r["url"] in proposed:  # its Other jobs row is Proposed: y turns that row Approved
            r = {**r, "row": proposed[r["url"]], "flags": r["flags"] + [f"already Proposed (Other jobs row {proposed[r['url']]})"]}
        todo.append(r)
    mine = [r for r in todo if r.get("row")]
    rest = [r for r in todo if not r.get("row")]
    today = today or date.today()
    left = {r["id"]: (date.fromisoformat(r["closes"]) - today).days if r.get("closes") else None for r in rest}
    urgent = sorted((r for r in rest if left[r["id"]] is not None and 0 <= left[r["id"]] <= jobs.URGENT_DAYS),
                    key=lambda r: left[r["id"]])
    return urgent + mine + [r for r in rest if r not in urgent], urgent


def details_for(r):
    """A queue entry's details (a minimal stand-in for one that could not be read)."""
    d = load(f"details/{r['id']}.json", None)
    return d or {"id": r["id"], "title": r["title"], "company": r["company"], "url": r["url"], "location": r["location"],
                 "pay": "", "skills": [], "description": "", "qualifications": "", "deadline": None, "posting_end": None}


def closes_func():
    """row -> the date its posting closes (the list's saved details), or None when unknown / no date given. The listings
    are read once, for any number of rows."""
    by_url = {x["url"]: i for i, x in load("listings.json", {}).items()}

    def closes(row):
        i = by_url.get(real_url(row.url))
        d = load(f"details/{i}.json", None) if i else None
        return jobs.closes(d) if d else None
    return closes


def job_view(r, d, today=None):
    """One internship for the GUI's Review card: jobs.job_view plus where it is from (the posting's own link)."""
    listing = load("listings.json", {}).get(r["id"]) or {}
    extra = {"source": "simplify", "url": r["url"], "posted_text": f"{posted_text(listing, today)} ({list_name(r['id'])})",
             "year_text": r.get("year_text") or "", "unread": bool(r.get("unread")),
             "external": None}  # the posting IS the company's site: no "also apply there" hint
    if r.get("unread"):
        return {"id": r["id"], "title": r["title"], "company": r["company"], "location": r["location"],
                "category": r["category"], "category_label": jobs.category_label(r["category"]), "tag": None,
                "match": None, "threshold": None, "score": "not scored: the posting could not be read", "taste": None,
                "pay": "", "closes": None, "closes_text": "no deadline listed", "soon": False, "external": None,
                "flags": r["flags"], "why": r.get("unread_why", ""), "skills": [], "description": [],
                "qualifications": [], **extra}
    return {**jobs.job_view(r, d, today), **extra}


def sheet_item(r):
    """A queue entry as a new Other jobs tab row."""
    note = f"{TERM} internship ({list_name(r['id'])})"
    if r.get("unread"):
        notes = f"{note}; not scored (the posting could not be read)"
    else:
        notes = f"{note}; " + jobs.sheet_item(r)["notes"]
    return {"url": r["url"], "company": r["company"], "title": r["title"], "notes": notes}


def approve(r, interactive=True):
    """Review's Approve for an internship: an Approved row in the Other jobs tab (the tab is made if missing), or, for
    one whose row there is Proposed, that row turns Approved. Returns the row number. SheetError if it is there
    already or the link is not one the agent may open."""
    from nuauto import manage
    from nuauto import sheet
    if r.get("row"):
        sheet.approve_proposed(sheet.open_other(interactive=interactive), r["row"], r["url"])
        return r["row"]
    item = sheet_item(r)
    return manage.add("other", item["url"], item["company"], item["title"], "Approved", notes=item["notes"],
                      interactive=interactive)


def other_rows(interactive=True):
    """The Other jobs tab's rows ([] when the tab does not exist yet)."""
    from nuauto import sheet
    try:
        return sheet.read_rows(sheet.open_other(interactive=interactive))
    except sheet.NoOtherTab:
        return []


# ---------------------------------------------------------------- the run

def run_lock():
    """An exclusive lock for one update at a time (the 2-hourly timer and the NUworks update may meet). None if another
    update holds it."""
    os.makedirs(DIR, exist_ok=True)
    f = open(path("update.lock"), "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        return None
    return f


def update():
    """The whole update: list -> triage -> postings -> score -> category -> pool; the counts (and the best new one) go to
    scans.json for the morning message (no message of its own since 2026-10-26). Returns 0, or 1 if a step stopped
    (already told: daily.alert)."""
    from nuauto import daily
    lock = run_lock()
    if lock is None:
        daily.log("internships: another update is running; nothing to do")
        return 0
    with lock:
        before = {r["id"] for r in load("pool.json", [])}
        try:
            changed = refresh(daily.log)
        except Exception as e:  # the list can't be read now (network): the rest still runs on what we have
            daily.log(f"internship list: could not check it ({type(e).__name__}: {str(e)[:120]})")
            changed = None
        if changed and changed[1]:
            closed_notice(changed[1], daily)
        checked = []

        def ready():
            if not checked:
                checked.append(daily.claude_ready())
            return checked[0]
        fields = prompt_fields()
        if not daily.claude_step(ready, "INTERN_TRIAGE_PROMPT.md", triage_export(), triage_import,
                                 "NUauto: internship triage incomplete", fields):
            return 1
        fetch(daily.log)
        redate(daily.log)
        if not daily.claude_step(ready, "INTERN_SCORE_PROMPT.md", score_export(), score_import,
                                 "NUauto: internship scoring incomplete", fields):
            return 1
        daily.claude_step(ready, "CATEGORY_PROMPT.md", cat_export(), cat_import, "NUauto: internship categories incomplete")
        pool = save_pool()
        new = [r for r in pool if r["id"] not in before]
        daily.log(f"internship pool {len(pool)} ({len(new)} new)")
        best = max(new, key=lambda r: r["match"], default=None)
        scans = load("scans.json", [])
        scans.append({"time": datetime.now().isoformat(timespec="minutes"), "listed": len(changed[0]) if changed else 0,
                      "pool": len(new), **({"best": {"score": best["match"], "text": f"{best['title'][:40]} | "
                                                     f"{best['company'][:22]} ({best['match']}% match)"}} if best else {})})
        save("scans.json", scans[-200:])
        return 0


def closed_notice(closed, daily):
    """Internships you approved that their list now shows as closed: a line in the morning message (daily.later), so no
    assistant run is spent on one. The sheet is only read."""
    listings = load("listings.json", {})
    urls = {listings[i]["url"]: listings[i] for i in closed if i in listings}
    try:
        rows = [r for r in other_rows(interactive=False) if r.status == "Approved" and real_url(r.url) in urls]
    except BaseException as e:  # the sheet can't be read now (e.g. the Google login expired): no message
        daily.log(f"closed internships: could not read the sheet ({type(e).__name__})")
        return
    if rows:
        daily.log(f"closed internships: Other jobs rows {', '.join(str(r.number) for r in rows)}")
        daily.later("closed", [f"{r.company[:22]} (row {r.number})" for r in rows])


def stats():
    listings, triage, failed, scores = load("listings.json", {}), load("triage.json", {}), load("failed.json", {}), load("scores.json", {})
    details = load_details()
    open_ = [i for i, x in listings.items() if rules(x)[0]]
    kept = [i for i in open_ if (triage.get(i) or {}).get("keep")]
    src = load("source.json", {})
    src = {"simplify": src} if "checked" in src else src  # (before there were two lists)
    checked = min((v.get("checked", "never") for v in src.values()), default="never")
    print(f"{TERM}: {len(listings)} listed ({len(open_)} open to you) | triaged {sum(i in triage for i in open_)} "
          f"(kept {len(kept)}) | postings read {sum(i in details for i in kept)}, waiting "
          f"{sum(i not in details and not unreadable(failed, i) for i in kept)}, unreadable "
          f"{sum(unreadable(failed, i) for i in kept)} | scored {sum(i in scores for i in kept)} | pool "
          f"{len(load('pool.json', []))} | lists checked {checked}")
    for key, name, _ in LISTS:
        mine = [x for x in listings.values() if x.get("list", "simplify") == key and x.get("active")]
        if mine or LIST_URLS.get(key):
            dups = sum(1 for x in mine if x.get("dup_of"))
            print(f"{name}: {len(mine)} open, {dups} of them another list had first")
    hows = {}
    for d in details.values():
        hows[d.get("how")] = hows.get(d.get("how"), 0) + 1
    if hows:
        print("read by: " + ", ".join(f"{k} {v}" for k, v in sorted(hows.items(), key=lambda kv: -kv[1])))


def cmd_review(mode):
    """The terminal viewer (jobs.rate_viewer). approve: y = an Approved row in the Other jobs tab."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        sys.exit(f"intern {mode} needs an interactive terminal.")
    ratings = jobs.load("ratings.json", {})
    rows = other_rows()
    todo, urgent = review_queue(mode, ratings, rows)
    scored = [r for r in todo if not r.get("unread")]
    if not scored:
        print("Nothing new in the internship pool.")
    details = {r["id"]: details_for(r) for r in scored}
    approved = [] if mode == "approve" else None
    try:
        if scored:
            jobs.rate_viewer(scored, details, ratings, approved, urgent)
    finally:
        for r in approved or []:
            try:
                print(f"Other jobs row {approve(r)}: {r['company']} | {r['title']} (Approved).")
            except Exception as e:
                print(f"Not added ({r['company']}): {e}")
    unread = [r for r in todo if r.get("unread")]
    if mode == "approve" and unread:
        print(f"\n{len(unread)} postings could not be read (open them; add one with "
              "`nuauto sheet add other <url> <company> <title>`):")
        for r in unread[:30]:
            print(f"  {r['company'][:28]:28} {r['title'][:50]:50} {r['url']}")
    if mode == "approve" and approved:
        print("Next: nuauto assist other <row>")


def main(argv):
    cmd, rest = (argv[0], argv[1:]) if argv else ("", [])
    if cmd == "fetch" and rest:
        from nuauto import postings
        return postings.main(rest)
    here = cmd == "update" and rest == ["--here"]  # on this machine even when a homelab runs it (e.g. before it has the code)
    if cmd not in ("update", "approve", "rate", "stats", "pool") or (rest and not here):
        sys.exit(__doc__)
    if not ENABLED and cmd != "stats":
        sys.exit('Internships are off. To turn them on, add "internships": {} to local/local_config.json.')
    laptop = config.HAS_SERVER and not config.IS_SERVER and not here
    if laptop:
        from nuauto import sync
        if cmd == "update":
            print("Running the internship update on the homelab (Ctrl+C stops watching, not the run)...", flush=True)
            code = sync.ssh(sync.watch_unit("nuauto-intern.service"))
            sync.pull()
            sys.exit(code)
        sync.pull()
    try:
        if cmd == "update":
            try:
                code = update()
            except Exception as e:  # what the 2-hourly timer runs: say so on Discord, then fail the unit
                from nuauto import daily
                daily.notify("NUauto: internship update failed", f"{type(e).__name__}: {str(e)[:150]}")
                raise
            sys.exit(code)
        if cmd in ("approve", "rate"):
            cmd_review(cmd)
        elif cmd == "stats":
            stats()
        elif cmd == "pool":
            for r in build_pool():
                flags = f"  [{', '.join(r['flags'])}]" if r["flags"] else ""
                print(f"{r['match']:3d}% (needs {r['threshold']}, rank {r['rank']}) {r['category'][:13]:13s} "
                      f"{r['title']} | {r['company']} | {r['location'][:40]}{flags}")
    finally:
        if laptop and cmd in ("approve", "rate"):
            sync.push()  # the ratings go to the homelab


if __name__ == "__main__":
    main(sys.argv[1:])
