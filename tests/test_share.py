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

from nuauto import config

tmp = tempfile.mkdtemp()
LAPTOP, HOMELAB = os.path.join(tmp, "laptop"), os.path.join(tmp, "homelab")
for p in (LAPTOP, HOMELAB):
    os.makedirs(p, mode=0o700)
config.LOCAL_DIR = LAPTOP  # never the real local/
config.LOGS_DIR = os.path.join(tmp, "logs")

from nuauto import accounts  # noqa: E402
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
gone = [base[0], base[2]]                                             # only one side deleted
assert sync.merge_bank(base, gone, base) == (gone, [])
assert sync.merge_bank(base, base, gone) == (gone, [])
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
# one deleted, the other changed: the changed one stays (whichever side it is)
m, c = sync.merge_bank(base, gone, changed)
assert [x["answer"] for x in m if x["question"] == "email"] == ["b@x.com"] and c == []
m, c = sync.merge_bank(base, changed, gone)
assert [x["answer"] for x in m if x["question"] == "email"] == ["b@x.com"] and c == []
# both deleted: gone
assert sync.merge_bank(base, gone, gone) == (gone, [])
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
    """Run as the machine whose local/ is folder."""
    old = config.LOCAL_DIR, accounts.PATH, accounts.LOCK
    config.LOCAL_DIR, accounts.PATH, accounts.LOCK = folder, os.path.join(folder, "accounts.json"), os.path.join(folder, "accounts.lock")
    try:
        yield
    finally:
        config.LOCAL_DIR, accounts.PATH, accounts.LOCK = old


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
o = out_of(HOMELAB)
assert o["answers.json"] == [e("city", "Boston")] and o["answers_other.json"] is None and list(o["accounts.json"]) == ["acme.com"]
with at(HOMELAB):
    conflicts = sync.merge_into_banks({"answers.json": {"base": [e("city", "Boston")], "merged": [e("city", "Boston"), e("email", "a")]}})
assert conflicts == [] and names(read_bank(HOMELAB, "answers.json")) == ["city", "email"]
assert stat.S_IMODE(os.stat(os.path.join(HOMELAB, "answers.json")).st_mode) == 0o600
with at(HOMELAB):
    merged, clashes = sync.merge_into_accounts({"acme.com": login("pw-other", "2026-10-09"), "new.com": login("pw9", "2026-10-09")})
assert set(merged) == {"acme.com", "new.com"} and clashes == ["acme.com"]
assert read_accounts(HOMELAB)["acme.com"]["other_passwords"] == ["pw-other"]
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
old_flags = config.REMOTE_ASSIST, config.IS_SERVER
try:
    # off, or on the homelab itself: nothing happens, no ssh
    config.REMOTE_ASSIST, config.IS_SERVER = False, False
    assert run_share() == (True, "") and calls == []
    config.REMOTE_ASSIST, config.IS_SERVER = True, True
    assert run_share() == (True, "") and calls == []
    config.REMOTE_ASSIST, config.IS_SERVER = True, False

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

    # pull() shares only with remote_assist
    ran, shared = [], []
    real_run, real_share = sync._run, sync.share
    sync._run = lambda cmd, what: ran.append(what) or True
    sync.share = lambda: shared.append(1) or True
    try:
        config.REMOTE_ASSIST = False
        assert sync.pull() is True and shared == []
        config.REMOTE_ASSIST = True
        assert sync.pull() is True and shared == [1] and ran == ["pull", "pull"]
    finally:
        sync._run, sync.share = real_run, real_share
finally:
    sync._remote_py = real_remote
    config.REMOTE_ASSIST, config.IS_SERVER = old_flags

print("All share checks passed.")
