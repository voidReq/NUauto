"""Offline checks for jobs.py (hard rules, pool, model) and sheet.add_proposed. Run: python test_jobs.py"""
from datetime import date

from nuauto import jobs
from nuauto import sheet
from nuauto.sheet import HEADERS

TODAY = date(2026, 10, 2)


def d(**over):
    base = {"id": "x", "title": "Embedded Co-op", "company": "Co", "position_types": ["Co-op"],
            "term": ["2027 - Spring"], "lengths": ["6 Month"], "class_levels": [], "degree_levels": ["Undergraduate"],
            "majors": [], "states": ["US-CA"], "location": "San Jose, CA, USA", "skills": [], "experience": [],
            "function": [], "pay": "", "deadline": None, "posting_end": "2026-11-30", "expired": False,
            "applied": False, "description": "firmware", "qualifications": ""}
    base.update(over)
    return base


def rule(**over):
    return jobs.hard_rules(d(**over), TODAY)


# basic pass, threshold 65 when no class level listed
assert rule()[:2] == (True, 65)
# class levels
assert rule(class_levels=["Sophomore", "Junior"])[:2] == (True, 65)
keep, thr, flags, _ = rule(class_levels=["Junior", "Senior"])
assert (keep, thr) == (True, 90) and "junior+" in flags
assert rule(class_levels=["Senior", "Graduate"])[0] is False
assert rule(degree_levels=["Masters", "Doctorate"])[0] is False
# term
assert rule(term=["2026 - Fall"])[0] is False
keep, _, flags, _ = rule(term=[])
assert keep and "term unclear" in flags
assert "term unclear" not in rule(term=[], title="Spring 2027 Firmware Co-op")[2]
assert "title says other term" in rule(title="ML Co-op Summer 2027")[2]
assert "title says other term" in rule(title="SEP-DEC Co-op - Software Developer")[2]
assert "title says other term" not in rule(title="Spring 2027 Software Co-op")[2]
# position type: internship only when explicitly Spring 2027
assert rule(position_types=["Internship"])[0] is True
assert rule(position_types=["Internship"], term=[])[0] is False
assert rule(position_types=["Full Time / Part Time"])[0] is False
# length
assert rule(lengths=["8 Month"])[0] is False
assert rule(lengths=["4 Month", "8 Month"])[0] is True
assert "length unclear" in rule(lengths=[])[2]
# majors flag (not a drop)
keep, _, flags, _ = rule(majors=["Khoury College of Computer Sciences/Computer Science"])
assert keep and "not your major (targets Computer Science)" in flags, flags
_, _, flags, _ = rule(majors=["A/W", "A/X", "B/Y", "B/Z"])
assert "not your major (targets W, X, Y +1 more)" in flags, flags
assert not any(f.startswith("not your major") for f in rule(majors=["College of Engineering/Electrical and Computer Engineering"])[2])
# closed / applied / expired
assert rule(deadline="2026-10-01")[0] is False
assert rule(posting_end="2026-09-30T23:59:59-05:00")[0] is False
assert rule(applied=True)[0] is False and rule(expired=True)[0] is False
# text-stated class requirement from the scorer
assert jobs.pool_entry_rules(d(), {"class_req": "none"})[:2] == (True, 65)
k, t, f = jobs.pool_entry_rules(d(), {"class_req": "junior_plus"})
assert (k, t) == (True, 90) and "junior+ (in text)" in f
assert jobs.pool_entry_rules(d(), {"class_req": "senior_plus"})[0] is False
assert jobs.pool_entry_rules(d(), {"class_req": "grad"})[0] is False
assert "prior experience required" in jobs.pool_entry_rules(
    d(experience=["Co-op: Previous Co-op, Internship, or Related Work Experience Required"]), {"class_req": "none"})[2]
assert "mentions graduation 2027/2028" in jobs.pool_entry_rules(
    d(description="Students graduating in 2027 or early 2028 preferred"), {"class_req": "none"})[2]
assert not any(f.startswith("mentions graduation") for f in jobs.pool_entry_rules(
    d(description="Graduating May 2029 is fine; founded 2027"), {"class_req": "none"})[2])
assert "no first-time co-ops?" in jobs.pool_entry_rules(d(description="We are not accepting first-time co-ops this cycle."), {"class_req": "none"})[2]
assert "vague posting, low confidence" in jobs.pool_entry_rules(d(), {"class_req": "none", "met": ["a"], "missing": ["b"]})[2]
assert "vague posting, low confidence" not in jobs.pool_entry_rules(d(), {"class_req": "none", "met": ["a", "b"], "partial": ["c"], "missing": ["d"]})[2]
# security threshold 50, but junior-only still 90
assert jobs.pool_entry_rules(d(), {"class_req": "none"}, "security")[1] == 60
assert jobs.pool_entry_rules(d(), {"class_req": "junior_plus"}, "security")[1] == 90
assert jobs.pool_entry_rules(d(), {"class_req": "none"}, "embedded")[1] == 65
# ranking: bonuses, full-stack always last
assert jobs.category_bonus("security") == 20 and jobs.category_bonus("embedded") == 15 and jobs.category_bonus("software") == 0
rows = [{"category": "fullstack_web", "rank": 120}, {"category": "software", "rank": 60}, {"category": "security", "rank": 90}]
assert [r["category"] for r in sorted(rows, key=jobs.pool_sort_key)] == ["security", "software", "fullstack_web"]
# rating order: one per category in turn, best-first; no job twice
P = [{"id": "s1", "category": "security"}, {"id": "s2", "category": "security"}, {"id": "s3", "category": "security"},
     {"id": "e1", "category": "embedded"}, {"id": "w1", "category": "fullstack_web"}, {"id": "h1", "category": "hardware"}]
assert [r["id"] for r in jobs.rating_order(P)] == ["s1", "e1", "h1", "w1", "s2", "s3"]
T = [dict(r, taste=t) for r, t in zip(P[:3], (0.9, 0.8, 0.52))]
assert [r["id"] for r in jobs.rating_order(T)] == ["s1", "s3", "s2"]  # best, then least sure
# AR/XR (+10) vs wearables (+5) tag: clear phrases only
assert jobs.xr_tag(d(description="firmware for our smart glasses and AR/VR headsets")) == "AR/XR"
assert jobs.xr_tag(d(title="Wearable Devices Co-op")) == "wearables"
assert jobs.xr_tag(d(description="process AR invoices; AR aging reports")) is None
assert jobs.category_bonus("embedded") == 15 and jobs.category_bonus("hardware") == 10
# description headers and bullets
assert jobs.is_header("Responsibilities:") and jobs.is_header("WHAT YOU'LL DO") and jobs.is_header("What You'll Bring")
assert not jobs.is_header("Boston, MA") and not jobs.is_header("- Write firmware.") and not jobs.is_header("We build robots.")
# Boston bonus
assert jobs.boston(d(states=["US-MA"])) and not jobs.boston(d())

# html stripping keeps line structure
assert jobs.strip_html("<p>a &amp; b</p><ul><li>x</li><li>y</li></ul>") == "a & b\n- x\n- y"

# model: intersection jobs rank above single-field ones
D = {}
for k in range(6):
    D[f"y{k}"] = d(id=f"y{k}", title="Embedded Security Co-op", skills=["C", "cryptography"],
                   description="secure boot firmware on microcontrollers, threat modeling of embedded devices")
    D[f"n{k}"] = d(id=f"n{k}", title=["Web Developer Co-op", "Security Policy Analyst", "PCB Design Co-op"][k % 3],
                   skills=[["React"], ["GRC"], ["Altium"]][k % 3],
                   description=["build web pages", "write compliance policies", "design circuit boards"][k % 3])
D["hit"] = d(id="hit", title="Firmware Security Co-op", skills=["C"], description="secure boot firmware hardening")
D["miss"] = d(id="miss", title="Frontend Developer Co-op", skills=["React"], description="build web dashboards")
assert jobs.train(D, {"y0": 1, "n0": 0}) is None
score, explain = jobs.train(D, {i: int(i.startswith("y")) for i in D if i[0] in "yn"})
p = score(["hit", "miss"])
assert p["hit"] > p["miss"], p
assert explain("hit")


# add_proposed: appends below data, skips duplicates, never overwrites
class FakeWS:
    def __init__(self, values):
        self.values = values

    def get_all_values(self):
        return [list(r) for r in self.values]

    def get(self, rng):
        a, b = (int(x[1:]) for x in rng.split(":"))
        return [self.values[r - 1] if r <= len(self.values) else [] for r in range(a, b + 1)]

    def update(self, range_name, values, value_input_option=None):
        start = int(range_name.split(":")[0][1:])
        for k, row in enumerate(values):
            while len(self.values) < start + k:
                self.values.append([""] * 6)
            self.values[start + k - 1] = row


# apply-on-company-site detection (Liberty Mutual wording, curly apostrophe)
assert jobs.external_hint(d(description="For this co-op position, you are asked to complete an online application "
                                        "on the employer’s external website.  Prior to applying, read the posting."))
assert jobs.external_hint(d(description="Please apply on our careers page."))
assert jobs.external_hint(d(description="Apply machine learning to telematics.")) is None

ws = FakeWS([HEADERS, ["https://a", "A", "T", "Applied", "", "2026-10-01"]])
n = sheet.add_proposed(ws, [{"url": "https://a", "company": "A", "title": "T", "notes": ""},
                            {"url": "https://b", "company": "B", "title": "U", "notes": "suggested"}])
assert n == 1 and ws.values[2][:4] == ["https://b", "B", "U", "Proposed"], ws.values

print("All job-pool checks passed.")
