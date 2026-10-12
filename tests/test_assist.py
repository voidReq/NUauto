"""Offline checks for assist.py (the agent's guard and answer bank). Run: python test_assist.py

No browser, no Claude, no sheet. answers.json and the run folder are temp files.
"""
import io
import json
import os
import sys
import tempfile

from nuauto import config

tmp = tempfile.mkdtemp()
config.ANSWERS_PATH = os.path.join(tmp, "answers.json")
config.OTHER_ANSWERS_PATH = os.path.join(tmp, "answers_other.json")  # never the real banks
config.LOCAL_DIR = os.path.join(tmp, "local")  # the session locks (slots, sites, rows) and the bank's lock: never the real local/
config.REMOTE_ASSIST, config.IS_SERVER = False, False  # your own settings (a homelab, remote_assist) never leak in

from nuauto import answers  # noqa: E402
from nuauto import assist  # noqa: E402

H = "acme.wd5.myworkdayjobs.com"
SNAP = f"""### Page state
- Page URL: https://{H}/careers/job/X_1/apply
- Page Title: Apply
- Page Snapshot:
```yaml
- textbox "First Name*" [ref=e12]
- textbox "City*" [ref=e13]
- button "State Select One" [ref=e20]
- option "Massachusetts" [ref=e31]
- option "Maine" [ref=e32]
- radio "Yes" [ref=e40]
- checkbox "I agree to the terms" [ref=e50]
- button "Submit" [ref=e60] [cursor=pointer]
- button "Next" [ref=e61]
- textbox "Password" [ref=e70]
- link "Sign in with Google" [ref=e80]
- button "Submit Application" [ref=e90]
```"""
B = "mcp__browser__"
RESUME = os.path.join(tmp, "Resume.pdf")
open(RESUME, "w").write("x")
ANSWER_CMD = ["/venv/python", "/proj/assist.py"]


def fresh():
    s = {"current_url": f"https://{H}/careers/job/X_1", "refs": {}, "issued": {}, "issued_values": [],
         "answer_cmd": ANSWER_CMD, "resume": os.path.realpath(RESUME)}
    s["refs"].update(assist.parse_refs(SNAP))
    s["current_url"] = assist.page_url(SNAP)
    return s


def blocked(state, tool, inp, needle=""):
    verdict, why = assist.decide(state, tool, inp)
    assert verdict == "deny" and needle.lower() in why.lower(), (tool, inp, verdict, why)


def asks(state, tool, inp, needle=""):
    verdict, why = assist.decide(state, tool, inp)
    assert verdict == "ask" and why.startswith("NUAUTO") and needle.lower() in why.lower(), (tool, inp, verdict, why)


def ok(state, tool, inp):
    verdict, why = assist.decide(state, tool, inp)
    assert verdict == "allow", (tool, inp, verdict, why)


s = fresh()
assert s["refs"]["e12"] == {"role": "textbox", "name": "First Name*"} and s["refs"]["e60"]["name"] == "Submit"
assert s["current_url"] == f"https://{H}/careers/job/X_1/apply"

# tools: browser tools only; never page scripts (they could submit behind the review)
for t in ("browser_evaluate", "browser_run_code_unsafe", "browser_run_code", "browser_drag", "browser_install"):
    blocked(s, B + t, {}, "not allowed")
blocked(s, "mcp__other__x", {}, "not allowed")
blocked(s, "Write", {}, "not allowed")
ok(s, B + "browser_snapshot", {})

# any web site is fine; non-web links are not
ok(s, B + "browser_navigate", {"url": f"https://{H}/careers/job/X_1"})
ok(s, B + "browser_navigate", {"url": "https://campus.icims.example/jobs/1/login"})
ok(s, B + "browser_tabs", {"action": "new", "url": "https://other.example/"})
blocked(s, B + "browser_navigate", {"url": "file:///home/x/token.json"}, "web pages")
blocked(s, B + "browser_navigate", {"url": "javascript:alert(1)"}, "web pages")
other = {**fresh(), "current_url": "https://accounts.example/signin"}
ok(other, B + "browser_click", {"ref": "e61"})  # clicking on another site is fine now

# submitting: always asks the user first; the ref decides, not the agent's description
asks(s, B + "browser_click", {"ref": "e60", "element": "Next button"}, "submit")
asks(s, B + "browser_click", {"target": "e90"}, "submit")
apply_snap = SNAP.replace('button "Next" [ref=e61]', 'button "Apply" [ref=e61]')
a = {**fresh(), "refs": assist.parse_refs(apply_snap)}
asks(a, B + "browser_click", {"ref": "e61"}, "apply")             # some sites' final button is "Apply"
asks(s, B + "browser_press_key", {"key": "Enter"}, "enter")
asks({**s, "current_url": "https://jobs.lever.co/acme/1/apply"}, B + "browser_type", {"ref": "e12", "text": "Jane", "submit": True},
     "submit")  # (on Workday, Enter in a box doesn't ask: below)
ok(s, B + "browser_click", {"ref": "e61"})       # Next
ok(s, B + "browser_click", {"ref": "e20"})       # open a dropdown
ok(s, B + "browser_click", {"ref": "e50"})       # checkboxes are fine now
ok(s, B + "browser_click", {"ref": "e31"})       # options are fine now
ok(s, B + "browser_press_key", {"key": "Tab"})
ok(s, B + "browser_type", {"ref": "e12", "text": "anything"})   # typing is not tied to the answer bank any more
ok(s, B + "browser_fill_form", {"fields": [{"ref": "e12", "type": "textbox", "value": "Jane"},
                                           {"ref": "e50", "type": "checkbox", "value": "true"}]})
ok(s, B + "browser_select_option", {"ref": "e20", "values": ["Maine"]})
blocked(s, B + "browser_click", {"ref": "e99"}, "snapshot")      # unknown ref: the submit check needs its name
blocked(s, B + "browser_click", {"target": "Submit button"}, "unknown element")  # a description is not a ref

# a sign-in / create-account page's own "Submit" signs in: no ask (only account boxes on the page, as on Workday's,
# bot trap included). Any other question box on the page, or another submit name, still asks.
SIGNIN = f"""### Page
- Page URL: https://{H}/en-US/External/login
### Snapshot
```yaml
- button "Accept Cookies" [ref=f1e1] [cursor=pointer]
- button "Sign In" [ref=f1e2] [cursor=pointer]
- textbox "Email Address" [ref=f1e10]
- textbox "Password" [ref=f1e11]
- button "Submit" [ref=f1e12] [cursor=pointer]
- button "Create Account" [ref=f1e13] [cursor=pointer]
- button "Forgot your password?" [ref=f1e14] [cursor=pointer]
- textbox "Enter website. This input is for robots only, do not enter if you're human." [ref=f1e15]
- button "Apply" [ref=f1e16]
- button "Submit Application" [ref=f1e17]
```"""
g = {**fresh(), "refs": assist.parse_refs(SIGNIN)}
assert assist.sign_in_page(g["refs"]) and not assist.sign_in_page(fresh()["refs"])  # the test form above: other questions
ok(g, B + "browser_click", {"ref": "f1e12"})
assert "sign-in page" in assist.decide(g, B + "browser_click", {"ref": "f1e12"})[1]
asks(g, B + "browser_click", {"ref": "f1e16"}, "submit")   # "Apply" still asks
asks(g, B + "browser_click", {"ref": "f1e17"}, "submit")   # so does "Submit Application"
create = SIGNIN.replace('- textbox "Password" [ref=f1e11]', '- textbox "Password" [ref=f1e11]\n- textbox "Verify New Password" [ref=f1e20]\n'
                        '- checkbox "I agree to the terms" [ref=f1e21]')
ok({**g, "refs": assist.parse_refs(create)}, B + "browser_click", {"ref": "f1e12"})
for extra in ('- textbox "First Name" [ref=f1e30]', '- radio "Yes" [ref=f1e31]', '- combobox "Country" [ref=f1e32]',
              '- textbox [ref=f1e33]'):
    mixed = {**g, "refs": assist.parse_refs(SIGNIN.replace('- button "Submit" [ref=f1e12]', extra + '\n- button "Submit" [ref=f1e12]'))}
    asks(mixed, B + "browser_click", {"ref": "f1e12"}, "submit")  # a page with a question: its Submit may send an application
no_pw = {**g, "refs": assist.parse_refs(SIGNIN.replace('- textbox "Password" [ref=f1e11]\n', ''))}
asks(no_pw, B + "browser_click", {"ref": "f1e12"}, "submit")   # no password box: not a sign-in page
# the posting's own "Apply" opens the application: no ask on the job's own page (the row's link, a language part like
# /en-US/ aside), before anything is filled in, with no form boxes on it (a job search box, a bot trap aside)
POSTING = f"""### Page
- Page URL: https://{H}/en-US/External/job/Boston-MA/Firmware-Intern_R123?source=simplify
### Snapshot
```yaml
- heading "Firmware Intern" [level=2] [ref=p1]
- textbox "Search for Jobs" [ref=p2]
- button "Apply" [ref=p3] [cursor=pointer]
- link "Apply Now" [ref=p4]
- button "Submit" [ref=p5]
- textbox "Enter website. This input is for robots only, do not enter if you're human." [ref=p6]
```"""
post = {**fresh(), "refs": assist.parse_refs(POSTING), "job_url": f"https://{H}/External/job/Boston-MA/Firmware-Intern_R123"}
assist.update_after(post, B + "browser_snapshot", POSTING)
assert assist.same_posting(post["current_url"], post["job_url"]) and assist.posting_page(post)
ok(post, B + "browser_click", {"ref": "p3"})
assert "posting's own Apply" in assist.decide(post, B + "browser_click", {"ref": "p3"})[1]
ok(post, B + "browser_click", {"ref": "p4"})                       # "Apply Now" too
asks(post, B + "browser_click", {"ref": "p5"}, "submit")           # a Submit on it still asks
asks({**post, "filled": True}, B + "browser_click", {"ref": "p3"}, "apply")      # something filled in: asks again
asks({**post, "current_url": f"https://{H}/External/job/Boston-MA/Other-Job_R999"}, B + "browser_click", {"ref": "p3"}, "apply")
asks({**post, "current_url": f"https://other.example/External/job/Boston-MA/Firmware-Intern_R123"}, B + "browser_click",
     {"ref": "p3"}, "apply")                                       # another site
with_form = POSTING.replace('- button "Apply" [ref=p3]', '- textbox "First Name" [ref=p7]\n- button "Apply" [ref=p3]')
asks({**post, "refs": assist.parse_refs(with_form)}, B + "browser_click", {"ref": "p3"}, "apply")  # a form on the page
asks({**post, "job_url": ""}, B + "browser_click", {"ref": "p3"}, "apply")      # no link to compare with
# a button with no name, only its text (Workday: `button [ref=e278] [cursor=pointer]: Apply`), counts by its text:
# a final Submit written that way asks too
NAMELESS = SNAP.replace('- button "Submit Application" [ref=e90]', '- button [ref=e90] [cursor=pointer]: Submit Application\n'
                                                                    '- link [ref=e91]: Submit\n- button [ref=e92]: "Next"')
nm = {**fresh(), "refs": assist.parse_refs(NAMELESS)}
assert nm["refs"]["e90"]["name"] == "Submit Application" and nm["refs"]["e92"]["name"] == "Next"
asks(nm, B + "browser_click", {"ref": "e90"}, "submit")
asks(nm, B + "browser_click", {"ref": "e91"}, "submit")
ok(nm, B + "browser_click", {"ref": "e92"})
wd = POSTING.replace('- button "Apply" [ref=p3] [cursor=pointer]', '- button [ref=p3] [cursor=pointer]: Apply')
ok({**post, "refs": assist.parse_refs(wd)}, B + "browser_click", {"ref": "p3"})  # Workday's posting: no ask
# Enter: no ask on a sign-in page or the posting (no form there); on any page with a form it asks
ok(g, B + "browser_press_key", {"key": "Enter"})
ok(post, B + "browser_press_key", {"key": "Enter"})
asks({**post, "refs": assist.parse_refs(with_form)}, B + "browser_press_key", {"key": "Enter"}, "click the option")
asks(fresh(), B + "browser_press_key", {"key": "Enter"}, "enter")
# Workday: Enter in a box doesn't ask (its pages don't submit on Enter: Submit is its own button on a page with no
# boxes); the box must have the focus in the latest snapshot ([active]), or be the one typed into with submit
WD_FORM = f"""### Page
- Page URL: https://{H}/en-US/External/job/Boston-MA/Firmware-Intern_R123/apply/applyManually
### Snapshot
```yaml
- textbox "First Name*" [ref=w1]
- textbox "How Did You Hear About Us?*" [active] [ref=w2]
- button "Save and Continue" [ref=w3]
```"""
wdf = {**fresh(), "job_url": f"https://{H}/External/job/Boston-MA/Firmware-Intern_R123"}
assist.update_after(wdf, B + "browser_snapshot", WD_FORM)
assert wdf["refs"]["w2"] == {"role": "textbox", "name": "How Did You Hear About Us?*", "active": True}
assert "active" not in wdf["refs"]["w1"]
ok(wdf, B + "browser_press_key", {"key": "Enter"})
assert "Workday" in assist.decide(wdf, B + "browser_press_key", {"key": "Enter"})[1]
ok(wdf, B + "browser_type", {"ref": "w2", "text": "LinkedIn", "submit": True})     # type and search in one call
asks(wdf, B + "browser_type", {"ref": "w3", "text": "x", "submit": True}, "submit")  # into a button: asks
on_button = {**wdf, "refs": assist.parse_refs(WD_FORM.replace(' [active] [ref=w2]', ' [ref=w2]')
                                              .replace('- button "Save and Continue" [ref=w3]', '- button "Submit" [active] [ref=w3]'))}
asks(on_button, B + "browser_press_key", {"key": "Enter"}, "enter")                # the focus on a button: Enter clicks it
asks({**wdf, "refs": {}}, B + "browser_press_key", {"key": "Enter"}, "enter")      # no snapshot since: unknown focus
gh = {**wdf, "current_url": "https://job-boards.greenhouse.io/acme/jobs/1"}         # one-page forms submit on Enter
asks(gh, B + "browser_press_key", {"key": "Enter"}, "enter")
asks(gh, B + "browser_type", {"ref": "w2", "text": "LinkedIn", "submit": True}, "submit")
assert not assist.workday({"current_url": "https://evilmyworkdayjobs.com/x"}) and assist.workday({"current_url": f"https://{H}/x"})
# the hook remembers that something was filled in (a type, a pick, an upload), so the next "Apply" asks
dd = tempfile.mkdtemp()
with open(os.path.join(dd, "state.json"), "w") as f:
    json.dump({**post, "answer_cmd": ANSWER_CMD}, f)
os.environ[assist.STATE_ENV] = dd
sys.stdin = io.StringIO(json.dumps({"tool_name": B + "browser_type", "tool_input": {"ref": "p2", "text": "firmware"}}))
assist.hook("pre")
sys.stdin = sys.__stdin__
assert json.load(open(os.path.join(dd, "state.json")))["filled"] is True

# the bot trap is never filled
blocked(g, B + "browser_type", {"ref": "f1e15", "text": "x"}, "trap for bots")
blocked(g, B + "browser_fill_form", {"fields": [{"ref": "f1e10", "type": "textbox", "value": "me@example.com"},
                                                {"ref": "f1e15", "type": "textbox", "value": "x"}]}, "trap for bots")
ok(g, B + "browser_fill_form", {"fields": [{"ref": "f1e10", "type": "textbox", "value": "me@example.com"},
                                           {"ref": "f1e15", "type": "textbox", "value": ""}]})

# never passwords
blocked(s, B + "browser_type", {"ref": "e70", "text": "x"}, "password")
blocked(s, B + "browser_fill_form", {"fields": [{"ref": "e70", "type": "textbox", "value": "x"}]}, "password")

# uploads: only the resume
ok(s, B + "browser_file_upload", {"paths": [RESUME]})
ok(s, B + "browser_file_upload", {"paths": []})   # cancel the file chooser
blocked(s, B + "browser_file_upload", {"paths": [os.path.join(config.PROJECT_DIR, "token.json")]}, "only the resume")
blocked(s, B + "browser_file_upload", {"paths": [RESUME, "/etc/passwd"]}, "only the resume")
blocked({**s, "resume": None}, B + "browser_file_upload", {"paths": [RESUME]}, "only the resume")
# the browser tool only uploads from its output folder: the run gets a copy there, and only the copy is allowed
run_dir = tempfile.mkdtemp()
copy = assist.upload_copy(RESUME, run_dir)
assert copy == os.path.realpath(os.path.join(run_dir, "upload", "Resume.pdf")) and open(copy).read() == "x"
assert os.stat(copy).st_mode & 0o777 == 0o600
c = {**s, "resume": copy}
ok(c, B + "browser_file_upload", {"paths": [copy]})
blocked(c, B + "browser_file_upload", {"paths": [RESUME]}, "only the resume")
assert assist.upload_copy(RESUME, run_dir) == copy   # a second run into the same folder is fine
# the transcript (optional, local_config transcript_path): its copy may be uploaded too, and its source read; nothing else
tsrc = os.path.join(tempfile.mkdtemp(), "unofficial_transcript.pdf")
open(tsrc, "w").write("t")
tcopy = assist.upload_copy(tsrc, run_dir)
t = {**c, "transcript": tcopy, "transcript_src": os.path.realpath(tsrc)}
ok(t, B + "browser_file_upload", {"paths": [tcopy]})
ok(t, B + "browser_file_upload", {"paths": [copy, tcopy]})
blocked(t, B + "browser_file_upload", {"paths": [tsrc]}, "or the transcript")
blocked(c, B + "browser_file_upload", {"paths": [tcopy]}, "only the resume")   # no transcript saved: never
assert assist.check_read(t, "Read", {"file_path": tsrc}) is None
assert assist.check_read(c, "Read", {"file_path": tsrc}) is not None

# Bash: only the answer-bank command, no shell tricks
ok(s, "Bash", {"command": '/venv/python /proj/assist.py answer "City*" --option "A (+1)"'})
ok(s, "Bash", {"command": "/venv/python /proj/assist.py wait"})
for bad in ['rm -rf /', '/venv/python /proj/assist.py answer "x"; rm -rf /', '/venv/python /proj/assist.py answer "$(id)"',
            '/venv/python /proj/assist.py hook pre', '/venv/python /other.py answer x', 'cat answers.json',
            '/venv/python /proj/assist.py answer x | sh']:
    blocked(s, "Bash", {"command": bad})

# refs: only from the latest snapshot; page-changing actions without one clear them (stale refs can't be trusted)
s = fresh()
assist.update_after(s, B + "browser_click", "### Page\n- Page URL: https://" + H + "/next\n")
assert s["refs"] == {}, "a click without a snapshot must clear refs"
blocked(s, B + "browser_click", {"ref": "e61"}, "snapshot")
assist.update_after(s, B + "browser_snapshot", SNAP.replace('button "Next" [ref=e61]', 'button "Submit" [ref=e61]'))
asks(s, B + "browser_click", {"ref": "e61"}, "submit")  # same ref, now Submit: the user is asked
s2 = fresh()
assist.update_after(s2, B + "browser_wait_for", "waited")  # not page-changing: refs kept
assert s2["refs"]
assist.update_after(s2, B + "browser_click", "### Page\n- Page URL: https://accounts.example/x\n")
assert s2["current_url"] == "https://accounts.example/x" and s2["refs"] == {}

# unnamed fields take the question text right above them (from the page, never from the agent)
Q = """              - group [ref=f13e993]:
                - paragraph [ref=f13e997]: What are your top 3 skills? (30 word limit)*
                - textbox [ref=f13e1000]
              - group [ref=f13e1002]:
                - paragraph [ref=f13e1006]: How did you hear about this co-op opportunity? (30 word limit)*
                - textbox [ref=f13e1009]
                - textbox [ref=f13e1010]
          - button "Back" [ref=f13e744] [cursor=pointer]"""
r = assist.parse_refs(Q)
assert r["f13e1000"]["name"] == "What are your top 3 skills? (30 word limit)*"
assert r["f13e1009"]["name"] == "How did you hear about this co-op opportunity? (30 word limit)*"
assert r["f13e1010"]["name"] == "" and r["f13e744"]["name"] == "Back"

# files: only the resume and notes, never the project folder (secrets)
notes = tempfile.mkdtemp()
open(os.path.join(notes, "writeup.md"), "w").write("x")
resume = os.path.join(tmp, "resume.pdf")
open(resume, "w").write("x")
os.symlink(config.PROJECT_DIR, os.path.join(notes, "sneaky"))
r = {**fresh(), "read_roots": [os.path.realpath(notes), os.path.realpath(resume)]}  # noqa
ok(r, "Read", {"file_path": os.path.join(notes, "writeup.md")})
ok(r, "Read", {"file_path": resume})
ok(r, "Grep", {"pattern": "XSS", "path": notes})
ok(r, "Glob", {"pattern": "**/*.md", "path": notes})
blocked(r, "Read", {"file_path": os.path.join(config.PROJECT_DIR, "token.json")}, "blocked")
blocked(r, "Read", {"file_path": os.path.join(notes, "sneaky", "token.json")}, "blocked")      # symlink out
blocked(r, "Read", {"file_path": os.path.join(notes, "..", "x")}, "blocked")
blocked(r, "Grep", {"pattern": "x"}, "path")                                               # default = project folder
blocked(r, "Glob", {"pattern": "/home/**/token.json", "path": notes}, "inside")
blocked(r, "Glob", {"pattern": "../**", "path": notes}, "inside")
blocked(r, "Read", {"file_path": tmp + "/answers.json"}, "blocked")

# which rows the agent may take: company sites only, never NUworks, never NUworks-side blockers
u, why = assist.assist_target(f"External application: {H} -> https://{H}/careers/job/X_1")
assert u == f"https://{H}/careers/job/X_1" and why is None
u, why = assist.assist_target("Popup requires Cover Letter, Transcript: attach by hand on NUworks.")
assert u is None and "yours on NUworks" in why
u, why = assist.assist_target("Popup requires Cover Letter", url_override=f"https://{H}/x")  # --url can't turn it into one
assert u is None
u, why = assist.assist_target(f"External application: {H}")
assert u is None and "--url" in why
assert assist.assist_target(f"External application: {H}", f"https://{H}/careers/job/X_1")[0]
nu = "https://northeastern-csm.symplicity.com/students/app/jobs/detail/abc"
assert "NUworks itself" in assist.assist_target(f"External application: {H}", nu)[1]
assert "NUworks itself" in assist.assist_target(f"External application: x -> {nu}")[1]

# links in Notes
assert assist.link_from_notes(f"External application: {H} -> https://{H}/careers/job/X_1") == f"https://{H}/careers/job/X_1"
safe = "https://eur01.safelinks.protection.outlook.com/?url=https%3A%2F%2Fcareer.example%2Fjob%3Fid%3D1&data=x"
assert assist.link_from_notes(f"External application: eur01.safelinks.protection.outlook.com -> {safe}") == "https://career.example/job?id=1"
assert assist.link_from_notes(f"External application: {H}") is None
assert assist.link_from_notes("Popup requires Cover Letter") is None

# answer bank lookups
answers.save([answers.new_entry("email", "me@example.com", "text"), answers.new_entry("work authorization", "", "select", always_ask=True)])
E = answers.load()
assert assist.lookup(E, "Email*", [], "My Information") == {"status": "answer", "value": "me@example.com"}
assert assist.lookup(E, "City*", [], "My Information") == {"status": "unknown", "saved": [{"question": "email", "answer": "me@example.com"}]}
assert assist.lookup(E, "Work Authorization*", ["Yes", "No"], "")["status"] == "ask_every_time"
assert assist.lookup(E, "Email*", [], "Voluntary Disclosures") == {"status": "answer", "value": "me@example.com"}  # saved demographics are reused
assert assist.lookup(E, "Email*", ["a@b.c"], "")["status"] == "not_an_option"
assert assist.valid_answer("x" * 301, []) and assist.valid_answer("two\nlines", []) and assist.valid_answer(" ", [])
assert assist.valid_answer("No", ["Yes", "No"]) is None and assist.valid_answer("Nope", ["Yes", "No"])

# answer-bank commands through a run folder (what the agent runs)
d = os.path.join(tmp, "run")
os.makedirs(d)
json.dump(fresh(), open(os.path.join(d, "state.json"), "w"))
os.environ[assist.STATE_ENV] = d


def bank(*argv):
    out, sys.stdout = sys.stdout, io.StringIO()
    try:
        assist.bank(list(argv))
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout = out


def state():
    return json.load(open(os.path.join(d, "state.json")))


assert bank("answer", "Email*")["value"] == "me@example.com" and state()["issued"]["email"] == "me@example.com"
assert bank("answer", "City*")["status"] == "unknown"
assert bank("save", "City*", "Boston")["saved"] and answers.find(answers.load(), "city")["answer"] == "Boston"
assert "error" in bank("save", "City*", "Cambridge")          # never silently overwritten
assert "error" in bank("save", "Why us?*", "line one\nline two")  # no essays
assert "error" in bank("save", "Work Authorization*", "Yes", "--option", "Yes", "--option", "No")  # always-ask: once only
before = open(config.ANSWERS_PATH).read()
r = bank("once", "Gender*", "Decline", "--page", "Voluntary Disclosures")
assert r["value"] == "Decline" and not r["saved"] and open(config.ANSWERS_PATH).read() == before
assert "Decline" in state()["issued_values"]
assert bank("blank", "Phone Extension")["status"] == "leave_blank"
assert bank("answer", "Phone Extension")["status"] == "leave_blank"
assert bank("alias", "E-mail Address*", "email")["value"] == "me@example.com"
# unknown: the agent gets the saved answers to work it out from; never always-ask or leave-blank ones
saved = bank("answer", "Your e-mail*")["saved"]
assert {"question": "email", "answer": "me@example.com"} in saved and {"question": "city", "answer": "Boston"} in saved
assert all(x["question"] not in ("work authorization", "phone extension") for x in saved)

# NUworks prompt: typing the option's exact text works too (was: number only)
class IO(answers.TerminalIO):  # the terminal wording, canned replies
    def __init__(self, reply):
        self.reply = reply

    def say(self, m):
        pass

    def ask(self, p):
        return self.reply


assert answers.choose_answer("x", "select", ["A", "University / College recruiting"], IO("University / College recruiting")) == "University / College recruiting"
assert answers.choose_answer("x", "select", ["A", "B"], IO("2")) == "B"

# NUworks-side retry: refused once Submit was clicked there (mark is written before the click), or when done
from nuauto.apply import NUWORKS_CLICK_MARK
blocked = assist.nuworks_side_blocked
assert blocked("") is None
assert blocked("Applied on the company site.") is None
assert blocked("NUworks side NOT submitted: Resume missing.") is None
assert "already submitted" in blocked("x NUworks side submitted too (confirmed by NUworks page).")
assert "check NUworks" in blocked(f"x {NUWORKS_CLICK_MARK}")
assert "check NUworks" in blocked("NUworks side: Submit clicked but no confirmation seen; check NUworks.")
assert "check NUworks" in blocked("NUworks side: stopped by Ctrl+C after Submit was clicked; check NUworks.")
assert blocked("NUworks side: stopped by Ctrl+C before Submit; not submitted.") is None

# ---- the Other jobs tab: jobs that are not on NUworks
from nuauto import sheet  # noqa: E402

Row = sheet.Row
assert assist.other_target(Row(2, f"https://{H}/careers/job/X_1", "Acme", "Intern", "Approved", "", "")) == (f"https://{H}/careers/job/X_1", None)
assert "not Approved" in assist.other_target(Row(2, f"https://{H}/j", "Acme", "Intern", "Proposed", "", ""))[1]
assert "not Approved" in assist.other_target(Row(2, f"https://{H}/j", "Acme", "Intern", "Applied", "", ""))[1]
assert "NUworks itself" in assist.other_target(Row(2, nu, "Acme", "Intern", "Approved", "", ""))[1]
assert "https" in assist.other_target(Row(2, f"http://{H}/j", "Acme", "Intern", "Approved", "", ""))[1]

# NUworks-only answers: the default list, or the entry's own flag
assert answers.nuworks_only(answers.new_entry("Available Start Date")) and not answers.nuworks_only(answers.new_entry("email"))
assert not answers.nuworks_only({**answers.new_entry("co-op term"), "nuworks_only": False})
assert answers.nuworks_only({**answers.new_entry("email"), "nuworks_only": True})

# an Other jobs run: its own bank first, then the NUworks one minus NUworks-only entries; saves go to its own bank
start = answers.new_entry("available start date", "2027-01-11", "text")
start["aliases"] = ["start date"]
answers.save([answers.new_entry("email", "me@example.com", "text"), start, answers.new_entry("gpa", "", "text"),
              answers.new_entry("work authorization", "", "select", always_ask=True)])
answers.save([answers.new_entry("phone", "555-0199", "text")], config.OTHER_ANSWERS_PATH)
other_state = {**fresh(), "tab": "other"}
json.dump(other_state, open(os.path.join(d, "state.json"), "w"))
main_before = open(config.ANSWERS_PATH).read()
assert bank("answer", "Email*")["value"] == "me@example.com"                  # shared answers still work
assert bank("answer", "Phone*")["value"] == "555-0199"                        # its own bank
unknown = bank("answer", "Start Date*")                                       # NUworks-only (even by alias): asked
assert unknown["status"] == "unknown" and all("start" not in x["question"] for x in unknown["saved"]), unknown
assert {"question": "phone", "answer": "555-0199"} in unknown["saved"] and {"question": "email", "answer": "me@example.com"} in unknown["saved"]
assert bank("answer", "Work Authorization*", "--option", "Yes", "--option", "No")["status"] == "ask_every_time"
V = ["--option", "Decline to self-identify", "--option", "Yes", "--page", "Voluntary Self-Identification"]
assert bank("save", "Veteran Status*", "Decline to self-identify", *V)["saved"]   # demographics are saved...
assert bank("answer", "Veteran Status*", *V) == {"status": "answer", "value": "Decline to self-identify"}  # ...and reused
assert bank("save", "Start Date*", "2027-05-24")["saved"]
assert bank("save", "GPA", "3.9")["saved"]                                   # a NUworks starter with no answer: saved here
assert open(config.ANSWERS_PATH).read() == main_before, "an Other jobs run changed answers.json"
own = answers.load(config.OTHER_ANSWERS_PATH)
assert answers.find(own, "start date")["answer"] == "2027-05-24" and answers.find(own, "gpa")["answer"] == "3.9"
assert bank("answer", "Start Date*")["value"] == "2027-05-24"
assert "error" in bank("save", "Email*", "other@example.com")                 # never silently overwritten
assert bank("blank", "Middle Name")["saved"] and answers.find(answers.load(config.OTHER_ANSWERS_PATH), "middle name")["leave_blank"]
assert open(config.ANSWERS_PATH).read() == main_before
assert bank("alias", "E-mail address*", "email")["value"] == "me@example.com"  # an alias goes on the entry it names
assert "e-mail address" in answers.find(answers.load(), "email")["aliases"]
assert bank("alias", "Mobile*", "phone")["value"] == "555-0199"
assert "mobile" in answers.find(answers.load(config.OTHER_ANSWERS_PATH), "phone")["aliases"]
assert "error" in bank("alias", "Begin*", "available start date")             # NUworks-only: not even by alias
# a NUworks job never sees the Other jobs bank
json.dump(fresh(), open(os.path.join(d, "state.json"), "w"))
assert bank("answer", "Start Date*")["value"] == "2027-01-11" and bank("answer", "Phone*")["status"] == "unknown"


# the whole run for an Other jobs row, with fakes for the terminal, Claude and the sheet: Applied when you say so,
# never anything on NUworks, no weekly / total cap
class FakeWS:
    def __init__(self, values):
        self.values = values

    def get_all_values(self):
        return [list(r) for r in self.values]

    def update(self, range_name, values, value_input_option=None):
        row = int(range_name.split(":")[0][1:])
        self.values[row - 1][3:6] = values[0]


from nuauto import browser  # noqa: E402

config.LOGS_DIR, config.ASSIST_PROFILE_DIR, config.LAPTOP_RESUME = os.path.join(tmp, "logs"), os.path.join(tmp, "prof"), RESUME
config.RESUME_PATH = RESUME  # assist.run reads the resume from RESUME_PATH (the homelab has its copy there)
config.ASSIST_READ_PATHS = []
posting = "https://careers.example.com/jobs/4821"
full_week = [list(sheet.HEADERS)] + [[f"https://x/{i}", "Co", "Job", "Applied", "", __import__("datetime").date.today().isoformat()]
                                    for i in range(sheet.MAX_PER_WEEK + 2)]
ws = FakeWS(full_week + [[posting, "Granite Robotics", "Summer Robotics Intern", "Approved", "", ""]])
row_n = len(ws.values)
calls = []
sheet.open_other = lambda interactive=True, create=False: ws
sheet.open_worksheet = lambda interactive=True: (_ for _ in ()).throw(AssertionError("opened the NUworks tab"))
assist.nuworks_side = lambda *a, **k: calls.append("nuworks_side")
assist.subprocess.run = lambda cmd, **kw: calls.append(cmd)
sys.stdin.isatty = lambda: True
for reply, status in (("n", "Approved"), ("y", "Applied")):
    __builtins__.input = lambda prompt, r=reply: r
    out, sys.stdout = sys.stdout, io.StringIO()
    try:
        assist.main(["other", str(row_n)])
    finally:
        sys.stdout = out
    assert ws.values[row_n - 1][3] == status, ws.values[row_n - 1]
cmd = calls[0]
context = cmd[cmd.index("--append-system-prompt") + 1]
assert f"Other jobs row {row_n}: Granite Robotics" in context and "not on NUworks" in context and posting in context
assert cmd[-1].endswith(f"for Other jobs row {row_n}, following the rules.")
assert "nuworks_side" not in calls
assert ws.values[row_n - 1][5] == __import__("datetime").date.today().isoformat()
assert "nuauto assist" in ws.values[row_n - 1][4]
upload = [p for p in os.listdir(config.LOGS_DIR) if p.endswith(f"assist_other_row{row_n}")]
assert upload and os.path.exists(os.path.join(config.LOGS_DIR, upload[0], "browser", "upload", "Resume.pdf"))  # the resume copy
assert not os.path.exists(os.path.join(config.LOGS_DIR, upload[0], "upload"))   # not in the run folder itself (browser/ is the tool's)
assert json.load(open(os.path.join(config.LOGS_DIR, upload[0], "state.json")))["tab"] == "other"
try:
    assist.main(["other", str(row_n)])  # Applied now: refused
    raise AssertionError("ran on an Applied row")
except SystemExit as e:
    assert "not Approved" in str(e), e

# ---- passwords: only placeholders, filled by the guard for that site; the agent never sees one


def refused(state, tool, inp, needle=""):  # (blocked is the NUworks-side check by now)
    verdict, why = assist.decide(state, tool, inp)
    assert verdict == "deny" and needle.lower() in why.lower(), (tool, inp, verdict, why)


from nuauto import accounts  # noqa: E402
accounts.PATH, accounts.LOCK = os.path.join(tmp, "accounts.json"), os.path.join(tmp, "accounts.lock")  # never the real one
config.LOCAL = {**config.LOCAL, "accounts_email": "me@example.com"}
NEW, IN = accounts.NEW, accounts.SIGN_IN
p = fresh()
assert assist.decide(p, B + "browser_type", {"ref": "e70", "text": NEW}) == ("fill", [("text", NEW)])
assert assist.decide(p, B + "browser_fill_form", {"fields": [{"ref": "e12", "type": "textbox", "value": "Jane"},
                                                             {"ref": "e70", "type": "textbox", "value": IN}]}) == ("fill", [(1, IN)])
refused(p, B + "browser_type", {"ref": "e12", "text": NEW}, "only into a field")     # not a password field
refused(p, B + "browser_type", {"ref": "e99", "text": NEW}, "only into a field")     # a ref the snapshot doesn't have
refused(p, B + "browser_type", {"ref": "e70", "text": NEW + "!"}, "alone")           # mixed with other text
refused(p, B + "browser_type", {"ref": "e12", "text": f"my password is {IN}"}, "alone")
refused(p, B + "browser_type", {"ref": "e70", "text": "hunter2"}, "never a password itself")
refused(p, B + "browser_type", {"ref": "e70", "text": NEW, "submit": True}, "without submit")
refused(p, B + "browser_fill_form", {"fields": [{"name": "Password", "type": "textbox", "value": "hunter2"}]}, "never a password")
refused(p, B + "browser_fill_form", {"fields": [{"ref": "e12", "type": "textbox", "value": NEW}]}, "only into a field")
for idp in ("https://accounts.google.com/signin", "https://login.microsoftonline.com/x", "https://neuidmsso.neu.edu/idp",
            "https://www.linkedin.com/login", "https://appleid.apple.com/auth"):
    refused({**p, "current_url": idp}, B + "browser_type", {"ref": "e70", "text": IN}, "yours")
ok(p, "WebSearch", {"query": "Acme Robotics firmware team"})
ok(p, "WebFetch", {"url": "https://acme.example/about", "prompt": "what do they build?"})

p2 = {**fresh(), "company": "Acme", "job_title": "Firmware Intern", "job_url": "https://x/1"}
filled = assist.fill(p2, {"ref": "e70", "text": NEW}, [("text", NEW)])
pw = filled["text"]
assert pw not in accounts.PLACEHOLDERS and len(pw) == accounts.LENGTH and filled["ref"] == "e70"
assert assist.fill(p2, {"ref": "e70", "text": NEW}, [("text", NEW)])["text"] == pw   # the confirm box: the same one
assert assist.fill(p2, {"ref": "e70", "text": IN}, [("text", IN)])["text"] == pw    # signing in later
form = assist.fill(p2, {"fields": [{"ref": "e12", "value": "Jane"}, {"ref": "e70", "value": IN}]}, [(1, IN)])
assert form["fields"] == [{"ref": "e12", "value": "Jane"}, {"ref": "e70", "value": pw}]
assert p2["password_sites"] == [H]
saved = json.load(open(accounts.PATH))
assert saved[H]["email"] == "me@example.com" and saved[H]["company"] == "Acme" and saved[H]["url"] == f"https://{H}/"
assert [j["url"] for j in saved[H]["jobs"]] == ["https://x/1"] and os.stat(accounts.PATH).st_mode & 0o777 == 0o600
p3 = {**fresh(), "current_url": "https://careers-other.icims.com/jobs/1/login"}
try:
    assist.fill(p3, {"ref": "e70", "text": IN}, [("text", IN)])
    raise AssertionError("signed in where NUauto has no login")
except accounts.NoAccount as e:
    assert "no saved login" in str(e)
assert assist.fill(p3, {"ref": "e70", "text": NEW}, [("text", NEW)])["text"] != pw  # every site its own password
pwds = {accounts.make_password() for _ in range(50)}
assert len(pwds) == 50 and all(any(c in accounts.SYMBOLS for c in x) and any(c.isdigit() for c in x)
                               and any(c.isupper() for c in x) for x in pwds)
assert accounts.never("acme.wd5.myworkdayjobs.com") is None and accounts.never("") and accounts.never("x.okta.google.com")

# replies: the browser tool echoes what it typed; the guard takes the password out (and out of the run's log)
reply = [{"type": "text", "text": "### Ran Playwright code\n```js\nawait page.getByRole('textbox', { name: 'Password' })"
                                  f".fill('{pw}');\n```\n### Page\n- Page URL: https://{H}/signup"}]
red, changed = assist.redact(reply, accounts.passwords([H]))
assert changed and pw not in json.dumps(red) and assist.HIDDEN in red[0]["text"] and assist.redact(reply, [])[1] is False
snap = [{"type": "text", "text": f'### Snapshot\n- textbox "Password" [ref=e7]: {pw}\n- textbox "Email" [ref=e5]: me@example.com'}]
assert assist.redact(snap, [pw])[0][0]["text"].endswith(f'[ref=e7]: {assist.HIDDEN}\n- textbox "Email" [ref=e5]: me@example.com')

# the hooks themselves: the call on stdin, Claude Code's answer on stdout; the log shows the placeholder only
d = tempfile.mkdtemp()
with open(os.path.join(d, "state.json"), "w") as f:
    json.dump({**fresh(), "company": "Acme", "job_title": "T", "job_url": "https://x/1", "password_sites": []}, f)
os.environ[assist.STATE_ENV] = d


def run_hook(kind, data):
    out, sys.stdout, real_in, sys.stdin = sys.stdout, io.StringIO(), sys.stdin, io.StringIO(json.dumps(data))
    try:
        assist.hook(kind)
        return sys.stdout.getvalue()
    except SystemExit as e:
        return f"EXIT {e.code}"
    finally:
        sys.stdout, sys.stdin = out, real_in


res = json.loads(run_hook("pre", {"tool_name": B + "browser_type",
                                  "tool_input": {"ref": "e70", "text": NEW, "element": "Password"}}))["hookSpecificOutput"]
assert res["permissionDecision"] == "allow" and res["updatedInput"] == {"ref": "e70", "text": pw, "element": "Password"}
logged = open(os.path.join(d, "actions.log")).read()
assert "FILL" in logged and NEW in logged and pw not in logged
post = json.loads(run_hook("post", {"tool_name": B + "browser_type", "tool_response": reply}))["hookSpecificOutput"]
assert post["hookEventName"] == "PostToolUse" and pw not in json.dumps(post) and assist.HIDDEN in post["updatedToolOutput"][0]["text"]
assert pw not in open(os.path.join(d, "last_response.json")).read()
assert run_hook("post", {"tool_name": B + "browser_snapshot", "tool_response": [{"type": "text", "text": SNAP}]}) == ""
assert run_hook("pre", {"tool_name": B + "browser_type", "tool_input": {"ref": "e70", "text": "hunter2"}}) == "EXIT 2"
with open(os.path.join(d, "state.json")) as f:
    st = json.load(f)
st["current_url"] = "https://new-site.example/login"
with open(os.path.join(d, "state.json"), "w") as f:
    json.dump(st, f)
assert run_hook("pre", {"tool_name": B + "browser_type", "tool_input": {"ref": "e70", "text": IN}}) == "EXIT 2"  # no login there

# after the session: Claude Code's session log (the guard's answers) and the run's folder are cleaned of the passwords
import time  # noqa: E402
logs_home = tempfile.mkdtemp()
os.environ["CLAUDE_CONFIG_DIR"] = logs_home
sess = assist.session_log_dir(config.STATE_DIR)  # sessions run in the state folder
os.makedirs(os.path.join(sess, "sub"))
line = json.dumps({"type": "attachment", "attachment": {"stdout": json.dumps({"updatedInput": {"text": pw}})}})
for p_ in (os.path.join(sess, "s1.jsonl"), os.path.join(sess, "sub", "s2.jsonl"), os.path.join(d, "page-1.yml")):
    with open(p_, "w") as f:
        f.write(line + "\n" + json.dumps({"type": "user", "message": "hi"}) + "\n")
    os.chmod(p_, 0o600)
old = os.path.join(sess, "old.jsonl")
with open(old, "w") as f:
    f.write(pw)
os.utime(old, (time.time() - 7200, time.time() - 7200))  # from before the session: left alone
before = time.time() - 60
lines_ = []
assist.after_session(d, before + 60, type("L", (), {"write": lambda self, m: lines_.append(m)})())
for p_ in (os.path.join(sess, "s1.jsonl"), os.path.join(sess, "sub", "s2.jsonl"), os.path.join(d, "page-1.yml")):
    text = open(p_).read()
    assert pw not in text and assist.HIDDEN in text and [json.loads(x) for x in text.splitlines()], p_  # still valid JSON lines
    assert os.stat(p_).st_mode & 0o777 == 0o600
assert open(old).read() == pw and lines_ and "3 files" in lines_[0], lines_
del os.environ["CLAUDE_CONFIG_DIR"]
real_dir = os.path.realpath(tempfile.mkdtemp())  # Claude Code names the folder after the real path (macOS: /private/var)
assert assist.session_log_dir(real_dir) == os.path.join(os.path.expanduser("~/.claude"), "projects",
                                                        "".join(c if c.isalnum() or c == "-" else "-" for c in real_dir))
assert assist.session_log_dir(real_dir).rsplit("/", 1)[1].startswith("-")  # "/tmp/x" -> "-tmp-x"

# Bitwarden's import file: one login per site, matched on that host only, the jobs in its notes; then nothing new
path, n = accounts.export(os.path.join(tmp, "bw.json"))
bw = json.load(open(path))
item = next(i for i in bw["items"] if i["login"]["uris"][0]["uri"] == f"https://{H}/")
assert n == 2 and bw["encrypted"] is False and bw["folders"][0]["name"] == accounts.FOLDER and item["folderId"] == bw["folders"][0]["id"]
assert item["type"] == 1 and item["login"]["username"] == "me@example.com" and item["login"]["password"] == pw
assert item["login"]["uris"][0]["match"] == accounts.BITWARDEN_HOST_MATCH and "https://x/1" in item["notes"]
assert os.stat(path).st_mode & 0o777 == 0o600
assert accounts.export(os.path.join(tmp, "bw2.json")) == (None, 0) and accounts.export(os.path.join(tmp, "bw3.json"), every=True)[1] == 2

# notes: anything inside the notes folders except secrets (keys, tokens, .env, .git, NUauto's local/ folders)
proj = tempfile.mkdtemp()
for rel in ("app/local/local_config.json", "app/local/token.json", "app/notes.md", "app/.env", "app/.git/config",
            "app/deploy_key.pem", "writeups/robot.md", "writeups/api_token.txt"):
    os.makedirs(os.path.dirname(os.path.join(proj, rel)), exist_ok=True)
    open(os.path.join(proj, rel), "w").write("x")
roots = [os.path.realpath(proj)]
q = {**fresh(), "read_roots": roots, "protected": assist.protected_dirs(roots)}
assert os.path.realpath(os.path.join(proj, "app", "local")) in q["protected"]
ok(q, "Read", {"file_path": os.path.join(proj, "app", "notes.md")})
ok(q, "Read", {"file_path": os.path.join(proj, "writeups", "robot.md")})
ok(q, "Glob", {"pattern": "**/*.md", "path": proj})
ok(q, "Grep", {"pattern": "firmware", "path": os.path.join(proj, "writeups")})
refused(q, "Grep", {"pattern": "x", "path": proj}, "app/local")             # would search NUauto's local/
refused(q, "Grep", {"pattern": "x", "path": os.path.join(proj, "app")}, "own logins")
for secret in ("app/local/token.json", "app/local/local_config.json", "app/.env", "app/.git/config", "app/deploy_key.pem",
               "writeups/api_token.txt"):
    refused(q, "Read", {"file_path": os.path.join(proj, secret)}, "blocked")
refused(q, "Read", {"file_path": accounts.PATH}, "blocked")
config.LOGS_DIR = os.path.join(proj, "app", "logs")  # NUauto's run logs: never (snapshots, what earlier runs typed)
os.makedirs(config.LOGS_DIR)
refused(q, "Read", {"file_path": os.path.join(config.LOGS_DIR, "run1", "page-1.yml")}, "run logs")
refused(q, "Grep", {"pattern": "x", "path": os.path.join(proj, "writeups", "..", "app", "logs")}, "run logs")
refused(q, "Grep", {"pattern": "x", "path": os.path.join(proj, "app")}, "")

# the session: Sonnet at the effort setting (low by default), web tools, every hook blocks when it fails
d2 = tempfile.mkdtemp()
cmd = assist.session(d2, ANSWER_CMD, roots, "/tmp/profile-x", "PROMPT", "Other jobs row 3")
assert cmd[cmd.index("--model") + 1] == "sonnet" and cmd[cmd.index("--effort") + 1] == "low"
assert cmd[cmd.index("--permission-mode") + 1] == "default"  # never your auto mode: its check asked about every click
assert cmd[cmd.index("--tools") + 1: cmd.index("--add-dir")] == ["Bash", "Read", "Glob", "Grep", "WebSearch", "WebFetch"]
allowed = cmd[cmd.index("--allowedTools") + 1: cmd.index("--append-system-prompt")]
assert {"mcp__browser", "WebSearch", "WebFetch", "Read"} <= set(allowed) and not any(a.startswith("Write") for a in allowed)
settings = json.load(open(os.path.join(d2, "settings.json")))
for kind in ("PreToolUse", "PostToolUse"):
    h = settings["hooks"][kind][0]["hooks"][0]
    assert h["onFailure"] == "block" and h["timeout"] == 60 and h["command"].endswith(" hook " + ("pre" if kind == "PreToolUse" else "post"))
assert settings["hooks"]["PreToolUse"][0]["matcher"].endswith("|WebSearch|WebFetch")
for kind, arg in (("Stop", "stop"), ("Notification", "notification"), ("UserPromptSubmit", "clear")):  # "needs you"
    h = settings["hooks"][kind][0]["hooks"][0]
    assert h["command"].endswith(" attention " + arg) and h["timeout"] == 10 and "onFailure" not in h, (kind, h)
assert json.load(open(os.path.join(d2, "mcp.json")))["mcpServers"]["browser"]["args"][:3] == \
    [assist.MCP_PACKAGE, "--user-data-dir", "/tmp/profile-x"]
assert cmd[-1] == "Go: open the posting and fill the application for Other jobs row 3, following the rules."
config.LOCAL = {**config.LOCAL, "assist_effort": "medium"}
assert assist.effort() == "medium"
config.LOCAL = {**config.LOCAL, "assist_effort": "turbo"}
assert assist.effort() == "low"
assert "Account email (for any account a site needs): me@example.com" in assist.context_text(
    "row 1", sheet.Row(1, "u", "Co", "T", "Approved", "", ""), "https://x", True, None, [], ANSWER_CMD)

# ---- side by side: up to 3 sessions, each its own browser profile; one per row and per job site
import subprocess  # noqa: E402
assert assist.slots() == 3 and assist.slot_profile(1) == config.ASSIST_PROFILE_DIR
assert assist.slot_profile(2) == config.ASSIST_PROFILE_DIR + "_2"
held = [assist.take_slot({"row": n, "tab": "other", "company": f"Co{n}", "host": "x"}) for n in (1, 2, 3)]
assert [n for n, _ in held] == [1, 2, 3] and assist.take_slot({"row": 9})[0] is None  # all in use
running = assist.sessions()
assert [(s["slot"], s["row"], s["company"]) for s in running] == [(1, 1, "Co1"), (2, 2, "Co2"), (3, 3, "Co3")], running
held[1][1].close()  # slot 2's session ends: free again, and the next one takes it
assert [s["slot"] for s in assist.sessions()] == [1, 3] and assist.take_slot({"row": 9})[0] == 2
config.LOCAL = {**config.LOCAL, "assist_slots": 1}
assert assist.slots() == 1
config.LOCAL = {**config.LOCAL, "assist_slots": 7}
assert assist.slots() == 3  # out of range: the default
site = assist.take_site("https://acme.wd5.myworkdayjobs.com/en-US/Careers/job/1")
assert site and assist.take_site("https://acme.wd5.myworkdayjobs.com/other") is None   # same site: busy
assert assist.take_site("https://beta.wd1.myworkdayjobs.com/x")                        # another site: fine
site.close()
assert assist.take_site("https://acme.wd5.myworkdayjobs.com/x")
# a slot held by another process (a real session) counts too; it frees itself when that process ends
holder = subprocess.Popen([sys.executable, "-c", f"""
import fcntl, json, sys, time
f = open({os.path.join(config.LOCAL_DIR, 'assist_slot2.lock')!r}, "a+"); fcntl.flock(f, fcntl.LOCK_EX); f.seek(0); f.truncate()
f.write(json.dumps({{"slot": 2, "row": 44, "tab": "jobs"}})); f.flush(); print("held", flush=True); time.sleep(30)"""],
    stdout=subprocess.PIPE, text=True)
for h in held:
    h[1] and h[1].close()
assert holder.stdout.readline().strip() == "held"
assert [(s["slot"], s["row"]) for s in assist.sessions()] == [(2, 44)]
holder.kill()
holder.wait()
assert assist.sessions() == []

# the answer bank: commands from sessions side by side never lose each other's answers (one lock around each)
os.makedirs(config.LOCAL_DIR, exist_ok=True)
code = ("import sys; from nuauto import config; config.ANSWERS_PATH, config.OTHER_ANSWERS_PATH, config.LOCAL_DIR = sys.argv[2:5]; "
        "from nuauto import assist; assist.bank(['save', 'Question ' + sys.argv[1], 'answer ' + sys.argv[1]])")
procs = []
for k in range(8):  # each its own run folder, as sessions side by side have: only the bank's lock keeps them apart
    side = tempfile.mkdtemp()
    with open(os.path.join(side, "state.json"), "w") as f:
        json.dump({**fresh(), "tab": "other"}, f)
    procs.append(subprocess.Popen([sys.executable, "-c", code, str(k), config.ANSWERS_PATH, config.OTHER_ANSWERS_PATH,
                                   config.LOCAL_DIR], env={**os.environ, assist.STATE_ENV: side}, stdout=subprocess.DEVNULL))
assert all(p.wait() == 0 for p in procs)
saved = {e["question"]: e["answer"] for e in answers.load(config.OTHER_ANSWERS_PATH)}
assert all(saved.get(f"question {k}") == f"answer {k}" for k in range(8)), saved

# guess, then ask once (2026-10-11): an unknown field gets a recorded guess and the run goes on; at the end the agent
# lists them (guesses) and records the user's answers; every Submit question shows what is still a guess
answers.save([answers.new_entry("email", "me@example.com", "text"), answers.new_entry("city", "Boston", "text"),
              answers.new_entry("work authorization", "", "select", always_ask=True)])
answers.save([], config.OTHER_ANSWERS_PATH)
d = tempfile.mkdtemp()
with open(os.path.join(d, "state.json"), "w") as f:
    json.dump({**fresh(), "tab": "jobs", "company": "Acme", "job_title": "T", "job_url": "https://x/1",
               "password_sites": [], "guesses": {}, "typed": {}, "account_email": "jobs@example.com"}, f)
os.environ[assist.STATE_ENV] = d
YN = ("--option", "Yes", "--option", "No")
assert "error" in bank("guess", "Email*", "x@example.com", "--why", "w")          # it has a saved answer: use it
assert "error" in bank("guess", "Relocate?*", "Yes", *YN)                         # a guess says what it's based on
assert "error" in bank("guess", "Relocate?*", "Maybe", "--why", "w", *YN)         # one of the options
assert "error" in bank("guess", "Relocate?*", "x\ny", "--why", "w")               # one line
r = bank("guess", "Graduation date*", "May 2029", "--why", "resume: B.S. expected May 2029", "--page", "My Experience")
assert r["status"] == "guess" and r["open"] == 1
bank("guess", "Relocate?*", "Yes", "--why", "saved answers: anywhere in the US", *YN)
assert bank("guess", "Work Authorization*", "Yes", "--why", "resume: US citizen", *YN)["open"] == 3  # always-ask: guessed too
bank("guess", "Why Acme?*", "essay draft: I wrote drone firmware...", "--essay", "--why", "writeups/flight-controller.md")
g = bank("guesses")
assert [x["label"] for x in g["open"]] == ["Graduation date*", "Relocate?*", "Work Authorization*", "Why Acme?*"]
assert [x["n"] for x in g["open"]] == [1, 2, 3, 4] and g["open"][2]["always_ask"] and g["open"][3]["essay"]
assert g["open"][1]["options"] == ["Yes", "No"] and g["open"][0]["page"] == "My Experience" and g["settled"] == []
# the guard keeps its own record of what was typed: a value with no saved answer or guess behind it is listed
assert bank("answer", "City*")["value"] == "Boston"
run_hook("pre", {"tool_name": B + "browser_type", "tool_input": {"ref": "e13", "text": "Boston"}})        # saved: fine
run_hook("pre", {"tool_name": B + "browser_type", "tool_input": {"ref": "e12", "text": "Jamie"}})         # no answer behind it
run_hook("pre", {"tool_name": B + "browser_type", "tool_input": {"ref": "e70", "text": assist.accounts.NEW}})  # a password
run_hook("pre", {"tool_name": B + "browser_fill_form", "tool_input": {"fields": [
    {"ref": "e50", "name": "terms", "type": "checkbox", "value": "true"},                                 # a tick box
    {"ref": "e99", "name": "Mobile", "type": "textbox", "value": "jobs@example.com"}]}})                  # the account email
st = state()
assert st["typed"] == {"City*": "Boston", "First Name*": "Jamie", "Mobile (as the agent called it)": "jobs@example.com"}
assert assist.unexplained(st) == [("First Name*", "Jamie")]  # the saved city and the account email are explained
verdict, why = assist.decide(st, B + "browser_click", {"ref": "e90"})                     # "Submit Application"
assert verdict == "ask" and "NOT CONFIRMED BY YOU" in why and "Graduation date* = 'May 2029' (resume: B.S." in why, why
assert "Why Acme?* = 'essay draft: I wrote drone firmware...'" in why and "First Name* = 'Jamie'" in why
assert "NOT CONFIRMED" in assist.decide(st, B + "browser_press_key", {"key": "Enter"})[1]  # Enter asks the same way
# the end list: the user's answers settle every guess (save: reused next time; once: always-ask; confirm: an essay)
assert bank("save", "Graduation date*", "May 2029")["saved"]
assert bank("save", "Relocate?*", "No", *YN)["saved"]                                     # the user changed this one
assert "error" in bank("save", "Work Authorization*", "Yes", *YN)                         # always-ask: once only
assert bank("once", "Work Authorization*", "Yes", *YN)["value"] == "Yes"
assert "error" in bank("confirm", "Cover letter*")                                        # no such guess
assert bank("confirm", "Why Acme?*")["status"] == "confirmed"
assert "error" in bank("confirm", "Why Acme?*")                                           # once only
assert "error" in bank("guess", "Work Authorization*", "No", "--why", "w", *YN)           # answered in this run
g = bank("guesses")
assert g["open"] == [] and {x["label"]: x["how"] for x in g["settled"]} == {
    "Graduation date*": "saved", "Relocate?*": "saved", "Work Authorization*": "once", "Why Acme?*": "confirmed"}
assert answers.find(answers.load(), "relocate?")["answer"] == "No"                        # reused next time
assert answers.find(answers.load(), "why acme?") is None                                  # an essay is never saved
why = assist.decide(state(), B + "browser_click", {"ref": "e90"})[1]
assert "NOT CONFIRMED" not in why and "Relocate?*: 'Yes' -> 'No'" in why and "confirmed as they were: 3" in why, why
assert "First Name* = 'Jamie'" in why                                                     # still listed: no answer behind it
# a guess is settled by a blank or an alias too; a run with nothing guessed and nothing extra asks as before
bank("guess", "Middle name", "J", "--why", "no info")
bank("blank", "Middle name")
bank("guess", "E-mail address*", "me@example.com", "--why", "the saved email")
assert bank("alias", "E-mail address*", "email")["value"] == "me@example.com" and bank("guesses")["open"] == []
plain = assist.decide({**fresh(), "guesses": {}, "typed": {}}, B + "browser_click", {"ref": "e90"})[1]
assert plain.startswith("NUAUTO: clicking 'Submit Application'") and "\n" not in plain
assert "guess" in assist.BASH_COMMANDS and assist.check_bash(fresh(), " ".join(ANSWER_CMD) + " guesses") is None
# the run's context names the writeups (the first place to look for essays)
ctx = assist.context_text("row 3", type("R", (), {"company": "Acme", "title": "Intern"})(), "https://x/1", False, RESUME,
                          [tmp], ANSWER_CMD, ["/home/u/projects/site/data/projects"])
assert "writeups (look here first for essays" in ctx and "/home/u/projects/site/data/projects" in ctx
assert "<answer|guess|guesses|save|once|confirm|alias|blank>" in ctx
assert "Transcript: none saved" in ctx
ctx_t = assist.context_text("row 3", type("R", (), {"company": "Acme", "title": "Intern"})(), "https://x/1", False, RESUME,
                            [tmp], ANSWER_CMD, transcript="/run/upload/transcript.pdf")
assert "Transcript file (the user's unofficial transcript: upload it only where a page asks for a transcript" in ctx_t
assert "/run/upload/transcript.pdf" in ctx_t
assert "writeups" not in assist.context_text("row 3", type("R", (), {"company": "A", "title": "I"})(), "https://x/1",
                                             False, RESUME, [tmp], ANSWER_CMD).split("Resume and notes")[1].split("\n")[1]
rules = assist.rules_text(ANSWER_CMD)
assert "THE END LIST" in rules and f"{' '.join(ANSWER_CMD)} guesses" in rules and "ANSWER " not in rules

# "needs you" (2026-10-11): Claude Code's Stop hook (its turn ended: it waits for your reply) and its permission
# notifications (a Submit to approve) write the run's attention.json and show a desktop notification; you answering
# (UserPromptSubmit) or a finished browser call clears it; idle reminders are left out; the same news only once a minute
from nuauto import health  # noqa: E402
shown = []
health.desktop_notify = lambda title, body="": shown.append((title, body))
d = tempfile.mkdtemp()
with open(os.path.join(d, "state.json"), "w") as f:
    json.dump({**fresh(), "company": "Acme", "where": "Other jobs row 7", "password_sites": []}, f)
os.environ[assist.STATE_ENV] = d
ATT = os.path.join(d, assist.ATTENTION)


def attention(kind, data):
    real_in, sys.stdin = sys.stdin, io.StringIO(json.dumps(data))
    out, sys.stdout = sys.stdout, io.StringIO()
    try:
        assist.attention_main(kind)
        return sys.stdout.getvalue()
    finally:
        sys.stdin, sys.stdout = real_in, out


assert attention("stop", {"hook_event_name": "Stop"}) == ""  # prints nothing: a Stop hook's output could change the turn
assert json.load(open(ATT))["kind"] == "waiting"
assert shown == [("NUauto: Acme", "The assistant is waiting for you in its terminal window (Other jobs row 7).")], shown
attention("stop", {})
assert len(shown) == 1                                           # the same news within a minute: once
attention("notification", {"message": "Claude is waiting for your input", "notification_type": "idle_prompt"})
assert len(shown) == 1 and json.load(open(ATT))["kind"] == "waiting"
attention("notification", {"message": "Claude needs your permission to use mcp__browser__browser_click",
                           "notification_type": "permission_prompt"})
assert json.load(open(ATT))["kind"] == "approve" and shown[-1][1].startswith("Approve or decline in its terminal window")
attention("clear", {"prompt": "all ok"})
assert not os.path.exists(ATT)                                   # you answered
attention("stop", {"last_assistant_message": "Please confirm these guesses:\n1. Graduation = May 2029"})
assert shown[-1][1] == "The assistant is waiting for you in its terminal window (Other jobs row 7): Please confirm these guesses:"
assert json.load(open(ATT))["said"] == "Please confirm these guesses:"
attention("clear", {})
attention("stop", {})
run_hook("post", {"tool_name": B + "browser_snapshot", "tool_response": [{"type": "text", "text": SNAP}]})
assert not os.path.exists(ATT)                                   # it is working again
attention("stop", {})
# the GUI (and later a phone or Discord view) reads it from the running sessions
holder = subprocess.Popen([sys.executable, "-c", f"""
import fcntl, json, time
f = open({os.path.join(config.LOCAL_DIR, 'assist_slot3.lock')!r}, "a+"); fcntl.flock(f, fcntl.LOCK_EX); f.seek(0); f.truncate()
f.write(json.dumps({{"slot": 3, "row": 7, "tab": "other", "run": {d!r}}})); f.flush(); print("held", flush=True); time.sleep(30)"""],
    stdout=subprocess.PIPE, text=True)
assert holder.stdout.readline().strip() == "held"
running = assist.sessions()
assert [(x["slot"], x["attention"]["kind"]) for x in running] == [(3, "waiting")], running
holder.kill()
holder.wait()
os.environ.pop(assist.STATE_ENV)
assert attention("stop", {}) == ""                               # outside a session: nothing, no error

def blocked(state, tool, inp, needle=""):  # (the name was used for the NUworks-side check further up)
    verdict, why = assist.decide(state, tool, inp)
    assert verdict == "deny" and needle.lower() in why.lower(), (tool, inp, verdict, why)


# captchas (2026-10-11): an element named like one, or anything inside a captcha frame, is the user's: never clicked,
# typed in or hovered; the rest of the page works as before
CAPTCHA_SNAP = f"""### Page state
- Page URL: https://{H}/careers/job/X_1/apply
- Page Snapshot:
```yaml
- generic [ref=c1]:
  - textbox "First Name*" [ref=c2]
  - checkbox "I'm not a robot" [ref=c3]
  - iframe "reCAPTCHA" [ref=c4]:
    - checkbox "I'm not a robot" [ref=c5]
    - button "Get an audio challenge" [ref=c6]
    - generic [ref=c7]:
      - textbox "Type the text" [active] [ref=c8]
  - textbox "Last Name*" [ref=c9]
  - iframe "Widget containing checkbox for hCaptcha security challenge" [ref=c10]:
    - checkbox "hCaptcha checkbox" [ref=c11]
  - button "Verify you are human" [ref=c12]
  - button "Next" [ref=c13]
  - iframe "Ad" [ref=c14]:
    - link "Learn more" [ref=c15]
  - button "Submit Application" [ref=c16]
```"""
cp = {**fresh(), "refs": assist.parse_refs(CAPTCHA_SNAP)}
marked = sorted(r for r, v in cp["refs"].items() if v.get("captcha"))
assert marked == ["c10", "c11", "c12", "c3", "c4", "c5", "c6", "c7", "c8"], marked  # frames + everything under them
assert "captcha" not in cp["refs"]["c2"] and "captcha" not in cp["refs"]["c9"]     # same / lower indent after a frame: not marked
assert "captcha" not in cp["refs"]["c13"] and "captcha" not in cp["refs"]["c15"] and "captcha" not in cp["refs"]["c1"]
assert cp["refs"]["c3"] == {"role": "checkbox", "name": "I'm not a robot", "captcha": True}
for name in ("Verify you are human", "Verify that you're a human", "Are you a robot?", "Human verification", "Press & hold",
             "Cloudflare Turnstile", "hCaptcha checkbox", "Security check", "Arkose Labs challenge"):
    assert assist.parse_refs(f'- checkbox "{name}" [ref=z1]')["z1"].get("captcha"), name
for name in ("First Name", "Verify your email address", "Robotics experience", "Humane Society volunteer"):
    assert "captcha" not in assist.parse_refs(f'- textbox "{name}" [ref=z1]')["z1"], name
assert assist.parse_refs('- iframe [ref=f1]:\n  - button "Go" [ref=f2]')["f2"] == {"role": "button", "name": "Go"}  # no name: not one
# the guard: any action on a captcha ref is refused, whatever the tool
for ref in ("c3", "c5", "c6", "c8", "c11", "c12"):
    for tool in ("browser_click", "browser_hover", "browser_type", "browser_select_option"):
        blocked(cp, B + tool, {"ref": ref, "text": "x", "values": ["x"]}, "captcha")
    blocked(cp, B + "browser_click", {"target": ref}, "captcha")
blocked(cp, B + "browser_fill_form", {"fields": [{"ref": "c2", "type": "textbox", "value": "Jane"},
                                                 {"ref": "c5", "type": "checkbox", "value": "true"}]}, "captcha")
blocked(cp, B + "browser_press_key", {"key": "Space"}, "captcha")                       # the focused element is in the captcha
assert "captcha" in assist.decide(cp, B + "browser_click", {"ref": "c12"})[1].lower() and "wait" in assist.CAPTCHA_DENY
# the rest of the page behaves as before
ok(cp, B + "browser_type", {"ref": "c2", "text": "Jane"})
ok(cp, B + "browser_type", {"ref": "c9", "text": "Doe"})
ok(cp, B + "browser_click", {"ref": "c13"})
ok(cp, B + "browser_click", {"ref": "c15"})
ok(cp, B + "browser_fill_form", {"fields": [{"ref": "c2", "type": "textbox", "value": "Jane"}]})
asks(cp, B + "browser_click", {"ref": "c16"}, "submit")
nofocus = {**cp, "refs": {r: {k: v for k, v in info.items() if k != "active"} for r, info in cp["refs"].items()}}
asks(nofocus, B + "browser_press_key", {"key": "Enter"}, "enter")                    # no captcha focused: Enter asks as before
ok(nofocus, B + "browser_press_key", {"key": "Tab"})
focus_normal = {**cp, "refs": {**cp["refs"], "c8": {k: v for k, v in cp["refs"]["c8"].items() if k != "active"},
                               "c2": {**cp["refs"]["c2"], "active": True}}}
ok(focus_normal, B + "browser_press_key", {"key": "Tab"})                             # focus on a normal box: fine
ok(cp, B + "browser_snapshot", {})
ok(cp, B + "browser_navigate", {"url": "https://other.example/"})

# on the homelab the "needs you" news goes to Discord (no desktop there): a link to the review page, and how to reach the
# terminal; the agent's whole message is kept for that page
import nuauto.daily as daily  # noqa: E402
from nuauto import review  # noqa: E402
sent, real_discord, real_server, real_page = [], daily.discord, config.IS_SERVER, review.page_url
daily.discord = lambda text: sent.append(text)
review.page_url = lambda slot=None: "http://100.64.0.1:8766" + (f"/s/{slot}" if slot else "/")
shown.clear()
PAGE1 = "http://100.64.0.1:8766/s/1"
try:
    config.IS_SERVER = True
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "state.json"), "w") as f:
        json.dump({**fresh(), "company": "Acme", "where": "Other jobs row 7", "password_sites": [], "tmux": "nuauto1",
                   "slot": 1}, f)
    os.environ[assist.STATE_ENV] = d
    whole = "There is a captcha on this page.\nPlease solve it.\n<b>not html</b>"
    assert attention("stop", {"last_assistant_message": whole}) == ""
    assert len(sent) == 1 and shown == [], (sent, shown)
    assert f"Answer it: {PAGE1}" in sent[0] and "Its terminal: ssh homelab -t tmux attach -t nuauto1" in sent[0]
    assert "Acme" in sent[0] and "Other jobs row 7" in sent[0] and "vnc.html" not in sent[0]
    assert "There is a captcha on this page." in sent[0] and "Please solve it" not in sent[0]
    assert open(os.path.join(d, assist.MESSAGE)).read() == whole        # the whole message, for the review page
    assert oct(os.stat(os.path.join(d, assist.MESSAGE)).st_mode & 0o777) == "0o600"
    assert json.load(open(os.path.join(d, assist.ATTENTION)))["kind"] == "waiting"
    attention("stop", {})
    assert len(sent) == 1                                                # the same news within a minute: once
    attention("notification", {"message": "Claude needs your permission to use mcp__browser__browser_click",
                               "notification_type": "permission_prompt"})
    assert len(sent) == 2 and PAGE1 in sent[1] and "tmux attach -t nuauto1" in sent[1]
    assert open(os.path.join(d, assist.MESSAGE)).read() == whole        # an approval notice leaves the message alone
    # slot 2's session links to its own page
    d5 = tempfile.mkdtemp()
    with open(os.path.join(d5, "state.json"), "w") as f:
        json.dump({**fresh(), "company": "Eps", "where": "row 9", "password_sites": [], "tmux": "nuauto2", "slot": 2}, f)
    os.environ[assist.STATE_ENV] = d5
    attention("stop", {"last_assistant_message": "q?"})
    assert "http://100.64.0.1:8766/s/2" in sent[2] and "attach -t nuauto2" in sent[2], sent[2]
    # no tmux in the state: still sent, without the terminal line
    d2 = tempfile.mkdtemp()
    with open(os.path.join(d2, "state.json"), "w") as f:
        json.dump({**fresh(), "company": "Beta", "where": "row 3", "password_sites": []}, f)
    os.environ[assist.STATE_ENV] = d2
    attention("stop", {})
    assert len(sent) == 4 and "tmux" not in sent[3] and PAGE1 in sent[3] and "Beta" in sent[3]
    # on the laptop: the desktop notification, nothing to Discord, no message file
    config.IS_SERVER = False
    d4 = tempfile.mkdtemp()
    with open(os.path.join(d4, "state.json"), "w") as f:
        json.dump({**fresh(), "company": "Delta", "where": "row 5", "password_sites": [], "tmux": "nuauto1"}, f)
    os.environ[assist.STATE_ENV] = d4
    attention("stop", {"last_assistant_message": "hello"})
    assert len(sent) == 4 and len(shown) == 1 and "tmux" not in shown[0][1] and "8766" not in shown[0][1]
    assert not os.path.exists(os.path.join(d4, assist.MESSAGE))
finally:
    daily.discord, config.IS_SERVER, review.page_url = real_discord, real_server, real_page
    os.environ.pop(assist.STATE_ENV, None)
title, body = assist.attention_text("waiting", "row 1", "Acme", "hi", {"tmux": "nuauto2", "review_url": PAGE1})
assert title == "NUauto: Acme" and "Its terminal: ssh homelab -t tmux attach -t nuauto2" in body
assert body.splitlines()[0] == "The assistant on the homelab is waiting for you (row 1): hi" and f"Answer it: {PAGE1}" in body
assert "ssh" not in assist.attention_text("waiting", "row 1", "Acme", "", {"review_url": PAGE1})[1]
assert assist.attention_text("waiting", "row 1", "Acme", "hi")[1] == "The assistant is waiting for you in its terminal window (row 1): hi"

# the browser's settings in mcp.json: the screen it draws on, and which browser
sd = tempfile.mkdtemp()
real_browser = config.ASSIST_BROWSER


def mcp_of(**kw):
    assist.session(sd, ANSWER_CMD, [tmp], os.path.join(tmp, "profile"), "rules", "row 1", **kw)
    return json.load(open(os.path.join(sd, "mcp.json")))["mcpServers"][assist.SERVER]


try:
    config.ASSIST_BROWSER = "firefox"
    srv = mcp_of(display=":41")
    assert srv["env"] == {"DISPLAY": ":41"} and srv["args"][srv["args"].index("--browser") + 1] == "firefox"
    assert srv["args"][srv["args"].index("--user-data-dir") + 1] == os.path.join(tmp, "profile") and "--output-dir" in srv["args"]
    assert "env" not in mcp_of()                                         # the laptop: its own screen
    config.ASSIST_BROWSER = ""
    srv = mcp_of(display=":43")
    assert "--browser" not in srv["args"] and srv["env"] == {"DISPLAY": ":43"}
    assert "--browser" not in mcp_of()["args"]
finally:
    config.ASSIST_BROWSER = real_browser

# the homelab's agent never opens your own machines or network (the review page, the browser view, the LAN).
# No real DNS here: names "resolve" through a fake table (unknown names: no answer, like an offline machine).
import socket  # noqa: E402
real_getaddrinfo = socket.getaddrinfo
FAKE_DNS = {"127.0.0.1.nip.io": "127.0.0.1", "rebind.example.com": "100.98.1.2", "v6.example.com": "::1",
            "jobs.example.com": "93.184.216.34"}


def fake_getaddrinfo(host, *a, **k):
    if host not in FAKE_DNS:
        raise socket.gaierror("no such name (test)")
    ip = FAKE_DNS[host]
    return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]


socket.getaddrinfo = fake_getaddrinfo
for u in ("http://localhost./", "http://x.ts.net./", "http://x.local./", "http://2130706433/", "http://0x7f.0.0.1/",
          "http://0177.0.0.1/", "http://127.1/", "http://homelab:8766/", "http://[::ffff:127.0.0.1]/",
          "http://127.0.0.1.nip.io/", "https://rebind.example.com/", "http://v6.example.com/", "http://224.0.0.1/"):
    assert assist.own_network(u), u                                  # shorthands, short names, names resolving inward
assert not assist.own_network("https://jobs.example.com/apply") and not assist.own_network("https://nowhere.example/")
assert not assist.own_network("https://rebind.example.com/", resolve=False)
for u in ("http://localhost:8766/", "http://127.0.0.1/", "http://127.9.9.9:80", "http://[::1]/", "http://192.168.1.5/x", "http://10.0.0.2",
          "http://172.16.4.4", "http://169.254.169.254/latest", "http://0.0.0.0:6081", "http://100.64.0.1:6081/vnc.html",
          "http://100.127.255.254/", "http://box.ts.net/", "http://printer.local/", "http://nas.lan/", "http://x.localhost/",
          "http://[fd7a:115c:a1e0::1]/", "http://user@127.0.0.1/", "HTTP://LOCALHOST/"):
    assert assist.own_network(u), u
for u in ("http://127.0.0.1\\@example.com/", "http://100.64.0.1:8766\\@example.com/", "http://127.0.0.1\\", "http://loc\talhost/",
          "http://127.0.0.1\n/", "http://[::1/", "http://[bad/", "", None, "   "):
    assert assist.own_network(u), repr(u)                            # as a browser reads it; unreadable = own network
assert not assist.own_network("https://jobs.example.com\\@127.0.0.1/")   # a browser's host there is jobs.example.com
for u in ("https://acme.wd5.myworkdayjobs.com/en-US/x", "http://8.8.8.8/", "http://100.128.0.1/", "http://100.63.255.255/",
          "https://localhost.evil.com/", "https://notts.net/", "https://example.com/localhost", "http://192.169.0.1/"):
    assert not assist.own_network(u), u
OWN = ["http://100.64.0.1:6081/vnc.html", "http://localhost:8765/", "http://192.168.1.5/"]
server_before = config.IS_SERVER
try:
    config.IS_SERVER = True
    for u in OWN:
        blocked(s, B + "browser_navigate", {"url": u}, "own machines")
        blocked(s, B + "browser_tabs", {"action": "new", "url": u}, "own machines")
        blocked(s, "WebFetch", {"url": u}, "own machines")
    ok(s, B + "browser_navigate", {"url": "https://jobs.lever.co/acme/1"})
    ok(s, B + "browser_tabs", {"action": "new", "url": "https://jobs.lever.co/acme/1"})
    ok(s, B + "browser_tabs", {"action": "list"})
    ok(s, "WebFetch", {"url": "https://example.com/about"})
    ok(s, "WebSearch", {"query": "acme robotics"})
    blocked(s, B + "browser_navigate", {"url": "file:///etc/passwd"}, "web pages")
    blocked(s, B + "browser_navigate", {"url": "http://100.64.0.1:8766\\@example.com/"}, "own machines")
    # a link or a redirect already led the browser to your own network: nothing is clicked or typed there
    led = {**fresh(), "current_url": "http://100.64.0.1:8766/s/1"}
    for tool, inp in (("browser_click", {"ref": "e61"}), ("browser_type", {"ref": "e12", "text": "x"}), ("browser_hover", {"ref": "e61"}),
                      ("browser_fill_form", {"fields": [{"ref": "e12", "type": "textbox", "value": "x"}]}),
                      ("browser_select_option", {"ref": "e20", "values": ["Maine"]}), ("browser_press_key", {"key": "Tab"}),
                      ("browser_handle_dialog", {"accept": True})):
        blocked(led, B + tool, inp, "Go back")
    ok(led, B + "browser_snapshot", {})
    ok(led, B + "browser_navigate_back", {})
    ok(led, B + "browser_navigate", {"url": "https://jobs.lever.co/acme/1"})            # the way out
    for u in ("http://192.168.0.9/x", "http://localhost:8765/", "http://homelab:8766/", "http://127.0.0.1\\@example.com/"):
        blocked({**fresh(), "current_url": u}, B + "browser_click", {"ref": "e61"}, "own machines")
    ok({**fresh(), "current_url": "https://jobs.example.com/apply"}, B + "browser_click", {"ref": "e61"})
    config.IS_SERVER = False                                              # the laptop: unchanged
    ok(led, B + "browser_click", {"ref": "e61"})
    for u in OWN:
        ok(s, B + "browser_navigate", {"url": u})
        ok(s, B + "browser_tabs", {"action": "new", "url": u})
        ok(s, "WebFetch", {"url": u})
finally:
    config.IS_SERVER = server_before
    socket.getaddrinfo = real_getaddrinfo

# Submit approvals on the homelab: the guard opens the question on the review page and waits for the phone's answer
import threading  # noqa: E402
import time  # noqa: E402
from nuauto import review  # noqa: E402


def guard(d, tool, inp):
    """The real PreToolUse hook on the homelab: (stdout, stderr, exit code)."""
    os.environ[assist.STATE_ENV] = d
    out, err, real_in = sys.stdout, sys.stderr, sys.stdin
    sys.stdout, sys.stderr, sys.stdin = io.StringIO(), io.StringIO(), io.StringIO(json.dumps({"tool_name": tool, "tool_input": inp}))
    code = 0
    try:
        assist.hook("pre")
    except SystemExit as e:
        code = e.code
    finally:
        o, e_ = sys.stdout.getvalue(), sys.stderr.getvalue()
        sys.stdout, sys.stderr, sys.stdin = out, err, real_in
        os.environ.pop(assist.STATE_ENV, None)
    return o, e_, code


def new_run(**extra):
    run = tempfile.mkdtemp()
    with open(os.path.join(run, "state.json"), "w") as f:
        json.dump({**fresh(), "company": "Acme", "where": "row 3", "password_sites": [], "slot": 1, **extra}, f)
    return run


def answer_when_asked(run, decision, note="", by="phone", stale=False):
    """Plays the review page: once the question is there, writes the answer (like review.answer_approval)."""
    def play():
        path = os.path.join(run, assist.APPROVAL)
        for _ in range(100):
            a = assist.read_json(path)
            if a:
                with assist.approval_lock(run):
                    assist.write_private(os.path.join(run, assist.DECISION), {"id": a["id"], "decision": decision, "by": by,
                                                                           "note": note, "at": time.time()})
                return
            time.sleep(0.05)
    t = threading.Thread(target=play)
    t.start()
    return t


def has(run, name):
    return os.path.exists(os.path.join(run, name))


def log_of(run):
    return open(os.path.join(run, "actions.log")).read()


sent, real_discord, real_wait, real_page = [], daily.discord, assist.WAIT, review.page_url
daily.discord = lambda text: sent.append(text)
review.page_url = lambda slot=None: "http://100.64.0.1:8766" + (f"/s/{slot}" if slot else "/")
config.IS_SERVER = True
assist.WAIT = 1
SUBMIT = {"ref": "e60"}   # "Submit"
try:
    # the laptop is unchanged: an ask goes to Claude Code's own terminal question
    config.IS_SERVER = False
    out, err, code = guard(new_run(), B + "browser_click", SUBMIT)
    assert code == 0 and json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "ask" and sent == []
    config.IS_SERVER = True

    # (a) approved from the phone: allowed; the question and the answer are gone; the log says who
    run = new_run()
    t = answer_when_asked(run, "approve", by="phone")
    out, err, code = guard(run, B + "browser_click", SUBMIT)
    t.join()
    assert code == 0 and json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "allow", (out, err, code)
    assert "approved on the review page by phone" in out
    assert not has(run, assist.APPROVAL) and not has(run, assist.DECISION) and not has(run, assist.ATTENTION)
    assert "APPROVED on the review page by phone" in log_of(run) and "the review page" in log_of(run)
    assert len(sent) == 1 and "Ready to submit (row 3)" in sent[0] and "click 'Submit'" in sent[0] \
        and "http://100.64.0.1:8766/s/1" in sent[0], sent
    assert not has(run, assist.GRANT)

    # (b) declined with a note: blocked, the agent is told the note
    run = new_run()
    t = answer_when_asked(run, "decline", note="wrong phone number")
    out, err, code = guard(run, B + "browser_click", SUBMIT)
    t.join()
    assert code == 2 and out == "" and "declined" in err and "wrong phone number" in err and "Don't submit" in err, (out, err, code)
    assert not has(run, assist.APPROVAL) and not has(run, assist.DECISION)
    assert "DECLINED on the review page by phone" in log_of(run) and "wrong phone number" in log_of(run)

    # (c) nobody answers: blocked; the question stays on the page, no longer waiting
    run = new_run()
    started = time.time()
    out, err, code = guard(run, B + "browser_click", SUBMIT)
    assert code == 2 and "hasn't approved" in err and "End your turn" in err and 0.5 < time.time() - started < 10, (err, code)
    a = assist.read_json(os.path.join(run, assist.APPROVAL))
    assert a["waiting"] is False and a["what"] == "click 'Submit'" and a["url"] == fresh()["current_url"] and a["id"]
    assert json.load(open(os.path.join(run, assist.ATTENTION)))["kind"] == "approve"
    assert "NOT ANSWERED" in log_of(run) and not has(run, assist.GRANT)
    # the phone approves late: a pass for that one action, and the agent is told to do it
    typed = []
    real_type = review.type_into
    review.type_into = lambda sess, text: typed.append((sess["state"]["tmux"], text))
    try:
        stale = {"run": run, "state": {"tmux": "nuauto1"}}
        assert "gone" in review.answer_approval(stale, "some-other-id", "approve", "phone")   # not this question
        assert not has(run, assist.GRANT) and typed == []
        assert review.answer_approval(stale, a["id"], "approve", "phone") == "Approved."
    finally:
        review.type_into = real_type
    g = assist.read_json(os.path.join(run, assist.GRANT))
    assert g["what"] == a["what"] and g["url"] == a["url"] and g["by"] == "phone" and g["until"] > time.time() + 500
    assert oct(os.stat(os.path.join(run, assist.GRANT)).st_mode & 0o777) == "0o600"
    assert not has(run, assist.APPROVAL) and len(typed) == 1 and typed[0][0] == "nuauto1"
    assert "do exactly that action now" in typed[0][1] and "click 'Submit'" in typed[0][1]
    assert "REVIEW PAGE approve by phone" in log_of(run)
    # a different button, or another page, does not use it
    assert g["fp"] == a["fp"] == assist.typed_print({}) and a["pid"] == os.getpid()
    mine = {"approvals": [a["id"]]}                                        # this run's own questions
    other_what = {"what": "click 'Submit Application'", "url": a["url"], "fp": a["fp"]}
    other_page = {"what": a["what"], "url": a["url"] + "/other", "fp": a["fp"]}
    assert assist.take_grant(run, other_what, mine) is None and assist.take_grant(run, other_page, mine) is None
    assert has(run, assist.GRANT)                                          # left for the right action
    out, err, code = guard(run, B + "browser_click", {"ref": "e90"})       # "Submit Application": asks afresh
    assert code == 2 and "hasn't approved" in err and not has(run, assist.GRANT)
    assert assist.read_json(os.path.join(run, assist.APPROVAL))["what"] == "click 'Submit Application'"
    # the same click again, with the pass: through, once; the pass is used up
    run2 = new_run(approvals=[g["id"]])
    assist.write_private(os.path.join(run2, assist.GRANT), {**g})
    sent.clear()
    out, err, code = guard(run2, B + "browser_click", SUBMIT)
    assert code == 0 and out == "" and err == "", (out, err)               # allowed like any other action (no output)
    assert not has(run2, assist.GRANT) and not has(run2, assist.APPROVAL) and sent == []
    assert "approved on the review page by phone" in log_of(run2)
    out, err, code = guard(run2, B + "browser_click", SUBMIT)              # used up: asks (and waits) again
    assert code == 2 and has(run2, assist.APPROVAL) and len(sent) == 1
    # another page: the state's page changed since the Approve
    run3 = new_run(approvals=[g["id"]])
    assist.write_private(os.path.join(run3, assist.GRANT), {**g, "url": "https://elsewhere.example/apply"})
    assert guard(run3, B + "browser_click", SUBMIT)[2] == 2
    # an expired pass does not work, and is removed
    run4 = new_run(approvals=[g["id"]])
    assist.write_private(os.path.join(run4, assist.GRANT), {**g, "until": time.time() - 1})
    assert assist.take_grant(run4, {"what": g["what"], "url": g["url"], "fp": g["fp"]}, {"approvals": [g["id"]]}) is None
    assert not has(run4, assist.GRANT)
    assist.write_private(os.path.join(run4, assist.GRANT), {**g, "until": time.time() - 1})
    assert guard(run4, B + "browser_click", SUBMIT)[2] == 2 and not has(run4, assist.GRANT)

    # a stale answer (an older question's id) never counts, either by the guard or by a new question
    run5 = new_run()
    assist.write_private(os.path.join(run5, assist.DECISION), {"id": "older-question", "decision": "approve", "by": "phone"})
    a5 = {"what": "click 'Submit'", "url": "u", "id": "newer", "why": "w", "since": time.time(), "waiting": True}
    assist.write_private(os.path.join(run5, assist.APPROVAL), a5)
    out, sys_out = io.StringIO(), sys.stderr
    sys.stderr = io.StringIO()
    try:
        assist.remote_decision(run5, a5)
        raise AssertionError("a stale answer let the action through")
    except SystemExit as e:
        assert e.code == 2 and "hasn't approved" in sys.stderr.getvalue()
    finally:
        sys.stderr = sys_out
    assert assist.read_json(os.path.join(run5, assist.DECISION))["id"] == "older-question"
    run6 = new_run()
    assist.write_private(os.path.join(run6, assist.DECISION), {"id": "older-question", "decision": "approve", "by": "phone"})
    out, err, code = guard(run6, B + "browser_click", SUBMIT)              # a new question clears it first
    assert code == 2 and "hasn't approved" in err and not has(run6, assist.DECISION)
    # an answer for the right question while the guard waits is used even if a stale file came first
    run7 = new_run()
    t = answer_when_asked(run7, "approve")
    assert guard(run7, B + "browser_click", SUBMIT)[2] == 0
    t.join()
    # Enter and typing with submit ask the same way
    run8 = new_run()
    out, err, code = guard(run8, B + "browser_press_key", {"key": "Enter"})
    assert code == 2 and assist.read_json(os.path.join(run8, assist.APPROVAL))["what"] == "press Enter"
    # a denied action never opens a question
    run9 = new_run()
    out, err, code = guard(run9, B + "browser_evaluate", {})
    assert code == 2 and not has(run9, assist.APPROVAL)

    # a pass is the guard's own: a made-up id, or other typed values since, never counts
    run10 = new_run()
    GRANT10 = os.path.join(run10, assist.GRANT)
    tgt = {"what": "click 'Submit'", "url": fresh()["current_url"], "fp": assist.typed_print({})}
    planted = {"what": tgt["what"], "url": tgt["url"], "id": "made-up", "fp": tgt["fp"], "by": "me", "until": time.time() + 500}
    assist.write_private(GRANT10, planted)
    assert assist.take_grant(run10, tgt, {"approvals": ["real"]}) is None and not os.path.exists(GRANT10)   # ignored and removed
    assist.write_private(GRANT10, planted)
    assert assist.take_grant(run10, tgt, {}) is None and not os.path.exists(GRANT10)                         # no questions asked at all
    assist.write_private(GRANT10, planted)
    assert guard(run10, B + "browser_click", SUBMIT)[2] == 2 and not os.path.exists(GRANT10)                 # asked afresh instead
    good = {**planted, "id": "q1"}
    typed_state = {"typed": {"First Name*": "Jan"}}
    fp2 = assist.typed_print(typed_state)
    assert fp2 != tgt["fp"] and assist.typed_print({"typed": {}}) == tgt["fp"] == assist.typed_print({})
    assist.write_private(GRANT10, good)
    assert assist.take_grant(run10, {**tgt, "fp": fp2}, {"approvals": ["q1"]}) is None and os.path.exists(GRANT10)   # typed since
    assert assist.take_grant(run10, tgt, {"approvals": ["q1"]})["by"] == "me" and not os.path.exists(GRANT10)   # the right one: once
    assert assist.take_grant(run10, tgt, {"approvals": ["q1"]}) is None
    run11 = new_run(approvals=["q1"], typed={"First Name*": "Jan"})                                             # through the hook
    assist.write_private(os.path.join(run11, assist.GRANT), good)
    out, err, code = guard(run11, B + "browser_click", SUBMIT)
    assert code == 2 and "hasn't approved" in err and assist.read_json(os.path.join(run11, assist.APPROVAL))["fp"] == fp2
    # the guard's questions this run: recorded (the last 20), with the hook's pid
    st25, rd = fresh(), new_run()
    ids = [assist.open_approval(rd, st25, tgt, "why") for _ in range(25)]
    assert len(st25["approvals"]) == 20 and st25["approvals"] == [x["id"] for x in ids][-20:] and ids[-1]["pid"] == os.getpid()
    assert ids[0]["id"] not in st25["approvals"]
    # what you approve says which box a key or Enter goes to
    wst = {**fresh(), "refs": assist.parse_refs(WD_FORM), "typed": {}}
    assert assist.approval_target(wst, B + "browser_press_key", {"key": "Enter"})["what"] == "press Enter in 'How Did You Hear About Us?*'"
    assert assist.approval_target(fresh(), B + "browser_press_key", {"key": "Enter"})["what"] == "press Enter"
    t_ = assist.approval_target(wst, B + "browser_type", {"ref": "w1", "text": "x", "submit": True})
    assert t_["what"] == "type into 'First Name*' and press Enter" and t_["url"] == wst["current_url"] and t_["fp"] == tgt["fp"]
    assert assist.approval_target(wst, B + "browser_type", {"ref": "zz", "text": "x"})["what"] == "type into 'a box' and press Enter"
    assert assist.approval_target({**wst, "typed": {"a": "b"}}, B + "browser_press_key", {"key": "Enter"})["fp"] != tgt["fp"]

    # the page changes after a question: the open question and any pass go (not one the guard still waits on)
    for tool, inp, changes in ((B + "browser_type", {"ref": "e12", "text": "Jane"}, True),
                               (B + "browser_fill_form", {"fields": [{"ref": "e12", "type": "textbox", "value": "J"}]}, True),
                               (B + "browser_select_option", {"ref": "e20", "values": ["Maine"]}, True),
                               (B + "browser_navigate", {"url": "https://jobs.lever.co/acme/1"}, True),
                               (B + "browser_navigate_back", {}, True),
                               (B + "browser_tabs", {"action": "new", "url": "https://jobs.lever.co/acme/1"}, True),
                               (B + "browser_tabs", {"action": "select", "index": 0}, True),
                               (B + "browser_tabs", {"action": "close"}, True),
                               (B + "browser_tabs", {"action": "list"}, False), (B + "browser_snapshot", {}, False),
                               (B + "browser_click", {"ref": "e61"}, False), (B + "browser_press_key", {"key": "Tab"}, False),
                               (B + "browser_hover", {"ref": "e61"}, False), (B + "browser_wait_for", {"time": 1}, False)):
        assert assist.changes_page(tool, inp) is changes, (tool, inp)

    def staged(waiting):
        r_ = new_run(approvals=["q1"])
        assist.write_private(os.path.join(r_, assist.APPROVAL), {"id": "q1", "what": "click 'Submit'", "url": tgt["url"],
                                                                 "waiting": waiting, "since": time.time(), "fp": tgt["fp"]})
        assist.write_private(os.path.join(r_, assist.SHOT), b"png")
        assist.write_private(os.path.join(r_, assist.GRANT), good)
        return r_

    for tool, inp, changes in ((B + "browser_type", {"ref": "e12", "text": "Jane"}, True),
                               (B + "browser_fill_form", {"fields": [{"ref": "e12", "type": "textbox", "value": "J"}]}, True),
                               (B + "browser_select_option", {"ref": "e20", "values": ["Maine"]}, True),
                               (B + "browser_navigate", {"url": "https://jobs.lever.co/acme/1"}, True),
                               (B + "browser_navigate_back", {}, True),
                               (B + "browser_tabs", {"action": "new", "url": "https://jobs.lever.co/acme/1"}, True),
                               (B + "browser_tabs", {"action": "select", "index": 0}, True),
                               (B + "browser_tabs", {"action": "close"}, True),
                               (B + "browser_tabs", {"action": "list"}, False), (B + "browser_snapshot", {}, False),
                               (B + "browser_click", {"ref": "e61"}, False), (B + "browser_press_key", {"key": "Tab"}, False)):
        rw = staged(False)
        out, err, code = guard(rw, tool, inp)
        assert code == 0, (tool, err)
        gone = not has(rw, assist.APPROVAL) and not has(rw, assist.SHOT) and not has(rw, assist.GRANT)
        assert gone is changes, (tool, inp, os.listdir(rw))
        assert ("withdrawn" in log_of(rw)) is changes
    rw = staged(True)                                                    # the hook that asked is still waiting: its question stays
    assert guard(rw, B + "browser_type", {"ref": "e12", "text": "Jane"})[2] == 0
    assert assist.read_json(os.path.join(rw, assist.APPROVAL))["waiting"] is True and not has(rw, assist.GRANT)
    rw = staged(False)                                                   # an action that is denied changes nothing
    assert guard(rw, B + "browser_navigate", {"url": "http://localhost:8766/"})[2] == 2 and has(rw, assist.APPROVAL) and has(rw, assist.GRANT)
    config.IS_SERVER = False                                             # the laptop has none of this
    rw = staged(False)
    assert guard(rw, B + "browser_type", {"ref": "e12", "text": "Jane"})[2] == 0 and has(rw, assist.APPROVAL) and has(rw, assist.GRANT)
    config.IS_SERVER = True

    # approved, but something was typed meanwhile (another of its calls): not what you were shown
    run12 = new_run()

    def type_then_approve():
        for _ in range(100):
            a_ = assist.read_json(os.path.join(run12, assist.APPROVAL))
            if a_:
                st_ = assist.read_json(os.path.join(run12, "state.json"))
                st_["typed"] = {"First Name*": "Mallory"}
                assist.write_private(os.path.join(run12, "state.json"), st_)
                with assist.approval_lock(run12):
                    assist.write_private(os.path.join(run12, assist.DECISION), {"id": a_["id"], "decision": "approve", "by": "phone"})
                return
            time.sleep(0.05)
    t = threading.Thread(target=type_then_approve)
    t.start()
    out, err, code = guard(run12, B + "browser_click", SUBMIT)
    t.join()
    assert code == 2 and out == "" and "page changed" in err, (out, err, code)
    assert "APPROVED by phone, but the page changed" in log_of(run12) and "APPROVED on the review page" not in log_of(run12)
    assert not has(run12, assist.APPROVAL) and not has(run12, assist.DECISION)

    # the hook that asked is gone: Approve then gives a pass (and types the message), never an answer nobody reads
    def child(argv):
        return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", *argv])
    live_hook, other_proc = child(["hook", "pre"]), child(["hook"])
    gone_proc = subprocess.Popen([sys.executable, "-c", "pass"])
    gone_proc.wait()
    try:
        time.sleep(0.2)
        assert review.hook_alive(live_hook.pid) is True and review.hook_alive(None) is True and review.hook_alive(0) is True
        if os.path.isdir("/proc/self"):  # Linux (the homelab) can tell the hook from another process; macOS only sees it runs
            assert review.hook_alive(os.getpid()) is False and review.hook_alive(other_proc.pid) is False
        assert review.hook_alive(gone_proc.pid) is False
        typed2, real_type2 = [], review.type_into
        review.type_into = lambda sess, text_: typed2.append(text_)
        try:
            cases = ((gone_proc.pid, True), (os.getpid(), True), (live_hook.pid, False), (None, False))
            if not os.path.isdir("/proc/self"):  # macOS can't tell another live process from the hook: that case is Linux's
                cases = tuple(c for c in cases if c[0] != os.getpid())
            for pid_, passes in cases:
                rr = new_run()
                aa = {"id": "q9", "what": "click 'Submit'", "url": tgt["url"], "waiting": True, "since": time.time(), "fp": tgt["fp"],
                      "why": "w", **({"pid": pid_} if pid_ is not None else {})}
                assist.write_private(os.path.join(rr, assist.APPROVAL), aa)
                typed2.clear()
                assert review.answer_approval({"run": rr, "state": {"tmux": "x"}}, "q9", "approve", "phone") == "Approved."
                assert has(rr, assist.GRANT) is passes and has(rr, assist.DECISION) is not passes, (pid_, os.listdir(rr))
                assert bool(typed2) is passes
                if passes:
                    assert assist.read_json(os.path.join(rr, assist.GRANT))["fp"] == tgt["fp"] and not has(rr, assist.APPROVAL)
            rr = new_run()                                               # a dead hook and Decline: told not to
            assist.write_private(os.path.join(rr, assist.APPROVAL), {**aa, "pid": gone_proc.pid})
            typed2.clear()
            assert review.answer_approval({"run": rr, "state": {"tmux": "x"}}, "q9", "decline", "phone", "no") == "Declined."
            assert not has(rr, assist.GRANT) and not has(rr, assist.DECISION) and "declined" in typed2[0]
        finally:
            review.type_into = real_type2
    finally:
        for h_ in (live_hook, other_proc):
            h_.kill()
            h_.wait()
finally:
    daily.discord, assist.WAIT, review.page_url, config.IS_SERVER = real_discord, real_wait, real_page, server_before
    os.environ.pop(assist.STATE_ENV, None)

# a frame with a captcha anywhere in it: all its elements are the captcha's, named or not (2026-10-11)
FRAMES = f"""### Page state
- Page URL: https://{H}/careers/job/X_1/apply
- Page Snapshot:
```yaml
- generic [ref=k1]:
  - textbox "First" [ref=k2]
  - iframe [ref=k3]:
    - generic [ref=k4]:
      - text: Select all images with traffic lights
      - button [ref=k5]
      - button [ref=k6]
      - button "Skip" [ref=k7]
    - link "Privacy" [ref=k8]
  - button "Next" [ref=k9]
  - iframe "Chat widget" [ref=k10]:
    - textbox "Message" [ref=k11]
  - iframe [ref=k12]:
    - button "Verify once there are none left" [ref=k13]
    - button [ref=k14]
  - iframe "Help" [ref=k15]:
    - link "reCAPTCHA terms" [ref=k16]
    - button [ref=k17]
  - button "I am human" [ref=k18]
  - iframe [ref=k20]:
    - text: Slide to verify
    - generic [ref=k21]
  - iframe [ref=k22]:
    - text: Prove you are human
    - button [ref=k23]
  - button "Submit Application" [ref=k19]
```"""
fr = assist.parse_refs(FRAMES)
assert sorted(r for r, v in fr.items() if v.get("captcha")) == sorted(
    ["k3", "k4", "k5", "k6", "k7", "k8", "k12", "k13", "k14", "k15", "k16", "k17", "k18", "k20", "k21", "k22", "k23"])
assert not any(fr[r].get("captcha") for r in ("k1", "k2", "k9", "k10", "k11", "k19"))       # outside a captcha frame
cf = {**fresh(), "refs": fr}
for ref in ("k5", "k6", "k7", "k14", "k17", "k21", "k23"):                                  # even the unnamed ones
    blocked(cf, B + "browser_click", {"ref": ref}, "captcha")
    blocked(cf, B + "browser_hover", {"ref": ref}, "captcha")
ok(cf, B + "browser_click", {"ref": "k9"})
ok(cf, B + "browser_type", {"ref": "k2", "text": "Jane"})
ok(cf, B + "browser_type", {"ref": "k11", "text": "hello"})                                    # an ordinary frame (a chat widget)
asks(cf, B + "browser_click", {"ref": "k19"}, "submit")
for name in ("Select all squares with a bus", "select all the images with crosswalks", "Verify once there are none left",
             "I am human", "Prove you're human", "Human challenge", "Slide to verify", "Slide to complete the puzzle"):
    assert assist.parse_refs(f'- iframe "x" [ref=f1]:\n  - button "{name}" [ref=f2]\n  - button [ref=f3]')["f3"].get("captcha"), name
    assert assist.parse_refs(f'- button "{name}" [ref=f2]')["f2"].get("captcha"), name
for name in ("Select all that apply", "Select a state", "Verify your email", "Slide deck", "I am a student"):
    assert not assist.parse_refs(f'- iframe "x" [ref=f1]:\n  - button "{name}" [ref=f2]')["f2"].get("captcha"), name

# the browser tool's output folder is its own: never the run folder with the guard's files
sd2 = tempfile.mkdtemp()
real_assist_browser = config.ASSIST_BROWSER
try:
    config.ASSIST_BROWSER = "firefox"
    assist.session(sd2, ANSWER_CMD, [tmp], os.path.join(tmp, "profile"), "rules", "row 1", display=":41")
    srv = json.load(open(os.path.join(sd2, "mcp.json")))["mcpServers"][assist.SERVER]
    outdir = srv["args"][srv["args"].index("--output-dir") + 1]
    assert assist.BROWSER_DIR == "browser" and outdir == os.path.join(sd2, "browser") and outdir != sd2 and os.path.isdir(outdir)
    assert oct(os.stat(outdir).st_mode & 0o777) == "0o700"
    assert not os.path.exists(os.path.join(outdir, "state.json"))
finally:
    config.ASSIST_BROWSER = real_assist_browser

# the end of a session on the homelab: the outcome goes to the queue's records (remote.record_outcome); the laptop is as before
from nuauto import remote, screen  # noqa: E402

URLS = [f"https://careers{n}.example.com/job" for n in range(8)]
ws2 = FakeWS([list(sheet.HEADERS)] + [[URLS[n], f"Hornet{n}", "Intern", "Approved", "", ""] for n in range(1, 8)])
sheet.open_other = lambda interactive=True, create=False: ws2


class FakeScreen:
    def __init__(self, slot, d):
        self.slot, self.url, self.display, self.password = slot, "http://100.64.0.1:6081/vnc.html", ":41", "pw12345"

    def start(self):
        return self

    def stop(self):
        pass


outcomes, finished, asked_with = [], [], []
real_bits = (screen.Screen, remote.record_outcome, assist.ask_line, assist.approved_submit, daily.discord, review.page_url,
             os.environ.get("TMUX_PANE"))
screen.Screen = FakeScreen
remote.record_outcome = lambda tab, rown, url, approved, answer: outcomes.append((tab, rown, url, approved, answer))
daily.discord = lambda text: finished.append(text)
review.page_url = lambda slot=None: f"http://100.64.0.1:8766/s/{slot}"


def run_end(n, reply, server=True, approved=False, pane="%7"):
    def fake_ask(prompt, timeout=None):
        asked_with.append(timeout)
        if isinstance(reply, BaseException):
            raise reply
        return reply
    assist.ask_line = fake_ask
    assist.approved_submit = lambda d_: approved
    if pane is None:
        os.environ.pop("TMUX_PANE", None)
    else:
        os.environ["TMUX_PANE"] = pane
    config.IS_SERVER = server
    real_in, sys.stdin = sys.stdin, io.StringIO()
    sys.stdin.isatty = lambda: True
    out_, sys.stdout = sys.stdout, io.StringIO()
    try:
        assist.run(n, None, other=True)
    finally:
        sys.stdin, sys.stdout = real_in, out_
        config.IS_SERVER = server_before
    run_d = sorted(p_ for p_ in os.listdir(config.LOGS_DIR) if p_.endswith(f"assist_other_row{n}"))[-1]
    return os.path.join(config.LOGS_DIR, run_d)


def status_of(n):
    return ws2.values[n - 1][3]


try:
    d1 = run_end(2, "y", approved=True)
    assert outcomes == [("other", 2, URLS[1], True, "y")] and status_of(2) == "Applied"
    assert asked_with == [assist.FINISH_WAIT]                                                # the homelab waits, for a day
    assert json.load(open(os.path.join(d1, "state.json")))["tmux_pane"] == "%7"
    assert json.load(open(os.path.join(d1, assist.ATTENTION)))["kind"] == "finish"          # the review page asks too
    assert any("Hornet1" in m and "Did it go through" in m and "8766/s/" in m for m in finished), finished
    outcomes.clear()
    run_end(3, "n")
    assert outcomes == [("other", 3, URLS[2], False, "n")] and status_of(3) == "Approved"
    d4 = run_end(4, None, approved=True)                                                     # nobody answered in time
    assert outcomes[-1] == ("other", 4, URLS[3], True, None) and status_of(4) == "Approved"
    log4 = open(os.path.join(d4, "actions.log")).read()
    assert "No answer within 24 h" in log4 and "Not submitted" not in log4                  # left as it was, not "no"
    for n, exc in ((5, KeyboardInterrupt()), (6, EOFError())):
        run_end(n, exc, approved=True)
        assert outcomes[-1] == ("other", n, URLS[n - 1], True, None) and status_of(n) == "Approved", (n, outcomes[-1])
    assert len(outcomes) == 4
    # the pane the review page types into: only when it is a real pane id
    for bad in ("x", "%7; kill-server", "", "%"):
        dd = run_end(7, "n", pane=bad)
        assert json.load(open(os.path.join(dd, "state.json")))["tmux_pane"] is None, bad
        ws2.values[6][3] = "Approved"
    dd = run_end(7, "n", pane=None)
    assert json.load(open(os.path.join(dd, "state.json")))["tmux_pane"] is None
    n_before = len(outcomes)
    # the laptop: nothing recorded, Ctrl+C is still "no", no waiting limit
    asked_with.clear()
    for n, reply in ((2, "n"), (3, KeyboardInterrupt()), (4, "n")):
        ws2.values[n - 1][3] = "Approved"
        dd = run_end(n, reply, server=False)
        assert status_of(n) == "Approved" and json.load(open(os.path.join(dd, "state.json")))["tmux_pane"] is None
        assert "Not submitted" in open(os.path.join(dd, "actions.log")).read()
    assert len(outcomes) == n_before                                                        # (nothing recorded on the laptop)
    assert asked_with == [None, None, None]
finally:
    (screen.Screen, remote.record_outcome, assist.ask_line, assist.approved_submit, daily.discord, review.page_url,
     pane_before) = real_bits
    if pane_before is None:
        os.environ.pop("TMUX_PANE", None)
    else:
        os.environ["TMUX_PANE"] = pane_before

# approved_submit reads the run's own log: an approval on the review page, or a pass that was used
ad = tempfile.mkdtemp()
assert assist.approved_submit(ad) is False                                                   # no log
for text, want in (("2026-10-11  ALLOW mcp__browser__browser_click {}\n", False),
                   ("2026-10-11  APPROVED on the review page by phone: click 'Submit'\n", True),
                   ("2026-10-11  ALLOW mcp__browser__browser_click {}  (approved on the review page by phone)\n", True),
                   ("2026-10-11  DECLINED on the review page by phone: click 'Submit'\n", False),
                   ("2026-10-11  APPROVED by phone, but the page changed after the question: blocked (click 'Submit')\n", False),
                   ("2026-10-11  NOT ANSWERED within 9 min: click 'Submit' (still on the review page)\n", False)):
    with open(os.path.join(ad, "actions.log"), "w") as f:
        f.write(text)
    assert assist.approved_submit(ad) is want, text

# accounts.passwords: a login a merge kept a second password for (sync.merge_accounts) hides both from the agent
pw_path = accounts.PATH
accounts.PATH = os.path.join(tmp, "accounts-other-pw.json")
try:
    assert accounts.passwords(["acme.example"]) == []                                       # no file
    with open(accounts.PATH, "w") as f:
        json.dump({"acme.example": {"password": "pw-main-1234", "other_passwords": ["pw-2nd", "pw-third-long"], "jobs": []},
                   "plain.example": {"password": "pw-plain"}, "empty.example": {"password": "", "other_passwords": None},
                   "other.example": {"password": "pw-elsewhere", "other_passwords": ["pw-other-site"]}}, f)
    assert accounts.passwords(["acme.example"]) == ["pw-third-long", "pw-main-1234", "pw-2nd"]    # longest first
    assert accounts.passwords(["https://acme.example/jobs", "plain.example"]) == ["pw-third-long", "pw-main-1234", "pw-plain", "pw-2nd"]
    assert accounts.passwords(["empty.example", "nothing.example"]) == []
    assert "pw-other-site" not in accounts.passwords(["acme.example", "plain.example"])      # only the sites asked about
    hidden, changed = assist.redact({"text": "typed pw-2nd and pw-main-1234 and pw-third-long"}, accounts.passwords(["acme.example"]))
    assert changed and "pw-2nd" not in json.dumps(hidden) and "pw-main-1234" not in json.dumps(hidden) and "pw-third" not in json.dumps(hidden)
finally:
    accounts.PATH = pw_path

print("All assist (agent guard) checks passed.")
