"""Offline checks for the answer banks and job-site logins both machines share (sync.share and its parts).
Run: python tests/test_share.py

No ssh, no network, no sheet: the homelab is a second temp folder, played by a fake sync._remote_py.
"""
import contextlib
import io
import json
import os
import stat
import sys
import tempfile

import tempfile as _tempfile  # noqa: E402
os.environ["NUAUTO_STATE_DIR"] = _tempfile.mkdtemp(prefix="nuauto-test-")  # never the real local/ (logins, sheet)
from nuauto import config

tmp = tempfile.mkdtemp()
LAPTOP, HOMELAB = os.path.join(tmp, "laptop"), os.path.join(tmp, "homelab")
for p in (LAPTOP, HOMELAB):
    os.makedirs(p, mode=0o700)
config.LOCAL_DIR = LAPTOP  # never the real local/
config.DATA_DIR = os.path.join(LAPTOP, "data")
config.LOGS_DIR = os.path.join(tmp, "logs")

from nuauto import accounts  # noqa: E402
from nuauto import answers  # noqa: E402
from nuauto import sync  # noqa: E402

accounts.PATH, accounts.LOCK = os.path.join(LAPTOP, "accounts.json"), os.path.join(LAPTOP, "accounts.lock")


def e(q, a="x", aliases=(), **kw):
    return {"question": q, "aliases": list(aliases), "answer": a, "field_type": "text", **kw}


def _k(entry):
    return entry["question"]


def names(bank):
    return [x["question"] for x in bank]


# ---- merge_bank: each question takes the side that changed it
base = [e("email", "a@x.com"), e("city", "Boston"), e("phone", "1")]
m, c = sync.merge_bank(base, base, base)
assert m == base and c == []
mine = base + [e("github", "me")]                                    # only one side added
assert sync.merge_bank(base, mine, base) == (mine, [])
m, c = sync.merge_bank(base, base, mine)
assert names(m) == ["email", "city", "phone", "github"] and c == []
changed = [e("email", "b@x.com"), base[1], base[2]]                   # only one side changed
assert sync.merge_bank(base, changed, base) == (changed, [])
assert sync.merge_bank(base, base, changed) == (changed, [])
fewer = [base[0], base[2]]                                            # a side that lacks an entry: the merge never deletes
m, c = sync.merge_bank(base, fewer, base)
assert names(m) == ["email", "phone", "city"] and c == []
m, c = sync.merge_bank(base, base, fewer)
assert names(m) == ["email", "city", "phone"] and c == []
m, c = sync.merge_bank(base, fewer, fewer)                            # both lack it: it is gone (nobody has it)
assert names(m) == ["email", "phone"]
# deleted on purpose = in gone (answers.forget): dropped on both, whatever either side has
for gone_arg in ({"city"}, ["city"], {"city": {"when": "2026-10-11T10:00:00.000001", "gone": True}}.keys(), ("city",)):
    m, c = sync.merge_bank(base, base, base, gone_arg)
    assert names(m) == ["email", "phone"] and c == []
m, c = sync.merge_bank(base, fewer, base, {"city"})
assert names(m) == ["email", "phone"]
m, c = sync.merge_bank(base, mine, mine, {"github", "email"})                 # a changed one is dropped too when it was deleted
assert names(m) == ["city", "phone"]
assert sync.merge_bank(base, base, base, {"nothing-like-it"}) == (base, [])
m, c = sync.merge_bank(base, [e("City", "Boston")], base, {"city"})           # keys are lowercased questions
assert "city" not in [x.lower() for x in names(m)]
# a copy that is not known (None: a missing file) is "unchanged since base": nothing is wiped, the other side's news stays
assert sync.merge_bank(base, None, base) == (base, [])
m, c = sync.merge_bank(base, None, mine)
assert names(m) == ["email", "city", "phone", "github"] and c == []
m, c = sync.merge_bank(base, mine, None)
assert m == mine and c == []
assert sync.merge_bank(base, None, None) == (base, [])
m, c = sync.merge_bank(None, None, mine)                                      # no base, nothing here: theirs
assert m == mine
# a stale copy (made from an old read: it lacks entries the base had and holds nothing newer): wipes nothing
stale = [base[0]]
m, c = sync.merge_bank(base, stale, base + [e("github", "me")])
assert names(m) == ["email", "city", "phone", "github"] and c == []
m, c = sync.merge_bank(base, base + [e("github", "me")], stale)
assert names(m) == ["email", "city", "phone", "github"] and c == []
# both changed the same entry differently: mine wins, listed
theirs = [e("email", "t@x.com"), base[1], base[2]]
m, c = sync.merge_bank(base, changed, theirs)
assert m[0]["answer"] == "b@x.com" and c == ["email"] and names(m) == ["email", "city", "phone"]
# both made the same change: not a conflict
assert sync.merge_bank(base, changed, changed) == (changed, [])
# both changed only aliases: both kept, no conflict
mine_a = [e("email", "a@x.com", ["mail"]), base[1], base[2]]
their_a = [e("email", "a@x.com", ["e-mail", "mail"]), base[1], base[2]]
m, c = sync.merge_bank(base, mine_a, their_a)
assert m[0]["aliases"] == ["mail", "e-mail"] and c == [] and m[0]["answer"] == "a@x.com"
# one lacks it, the other changed it: the changed one stays (whichever side it is)
m, c = sync.merge_bank(base, fewer, changed)
assert [x["answer"] for x in m if x["question"] == "email"] == ["b@x.com"] and c == []
m, c = sync.merge_bank(base, changed, fewer)
assert [x["answer"] for x in m if x["question"] == "email"] == ["b@x.com"] and c == []
# merge_gone: both machines' records ({bank: {question: {"when", "gone"}}}), per question the later event
def ev(when, gone=True):
    return {"when": f"2026-10-{when}", "gone": gone}


assert sync.merge_gone(None, None) == {}
assert sync.merge_gone({"answers.json": {"city": ev("01T10:00:00.000001")}},
                       {"answers.json": {"city": ev("03T10:00:00.000001"), "phone": ev("02T10:00:00.000001")},
                        "answers_other.json": {"x": ev("01T00:00:00.000001")}}) == {
    "answers.json": {"city": ev("03T10:00:00.000001"), "phone": ev("02T10:00:00.000001")}, "answers_other.json": {"x": ev("01T00:00:00.000001")}}
assert sync.merge_gone({"answers.json": {"city": ev("05T10:00:00.000001")}}, {"answers.json": {"city": ev("03T10:00:00.000001")}}) == {
    "answers.json": {"city": ev("05T10:00:00.000001")}}
# deleted, then saved again (later): back, from whichever machine said it last
deleted, back = ev("03T10:00:00.000001"), ev("04T10:00:00.000001", False)
assert sync.merge_gone({"answers.json": {"c": deleted}}, {"answers.json": {"c": back}}) == {"answers.json": {"c": back}}
assert sync.merge_gone({"answers.json": {"c": back}}, {"answers.json": {"c": deleted}}) == {"answers.json": {"c": back}}
assert sync.merge_gone({"answers.json": {"c": back}}, {"answers.json": {"c": ev("05T10:00:00.000001")}}) == {"answers.json": {"c": ev("05T10:00:00.000001")}}
assert sync.merge_gone({"answers.json": {"a": ev("01T00:00:00.000001")}}, {"answers.json": None}) == {"answers.json": {"a": ev("01T00:00:00.000001")}}
assert sync.merge_gone({"answers.json": {"a": ev("01T00:00:00.000001")}}, {"answers.json": {"a": "junk"}}) == {"answers.json": {"a": ev("01T00:00:00.000001")}}
assert answers.gone_questions({"answers.json": {"c": deleted, "d": back, "e": "junk"}}, "answers.json") == {"c"}
assert answers.gone_questions({}, "answers.json") == set() and answers.gone_questions({"answers.json": None}, "answers.json") == set()
# no base (first share): union; the same entry once; differing entries -> mine + conflict
m, c = sync.merge_bank(None, [e("email", "a@x.com"), e("city", "Boston")], [e("city", "Boston"), e("phone", "1")])
assert names(m) == ["email", "city", "phone"] and c == []
m, c = sync.merge_bank(None, [e("city", "Boston")], [e("city", "Cambridge")])
assert m == [e("city", "Boston")] and c == ["city"]
assert sync.merge_bank(None, None, None) == ([], [])
# keys are the lowercased question; order is mine's order, then theirs-only
m, c = sync.merge_bank([], [e("Zeta"), e("alpha")], [e("beta"), e("ALPHA")])
assert names(m) == ["Zeta", "alpha", "beta"] and c == ["alpha"]   # "ALPHA" is the same key (the entries differ)

# ---- merge_accounts
def login(pw, created, jobs=(), **kw):
    return {"email": "me@x.com", "password": pw, "created": created, "company": kw.pop("company", ""),
            "jobs": [{"url": u, "title": "t"} for u in jobs], "exported": kw.pop("exported", False), **kw}


a = {"acme.com": login("pw1", "2026-10-01", ["u1"]), "only-mine.com": login("pw2", "2026-10-02")}
b = {"acme.com": login("pw1", "2026-10-01", ["u1"]), "only-theirs.com": login("pw3", "2026-10-03")}
m, clashes = sync.merge_accounts(a, b)
assert list(m) == ["acme.com", "only-mine.com", "only-theirs.com"] and clashes == [] and m["acme.com"] == a["acme.com"]
assert sync.merge_accounts(None, None) == ({}, [])
assert sync.merge_accounts(a, None) == (a, []) and sync.merge_accounts(None, b) == (b, [])
# two different passwords: the earlier `created` stays the login, the other kept, the site listed
a = {"acme.com": login("pw-late", "2026-10-05", ["u2"], exported=True)}
b = {"acme.com": login("pw-early", "2026-10-01", ["u1"], company="Acme")}
m, clashes = sync.merge_accounts(a, b)
got = m["acme.com"]
assert clashes == ["acme.com"] and got["password"] == "pw-early" and got["other_passwords"] == ["pw-late"]
assert [j["url"] for j in got["jobs"]] == ["u1", "u2"] and got["exported"] is True and got["company"] == "Acme"
m2, clashes2 = sync.merge_accounts(b, a)                                 # the same whichever side is "mine"
assert m2["acme.com"]["password"] == "pw-early" and m2["acme.com"]["other_passwords"] == ["pw-late"] and clashes2 == ["acme.com"]
# the same site and password on both, different jobs: one login, not a clash
m, clashes = sync.merge_accounts({"s.com": login("p", "2026-10-01", ["u1"])}, {"s.com": login("p", "2026-10-01", ["u2"])})
assert clashes == [] and "other_passwords" not in m["s.com"]

# ---- notes_files: only what the agent may read
notes = os.path.join(tmp, "notes")
for rel in ("a.md", "sub/b.md", "sub/deep/c.txt", ".git/config", ".git/HEAD", "local/local_config.json", "local/answers.json",
            ".env", "secrets.pem", "sub/id_rsa", "sub/token.json", "sub/.ssh/known_hosts", "browser_profile/x.txt"):
    os.makedirs(os.path.dirname(os.path.join(notes, rel)), exist_ok=True)
    open(os.path.join(notes, rel), "w").write("x")
os.symlink(os.path.join(notes, "a.md"), os.path.join(notes, "link.md"))        # no links
os.symlink(os.path.join(notes, "sub"), os.path.join(notes, "linkdir"))
os.symlink("/etc/hostname", os.path.join(notes, "sub", "hostname"))
got = sync.notes_files(notes)
assert set(got) == {"a.md", os.path.join("sub", "b.md"), os.path.join("sub", "deep", "c.txt")}, got

# ---- the two machines' files, one after the other: shared_out / shared_in / merge_into_*


@contextlib.contextmanager
def at(folder):
    """Run as the machine whose local/ is folder (and whose data/ is folder/data)."""
    old = config.LOCAL_DIR, config.DATA_DIR, accounts.PATH, accounts.LOCK
    config.LOCAL_DIR, config.DATA_DIR = folder, os.path.join(folder, "data")
    accounts.PATH, accounts.LOCK = os.path.join(folder, "accounts.json"), os.path.join(folder, "accounts.lock")
    try:
        yield
    finally:
        config.LOCAL_DIR, config.DATA_DIR, accounts.PATH, accounts.LOCK = old


def write_bank(folder, name, entries):
    with open(os.path.join(folder, name), "w") as f:
        json.dump(entries, f)


def read_bank(folder, name):
    with open(os.path.join(folder, name)) as f:
        return json.load(f)


def write_accounts(folder, data):
    with at(folder), accounts.locked() as d:
        d.update(data)


def read_accounts(folder):
    with at(folder), accounts.locked() as d:
        return dict(d)


def out_of(folder):
    buf, real = io.StringIO(), sys.stdout
    sys.stdout = buf
    try:
        with at(folder):
            sync.shared_out()
    finally:
        sys.stdout = real
    return json.loads(buf.getvalue())


write_bank(HOMELAB, "answers.json", [e("city", "Boston")])
write_accounts(HOMELAB, {"acme.com": login("pw1", "2026-10-01")})
old_flags = config.REMOTE_ASSIST, config.REMOTE_APP, config.IS_SERVER
config.REMOTE_ASSIST = config.REMOTE_APP = True
o = out_of(HOMELAB)
assert o["answers.json"] == [e("city", "Boston")] and o["answers_other.json"] is None and list(o["accounts.json"]) == ["acme.com"]
assert o["gone"] == {} and o["local_config.json"] is None and o["profile.json"] is None and o["ratings.json"] is None
config.REMOTE_ASSIST = False  # the homelab's page alone (no assistant there): the logins stay where they are
assert "accounts.json" not in out_of(HOMELAB)
config.REMOTE_ASSIST = True
with at(HOMELAB):
    conflicts, skipped = sync.merge_into_banks({"answers.json": {"base": [e("city", "Boston")], "merged": [e("city", "Boston"), e("email", "a")]}}, {})
assert conflicts == [] and skipped == [] and names(read_bank(HOMELAB, "answers.json")) == ["city", "email"]
# the deletion records ride along and are applied
with at(HOMELAB):
    conflicts, skipped = sync.merge_into_banks({"answers.json": {"base": None, "merged": [e("city", "Boston"), e("email", "a")]}},
                                               {"answers.json": {"email": ev("11T10:00:00.000001")}})
assert names(read_bank(HOMELAB, "answers.json")) == ["city"] and out_of(HOMELAB)["gone"] == {"answers.json": {"email": ev("11T10:00:00.000001")}}
write_bank(HOMELAB, "answers.json", [e("city", "Boston"), e("email", "a")])
with at(HOMELAB):
    answers.set_gone({})
# a file that can't be read is left alone (and said): in either direction
open(os.path.join(HOMELAB, "answers_other.json"), "w").write("{bad json")
assert out_of(HOMELAB)["answers_other.json"] == sync.UNREADABLE
with at(HOMELAB):
    conflicts, skipped = sync.merge_into_banks({"answers_other.json": {"base": None, "merged": [e("x", "1")]},
                                                "answers.json": {"base": None, "merged": [e("zip", "02115")]}}, {})
assert skipped == ["answers_other.json"] and open(os.path.join(HOMELAB, "answers_other.json")).read() == "{bad json"
assert names(read_bank(HOMELAB, "answers.json")) == ["city", "email", "zip"]                  # the other bank still merges
with at(HOMELAB):
    conflicts, skipped = sync.merge_into_banks({"answers_other.json": {"base": None, "merged": sync.UNREADABLE}}, {})
assert skipped == ["answers_other.json"]
os.remove(os.path.join(HOMELAB, "answers_other.json"))
assert sync._read_json(os.path.join(HOMELAB, "nothing.json"), "dflt") == "dflt"
write_bank(HOMELAB, "answers.json", [e("city", "Boston"), e("email", "a")])
assert stat.S_IMODE(os.stat(os.path.join(HOMELAB, "answers.json")).st_mode) == 0o600
with at(HOMELAB):
    merged, clashes = sync.merge_into_accounts({"acme.com": login("pw-other", "2026-10-09"), "new.com": login("pw9", "2026-10-09")})
assert set(merged) == {"acme.com", "new.com"} and clashes == ["acme.com"]
assert read_accounts(HOMELAB)["acme.com"]["other_passwords"] == ["pw-other"]
before_acc = open(os.path.join(HOMELAB, "accounts.json")).read()
with at(HOMELAB):
    assert sync.merge_into_accounts(sync.UNREADABLE) == (None, [])                           # the other side's unreadable: nothing changes
assert open(os.path.join(HOMELAB, "accounts.json")).read() == before_acc
open(os.path.join(HOMELAB, "accounts.json"), "w").write("{bad")                              # this side's unreadable
with at(HOMELAB):
    assert sync.merge_into_accounts({"x.com": login("p", "2026-10-01")}) == (None, [])
assert open(os.path.join(HOMELAB, "accounts.json")).read() == "{bad" and out_of(HOMELAB)["accounts.json"] == sync.UNREADABLE
open(os.path.join(HOMELAB, "accounts.json"), "w").write(before_acc)
assert stat.S_IMODE(os.stat(os.path.join(HOMELAB, "accounts.json")).st_mode) == 0o600
# a bank file that does not exist yet is made (never an error) and a bank with no change is not rewritten
with at(HOMELAB):
    sync.shared_in({"answers_other.json": {"base": None, "merged": [e("x", "1")]}})
    assert names(read_bank(HOMELAB, "answers_other.json")) == ["x"]
    before = os.stat(os.path.join(HOMELAB, "answers_other.json")).st_mtime_ns
    sync.shared_in({"answers_other.json": {"base": [e("x", "1")], "merged": [e("x", "1")]}})
    assert os.stat(os.path.join(HOMELAB, "answers_other.json")).st_mtime_ns == before

# ---- share(): the laptop's side, with a fake homelab
for f in os.listdir(HOMELAB):
    os.remove(os.path.join(HOMELAB, f))
for f in os.listdir(LAPTOP):
    os.remove(os.path.join(LAPTOP, f))
calls = []
state = {"fail_in": False, "fail_out": False, "garbage": False, "meanwhile": None}


def fake_remote(command, stdin=None):
    calls.append(command)
    if command == "shared-out":
        if state["fail_out"]:
            return None
        if state["garbage"]:
            return "not json"
        out = json.dumps(out_of(HOMELAB))
        if state["meanwhile"]:       # the homelab's assistant saves something after the laptop read its banks
            state["meanwhile"]()
        return out
    assert command == "shared-in"
    if state["fail_in"]:
        return None
    with at(HOMELAB):
        sync.shared_in(json.loads(stdin))
    return ""


def run_share():
    buf, real = io.StringIO(), sys.stdout
    sys.stdout = buf
    try:
        return sync.share(), buf.getvalue()
    finally:
        sys.stdout = real


real_remote = sync._remote_py
sync._remote_py = fake_remote
try:
    # off (no NUauto on the homelab), or on the homelab itself: nothing happens, no ssh
    config.REMOTE_ASSIST, config.REMOTE_APP, config.IS_SERVER = False, False, False
    assert run_share() == (True, "") and calls == []
    config.REMOTE_ASSIST, config.REMOTE_APP, config.IS_SERVER = True, True, True
    assert run_share() == (True, "") and calls == []
    config.REMOTE_ASSIST, config.REMOTE_APP, config.IS_SERVER = True, True, False

    # first share: no base file; the union goes both ways; the base is written after the send
    write_bank(LAPTOP, "answers.json", [e("email", "a@x.com"), e("city", "Boston")])
    write_bank(HOMELAB, "answers.json", [e("city", "Boston"), e("phone", "1")])
    write_accounts(LAPTOP, {"acme.com": login("pw1", "2026-10-01")})
    write_accounts(HOMELAB, {"beta.com": login("pw2", "2026-10-02")})
    ok, printed = run_share()
    assert ok and printed == "" and calls == ["shared-out", "shared-in"]
    for folder in (LAPTOP, HOMELAB):
        assert sorted(names(read_bank(folder, "answers.json"))) == ["city", "email", "phone"], folder
        assert set(read_accounts(folder)) == {"acme.com", "beta.com"}, folder
    base_path = os.path.join(LAPTOP, sync.BASE)
    assert stat.S_IMODE(os.stat(base_path).st_mode) == 0o600
    base = json.load(open(base_path))
    assert names(base["answers.json"]) == ["email", "city", "phone"] and "answers_other.json" not in base

    # a change on each machine, to different answers: both survive; deletes go across too
    calls.clear()
    write_bank(LAPTOP, "answers.json", [e("email", "new@x.com"), e("city", "Boston"), e("phone", "1")])
    write_bank(HOMELAB, "answers.json", [e("city", "Cambridge"), e("phone", "1")])   # changed city, deleted email
    ok, printed = run_share()
    got = {x["question"]: x["answer"] for x in read_bank(LAPTOP, "answers.json")}
    assert ok and got == {"email": "new@x.com", "city": "Cambridge", "phone": "1"}, got   # laptop changed email, homelab city
    assert sorted(read_bank(HOMELAB, "answers.json"), key=_k) == sorted(read_bank(LAPTOP, "answers.json"), key=_k) and printed == ""   # (order may differ)

    # conflict: both changed one answer: the laptop's stays, and it is printed
    write_bank(LAPTOP, "answers.json", [e("email", "new@x.com"), e("city", "Somerville"), e("phone", "1")])
    write_bank(HOMELAB, "answers.json", [e("email", "new@x.com"), e("city", "Quincy"), e("phone", "1")])
    ok, printed = run_share()
    assert ok and "changed on both machines" in printed and "city" in printed
    assert {x["question"]: x["answer"] for x in read_bank(HOMELAB, "answers.json")}["city"] == "Somerville"

    # a change the homelab makes after the laptop read it (in the meantime) survives
    state["meanwhile"] = lambda: write_bank(HOMELAB, "answers.json", read_bank(HOMELAB, "answers.json") + [e("late", "saved by its assistant")])
    write_bank(LAPTOP, "answers.json", read_bank(LAPTOP, "answers.json") + [e("fresh", "laptop")])
    ok, printed = run_share()
    state["meanwhile"] = None
    assert ok and "late" in names(read_bank(HOMELAB, "answers.json")) and "fresh" in names(read_bank(HOMELAB, "answers.json"))
    assert "late" not in names(read_bank(LAPTOP, "answers.json"))        # the laptop gets it next time
    ok, _ = run_share()
    assert ok and "late" in names(read_bank(LAPTOP, "answers.json"))

    # two passwords for one site: both kept, the clash printed, on both machines
    write_accounts(LAPTOP, {"clash.com": login("pw-l", "2026-10-05")})
    write_accounts(HOMELAB, {"clash.com": login("pw-h", "2026-10-02")})
    ok, printed = run_share()
    assert ok and "two passwords for clash.com" in printed and "pw-" not in printed
    for folder in (LAPTOP, HOMELAB):
        assert read_accounts(folder)["clash.com"]["password"] == "pw-h"
        assert read_accounts(folder)["clash.com"]["other_passwords"] == ["pw-l"]

    # a failed send: False, message, the base file is NOT updated; the laptop's own merge is kept
    base_before = open(base_path).read()
    write_bank(LAPTOP, "answers.json", read_bank(LAPTOP, "answers.json") + [e("unsent", "1")])
    state["fail_in"] = True
    ok, printed = run_share()
    state["fail_in"] = False
    assert not ok and "could not send" in printed and open(base_path).read() == base_before
    assert "unsent" in names(read_bank(LAPTOP, "answers.json")) and "unsent" not in names(read_bank(HOMELAB, "answers.json"))
    ok, _ = run_share()                                                    # next time it goes through
    assert ok and "unsent" in names(read_bank(HOMELAB, "answers.json")) and open(base_path).read() != base_before

    # an unreachable or garbled homelab: False, nothing sent, laptop files untouched
    calls.clear()
    laptop_before = open(os.path.join(LAPTOP, "answers.json")).read()
    for key in ("fail_out", "garbage"):
        state[key] = True
        ok, printed = run_share()
        state[key] = False
        assert not ok and "could not read the homelab" in printed and calls[-1] == "shared-out"
    assert "shared-in" not in calls and open(os.path.join(LAPTOP, "answers.json")).read() == laptop_before

    # ---- the merge never deletes by itself
    def reset():
        for folder in (LAPTOP, HOMELAB):
            for f_ in os.listdir(folder):
                os.remove(os.path.join(folder, f_))
    abc = [e("a", "1"), e("b", "2"), e("c", "3")]
    reset()
    write_bank(LAPTOP, "answers.json", abc)
    assert run_share()[0]
    assert names(read_bank(HOMELAB, "answers.json")) == ["a", "b", "c"]
    # the homelab's file is missing (a rebuilt box): nothing is wiped on the laptop, the homelab gets the laptop's
    os.remove(os.path.join(HOMELAB, "answers.json"))
    assert run_share()[0]
    assert names(read_bank(LAPTOP, "answers.json")) == ["a", "b", "c"] and names(read_bank(HOMELAB, "answers.json")) == ["a", "b", "c"]
    # the laptop's file is missing: it takes the homelab's
    os.remove(os.path.join(LAPTOP, "answers.json"))
    assert run_share()[0] and names(read_bank(LAPTOP, "answers.json")) == ["a", "b", "c"]
    assert names(read_bank(HOMELAB, "answers.json")) == ["a", "b", "c"]
    # a stale save (the laptop's copy lacks entries the last agreement had): wipes nothing, on either side
    write_bank(LAPTOP, "answers.json", [e("a", "1")])
    assert run_share()[0]
    assert names(read_bank(LAPTOP, "answers.json")) == ["a", "b", "c"] and names(read_bank(HOMELAB, "answers.json")) == ["a", "b", "c"]
    write_bank(HOMELAB, "answers.json", [e("a", "1")])                                      # and the same the other way
    assert run_share()[0]
    assert names(read_bank(LAPTOP, "answers.json")) == ["a", "b", "c"] and sorted(names(read_bank(HOMELAB, "answers.json"))) == ["a", "b", "c"]
    # no base file at all (a first share on a laptop that lost it): the union
    os.remove(os.path.join(LAPTOP, sync.BASE))
    write_bank(HOMELAB, "answers.json", [e("a", "1"), e("z", "9")])
    assert run_share()[0]
    assert sorted(names(read_bank(LAPTOP, "answers.json"))) == ["a", "b", "c", "z"] and sorted(names(read_bank(HOMELAB, "answers.json"))) == ["a", "b", "c", "z"]
    # a deletion on purpose (the GUI's Answers screen): recorded, then deleted on both machines, and it stays deleted
    from nuauto import gui
    config.ANSWERS_PATH, config.OTHER_ANSWERS_PATH = os.path.join(LAPTOP, "answers.json"), os.path.join(LAPTOP, "answers_other.json")
    reset()
    write_bank(LAPTOP, "answers.json", abc)
    assert run_share()[0]
    version = gui.answers_get("nuworks")["version"]
    keep = [{**x, "field_type": "text"} for x in abc if x["question"] != "c"]
    gui.answers_put({"bank": "nuworks", "entries": keep, "version": version})
    assert names(read_bank(LAPTOP, "answers.json")) == ["a", "b"]
    gq = lambda records=None: answers.gone_questions(records if records is not None else answers.gone_all(), "answers.json")  # noqa: E731
    g = answers.gone_all()
    assert list(g) == ["answers.json"] and list(g["answers.json"]) == ["c"]
    assert g["answers.json"]["c"]["gone"] is True and "." in g["answers.json"]["c"]["when"]       # (with microseconds)
    assert stat.S_IMODE(os.stat(os.path.join(LAPTOP, "answers_gone.json")).st_mode) == 0o600
    assert names(read_bank(HOMELAB, "answers.json")) == ["a", "b", "c"]
    ok, printed = run_share()
    assert ok and printed == ""
    assert names(read_bank(LAPTOP, "answers.json")) == ["a", "b"] and names(read_bank(HOMELAB, "answers.json")) == ["a", "b"]
    with at(HOMELAB):
        assert gq() == {"c"}                                                                    # the homelab knows too
    assert run_share()[0] and names(read_bank(HOMELAB, "answers.json")) == ["a", "b"]           # and it stays deleted
    # a deletion made on the homelab's side only (a record there) reaches the laptop
    with at(HOMELAB):
        answers.forget(os.path.join(HOMELAB, "answers.json"), ["b"])
    assert run_share()[0]
    assert names(read_bank(LAPTOP, "answers.json")) == ["a"] and names(read_bank(HOMELAB, "answers.json")) == ["a"]
    # an entry you only change (not delete) in the GUI leaves no new record
    version = gui.answers_get("nuworks")["version"]
    gui.answers_put({"bank": "nuworks", "entries": [{**keep[0], "answer": "changed"}], "version": version})
    assert gq() == {"b", "c"} and sorted(answers.gone_all()["answers.json"]) == ["b", "c"]
    # a question deleted, then saved again: "back" (a newer event, kept in the records; answers.save)
    answers.save([e("a", "1"), e("c", "again")], os.path.join(LAPTOP, "answers.json"))
    assert gq() == {"b"} and answers.gone_all()["answers.json"]["c"]["gone"] is False
    answers.forget(os.path.join(LAPTOP, "answers.json"), ["  Zed ", "", "  "])
    assert gq() == {"b", "zed"}
    answers.forget(os.path.join(LAPTOP, "answers.json"), [])
    answers.save([e("a", "1"), e("Zed", "x")], os.path.join(LAPTOP, "answers.json"))
    assert gq() == {"b"}
    answers.save([e("never-deleted", "1")], os.path.join(LAPTOP, "answers.json"))              # no record for a question never deleted
    assert "never-deleted" not in answers.gone_all()["answers.json"]
    answers.save([], os.path.join(LAPTOP, "answers_other.json"))                              # another bank: its own records
    assert gq() == {"b"} and "answers_other.json" not in answers.gone_all()

    # a deleted question added again later (both machines already hold the deletion) stays: on the laptop's side...
    reset()
    write_bank(LAPTOP, "answers.json", abc)
    assert run_share()[0]
    gui.answers_put({"bank": "nuworks", "entries": [{**x, "field_type": "text"} for x in abc[:2]], "version": gui.answers_get("nuworks")["version"]})
    assert run_share()[0] and names(read_bank(HOMELAB, "answers.json")) == ["a", "b"]
    with at(HOMELAB):
        assert gq() == {"c"}
    answers.save([e("a", "1"), e("b", "2"), e("c", "back")], os.path.join(LAPTOP, "answers.json"))
    assert run_share()[0]
    for folder in (LAPTOP, HOMELAB):
        assert {x["question"]: x["answer"] for x in read_bank(folder, "answers.json")}.get("c") == "back", folder
        with at(folder):
            assert gq() == set()
    assert run_share()[0] and names(read_bank(HOMELAB, "answers.json")).count("c") == 1       # and it is not deleted again later
    assert names(read_bank(LAPTOP, "answers.json")).count("c") == 1
    # ...and on the homelab's side (its assistant saved it again after the laptop deleted it)
    gui.answers_put({"bank": "nuworks", "entries": [{**x, "field_type": "text"} for x in abc[:2]], "version": gui.answers_get("nuworks")["version"]})
    assert run_share()[0] and "c" not in names(read_bank(HOMELAB, "answers.json")) and "c" not in names(read_bank(LAPTOP, "answers.json"))
    with at(HOMELAB):
        answers.save(read_bank(HOMELAB, "answers.json") + [e("c", "homelab again")], os.path.join(HOMELAB, "answers.json"))
    assert run_share()[0]
    for folder in (LAPTOP, HOMELAB):
        assert {x["question"]: x["answer"] for x in read_bank(folder, "answers.json")}.get("c") == "homelab again", folder
    # deleted on the laptop AFTER the homelab saved it: the later event wins, deleted everywhere
    gui.answers_put({"bank": "nuworks", "entries": [{**x, "field_type": "text"} for x in abc[:2]], "version": gui.answers_get("nuworks")["version"]})
    assert run_share()[0] and "c" not in names(read_bank(HOMELAB, "answers.json")) and "c" not in names(read_bank(LAPTOP, "answers.json"))
    config.ANSWERS_PATH = os.path.join(tmp, "unused-answers.json")
    # a bank that can't be read on one machine is left alone and said; the other bank and the logins still merge
    reset()
    write_bank(LAPTOP, "answers.json", abc)
    write_bank(LAPTOP, "answers_other.json", [e("o", "1")])
    write_accounts(LAPTOP, {"acme.com": login("pw1", "2026-10-01")})
    assert run_share()[0]
    open(os.path.join(HOMELAB, "answers_other.json"), "w").write("{bad json")                  # hand-edited, mid-edit
    write_bank(LAPTOP, "answers.json", abc + [e("d", "4")])
    write_bank(LAPTOP, "answers_other.json", [e("o", "1"), e("p", "2")])
    ok, printed = run_share()
    assert ok and "answers_other.json can't be read on one of the machines" in printed and "left alone" in printed
    assert open(os.path.join(HOMELAB, "answers_other.json")).read() == "{bad json"
    assert names(read_bank(LAPTOP, "answers_other.json")) == ["o", "p"]
    assert names(read_bank(HOMELAB, "answers.json")) == ["a", "b", "c", "d"]
    assert "answers_other.json" not in json.load(open(base_path)) and "answers.json" in json.load(open(base_path))   # no agreement recorded for it
    os.remove(os.path.join(HOMELAB, "answers_other.json"))
    open(os.path.join(LAPTOP, "answers_other.json"), "w").write("[oops")                       # and on the laptop's side
    write_bank(HOMELAB, "answers.json", read_bank(HOMELAB, "answers.json") + [e("e", "5")])
    ok, printed = run_share()
    assert ok and "answers_other.json can't be read" in printed and open(os.path.join(LAPTOP, "answers_other.json")).read() == "[oops"
    assert not os.path.exists(os.path.join(HOMELAB, "answers_other.json"))                    # nothing was sent for it
    assert "e" in names(read_bank(LAPTOP, "answers.json"))
    # logins: an unreadable accounts.json is left alone, on either side
    before_h = open(os.path.join(HOMELAB, "accounts.json")).read()
    open(os.path.join(LAPTOP, "accounts.json"), "w").write("{nope")
    assert run_share()[0] and open(os.path.join(LAPTOP, "accounts.json")).read() == "{nope" and open(os.path.join(HOMELAB, "accounts.json")).read() == before_h
    open(os.path.join(LAPTOP, "accounts.json"), "w").write(before_h)
    open(os.path.join(HOMELAB, "accounts.json"), "w").write("{nope")
    laptop_before = open(os.path.join(LAPTOP, "accounts.json")).read()
    assert run_share()[0] and open(os.path.join(HOMELAB, "accounts.json")).read() == "{nope" and open(os.path.join(LAPTOP, "accounts.json")).read() == laptop_before

    # pull() shares only with NUauto on the homelab (its page, its assistant)
    ran, shared = [], []
    real_run, real_share = sync._run, sync.share
    sync._run = lambda cmd, what: ran.append(what) or True
    sync.share = lambda: shared.append(1) or True
    try:
        config.REMOTE_APP = False
        assert sync.pull() is True and shared == []
        config.REMOTE_APP = True
        assert sync.pull() is True and shared == [1] and ran == ["pull", "pull"]
    finally:
        sync._run, sync.share = real_run, real_share

    # ---- settings, the NUworks resume label and ratings: per key, both ways (FILES)
    def write_json(path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f)

    os.remove(os.path.join(LAPTOP, "answers_other.json"))  # (the unreadable one from above)
    cfg_l, cfg_h = os.path.join(LAPTOP, "local_config.json"), os.path.join(HOMELAB, "local_config.json")
    rat_l, rat_h = os.path.join(LAPTOP, "data", "ratings.json"), os.path.join(HOMELAB, "data", "ratings.json")
    write_json(cfg_l, {"sheet_id": "S", "max_per_week": 11, "tools": {"claude": "/laptop/claude"}, "remote_hours": [9, 22]})
    write_json(cfg_h, {"sheet_id": "S", "max_per_week": 11, "tools": {"claude": "/laptop/claude"}, "remote_hours": [9, 22]})
    write_json(rat_l, {"1": {"label": 1, "date": "2026-10-01"}})
    write_json(rat_h, {"1": {"label": 1, "date": "2026-10-01"}})
    write_json(os.path.join(HOMELAB, "profile.json"), {"resume_label": "Old"})
    write_json(os.path.join(LAPTOP, "profile.json"), {"resume_label": "Old"})
    # the first merge (no base yet): the laptop's copy as it is, as push() copied it before (an old homelab copy's
    # leftovers never come back)
    write_json(cfg_h, {**json.load(open(cfg_h)), "max_per_week": 9, "internships": {}})
    write_json(rat_h, {**json.load(open(rat_h)), "old": {"label": 0, "date": "2026-09-01"}})
    ok, printed = run_share()                                                 # agree once: the base
    assert ok and sync.SHARED_FILES == {"local_config.json", "profile.json", "ratings.json"}
    for cfg in (cfg_l, cfg_h):
        c = json.load(open(cfg))
        assert c["max_per_week"] == 11 and "internships" not in c, (cfg, c)
    assert sorted(json.load(open(rat_h))) == ["1"] == sorted(json.load(open(rat_l)))
    # the homelab's page changed the cap and rated a job; the laptop changed the hours and the resume label and rated another
    write_json(cfg_h, {**json.load(open(cfg_h)), "max_per_week": 14, "tools": {"claude": "/homelab/claude"}})
    write_json(rat_h, {**json.load(open(rat_h)), "2": {"label": 0, "date": "2026-10-14"}})
    write_json(cfg_l, {**json.load(open(cfg_l)), "remote_hours": [8, 20]})
    write_json(rat_l, {**json.load(open(rat_l)), "3": {"label": 1, "date": "2026-10-14"}})
    write_json(os.path.join(LAPTOP, "profile.json"), {"resume_label": "New"})
    ok, printed = run_share()
    assert ok and printed == "", printed
    for cfg in (cfg_l, cfg_h):
        c = json.load(open(cfg))
        assert c["max_per_week"] == 14 and c["remote_hours"] == [8, 20] and c["sheet_id"] == "S", (cfg, c)
        assert c["tools"] == {"claude": "/laptop/claude"}, (cfg, c)              # the laptop's tool paths, on both
    for rat in (rat_l, rat_h):
        assert sorted(json.load(open(rat))) == ["1", "2", "3"], rat
    for folder in (LAPTOP, HOMELAB):
        assert json.load(open(os.path.join(folder, "profile.json"))) == {"resume_label": "New"}
    assert stat.S_IMODE(os.stat(os.path.join(HOMELAB, "profile.json")).st_mode) == 0o600   # written by the merge
    assert stat.S_IMODE(os.stat(cfg_h).st_mode) == 0o644
    # changed on both: the laptop's kept, and said; a rating taken away on one side (Undo) goes on both
    write_json(cfg_h, {**json.load(open(cfg_h)), "max_per_week": 15})
    write_json(cfg_l, {**json.load(open(cfg_l)), "max_per_week": 12})
    r = json.load(open(rat_h))
    del r["2"]
    write_json(rat_h, r)
    ok, printed = run_share()
    assert ok and "max_per_week (local_config.json)" in printed, printed
    assert json.load(open(cfg_l))["max_per_week"] == 12 == json.load(open(cfg_h))["max_per_week"]
    assert sorted(json.load(open(rat_l))) == ["1", "3"] == sorted(json.load(open(rat_h)))
    # an older homelab sends no FILES: nothing of them is merged (push() then copies them, as before)
    real_out = sync.shared_out
    try:
        sync.shared_out = lambda: print(json.dumps({"answers.json": [], "answers_other.json": None, "gone": {}}))
        ok, _ = run_share()
        assert ok and sync.SHARED_FILES == set()
    finally:
        sync.shared_out = real_out
    # the homelab can't be asked: nothing merged, push() copies them
    state["fail_out"] = True
    ok, printed = run_share()
    assert not ok and sync.SHARED_FILES == set() and "could not read the homelab's" in printed
    state["fail_out"] = False
finally:
    sync._remote_py = real_remote
    config.REMOTE_ASSIST, config.REMOTE_APP, config.IS_SERVER = old_flags

# ---- merge_dict alone (pure)
m, c = sync.merge_dict({"a": 1, "b": 2, "c": 3}, {"a": 1, "b": 20, "c": 3, "d": 4}, {"a": 10, "b": 2, "e": 5})
assert m == {"a": 10, "b": 20, "d": 4, "e": 5} and c == []      # c: gone on their side, unchanged on mine: gone
m, c = sync.merge_dict({"a": 1}, {"a": 2}, {"a": 3})
assert m == {"a": 2} and c == ["a"]
m, c = sync.merge_dict({"t": 1}, {"t": 2}, {"t": 3}, own={"t"})
assert m == {"t": 2} and c == []
m, c = sync.merge_dict({"t": 1}, {"t": 2}, {"t": 3}, their={"t"})
assert m == {"t": 3} and c == []
m, c = sync.merge_dict(None, {"a": 1}, None)                     # nothing known on their side: mine as it is
assert m == {"a": 1} and c == []
m, c = sync.merge_dict({"a": 1}, None, {"a": 1, "b": 2})         # mine missing: as if unchanged
assert m == {"a": 1, "b": 2}

print("All share checks passed.")
