"""Offline checks for apply.check_popup and the GUI's queue (apply.run_rows --queue). Run: python test_apply.py"""
from nuauto.apply import NeedsHuman, check_popup

LABEL = "Doe, Jane | Spring 2027"
RESUME = {"tag": "select", "type": "select-one", "label": "Resume *",
          "options": ["Select a resume", "", LABEL]}


def expect_stop(text, links, fields, needle):
    try:
        check_popup(text, links, fields, LABEL)
    except NeedsHuman as e:
        assert needle in str(e), str(e)
        return
    raise AssertionError("expected NeedsHuman")


# plain popup passes
check_popup("Apply to X | Submit Your Application", [], [RESUME], LABEL)

# external link / "How to Apply" stops
expect_stop("How to Apply", ["https://x.icims.com/j"], [RESUME], "x.icims.com")
expect_stop("How to Apply", [], [RESUME], "External")

# extra fields are no longer rejected here; the answer bank (test_answers.py) handles them
extra = {"tag": "textarea", "type": "textarea", "label": "Why us?", "options": []}
check_popup("Apply to X", [], [RESUME, extra], LABEL)

# required items that are not fillable fields stop (e.g. Cover Letter + Transcript pickers)
POPUP = "Apply to X\nSubmit Your Application\nResume *\nor add a new resume\nCover Letter *\nAdd a new cover letter\nTranscript\n*\nAdd a new transcript\nCancel\nSubmit"
expect_stop(POPUP, [], [RESUME], "Popup requires Cover Letter, Transcript")
check_popup("Apply to X\nResume *\nCancel\nSubmit", [], [RESUME], LABEL)  # only the resume: fine
q = {"tag": "select", "type": "select-one", "label": "Work authorization *", "options": ["Yes", "No"]}
check_popup("Apply to X\nResume *\nWork authorization *\nSubmit", [], [RESUME, q], LABEL)  # a real field: answer bank handles it

# Outlook safelinks are unwrapped to the real site
SAFE = "https://eur01.safelinks.protection.outlook.com/?url=https%3A%2F%2Fcareer55.sapsf.eu%2Fsfcareer%2Fjob%3Fid%3D1&data=x&reserved=0"
expect_stop("How to Apply", [SAFE], [RESUME], "External application: career55.sapsf.eu -> https://career55.sapsf.eu/sfcareer/job?id=1")

# company site already done (nuauto assist): an off-site link is no reason to stop, every other check stays
check_popup("How to Apply", ["https://x.icims.com/j"], [RESUME], LABEL, company_site_done=True)
try:
    check_popup(POPUP, ["https://x.icims.com/j"], [RESUME], LABEL, company_site_done=True)
    raise AssertionError("expected NeedsHuman")
except NeedsHuman as e:
    assert "Cover Letter" in str(e)

# missing resume option stops
expect_stop("Apply to X", [], [{**RESUME, "options": ["Select a resume", "Other resume"]}], "not in the dropdown")

# no dropdown stops
expect_stop("Apply to X", [], [], "exactly one Resume")

# the GUI's queue (apply.run_rows --queue): rows queued while a run goes are done next, in the order queued; a row's
# own Apply then ends when the queue is empty; rows no longer Approved are skipped; -n is extended by queued rows only
import json, os, tempfile, types
from nuauto import apply, sheet

def run(queue, row=None, n=None, add_during=None):
    rows = [sheet.Row(k, f"u{k}", f"C{k}", "T", "Approved", "", "") for k in range(2, 8)]
    rows[3].status = "Applied"  # row 5: sent already
    qfile = os.path.join(tempfile.mkdtemp(), "queue.json")
    open(qfile, "w").write(json.dumps(queue))
    done = []

    def one(r, *a):
        done.append(r.number)
        r.status = "Applied"
        if add_during and r.number == add_during[0]:  # you click another row's Apply while this one runs
            now = json.loads(open(qfile).read())
            open(qfile, "w").write(json.dumps(now + [add_during[1]]))
        return "next"
    patch = {"read_rows": lambda ws: rows, "check_limits": lambda rs: (0, 0), "week_window": lambda: (None, "this week")}
    saved = {k: getattr(sheet, k) for k in patch} | {"apply_one": apply.apply_one, "sleep": apply.time.sleep}
    for k, v in patch.items():
        setattr(sheet, k, v)
    apply.apply_one, apply.time.sleep = one, lambda s: None
    try:
        io = types.SimpleNamespace(event=lambda *a, **k: None)
        apply.run_rows(types.SimpleNamespace(row=row, n=n, order="closes", queue=qfile), None, "", [], io)
    finally:
        apply.apply_one, apply.time.sleep = saved.pop("apply_one"), saved.pop("sleep")
        for k, v in saved.items():
            setattr(sheet, k, v)
    return done

assert run([], row=3) == [3]                                  # one row's Apply, nothing queued: only it
assert run([6, 2], row=3) == [3, 6, 2]                        # then the queue, in the order queued
assert run([5, 6], row=3) == [3, 6]                           # row 5 is not Approved: skipped
assert run([], row=3, add_during=(3, 7)) == [3, 7]            # queued while the run goes
assert run([7], n=1) == [2, 7]                                # Start (-n 1): its row, then the queued one
assert run([], n=2) == [2, 3]
assert apply.BETWEEN == (10, 20)

# past a limit only rows being re-applied go (sheet.reapplying: the same applications); none of those: refused as before
def run_limited(reapply_rows, row=None):
    rows = [sheet.Row(k, f"u{k}", f"C{k}", "T", "Approved",
                      f"{sheet.REAPPLY_MARK} (first sent 2026-10-03)." if k in reapply_rows else "", "2026-10-03" if k in reapply_rows else "")
            for k in range(2, 6)]
    done = []

    def one(r, *a):
        done.append(r.number)
        r.status = "Applied"
        return "next"

    def full(rs):
        raise sheet.LimitReached("Weekly limit reached.")
    patch = {"read_rows": lambda ws: rows, "check_limits": full, "check_limits_safe": lambda rs: (11, 40),
             "week_window": lambda: (None, "this week")}
    saved = {k: getattr(sheet, k) for k in patch} | {"apply_one": apply.apply_one, "sleep": apply.time.sleep}
    for k, v in patch.items():
        setattr(sheet, k, v)
    apply.apply_one, apply.time.sleep = one, lambda s: None
    try:
        io = types.SimpleNamespace(event=lambda *a, **k: None)
        apply.run_rows(types.SimpleNamespace(row=row, n=None, order="closes", queue=None), None, "", [], io)
    except SystemExit as e:
        return str(e)
    finally:
        apply.apply_one, apply.time.sleep = saved.pop("apply_one"), saved.pop("sleep")
        for k, v in saved.items():
            setattr(sheet, k, v)
    return done

assert run_limited({3, 5}) == [3, 5]
assert run_limited(set()).startswith("Refusing to run: Weekly limit")
assert run_limited({3}, row=2).startswith("Refusing to run: Weekly limit")  # that row is a new application
assert run_limited({3}, row=3) == [3]

# the NUworks side when another process has the NUworks browser: no crash, nothing opened, "NOT submitted" in Notes
# (so the row is on the GUI's retry list) and a retry still allowed
import subprocess, sys
from nuauto import assist, browser, config, gui
tmp = tempfile.mkdtemp()
config.LOCAL_DIR, config.LOGS_DIR, config.PROFILE_LOCK_PATH = tmp, tmp, os.path.join(tmp, "browser_profile.lock")
holder = subprocess.Popen([sys.executable, "-c", "import sys\nfrom nuauto import browser, config\n"
                           "config.LOCAL_DIR, config.PROFILE_LOCK_PATH = sys.argv[1], sys.argv[2]\n"
                           "browser.lock_profile('probe.py')\nprint('locked', flush=True)\nsys.stdin.read()",
                           tmp, config.PROFILE_LOCK_PATH], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
assert holder.stdout.readline().strip() == "locked"
notes = []
saved = sheet.append_note, apply.sync_playwright
sheet.append_note = lambda ws, n, url, text: notes.append((n, text))
apply.sync_playwright = lambda: (_ for _ in ()).throw(AssertionError("opened a browser"))
try:
    note = apply.submit_nuworks_side(sheet.Row(9, "u9", "C9", "T", "Applied", "", ""), None)
finally:
    sheet.append_note, apply.sync_playwright = saved
    holder.communicate("")
assert notes == [(9, note)] and "probe.py" in note and "nuauto assist nuworks 9" in note, notes
assert note.startswith(gui.NUWORKS_SIDE_NOT_SENT[0]) and assist.nuworks_side_blocked(note) is None, note

print("All checks passed.")

# re-apply: the first application is withdrawn on My Job Applications (only this job's item, through NUworks' own
# confirm), then NUworks must no longer have it; a different question, or the job not listed once: nothing withdrawn
from playwright.sync_api import sync_playwright  # noqa: E402

LIST = """<html><body><p>1-2 of 2 results</p><ul class="list_job_applications">
<li><div class="list-item-title"><a href="https://northeastern-csm.symplicity.com/students/app/jobs/detail/{other}">B</a></div>
  <a href="javascript:;" onclick="confirmWithdraw('b2')">Withdraw</a></li>
<li><div class="list-item-title"><a href="https://northeastern-csm.symplicity.com/students/app/jobs/detail/abc">A</a></div>
  <a href="javascript:;" style="display:none" onclick="confirmWithdraw('a1')">Withdraw</a>
  <a href="javascript:;" onclick="confirmWithdraw('a1')">Withdraw</a></li></ul>
<script>function confirmWithdraw(id) { if (confirm({question})) self.location.href = '/students/index.php?rm=' + id + '&'; }</script>
</body></html>"""


def withdraw_case(question=apply.WITHDRAW_CONFIRM, other="zzz"):
    hits, state = [], {"applied": True}
    row = sheet.Row(9, "https://northeastern-csm.symplicity.com/students/app/jobs/detail/abc", "Co", "T", "Approved",
                    f"{sheet.REAPPLY_MARK} (first sent 2026-10-03).", "2026-10-03")

    def serve(route):
        u = route.request.url
        if "rm=" in u:
            hits.append(u.split("rm=")[1].rstrip("&"))
            state["applied"] = False
            return route.fulfill(content_type="text/html", body="<p>withdrawn</p>")
        return route.fulfill(content_type="text/html", body=LIST.replace("{other}", other).replace("{question}", json.dumps(question)))
    log = types.SimpleNamespace(write=lambda *a: None, screenshot=lambda *a: None)
    asked = apply.still_applied  # (page.request skips page.route: NUworks' job record is faked here, never fetched)
    apply.still_applied = lambda page, r: state["applied"]
    with sync_playwright() as p:
        b = p.firefox.launch(headless=True)
        page = b.new_page()
        page.route("**/*", serve)
        try:
            apply.withdraw_first(page, row, log)
            out = "withdrawn"
        except apply.NotWithdrawn as e:
            out = str(e)
        finally:
            b.close()
            apply.still_applied = asked
    return out, hits

config.DEMO_PACE = 0.01  # (browser.pause)
r = withdraw_case(); assert r == ("withdrawn", ["a1"]), r                          # this job's item only
out, hits = withdraw_case(question="Delete your account?")
assert hits == [] and "not withdrawn" in out, (out, hits)                          # not NUworks' withdraw question
out, hits = withdraw_case(other="abc")
assert hits == [] and "lists this job 2 times" in out, (out, hits)                  # the job twice: never a guess
print("Re-apply withdraw checks passed.")
