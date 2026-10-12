"""Laptop <-> homelab sync over Tailscale (ssh + rsync). Used by the `nuauto` command on the laptop.

The homelab owns the job data (it runs the update); the laptop owns your ratings.
  pull()          homelab data/ -> laptop (everything except ratings.json)
  push()          laptop -> homelab: ratings.json, code, local_config.json, resume, Google token (only if newer).
                  Not the code when "deploy_from_git" is on: the homelab then deploys main from GitHub itself (deploy.py)
  deploy_now()    start that deploy on the homelab now
  push_session()  laptop -> homelab: NUworks browser profile + session cookies (after `nuauto login`)
  share()         with "remote_assist" on (the assistant runs on the homelab too): the answer banks and the job-site
                  logins, merged both ways (merge_bank, merge_accounts); push() also sends the Gmail login and a copy of
                  your writeups (~/nuauto-notes there)
Secrets are copied file-to-file with rsync (or as JSON over ssh, under each file's lock) and never printed.
"""
import fcntl
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile

from nuauto import config

SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]
RSYNC = ["rsync", "-a", "-e", " ".join(SSH)]
REMOTE = f"{config.SERVER}:{config.SERVER_DIR}"
# Pushed with rsync -R, so paths keep their folders (src/nuauto/, prompts/, docs/...). The docs go too, so agents
# there read current ones. The homelab installs the package once (uv pip install -e .); code changes need no reinstall.
PACKAGE = ["__init__.py", "__main__.py", "cli.py", "config.py", "sheet.py", "jobs.py", "fields.py", "daily.py", "browser.py",
           "apply.py", "answers.py", "inspect_form.py", "setup_sheet.py", "sync.py", "web.py", "doctor.py", "assist.py",
           "health.py", "demo.py", "gui.py", "onboard.py", "window.py", "window_gtk.py", "selftest.py", "deploy.py", "insights.py",
           "manage.py", "intern.py", "postings.py", "accounts.py", "mail.py", "screen.py", "review.py", "remote.py"]
CODE = (["pyproject.toml", "README.md", "local_config.example.json", "CLAUDE.md", "CONTRIBUTING.md",
         "docs/DEPLOY.md", "docs/PIPELINE.md", "docs/ARCHITECTURE.md", "docs/SAFETY.md", "docs/GUI.md",
         "docs/REMOTE.md"]
        + [f"src/nuauto/{f}" for f in PACKAGE]
        + [f"prompts/{f}" for f in ("TRIAGE_PROMPT.md", "SCORE_PROMPT.md", "CATEGORY_PROMPT.md", "ASSIST_PROMPT.md",
                                              "INTERN_TRIAGE_PROMPT.md", "INTERN_SCORE_PROMPT.md")])


def _run(cmd, what):
    try:
        r = subprocess.run(cmd, cwd=config.PROJECT_DIR, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        print(f"(sync: {what} timed out; using local data)")
        return False
    if r.returncode != 0:
        print(f"(sync: {what} failed, homelab unreachable? using local data)")
        return False
    return True


def ssh(command, timeout=None):
    """Run a command on the homelab, output straight to this terminal. Returns the exit code."""
    return subprocess.run(SSH + [config.SERVER, command], timeout=timeout).returncode


def pull():
    ok = _run(RSYNC + ["--exclude", "ratings.json", f"{REMOTE}/data/", "data/"], "pull")
    if config.REMOTE_ASSIST:
        share()
    return ok


# Only rsync options that macOS's old built-in rsync (2.6.9) also has: no --mkpath, no --chmod. Folders are
# made with mkdir over ssh, and local/ is locked down (mode 700/600) with chmod afterwards.
def remote(command, what):
    return _run(SSH + [config.SERVER, command], what)


def remote_dirs(*dirs):
    return remote("mkdir -p " + " ".join(shlex.quote(f"{config.SERVER_DIR}/{d}") for d in dirs), "make homelab folders")


def lock_remote_local():
    return remote(f"chmod -R go-rwx {shlex.quote(config.SERVER_DIR + '/local')}", "lock down homelab local/")


def stage_private(src, name):
    """A copy of src in local/ only you can read (mode 600), so rsync -a carries that mode to the homelab (macOS's
    rsync can't set modes). The file itself when it is that copy already."""
    dest = os.path.join(config.LOCAL_DIR, name)
    if os.path.realpath(src) != os.path.realpath(dest):
        shutil.copyfile(src, dest + ".tmp")
        os.chmod(dest + ".tmp", 0o600)
        os.replace(dest + ".tmp", dest)
    else:
        os.chmod(dest, 0o600)
    return dest


def push(code=None):
    """code: send the laptop's code too (default: yes, unless the homelab deploys main from GitHub)."""
    if code is None:
        code = not config.DEPLOY_FROM_GIT
    ok = remote_dirs("local", "data", "docs")
    if os.path.exists(config.LOCAL_CONFIG_PATH):  # before the code, which reads it
        ok &= _run(RSYNC + ["-c", "local/local_config.json", f"{REMOTE}/local/local_config.json"], "push local config")
    if code:
        ok &= _run(RSYNC + ["-c", "-R"] + CODE + [f"{REMOTE}/"], "push code")
    if os.path.exists("docs/STATUS.md"):  # local log (gitignored); agents on the homelab read it too
        ok &= _run(RSYNC + ["-c", "docs/STATUS.md", f"{REMOTE}/docs/STATUS.md"], "push status")
    ok &= _run(RSYNC + ["data/ratings.json", f"{REMOTE}/data/ratings.json"], "push ratings")
    if os.path.exists(config.LAPTOP_RESUME):
        ok &= _run(RSYNC + ["-c", config.LAPTOP_RESUME, f"{REMOTE}/local/resume.pdf"], "push resume")
    if config.REMOTE_ASSIST and config.LAPTOP_TRANSCRIPT and os.path.exists(config.LAPTOP_TRANSCRIPT):
        ok &= _run(RSYNC + ["-c", stage_private(config.LAPTOP_TRANSCRIPT, "transcript.pdf"),
                            f"{REMOTE}/local/transcript.pdf"], "push transcript")  # only the assistant there uses it
    if os.path.exists(config.TOKEN_PATH):
        ok &= _run(RSYNC + ["-u", "local/token.json", f"{REMOTE}/local/token.json"], "push Google token")
    if os.path.exists(config.GOOGLE_LOGIN_PATH):
        ok &= _run(RSYNC + ["-u", "local/google_login.txt", f"{REMOTE}/local/google_login.txt"], "push Google login date")
    if config.REMOTE_ASSIST:
        from nuauto import mail
        if os.path.exists(mail.TOKEN):  # the assistant there reads its email codes too
            ok &= _run(RSYNC + ["-u", mail.TOKEN, f"{REMOTE}/local/gmail_token.json"], "push Gmail login")
        ok &= push_notes()
        ok &= share()
    ok &= lock_remote_local()
    return ok


def deploy_now():
    """Run the homelab's deploy now (the timer does it every 5 minutes) and show the end of its log."""
    return ssh("systemctl --user start nuauto-deploy.service; "
               f"tail -n 6 {shlex.quote(config.SERVER_DIR + '/logs/deploy.log')}")


def server_busy():
    """True if the homelab is running its update (the browser profile is in use there)."""
    return subprocess.run(SSH + [config.SERVER, "pgrep -f 'nuauto daily'"], capture_output=True).returncode == 0


def push_session():
    if server_busy():
        print("Homelab is running its update right now; run `nuauto login` again in a few minutes to copy the session.")
        return False
    ok = remote_dirs("local/browser_profile")
    ok &= _run(RSYNC + ["--delete", "local/browser_profile/", f"{REMOTE}/local/browser_profile/"], "push browser profile")
    ok &= _run(RSYNC + ["local/session_cookies.json", f"{REMOTE}/local/session_cookies.json"], "push session cookies")
    ok &= lock_remote_local()
    if ok:
        print("NUworks session copied to the homelab.")
    return ok


# ---------- the assistant on the homelab too ("remote_assist"): what both machines' assistants share ----------

BANKS = ("answers.json", "answers_other.json")   # in local/; lists of entries, keyed by their question
ACCOUNTS = "accounts.json"                       # in local/; {site: login}
BASE = "share_base.json"                         # in local/ (laptop): the banks as both machines last agreed
NOTES_MAX = 50_000_000                           # a writeups folder bigger than this is not copied (not notes)
UNREADABLE = "unreadable"                        # what shared_out sends for a file it can't read (left alone)


def _key(entry):
    return str(entry.get("question", "")).strip().lower()


def merge_bank(base, mine, theirs, gone=()):
    """Three-way merge of two copies of one answer bank that both started from base (the last merge): a question both
    have takes the side that changed it (both changed it: mine wins, and it is listed; aliases only: all kept). A
    question only one side has is kept: the merge never deletes by itself (a missing file, or a copy saved from an old
    read, must not wipe answers). Deleted on purpose = in gone (answers.forget: the GUI's Answers screen): dropped on
    both. mine / theirs None = not known (missing): as if unchanged since base. Returns (merged, [conflicting
    questions]). Pure (tests/test_share.py)."""
    gone = set(gone)
    b = {_key(e): e for e in base or []}
    m = {_key(e): e for e in (mine if mine is not None else base) or []}
    t = {_key(e): e for e in (theirs if theirs is not None else base) or []}
    out, conflicts = [], []
    for k in list(m) + [k for k in t if k not in m]:
        mk, tk, bk = m.get(k), t.get(k), b.get(k)
        if k in gone:
            continue
        if mk is None or tk is None or mk == tk or tk == bk:
            pick = mk or tk
        elif mk == bk:
            pick = tk
        elif {**mk, "aliases": []} == {**tk, "aliases": []}:
            pick = {**mk, "aliases": list(mk.get("aliases") or []) +
                    [a for a in tk.get("aliases") or [] if a not in (mk.get("aliases") or [])]}
        else:
            pick = mk
            conflicts.append(k)
        out.append(pick)
    return out, conflicts


def merge_accounts(mine, theirs):
    """The job-site logins of both machines together. A login is never dropped. The same site on both with the same
    password: one login (jobs of both, exported if either was). Two different passwords for one site (each machine
    made an account there before they shared): the earlier one stays the login, the other is kept with it under
    "other_passwords" (one of them is the site's real one: `nuauto accounts` says which sites). Returns (merged,
    [sites with two passwords]). Pure."""
    out, clashes = {}, []
    for site in list(mine or {}) + [s for s in theirs or {} if s not in (mine or {})]:
        a, b = (mine or {}).get(site), (theirs or {}).get(site)
        if a is None or b is None or a == b:
            out[site] = a or b
            continue
        # your own login (imported) always stays the site's login (two of yours: the newer import); else the earlier one
        own = [x for x in (a, b) if x.get("own")]
        if len(own) == 1:
            first, second = own[0], (b if own[0] is a else a)
        else:
            first, second = sorted((a, b), key=lambda x: x.get("created") or "", reverse=bool(own))
        login = dict(first)
        login["jobs"] = list(first.get("jobs") or []) + [j for j in second.get("jobs") or []
                                                        if j.get("url") not in {x.get("url") for x in first.get("jobs") or []}]
        login["exported"] = first.get("exported") or second.get("exported") or False
        login["company"] = first.get("company") or second.get("company") or ""
        others = [p for p in [*(first.get("other_passwords") or []), *(second.get("other_passwords") or []),
                              second.get("password")] if p and p != first.get("password")]
        if others:
            login["other_passwords"] = list(dict.fromkeys(others))
            if login != a:
                clashes.append(site)
        out[site] = login
    return out, clashes


def merge_gone(mine, theirs):
    """Both machines' deletion records together ({bank: {question: {"when", "gone"}}}): per question the later event
    (deleted, or saved again) counts."""
    out = {b: dict(q) for b, q in (mine or {}).items() if isinstance(q, dict)}
    for bank, qs in (theirs or {}).items():
        for q, ev in (qs or {}).items() if isinstance(qs, dict) else ():
            if isinstance(ev, dict) and str(ev.get("when", "")) > str((out.setdefault(bank, {}).get(q) or {}).get("when", "")):
                out[bank][q] = ev
    return out


def _read_json(path, default):
    """The file's JSON; default when it doesn't exist; UNREADABLE when it can't be read (bad JSON: perhaps being edited
    by hand): such a file is left alone, never merged over."""
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except (OSError, ValueError):
        return UNREADABLE


def _write_secret(path, obj):
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def _bank_lock():
    """The answer banks' lock (the assistants' bank commands and the GUI's Answers screen hold it too). Close the
    returned file to let go."""
    os.makedirs(config.LOCAL_DIR, mode=0o700, exist_ok=True)
    f = open(os.path.join(config.LOCAL_DIR, "answers.lock"), "a")
    fcntl.flock(f, fcntl.LOCK_EX)
    return f


def shared_out():
    """(homelab) The answer banks, their deletion records and the job-site logins, each read under its lock, as one
    JSON object on stdout: read by the laptop's share() over ssh, never shown."""
    from nuauto import accounts
    from nuauto import answers
    lock = _bank_lock()
    try:
        out = {n: _read_json(os.path.join(config.LOCAL_DIR, n), None) for n in BANKS}
        out["gone"] = answers.gone_all()
    finally:
        lock.close()
    try:
        with accounts.locked() as data:
            out[ACCOUNTS] = dict(data)
    except ValueError:  # accounts.json can't be read: left alone
        out[ACCOUNTS] = UNREADABLE
    json.dump(out, sys.stdout)


def merge_into_banks(parts, gone):
    """Merge {bank: {"base", "merged"}} into this machine's banks under their lock, with the deletion records (merged
    into this machine's too). A bank file that can't be read here is skipped. Returns (conflicts, skipped banks)."""
    from nuauto import answers
    conflicts, skipped = [], []
    lock = _bank_lock()
    try:
        answers.set_gone(merge_gone(answers.gone_all(), gone))
        gone = answers.gone_all()
        for n, part in parts.items():
            path = os.path.join(config.LOCAL_DIR, n)
            mine = _read_json(path, None)
            if mine == UNREADABLE or part.get("merged") == UNREADABLE:
                skipped.append(n)
                continue
            merged, c = merge_bank(part.get("base"), mine, part.get("merged"), answers.gone_questions(gone, n))
            conflicts += c
            if merged != (mine or []) and (merged or mine is not None):
                answers.save(merged, path)
    finally:
        lock.close()
    return conflicts, skipped


def merge_into_accounts(theirs):
    """Merge another machine's logins into this one's, under their lock. Returns (merged, sites with two passwords);
    (None, []) when either side can't be read (then nothing is changed)."""
    from nuauto import accounts
    if theirs == UNREADABLE:
        return None, []
    try:
        with accounts.locked() as data:
            merged, clashes = merge_accounts(dict(data), theirs or {})
            data.clear()
            data.update(merged)
    except ValueError:
        return None, []
    return merged, clashes


def shared_in(payload):
    """(homelab) Merge what the laptop's share() sent: per bank {"base": what it read here, "merged": its result}, the
    deletion records and its logins. A change made here in the meantime is kept (it shows up as this side's change)."""
    merge_into_banks({n: payload[n] for n in BANKS if n in payload}, payload.get("gone") or {})
    if payload.get(ACCOUNTS) not in (None, UNREADABLE):
        merge_into_accounts(payload[ACCOUNTS])


def _remote_py(command, stdin=None):
    """`python -m nuauto.sync <command>` on the homelab (its own code and venv): its output, or None if it failed.
    Neither its output nor its errors are printed (they carry the logins)."""
    cmd = SSH + [config.SERVER, f"cd {shlex.quote(config.SERVER_DIR)} && .venv/bin/python -m nuauto.sync {command}"]
    try:
        r = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return None
    return r.stdout if r.returncode == 0 else None


def share():
    """Laptop, with "remote_assist": the answer banks (and their deletion records) and the job-site logins of both
    machines, merged both ways (an answer saved by the assistant on either one is there for the next run on both).
    True if it went through."""
    if not config.REMOTE_ASSIST or config.IS_SERVER:
        return True
    out = _remote_py("shared-out")
    try:
        theirs = json.loads(out) if out is not None else None
    except ValueError:
        theirs = None
    if not isinstance(theirs, dict):
        print("(sync: could not read the homelab's answers and logins; using this computer's)")
        return False
    base_path = os.path.join(config.LOCAL_DIR, BASE)
    base = _read_json(base_path, {})
    base = base if isinstance(base, dict) else {}
    parts = {n: {"base": base.get(n), "merged": theirs.get(n)} for n in BANKS}
    conflicts, skipped = merge_into_banks(parts, theirs.get("gone") or {})
    from nuauto import answers
    merged = {n: _read_json(os.path.join(config.LOCAL_DIR, n), None) for n in BANKS if n not in skipped}
    logins, clashes = merge_into_accounts(theirs.get(ACCOUNTS))
    # The homelab merges our result with what it had when we read it as the base: whatever its assistants saved in
    # the meantime stays.
    payload = {**{n: {"base": theirs.get(n), "merged": merged[n]} for n in merged if merged[n] != UNREADABLE},
               "gone": answers.gone_all(), **({ACCOUNTS: logins} if logins is not None else {})}
    ok = _remote_py("shared-in", stdin=json.dumps(payload)) is not None
    if ok:
        _write_secret(base_path, {n: v for n, v in merged.items() if v not in (None, UNREADABLE)})
    else:
        print("(sync: could not send the answers and logins to the homelab; next time)")
    for n in skipped:
        print(f"(sync: {n} can't be read on one of the machines (bad JSON?): left alone, not merged)")
    if conflicts:
        print(f"(sync: changed on both machines, this computer's answer kept: {', '.join(conflicts[:5])})")
    if clashes:
        print(f"(sync: two passwords for {', '.join(clashes[:5])} (an account made on each machine): both kept; "
              "`nuauto accounts` lists them)")
    return ok


def notes_files(root):
    """The files of one writeups folder that may go to the homelab: only what the agent may read (assist.secret_path:
    no keys, tokens, .env, .git, NUauto's local/ folders), no links. Paths relative to root."""
    from nuauto import assist
    protected = assist.protected_dirs([root])
    out = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not assist.secret_path(os.path.join(folder, d, "x"), protected)
                         and not os.path.islink(os.path.join(folder, d)))
        for f in sorted(files):
            p = os.path.join(folder, f)
            if not os.path.islink(p) and os.path.isfile(p) and not assist.secret_path(p, protected):
                out.append(os.path.relpath(p, root))
    return out


def push_notes():
    """Laptop -> homelab: a copy of each writeups folder (assist_writeups) in ~/nuauto-notes/<its name> there, for the
    assistant on the homelab (it reads only these and the resume)."""
    ok = remote("mkdir -p nuauto-notes && chmod 700 nuauto-notes", "make the homelab's notes folder")
    for root in config.ASSIST_WRITEUPS:
        if not os.path.isdir(root):
            continue
        files = notes_files(root)
        if sum(os.path.getsize(os.path.join(root, f)) for f in files) > NOTES_MAX:
            print(f"(sync: {root} is over {NOTES_MAX // 1_000_000} MB, so not notes: not copied to the homelab)")
            ok = False
            continue
        stage = tempfile.mkdtemp(prefix="nuauto-notes-")
        try:
            for f in files:
                os.makedirs(os.path.dirname(os.path.join(stage, f)), exist_ok=True)
                shutil.copy2(os.path.join(root, f), os.path.join(stage, f))
            name = os.path.basename(root.rstrip(os.sep))
            ok &= _run(RSYNC + ["-c", "--delete", stage + "/", f"{config.SERVER}:nuauto-notes/{name}/"],
                       f"push notes ({name})")
        finally:
            shutil.rmtree(stage, ignore_errors=True)
    return ok


def main(argv):
    if argv in (["shared-out"], ["shared-in"]):
        if not config.IS_SERVER:
            sys.exit("shared-out / shared-in run on the homelab (the laptop's share() calls them over ssh).")
        return shared_out() if argv == ["shared-out"] else shared_in(json.load(sys.stdin))
    if argv == ["share"]:
        sys.exit(0 if share() else 1)
    sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
