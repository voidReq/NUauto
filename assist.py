"""Agent helper for company application sites (Workday, Oracle, iCIMS, SuccessFactors...).

  nuauto assist                       list Needs Human rows stopped at a company site
  nuauto assist <row> [--url U]       start a Claude session (Sonnet) that fills that row's application in a
                                      visible browser
  nuauto assist nuworks <row>         submit an Applied row's job on NUworks too (retry)

The agent may browse any site the application needs, fill fields, tick boxes and upload the resume. It
NEVER submits without you: every Submit-type click (SUBMIT_RE, incl. "Apply") and the Enter key make
Claude Code ask you in the terminal first (hook "ask"); you review the application, then approve.
When you /exit, the terminal asks whether you submitted; y marks the row Applied (dated today), then the
same job is submitted on NUworks too (apply.submit_nuworks_side, the tested NUworks code).

Code checks every call (Claude Code hooks -> `assist.py hook pre|post`, logic in decide / update_after):
- ask first: Submit-type clicks, Enter, type(submit=true). Element names come from the latest snapshot,
  never from the agent's description (refs are cleared by anything that changes the page).
- never: password fields, page scripts (they could submit behind the review), uploads other than the
  resume, non-web links; Bash other than the answer-bank command; files outside the resume and notes.
"""
import fcntl
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from contextlib import contextmanager
from urllib.parse import urlparse

import answers
import config

STATE_ENV = "NUAUTO_ASSIST_DIR"
SERVER = "browser"  # MCP server name -> tools are mcp__browser__browser_*
MCP_PACKAGE = "@playwright/mcp@0.0.82"  # pinned: the guard is tested against this version's tool inputs
TOOLS = {"browser_snapshot", "browser_click", "browser_type", "browser_fill_form", "browser_select_option",
         "browser_hover", "browser_press_key", "browser_navigate", "browser_navigate_back", "browser_wait_for",
         "browser_take_screenshot", "browser_tabs", "browser_handle_dialog", "browser_find",
         "browser_console_messages", "browser_resize", "browser_close", "browser_file_upload"}
INTERACT = {"browser_click", "browser_type", "browser_fill_form", "browser_select_option", "browser_hover",
            "browser_press_key", "browser_handle_dialog"}
# Buttons/links that may submit an application: the user is asked first. "Apply" too: some sites
# (SuccessFactors) name their final button that; on a posting it only opens the form.
SUBMIT_RE = re.compile(r"^\s*(submit|submit (my |your |the )?application|send( my)? application|apply|apply now|"
                       r"finish|complete( my)? application|confirm( and submit)?)\s*$", re.I)
ENTER_KEYS = {"enter", "numpadenter", "return"}
BASH_COMMANDS = {"answer", "save", "once", "alias", "blank", "wait"}
MAX_ANSWER = 300  # one line; enough for a "30 word limit" answer, not an essay
REF_LINE = re.compile(r'^(?P<indent>\s*)-\s+(?P<role>[a-z]+)(?:\s+"(?P<name>(?:[^"\\]|\\.)*)")?(?P<attrs>[^\n]*?)\[ref=(?P<ref>[^\]\s]+)\](?P<tail>[^\n]*)$')
TEXT_LINE = re.compile(r'^(?P<indent>\s*)-\s+text:\s*(?P<text>.+)$')
LABEL_ROLES = {"paragraph", "generic", "text", "heading", "label", "strong", "emphasis", "legend"}
FIELD_ROLES = {"textbox", "combobox", "searchbox", "spinbutton"}


# ---------- pure helpers (offline-tested) ----------

def host(url):
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def label_key(label):
    """Answer-bank key: label without a trailing required "*", lowercased (answers.norm)."""
    return answers.norm(label.rstrip().rstrip("*"))


def link_from_notes(notes):
    """The company-site link in a Needs Human row's Notes ("External application: host -> url"), or None."""
    import apply  # unwrap (Outlook safelinks)
    m = re.search(r"External application: \S+ -> (https://\S+)", notes or "")
    return apply.unwrap(m.group(1)) if m else None


def assist_target(notes, url_override=None):
    """(url, None) if this Needs Human row is a company-site application the agent may work on, else
    (None, reason). NUworks itself is never driven by the agent (apply.py does NUworks); rows stopped for
    something on NUworks (cover letter, transcript...) are yours."""
    external = (notes or "").startswith("External application")
    if not external:
        return None, f"not a company-site application ({(notes or 'no notes')[:70]}): yours on NUworks"
    url = url_override or link_from_notes(notes)
    if not url:
        return None, "the notes name the site but not the link: add --url <posting link>"
    h = host(url)
    if urlparse(url).scheme != "https" or not h:
        return None, f"not an https link: {url!r}"
    if h in config.ALLOWED_HOSTS or h in config.SSO_HOSTS or h.endswith("symplicity.com"):
        return None, "that is NUworks itself: the agent never drives NUworks"
    return url, None


def page_always_asks(page):
    p = (page or "").lower()
    return any(w in p for w in ("voluntary", "self identify", "self-identify", "disclosure", "eeo", "demographic"))


def parse_refs(text):
    """{ref: {"role", "name"}} from a Playwright MCP snapshot. A field with no accessible name gets the text
    of the label-like line right above it at the same depth (e.g. a question paragraph, then its textbox):
    read from the page, never from the agent."""
    out, above = {}, {}  # above: indent -> text of the latest label-like sibling line
    for line in text.splitlines():
        m = REF_LINE.match(line)
        t = None if m else TEXT_LINE.match(line)
        if not m and not t:
            continue
        indent = len((m or t).group("indent"))
        for k in [k for k in above if k > indent]:
            del above[k]
        if t:
            above[indent] = t.group("text").strip()
            continue
        role, name = m.group("role"), (m.group("name") or "").replace('\\"', '"')
        tail = m.group("tail").strip()
        inline = tail[1:].strip() if tail.startswith(":") else ""
        if not name and role in FIELD_ROLES and indent in above:
            name = above.pop(indent)
        out[m.group("ref")] = {"role": role, "name": name}
        if role in LABEL_ROLES and inline:
            above[indent] = inline
        elif role not in LABEL_ROLES:
            above.pop(indent, None)
    return out


def page_url(text):
    m = re.findall(r"Page URL: (\S+)", text)
    return m[-1] if m else None


def strings(obj):
    """All strings inside a tool response (MCP content blocks, dicts, lists)."""
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in strings(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in strings(v)]
    return []


def update_after(state, tool, text):
    """PostToolUse logic (pure). Element refs are only trusted from the latest full snapshot: any action that
    can change the page clears them unless its reply carries a new snapshot (so a click's target name, which
    the submit check reads, is always current)."""
    name = tool.split("__")[-1]
    url = page_url(text)
    if url:
        state["current_url"] = url
    if "### Snapshot" in text or "Page Snapshot" in text:
        state["refs"] = parse_refs(text)
    elif name in INTERACT or name in ("browser_navigate", "browser_navigate_back", "browser_tabs", "browser_close",
                                      "browser_file_upload"):
        state["refs"] = {}


def under(path, roots):
    """True if path (symlinks resolved) is one of the file roots or inside one of the folder roots."""
    if not path:
        return False
    real = os.path.realpath(os.path.expanduser(path))
    return any(real == r or (os.path.isdir(r) and real.startswith(r.rstrip(os.sep) + os.sep)) for r in roots)


def check_read(state, tool, inp):
    roots = state.get("read_roots", [])
    if tool == "Read":
        p = inp.get("file_path", "")
        return None if under(p, roots) else f"Reading {p!r} is blocked: only the resume and notes ({roots})."
    p = inp.get("path", "")
    if not under(p, roots):
        return f"{tool} needs a path inside the resume / notes ({roots}), not {p or 'the project folder'!r}."
    pattern = inp.get("pattern", "") if tool == "Glob" else inp.get("glob", "") or ""
    if pattern.startswith(("/", "~")) or ".." in pattern:
        return f"{tool} pattern must stay inside the notes folder."
    return None


ALLOW = ("allow", None)


def deny(reason):
    return ("deny", reason)


def ask(reason):
    return ("ask", "NUAUTO: " + reason)


def decide(state, tool, inp):
    """("allow" | "deny" | "ask", reason). Pure: the PreToolUse hook's whole logic. "ask" = Claude Code asks the
    user in the terminal before the action runs (that is the review before anything is submitted)."""
    if tool == "Bash":
        why = check_bash(state, inp.get("command", ""))
        return deny(why) if why else ALLOW
    if tool in ("Read", "Glob", "Grep"):
        why = check_read(state, tool, inp)
        return deny(why) if why else ALLOW
    prefix = f"mcp__{SERVER}__"
    if not tool.startswith(prefix):
        return deny(f"{tool} is not allowed in this session.")
    name = tool[len(prefix):]
    if name not in TOOLS:
        return deny(f"{name} is not allowed (no page scripts: they could submit behind the review).")
    if name in ("browser_navigate", "browser_tabs") and inp.get("url"):
        scheme = urlparse(inp["url"]).scheme
        return ALLOW if scheme in ("http", "https") else deny(f"Only web pages, not {scheme}: links.")
    if name == "browser_file_upload":
        paths = inp.get("paths") or []
        resume = state.get("resume")
        if all(resume and os.path.realpath(os.path.expanduser(p)) == resume for p in paths):
            return ALLOW
        return deny(f"Only the resume can be uploaded: {resume}")
    if name == "browser_press_key":
        if inp.get("key", "").lower() in ENTER_KEYS:
            return ask("pressing Enter can submit a form. Approve only if the user has reviewed everything.")
        return ALLOW
    if name == "browser_type":
        target = state["refs"].get(ref_of(inp) or "")
        if target and "password" in target["name"].lower():
            return deny("Password fields are the user's.")
        if inp.get("submit"):
            return ask("typing with submit=true presses Enter, which can submit the form.")
        return ALLOW
    if name == "browser_fill_form":
        for f in inp.get("fields", []):
            target = state["refs"].get(ref_of(f) or "")
            if (target and "password" in target["name"].lower()) or "password" in str(f.get("name", "")).lower():
                return deny("Password fields are the user's.")
        return ALLOW
    if name == "browser_click":
        target = state["refs"].get(ref_of(inp) or "")
        if not target:
            return deny(f"Unknown element {ref_of(inp)!r}: use the element's ref (like e52) from your latest "
                        "browser_snapshot; after any click or typing, take a new snapshot first.")
        if SUBMIT_RE.match(target["name"]):
            return ask(f"clicking {target['name']!r} may SUBMIT the application. Approve only after the user has "
                       "reviewed every page (it is fine if this only opens the form).")
        return ALLOW
    return ALLOW


def ref_of(inp):
    """The element an action targets: "ref" or (newer Playwright MCP) "target"."""
    return inp.get("ref") or inp.get("target")


def check_bash(state, command):
    if re.search(r"[;&|`$<>\n\\]", command):
        return "Only the answer-bank command is allowed (no shell operators)."
    try:
        argv = shlex.split(command)
    except ValueError:
        return "Could not parse the command."
    if argv[:2] != state["answer_cmd"] or len(argv) < 3 or argv[2] not in BASH_COMMANDS:
        return f"The only command allowed is: {' '.join(state['answer_cmd'])} <{'|'.join(sorted(BASH_COMMANDS))}> ..."
    return None


def lookup(entries, label, options, page):
    """The answer bank's reply for `answer`: dict with status (and value)."""
    if page_always_asks(page):
        return {"status": "ask_every_time", "why": "voluntary / self-identify page"}
    entry = answers.find(entries, label_key(label))
    if entry is None:
        return {"status": "unknown"}
    if entry.get("always_ask"):
        return {"status": "ask_every_time", "why": "always_ask question"}
    if entry.get("leave_blank"):
        return {"status": "leave_blank"}
    if not entry["answer"]:
        return {"status": "unknown"}
    if options and entry["answer"] not in options:
        return {"status": "not_an_option", "saved": entry["answer"], "options": options}
    return {"status": "answer", "value": entry["answer"]}


def valid_answer(value, options):
    if not value.strip() or "\n" in value or len(value) > MAX_ANSWER:
        return f"Answers are one short line (max {MAX_ANSWER} characters): essays are the user's."
    if options and value not in options:
        return f"{value!r} is not exactly one of the options."
    return None


# ---------- run state (one folder per run, under logs/) ----------

@contextmanager
def state_file(path):
    with open(path, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        state = json.load(f)
        yield state
        f.seek(0)
        f.truncate()
        json.dump(state, f)


def run_dir():
    d = os.environ.get(STATE_ENV)
    if not d or not os.path.exists(os.path.join(d, "state.json")):
        sys.exit("Not inside an `nuauto assist` session.")
    return d


def issue(state, label, value):
    state["issued"][label_key(label)] = value
    if value not in state["issued_values"]:
        state["issued_values"].append(value)


def log(d, msg):
    from datetime import datetime
    with open(os.path.join(d, "actions.log"), "a") as f:
        f.write(f"{datetime.now().isoformat(timespec='seconds')}  {msg}\n")


# ---------- answer-bank commands (run by the agent through Bash) ----------

def bank(argv):
    if argv[:1] == ["wait"]:  # the browser tools may still be connecting when the session starts
        import time
        time.sleep(5)
        return print(json.dumps({"status": "waited 5s: check whether the browser tools are there now"}))
    import argparse
    ap = argparse.ArgumentParser(prog="assist.py")
    ap.add_argument("cmd", choices=sorted(BASH_COMMANDS))
    ap.add_argument("label")
    ap.add_argument("value", nargs="?")
    ap.add_argument("--option", action="append", default=[])
    ap.add_argument("--page", default="")
    a = ap.parse_args(argv)
    d = run_dir()
    entries = answers.load()
    out = {}
    with state_file(os.path.join(d, "state.json")) as state:
        if a.cmd == "answer":
            out = lookup(entries, a.label, a.option, a.page or state.get("page", ""))
            if out["status"] == "answer":
                issue(state, a.label, out["value"])
        elif a.cmd in ("save", "once"):
            if a.value is None:
                out = {"error": f"{a.cmd} needs a value"}
            elif why := valid_answer(a.value, a.option):
                out = {"error": why}
            elif a.cmd == "save" and (page_always_asks(a.page) or (answers.find(entries, label_key(a.label)) or {}).get("always_ask")):
                out = {"error": "This question is asked every time: use once, not save."}
            else:
                if a.cmd == "save":
                    e = answers.find(entries, label_key(a.label))
                    if e and e["answer"] and not e.get("leave_blank"):
                        out = {"error": f"Already saved as {e['answer']!r}; edit answers.json by hand to change it."}
                    else:
                        if e:
                            e["answer"], e["field_type"], e["leave_blank"] = a.value, "select" if a.option else "text", False
                        else:
                            entries.append(answers.new_entry(label_key(a.label), a.value, "select" if a.option else "text"))
                        answers.save(entries)
                if not out:
                    issue(state, a.label, a.value)
                    out = {"status": "answer", "value": a.value, "saved": a.cmd == "save"}
        elif a.cmd == "alias":
            target = answers.find(entries, a.value or "")
            if not target:
                out = {"error": f"No saved question {a.value!r}."}
            else:
                target.setdefault("aliases", []).append(label_key(a.label))
                answers.save(entries)
                out = lookup(entries, a.label, a.option, a.page)
                if out["status"] == "answer":
                    issue(state, a.label, out["value"])
        elif a.cmd == "blank":
            if answers.find(entries, label_key(a.label)):
                out = {"error": "That question already has an entry; edit answers.json by hand."}
            else:
                e = answers.new_entry(label_key(a.label), "", "text")
                e["leave_blank"] = True
                entries.append(e)
                answers.save(entries)
                out = {"status": "leave_blank", "saved": True}
    log(d, f"bank {a.cmd} {a.label!r} -> {out.get('status') or out.get('error')}")
    print(json.dumps(out))


# ---------- hooks (Claude Code runs these for every browser / Bash call) ----------

def hook(kind):
    d = run_dir()
    data = json.load(sys.stdin)
    tool, inp = data.get("tool_name", ""), data.get("tool_input") or {}
    with state_file(os.path.join(d, "state.json")) as state:
        if kind == "pre":
            verdict, why = decide(state, tool, inp)
            log(d, f"{verdict.upper():5} {tool} {json.dumps(inp)[:300]}" + (f"  ({why})" if why else ""))
            if verdict == "deny":
                print(why, file=sys.stderr)
                sys.exit(2)
            if verdict == "ask":  # Claude Code shows the user a yes/no confirmation in the terminal
                print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                                         "permissionDecisionReason": why}}))
            return
        with open(os.path.join(d, "last_response.json"), "w") as f:  # for debugging the guard (local log folder)
            json.dump(data.get("tool_response"), f)
        update_after(state, tool, "\n".join(strings(data.get("tool_response"))))


def hook_main(kind):
    try:
        hook(kind)
    except SystemExit:
        raise
    except Exception as e:  # a crashing guard must block, never allow
        print(f"Guard error ({type(e).__name__}); blocked.", file=sys.stderr)
        sys.exit(2)


# ---------- launcher (nuauto assist) ----------

def find_row(number, url_override):
    import sheet
    ws = sheet.open_worksheet()
    rows = sheet.read_rows(ws)
    row = next((r for r in rows if r.number == number), None)
    if row is None or row.status != "Needs Human":
        sys.exit(f"Row {number} is not a Needs Human row.")
    url, why = assist_target(row.notes, url_override)
    if why:
        sys.exit(f"Row {number} ({row.company}): {why}.")
    return rows, row, url


def claude_bin():
    return shutil.which("claude", path=os.path.expanduser("~/.local/bin") + os.pathsep + os.environ.get("PATH", "")) or "claude"


def run(number, url_override):
    import browser
    import sheet
    if not sys.stdin.isatty():
        sys.exit("nuauto assist needs a real terminal (you talk to the agent and confirm Submit).")
    rows, row, url = find_row(number, url_override)
    sheet.check_limits(rows)
    log_ = browser.RunLog(f"assist_row{number}")
    d = log_.dir
    me = os.path.abspath(__file__)
    answer_cmd = [sys.executable, me]
    read_roots = [os.path.realpath(r) for r in [*config.ASSIST_READ_PATHS, config.LAPTOP_RESUME] if os.path.exists(r)]
    resume = os.path.realpath(config.LAPTOP_RESUME) if os.path.exists(config.LAPTOP_RESUME) else None
    state = {"current_url": url, "refs": {}, "issued": {}, "issued_values": [], "answer_cmd": answer_cmd,
             "read_roots": read_roots, "resume": resume}
    with open(os.path.join(d, "state.json"), "w") as f:
        json.dump(state, f)
    os.makedirs(config.ASSIST_PROFILE_DIR, mode=0o700, exist_ok=True)
    mcp = {"mcpServers": {SERVER: {"command": "npx", "args": [
        MCP_PACKAGE, "--user-data-dir", config.ASSIST_PROFILE_DIR, "--output-dir", d]}}}
    hook_cmd = f"{shlex.quote(sys.executable)} {shlex.quote(me)} hook"
    settings = {"hooks": {
        "PreToolUse": [{"matcher": f"mcp__{SERVER}__.*|Bash|Read|Glob|Grep", "hooks": [{"type": "command", "command": f"{hook_cmd} pre"}]}],
        "PostToolUse": [{"matcher": f"mcp__{SERVER}__.*", "hooks": [{"type": "command", "command": f"{hook_cmd} post"}]}]}}
    for name, obj in (("mcp.json", mcp), ("settings.json", settings)):
        with open(os.path.join(d, name), "w") as f:
            json.dump(obj, f, indent=1)
    with open(os.path.join(config.PROJECT_DIR, "ASSIST_PROMPT.md")) as f:
        rules = f.read().replace("ANSWER ", " ".join(shlex.quote(a) for a in answer_cmd) + " ")
    context = (f"\n\nTHIS RUN\nRow {row.number}: {row.company} | {row.title}\nPosting: {url}\n"
               f"Resume file (the only file you may upload): {resume or 'not found'}\n"
               "If you have no browser tools yet, they are still connecting: run the answer-bank command with "
               "just `wait` (it pauses 5 s), then look again (up to 6 times) before telling the user.\n"
               f"Resume and notes you may read (Read / Glob / Grep, nothing else): {', '.join(read_roots) or 'none'}\n"
               "Answer bank command: "
               f"{' '.join(shlex.quote(a) for a in answer_cmd)} <answer|save|once|alias|blank> ...")
    log_.write(f"Row {row.number}: {row.company} | {row.title} -> {host(url)}")
    log_.write("Starting the agent. Sign in / upload / Submit are yours. Type /exit in the session when done.")
    cmd = [claude_bin(), "--model", "sonnet", "--strict-mcp-config", "--mcp-config", os.path.join(d, "mcp.json"),
           "--settings", os.path.join(d, "settings.json"), "--tools", "Bash", "Read", "Glob", "Grep",
           *[a for r in read_roots for a in ("--add-dir", r if os.path.isdir(r) else os.path.dirname(r))],
           "--allowedTools", f"mcp__{SERVER}", f"Bash({sys.executable} {me}:*)", "Read", "Glob", "Grep",
           "--append-system-prompt", rules + context,
           f"Go: open the posting and fill the application for row {row.number}, following the rules."]
    try:
        subprocess.run(cmd, env={**os.environ, STATE_ENV: d}, cwd=config.PROJECT_DIR)
    except KeyboardInterrupt:
        pass
    try:
        while True:
            reply = input(f"\nDid you press Submit for row {row.number} ({row.company}) and see the confirmation? "
                          "[y = yes / n = no]: ").strip().lower()
            if reply in ("y", "n"):
                break
    except (KeyboardInterrupt, EOFError):
        reply = "n"
    if reply != "y":
        log_.write(f"Not submitted; row {row.number} stays Needs Human.")
        return
    ws = sheet.open_worksheet()
    sheet.mark_applied_by_hand(ws, row.number, row.url, how="on the company site (nuauto assist; you pressed Submit)")
    log_.write(f"Row {row.number} marked Applied (dated today).")
    nuworks_side(ws, row.number)


def nuworks_side(ws, number):
    """Company site done -> submit the same job on NUworks too (apply.submit_nuworks_side: tested NUworks
    code, not the agent)."""
    import apply
    import sheet
    row = next((r for r in sheet.read_rows(ws) if r.number == number), None)
    if row is None or row.status != "Applied":
        sys.exit(f"Row {number} is not Applied; the NUworks side runs only after the company site is done.")
    if "NUworks side submitted" in row.notes:
        sys.exit(f"Row {number}: the NUworks side is already submitted.")
    print(f"\nNow submitting row {number} on NUworks too (Ctrl+C stops)...")
    print(apply.submit_nuworks_side(row, ws))


def list_rows():
    import sheet
    rows = [r for r in sheet.read_rows(sheet.open_worksheet()) if r.status == "Needs Human"]
    targets = [(r, *assist_target(r.notes)) for r in rows]
    agent = [(r, u) for r, u, why in targets if u]
    other = [(r, why) for r, u, why in targets if not u]
    print("Company-site applications (nuauto assist <row>):" if agent else "No company-site applications waiting.")
    for r, u in agent:
        print(f"  row {r.number:<3} {r.company[:25]:25} {r.title[:38]:38} {host(u)}")
    if other:
        print("Not for the agent:")
        for r, why in other:
            print(f"  row {r.number:<3} {r.company[:25]:25} {why}")


def main(argv):
    args, url = list(argv), None
    if "--url" in args:
        i = args.index("--url")
        if i + 1 >= len(args):
            sys.exit(__doc__)
        url = args[i + 1]
        del args[i:i + 2]
    if not args:
        return list_rows()
    if len(args) == 2 and args[0] == "nuworks" and args[1].isdigit():  # retry / catch up the NUworks side
        import sheet
        return nuworks_side(sheet.open_worksheet(), int(args[1]))
    if len(args) == 1 and args[0].isdigit():
        import sheet
        try:
            return run(int(args[0]), url)
        except sheet.LimitReached as e:
            sys.exit(str(e))
    sys.exit(__doc__)


if __name__ == "__main__":
    if sys.argv[1:2] == ["hook"] and sys.argv[2:3] in (["pre"], ["post"]):
        hook_main(sys.argv[2])
    elif sys.argv[1:2] and sys.argv[1] in BASH_COMMANDS:
        bank(sys.argv[1:])
    else:
        main(sys.argv[1:])
