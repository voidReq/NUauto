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
assert upload and os.path.exists(os.path.join(config.LOGS_DIR, upload[0], "upload", "Resume.pdf"))  # the resume copy
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

print("All assist (agent guard) checks passed.")
