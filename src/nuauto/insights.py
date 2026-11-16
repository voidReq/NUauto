"""Insights: what the jobs in your pool and the ones you applied to pay, where they are, what kind of work they
are, and where your applications stand: the NUworks co-ops (data/, the sheet's NUworks tab) and, when they are on, the
Summer internships apart (data/intern/, the sheet's Other jobs tab). Read-only.

The GUI picks one set of jobs (SETS: your pool, or sent rows by status) and narrows it by tags the jobs carry (facets:
Massachusetts, remote, a kind of work, one of your tags), several at once (my request, 2026-11-16).

  nuauto insights        the same numbers as the GUI's Insights screen, in the terminal

Pay is NUworks' own "pay" text ("$24-$30 per hour"). Yearly pay of $1,000 or more becomes hourly (/ 2080 hours);
anything else that doesn't read as $10-$150 an hour counts as not listed, never as a guess.
"""
import re
import statistics
import sys
from collections import Counter
from datetime import date

from nuauto import jobs

PAY_BUCKETS = [(0, 20, "< $20"), (20, 25, "$20–25"), (25, 30, "$25–30"), (30, 35, "$30–35"),
               (35, 40, "$35–40"), (40, 1e9, "$40+")]
HOURS_PER_YEAR = 2080
STATES = {"AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
          "CT": "Connecticut", "DE": "Delaware", "DC": "Washington DC", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
          "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky",
          "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
          "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
          "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
          "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
          "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
          "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "PR": "Puerto Rico"}
TOP_PLACES = 5
TOP_SKILLS = 20
TOP_FUNCTIONS = 8
# Had an interview: Interview, a second round (Interview 2) or Accepted (an accepted one is counted as having had one)
INTERVIEWED = ("Interview", "Interview 2", "Accepted")
# The sets of jobs Insights picks from: (key, label, the sheet statuses in it; None: the pool, "sent": every sent row)
SETS = [("pool", "Your pool", None), ("applied", "Applied", "sent"), ("interview", "Interview", INTERVIEWED),
        ("interview2", "Interview 2", ("Interview 2",)), ("accepted", "Accepted", ("Accepted",)),
        ("rejected", "Rejected", ("Rejected",))]
ALWAYS_SHOWN = ("pool", "applied")  # the other sets show once they hold a job
_NUM = r"\$?\s*(\d[\d,]*(?:\.\d+)?)\s*(k)?"
PAY_RE = re.compile(_NUM + r"(?:\s*(?:-|–|to)\s*" + _NUM + r")?.*?\b(?:per|an|a|/)\s*(hour|hr|year|yr)", re.I)


def hourly(text):
    """(low, high) dollars an hour from a pay text, or None if it isn't clear."""
    m = PAY_RE.search(str(text or ""))
    if not m:
        return None
    lo = float(m.group(1).replace(",", "")) * (1000 if m.group(2) else 1)
    hi = float(m.group(3).replace(",", "")) * (1000 if m.group(4) else 1) if m.group(3) else lo
    if m.group(5).lower() in ("year", "yr"):
        if lo < 1000:
            return None  # "26-37 per year" is a mislabeled hourly rate, or nothing: not a guess either way
        lo, hi = lo / HOURS_PER_YEAR, hi / HOURS_PER_YEAR
    lo, hi = min(lo, hi), max(lo, hi)
    return (round(lo, 2), round(hi, 2)) if 10 <= lo and hi <= 150 else None


def place(location):
    """(city, state code) from "Boston, MA, USA" -> ("Boston", "MA"). Remote, several places or outside the US:
    (that label, None)."""
    text = str(location or "").strip()
    if not text:
        return "Not listed", None
    if re.search(r"\bremote\b", text, re.I):
        return "Remote", None
    if re.search(r"multiple|various", text, re.I):
        return "Multiple locations", None
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if parts and parts[-1].upper() in ("USA", "US", "UNITED STATES"):
        parts = parts[:-1]
    if len(parts) >= 2 and parts[-1].upper() in STATES:
        return parts[-2], parts[-1].upper()
    return (", ".join(parts) or "United States"), None


def place_text(location):
    """What a search matches a job's place on: the posting's own text plus each US state's full name ("Richmond, VA"
    also finds "virginia"); several places split on ";" (the internship lists)."""
    text = str(location or "").strip()
    states = {STATES[s] for _, s in (place(p) for p in text.split(";")) if s}
    return " ".join([text, *sorted(states)]).strip()


def states_of(d):
    """US state codes a saved posting is in: its "states" ("US-MA") and each of its places (several split on ";")."""
    codes = {s[3:] for s in d.get("states") or [] if str(s).startswith("US-")}
    return codes | {s for _, s in (place(p) for p in str(d.get("location") or "").split(";")) if s}


def tag_of(d):
    """The name of your interest tag (jobs.job_tag) a saved posting matches, or None. A posting may lack parts: an
    internship's has no skills, a job NUauto holds nothing on is {}."""
    return jobs.job_tag({"title": d.get("title") or "", "description": d.get("description") or "",
                         "qualifications": d.get("qualifications") or "", "skills": d.get("skills") or []})


def facets(d, cat):
    """The tags a job carries that narrow Insights: "ma" (any of its places in Massachusetts), "remote", "cat:<kind of
    work>", "tag:<your tag>"."""
    keys = {"ma"} if "MA" in states_of(d) else set()
    if re.search(r"\bremote\b", str(d.get("location") or ""), re.I):
        keys.add("remote")
    if cat:
        keys.add("cat:" + (cat if cat in jobs.CATEGORIES else "other"))
    tag = tag_of(d) if d else None
    if tag:
        keys.add("tag:" + tag)
    return keys


def facet_order():
    """Every facet key that can exist, in the order the chips show: places, your kinds of work, your tags."""
    return ["ma", "remote", *(f"cat:{c}" for c in [*jobs.CATEGORIES, "other"] if c), *(f"tag:{t['name']}" for t in jobs.PREFS["tags"])]


def facet_label(key):
    if key == "ma":
        return "Massachusetts"
    if key == "remote":
        return "Remote"
    kind, _, name = key.partition(":")
    return jobs.category_label(name) if kind == "cat" else name


def counted(counter, top=None, order=None):
    """[{"label", "count"}], biggest first (or in `order`); past `top`, the rest folds into one "Other"."""
    items = [(k, counter[k]) for k in order if counter.get(k)] if order else counter.most_common()
    if top and len(items) > top + 1:  # one "Other" at the end, holding the real "Other" too
        other = counter.get("Other", 0) if not order else 0
        items = [x for x in items if order or x[0] != "Other"]
        items = items[:top] + [("Other", other + sum(n for _, n in items[top:]))]
    return [{"label": k, "count": n} for k, n in items]


def group(entries, today):
    """Numbers for one set of jobs: [(details or {}, category or None)]."""
    pays = [p for p in (hourly(d.get("pay")) for d, _ in entries) if p]
    mids = [(lo + hi) / 2 for lo, hi in pays]
    buckets = Counter(next(label for a, b, label in PAY_BUCKETS if a <= m < b) for m in mids)
    places, cities = Counter(), Counter()  # places: a state's name, or Remote / Multiple locations / abroad
    for d, _ in entries:
        label, state = place(d.get("location"))
        places[STATES[state] if state else label] += 1
        if state:
            cities[f"{label}, {state}"] += 1
    cats = Counter(jobs.category_label(c) if c in jobs.CATEGORIES else jobs.category_label("other") for _, c in entries if c)
    # each job counts once per skill / function, however often it lists it
    skills = Counter(s for d, _ in entries for s in set(d.get("skills") or []) if s)
    functions = Counter(f for d, _ in entries for f in set(d.get("function") or []) if f)
    tags = Counter(t for d, _ in entries for t in [tag_of(d)] if t)
    years = Counter(d["year_asked"] for d, _ in entries if d.get("year_asked"))
    closing = [jobs.closes(d) for d, _ in entries if d]
    week = sum(1 for c in closing if c and 0 <= (c - today).days <= 7)
    return {
        "count": len(entries),
        "pay": {"listed": len(pays), "median": round(statistics.median(mids), 2) if mids else None,
                "low": min(lo for lo, _ in pays) if pays else None, "high": max(hi for _, hi in pays) if pays else None,
                # the middle half of the jobs (their range midpoints): what "usual" pay looks like, without outliers
                "mid_low": round(statistics.quantiles(mids, n=4)[0], 2) if len(mids) >= 4 else None,
                "mid_high": round(statistics.quantiles(mids, n=4)[2], 2) if len(mids) >= 4 else None,
                "buckets": [{"label": label, "count": buckets.get(label, 0)} for _, _, label in PAY_BUCKETS]},
        "places": counted(places, top=TOP_PLACES),
        "cities": counted(cities)[:3],
        "in_ma": sum(1 for d, _ in entries if "MA" in states_of(d)),  # any of its places: the Massachusetts tag's count
        "states": sum(1 for k in places if k in STATES.values()),
        "categories": counted(cats, top=8),
        "skills": counted(skills)[:TOP_SKILLS],  # no folded "Other": a skill the top leaves out is not one kind of thing
        "functions": counted(functions)[:TOP_FUNCTIONS],
        "tags": counted(tags),
        "years": counted(years, order=list(jobs.YEAR_ASKED.values())),  # jobs asking for a year above yours
        "closing_week": week,
    }


def interview_rate(statuses):
    """{interviews, second, sent, percent} from a Counter of sheet statuses: INTERVIEWED rows over every sent row;
    second: the rows at a second round (Interview 2)."""
    from nuauto import sheet
    sent = sum(statuses[s] for s in sheet.SENT)
    n = sum(statuses[s] for s in INTERVIEWED)
    return {"interviews": n, "second": statuses["Interview 2"], "sent": sent, "percent": round(100 * n / sent) if sent else None}


def summarize(pool, details, rows, categories=None, today=None, id_of=None, scores=None, pick="pool", only=()):
    """Everything the Insights screen and `nuauto insights` show. pool: data/pool.json entries; details: {id: job
    data}; rows: sheet.Row list (or None when the sheet can't be read); categories: {id: category} for jobs the pool
    no longer lists; id_of: a row's link -> its job id (default: NUworks' own, jobs.job_id); scores: {id: the scorer's
    reading} (the class year a description asks for).
    pick: a SETS key; only: facet keys. "view" holds the numbers for the picked set's jobs carrying every one of them;
    "sets" and "facets" are the screen's chips: each with how many jobs it would show next to what is already chosen
    (a facet that would show none is left out, unless it is chosen)."""
    from nuauto import sheet
    today = today or date.today()
    categories = categories or {}
    in_pool = {r["id"]: r for r in pool}

    def entry(job_id):
        cat = (in_pool.get(job_id) or {}).get("category") or categories.get(job_id)
        d = details.get(job_id) or {}
        ask = jobs.year_asked(d, (scores or {}).get(job_id))
        d = {**d, "year_asked": ask} if ask else d
        return d, cat, facets(d, cat)

    sent = [(r.status, entry((id_of or jobs.job_id)(r.url))) for r in rows or [] if r.status in sheet.SENT]
    sets = {key: [e for status, e in sent if holds == "sent" or status in holds] if holds else [entry(r["id"]) for r in pool]
            for key, _, holds in SETS}
    pick = pick if pick in sets else "pool"
    known = {k for e in sets["pool"] + sets["applied"] for k in e[2]}
    only = [k for k in facet_order() if k in set(only or ()) and k in known]
    match = lambda e, keys: set(keys) <= e[2]
    jobs_of = lambda es: [(d, cat) for d, cat, _ in es]
    statuses = Counter(r.status for r in rows or [] if r.status)
    return {
        "pool": group(jobs_of(sets["pool"]), today),
        "applied": group(jobs_of(sets["applied"]), today),
        "pick": pick, "only": only,
        "view": group(jobs_of([e for e in sets[pick] if match(e, only)]), today),
        "sets": [{"key": key, "label": label, "count": sum(1 for e in sets[key] if match(e, only))}
                 for key, label, _ in SETS if key in ALWAYS_SHOWN or key == pick or sets[key]],
        "facets": [f for f in ({"key": k, "label": facet_label(k), "on": k in only,
                                "count": sum(1 for e in sets[pick] if match(e, {*only, k}))} for k in facet_order())
                   if f["on"] or f["count"]],
        "statuses": None if rows is None else counted(statuses, order=sheet.STATUSES),
        "rows": None if rows is None else sum(statuses.values()),
        # interviews: Interview, Interview 2 and Accepted rows (an accepted one counts as having had one) over all sent rows
        "interviews": None if rows is None else interview_rate(statuses),
    }


def collect(rows, pick="pool", only=()):
    """summarize() on this machine's data/ (the NUworks co-ops; rows: the sheet's NUworks tab)."""
    return summarize(jobs.load("pool.json", []), jobs.load_details(), rows, jobs.load("categories.json", {}),
                     scores=jobs.load("scores.json", {}), pick=pick, only=only)


def collect_intern(rows, pick="pool", only=()):
    """summarize() on the Summer internships (data/intern/; rows: the sheet's Other jobs tab, matched to the lists'
    postings by link: an Other job that is on no list counts, with nothing known about it)."""
    from nuauto import intern
    by_url = {x["url"]: i for i, x in intern.load("listings.json", {}).items()}
    return summarize(intern.load("pool.json", []), intern.load_details(), rows, intern.load("categories.json", {}),
                     id_of=lambda url: by_url.get(intern.clean_url(url)), scores=intern.load("scores.json", {}),
                     pick=pick, only=only)


def money(x):
    return "—" if x is None else f"${x:,.0f}" if x >= 100 or x == int(x) else f"${x:,.2f}"


def bar_lines(items, width=24):
    top = max([i["count"] for i in items] + [1])
    pad = max([len(i["label"]) for i in items] + [0])
    return [f"  {i['label']:<{pad}}  {'█' * max(1 if i['count'] else 0, round(width * i['count'] / top)):<{width}} {i['count']}"
            for i in items]


def text(s):
    """The terminal version: short, one block per question."""
    out = []
    if s["statuses"] is not None:
        out.append(f"Your sheet: {s['rows']} rows")
        out += bar_lines(s["statuses"])
        iv = s["interviews"]
        if iv and iv["sent"]:
            second = f", {iv['second']} at a second round" if iv["second"] else ""
            out.append(f"  Interviews: {iv['interviews']} of {iv['sent']} sent ({iv['percent']}%){second}")
        out.append("")
    for key, name in (("pool", "Your pool"), ("applied", "Applied")):
        g = s[key]
        out.append(f"{name}: {g['count']} job{'s' if g['count'] != 1 else ''}")
        if not g["count"]:
            out.append("")
            continue
        p = g["pay"]
        if p["listed"]:
            usual = f", most {money(p['mid_low'])}–{money(p['mid_high'])}" if p["mid_low"] is not None else ""
            out.append(f"  Pay: median {money(p['median'])}/h{usual}, all {money(p['low'])}–{money(p['high'])} "
                       f"({p['listed']} of {g['count']} list pay)")
            out += ["  " + line for line in bar_lines(p["buckets"], 16)]
        else:
            out.append("  Pay: none listed")
        out.append(f"  Where: {g['in_ma']} in Massachusetts, {g['states']} state{'s' if g['states'] != 1 else ''}")
        out += ["  " + line for line in bar_lines(g["places"], 16)]
        if g["cities"]:
            out.append("  Top cities: " + ", ".join(f"{c['label']} {c['count']}" for c in g["cities"]))
        if g["categories"]:
            out.append("  Kind of work:")
            out += ["  " + line for line in bar_lines(g["categories"], 16)]
        for label, items in (("Skills", g["skills"][:10]), ("Job function", g["functions"][:5]), ("Your tags", g["tags"]),
                             ("Asks for a year above yours", g["years"])):
            if items:
                out.append(f"  {label}: " + ", ".join(f"{i['label']} {i['count']}" for i in items))
        if key == "pool":
            out.append(f"  Closing within 7 days: {g['closing_week']}")
        out.append("")
    return "\n".join(out).rstrip()


def main(argv=None):
    from nuauto import intern
    from nuauto import sheet

    def read(tab):
        try:
            return sheet.read_rows(sheet.open_other() if tab == "other" else sheet.open_worksheet())
        except Exception as e:  # the pool numbers still work without the sheet
            print(f"(sheet's {tab} tab not read: {type(e).__name__}; pool numbers only)", file=sys.stderr)
            return None
    print(text(collect(read("nuworks"))))
    if intern.ENABLED:
        print(f"\n{intern.TERM} internships (the Other jobs tab)\n")
        print(text(collect_intern(read("other"))))


if __name__ == "__main__":
    main(sys.argv[1:])
