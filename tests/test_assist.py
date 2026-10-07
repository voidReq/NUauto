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
asks(s, B + "browser_type", {"ref": "e12", "text": "Jane", "submit": True}, "submit")
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
assert assist.lookup(E, "Email*", [], "Voluntary Disclosures")["status"] == "ask_every_time"
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

print("All assist (agent guard) checks passed.")
