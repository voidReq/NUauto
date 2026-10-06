"""Offline checks for answers.py and apply.fill_extra_fields. Run: python test_answers.py"""
import json
import os
import tempfile

from playwright.sync_api import sync_playwright

from nuauto import answers
from nuauto import apply
from nuauto import config

config.ANSWERS_PATH = os.path.join(tempfile.mkdtemp(), "answers.json")


class ScriptedIO(answers.TerminalIO):
    """Feeds canned terminal answers; running out = nobody there = Stop."""
    def __init__(self, *replies):
        self.replies, self.said = list(replies), []

    def say(self, msg):
        self.said.append(msg)

    def ask(self, prompt):
        if not self.replies:
            raise answers.Stop("no more input")
        return self.replies.pop(0)


class Log:
    def write(self, msg):
        pass


def expect_stop(fn):
    try:
        fn()
    except (answers.Stop, apply.NeedsHuman):
        return
    raise AssertionError("expected a stop")


# --- matching: lowercase + trim, exact only
e = [answers.new_entry("LinkedIn", "https://l/x")]
assert answers.find(e, "  LINKEDIN ") is e[0]
assert answers.find(e, "linkedin profile") is None      # no fuzzy matching
assert answers.find(e, "linkedin *") is None

# --- saved answer is used silently
io = ScriptedIO()
assert answers.resolve_answer(e, "LinkedIn", "text", [], io) == "https://l/x" and not io.replies

# --- no match: new answer is asked, saved, then reused
e = []
assert answers.resolve_answer(e, "Favorite color *", "text", [], ScriptedIO("1", "blue")) == "blue"
assert json.load(open(config.ANSWERS_PATH))[0]["question"] == "favorite color *"
assert answers.resolve_answer(answers.load(), "favorite color *", "text", [], ScriptedIO()) == "blue"

# --- no match: alias of an existing entry
e = [answers.new_entry("github", "gh/me")]
assert answers.resolve_answer(e, "GitHub URL *", "text", [], ScriptedIO("2", "1")) == "gh/me"
assert e[0]["aliases"] == ["github url *"]
assert answers.find(answers.load(), "github url *")  # alias was saved

# --- starter entry with no answer yet: asks and fills it in
e = [answers.new_entry("gpa")]
assert answers.resolve_answer(e, "GPA", "text", [], ScriptedIO("3.9")) == "3.9" and e[0]["answer"] == "3.9"

# --- always_ask: asked every time, never saved
e = [answers.new_entry("work authorization", "yes", always_ask=True)]
assert answers.resolve_answer(e, "Work Authorization", "text", [], ScriptedIO("Yes")) == "Yes"
assert e[0]["answer"] == "yes"  # saved value untouched

# --- dropdown: exact option only, else ask
opts = ["Select", "", "Yes", "No"]
e = [answers.new_entry("over 18", "Yes", "select")]
assert answers.resolve_answer(e, "over 18", "select", opts, ScriptedIO()) == "Yes"
assert answers.resolve_answer(e, "over 18", "select", ["Select", "yes please"], ScriptedIO("2")) == "yes please"

# --- blank / invalid replies stop
expect_stop(lambda: answers.resolve_answer([], "Q", "text", [], ScriptedIO("1", "")))
expect_stop(lambda: answers.resolve_answer([], "Q", "select", opts, ScriptedIO("1", "9")))
expect_stop(lambda: answers.resolve_answer([], "Q", "text", [], ScriptedIO("3")))
expect_stop(lambda: answers.resolve_answer([], "Q", "text", [], answers.NoTerminalIO()))

# --- real form filling, against a local page (no network, no NUworks)
FORM = """
<div role="dialog">
  <label for="r">Resume *</label><select id="r"><option value="">Select a resume</option><option>My Resume</option></select>
  <label for="l">LinkedIn *</label><input id="l" type="text">
  <label for="c">Co-op term *</label><select id="c"><option value="">Select</option><option>Spring 2027</option><option>Fall 2027</option></select>
  <label for="p">Pre-filled</label><input id="p" type="text" value="from site">
  EXTRA
</div>"""


def run_form(extra, entries, io):
    with sync_playwright() as p:
        b = p.firefox.launch(headless=True)
        page = b.new_page()
        page.set_content(FORM.replace("EXTRA", extra))
        try:
            apply.fill_extra_fields(page, page.get_by_role("dialog"), entries, io, Log())
            return page.locator("#l").input_value(), page.locator("#c").input_value(), page.locator("#p").input_value()
        finally:
            b.close()


entries = [answers.new_entry("linkedin *", "https://linkedin.com/in/me"), answers.new_entry("co-op term *", "Fall 2027", "select")]
li, term, pre = run_form("", entries, ScriptedIO())
assert (li, term, pre) == ("https://linkedin.com/in/me", "Fall 2027", "from site"), (li, term, pre)

# free text always stops
expect_stop(lambda: run_form('<label for="t">Why us?</label><textarea id="t"></textarea>', list(entries), ScriptedIO()))
# unseen field types stop
expect_stop(lambda: run_form('<label for="k">Agree *</label><input id="k" type="checkbox">', list(entries), ScriptedIO()))
# an unknown question with nobody to ask stops
expect_stop(lambda: run_form('<label for="x">Shoe size *</label><input id="x" type="text">', list(entries), answers.NoTerminalIO()))
# ...and is filled once answered at the terminal
li, term, pre = run_form('<label for="x">Shoe size *</label><input id="x" type="text">', list(entries), ScriptedIO("1", "10"))

# --- the GUI's channel (JsonIO): same rules as typed answers, questions as JSON lines
import io as _io  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402


OPEN_PIPES = []  # a write end that gets garbage-collected closes = "the GUI went away" = Ctrl+C; keep them all


def gui_open(*replies):
    """A JsonIO wired to a pipe with the GUI's replies already written: (io, its stdout, the pipe's write end).
    Closing the write end = the GUI went away."""
    r, w = os.pipe()
    out = _io.StringIO()
    wf = os.fdopen(w, "w")
    OPEN_PIPES.append(wf)
    for x in replies:
        wf.write(json.dumps(x) + "\n")
    wf.flush()
    return answers.JsonIO(stdin=os.fdopen(r), stdout=out), out, wf


def sent(out):
    return [json.loads(line[len(answers.JsonIO.MARK):]) for line in out.getvalue().split("\n")
            if line.startswith(answers.JsonIO.MARK)]


# unknown dropdown question answered in one dialog: saved like a typed answer, then reused silently
e = []
jio, out, wf = gui_open({"id": 1, "how": "new", "answer": "Yes"})
assert answers.resolve_answer(e, "Over 18? *", "select", ["Select", "Yes", "No"], jio) == "Yes"
q = sent(out)[0]
assert q["t"] == "ask" and q["kind"] == "unknown" and q["choices"] == ["Select", "Yes", "No"], q
assert e[-1]["question"] == "over 18? *" and e[-1]["answer"] == "Yes"
assert answers.resolve_answer(e, "over 18? *", "select", ["Select", "Yes", "No"], jio) == "Yes" and len(sent(out)) == 1
# an option that is not on the form, a bool or out-of-range alias index, an unknown reply: all stop
jio, out, wf = gui_open({"id": 1, "how": "new", "answer": "Maybe"})
expect_stop(lambda: answers.resolve_answer([], "Q *", "select", ["Yes", "No"], jio))
jio, out, wf = gui_open({"id": 1, "how": "alias", "index": True})
expect_stop(lambda: answers.resolve_answer([answers.new_entry("a", "x")], "Q *", "text", [], jio))
jio, out, wf = gui_open({"id": 1, "how": "alias", "index": 5})
expect_stop(lambda: answers.resolve_answer([answers.new_entry("a", "x")], "Q *", "text", [], jio))
jio, out, wf = gui_open({"id": 1, "how": "whatever"})
expect_stop(lambda: answers.resolve_answer([], "Q *", "text", [], jio))
# alias: picks an existing entry and saves the alias
e = [answers.new_entry("github", "gh/me")]
jio, out, wf = gui_open({"id": 1, "how": "alias", "index": 0})
assert answers.resolve_answer(e, "GitHub URL *", "text", [], jio) == "gh/me" and e[0]["aliases"] == ["github url *"]
# always-ask: the dialog shows why (context), the answer is not saved; blank text = stop
e = [answers.new_entry("salary", "", always_ask=True)]
jio, out, wf = gui_open({"id": 1, "answer": "  25/hr "}, {"id": 2, "answer": " "})
assert answers.resolve_answer(e, "Salary", "text", [], jio) == "25/hr" and e[0]["answer"] == ""
assert "always-ask" in " ".join(sent(out)[0]["context"])
expect_stop(lambda: answers.resolve_answer(e, "Salary", "text", [], jio))
# replies for another question id are ignored; a menu reply that is not an option = the safe default
jio, out, wf = gui_open({"id": 7, "answer": "y"}, {"id": 1, "answer": "maybe"})
assert jio.menu("Did it submit?", [("u", "Not sure"), ("y", "Yes"), ("n", "No")], "u") == "u"
jio, out, wf = gui_open({"id": 1, "answer": "y"})
assert jio.menu("Did it submit?", [("u", "Not sure"), ("y", "Yes"), ("n", "No")], "u") == "y"
# events are protocol lines; say() stays a plain log line
jio, out, wf = gui_open()
jio.event("row", row=3)
jio.say("hello")
assert sent(out) == [{"t": "event", "kind": "row", "row": 3}] and "hello" not in out.getvalue()

# the GUI going away (stdin closes) stops the run like Ctrl+C, exactly once
jio, out, wf = gui_open()
threading.Timer(0.3, wf.close).start()
try:
    jio.ask("anyone there?")
    raise AssertionError("expected KeyboardInterrupt")
except KeyboardInterrupt:
    pass
time.sleep(0.5)  # a second interrupt would crash this test right here
jio, out, wf = gui_open()
try:
    wf.close()  # not waiting on a question: the main thread is interrupted, as by Ctrl+C
    for _ in range(50):
        time.sleep(0.1)
    raise AssertionError("expected KeyboardInterrupt")
except KeyboardInterrupt:
    pass

# --ui json only for a child of the running GUI (pid in gui.lock)
config.GUI_LOCK_PATH = os.path.join(tempfile.mkdtemp(), "gui.lock")
assert not answers.json_ui_allowed()
json.dump({"pid": os.getppid()}, open(config.GUI_LOCK_PATH, "w"))
assert answers.json_ui_allowed()
json.dump({"pid": os.getppid() + 100000}, open(config.GUI_LOCK_PATH, "w"))
assert not answers.json_ui_allowed()

print("All answer-bank checks passed.")
