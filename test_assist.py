"""Offline checks for assist.py (the agent's guard and answer bank). Run: python test_assist.py

No browser, no Claude, no sheet. answers.json and the run folder are temp files.
"""
import io
import json
import os
import sys
import tempfile

import config

tmp = tempfile.mkdtemp()
config.ANSWERS_PATH = os.path.join(tmp, "answers.json")

import answers  # noqa: E402
import assist  # noqa: E402

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
ANSWER_CMD = ["/venv/python", "/proj/assist.py"]


def fresh():
    s = {"allowed": [H], "current_url": f"https://{H}/careers/job/X_1", "refs": {}, "issued": {},
         "issued_values": [], "answer_cmd": ANSWER_CMD}
    s["refs"].update(assist.parse_refs(SNAP))
    s["current_url"] = assist.page_url(SNAP)
    return s


def blocked(state, tool, inp, needle=""):
    why = assist.decide(state, tool, inp)
    assert why and needle.lower() in why.lower(), (tool, inp, why)


def ok(state, tool, inp):
    why = assist.decide(state, tool, inp)
    assert why is None, (tool, inp, why)


s = fresh()
assert s["refs"]["e12"] == {"role": "textbox", "name": "First Name*"} and s["refs"]["e60"]["name"] == "Submit"
assert s["current_url"] == f"https://{H}/careers/job/X_1/apply"

# tools: only the listed browser tools; no scripts / uploads / other tools
for t in ("browser_evaluate", "browser_run_code_unsafe", "browser_run_code", "browser_file_upload", "browser_drag", "browser_install"):
    blocked(s, B + t, {}, "not allowed")
blocked(s, "mcp__other__x", {}, "not allowed")
blocked(s, "Write", {}, "not allowed")
ok(s, B + "browser_snapshot", {})

# sites
ok(s, B + "browser_navigate", {"url": f"https://{H}/careers/job/X_1"})
blocked(s, B + "browser_navigate", {"url": "https://evil.example/"}, "blocked")
blocked(s, B + "browser_tabs", {"action": "new", "url": "https://evil.example/"}, "blocked")
off = {**fresh(), "current_url": "https://accounts.google.com/signin"}
blocked(off, B + "browser_click", {"ref": "e61"}, "not the posting")
ok(off, B + "browser_snapshot", {})  # looking is fine

# never Submit, Enter, checkboxes, passwords
blocked(s, B + "browser_click", {"ref": "e60", "element": "Next button"}, "submit")  # the ref decides, not the description
blocked(s, B + "browser_click", {"ref": "e90"}, "submit")
ok(s, B + "browser_click", {"ref": "e61"})       # Next
ok(s, B + "browser_click", {"ref": "e20"})       # open a dropdown
ok(s, B + "browser_click", {"ref": "e80"})       # a link (sign-in pages on other hosts are then locked)
blocked(s, B + "browser_press_key", {"key": "Enter"}, "enter")
ok(s, B + "browser_press_key", {"key": "Tab"})
blocked(s, B + "browser_click", {"ref": "e50"}, "checkbox")
blocked(s, B + "browser_type", {"ref": "e70", "text": "x"}, "password")
blocked(s, B + "browser_click", {"ref": "e99"}, "snapshot")  # unknown ref
ok(s, B + "browser_click", {"target": "e61", "element": "Next"})               # newer MCP: "target"
blocked(s, B + "browser_click", {"target": "e60"}, "submit")
blocked(s, B + "browser_type", {"target": "First Name", "text": "Jane"}, "unknown element")  # a description is not a ref

# typing and choosing: only what the answer bank issued, for that field
blocked(s, B + "browser_type", {"ref": "e12", "text": "Jane"}, "answer")
assist.issue(s, "First Name*", "Jane")
ok(s, B + "browser_type", {"ref": "e12", "text": "Jane"})
blocked(s, B + "browser_type", {"ref": "e12", "text": "Janet"}, "exactly")
blocked(s, B + "browser_type", {"ref": "e13", "text": "Jane"}, "no answer-bank value")  # issued for another field
blocked(s, B + "browser_type", {"ref": "e12", "text": "Jane", "submit": True}, "submit")
blocked(s, B + "browser_fill_form", {"fields": [{"ref": "e12", "type": "textbox", "value": "Jane"},
                                                 {"ref": "e50", "type": "checkbox", "value": "true"}]}, "checkbox")
ok(s, B + "browser_fill_form", {"fields": [{"ref": "e12", "type": "textbox", "value": "Jane"}]})
blocked(s, B + "browser_click", {"ref": "e31"}, "answer bank")   # option not issued
blocked(s, B + "browser_click", {"ref": "e40"}, "answer bank")   # radio not issued
assist.issue(s, "State*", "Massachusetts")
assist.issue(s, "Over 18?*", "Yes")
ok(s, B + "browser_click", {"ref": "e31"})
blocked(s, B + "browser_click", {"ref": "e32"}, "answer bank")   # Maine was never issued
ok(s, B + "browser_click", {"ref": "e40"})
blocked(s, B + "browser_select_option", {"ref": "e20", "values": ["Maine"]}, "not given out")
ok(s, B + "browser_select_option", {"ref": "e20", "values": ["Massachusetts"]})

# Bash: only the answer-bank command, no shell tricks
ok(s, "Bash", {"command": '/venv/python /proj/assist.py answer "City*" --option "A (+1)"'})
for bad in ['rm -rf /', '/venv/python /proj/assist.py answer "x"; rm -rf /', '/venv/python /proj/assist.py answer "$(id)"',
            '/venv/python /proj/assist.py hook pre', '/venv/python /other.py answer x', 'cat answers.json',
            '/venv/python /proj/assist.py answer x | sh']:
    blocked(s, "Bash", {"command": bad})

# refs: only from the latest snapshot; page-changing actions without one clear them (stale refs can't be trusted)
s = fresh()
assert assist.update_after(s, B + "browser_click", "### Page\n- Page URL: https://" + H + "/next\n") is None
assert s["refs"] == {}, "a click without a snapshot must clear refs"
blocked(s, B + "browser_click", {"ref": "e61"}, "snapshot")
assist.update_after(s, B + "browser_snapshot", SNAP.replace('button "Next" [ref=e61]', 'button "Submit" [ref=e61]'))
blocked(s, B + "browser_click", {"ref": "e61"}, "submit")  # same ref, now Submit: blocked
s2 = fresh()
assist.update_after(s2, B + "browser_wait_for", "waited")  # not page-changing: refs kept
assert s2["refs"]
w = assist.update_after(s2, B + "browser_click", "### Page\n- Page URL: https://accounts.google.com/x\n")
assert w and "not the posting" in w and s2["current_url"].startswith("https://accounts.google.com")

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
assert assist.lookup(E, "City*", [], "My Information")["status"] == "unknown"
assert assist.lookup(E, "Work Authorization*", ["Yes", "No"], "")["status"] == "ask_every_time"
assert assist.lookup(E, "Email*", [], "Voluntary Disclosures")["status"] == "ask_every_time"
assert assist.lookup(E, "Email*", ["a@b.c"], "")["status"] == "not_an_option"
assert assist.valid_answer("x" * 201, []) and assist.valid_answer("two\nlines", []) and assist.valid_answer(" ", [])
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

# NUworks prompt: typing the option's exact text works too (was: number only)
class IO:
    def __init__(self, reply):
        self.reply = reply

    def say(self, m):
        pass

    def ask(self, p):
        return self.reply


assert answers.choose_answer("x", "select", ["A", "University / College recruiting"], IO("University / College recruiting")) == "University / College recruiting"
assert answers.choose_answer("x", "select", ["A", "B"], IO("2")) == "B"

print("All assist (agent guard) checks passed.")
