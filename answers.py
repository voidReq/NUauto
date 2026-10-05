"""Answer bank (answers.json): exact-match answers for form fields.

  python answers.py init    # create answers.json with the starter questions (never overwrites)
  python answers.py list    # show questions and whether each has an answer

Edit by hand with: nvim answers.json

Rules (from CLAUDE.md): lowercase + trim the field label, EXACT match only, no fuzzy
matching. No match -> ask in the terminal, save, reuse next time. always_ask entries
are asked every time and never saved or auto-filled.
"""
import json
import os
import sys
from datetime import date

import config

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
    def say(self, msg):
        print(msg)

    def ask(self, prompt):
        try:
            return input(prompt).strip()
        except EOFError:
            raise Stop("no terminal input")


class NoTerminalIO:
    """Used when stdin is not a terminal: any question becomes a stop."""

    def say(self, msg):
        print(msg)

    def ask(self, prompt):
        raise Stop("an answer is needed but there is no terminal to ask in")


def choose_answer(label, field_type, options, io):
    """Ask the human for an answer. Blank / invalid = stop."""
    if field_type == "select":
        choices = [o for o in options if o.strip()]
        for i, o in enumerate(choices, 1):
            io.say(f"    {i}) {o}")
        pick = io.ask(f"  Option number (or its exact text) for {label!r} (blank = stop): ")
        if pick in choices:  # typed the option's exact text
            return pick
        if not pick.isdigit() or not 1 <= int(pick) <= len(choices):
            raise Stop(f"no valid option chosen for {label!r}")
        return choices[int(pick) - 1]
    answer = io.ask(f"  Answer for {label!r} (blank = stop): ")
    if not answer:
        raise Stop(f"no answer given for {label!r}")
    return answer


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

    io.say(f"  No saved answer for {label!r} [{field_type}].")
    choice = io.ask("  1 = enter a new answer, 2 = same question as an existing entry (alias), anything else = stop: ")
    if choice == "1":
        answer = choose_answer(label, field_type, options, io)
        entries.append(new_entry(label, answer, field_type))
        save(entries)
        return answer
    if choice == "2":
        for i, e in enumerate(entries, 1):
            io.say(f"    {i}) {e['question']}{'' if e['answer'] or e.get('always_ask') else '  (no answer yet)'}")
        pick = io.ask("  Entry number (blank = stop): ")
        if not pick.isdigit() or not 1 <= int(pick) <= len(entries):
            raise Stop(f"no valid entry chosen for {label!r}")
        entries[int(pick) - 1].setdefault("aliases", []).append(norm(label))
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
