"""Offline checks for insights.py (the Insights numbers: pay, places, kinds of work, sheet statuses).
Run: python tests/test_insights.py
"""
from datetime import date, timedelta

from nuauto import insights, jobs, sheet

TODAY = date(2026, 10, 6)

# pay text -> dollars an hour; anything unclear is "not listed", never a guess
for text, want in [("$24-$30 per hour", (24, 30)), ("25-25 per hour", (25, 25)), ("20.90-37.30 per hour", (20.9, 37.3)),
                   ("$25 an hour", (25, 25)), ("$62,400-$72,800 per year", (30, 35)), ("55k-65k per year", (26.44, 31.25)),
                   ("26-37 per year", None), ("", None), (None, None), ("Unpaid", None), ("Competitive", None),
                   ("5-8 per hour", None), ("30-500 per hour", None), ("$30-$25 per hour", (25, 30))]:
    assert insights.hourly(text) == want, (text, insights.hourly(text), want)

# location -> (city, US state code); remote, several places and abroad keep a label and no state
for text, want in [("Boston, MA, USA", ("Boston", "MA")), ("Bethpage, NY, USA", ("Bethpage", "NY")),
                   ("Remote, USA", ("Remote", None)), ("Multiple Locations", ("Multiple locations", None)),
                   ("United States", ("United States", None)), ("London, UK", ("London, UK", None)),
                   ("", ("Not listed", None)), (None, ("Not listed", None))]:
    assert insights.place(text) == want, (text, insights.place(text))


def job(i, pay, location, closes_in=30):
    return {"id": i, "pay": pay, "location": location, "deadline": (TODAY + timedelta(days=closes_in)).isoformat()}


details = {
    "1": job("1", "$24-$30 per hour", "Boston, MA, USA", 3),
    "2": job("2", "$30-$34 per hour", "Cambridge, MA, USA"),
    "3": job("3", "$18-$18 per hour", "Austin, TX, USA", 6),
    "4": job("4", "", "Remote, USA"),
    "5": job("5", "$41-$45 per hour", "Nashua, NH, USA", -2),  # closed already: not "closing this week"
    "6": job("6", "$26-$28 per hour", "Boston, MA, USA"),
}
pool = [{"id": "1", "category": "security"}, {"id": "2", "category": "embedded"}, {"id": "3", "category": "other"},
        {"id": "4", "category": "software"}, {"id": "5", "category": "security"}]
# skills / functions / your tags: a job counts once per skill; a job with none adds nothing (job 4, 5, 6 here)
details["1"].update(skills=["Python", "Python", "C"], function=["Software Development"], title="XR firmware",
                    description="Build augmented reality headset firmware.")
details["2"].update(skills=["Python"], function=["Software Development", "Data Analysis"], description="Wearable sensors.")
details["3"].update(skills=["Excel"])
row = lambda n, i, status: sheet.Row(n, jobs.job_url(i), f"Co {i}", "Co-op", status, "", "")
rows = [row(2, "1", "Applied"), row(3, "6", "Applied"), row(4, "2", "Approved"), row(5, "9", "Needs Human"),
        row(6, "7", "Proposed"), row(7, "8", "Applied")]  # 8: no saved job data at all

s = insights.summarize(pool, details, rows, categories={"6": "hardware"}, today=TODAY)
p = s["pool"]
assert p["count"] == 5 and p["pay"]["listed"] == 4 and p["pay"]["low"] == 18 and p["pay"]["high"] == 45
assert p["pay"]["median"] == 29.5, p["pay"]  # midpoints 27, 32, 18, 43
assert p["pay"]["mid_low"] is not None and p["pay"]["mid_low"] <= p["pay"]["median"] <= p["pay"]["mid_high"]
assert {b["label"]: b["count"] for b in p["pay"]["buckets"]} == {"< $20": 1, "$20–25": 0, "$25–30": 1, "$30–35": 1,
                                                                 "$35–40": 0, "$40+": 1}
assert [b["label"] for b in p["pay"]["buckets"]] == [b[2] for b in insights.PAY_BUCKETS], "every bucket, in order"
assert p["in_ma"] == 2 and p["states"] == 3 and p["closing_week"] == 2
assert p["places"][0] == {"label": "Massachusetts", "count": 2}
assert {x["label"] for x in p["places"]} == {"Massachusetts", "Texas", "Remote", "New Hampshire"}
assert len(p["cities"]) == 3 and all(c["count"] == 1 for c in p["cities"])  # at most 3 top cities
assert p["categories"][0] == {"label": "Security", "count": 2}

assert p["skills"] == [{"label": "Python", "count": 2}, {"label": "C", "count": 1}, {"label": "Excel", "count": 1}], p["skills"]
assert p["functions"] == [{"label": "Software Development", "count": 2}, {"label": "Data Analysis", "count": 1}]
assert p["tags"] == [{"label": "AR/XR", "count": 1}, {"label": "wearables", "count": 1}], p["tags"]
assert insights.tag_of({}) is None and insights.tag_of({"skills": None, "description": None}) is None  # a posting may lack parts
ys = insights.summarize(pool, {"1": {"class_levels": ["Junior", "Senior"]}, "2": {}, "3": {}}, None, today=TODAY,
                        scores={"2": {"class_req": "senior_plus"}, "3": {"class_req": "none"}})["pool"]["years"]
assert ys == [{"label": "Juniors+", "count": 1}, {"label": "Seniors+", "count": 1}], ys  # in year order; none for job 3
many = insights.group([({"skills": [f"S{n}"]}, None) for n in range(40)], TODAY)
assert len(many["skills"]) == insights.TOP_SKILLS and all(x["label"] != "Other" for x in many["skills"])

a = s["applied"]  # rows 2, 3, 7: job 8 has no details (counted, no pay, "Not listed"); job 6 gets its category from
assert a["count"] == 3 and a["pay"]["listed"] == 2 and a["in_ma"] == 2  # categories.json (no longer in the pool)
assert {"label": "Not listed", "count": 1} in a["places"]
assert {x["label"] for x in a["categories"]} == {"Security", "Hardware"}

# statuses: in the sheet's order, empty ones left out
assert s["statuses"] == [{"label": "Proposed", "count": 1}, {"label": "Approved", "count": 1},
                         {"label": "Applied", "count": 3}, {"label": "Needs Human", "count": 1}] and s["rows"] == 6

# no sheet: pool numbers only
s2 = insights.summarize(pool, details, None, today=TODAY)
assert s2["statuses"] is None and s2["applied"]["count"] == 0 and s2["pool"]["count"] == 5

# folding: past `top`, the rest (and a real "Other") become one "Other" at the end
from collections import Counter
c = insights.counted(Counter({"A": 9, "Other": 4, "B": 3, "C": 2, "D": 1}), top=2)
assert c == [{"label": "A", "count": 9}, {"label": "B", "count": 3}, {"label": "Other", "count": 7}], c
assert insights.counted(Counter({"A": 2, "B": 1}), top=2) == [{"label": "A", "count": 2}, {"label": "B", "count": 1}]

# what you heard back still counts as applied (Accepted / Rejected); the statuses list them in the sheet's order
s3 = insights.summarize(pool, details, [row(2, "1", "Applied"), row(3, "6", "Rejected"), row(4, "2", "Accepted")], today=TODAY)
assert s3["applied"]["count"] == 3, s3["applied"]
assert [x["label"] for x in s3["statuses"]] == ["Applied", "Accepted", "Rejected"], s3["statuses"]

# interviews (Interview, and Accepted over it) of all sent rows; the ring lists Interview in the sheet's order
s4 = insights.summarize(pool, details, [row(2, "1", "Applied"), row(3, "6", "Interview"), row(4, "2", "Accepted"), row(5, "3", "Rejected")], today=TODAY)
assert [x["label"] for x in s4["statuses"]] == ["Applied", "Interview", "Accepted", "Rejected"], s4["statuses"]
assert s4["applied"]["count"] == 4 and s4["interviews"] == {"interviews": 2, "sent": 4, "percent": 50}, s4["interviews"]
assert "Interviews: 2 of 4 sent (50%)" in insights.text(s4)
assert insights.summarize(pool, details, [], today=TODAY)["interviews"] == {"interviews": 0, "sent": 0, "percent": None}
assert insights.summarize(pool, details, None, today=TODAY)["interviews"] is None

# empty everything: no crash, nothing made up
e = insights.summarize([], {}, [], today=TODAY)
assert e["pool"]["count"] == 0 and e["pool"]["pay"]["median"] is None and e["statuses"] == [] and e["rows"] == 0

# the terminal text: one block per question, the numbers from above
t = insights.text(s)
assert "Your sheet: 6 rows" in t and "Your pool: 5 jobs" in t and "median $29.50/h" in t and "Applied: 3 jobs" in t
assert "2 in Massachusetts" in t and "Closing within 7 days: 2" in t and "Security" in t
assert "Your pool: 0 jobs" in insights.text(e) and "Pay: none listed" in insights.text(
    insights.summarize([{"id": "4", "category": "it"}], details, None, today=TODAY))

print("All insights checks passed.")
