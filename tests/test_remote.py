"""Offline checks for remote.py (the homelab assistant's queue and claims) and the code around it: assist.run's claim
step, ask_line, review's queue and Start now, gui.homelab_assistants. Run: python tests/test_remote.py

No ssh, no sheet, no network, no real DNS. State is a temp folder. tmux (when installed) runs on a private socket
(TMUX_TMPDIR) and no systemd scope is ever made.
"""
import http.client
import io
import json
import os
import shlex
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import date, datetime, timedelta
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlencode, urlparse

STATE = tempfile.mkdtemp(prefix="nuauto-test-remote-")
os.environ["NUAUTO_STATE_DIR"] = STATE
os.environ["TMUX_TMPDIR"] = STATE  # a private tmux server for the real-tmux checks
os.environ.pop("TMUX", None)

from nuauto import config  # noqa: E402

assert config.LOCAL_DIR == os.path.join(STATE, "local") and not config.DEMO
os.makedirs(config.LOCAL_DIR, mode=0o700, exist_ok=True)

from nuauto import assist, jobs, remote, review, sheet  # noqa: E402

REAL_FLAGS = (config.IS_SERVER, config.REMOTE_ASSIST)
ORIG = {n: getattr(remote, n) for n in ("queue_now", "start", "in_hours", "ended", "record_started", "STARTUP_CHECK", "AUTO_PATH")}


def restore(*names):
    for n in names:
        setattr(remote, n, ORIG[n])
TODAY = date(2026, 10, 12)
HAVE_TMUX = subprocess.run(["which", "tmux"], capture_output=True).returncode == 0


def row(n, status, url="", notes="", company=None, title="Intern"):
    return sheet.Row(n, url, company or f"Co{n}", title, status, notes, "")


def ext(host, path="/job/1"):
    return f"External application: {host} -> https://{host}{path}"


# ---- hours
assert remote.hours() == (9, 22)
for given, want in (([8, 20], (8, 20)), ("7-23", (7, 23)), ((9, 22), (9, 22)), ([0, 24], (0, 24)), ("bad", (9, 22)),
                    ([22, 9], (9, 22)), ([9, 9], (9, 22)), ([-1, 5], (9, 22)), ([9, 25], (9, 22)), (None, (9, 22)),
                    (["a", "b"], (9, 22)), ([9], (9, 22)), (5, (9, 22)), ("9-", (9, 22))):
    config.LOCAL = {**config.LOCAL, "remote_hours": given}
    assert remote.hours() == want, (given, remote.hours())
config.LOCAL = {k: v for k, v in config.LOCAL.items() if k != "remote_hours"}
at = lambda h: datetime(2026, 10, 12, h, 30)  # noqa: E731
assert [remote.in_hours(at(h)) for h in (8, 9, 21, 22, 23, 0)] == [False, True, True, False, False, False]
config.LOCAL = {**config.LOCAL, "remote_hours": "6-8"}
assert remote.in_hours(at(6)) and remote.in_hours(at(7)) and not remote.in_hours(at(8))
config.LOCAL = {k: v for k, v in config.LOCAL.items() if k != "remote_hours"}
assert remote.match_of("Summer 2027 internship; match 87%") == 87 and remote.match_of("no") == -1 and remote.match_of(None) == -1

# ---- candidates: the order and what is skipped
main_rows = [
    row(2, "Needs Human", notes=ext("acme.wd5.myworkdayjobs.com") + " | match 80%"),                   # closes in 3 days
    row(3, "Needs Human", notes=ext("beta.icims.com") + " | match 50%"),                              # closes in 2 days
    row(4, "Needs Human", notes=ext("gamma.example", "/a") + " | match 90%"),
    row(5, "Needs Human", notes=ext("delta.example")),                                              # past its deadline
    row(6, "Needs Human", notes="Needs a cover letter on NUworks"),                                   # not a company site
    row(7, "Applied", notes=ext("eps.example")),
    row(8, "Needs Human", notes="External application: northeastern-csm.symplicity.com -> https://northeastern-csm.symplicity.com/x"),
    row(9, "Needs Human", notes=ext("zeta.example", "/z") + " | match 90%"),                          # ties row 4: sheet order
    row(10, "Needs Human", notes=ext("eta.example") + " | match 70%"),                                 # closes in 8 days: not urgent
    row(11, "Needs Human", notes=ext("theta.example") + " | match 60%"),                               # closes in exactly 7 days
    row(12, "Needs Human", notes="External application: no-link.example"),                            # no link in the notes
    row(13, "Needs Human", notes=""),
]
other_rows = [
    row(2, "Approved", "https://jobs.lever.co/a/1", "Summer 2027 internship; match 95%"),
    row(3, "Approved", "https://jobs.example.org/2", "match 90%"),                                      # ties 4 and 9: NUworks first
    row(4, "Proposed", "https://jobs.example.org/3", "match 99%"),
    row(5, "Approved", "https://northeastern-csm.symplicity.com/job/4"),                              # NUworks itself
    row(6, "Approved", "http://insecure.example/5"),
    row(7, "Applied", "https://jobs.example.org/6"),
    row(8, "Approved", "https://nomatch.example/7"),                                                  # no match note: last
]
closes = {2: TODAY + timedelta(days=3), 3: TODAY + timedelta(days=2), 5: TODAY - timedelta(days=1),
          10: TODAY + timedelta(days=8), 11: TODAY + timedelta(days=7)}
cands = remote.candidates(main_rows, other_rows, lambda r: closes.get(r.number), TODAY)
order = [(c["tab"], c["row"]) for c in cands]
assert order == [("jobs", 3), ("jobs", 2), ("jobs", 11), ("other", 2), ("jobs", 4), ("jobs", 9), ("other", 3),
                 ("jobs", 10), ("other", 8)], order
c3 = cands[0]
assert c3 == {"tab": "jobs", "row": 3, "url": "https://beta.icims.com/job/1", "host": "beta.icims.com", "company": "Co3",
              "title": "Intern", "closes": TODAY + timedelta(days=2), "match": 50}
nothing_urgent = [(c["tab"], c["row"]) for c in remote.candidates(main_rows, other_rows, None, TODAY)]   # no closing dates known
assert nothing_urgent[:5] == [("other", 2), ("jobs", 4), ("jobs", 9), ("other", 3), ("jobs", 2)], nothing_urgent   # best match, NUworks first, sheet order
assert ("jobs", 5) in nothing_urgent                                    # (no date known: not past its deadline)
assert remote.candidates(None, None) == [] and remote.candidates([], []) == []
assert remote.tab_word("other") == "other" and remote.tab_word("jobs") == "nuworks"
assert jobs.URGENT_DAYS == 7   # the order test above assumes it

# ---- plan
def cand(tab, rown, host, url=None, match=50):
    return {"tab": tab, "row": rown, "url": url or f"https://{host}/{rown}", "host": host, "company": f"C{rown}", "title": "T",
            "closes": None, "match": match}


a, b, c, d, e = cand("jobs", 1, "a.com"), cand("other", 2, "b.com"), cand("other", 3, "c.com"), cand("jobs", 4, "a.com"), cand("other", 5, "e.com")
go, rest = remote.plan([a, b, c], [], [], {}, 5)
assert [x for x, _ in go] == [a, b, c] and rest == []
go, rest = remote.plan([a, b, c], [], [], {}, 2)                              # free slots limit
assert [x for x, _ in go] == [a, b] and rest == [(c, "waiting for a free slot")]
assert remote.plan([a, b], [], [], {}, 0) == ([], [(a, "waiting for a free slot"), (b, "waiting for a free slot")])
go, rest = remote.plan([a, d], [], [], {}, 5)                                 # one per host, within one plan
assert [x for x, _ in go] == [a] and rest == [(d, "another session is on a.com")]
go, rest = remote.plan([a, b], [{"tab": "other", "row": 9, "host": "a.com"}], [], {}, 5)          # a running session's host
assert [x for x, _ in go] == [b] and rest == [(a, "another session is on a.com")]
go, rest = remote.plan([a, b], [], [{"tab": "other", "row": 9, "host": "b.com"}], {}, 5)          # a laptop claim's host
assert [x for x, _ in go] == [a] and rest == [(b, "another session is on b.com")]
go, rest = remote.plan([a, b], [{"tab": "jobs", "row": 1, "host": "z.com"}], [], {}, 5)           # a session on the row (any host)
assert [x for x, _ in go] == [b] and rest == [(a, "a session is on it")]
go, rest = remote.plan([a, b], [], [{"tab": "other", "row": 2, "host": "z.com"}], {}, 5)          # a claim on the row
assert [x for x, _ in go] == [a] and rest == [(b, "a session is on it")]
go, rest = remote.plan([a, b], [{"tab": "other", "row": 1, "host": "z.com"}], [], {}, 5)          # row 1 of the OTHER tab: not a's
assert [x for x, _ in go] == [a, b]
once = {"jobs-1": {"url": a["url"], "auto": True}, "other-2": {"url": "https://old.example/was-here", "auto": True}}
go, rest = remote.plan([a, b], [], [], once, 5)                               # started once: only if the URL matches
assert [x for x, _ in go] == [b] and rest == [(a, "started by itself once already: yours to start now")]
go, rest = remote.plan([a, b], [], [], once, 5, manual=True)                  # by hand: the start-once rule doesn't apply
assert [x for x, _ in go] == [a, b] and rest == []
# a row started by hand before (auto False), not approved: the queue may still start it
assert [x for x, _ in remote.plan([a], [], [], {"jobs-1": {"url": a["url"], "auto": False}}, 5)[0]] == [a]
assert [x for x, _ in remote.plan([a], [], [], {"jobs-1": {"url": a["url"], "auto": True, "approved": False, "answer": "n"}}, 5)[0]] == []
# the NUworks applications left (week and total): NUworks-tab rows only, one used per row started
j1, j2, j3 = cand("jobs", 31, "j1.com"), cand("jobs", 32, "j2.com"), cand("jobs", 33, "j3.com")
USED = "this week's (or the total) NUworks applications are used up"
go, rest = remote.plan([j1, b, j2, j3], [], [], {}, 9, jobs_left=2)
assert [x["row"] for x, _ in go] == [31, 2, 32] and rest == [(j3, USED)]
go, rest = remote.plan([a, b, d], [], [], {}, 5, jobs_left=0)
assert [x for x, _ in go] == [b] and [(x["row"], w) for x, w in rest] == [(1, USED), (4, USED)]
assert [x["row"] for x, _ in remote.plan([e, a], [], [], {}, 1, jobs_left=-3)[0]] == [5]   # the other tab has no cap
assert [x["row"] for x, _ in remote.plan([j1, j2, j3], [], [], {}, 9, jobs_left=None)[0]] == [31, 32, 33]   # no limit to check
assert [x["row"] for x, _ in remote.plan([j1, j2, j3], [], [], {}, 9, jobs_left=1)[0]] == [31]
go, rest = remote.plan([j1, j2], [], [], {}, 1, jobs_left=5)                 # a row that waits for a slot used no application
assert [x["row"] for x, _ in go] == [31] and rest == [(j2, "waiting for a free slot")]
# an approved Submit nobody said the outcome of: never again by itself (even by hand); y / n settle it
unk = {"jobs-1": {"url": a["url"], "auto": True, "approved": True, "answer": None}}
go, rest = remote.plan([a, b], [], [], unk, 5)
assert [x for x, _ in go] == [b] and rest == [(a, remote.OUTCOME_UNKNOWN)]
assert remote.plan([a], [], [], unk, 5, manual=True) == ([], [(a, remote.OUTCOME_UNKNOWN)])
assert "--again" in remote.OUTCOME_UNKNOWN and "check the site" in remote.OUTCOME_UNKNOWN
assert [x for x, _ in remote.plan([a], [], [], {"jobs-1": {**unk["jobs-1"], "url": "https://elsewhere/1"}}, 5)[0]] == [a]   # another job
for ans in ("y", "n"):
    got, _ = remote.plan([a], [], [], {"jobs-1": {**unk["jobs-1"], "answer": ans, "auto": False}}, 5)
    assert [x for x, _ in got] == [a], ans
assert [x for x, _ in remote.plan([a], [], [], {"jobs-1": {"url": a["url"], "approved": True, "answer": "y", "auto": True}}, 5, manual=True)[0]] == [a]
# the order of reasons: a busy row first, then the unknown outcome, then start-once, the application limit, the host, the slots
go, rest = remote.plan([a], [{"tab": "jobs", "row": 1, "host": "z.com"}], [], unk, 5, jobs_left=0)
assert rest == [(a, "a session is on it")]
go, rest = remote.plan([a], [], [], {"jobs-1": {**unk["jobs-1"]}}, 5, jobs_left=0)
assert rest == [(a, remote.OUTCOME_UNKNOWN)]
go, rest = remote.plan([a], [], [], once, 5, jobs_left=0)
assert rest == [(a, "started by itself once already: yours to start now")]
go, rest = remote.plan([a], [{"tab": "other", "row": 9, "host": "a.com"}], [], {}, 0, jobs_left=0)
assert rest == [(a, USED)]
# a skipped row does not take a slot or its host
go, rest = remote.plan([a, d, b], [], [], once, 1)
assert [x for x, _ in go] == [d] and [w for _, w in rest] == ["started by itself once already: yours to start now", "waiting for a free slot"]

# ---- the NUworks applications left: the week's cap and the total, minus sessions on either machine
real_max_total, real_local = sheet.MAX_TOTAL, config.LOCAL
today_s, old_s = date.today().isoformat(), (date.today() - timedelta(days=40)).isoformat()
applied = [row(60 + i, "Applied", f"https://x/{i}", "", None) for i in range(2)]
for r_ in applied:
    r_.date = today_s
longago = row(70, "Applied", "https://x/old")
longago.date = old_s
try:
    config.LOCAL = {**config.LOCAL, "max_per_week": 5}
    assert remote.jobs_left(applied + [longago], [], []) == 3                           # 5 - the 2 this week
    assert remote.jobs_left(applied, [{"tab": "jobs"}], [{"tab": "jobs"}, {"tab": "other"}]) == 1   # minus NUworks-tab sessions
    assert remote.jobs_left(applied, [{"tab": "other"}], []) == 3                       # the other tab uses none
    assert remote.jobs_left(applied, [{"tab": "jobs"}] * 4, []) == -1
    sheet.MAX_TOTAL = 3                                                                  # the total binds first
    assert remote.jobs_left(applied + [longago], [], []) == 0
    sheet.MAX_TOTAL = 99
    config.LOCAL = {**config.LOCAL, "max_per_week": 2}
    assert remote.jobs_left(applied, [], []) == 0
    assert remote.jobs_left([], [], []) == 2
finally:
    sheet.MAX_TOTAL, config.LOCAL = real_max_total, real_local

# ---- claims (the homelab's side), in the temp local/
assert remote.CLAIMS_DIR == os.path.join(config.LOCAL_DIR, "assist_claims") and remote.AUTO_PATH.startswith(config.LOCAL_DIR)
sessions_now = []
real_sessions = assist.sessions
assist.sessions = lambda: list(sessions_now)
try:
    assert remote.live_claims() == [] and remote.claimed("other", 5, "x.com") is None
    assert remote.claim("other", 5, "acme.example") == (True, None)
    assert not [n for n in os.listdir(remote.CLAIMS_DIR) if n.endswith(".tmp")]           # written whole (tmp + rename), none left
    assert oct(os.stat(remote.CLAIMS_DIR).st_mode & 0o777) == "0o700"
    assert [(x["tab"], x["row"], x["host"]) for x in remote.live_claims()] == [("other", 5, "acme.example")]
    assert remote.claim("other", 5, "acme.example") == (True, None)                      # the same row again: fine
    ok, why = remote.claim("jobs", 6, "acme.example")                                   # same site, another row
    assert not ok and "another laptop session is on acme.example" in why and len(remote.live_claims()) == 1
    assert remote.claim("jobs", 6, "beta.example") == (True, None)
    assert remote.claimed("other", 5, "zzz") == "the laptop's assistant is on this row now"   # by row
    assert remote.claimed("jobs", 7, "acme.example") == "the laptop's assistant is on acme.example now"   # by site
    assert remote.claimed("jobs", 5, "zzz") is None                                       # row 5 of the other tab is another row
    remote.unclaim("other", 5)
    remote.unclaim("other", 5)                                                            # twice: no error
    assert remote.claimed("other", 5, "acme.example") is None and len(remote.live_claims()) == 1
    # the homelab's own sessions refuse a claim
    sessions_now[:] = [{"slot": 2, "tab": "other", "row": 5, "host": "x.example"}]
    ok, why = remote.claim("other", 5, "q.example")
    assert not ok and "on this row now (slot 2)" in why
    sessions_now[:] = [{"slot": 1, "tab": "other", "row": 9, "host": "q.example"}]
    ok, why = remote.claim("other", 5, "q.example")
    assert not ok and "q.example now" in why
    assert remote.claim("other", 5, "other.example") == (True, None)
    sessions_now[:] = []
    # old claims are ignored, and a new claim on their site works
    old = {"tab": "other", "row": 40, "host": "old.example", "since": time.time() - remote.CLAIM_HOURS * 3600 - 5}
    with open(remote._claim_path("other", 40), "w") as f:
        json.dump(old, f)
    assert (("other", 40) not in [(x["tab"], x["row"]) for x in remote.live_claims()]
            and remote.claimed("other", 40, "old.example") is None)
    assert remote.claim("jobs", 41, "old.example") == (True, None)
    with open(os.path.join(remote.CLAIMS_DIR, "bad.json"), "w") as f:
        f.write("{not json")
    assert len(remote.live_claims()) == 3                                                 # a damaged file is skipped
    for t_, r_ in (("other", 5), ("jobs", 6), ("jobs", 41)):
        remote.unclaim(t_, r_)
    assert remote.live_claims() == []
    # claim / unclaim / claimed from several threads at once: always whole claims, nothing left over
    errors = []

    def churn(n):
        try:
            for k in range(40):
                assert remote.claim("other", 100 + n, f"h{n}.example")[0]
                remote.unclaim("other", 100 + n)
        except Exception as ex:  # noqa: BLE001
            errors.append(ex)
    threads = [threading.Thread(target=churn, args=(n,)) for n in range(4)]
    for t_ in threads:
        t_.start()
    for _ in range(150):
        remote.claimed("jobs", 1, "zzz.example")
        remote.live_claims()
    for t_ in threads:
        t_.join()
    assert errors == [] and remote.live_claims() == [] and not [n for n in os.listdir(remote.CLAIMS_DIR) if n.endswith(".tmp")]
finally:
    assist.sessions = real_sessions

# ---- the queue's records (local/assist_auto.json): started, approved, answered
remote.AUTO_PATH = os.path.join(config.LOCAL_DIR, "assist_auto_records.json")
try:
    cx = {**cand("other", 51, "x.example"), "company": "Xco"}
    assert remote._read_auto() == {}                                                     # no file yet
    remote.record_started(cx)
    rec = remote._read_auto()["other-51"]
    assert (rec["url"], rec["company"], rec["auto"], rec["approved"], rec["answer"]) == (cx["url"], "Xco", True, False, None) and rec["started"]
    assert oct(os.stat(remote.AUTO_PATH).st_mode & 0o777) == "0o600" and os.path.exists(remote.AUTO_PATH + ".lock")
    remote.record_outcome("other", 51, cx["url"], True, None)                            # approved a Submit, nobody said the outcome
    rec = remote._read_auto()["other-51"]
    assert rec["approved"] is True and rec["answer"] is None and rec["auto"] is True and rec["company"] == "Xco" and rec["ended"]
    assert remote.plan([cx], [], [], remote._read_auto(), 5, manual=True) == ([], [(cx, remote.OUTCOME_UNKNOWN)])
    remote.record_outcome("other", 51, cx["url"], True, "y")                             # said: it went through
    assert remote._read_auto()["other-51"]["answer"] == "y"
    assert [x for x, _ in remote.plan([cx], [], [], remote._read_auto(), 5, manual=True)[0]] == [cx]
    remote.record_started(cx, auto=False)                                                # by hand, the same job: auto stays, a new try
    rec = remote._read_auto()["other-51"]
    assert rec["auto"] is True and rec["approved"] is False and rec["answer"] is None
    cy = {**cx, "url": "https://x.example/another-job"}                                  # the row reused for another job: fresh
    remote.record_started(cy, auto=False)
    rec = remote._read_auto()["other-51"]
    assert rec["url"] == cy["url"] and rec["auto"] is False
    remote.record_outcome("other", 51, "https://x.example/third", False, "n")            # an outcome for a job never recorded as started
    rec = remote._read_auto()["other-51"]
    assert rec["url"] == "https://x.example/third" and rec["approved"] is False and rec["answer"] == "n" and "auto" not in rec
    remote.record_outcome("jobs", 52, "https://x.example/52", False, None)
    assert remote._read_auto()["jobs-52"]["answer"] is None and remote._read_auto()["jobs-52"]["approved"] is False
    # several writers at once: none lost
    with open(remote.AUTO_PATH, "w") as f:
        f.write("{}")
    ts = [threading.Thread(target=remote.record_started, args=({**cand("other", 200 + n, f"w{n}.example"), "company": f"W{n}"},)) for n in range(10)]
    for t_ in ts:
        t_.start()
    for t_ in ts:
        t_.join()
    assert sorted(remote._read_auto()) == sorted(f"other-{200 + n}" for n in range(10))
    # a file that can't be read: nothing is guessed, nothing overwritten
    for bad in ("{not json", "[1, 2]", '"text"', "null", ""):
        with open(remote.AUTO_PATH, "w") as f:
            f.write(bad)
        try:
            remote._read_auto()
            raise AssertionError(f"read {bad!r}")
        except remote.RecordError as ex:
            assert remote.AUTO_PATH in str(ex) and "starts nothing" in str(ex) or "not what the queue wrote" in str(ex)
        try:
            remote.record_started(cx)
            raise AssertionError("wrote over an unreadable file")
        except remote.RecordError:
            pass
        out_, sys.stdout = sys.stdout, io.StringIO()
        try:
            remote.record_outcome("other", 51, cx["url"], True, "y")                       # never fails the end of a session
            said = sys.stdout.getvalue()
        finally:
            sys.stdout = out_
        assert "could not record how this session ended" in said and open(remote.AUTO_PATH).read() == bad
    # the real queue_now: records, applications left and "manual" reach plan()
    from nuauto import apply as apply_mod
    nrows = [row(2, "Needs Human", notes=ext("g1.example") + " | match 80%"), row(3, "Needs Human", notes=ext("g2.example") + " | match 70%"),
             row(4, "Applied", "https://x/4"), row(5, "Applied", "https://x/5")]
    nrows[2].date = nrows[3].date = date.today().isoformat()
    orows = [row(9, "Approved", "https://o1.example/1", "match 60%")]
    fakes = (sheet.open_worksheet, sheet.open_other, sheet.read_rows, apply_mod.row_closes, assist.sessions)
    sheet.open_worksheet, sheet.open_other = (lambda interactive=True: "main"), (lambda interactive=True, create=False: "other")
    sheet.read_rows = lambda ws: nrows if ws == "main" else orows
    apply_mod.row_closes = lambda r: None
    assist.sessions = lambda: []
    real_cfg = config.LOCAL
    try:
        config.LOCAL = {**config.LOCAL, "max_per_week": 3}                               # 2 applied this week: 1 left
        with open(remote.AUTO_PATH, "w") as f:
            json.dump({"jobs-2": {"url": "https://g1.example/job/1", "auto": True}}, f)
        go, rest = remote.queue_now()
        assert [(c["tab"], c["row"]) for c, _ in go] == [("jobs", 3), ("other", 9)]
        assert [(c["row"], w) for c, w in rest] == [(2, "started by itself once already: yours to start now")]
        go, rest = remote.queue_now(manual=True)                                          # by hand: row 2 is allowed, 3 is out of applications
        assert [(c["tab"], c["row"]) for c, _ in go] == [("jobs", 2), ("other", 9)]
        assert rest[0][0]["row"] == 3 and "used up" in rest[0][1]
        with open(remote.AUTO_PATH, "w") as f:
            f.write("{broken")
        try:
            remote.queue_now()
            raise AssertionError("queue_now went on with a broken record file")
        except remote.RecordError:
            pass
    finally:
        config.LOCAL = real_cfg
        sheet.open_worksheet, sheet.open_other, sheet.read_rows, apply_mod.row_closes, assist.sessions = fakes
        assist.sessions = real_sessions
finally:
    restore("AUTO_PATH")

# ---- the laptop's side of a claim, ssh stood in for
calls = []
real_homelab = remote._homelab


def fake(result):
    def f(*args, timeout=30):
        calls.append(args)
        return result
    return f


remote._homelab = fake((None, ""))
ok, note = remote.claim_on_homelab("other", 7, "acme.example")
assert ok and "could not be reached" in note and calls[-1] == ("claim", "other", 7, "acme.example")
remote._homelab = fake((3, json.dumps({"ok": False, "why": "the homelab's assistant is on this row now (slot 1)"}) + "\n"))
assert remote.claim_on_homelab("jobs", 7, "x") == (False, "the homelab's assistant is on this row now (slot 1)") and calls[-1][1] == "nuworks"
remote._homelab = fake((3, json.dumps({"ok": False})))
assert remote.claim_on_homelab("other", 7, "x") == (False, "the homelab refused")
remote._homelab = fake((0, "garbage"))
ok, note = remote.claim_on_homelab("other", 7, "x")
assert ok and "no answer" in note
remote._homelab = fake((0, ""))
assert remote.claim_on_homelab("other", 7, "x")[0] is True
remote._homelab = fake((0, "some login banner\n" + json.dumps({"ok": True, "why": None}) + "\n"))
assert remote.claim_on_homelab("other", 7, "x") == (True, None)
remote._homelab = fake((0, "{}"))
assert remote.claim_on_homelab("other", 7, "x") == (False, "the homelab refused")                 # no "ok": not allowed
remote.unclaim_on_homelab("jobs", 9)
assert calls[-1] == ("unclaim", "nuworks", 9, "-")
remote._homelab = fake((0, json.dumps([{"slot": 1, "row": 3, "tab": "other"}])))
assert remote.homelab_sessions() == [{"slot": 1, "row": 3, "tab": "other"}]
for bad in ((None, ""), (3, "[]"), (0, "garbage"), (0, "")):
    remote._homelab = fake(bad)
    assert remote.homelab_sessions() == [], bad
remote._homelab = real_homelab
# _homelab itself: ssh's argv, and what counts as an answer
runs = []
real_run = subprocess.run


def fake_run(result):
    def f(cmd, **kw):
        runs.append((cmd, kw))
        if isinstance(result, Exception):
            raise result
        return subprocess.CompletedProcess(cmd, result[0], result[1], "")
    return f


subprocess.run = fake_run((0, "out"))
try:
    assert remote._homelab("claim", "other", 7, "a b; rm -rf") == (0, "out")
    cmd = runs[-1][0]
    assert cmd[0] == "ssh" and "BatchMode=yes" in cmd and cmd[-1].endswith(".venv/bin/nuauto assist claim other 7 'a b; rm -rf'")
    assert cmd[-1].startswith("cd ") and runs[-1][1]["timeout"] == 30
    subprocess.run = fake_run((3, "refused"))
    assert remote._homelab("claim", "other", 7, "h") == (3, "refused")
    subprocess.run = fake_run((255, "x"))                                                   # ssh failed
    assert remote._homelab("sessions") == (None, "")
    subprocess.run = fake_run((1, "x"))
    assert remote._homelab("sessions") == (None, "")
    subprocess.run = fake_run(subprocess.TimeoutExpired("ssh", 1))
    assert remote._homelab("sessions") == (None, "")
    subprocess.run = fake_run(OSError("no ssh"))
    assert remote._homelab("sessions") == (None, "")
finally:
    subprocess.run = real_run

# ---- main(): arguments, on the homelab and on the laptop
def run_main(argv, server=True, remote_on=True):
    config.IS_SERVER, config.REMOTE_ASSIST = server, remote_on
    out, sys.stdout = sys.stdout, io.StringIO()
    code = None
    try:
        remote.main(argv)
    except SystemExit as ex:
        code = ex.code
    finally:
        printed, sys.stdout = sys.stdout.getvalue(), out
        config.IS_SERVER, config.REMOTE_ASSIST = REAL_FLAGS
    return code, printed


try:
    code, out = run_main(["claim", "other", "12", "jobs.example"])
    assert code == 0 and json.loads(out) == {"ok": True, "why": None} and remote.claimed("other", 12, "x") is not None
    code, out = run_main(["claim", "nuworks", "13", "jobs.example"])                          # same site: refused, exit 3
    assert code == 3 and json.loads(out)["ok"] is False and "jobs.example" in json.loads(out)["why"]
    assert remote.claimed("jobs", 13, "zzz") is None
    code, out = run_main(["unclaim", "other", "12", "-"])
    assert code is None and out == "" and remote.live_claims() == []
    code, out = run_main(["claim", "other", "12", "h"], remote_on=False)                      # claims need no remote_assist
    assert code == 0
    remote.unclaim("other", 12)
    code, _ = run_main(["claim", "other", "12", "h"], server=False)
    assert isinstance(code, str) and "run on the homelab" in code
    for bad in (["claim", "other", "x", "h"], ["claim", "bogus", "1", "h"], ["claim", "other", "1"], ["unclaim"]):
        code, _ = run_main(bad)
        assert isinstance(code, str) and remote.live_claims() == [], bad
    code, _ = run_main(["queue"], server=False)
    assert "homelab" in code
    code, out = run_main(["auto"], remote_on=False)                                            # the timer's: quietly nothing when off
    assert code is None and out == ""
    code, out = run_main(["auto"], remote_on=True)                                             # on, but remote_auto off: also nothing
    assert code is None and out == ""
    code, _ = run_main(["queue"], remote_on=False)
    assert "remote_assist" in code
    code, _ = run_main(["bogus"])
    assert code == remote.__doc__
    # sessions: JSON, no remote_assist needed
    assist.sessions = lambda: [{"slot": 1, "row": 3, "tab": "other", "company": "Acme", "host": "h", "pid": 77, "run": "/x/y",
                                "attention": {"kind": "waiting"}}]
    code, out = run_main(["sessions"], remote_on=False)
    assert code is None and json.loads(out) == [{"slot": 1, "row": 3, "tab": "other", "company": "Acme", "host": "h",
                                                 "attention": {"kind": "waiting"}}]
    assist.sessions = real_sessions
    # queue prints what the queue holds
    remote.queue_now = lambda manual=False: ([(cand("other", 5, "e.com"), None)], [(cand("jobs", 1, "a.com"), "waiting for a free slot")])
    code, out = run_main(["queue"])
    assert code is None and "Starts by itself: off" in out and "9:00-22:00" in out and "row 5" in out and "waiting for a free slot" in out
    assert "next" in out
    # start <tab> <row>
    started_with = []
    remote.start = lambda tab, rown: started_with.append((tab, rown)) or "tmux is not installed"
    code, _ = run_main(["start", "other", "12"])
    assert code == "tmux is not installed" and started_with == [("other", 12)]
    remote.start = lambda tab, rown: None
    code, out = run_main(["start", "nuworks", "5"])
    assert code is None and "nuauto-nuworks-5" in out and "tmux attach -t nuauto-nuworks-5" in out
    for bad in (["start", "other"], ["start", "other", "x"], ["start", "bogus", "1"], ["start", "other", "1", "--bogus"],
                ["start", "other", "1", "--again", "x"]):
        assert run_main(bad)[0] == remote.__doc__
    # an approved Submit nobody said the outcome of: refused unless --again; an answer (y / n) settles it
    started_with.clear()
    remote.start = lambda tab, rown: started_with.append((tab, rown)) or None
    saved_path = remote.AUTO_PATH
    remote.AUTO_PATH = os.path.join(config.LOCAL_DIR, "assist_auto_start.json")
    try:
        with open(remote.AUTO_PATH, "w") as f:
            json.dump({"other-12": {"url": "https://x/1", "approved": True, "answer": None}}, f)
        code, _ = run_main(["start", "other", "12"])
        assert isinstance(code, str) and code.startswith("Not started:") and "nobody said whether it went through" in code and started_with == []
        code, out = run_main(["start", "other", "12", "--again"])
        assert code is None and started_with == [("other", 12)] and "nuauto-other-12" in out
        started_with.clear()
        for ans in ("y", "n"):
            with open(remote.AUTO_PATH, "w") as f:
                json.dump({"other-12": {"url": "https://x/1", "approved": True, "answer": ans}}, f)
            assert run_main(["start", "other", "12"])[0] is None
        with open(remote.AUTO_PATH, "w") as f:
            json.dump({"other-12": {"url": "https://x/1", "approved": False, "answer": None}}, f)
        assert run_main(["start", "other", "12"])[0] is None and len(started_with) == 3          # not approved: nothing unknown
    finally:
        remote.AUTO_PATH = saved_path
finally:
    assist.sessions = real_sessions
restore('queue_now', 'start')       # the module's own again

# ---- start(): the tmux command, then a real tmux
real_which, real_self_cmd = remote.shutil.which, config.self_cmd
captured = []


def scripted(dead):
    def f(argv, **kw):
        captured.append(argv)
        rc, out = 0, ""
        if argv[:2] == ["tmux", "has-session"]:
            rc = 0 if dead is not None else 1
        elif argv[:2] == ["tmux", "display-message"]:
            out = "1\n" if dead else "0\n"
        return subprocess.CompletedProcess(argv, rc, out, "err text")
    return f


try:
    remote.shutil.which = lambda n: {"tmux": "/usr/bin/tmux", "systemd-run": "/usr/bin/systemd-run"}.get(n)
    subprocess.run = scripted(None)                                                         # no such session yet
    assert remote.start("other", 12) is None
    argv = captured[-1]
    assert argv[:5] == ["systemd-run", "--user", "--scope", "--quiet", "--collect"] and argv[5].startswith("--unit=nuauto-assist-other-12-")
    i = argv.index("tmux")
    assert argv[i + 1:i + 6] == ["new-session", "-d", "-s", "nuauto-other-12", "-x"]
    shell = argv[argv.index(";") - 1]
    assert shell.startswith(f"cd {shlex.quote(config.PROJECT_DIR)} && exec ") and shell.endswith(" assist other 12"), shell
    assert shlex.quote(sys.executable) in shell and "-m nuauto" in shell
    assert argv[argv.index(";"):] == [";", "set-option", "-t", "=nuauto-other-12:", "remain-on-exit", "on"]
    assert remote.start("jobs", 5) is None
    assert captured[-1][captured[-1].index("tmux") + 4] == "nuauto-nuworks-5" and captured[-1][-6] == ";"
    assert "exec" in captured[-1][captured[-1].index(";") - 1] and captured[-1][captured[-1].index(";") - 1].endswith(" assist 5")
    assert "--unit=nuauto-assist-nuworks-5-" in " ".join(captured[-1])
    remote.shutil.which = lambda n: {"tmux": "/usr/bin/tmux"}.get(n)                         # no systemd-run: tmux alone
    assert remote.start("other", 12) is None and captured[-1][0] == "tmux" and captured[-1][1] == "new-session"
    # a same-name session: live = refuse; ended = killed first, then replaced
    subprocess.run = scripted(False)
    captured.clear()
    assert remote.start("other", 12) == "tmux session nuauto-other-12 exists already"
    assert [c[1] for c in captured] == ["has-session", "display-message"]
    subprocess.run = scripted(True)
    captured.clear()
    assert remote.start("other", 12) is None
    assert [c[1] for c in captured] == ["has-session", "display-message", "kill-session", "new-session"]
    assert captured[2] == ["tmux", "kill-session", "-t", "=nuauto-other-12"]
    # failures
    subprocess.run = lambda argv, **kw: subprocess.CompletedProcess(argv, 1 if argv[1] == "new-session" else 1, "", "boom: no server")
    assert remote.start("other", 12) == "tmux: boom: no server"
    subprocess.run = lambda argv, **kw: (_ for _ in ()).throw(OSError("x")) if argv[1] == "new-session" else subprocess.CompletedProcess(argv, 1, "", "")
    assert remote.start("other", 12).startswith("could not start tmux")
    remote.shutil.which = lambda n: None
    assert remote.start("other", 12) == "tmux is not installed"
finally:
    subprocess.run, remote.shutil.which = real_run, real_which

if not HAVE_TMUX:
    print("(skipped the real tmux checks: tmux is not installed here)")
else:
    def has(name):
        return subprocess.run(["tmux", "has-session", "-t", f"={name}"], capture_output=True).returncode == 0

    ROW = 987654
    NAME = remote.tmux_name("other", ROW)
    assert NAME == f"nuauto-other-{ROW}"
    remote.shutil.which = lambda n: None if n == "systemd-run" else real_which(n)   # never a real systemd scope
    try:
        config.self_cmd = lambda *a: ["sleep", "30"]
        assert remote.ended("nuauto-no-such-session") is True                        # gone counts as ended
        assert remote.start("other", ROW) is None and has(NAME)
        assert remote.ended(NAME) is False                                           # a live one
        assert remote.start("other", ROW) == f"tmux session {NAME} exists already"   # refuses over a live one
        subprocess.run(["tmux", "kill-session", "-t", f"={NAME}"], check=True)
        assert not has(NAME)
        # a program that ends: the session stays (remain-on-exit) and counts as ended; a new start replaces it
        config.self_cmd = lambda *a: ["sh", "-c", "sleep 1; exit 0"]
        assert remote.start("other", ROW) is None
        assert remote.ended(NAME) is False
        for _ in range(40):
            if remote.ended(NAME):
                break
            time.sleep(0.25)
        assert has(NAME) and remote.ended(NAME) is True, "a finished session should stay, dead"
        config.self_cmd = lambda *a: ["sleep", "30"]
        assert remote.start("other", ROW) is None                                    # replaced
        assert has(NAME) and remote.ended(NAME) is False
        assert remote.start("other", ROW).endswith("exists already")
    finally:
        config.self_cmd, remote.shutil.which = real_self_cmd, real_which
        subprocess.run(["tmux", "kill-session", "-t", f"={NAME}"], capture_output=True)
        subprocess.run(["tmux", "kill-server"], capture_output=True)

# ---- auto(): the timer's run
from nuauto import daily  # noqa: E402

sent, started_now, recorded = [], [], []
real_daily, real_page = daily.discord, review.page_url
daily.discord = lambda text: sent.append(text)
review.page_url = lambda slot=None: "http://100.64.0.1:8766/"
remote.AUTO_PATH = os.path.join(config.LOCAL_DIR, "assist_auto_test.json")
remote.STARTUP_CHECK = 0
ca, cb, cc = cand("other", 21, "a.example"), cand("other", 22, "b.example"), cand("jobs", 23, "c.example")
remote.queue_now = lambda manual=False: ([(ca, None), (cb, None), (cc, None)], [])
remote.in_hours = lambda now=None: True
remote.ended = lambda name: name == remote.tmux_name("jobs", 23)


def fake_start(tab, rown):
    started_now.append((tab, rown))
    return "tmux is not installed" if rown == 22 else None


remote.start = fake_start


def run_auto(server=True, remote_on=True, auto_on=True):
    config.IS_SERVER, config.REMOTE_ASSIST = server, remote_on
    config.LOCAL = {**config.LOCAL, "remote_auto": auto_on}
    out, sys.stdout = sys.stdout, io.StringIO()
    try:
        remote.auto()
        return sys.stdout.getvalue()
    finally:
        sys.stdout = out
        config.IS_SERVER, config.REMOTE_ASSIST = REAL_FLAGS


try:
    for kw in (dict(server=False), dict(remote_on=False), dict(auto_on=False)):       # off: nothing at all
        assert run_auto(**kw) == "" and started_now == [] and sent == [] and not os.path.exists(remote.AUTO_PATH), kw
    remote.in_hours = lambda now=None: False
    assert run_auto() == "" and started_now == [] and sent == []                        # out of hours
    remote.in_hours = lambda now=None: True
    printed = run_auto()
    assert started_now == [("other", 21), ("other", 22), ("jobs", 23)]
    assert "not started: tmux is not installed" in printed and "started C21" in printed and "started C23" in printed
    rec = json.load(open(remote.AUTO_PATH))
    assert sorted(rec) == ["jobs-23", "other-21"] and rec["other-21"]["url"] == ca["url"] and "other-22" not in rec   # a failure: not recorded
    assert oct(os.stat(remote.AUTO_PATH).st_mode & 0o777) == "0o600"
    assert len(sent) == 1 and "C21: T" in sent[0] and "C22" not in sent[0] and "C23: T (ended right away" in sent[0]
    assert "tmux attach -t nuauto-nuworks-23" in sent[0] and "C21: T (ended" not in sent[0] and "http://100.64.0.1:8766/" in sent[0]
    # a broken record file: said, nothing starts, no message
    started_now.clear()
    sent.clear()
    remote.start = fake_start
    remote.queue_now = lambda manual=False: (_ for _ in ()).throw(remote.RecordError("assist_auto.json can't be read"))
    printed = run_auto()
    assert "can't be read" in printed and started_now == [] and sent == []
    remote.queue_now = lambda manual=False: ([(ca, None), (cb, None), (cc, None)], [])
    # nothing started: no message
    sent.clear()
    remote.start = lambda tab, rown: "no"
    run_auto()
    assert sent == []
    remote.queue_now = lambda manual=False: ([], [])
    assert run_auto() == "" and sent == []
    # the written record makes plan() leave those rows to you
    go, rest = remote.plan([ca, cb], [], [], remote._read_auto(), 5)
    assert [x for x, _ in go] == [cb] and "started by itself once" in rest[0][1]
finally:
    daily.discord, review.page_url = real_daily, real_page
    restore("queue_now", "in_hours", "start", "ended", "STARTUP_CHECK", "AUTO_PATH")
    config.LOCAL = {k: v for k, v in config.LOCAL.items() if k != "remote_auto"}

# ---- `nuauto assist auto` on the homelab while remote_assist is off: no failure for the timer
import socket as _socket  # noqa: E402

example = json.load(open(os.path.join(config.PROJECT_DIR, "local_config.example.json")))
cli_state = tempfile.mkdtemp(prefix="nuauto-test-remote-cli-")
os.makedirs(os.path.join(cli_state, "local"), mode=0o700)


def cli(argv, **local):
    with open(os.path.join(cli_state, "local", "local_config.json"), "w") as f:
        json.dump({**example, "server_hostname": _socket.gethostname(), "server_dir": "/x", **local}, f)
    env = {**os.environ, "NUAUTO_STATE_DIR": cli_state}
    for k in ("NUAUTO_DEMO", "TMUX"):
        env.pop(k, None)
    return subprocess.run([sys.executable, "-m", "nuauto", *argv], env=env, capture_output=True, text=True, timeout=120,
                          cwd=config.PROJECT_DIR)


r = cli(["assist", "auto"])
assert r.returncode == 0 and r.stdout == "" and r.stderr == "", (r.returncode, r.stdout, r.stderr)
r = cli(["assist", "queue"])
assert r.returncode != 0 and "only with \"remote_assist\"" in (r.stdout + r.stderr), (r.stdout, r.stderr)    # the others still say why
r = cli(["assist", "7"])
assert r.returncode != 0 and "remote_assist" in (r.stdout + r.stderr)
r = cli(["assist", "auto"], remote_assist=True)                                                # on, remote_auto off: quiet too
assert r.returncode == 0 and r.stdout == "", (r.returncode, r.stdout, r.stderr[-300:])

# ---- assist.ask_line
def fed(data, close=True):
    r, w = os.pipe()
    if data:
        os.write(w, data)
    if close:
        os.close(w)
    return os.fdopen(r), w


real_in, out = sys.stdin, sys.stdout
try:
    sys.stdout = io.StringIO()
    sys.stdin, _ = fed(b"  YES \n")
    assert assist.ask_line("Did it? ", 2) == "yes"
    sys.stdin, w = fed(b"", close=False)
    t0 = time.time()
    assert assist.ask_line("Did it? ", 0.3) is None and 0.2 < time.time() - t0 < 3     # nobody answers
    os.write(w, b"n\n")
    assert assist.ask_line("again? ", 2) == "n"                                         # an answer that comes in time
    os.close(w)
    try:
        assist.ask_line("p ", 1)
        raise AssertionError("no EOFError at end of input")
    except EOFError:
        pass
    sys.stdin = io.StringIO("Y\n")                                                       # no timeout: plain input()
    assert assist.ask_line("p ") == "y"
finally:
    sys.stdin, sys.stdout = real_in, out

# ---- assist.main routes the homelab's commands to remote.main
routed = []
real_main = remote.main
remote.main = lambda args: routed.append(list(args))
try:
    for argv in (["auto"], ["queue"], ["start", "other", "3"], ["sessions"], ["claim", "other", "3", "h"], ["unclaim", "other", "3", "-"]):
        assist.main(argv)
    assert routed == [["auto"], ["queue"], ["start", "other", "3"], ["sessions"], ["claim", "other", "3", "h"],
                      ["unclaim", "other", "3", "-"]]
finally:
    remote.main = real_main

# ---- assist.run's claim step
class TTY(io.StringIO):
    def isatty(self):
        return True


ran, claims, unclaims = [], [], []
real_find, real_locked, real_claim, real_unclaim, real_claimed = (assist.find_other_row, assist.run_locked, remote.claim_on_homelab,
                                                                  remote.unclaim_on_homelab, remote.claimed)
URL = "https://jobs.example.com/claimstep"
assist.find_other_row = lambda n, u: ([], row(n, "Approved", URL, company="Acme"), URL)
remote.unclaim_on_homelab = lambda tab, rown: unclaims.append((tab, rown))


def locked(*a):
    ran.append(a)
    if len(ran) == 99:
        raise AssertionError
    return "ran"


def run_step(number, server=False, remote_on=True, claim=(True, None), fail=False):
    def claim_fn(tab, rown, host):
        claims.append((tab, rown, host))
        return claim
    remote.claim_on_homelab = claim_fn
    assist.run_locked = (lambda *a: (_ for _ in ()).throw(RuntimeError("session crashed"))) if fail else locked
    config.IS_SERVER, config.REMOTE_ASSIST = server, remote_on
    real_stdin, sys.stdin = sys.stdin, TTY()
    out, sys.stdout = sys.stdout, io.StringIO()
    try:
        return assist.run(number, None, other=True), sys.stdout.getvalue()
    except SystemExit as ex:
        return ("exit", str(ex.code)), sys.stdout.getvalue()
    except RuntimeError as ex:
        return ("raised", str(ex)), sys.stdout.getvalue()
    finally:
        sys.stdin, sys.stdout = real_stdin, out
        config.IS_SERVER, config.REMOTE_ASSIST = REAL_FLAGS


try:
    # refused by the homelab: out before a run, nothing released that was never taken
    res, _ = run_step(31, claim=(False, "the homelab's assistant is on jobs.example.com now"))
    assert res[0] == "exit" and "Not started: the homelab's assistant is on jobs.example.com now" in res[1]
    assert ran == [] and unclaims == [] and claims == [("other", 31, "jobs.example.com")]
    assert not os.path.exists(config.LOGS_DIR) or os.listdir(config.LOGS_DIR) == []
    assert real_sessions() == []                                                        # the locks it took are free again
    # allowed (with a note): runs, then lets go
    claims.clear()
    res, out_ = run_step(32, claim=(True, "the homelab could not be reached, so nothing there knows this row is taken"))
    assert res == "ran" and len(ran) == 1 and ran[0][0] == 32 and "could not be reached" in out_
    assert unclaims == [("other", 32)]
    # a crash in the session still lets go
    res, _ = run_step(33, fail=True)
    assert res == ("raised", "session crashed") and unclaims[-1] == ("other", 33) and len(unclaims) == 2
    # no remote_assist: the homelab is never asked
    claims.clear()
    res, _ = run_step(34, remote_on=False)
    assert res == "ran" and claims == [] and len(unclaims) == 2
    # on the homelab: it checks the laptop's claims instead of asking
    remote.claimed = lambda tab, rown, h: "the laptop's assistant is on this row now"
    ran.clear()
    res, _ = run_step(35, server=True)
    assert res[0] == "exit" and "Not started: the laptop's assistant is on this row now" in res[1] and ran == [] and claims == []
    remote.claimed = lambda tab, rown, h: None
    res, _ = run_step(36, server=True)
    assert res == "ran" and claims == [] and len(unclaims) == 2                          # no claim of its own on the homelab
finally:
    (assist.find_other_row, assist.run_locked, remote.claim_on_homelab, remote.unclaim_on_homelab,
     remote.claimed) = real_find, real_locked, real_claim, real_unclaim, real_claimed

# ---- review: the queue on the index page, and Start now
config.LOCAL = {**config.LOCAL, "web_listen_host": "127.0.0.1"}
with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    PORT = sk.getsockname()[1]
review.PORT = PORT
HOST = f"127.0.0.1:{PORT}"
srv = ThreadingHTTPServer(("127.0.0.1", PORT), review.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()


def req(method, path, form=None, cookie=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
    h = {"Host": HOST, **(headers or {})}
    if cookie:
        h["Cookie"] = f"{review.COOKIE}={cookie}"
    body = None
    if form is not None:
        body = urlencode(form)
        h["Content-Type"] = "application/x-www-form-urlencoded"
    conn.request(method, path, body=body, headers=h)
    r = conn.getresponse()
    data = r.read()
    out = (r.status, {k.lower(): v for k, v in r.getheaders()}, data.decode())
    conn.close()
    return out


KEY = review.pair(parse_qs(urlparse(review.make_pairing("phone")).query)["c"][0], "Phone")
TOKEN = review.form_token(KEY)
c1 = {**cand("other", 12, "a.example"), "company": "Acme <i>Co</i>"}
c2, c3, c4, c5 = cand("jobs", 13, "b.example"), cand("other", 14, "c.example"), cand("other", 15, "d.example"), cand("jobs", 16, "e.example")
c6 = cand("other", 17, "f.example")
W_BUSY, W_ONCE, W_SLOT, W_USED = ("another session is on b.example", "started by itself once already: yours to start now",
                                  "waiting for a free slot", "this week's (or the total) NUworks applications are used up")
AUTO_VIEW = ([(c1, None)], [(c2, W_BUSY), (c3, W_ONCE), (c4, W_SLOT), (c5, W_USED), (c6, remote.OUTCOME_UNKNOWN)])
MANUAL_VIEW = ([(c1, None), (c3, None)], [(c2, W_BUSY), (c4, W_SLOT), (c5, W_USED), (c6, remote.OUTCOME_UNKNOWN)])
asked = []


def queue_view(manual=False):
    asked.append(manual)
    return MANUAL_VIEW if manual else AUTO_VIEW


remote.queue_now = queue_view
started_now, recorded = [], []
remote.start = lambda tab, rown: started_now.append((tab, rown)) or None
remote.record_started = lambda c, auto=True: recorded.append((c, auto))
assert not hasattr(review, "remote_state")                                            # one rule: remote.plan, manual
try:
    page = req("GET", "/", cookie=KEY)[2]
    assert "<h3>Queue</h3>" in page and "Starts by itself: off" in page and "9:00-22:00" in page
    assert "Acme &lt;i&gt;Co&lt;/i&gt;" in page and "<i>Co</i>" not in page and "starts next" in page
    assert page.count("Start now") == 2                                       # c1 (next) and c3 (yours to start now) only
    assert "name=row value='12'" in page and "name=row value='14'" in page and "name=tab value='other'" in page
    for n in (13, 15, 16, 17):
        assert f"name=row value='{n}'" not in page                            # busy, slot, used up, outcome unknown: no button
    assert W_BUSY in page and W_SLOT in page and "used up" in page and "nobody said whether it went through" in page
    config.LOCAL = {**config.LOCAL, "remote_auto": True, "remote_hours": [8, 20]}
    page = req("GET", "/", cookie=KEY)[2]
    assert "Starts by itself: on" in page and "8:00-20:00" in page
    config.LOCAL = {k: v for k, v in config.LOCAL.items() if k not in ("remote_auto", "remote_hours")}
    remote.queue_now = lambda manual=False: ([(cand("other", n, f"h{n}.example"), None) for n in range(20, 40)], [])
    assert req("GET", "/", cookie=KEY)[2].count("Start now") == 12              # the first twelve
    remote.queue_now = lambda manual=False: ([], [])
    assert "Nothing approved is waiting" in req("GET", "/", cookie=KEY)[2]
    remote.queue_now = lambda manual=False: (_ for _ in ()).throw(RuntimeError("sheet"))
    assert "Queue not readable now (RuntimeError)" in req("GET", "/", cookie=KEY)[2]
    remote.queue_now = lambda manual=False: (_ for _ in ()).throw(remote.RecordError("assist_auto.json can't be read"))
    assert "Queue not readable now (RecordError)" in req("GET", "/", cookie=KEY)[2]
    remote.queue_now = queue_view

    def post_start(form, token=TOKEN, **kw):
        r = req("POST", "/start", form={"t": token, **form}, cookie=KEY, **kw)
        return r[0], unquote(r[1].get("location", ""))

    # who may press it
    assert req("POST", "/start", form={"tab": "other", "row": "12"}, cookie=KEY)[0] == 403
    assert req("POST", "/start", form={"t": "0" * 32, "tab": "other", "row": "12"}, cookie=KEY)[0] == 403
    assert req("POST", "/start", form={"t": TOKEN, "tab": "other", "row": "12"})[0] == 403
    assert post_start({"tab": "other", "row": "12"}, headers={"Origin": "http://evil.example"})[0] == 403
    assert started_now == []
    # a row the queue would start: recorded as started by hand (auto False), asked for as "manual"
    asked.clear()
    code, where = post_start({"tab": "other", "row": "12"})
    assert code == 303 and where == "/?m=Started Acme <i>Co</i>." and started_now == [("other", 12)] and recorded == [(c1, False)]
    assert asked == [True]
    # one that waits for you (started once by itself): the manual view lets it go
    started_now.clear(); recorded.clear()
    assert "Started" in post_start({"tab": "other", "row": "14"})[1] and started_now == [("other", 14)] and recorded == [(c3, False)]
    started_now.clear(); recorded.clear()
    # the others say why, and nothing starts
    for form, why in (({"tab": "jobs", "row": "13"}, W_BUSY), ({"tab": "other", "row": "15"}, W_SLOT), ({"tab": "jobs", "row": "16"}, W_USED),
                      ({"tab": "other", "row": "17"}, remote.OUTCOME_UNKNOWN)):
        assert post_start(form)[1] == f"/?m=Not started: {why}.", form
    for form in ({"tab": "other", "row": "999"}, {"tab": "jobs", "row": "12"}):
        assert post_start(form)[1] == "/?m=Not started: it is not in the queue.", form
    for form in ({"tab": "bogus", "row": "12"}, {"tab": "other", "row": "x"}, {"tab": "other"}, {"row": "12"}, {}):
        assert post_start(form)[1] == "/?m=Nothing to do.", form
    assert started_now == [] and recorded == []
    # tmux refuses: said, not recorded
    remote.start = lambda tab, rown: "tmux session nuauto-other-12 exists already"
    assert post_start({"tab": "other", "row": "12"})[1] == "/?m=Not started: tmux session nuauto-other-12 exists already"
    assert recorded == []
    # the start is logged with the device's name
    remote.start = lambda tab, rown: None
    assert post_start({"tab": "other", "row": "12"})[0] == 303
    assert "Phone started other row 12" in open(os.path.join(config.LOGS_DIR, "review.log")).read()
    # the flash is escaped on the page
    page = req("GET", "/?m=" + "%3Cscript%3Ex%3C%2Fscript%3E", cookie=KEY)[2]
    assert "<script>x</script>" not in page and "&lt;script&gt;x&lt;/script&gt;" in page
finally:
    srv.shutdown()
    restore("queue_now", "start", "record_started")

# ---- gui.homelab_assistants: a cached background read
from nuauto import gui  # noqa: E402

reads, gate = [], threading.Event()
remote.homelab_sessions = lambda: (reads.append(1), gate.wait(5), [{"slot": 1, "tab": "other", "row": 3}])[2]
real_flags = (gui.VIEW, config.DEMO, config.REMOTE_ASSIST, config.IS_SERVER)


def fresh_cache():
    gui._homelab.update({"at": 0.0, "sessions": [], "busy": False})


def wait_idle():
    for _ in range(100):
        if not gui._homelab["busy"]:
            return
        time.sleep(0.05)
    raise AssertionError("the background read did not finish")


try:
    config.REMOTE_ASSIST, config.IS_SERVER, config.DEMO, gui.VIEW = True, False, False, False
    for name, vals in (("VIEW", (True, False, True, False)), ("DEMO", (False, True, True, False)),
                       ("no remote_assist", (False, False, False, False)), ("on the homelab", (False, False, True, True))):
        gui.VIEW, config.DEMO, config.REMOTE_ASSIST, config.IS_SERVER = vals
        fresh_cache()
        assert gui.homelab_assistants() == [] and reads == [] and gui._homelab["busy"] is False, name
    gui.VIEW, config.DEMO, config.REMOTE_ASSIST, config.IS_SERVER = False, False, True, False
    fresh_cache()
    assert gui.homelab_assistants() == []                                    # the first call: nothing yet, a read starts
    assert gui._homelab["busy"] is True
    assert gui.homelab_assistants() == []                                    # still reading: no second read
    gate.set()
    wait_idle()
    assert reads == [1]
    assert gui.homelab_assistants() == [{"slot": 1, "tab": "other", "row": 3}]   # the result
    assert gui.homelab_assistants() == [{"slot": 1, "tab": "other", "row": 3}] and reads == [1]   # not re-read within HOMELAB_EVERY
    assert gui.HOMELAB_EVERY == 60
    gui._homelab["at"] -= gui.HOMELAB_EVERY + 1                              # old: the last answer now, a new read behind it
    assert gui.homelab_assistants() == [{"slot": 1, "tab": "other", "row": 3}]
    wait_idle()
    assert reads == [1, 1]
    # a read that fails still frees the slot for the next one
    def boom():
        raise RuntimeError("ssh")
    remote.homelab_sessions = boom
    gui._homelab["at"] = 0
    threading.excepthook = lambda args: None
    gui.homelab_assistants()
    wait_idle()
    assert gui._homelab["busy"] is False and gui._homelab["at"] > 0
finally:
    gui.VIEW, config.DEMO, config.REMOTE_ASSIST, config.IS_SERVER = real_flags
    threading.excepthook = threading.__excepthook__

# ---- the GUI's Homelab assistants card: the review page's address, Pair this computer, the browser tool's screenshots
real_flags = (gui.VIEW, config.DEMO, config.REMOTE_ASSIST, config.IS_SERVER)
real_local = config.LOCAL
real_pair = review.pair_link_from_homelab
try:
    config.LOCAL = {**config.LOCAL, "web_listen_host": "100.64.0.9"}
    config.REMOTE_ASSIST, config.IS_SERVER, config.DEMO, gui.VIEW = True, False, False, False
    assert gui.homelab_review() == f"http://100.64.0.9:{review.PORT}"           # (an earlier part moved the port)
    for flags in ((True, False, True, False), (False, True, True, False), (False, False, False, False), (False, False, True, True)):
        gui.VIEW, config.DEMO, config.REMOTE_ASSIST, config.IS_SERVER = flags
        assert gui.homelab_review() is None, flags                         # view, demo, off, or the homelab itself
        try:
            gui.act({"kind": "review_pair", "args": {}})
            raise AssertionError("paired while off")
        except gui.Refused:
            pass
    gui.VIEW, config.DEMO, config.REMOTE_ASSIST, config.IS_SERVER = False, False, True, False
    review.pair_link_from_homelab = lambda name: "http://100.64.0.9:8766/pair?c=abc_123"
    act = gui.act
    assert act({"kind": "review_pair", "args": {}}) == {"url": "http://100.64.0.9:8766/pair?c=abc_123"}
    review.pair_link_from_homelab = lambda name: None
    try:
        act({"kind": "review_pair", "args": {}})
        raise AssertionError("no link, no error")
    except gui.Refused as e:
        assert "pairing link" in str(e)
finally:
    gui.VIEW, config.DEMO, config.REMOTE_ASSIST, config.IS_SERVER = real_flags
    config.LOCAL, review.pair_link_from_homelab = real_local, real_pair

# pair_link_from_homelab reads the link out of `nuauto review pair` over ssh (faked); nothing else counts
real_run = subprocess.run
try:
    subprocess.run = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "Open this on the device to pair (once...):\n"
                                                                     "  http://100.64.0.9:8766/pair?c=Zz9-_x\n", "")
    assert review.pair_link_from_homelab("this computer") == "http://100.64.0.9:8766/pair?c=Zz9-_x"
    subprocess.run = lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "refused")
    assert review.pair_link_from_homelab("x") is None
    subprocess.run = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "no link here", "")
    assert review.pair_link_from_homelab("x") is None
    subprocess.run = lambda argv, **kw: (_ for _ in ()).throw(subprocess.TimeoutExpired(argv, 30))
    assert review.pair_link_from_homelab("x") is None
finally:
    subprocess.run = real_run

# Past runs lists the browser tool's screenshots (now in <run>/browser/), not its snapshots or the resume copy
run_d = os.path.join(config.LOGS_DIR, "20990101-000000_assist_other_row9")
os.makedirs(os.path.join(run_d, "browser", "upload"), exist_ok=True)
for f in ("actions.log", "state.json", "browser/page-1.png", "browser/page.md", "browser/upload/Resume.pdf"):
    open(os.path.join(run_d, f), "w").write("x")
listed = next(r for r in gui.logs()["runs"] if r["name"] == os.path.basename(run_d))["files"]
assert listed == ["actions.log", "browser/page-1.png"], listed
assert gui.log_file(os.path.join(run_d, "browser", "page-1.png")).endswith("page-1.png")

print("All remote checks passed.")
