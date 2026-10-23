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

print("All checks passed.")
