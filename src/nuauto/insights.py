"""Insights: what the jobs in your pool and the ones you applied to pay, where they are, what kind of work they
are, and where your applications stand. Read-only: from data/ (pool, job details) and the sheet's rows.

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

CATEGORY_LABELS = {"security": "Security", "embedded": "Embedded", "hardware": "Hardware", "systems": "Systems",
                   "robotics_test": "Robotics / test", "software": "Software", "data_ml": "Data / ML", "it": "IT",
                   "fullstack_web": "Web / full-stack", "other": "Other"}
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
    cats = Counter(CATEGORY_LABELS.get(c, "Other") for _, c in entries if c)
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
        "in_ma": places.get("Massachusetts", 0),
        "states": sum(1 for k in places if k in STATES.values()),
        "categories": counted(cats, top=8),
        "closing_week": week,
    }


def summarize(pool, details, rows, categories=None, today=None):
    """Everything the Insights screen and `nuauto insights` show. pool: data/pool.json entries; details: {id: job
    data}; rows: sheet.Row list (or None when the sheet can't be read); categories: {id: category} for jobs the pool
    no longer lists."""
    from nuauto import sheet
    today = today or date.today()
    categories = categories or {}
    in_pool = {r["id"]: r for r in pool}

    def entry(job_id):
        cat = (in_pool.get(job_id) or {}).get("category") or categories.get(job_id)
        return details.get(job_id) or {}, cat

    applied = [entry(jobs.job_id(r.url)) for r in rows or [] if r.status == "Applied"]
    statuses = Counter(r.status for r in rows or [] if r.status)
    return {
        "pool": group([entry(r["id"]) for r in pool], today),
        "applied": group(applied, today),
        "statuses": None if rows is None else counted(statuses, order=sheet.STATUSES),
        "rows": None if rows is None else sum(statuses.values()),
    }


def collect(rows):
    """summarize() on this machine's data/."""
    return summarize(jobs.load("pool.json", []), jobs.load_details(), rows, jobs.load("categories.json", {}))


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
        if key == "pool":
            out.append(f"  Closing within 7 days: {g['closing_week']}")
        out.append("")
    return "\n".join(out).rstrip()


def main(argv=None):
    from nuauto import sheet
    try:
        rows = sheet.read_rows(sheet.open_worksheet())
    except Exception as e:  # the pool numbers still work without the sheet
        print(f"(sheet not read: {type(e).__name__}; pool numbers only)", file=sys.stderr)
        rows = None
    print(text(collect(rows)))


if __name__ == "__main__":
    main(sys.argv[1:])
