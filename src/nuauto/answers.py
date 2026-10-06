"""Answer bank (answers.json): exact-match answers for form fields.

  nuauto answers init    # create answers.json with the starter questions (never overwrites)
  nuauto answers list    # show questions and whether each has an answer

Edit by hand with: nvim answers.json

Rules (from CLAUDE.md): lowercase + trim the field label, EXACT match only, no fuzzy
matching. No match -> ask in the terminal, save, reuse next time. always_ask entries
are asked every time and never saved or auto-filled.
"""
import _thread
import json
import os
import queue
import sys
import threading
from datetime import date

from nuauto import config

# (question, always_ask)
STARTERS = [
    ("name", False), ("preferred name", False), ("email", False), ("phone", False),
    ("address", False), ("school", False), ("major", False),
    ("expected graduation date", False), ("gpa", False),
    ("work authorization", True), ("sponsorship needed", True),
    ("available start date", False), ("available end date", False),
    ("hours per week", False), ("co-op term", False), ("relocation", False),
    ("commute", False), ("linkedin", False), ("github", False), ("portfolio", False),
    ("how did you hear about us", False), ("over 18", False),
    ("ok with background check", False),
]


class Stop(Exception):
    """The human chose to stop, or there is nobody at a terminal to ask."""


def norm(label):
    return label.strip().lower()


def new_entry(question, answer="", field_type="", always_ask=False):
    return {
        "question": norm(question),
        "aliases": [],
        "answer": answer,
        "field_type": field_type,
        "date_added": date.today().isoformat(),
        "always_ask": always_ask,
    }


def load():
    if not os.path.exists(config.ANSWERS_PATH):
        return []
    with open(config.ANSWERS_PATH) as f:
        return json.load(f)


def save(entries):
    tmp = config.ANSWERS_PATH + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(entries, f, indent=2)
    os.replace(tmp, config.ANSWERS_PATH)


def find(entries, label):
    """Exact match on lowercase + trimmed label, against the question or any alias."""
    key = norm(label)
    for e in entries:
        if key == norm(e["question"]) or key in [norm(a) for a in e.get("aliases", [])]:
            return e
    return None


class TerminalIO:
    """Questions at the terminal. say/ask show and read one line; the question methods below keep the exact
    wording the terminal has always used. JsonIO (the GUI) replaces them with dialogs; the rules that decide
    what is asked, saved or refused stay in resolve_answer."""

    def say(self, msg):
        print(msg)

    def ask(self, prompt):
        try:
            return input(prompt).strip()
        except EOFError:
            raise Stop("no terminal input")

    def event(self, kind, **data):
        """Progress for the GUI (row started, screenshot saved...). The terminal already shows the log."""

    def choose_option(self, label, choices):
        """Exactly one of `choices` for a dropdown. Blank / invalid = Stop."""
        for i, o in enumerate(choices, 1):
            self.say(f"    {i}) {o}")
        pick = self.ask(f"  Option number (or its exact text) for {label!r} (blank = stop): ")
        if pick in choices:  # typed the option's exact text
            return pick
        if not pick.isdigit() or not 1 <= int(pick) <= len(choices):
            raise Stop(f"no valid option chosen for {label!r}")
        return choices[int(pick) - 1]

    def enter_text(self, label):
        answer = self.ask(f"  Answer for {label!r} (blank = stop): ")
        if not answer:
            raise Stop(f"no answer given for {label!r}")
        return answer

    def unknown_field(self, label, field_type, options, entries):
        """No saved answer. ("new", None) = answer it now; ("alias", i) = it is entries[i] reworded. Else Stop."""
        self.say(f"  No saved answer for {label!r} [{field_type}].")
        choice = self.ask("  1 = enter a new answer, 2 = same question as an existing entry (alias), anything else = stop: ")
        if choice == "1":
            return "new", None
        if choice == "2":
            for i, e in enumerate(entries, 1):
                self.say(f"    {i}) {e['question']}{'' if e['answer'] or e.get('always_ask') else '  (no answer yet)'}")
            pick = self.ask("  Entry number (blank = stop): ")
            if not pick.isdigit() or not 1 <= int(pick) <= len(entries):
                raise Stop(f"no valid entry chosen for {label!r}")
            return "alias", int(pick) - 1
        raise Stop(f"stopped at {label!r}")

    def menu(self, prompt, options, default):
        """One key of options [(key, label)]. Anything else, or no input, counts as `default` (the safest)."""
        try:
            answer = self.ask(prompt).lower()
        except Stop:
            answer = ""
        return answer if answer in [k for k, _ in options] else default


class NoTerminalIO(TerminalIO):
    """Used when stdin is not a terminal: any question becomes a stop."""

    def ask(self, prompt):
        raise Stop("an answer is needed but there is no terminal to ask in")


class JsonIO(TerminalIO):
    """The GUI's channel (`--ui json`, only in a child process of `nuauto gui`). Each question is one line on
    stdout: MARK + JSON. The GUI answers with one JSON line on stdin, {"id": ..., ...}. Everything else printed
    stays a plain log line. Answers are checked here exactly like typed ones (an option must be one of the
    choices, blank = stop). If stdin closes, the GUI is gone: the run stops the way Ctrl+C stops it."""
    MARK = "::nuauto:: "  # starts every protocol line; plain text, so splitlines() and terminals treat it normally

    def __init__(self, stdin=None, stdout=None):
        self._out = stdout or sys.stdout
        self._answers = queue.Queue()
        self._lock = threading.Lock()
        self._closed = self._waiting = False
        self._n = 0
        self._context = []   # say() lines since the last question, shown in its dialog
        self._prefill = {}   # answers given in an "unknown field" dialog, used by the next question for that label
        threading.Thread(target=self._read, args=(stdin or sys.stdin,), daemon=True).start()

    def _read(self, stdin):
        for line in stdin:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict):
                self._answers.put(msg)
        with self._lock:
            self._closed = True
            waiting = self._waiting
            self._answers.put(None)
        if not waiting:  # a waiting question raises KeyboardInterrupt itself; never interrupt twice
            _thread.interrupt_main()

    def send(self, obj):
        self._out.write(self.MARK + json.dumps(obj) + "\n")
        self._out.flush()

    def say(self, msg):
        print(msg, flush=True)
        self._context = (self._context + [str(msg)])[-6:]

    def event(self, kind, **data):
        self.send({"t": "event", "kind": kind, **data})

    def _ask(self, kind, **fields):
        with self._lock:
            if self._closed:
                raise KeyboardInterrupt
            self._waiting = True
        try:
            self._n += 1
            self.send({"t": "ask", "id": self._n, "kind": kind, "context": self._context, **fields})
            self._context = []
            while True:
                try:  # short waits: a Ctrl+C / Stop that lands just as the wait starts is still seen within 0.5 s
                    msg = self._answers.get(timeout=0.5)
                except queue.Empty:
                    continue
                if msg is None:
                    raise KeyboardInterrupt  # the GUI went away mid-question
                if msg.get("id") == self._n:
                    return msg
        finally:
            with self._lock:
                self._waiting = False

    def ask(self, prompt):
        answer = self._ask("text", prompt=prompt).get("answer")
        return answer.strip() if isinstance(answer, str) else ""

    def choose_option(self, label, choices):
        answer = self._prefill.pop(norm(label), None)
        if answer is None:
            answer = self._ask("option", label=label, choices=choices).get("answer")
        if isinstance(answer, str) and answer in choices:
            return answer
        raise Stop(f"no valid option chosen for {label!r}")

    def enter_text(self, label):
        answer = self._prefill.pop(norm(label), None)
        if answer is None:
            answer = self._ask("field", label=label).get("answer")
        if isinstance(answer, str) and answer.strip():
            return answer.strip()
        raise Stop(f"no answer given for {label!r}")

    def unknown_field(self, label, field_type, options, entries):
        msg = self._ask("unknown", label=label, field_type=field_type, choices=[o for o in options if o.strip()],
                        entries=[{"question": e["question"], "answered": bool(e["answer"] or e.get("always_ask"))}
                                 for e in entries])
        how, i = msg.get("how"), msg.get("index")
        if how == "new":
            if "answer" in msg:
                self._prefill[norm(label)] = msg["answer"]
            return "new", None
        if how == "alias" and isinstance(i, int) and not isinstance(i, bool) and 0 <= i < len(entries):
            return "alias", i
        raise Stop(f"stopped at {label!r}")

    def menu(self, prompt, options, default):
        answer = self._ask("menu", prompt=prompt, options=[{"key": k, "label": v} for k, v in options],
                           default=default).get("answer")
        return answer if answer in [k for k, _ in options] else default


def json_ui_allowed():
    """`--ui json` is only for a child process of the running `nuauto gui` (its pid is in local/gui.lock)."""
    try:
        with open(config.GUI_LOCK_PATH) as f:
            return json.load(f).get("pid") == os.getppid()
    except (OSError, ValueError, AttributeError):
        return False


def choose_answer(label, field_type, options, io):
    """Ask the human for an answer. Blank / invalid = stop."""
    if field_type == "select":
        return io.choose_option(label, [o for o in options if o.strip()])
    return io.enter_text(label)


def resolve_answer(entries, label, field_type, options, io):
    """Return the exact text to enter for this field. Saves new answers/aliases to answers.json."""
    entry = find(entries, label)

    if entry and entry.get("always_ask"):
        io.say(f"  {label!r} is an always-ask question: not auto-filled, not saved.")
        return choose_answer(label, field_type, options, io)

    if entry and entry["answer"]:
        if field_type != "select" or entry["answer"] in options:
            return entry["answer"]
        io.say(f"  Saved answer {entry['answer']!r} is not one of this dropdown's options.")
        return choose_answer(label, field_type, options, io)

    if entry:  # a starter question that has no answer yet
        answer = choose_answer(label, field_type, options, io)
        entry["answer"], entry["field_type"] = answer, field_type
        save(entries)
        return answer

    how, i = io.unknown_field(label, field_type, options, entries)
    if how == "new":
        answer = choose_answer(label, field_type, options, io)
        entries.append(new_entry(label, answer, field_type))
        save(entries)
        return answer
    if how == "alias" and isinstance(i, int) and 0 <= i < len(entries):
        entries[i].setdefault("aliases", []).append(norm(label))
        save(entries)
        return resolve_answer(entries, label, field_type, options, io)
    raise Stop(f"stopped at {label!r}")


def main():
    cmd = sys.argv[1:]
    if cmd == ["init"]:
        if os.path.exists(config.ANSWERS_PATH):
            sys.exit("answers.json already exists. Not overwriting. Edit it with: nvim answers.json")
        save([new_entry(q, always_ask=a) for q, a in STARTERS])
        print(f"Created answers.json with {len(STARTERS)} starter questions (no answers yet).")
    elif cmd == ["list"]:
        for e in load():
            state = "always asks" if e.get("always_ask") else ("answered" if e["answer"] else "no answer yet")
            aliases = f"  aliases: {e['aliases']}" if e.get("aliases") else ""
            print(f"- {e['question']}: {state}{aliases}")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
