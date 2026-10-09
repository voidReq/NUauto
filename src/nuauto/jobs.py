"""Build a clean pool of Spring 2027 co-ops that match your resume, then suggest from it.

Pipeline (run in order; each step only does new work):
  nuauto jobs list              # 1. job list via NUworks search, server-side filters (script)
  nuauto jobs triage-export     # 2. batches for a Claude subagent: drop clearly unrelated titles
  nuauto jobs triage-import
  nuauto jobs details           # 3. full details for kept jobs + hard rules (script)
  nuauto jobs score-export      # 4. batches for a Claude subagent: match % vs your resume
  nuauto jobs score-import
  nuauto jobs cat-export        # 4b. batches for a Claude subagent: role category (CATEGORY_PROMPT.md)
  nuauto jobs cat-import
  nuauto jobs pool              # 5. thresholds + bonuses -> the pool
  nuauto jobs rate              # your y/n on pool jobs (teaches the ranking model)
  nuauto approve                  # viewer on the best unrated jobs: y = Approved in the sheet, n = no
  nuauto jobs suggest [N]       # top N of the pool -> sheet as Proposed, after you say y
  nuauto jobs stats

Rules live in RULES below. Nothing here clicks Apply or changes NUworks.
"""
import glob
import html
import json
import math
import os
import re
import subprocess
import sys
from datetime import date
from urllib.parse import urlparse

from nuauto import config
from nuauto import sheet

HOST = "https://northeastern-csm.symplicity.com"

# Your preferences: local_config.json "preferences" (the GUI's setup wizard writes them). A missing key uses the
# default below. The defaults are this project's original settings (decided 2026-10-02), so a setup without
# preferences works exactly as before.
DEFAULTS = {
    "term": "2027 - Spring",                        # your co-op term, as NUworks names it
    "term_id": "d13c36bce4531e63c56c9b58b90dbb71",  # its el_work_term id (from /api/v2/jobs/filters/students)
    "class_year": "sophomore",                      # yours: freshman, sophomore, junior or senior
    "threshold": 65,           # match % needed when the job is open to your year (or names no year)
    "threshold_above": 90,     # ...when its lowest year is the one after yours (two or more years up: dropped)
    "category_threshold": {"security": 60},  # a lower bar for roles you care most about (not for "above your year")
    "major_words": ["college of engineering", "electrical"],  # a targeted major without these words: flagged
    "home_state": "MA", "home_label": "Boston", "home_bonus": 10,  # ranking only (not for getting into the pool)
    "category_bonus": {"security": 20, "embedded": 15, "hardware": 10, "systems": 10, "robotics_test": 10},  # ranking
    # ranking only: a tag is given when any of its phrases is anywhere in the job (job_tag); the first tag that matches
    # wins (one per job, not added up)
    "tags": [{"name": "AR/XR", "bonus": 5, "phrases": [
                 "augmented reality", "virtual reality", "mixed reality", "extended reality", "AR/VR", "VR/AR", "XR",
                 "smart glasses", "smartglasses", "head mounted", "HMD", "spatial computing", "hololens", "vision pro",
                 "meta quest"]},
             {"name": "wearables", "bonus": 3, "phrases": ["wearable", "wearables"]}],
    "rank_last": ["fullstack_web"],             # categories that stay in the pool but always rank last
    "grad_year": 2029,                          # flags postings that mention graduating in the 4 years before
    # for Claude's triage (prompts/TRIAGE_PROMPT.md): who you are, the roles that fit, the ones that clearly don't
    "student": "2nd-year Electrical & Computer Engineering (math minor). Interests sit at\n"
               "intersections of embedded systems, hardware, software, security/pentesting,\n"
               "networking/Linux, robotics/controls, AR/VR, computer architecture.",
    "keep_roles": "software, firmware/embedded, electrical/computer/hardware engineering,\n"
                  "  test/validation, security/IT/networking, robotics/controls, data/ML, R&D,\n"
                  "  technical product or technical operations, lab/automation engineering, etc.",
    "drop_roles": "accounting, finance,\n"
                  "  marketing, sales, HR, nursing/clinical, pharmacy, law, purely biology/chemistry lab\n"
                  "  work, pure mechanical/civil design with no electrical/software side, teaching,\n"
                  "  hospitality.",
}
PREFS = {**DEFAULTS, **(config.LOCAL.get("preferences") or {})}
YEARS = ["freshman", "sophomore", "junior", "senior"]
YEAR_WORDS = ["freshm", "sophomore", "junior", "senior"]  # as they appear in NUworks' class level labels
MY_YEAR = YEARS.index(PREFS["class_year"])
NEXT_YEAR = (YEARS + ["grad"])[MY_YEAR + 1]  # for the flag: "junior+" when you are a sophomore
CLASS_REQ_YEAR = {"none": -1, "junior_plus": 2, "senior_plus": 3, "grad": 4}  # the scorer's class_req, as a year

RULES = {
    # server-side search filters (ids from /api/v2/jobs/filters/students)
    "query_term": f"job_type=5&el_work_term={PREFS['term_id']}&internal_status=1&job_length_ms=4,3&exclude_applied_jobs=1",
    "query_no_term": "job_type=5&internal_status=1&job_length_ms=4,3&exclude_applied_jobs=1",  # minus the above = term unclear
    "position_types": {"Co-op", "Internship"},  # Internship only if explicitly your term
    "term": PREFS["term"],
    "lengths": {"4 Month", "6 Month"},
}
def set_prefs(prefs):
    """Use new preferences in this process (the GUI, after the wizard saves them). Child processes read the file."""
    global PREFS, MY_YEAR, NEXT_YEAR, TERM_TEXT, OTHER_TERM, GRAD_YEARS, TAGS
    PREFS = {**DEFAULTS, **(prefs or {})}
    MY_YEAR = YEARS.index(PREFS["class_year"])
    NEXT_YEAR = (YEARS + ["grad"])[MY_YEAR + 1]
    RULES["query_term"] = f"job_type=5&el_work_term={PREFS['term_id']}&internal_status=1&job_length_ms=4,3&exclude_applied_jobs=1"
    RULES["term"] = PREFS["term"]
    TERM_TEXT, OTHER_TERM = term_patterns(PREFS["term"])
    GRAD_YEARS = grad_pattern()
    TAGS = tag_patterns(PREFS["tags"])


SEASONS = ["spring", "summer", "fall", "autumn", "winter"]
# month ranges that mean the other main term, e.g. "SEP-DEC" in a title tagged Spring
OTHER_MONTHS = {"spring": r"(sep|sept|jul|july|aug)\s*[-–]\s*dec", "fall": r"(jan|january|feb)\s*[-–]\s*(jun|june|may)"}


def term_patterns(label):
    """(the text that names your term, a regex for a title naming another term). "2027 - Spring" -> "spring 2027"."""
    m = re.match(r"^\s*(\d{4})\s*-\s*([A-Za-z]+)", label)
    if not m:
        return label.lower(), re.compile(r"(?!x)x")  # can't tell: never flags
    year, season = int(m.group(1)), m.group(2).lower()
    same = {"fall", "autumn"} if season in ("fall", "autumn") else {season}
    years = f"{(year - 1) % 100:02d}|{year % 100:02d}"
    pattern = rf"\b({'|'.join(x for x in SEASONS if x not in same)})\s*'?(20)?({years})\b"
    if season in OTHER_MONTHS:
        pattern += rf"|\b{OTHER_MONTHS[season]}\b"
    return f"{season} {year}", re.compile(pattern, re.IGNORECASE)


def phrase_pattern(phrase):
    """A tag phrase as a regex: any case, whole words, a space or hyphen matching either ("head mounted" also finds
    "head-mounted")."""
    parts = [re.escape(w) for w in re.split(r"[\s-]+", phrase.strip()) if w]
    body = r"[\s-]+".join(parts)
    start = r"\b" if re.match(r"\w", phrase.strip()) else ""
    end = r"\b" if re.search(r"\w$", phrase.strip()) else ""
    return start + body + end


def tag_patterns(tags):
    return [(t["name"], re.compile("|".join(phrase_pattern(x) for x in t["phrases"]), re.IGNORECASE)) for t in tags]


TAGS = tag_patterns(PREFS["tags"])


def job_tag(d):
    """The name of the first tag (PREFS "tags") whose phrases appear in the job's text, or None."""
    text = " ".join([d["title"], d["description"], d["qualifications"], " ".join(d["skills"])])
    return next((name for name, pattern in TAGS if pattern.search(text)), None)


def tag_bonus(tag):
    return next((t["bonus"] for t in PREFS["tags"] if t["name"] == tag), 0)


CATEGORIES = {"security", "embedded", "hardware", "systems", "robotics_test", "fullstack_web",
              "software", "data_ml", "it", "other"}
CAT_BATCH = 120

# a title naming another term even though NUworks tags the job with yours
TERM_TEXT, OTHER_TERM = term_patterns(PREFS["term"])

# "graduating in 2027", "Class of 2028", "graduation date between Dec 2027 and Jun 2028"...
FIRST_COOP = re.compile(r"(not|no)\b[^.\n]{0,40}first[- ]time co-?op|previous co-?op (is )?required|must have completed (at least )?(one|1|a) (prior |previous )?co-?op", re.IGNORECASE)
GRAD_WORD = re.compile(r"graduat\w*|class of", re.IGNORECASE)


def grad_pattern():
    return re.compile(r"\b(" + "|".join(str(PREFS["grad_year"] - k) for k in range(4, 0, -1)) + r")\b")


GRAD_YEARS = grad_pattern()


def grad_years(text):
    """The 4 years before your graduation year (2025-2028 for 2029) within the same clause (50 chars, up to . ; or
    newline) after 'graduat...'/'class of'."""
    years = set()
    for m in GRAD_WORD.finditer(text):
        clause = re.split(r"[.;\n]", text[m.end():m.end() + 50])[0]
        years.update(GRAD_YEARS.findall(clause))
    return sorted(years)
CLASS_REQS = {"none", "junior_plus", "senior_plus", "grad"}  # from the description, set by the scorer

TEXT_CAP = 15000       # characters of description sent to the scorer (was 5000 on the first run)
OLD_TEXT_CAP = 5000

TRIAGE_BATCH = 250
SCORE_BATCH = 20


# ---------------------------------------------------------------- storage

def path(*parts):
    return os.path.join(config.DATA_DIR, *parts)


def load(name, default):
    p = path(name)
    if not os.path.exists(p):
        return default
    with open(p) as f:
        return json.load(f)


def save(name, data):
    os.makedirs(config.DATA_DIR, exist_ok=True)
    tmp = path(name + ".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path(name))


def load_details():
    out = {}
    for p in glob.glob(path("details", "*.json")):
        with open(p) as f:
            d = json.load(f)
        out[d["id"]] = d
    return out


def open_url(url):
    """Open a page in your default browser, quietly (no output into the terminal viewer)."""
    cmd = ["open", url] if sys.platform == "darwin" else ["xdg-open", url]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def job_url(job_id):
    return f"{HOST}/students/app/jobs/detail/{job_id}"


def job_id(url):
    return url.rstrip("/").split("/")[-1]


def closes(d):
    """The date applications close: the earlier of the deadline and the posting end. None if neither is set."""
    days = [date.fromisoformat(str(x)[:10]) for x in (d.get("deadline"), d.get("posting_end")) if x]
    return min(days) if days else None


def closes_text(day, today=None):
    if day is None:
        return "no deadline listed"
    n = (day - (today or date.today())).days
    when = "today" if n == 0 else "tomorrow" if n == 1 else f"in {n} days" if n > 1 else f"{-n} days ago"
    return f"{day:%a %b %-d} ({when})"


# Postings that (also) want an application on the company's own site; NUworks alone may not count.
EXTERNAL = re.compile(r"(apply|application)[^.\n]{0,60}\b(our|(company|employer)['’]?s?|the following|this) ((own|external) )?(web ?site|careers? (site|page|portal)|link|portal)"
                      r"|must (also )?apply|apply (directly|online) (at|on|through|via)|will not be considered"
                      r"|myworkdayjobs|icims|greenhouse\.io|lever\.co|taleo|smartrecruiters|jobvite|successfactors|ashbyhq",
                      re.IGNORECASE)


def external_hint(d):
    """The sentence that says to apply on the company site, or None."""
    text = (d.get("description") or "") + "\n" + (d.get("qualifications") or "")
    m = EXTERNAL.search(text)
    if not m:
        return None
    # sentence bounds: a period followed by whitespace, or a line break (URLs keep their dots)
    starts = [x.end() for x in re.finditer(r"\.\s|\n", text[:m.start()])]
    stop = re.search(r"\.(\s|$)|\n", text[m.end():])
    end = m.end() + (stop.start() + 1 if stop else len(text))
    return " ".join(text[(starts[-1] if starts else 0):end].split())[:220]


URGENT_DAYS = 7  # approve shows jobs closing within this many days first


def strip_html(s):
    s = re.sub(r"(?i)<\s*(br|/p|/li|/h\d|/div)\s*/?>", "\n", s or "")
    s = re.sub(r"(?i)<li[^>]*>", "- ", s)
    s = html.unescape(re.sub(r"<[^>]+>", "", s))
    return re.sub(r"\n\s*\n+", "\n", re.sub(r"[ \t]+", " ", s)).strip()


def labels(value):
    """Picklist values come as [{"_label": ...}] or {"_label": ...}; return the labels."""
    if isinstance(value, dict):
        value = [value]
    return [v.get("_label") or v.get("title") or "" for v in (value or []) if isinstance(v, dict) and (v.get("_label") or v.get("title"))]


# ---------------------------------------------------------------- browser helpers

def open_browser(p, label):
    from nuauto import browser
    log = browser.RunLog(label)
    blocked = []
    context = browser.launch(p)
    browser.install_domain_lock(context, log, blocked)
    page = context.pages[0] if context.pages else context.new_page()
    return browser, log, context, page, blocked


def search_pages(browser, page, context, log, query):
    """Let the NUworks search page request each results page; read the JSON it receives.
    (The list request needs the app's own auth header, so the script never builds it itself.)"""
    got = {}

    def on_resp(r):
        if urlparse(r.url).path == "/api/v2/jobs":
            try:
                got[r.url] = r.json()
            except Exception:
                pass

    page.on("response", on_resp)
    results, n, total = [], 1, None
    while True:
        got.clear()
        url = f"{HOST}/students/app/jobs/search?{query}&perPage=100&page={n}"
        if not browser.goto_logged_in(page, context, url, log):
            raise SystemExit("Not logged in. Run: nuauto login")
        for _ in range(30):
            hit = [v for u, v in got.items() if f"page={n}&" in u and "perPage=100" in u]
            if hit:
                break
            page.wait_for_timeout(500)
        else:
            raise SystemExit(f"No job list response for page {n}; stopping.")
        data = hit[0]
        total = int(data["total"])
        results += data.get("models") or []
        log.write(f"  page {n}: {len(results)}/{total}")
        if n >= math.ceil(total / 100) or not data.get("models"):
            break
        n += 1
        browser.pause(page, 2, 4)
    page.remove_listener("response", on_resp)
    if len(results) != total:
        log.write(f"  note: got {len(results)} of {total} reported")
    return results


# ---------------------------------------------------------------- 1. list

def cmd_list():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser, log, context, page, blocked = open_browser(p, "jobs_list")
        try:
            spring = search_pages(browser, page, context, log, RULES["query_term"])
            browser.pause(page, 2, 4)
            any_term = search_pages(browser, page, context, log, RULES["query_no_term"])
        except KeyboardInterrupt:
            log.write("Ctrl+C: stopping; nothing saved.")
            return
        finally:
            context.close()
        if blocked:
            sys.exit("Navigation left the allowed domain; nothing saved.")
    spring_ids = {m["job_id"] for m in spring}
    jobs = {}
    for m, source in [(m, "spring") for m in spring] + [(m, "no_term") for m in any_term if m["job_id"] not in spring_ids]:
        jobs[m["job_id"]] = {
            "id": m["job_id"],
            "title": (m.get("job_title") or "").strip(),
            "company": (m.get("name") or "").strip(),
            "location": m.get("job_location") or "",
            "snippet": strip_html(m.get("job_desc"))[:300],
            "source": source,
        }
    save("list.json", jobs)
    print(f"Saved {len(jobs)} jobs: {len(spring_ids)} {PREFS['term']}, {len(jobs) - len(spring_ids)} with no/other term.")


# ---------------------------------------------------------------- batch exchange with subagents

def write_batches(kind, items, size):
    os.makedirs(config.WORK_DIR, exist_ok=True)
    for old in glob.glob(os.path.join(config.WORK_DIR, f"{kind}_in_*.json")) + \
            glob.glob(os.path.join(config.WORK_DIR, f"{kind}_out_*.json")):
        os.remove(old)
    files = []
    for k in range(0, len(items), size):
        f = os.path.join(config.WORK_DIR, f"{kind}_in_{k // size + 1:03d}.json")
        with open(f, "w") as fh:
            json.dump(items[k:k + size], fh, indent=1)
        files.append(f)
    return files


def read_outputs(kind):
    """Read every <kind>_out_*.json and check it answers exactly the ids of the matching input."""
    results, problems = {}, []
    for fin in sorted(glob.glob(os.path.join(config.WORK_DIR, f"{kind}_in_*.json"))):
        fout = fin.replace("_in_", "_out_")
        if not os.path.exists(fout):
            problems.append(f"missing {os.path.basename(fout)}")
            continue
        with open(fin) as f:
            want = {j["id"] for j in json.load(f)}
        try:
            with open(fout) as f:
                rows = json.load(f)
        except json.JSONDecodeError as e:
            problems.append(f"{os.path.basename(fout)} is not valid JSON: {e}")
            continue
        got = {r.get("id") for r in rows}
        if got != want:
            problems.append(f"{os.path.basename(fout)}: {len(want - got)} ids missing, {len(got - want)} unexpected")
            continue
        for r in rows:
            results[r["id"]] = r
    return results, problems


def resume_text():
    import pypdf
    reader = pypdf.PdfReader(config.RESUME_PATH)
    text = "\n".join(pg.extract_text(extraction_mode="layout") for pg in reader.pages)
    text = re.sub(r"[ \t]+", " ", text)
    os.makedirs(config.WORK_DIR, exist_ok=True)
    with open(os.path.join(config.WORK_DIR, "resume.txt"), "w") as f:
        f.write(text)
    return text


PROMPT_FIELDS = ("student", "keep_roles", "drop_roles")  # {{name}} in prompts/*.md, filled from PREFS


def render_prompt(name):
    """prompts/<name> with your preferences filled in, written to work/<name> for Claude to read. Returns its path.
    With the default preferences the text is exactly the original prompt."""
    with open(os.path.join(config.PROJECT_DIR, "prompts", name)) as f:
        text = f.read()
    for key in PROMPT_FIELDS:
        text = text.replace("{{" + key + "}}", str(PREFS[key]))
    os.makedirs(config.WORK_DIR, exist_ok=True)
    out = os.path.join(config.WORK_DIR, name)
    with open(out, "w") as f:
        f.write(text)
    return out


# ---------------------------------------------------------------- 2. triage

def cmd_triage_export():
    jobs, triage = load("list.json", {}), load("triage.json", {})
    todo = [{k: j[k] for k in ("id", "title", "company", "location", "snippet")} for i, j in jobs.items() if i not in triage]
    if not todo:
        print("Nothing to triage.")
        return []
    resume_text()
    files = write_batches("triage", todo, TRIAGE_BATCH)
    print(f"{len(todo)} jobs in {len(files)} batch files: work/triage_in_*.json (prompt: TRIAGE_PROMPT.md)")
    return files


def cmd_triage_import():
    results, problems = read_outputs("triage")
    bad = [i for i, r in results.items() if not isinstance(r.get("keep"), bool)]
    if problems or bad:
        sys.exit("Not imported:\n  " + "\n  ".join(problems + ([f"{len(bad)} rows without a true/false keep"] if bad else [])))
    triage = load("triage.json", {})
    triage.update({i: {"keep": r["keep"], "why": str(r.get("why", ""))[:200]} for i, r in results.items()})
    save("triage.json", triage)
    kept = sum(r["keep"] for r in results.values())
    print(f"Imported {len(results)} triage decisions: {kept} kept, {len(results) - kept} dropped.")


# ---------------------------------------------------------------- 3. details + hard rules

def slim(raw):
    """Keep only the fields the rules, the scorer and the model use."""
    return {
        "id": raw["job_id"],
        "title": (raw.get("job_title") or "").strip(),
        "company": (raw.get("job_emp") or {}).get("name") or raw.get("employer_name") or "",
        "position_types": labels(raw.get("job_type")),
        "term": labels(raw.get("el_work_term")) + labels(raw.get("screen_applicant_type")),
        "lengths": labels(raw.get("job_length_ms")),
        "class_levels": sorted(set(labels(raw.get("class_level")) + labels(raw.get("screen_class_level")))),
        "degree_levels": labels(raw.get("screen_degree_level")) + labels(raw.get("degree_level")),
        "majors": labels(raw.get("targeted_academic_majors")) + labels(raw.get("major")) + labels(raw.get("screen_major")),
        "states": [((l.get("state") or {}).get("_id") or "") for l in (raw.get("location") or []) if isinstance(l, dict)],
        "location": raw.get("job_location") or "",
        "skills": [s.get("skill_name") or s.get("_label") for s in (raw.get("skills") or []) if isinstance(s, dict)],
        "experience": labels(raw.get("desired_experience_level")),
        "function": labels(raw.get("job_function2")),
        "pay": f"{raw.get('compensation_from') or ''}-{raw.get('compensation_to') or ''} {(raw.get('compensation_frequency') or {}).get('_label') or ''}".strip(" -"),
        "deadline": raw.get("application_deadline"),
        "posting_end": (raw.get("job_dates") or {}).get("end") or raw.get("job_end"),
        "expired": bool(raw.get("expired")),
        "applied": bool(raw.get("applied")),
        "description": strip_html(raw.get("job_desc")),
        "qualifications": strip_html(raw.get("qualifications")),
        "fetched": date.today().isoformat(),
    }


def hard_rules(d, today=None):
    """Returns (keep, threshold, flags, reason). Pure function: see test_jobs.py."""
    today = (today or date.today()).isoformat()
    flags = []
    if d["applied"]:
        return False, None, flags, "already applied"
    if d["expired"]:
        return False, None, flags, "expired"
    for when in (d.get("deadline"), d.get("posting_end")):
        if when and str(when)[:10] < today:
            return False, None, flags, f"closed {str(when)[:10]}"
    types = set(d["position_types"])
    mine = RULES["term"] in d["term"] or (not d["term"] and TERM_TEXT in (d["title"] + " " + d["description"]).lower())
    if "Co-op" not in types and not ("Internship" in types and mine):
        return False, None, flags, f"position type {sorted(types) or 'none'}"
    if not mine:
        if d["term"]:
            return False, None, flags, f"term {d['term']}"
        flags.append("term unclear")
    elif OTHER_TERM.search(d["title"]):
        flags.append("title says other term")
    if d["lengths"] and not set(d["lengths"]) & RULES["lengths"]:
        return False, None, flags, f"length {d['lengths']}"
    if not d["lengths"]:
        flags.append("length unclear")
    if d["degree_levels"] and not any("undergraduate" in x.lower() for x in d["degree_levels"]):
        return False, None, flags, f"degree {d['degree_levels']}"
    levels = " ".join(d["class_levels"]).lower()
    listed = [i for i, w in enumerate(YEAR_WORDS) if w in levels]
    if not levels or any(i <= MY_YEAR for i in listed):  # open to your year (or names none)
        threshold = PREFS["threshold"]
    elif MY_YEAR + 1 in listed:  # its lowest year is the one after yours
        threshold = PREFS["threshold_above"]
        flags.append(f"{NEXT_YEAR}+")
    else:
        return False, None, flags, f"class level {d['class_levels']}"
    if d["majors"] and not any(w in m.lower() for m in d["majors"] for w in PREFS["major_words"]):
        names = list(dict.fromkeys(m.split("/")[-1].strip() for m in d["majors"]))  # "College/Major" -> "Major"
        more = f" +{len(names) - 3} more" if len(names) > 3 else ""
        flags.append(f"not your major (targets {', '.join(names[:3])}{more})")
    return True, threshold, flags, ""


def in_home_state(d):
    st = PREFS["home_state"]
    return bool(st) and (f"US-{st}" in d["states"] or f", {st}" in d["location"])


def cmd_details():
    from playwright.sync_api import sync_playwright
    jobs, triage, have = load("list.json", {}), load("triage.json", {}), load_details()
    todo = [i for i in jobs if triage.get(i, {}).get("keep") and i not in have]
    if not todo:
        print("No new details to fetch.")
    else:
        os.makedirs(path("details"), exist_ok=True)
        with sync_playwright() as p:
            browser, log, context, page, blocked = open_browser(p, "jobs_details")
            try:
                if not browser.goto_logged_in(page, context, config.NUWORKS_START_URL, log):
                    sys.exit("Not logged in. Run: nuauto login")
                for n, i in enumerate(todo, 1):
                    raw = None
                    for attempt in (1, 2):
                        r = page.request.get(f"{HOST}/api/v3/jobs/{i}")
                        try:
                            raw = r.json() if r.ok else None
                        except Exception:
                            raw = None
                        if raw and raw.get("job_id") == i:
                            break
                        raw = None
                        if attempt == 1 and not browser.goto_logged_in(page, context, config.NUWORKS_START_URL, log):
                            break
                    if raw is None:
                        log.write(f"Could not read job {i}; stopping. Run `nuauto login`, then rerun.")
                        break
                    with open(path("details", f"{i}.json"), "w") as f:
                        json.dump(slim(raw), f, indent=1)
                    if n % 25 == 0 or n == len(todo):
                        log.write(f"  details {n}/{len(todo)}")
                    browser.pause(page, 1.5, 3)
            except KeyboardInterrupt:
                log.write("Ctrl+C: stopping. Details fetched so far are saved.")
            finally:
                context.close()
    rules_summary()


def check_applied(ids):
    """Ask NUworks whether you applied to these jobs (e.g. by hand). Returns the ids it says yes for.
    Updates their saved details too."""
    from playwright.sync_api import sync_playwright
    ids, found = list(ids), []
    if not ids:
        return found
    with sync_playwright() as p:
        browser, log, context, page, blocked = open_browser(p, "check_applied")
        try:
            if not browser.goto_logged_in(page, context, config.NUWORKS_START_URL, log):
                sys.exit("Not logged in. Run: nuauto login")
            for i in ids:
                r = page.request.get(f"{HOST}/api/v3/jobs/{i}")
                try:
                    raw = r.json() if r.ok else None
                except Exception:
                    raw = None
                if raw and raw.get("job_id") == i:
                    with open(path("details", f"{i}.json"), "w") as f:
                        json.dump(slim(raw), f, indent=1)
                    if raw.get("applied"):
                        found.append(i)
                browser.pause(page, 1.5, 3)
            log.write(f"checked {len(ids)} jobs for an application: {len(found)} applied")
        finally:
            context.close()
    return found


def rules_summary():
    kept, dropped, reasons = 0, 0, {}
    for d in load_details().values():
        keep, _, _, why = hard_rules(d)
        kept += keep
        dropped += not keep
        if not keep:
            key = why.split(" [")[0].split(" 20")[0]
            reasons[key] = reasons.get(key, 0) + 1
    print(f"Hard rules: {kept} pass, {dropped} dropped {dict(sorted(reasons.items(), key=lambda x: -x[1]))}")


# ---------------------------------------------------------------- 4. score

def score_text(d):
    return d["description"] + ("\nQualifications:\n" + d["qualifications"] if d["qualifications"] else "")


def cmd_score_export(rescore_long=False, rescore_all=False):
    """New jobs only; with rescore_long, instead the already-scored jobs whose text was cut at OLD_TEXT_CAP."""
    scores = load("scores.json", {})
    todo = []
    for i, d in load_details().items():
        keep, _, _, _ = hard_rules(d)
        if rescore_all:
            want = True
        else:
            want = (i in scores and len(score_text(d)) > OLD_TEXT_CAP) if rescore_long else (i not in scores)
        if keep and want:
            todo.append({
                "id": i, "title": d["title"], "company": d["company"], "skills": d["skills"],
                "experience": d["experience"], "function": d["function"],
                "description": score_text(d)[:TEXT_CAP],
            })
    if not todo:
        print("Nothing to score.")
        return []
    resume_text()
    files = write_batches("score", todo, SCORE_BATCH)
    print(f"{len(todo)} jobs in {len(files)} batch files: work/score_in_*.json (prompt: SCORE_PROMPT.md)")
    return files


def cmd_score_import():
    results, problems = read_outputs("score")
    bad = [i for i, r in results.items() if not (isinstance(r.get("match"), int) and 0 <= r["match"] <= 100
                                                 and r.get("class_req") in CLASS_REQS)]
    if problems or bad:
        sys.exit("Not imported:\n  " + "\n  ".join(problems + ([f"{len(bad)} rows without an integer match 0-100 "
                                                               f"and a class_req in {sorted(CLASS_REQS)}"] if bad else [])))
    scores = load("scores.json", {})
    for i, r in results.items():
        scores[i] = {k: r.get(k) for k in ("match", "class_req", "met", "partial", "missing", "why")}
        scores[i]["scored"] = date.today().isoformat()
    save("scores.json", scores)
    print(f"Imported {len(results)} scores.")


# ---------------------------------------------------------------- 4b. category

def cmd_cat_export():
    details, scores, cats = load_details(), load("scores.json", {}), load("categories.json", {})
    todo = [{"id": i, "title": d["title"], "company": d["company"], "function": d["function"],
             "skills": d["skills"], "excerpt": d["description"][:600]}
            for i, d in details.items() if i in scores and i not in cats]
    if not todo:
        print("Nothing to categorize.")
        return []
    files = write_batches("cat", todo, CAT_BATCH)
    print(f"{len(todo)} jobs in {len(files)} batch files: work/cat_in_*.json (prompt: CATEGORY_PROMPT.md)")
    return files


def cmd_cat_import():
    results, problems = read_outputs("cat")
    bad = [i for i, r in results.items() if r.get("category") not in CATEGORIES]
    if problems or bad:
        sys.exit("Not imported:\n  " + "\n  ".join(problems + ([f"{len(bad)} rows with a category not in {sorted(CATEGORIES)}"] if bad else [])))
    cats = load("categories.json", {})
    cats.update({i: r["category"] for i, r in results.items()})
    save("categories.json", cats)
    from collections import Counter
    print(f"Imported {len(results)} categories: {dict(Counter(r['category'] for r in results.values()).most_common())}")


# ---------------------------------------------------------------- 5. pool

def pool_entry_rules(d, score, category=None):
    """hard_rules + what the scorer read in the text + role category. Returns (keep, threshold, flags)."""
    keep, threshold, flags, _ = hard_rules(d)
    if not keep:
        return False, None, flags
    flags = list(flags)
    if category in PREFS["category_threshold"] and threshold == PREFS["threshold"]:
        threshold = PREFS["category_threshold"][category]  # "above your year" still needs threshold_above
    req = CLASS_REQ_YEAR.get(score.get("class_req", "none"), -1)
    if req >= MY_YEAR + 2:
        return False, None, flags
    if req == MY_YEAR + 1:
        threshold = max(threshold, PREFS["threshold_above"])
        if f"{NEXT_YEAR}+" not in flags:
            flags.append(f"{NEXT_YEAR}+ (in text)")
    if any("required" in e.lower() for e in d["experience"]):
        flags.append("prior experience required")
    if FIRST_COOP.search(score_text(d)):
        flags.append("no first-time co-ops?")
    if len((score.get("met") or []) + (score.get("partial") or []) + (score.get("missing") or [])) < 4:
        flags.append("vague posting, low confidence")
    years = grad_years(score_text(d))
    if years:
        flags.append(f"mentions graduation {'/'.join(years)}")  # you graduate May 2029: check eligibility
    return True, threshold, flags


def category_bonus(cat):
    return PREFS["category_bonus"].get(cat, 0)


def pool_sort_key(r):
    """rank_last categories (full-stack/web) always last; otherwise by rank (match + home state + category bonus),
    or blended with taste."""
    base = r["rank"] / 100
    value = 0.5 * base + 0.5 * r["taste"] if "taste" in r else base
    return (r["category"] in PREFS["rank_last"], -value)


def score_parts(r):
    """What the job's ranking score (rank) is made of, as [(points, label)]: match first, then each bonus."""
    parts = [(r["match"], "% match"), (r["bonus"], " " + PREFS["home_label"]),
             (category_bonus(r["category"]), " " + r["category"].replace("_", " ")), (tag_bonus(r.get("tag")), f" {r.get('tag')}")]
    return [p for k, p in enumerate(parts) if k == 0 or p[0]]


def rank_text(r):
    """'103 = 73% match + 20 security + 10 MA', or just '73' with no bonuses."""
    parts = score_parts(r)
    if len(parts) == 1:
        return str(r["rank"])
    return f"{r['rank']} = " + " + ".join(f"{n}{label}" for n, label in parts)


def order_text(r, why):
    """Why a job sits where it does in Review. why: urgent | proposed | pool | rate."""
    if why == "urgent":
        return "closing within %d days (these come first, soonest first)" % URGENT_DAYS
    if why == "proposed":
        return "your Proposed row (these come next)"
    if why == "rate":
        return "rating order (takes turns between kinds of work)"
    if r["category"] in PREFS["rank_last"]:
        return f"{r['category'].replace('_', '/')}: always after the rest"
    if "taste" in r:
        taste = round(r["taste"] * 100)
        return f"half score, half taste: ({r['rank']} + {taste}) / 2 = {(r['rank'] + taste) / 2:g}"
    return "by score (taste counts once you have 5 yes and 5 no)"


def pool_entry(i, d, score, cat, threshold, flags):
    """One job as a pool row (what pool.json holds and the viewers show)."""
    bonus = PREFS["home_bonus"] if in_home_state(d) else 0
    match = score["match"]
    tag = job_tag(d)
    return {"id": i, "title": d["title"], "company": d["company"], "location": d["location"],
            "category": cat, "match": match, "bonus": bonus, "effective": match + bonus,
            "rank": match + bonus + category_bonus(cat) + tag_bonus(tag), "tag": tag,
            "threshold": threshold, "closes": (closes(d) or "") and closes(d).isoformat(),
            "flags": flags + (["uncategorized"] if cat == "uncategorized" else [])
                     + (["apply on company site too?"] if external_hint(d) else []),
            "why": score.get("why", "")}


def build_pool():
    details, scores, still_listed = load_details(), load("scores.json", {}), load("list.json", {})
    cats = load("categories.json", {})
    liked = {i for i, r in load("ratings.json", {}).items() if r.get("label") == 1}
    pool = []
    for i, d in details.items():
        if i not in scores or i not in still_listed:
            continue
        cat = cats.get(i, "uncategorized")
        keep, threshold, flags = pool_entry_rules(d, scores[i], cat)
        if not keep:
            continue
        match = scores[i]["match"]
        if i in liked and match < threshold:  # a job I rated yes stays even if a rescore dipped it below the bar
            flags = flags + ["kept: rated yes"]
            threshold = match
        if match >= threshold:  # entry on the real resume match; the home-state bonus only affects ranking
            pool.append(pool_entry(i, d, scores[i], cat, threshold, flags))
    pool.sort(key=pool_sort_key)
    return pool


def proposed_entries(rows, details):
    """Proposed sheet rows as pool-shaped entries (with "row"), for the approve queue: you put them there on purpose,
    so they show even below the pool bar (the flags say so). Returns (entries, rows_without_details_or_score)."""
    scores, cats = load("scores.json", {}), load("categories.json", {})
    entries, missing = [], []
    for r in rows:
        if r.status != "Proposed":
            continue
        i = job_id(r.url)
        if i not in details or i not in scores:
            missing.append(r)
            continue
        d, cat = details[i], cats.get(i, "uncategorized")
        keep, threshold, flags = pool_entry_rules(d, scores[i], cat)
        if not keep:
            threshold = PREFS["threshold"]
            flags = flags + ["outside your pool rules"]
        if scores[i]["match"] < threshold:
            flags = flags + [f"below the pool bar ({threshold}%)"]
        entry = pool_entry(i, d, scores[i], cat, threshold, flags + [f"already Proposed (sheet row {r.number})"])
        entry["row"] = r.number
        entries.append(entry)
    return entries, missing


def cmd_pool():
    pool = build_pool()
    save("pool.json", pool)
    for r in pool:
        flags = f"  [{', '.join(r['flags'])}]" if r["flags"] else ""
        print(f"{r['match']:3d}% (needs {r['threshold']}, rank {r['rank']}) "
              f"{r['category'][:13]:13s} {r['title']} | {r['company']} | {r['location']}{flags}")
    print(f"\nPool: {len(pool)} jobs.")


# ---------------------------------------------------------------- ranking model on top of the pool

def doc(d):
    return " ".join([d["title"]] * 3 + d["skills"] + d["function"] + [d["description"]])


def train(details, labels_):
    """(score_fn, explain_fn), or None until there are 5 yes and 5 no."""
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    ids = [i for i in labels_ if i in details]
    y = [labels_[i] for i in ids]
    if sum(y) < 5 or len(y) - sum(y) < 5:
        return None
    vec = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, stop_words="english", max_df=0.9)
    vec.fit([doc(details[i]) for i in details])
    clf = LogisticRegression(class_weight="balanced", C=2.0, max_iter=2000)
    clf.fit(vec.transform([doc(details[i]) for i in ids]), y)
    terms, coef = vec.get_feature_names_out(), clf.coef_[0]

    def score(job_ids):
        return dict(zip(job_ids, clf.predict_proba(vec.transform([doc(details[i]) for i in job_ids]))[:, 1]))

    def explain(i, k=5):
        row = vec.transform([doc(details[i])]).tocoo()
        return [t for w, t in sorted(((v * coef[c], terms[c]) for c, v in zip(row.col, row.data)), reverse=True)[:k] if w > 0]

    return score, explain


def my_labels(details, ratings, rows):
    """Sheet rows you approved count as yes; your explicit ratings override."""
    out = {job_id(r.url): 1 for r in rows if r.status in {"Approved", "Applied", "Needs Human", "Failed"} and job_id(r.url) in details}
    out.update({i: v["label"] for i, v in ratings.items() if i in details})
    return out


def ranked_pool(details, ratings, rows):
    """Pool order: match % until the model can train, then half match %, half your taste."""
    pool = build_pool()
    model = train(details, my_labels(details, ratings, rows))
    if model:
        taste = model[0]([r["id"] for r in pool]) if pool else {}
        for r in pool:
            r["taste"] = float(taste[r["id"]])
        pool.sort(key=pool_sort_key)
    return pool, model


def show(r, d):
    print("\n" + "=" * 72)
    print(f"{r['title']}  |  {r['company']}  |  {d['location']}  [{r['category']}]")
    print(f"  match {r['match']}%{' +' + str(r['bonus']) + ' ' + PREFS['home_label'] if r['bonus'] else ''} (needs {r['threshold']})"
          f"{'  [' + ', '.join(r['flags']) + ']' if r['flags'] else ''}  pay: {d['pay'] or '?'}")
    print(f"  why: {r['why']}")
    if d["skills"]:
        print(f"  skills: {', '.join(d['skills'])[:200]}")
    print("  " + d["description"][:500].replace("\n", " ") + ("..." if len(d["description"]) > 500 else ""))


RATE_ORDER = ["security", "embedded", "hardware", "systems", "robotics_test", "software",
              "data_ml", "it", "other", "fullstack_web", "uncategorized"]


def rating_order(pool):
    """Order jobs for rating so every category gets ratings, not just the top of the pool:
    one job from each category in turn (RATE_ORDER), best-first inside a category. Once the
    taste model exists, inside each category alternate its best job and the one it is least sure about."""
    from itertools import zip_longest
    groups = {}
    for r in pool:
        groups.setdefault(r["category"], []).append(r)
    queues = []
    for cat in RATE_ORDER + sorted(set(groups) - set(RATE_ORDER)):
        g = groups.get(cat)
        if not g:
            continue
        if all("taste" in r for r in g):
            unsure = sorted(g, key=lambda r: abs(r["taste"] - 0.5))
            seq, seen = [], set()
            for a, b in zip_longest(g, unsure):
                for r in (a, b):
                    if r is not None and r["id"] not in seen:
                        seen.add(r["id"])
                        seq.append(r)
            g = seq
        queues.append(list(g))
    out = []
    while any(queues):
        for q in queues:
            if q:
                out.append(q.pop(0))
    return out


HEADER_START = re.compile(
    r"^(about|overview|summary|responsibilit|what you|who you|your role|the role|role|key|duties|requirement|"
    r"required|preferred|qualification|minimum|basic|nice to have|bonus|skills|benefits|compensation|pay|perks|"
    r"why|our|we offer|education|experience|job|position|location|schedule|how to apply|additional|desired|"
    r"essential|day[- ]to[- ]day|what we|you will|you'll|you have|team|the opportunity|company|eligibility)",
    re.IGNORECASE)
BULLET = re.compile(r"^(?:[-•*·▪●◦]|\d{1,2}[.)])\s+")


def is_header(line):
    """Guess whether a description line is a section header ("Responsibilities:", "WHAT YOU'LL DO")."""
    s = line.strip()
    if not 2 < len(s) <= 70 or BULLET.match(s):
        return False
    if s.endswith(":"):
        return len(s.split()) <= 10
    if s[-1] in ".,;!?":
        return False
    words = s.split()
    if len(words) > 8:
        return False
    if s.isupper() and any(c.isalpha() for c in s):
        return True
    caps = sum(w[0].isupper() for w in words if w[0].isalpha())
    return bool(HEADER_START.match(s)) and caps >= max(1, len(words) // 2)


def job_lines(r, d, width):
    """The full job as display lines; each line is a list of (text, style) segments."""
    import textwrap
    out = []
    pad = " " * 10

    def labeled(label, text, style="text"):
        chunks = textwrap.wrap(str(text), max(10, width - 10)) or [""]
        out.append([(f"{label:<10}", "label"), (chunks[0], style)])
        out.extend([(pad, "text"), (c, style)] for c in chunks[1:])

    out.append([(r["title"], "title")])
    out.append([(r["company"], "company"), ("  ·  ", "dim"), (d["location"], "dim")])
    out.append([])
    tags = [(" " + r["category"] + " ", "tag:" + r["category"])]
    if r.get("tag"):
        tags += [("  ", "text"), (" " + r["tag"] + " ", "tag:xr")]
    out.append([(f"{'Type':<10}", "label")] + tags)
    margin = r["match"] - r["threshold"]
    out.append([(f"{'Match':<10}", "label"), (f"{r['match']}%", "good" if margin >= 15 else "ok"),
                (f"   needs {r['threshold']}%", "dim"), ("      Pay  ", "label"), (d["pay"] or "?", "text")])
    labeled("Score", rank_text(r) + (f"   ·   taste {round(r['taste'] * 100)}%" if "taste" in r else ""), "dim")
    day = closes(d)
    soon = day is not None and (day - date.today()).days <= URGENT_DAYS
    labeled("Closes", closes_text(day), "flag" if soon else "dim")
    hint = external_hint(d)
    if hint:
        labeled("Apply", "ALSO ON COMPANY SITE? \"" + hint + "\"", "flag")
    shown = [f for f in r["flags"] if f != "apply on company site too?"]  # the Apply line already says it
    if shown:
        labeled("Flags", " · ".join(shown), "flag")
    labeled("Why", r["why"], "why")
    if d["skills"]:
        labeled("Skills", ", ".join(d["skills"]), "dim")
    out.append([("─" * min(width, 80), "dim")])

    def body(text):
        prev = None
        for raw in str(text).split("\n"):
            s = raw.strip()
            if not s:
                continue
            if is_header(s):
                out.append([])
                out.append([("▍ ", "bar"), (s.rstrip(":").upper(), "header")])
                prev = "header"
            elif BULLET.match(s):
                m = BULLET.match(s)
                mark = m.group(0).strip()
                mark = "•" if not mark[0].isdigit() else mark
                chunks = textwrap.wrap(s[m.end():], max(10, width - 6)) or [""]
                out.append([(f"  {mark:<3} ", "bullet"), (chunks[0], "text")])
                out.extend([("      ", "text"), (c, "text")] for c in chunks[1:])
                prev = "bullet"
            else:
                if prev in ("text", "bullet"):
                    out.append([])
                out.extend([(c, "text")] for c in (textwrap.wrap(s, width) or [""]))
                prev = "text"

    body(d["description"])
    if d["qualifications"]:
        out.append([])
        out.append([("▍ ", "bar"), ("QUALIFICATIONS", "header")])
        body(d["qualifications"])
    out.append([])
    return out


def rate_viewer(todo, details, ratings, approved=None, urgent=()):
    """Full-screen pager. j/k scroll, space/b page, g/G top/bottom, y/n rate (overwrites), s skip, u back, o open, q quit.
    With `approved` (a list), y means "approve": the job is rated yes and appended to the list."""
    import curses

    def styles():
        curses.start_color()
        try:
            curses.use_default_colors()
            bg = -1
        except curses.error:
            bg = curses.COLOR_BLACK
        names = {"red": curses.COLOR_RED, "green": curses.COLOR_GREEN, "yellow": curses.COLOR_YELLOW,
                 "blue": curses.COLOR_BLUE, "magenta": curses.COLOR_MAGENTA, "cyan": curses.COLOR_CYAN}
        pair = {}
        for k, (n, col) in enumerate(names.items(), 1):
            curses.init_pair(k, col, bg)
            pair[n] = curses.color_pair(k)
        cat_color = {"security": pair["red"], "embedded": pair["green"], "hardware": pair["green"],
                     "systems": pair["green"], "robotics_test": pair["green"], "fullstack_web": curses.A_DIM}
        st = {"title": curses.A_BOLD | pair["yellow"], "company": pair["cyan"] | curses.A_BOLD,
              "dim": curses.A_DIM, "label": curses.A_BOLD, "text": curses.A_NORMAL, "why": pair["cyan"],
              "good": pair["green"] | curses.A_BOLD, "ok": pair["yellow"] | curses.A_BOLD,
              "flag": pair["magenta"], "bullet": pair["cyan"] | curses.A_BOLD, "bar": pair["blue"] | curses.A_BOLD,
              "header": pair["blue"] | curses.A_BOLD | curses.A_UNDERLINE, "tag:xr": pair["magenta"] | curses.A_REVERSE}

        def attr(style):
            if style.startswith("tag:") and style != "tag:xr":
                return cat_color.get(style[4:], pair["blue"]) | curses.A_REVERSE | curses.A_BOLD
            return st.get(style, curses.A_NORMAL)
        return attr

    def run(scr):
        curses.curs_set(0)
        scr.keypad(True)
        attr = styles()
        i, top = 0, 0
        while 0 <= i < len(todo):
            r = todo[i]
            h, w = scr.getmaxyx()
            lines = job_lines(r, details[r["id"]], max(20, w - 4))
            view = max(1, h - 1)
            top = max(0, min(top, len(lines) - view))
            scr.erase()
            for row, segs in enumerate(lines[top:top + view]):
                x = 1  # one-column left margin
                for text, style in segs:
                    if x >= w - 1:
                        break
                    scr.addnstr(row, x, text, w - 1 - x, attr(style))
                    x += len(text)
            mark = {1: "  rated: yes", 0: "  rated: no"}.get(ratings.get(r["id"], {}).get("label"), "")
            urgent_ids = [u["id"] for u in urgent]
            soon = f"  CLOSING SOON {urgent_ids.index(r['id']) + 1}/{len(urgent_ids)}" if r["id"] in urgent_ids else ""
            bar = (f" job {i + 1}/{len(todo)}{soon}  line {top + 1}/{len(lines)}{mark}  |  j/k scroll  space/b page  g/G"
                   f"  |  " + ("y APPROVE  n no" if approved is not None else "y yes  n no") + "  s skip  u back  o open  q quit ")
            scr.addnstr(h - 1, 0, bar.ljust(w - 1), w - 1, curses.A_REVERSE)
            scr.refresh()
            c = scr.getch()
            if c in (ord("j"), curses.KEY_DOWN):
                top += 1
            elif c in (ord("k"), curses.KEY_UP):
                top -= 1
            elif c in (ord(" "), curses.KEY_NPAGE, 4):   # 4 = Ctrl-D
                top += view - 1
            elif c in (ord("b"), curses.KEY_PPAGE, 21):  # 21 = Ctrl-U
                top -= view - 1
            elif c == ord("g"):
                top = 0
            elif c == ord("G"):
                top = len(lines)
            elif c in (ord("y"), ord("n")):
                ratings[r["id"]] = {"label": int(c == ord("y")), "date": date.today().isoformat()}
                save("ratings.json", ratings)
                if approved is not None:
                    if r in approved:
                        approved.remove(r)
                    if c == ord("y"):
                        approved.append(r)
                i, top = i + 1, 0
            elif c == ord("s"):
                i, top = i + 1, 0
            elif c == ord("u"):  # back one job; y/n there replaces the earlier answer
                i, top = max(0, i - 1), 0
            elif c == ord("o"):
                open_url(job_url(r["id"]))
            elif c == ord("q"):
                return

    try:
        curses.wrapper(run)
    except KeyboardInterrupt:
        pass  # every answer is already saved


def review_queue(mode, details, ratings, rows, today=None):
    """What Review shows, as (todo, urgent); used by `nuauto approve` / `nuauto rate` and the GUI.
    approve: your Proposed sheet rows (that have stored details; below the pool bar too), then pool jobs not in the
             sheet, none rated no; closing within URGENT_DAYS first (soonest first), then the usual ranking.
             A Proposed entry has "row" (y turns that row Approved instead of adding one).
    rate:    unrated pool jobs not in the sheet, in rating_order (urgent is empty)."""
    in_sheet = {job_id(r.url) for r in rows}
    pool, _ = ranked_pool(details, ratings, rows)
    if mode == "rate":
        return rating_order([r for r in pool if r["id"] not in ratings and r["id"] not in in_sheet]), []
    todo = [r for r in pool if r["id"] not in in_sheet and ratings.get(r["id"], {}).get("label") != 0]
    proposed = [r for r in proposed_entries(rows, details)[0] if ratings.get(r["id"], {}).get("label") != 0]
    todo = proposed + todo  # your own Proposed rows first (n skips one from now on, like any job)
    today = today or date.today()
    left = {r["id"]: (closes(details[r["id"]]) - today).days if closes(details[r["id"]]) else None for r in todo}
    urgent = sorted((r for r in todo if left[r["id"]] is not None and 0 <= left[r["id"]] <= URGENT_DAYS),
                    key=lambda r: left[r["id"]])
    return urgent + [r for r in todo if r not in urgent], urgent


def search_terms(q):
    """Review's search box as lowercase terms: words, or "quoted phrases" kept whole."""
    return [(a or b).lower() for a, b in re.findall(r'"([^"]+)"|(\S+)', str(q or "")) if (a or b).strip()]


def search_match(r, d, terms):
    """True if every term appears (any case) in the job's title, company, place, kind of work, tag, skills,
    description or qualifications."""
    if not terms:
        return True
    text = " ".join(str(x or "") for x in (r["title"], r["company"], d.get("location"), r["category"].replace("_", " "),
                                           r.get("tag"), " ".join(d.get("skills") or []), d.get("description"),
                                           d.get("qualifications"))).lower()
    return all(t in text for t in terms)


def text_blocks(text):
    """Description text as [{"kind": "header" | "bullet" | "text", "text": ...}], by the same rules as job_lines."""
    out = []
    for raw in str(text or "").split("\n"):
        s = raw.strip()
        if not s:
            continue
        m = BULLET.match(s)
        if is_header(s):
            out.append({"kind": "header", "text": s.rstrip(":")})
        elif m:
            out.append({"kind": "bullet", "text": s[m.end():]})
        else:
            out.append({"kind": "text", "text": s})
    return out


def job_view(r, d, today=None):
    """One pool job for the GUI's Review card: what job_lines shows in the terminal, as data."""
    day = closes(d)
    return {"id": r["id"], "url": job_url(r["id"]), "title": r["title"], "company": r["company"],
            "location": d["location"], "category": r["category"], "tag": r.get("tag"), "match": r["match"],
            "threshold": r["threshold"], "score": rank_text(r), "taste": r.get("taste"),
            "pay": d["pay"],
            "closes": day.isoformat() if day else None, "closes_text": closes_text(day, today),
            "soon": day is not None and (day - (today or date.today())).days <= URGENT_DAYS,
            "external": external_hint(d), "flags": [f for f in r["flags"] if f != "apply on company site too?"],
            "why": r["why"], "skills": d["skills"], "description": text_blocks(d["description"]),
            "qualifications": text_blocks(d["qualifications"])}


def sheet_item(r):
    """A pool job as a new sheet row (add_proposed)."""
    return {"url": job_url(r["id"]), "company": r["company"], "title": r["title"],
            "notes": f"match {r['match']}%" + (f" +{r['bonus']} {PREFS['home_label']}" if r["bonus"] else "")
                     + (f"; {', '.join(r['flags'])}" if r["flags"] else "")}


def cmd_rate():
    details, ratings = load_details(), load("ratings.json", {})
    rows = sheet.read_rows(sheet.open_worksheet())
    todo, _ = review_queue("rate", details, ratings, rows)
    if sys.stdin.isatty() and sys.stdout.isatty():
        rate_viewer(todo, details, ratings)
        yes = sum(v["label"] for v in ratings.values())
        print(f"Ratings: {yes} yes, {len(ratings) - yes} no.")
        return
    print(f"{len(todo)} unrated pool jobs. y = would apply, n = no, s = skip, o = open in browser, q = quit")
    for r in todo:
        show(r, details[r["id"]])
        while True:
            try:
                a = input("  [y/n/s/o/q] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                a = "q"
            if a == "o":
                open_url(job_url(r["id"]))
                continue
            break
        if a == "q":
            break
        if a in ("y", "n"):
            ratings[r["id"]] = {"label": int(a == "y"), "date": date.today().isoformat()}
            save("ratings.json", ratings)
    yes = sum(v["label"] for v in ratings.values())
    print(f"\nRatings: {yes} yes, {len(ratings) - yes} no.")


def cmd_suggest(n):
    details, ratings = load_details(), load("ratings.json", {})
    ws = sheet.open_worksheet()
    rows = sheet.read_rows(ws)
    in_sheet = {job_id(r.url) for r in rows}
    pool, model = ranked_pool(details, ratings, rows)
    top = [r for r in pool if r["id"] not in in_sheet and ratings.get(r["id"], {}).get("label") != 0][:n]
    if not top:
        sys.exit("Nothing new in the pool to suggest.")
    print("Ranking: " + ("match % + your ratings" if model else "match % only (rate 5 yes + 5 no to add your taste)"))
    for k, r in enumerate(top, 1):
        taste = f", taste {r['taste']:.2f}" if "taste" in r else ""
        flags = f" [{', '.join(r['flags'])}]" if r["flags"] else ""
        print(f"{k}. {r['effective']}%{taste} [{r['category']}]  {r['title']} | {r['company']} | {r['location']}{flags}\n     {r['why']}")
    try:
        ok = input(f"\nAdd these {len(top)} to the sheet as Proposed? [y/N] ").strip().lower() == "y"
    except EOFError:
        ok = False
    if not ok:
        print("Nothing added.")
        return
    print(f"Added {sheet.add_proposed(ws, [sheet_item(r) for r in top])} rows as Proposed.")


def cmd_approve():
    """Full-screen viewer over your Proposed rows and the best pool jobs not in the sheet (rated-no skipped): y approves (the row becomes Approved, or is added as Approved), n rejects."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        sys.exit("approve needs an interactive terminal.")
    details, ratings = load_details(), load("ratings.json", {})
    ws = sheet.open_worksheet()
    rows = sheet.read_rows(ws)
    # your Proposed rows, then everything not in the sheet that you haven't rated no; closing soon first
    todo, urgent = review_queue("approve", details, ratings, rows)
    left_out = proposed_entries(rows, details)[1]
    if left_out:
        print("Proposed rows with no stored details (not shown; open the link): "
              + ", ".join(f"row {r.number} {r.company}" for r in left_out))
    if not todo:
        sys.exit("Nothing new in the pool to approve.")
    if urgent:
        print(f"{len(urgent)} job{'s' if len(urgent) > 1 else ''} closing within {URGENT_DAYS} days shown first.")
    try:
        week, total = sheet.check_limits(rows)
        print(f"Applied {sheet.week_window()[1]}: {week}/{sheet.max_per_week()}. Total: {total}/{sheet.MAX_TOTAL}.")
    except sheet.LimitReached as e:
        print(f"Note: {e} Approving is still fine; apply.py will refuse until the limit clears.")
    approved = []
    try:
        rate_viewer(todo, details, ratings, approved, urgent)
    finally:  # runs on q, Ctrl+C or a crash: whatever you approved gets written
        if approved:
            for r in (r for r in approved if r.get("row")):
                try:
                    sheet.approve_proposed(ws, r["row"], job_url(r["id"]))
                    print(f"Row {r['row']} ({r['company']}) is now Approved.")
                except sheet.SheetError as e:
                    print(f"Not approved: {e}")
            items = [sheet_item(r) for r in approved if not r.get("row")]
            if items:
                print(f"Added {sheet.add_proposed(ws, items, status='Approved')} rows as Approved.")
        else:
            print("Nothing approved.")
    print("Next: nuauto apply")


def cmd_stats():
    jobs, triage, details, scores = load("list.json", {}), load("triage.json", {}), load_details(), load("scores.json", {})
    passing = sum(hard_rules(d)[0] for d in details.values())
    ratings = load("ratings.json", {})
    print(f"Listed {len(jobs)} | triaged {len(triage)} (kept {sum(t['keep'] for t in triage.values())}) | "
          f"details {len(details)} (pass rules {passing}) | scored {len(scores)} | pool {len(build_pool())} | "
          f"ratings {sum(v['label'] for v in ratings.values())} yes / {sum(1 - v['label'] for v in ratings.values())} no")


def main():
    args = sys.argv[1:]
    cmds = {"list": cmd_list, "triage-export": cmd_triage_export, "triage-import": cmd_triage_import,
            "details": cmd_details, "score-export": cmd_score_export, "score-import": cmd_score_import,
            "pool": cmd_pool, "rate": cmd_rate, "approve": cmd_approve, "stats": cmd_stats,
            "score-export-long": lambda: cmd_score_export(rescore_long=True),
            "score-export-all": lambda: cmd_score_export(rescore_all=True),  # after a resume change
            "cat-export": cmd_cat_export, "cat-import": cmd_cat_import}
    if len(args) == 1 and args[0] in cmds:
        cmds[args[0]]()
    elif args[:1] == ["suggest"]:
        cmd_suggest(int(args[1]) if len(args) == 2 and args[1].isdigit() else 5)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
