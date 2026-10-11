"""Agent helper for company application sites (Workday, Oracle, iCIMS, SuccessFactors...).

  nuauto assist                       list Needs Human rows stopped at a company site
  nuauto assist <row> [--url U]       start a Claude session (Sonnet) that fills that row's application in a
                                      visible browser
  nuauto assist nuworks <row>         submit an Applied row's job on NUworks too (retry; you at a terminal,
                                      or `--ui json` from nuauto gui)
  nuauto assist other                 list the Approved rows of the sheet's Other jobs tab (jobs not on NUworks)
  nuauto assist other <row>           the same agent for that tab's row; afterwards nothing is sent to NUworks
  nuauto assist other add <url> <company> <title>
                                      add a job to the Other jobs tab as Approved (the tab is made if missing)

The agent may browse any site the application needs, fill fields, tick boxes, upload the resume, look things up on
the web (WebSearch / WebFetch: the company, the role) and read your notes (local_config.json "assist_read_paths"). It
makes the accounts sites ask for, with your account email and a password NUauto makes (accounts.py): it types a
placeholder and the guard types the real password, for that site only; the agent never sees it. It NEVER submits
without you: every Submit-type click (SUBMIT_RE, incl. "Apply") and the Enter key make Claude Code ask you in the
terminal first (hook "ask"); you review the application, then approve.
When you /exit, the terminal asks whether you submitted; y marks the row Applied (dated today), then the
same job is submitted on NUworks too (apply.submit_nuworks_side, the tested NUworks code). For an Other jobs tab
row, y only marks it Applied (those rows have no weekly or total cap), and its answers come from Bank.

Code checks every call (Claude Code hooks -> `assist.py hook pre|post`, logic in decide / update_after):
- ask first: Submit-type clicks, Enter, type(submit=true). Element names come from the latest snapshot,
  never from the agent's description (refs are cleared by anything that changes the page).
- passwords: only {{NEW_PASSWORD}} / {{PASSWORD}}, alone, into a field the latest snapshot names a password, never
  on an identity provider or Northeastern (accounts.never); the guard swaps in the site's password (updatedInput) and
  takes it out of what the browser tool replies (updatedToolOutput) and of the run's logs.
- never: page scripts (they could submit behind the review), uploads other than the resume, non-web links; Bash
  other than the answer-bank command; files outside the resume and notes, or secret ones inside them (secret_path).
- If the guard itself fails or times out, the action is blocked (the hooks' onFailure "block").
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

from nuauto import accounts
from nuauto import answers
from nuauto import config

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
CLICK_ROLES = {"button", "link", "menuitem", "tab", "option"}  # with no name, their visible text is what they say
WEB_TOOLS = {"WebSearch", "WebFetch"}  # looking things up (the company, the role): read-only
# A box meant for bots only (Workday: "Enter website. This input is for robots only, do not enter if you're human"):
# filling it marks you as a bot. Never typed into; and it doesn't count as a question on the page.
HONEYPOT = re.compile(r"robots? only|for robots|do not (fill|enter|type)|leave (this )?(field |box )?(blank|empty)|honey ?pot", re.I)
ACCOUNT_FIELD = re.compile(r"e-?mail|user ?name|user ?id|login|password|passcode", re.I)
SIGN_IN_SUBMIT = {"submit", "confirm"}  # the SUBMIT_RE names a sign-in page's own button may have
POSTING_APPLY = {"apply", "apply now"}  # the SUBMIT_RE names a posting's own button may have (it opens the application)
SEARCH_BOX = re.compile(r"search|keyword", re.I)  # a job-search box on a posting page is not a form
LOCALE = re.compile(r"^[a-z]{2}([-_][A-Za-z]{2})?$")  # "en-US" in /en-US/Careers/job/...: the same posting
INPUT_TOOLS = {"browser_type", "browser_fill_form", "browser_select_option", "browser_file_upload"}  # filling something in
HIDDEN = "<password hidden by NUauto>"  # what the agent sees where a password NUauto typed would show (replies, snapshots)
PASSWORD_WORDS = ("password", "passcode", "passwd", "pass word")
EFFORT = "low"  # the agent's reasoning effort (local_config.json "assist_effort"): the guard, not the model, keeps it safe
# Never read, even inside the notes folders: folders and file names that hold keys, tokens and logins.
SECRET_DIRS = {".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".password-store", ".git", "browser_profile",
               "assist_profile", ".mozilla"}
SECRET_NAME = re.compile(r"^\.env(\..+)?$|\.(pem|key|p12|pfx|kdbx|keystore|jks|ovpn)$|^id_(rsa|dsa|ecdsa|ed25519)|"
                         r"secret|token|cookie|credential|password|passwd|^\.netrc$|^\.npmrc$|^\.pypirc$|"
                         r"^\.git-credentials$|^accounts\.json$|^answers(_other)?\.json$|^local_config\.json$", re.I)


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
    from nuauto import apply  # unwrap (Outlook safelinks)
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
    return check_link(url)


def check_link(url):
    """(url, None) for an https link the agent may open first, else (None, reason). Never NUworks itself."""
    h = host(url)
    if urlparse(url).scheme != "https" or not h:
        return None, f"not an https link: {url!r}"
    if h in config.ALLOWED_HOSTS or h in config.SSO_HOSTS or h.endswith("symplicity.com"):
        return None, "that is NUworks itself: the agent never drives NUworks (NUworks jobs go in the main tab)"
    return url, None


def other_target(row, url_override=None):
    """(url, None) if the agent may work on this Other jobs tab row (Approved, an https link that is not NUworks),
    else (None, reason)."""
    if row.status != "Approved":
        return None, f"it is {row.status or 'not marked'}, not Approved"
    return check_link(url_override or row.url)


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
        tail = re.sub(r"^(?:\s*\[[^\]]*\])+", "", m.group("tail")).strip()  # past " [cursor=pointer]" and the like
        inline = tail[1:].strip().strip('"') if tail.startswith(":") else ""
        if not name and role in FIELD_ROLES and indent in above:
            name = above.pop(indent)
        if not name and role in CLICK_ROLES and inline:  # `button [ref=e9]: Submit` (no name, only its text): the text
            name = inline
        out[m.group("ref")] = {"role": role, "name": name}
        if "[active]" in m.group("attrs") + m.group("tail"):  # the element that has the keyboard focus
            out[m.group("ref")]["active"] = True
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


def upload_copy(resume, run_dir):
    """Copy the resume (same file name) into the run's log folder; return the copy's real path.
    The browser tool only uploads from its working and output folders, and the resume usually lives
    elsewhere. The copy is the one file the guard lets the agent upload."""
    folder = os.path.join(run_dir, "upload")
    os.makedirs(folder, mode=0o700, exist_ok=True)
    copy = os.path.join(folder, os.path.basename(resume))
    shutil.copyfile(resume, copy)
    os.chmod(copy, 0o600)
    return os.path.realpath(copy)


def under(path, roots):
    """True if path (symlinks resolved) is one of the file roots or inside one of the folder roots."""
    if not path:
        return False
    real = os.path.realpath(os.path.expanduser(path))
    return any(real == r or (os.path.isdir(r) and real.startswith(r.rstrip(os.sep) + os.sep)) for r in roots)


def protected_dirs(roots):
    """NUauto's local/ folders under the notes roots (this one, and any other checkout's: local/ with a
    local_config.json in it), found once when the run starts: Grep may not search a folder that holds one."""
    import glob
    found = {os.path.realpath(config.LOCAL_DIR)}
    for r in roots:
        if os.path.isdir(r):
            for depth in ("", "*/", "*/*/", "*/*/*/", "*/.claude/worktrees/*/"):
                found.update(os.path.dirname(os.path.realpath(p)) for p in glob.glob(os.path.join(r, depth + "local/local_config.json")))
    return sorted(found)


def secret_path(path, protected=()):
    """Why this path is never read, even inside the notes folders (NUauto's local/ folder, keys, tokens, logins,
    .env files...), or None."""
    real = os.path.realpath(os.path.expanduser(path or ""))
    for p in [os.path.realpath(config.LOCAL_DIR), *protected]:
        if real == p or real.startswith(p.rstrip(os.sep) + os.sep):
            return "NUauto's local/ folder (your logins, answers and settings)"
    logs = os.path.realpath(config.LOGS_DIR)
    if real == logs or real.startswith(logs + os.sep):
        return "NUauto's run logs (what earlier runs typed and saw)"
    parts = real.split(os.sep)
    hit = next((x for x in parts[:-1] if x in SECRET_DIRS), None)
    if hit:
        return f"inside a {hit} folder"
    if SECRET_NAME.search(parts[-1]):
        return "a file that may hold a secret (keys, tokens, passwords, cookies, .env)"
    return None


def check_read(state, tool, inp):
    roots = state.get("read_roots", [])
    protected = state.get("protected", [])
    if tool == "Read":
        p = inp.get("file_path", "")
        if state.get("resume_src") and os.path.realpath(os.path.expanduser(p)) == state["resume_src"]:
            return None
        if not under(p, roots):
            return f"Reading {p!r} is blocked: only the resume and notes ({roots})."
        why = secret_path(p, protected)
        return f"Reading {p!r} is blocked: {why}." if why else None
    p = inp.get("path", "")
    if not under(p, roots):
        return f"{tool} needs a path inside the resume / notes ({roots}), not {p or 'the project folder'!r}."
    why = secret_path(p, protected)
    if why:
        return f"{tool} on {p!r} is blocked: {why}."
    real = os.path.realpath(os.path.expanduser(p))
    if tool == "Grep":
        inside = [d for d in [*protected, os.path.realpath(config.LOGS_DIR)] if d.startswith(real.rstrip(os.sep) + os.sep)]
        if inside:
            return (f"Grep {p!r} would search {inside[0]} (NUauto's own logins or logs). Search a folder inside "
                    "it instead (one project, or the notes folder).")
    pattern = inp.get("pattern", "") if tool == "Glob" else inp.get("glob", "") or ""
    if pattern.startswith(("/", "~")) or ".." in pattern:
        return f"{tool} pattern must stay inside the notes folder."
    return None


ALLOW = ("allow", None)


def deny(reason):
    return ("deny", reason)


def ask(reason):
    return ("ask", "NUAUTO: " + reason)


def is_password(target):
    """Whether the latest snapshot names this field a password (never the agent's own description of it)."""
    return bool(target) and any(w in (target.get("name") or "").lower() for w in PASSWORD_WORDS)


def sign_in_page(refs):
    """Whether the latest snapshot is a sign-in or create-account page: a password box, and every other box on it is
    for the account (email, user name, the password again) or a bot trap; tick boxes may be there (terms, remember
    me). Its "Submit" signs in or makes the account: no application goes out."""
    boxes = [r for r in refs.values() if r["role"] in FIELD_ROLES | {"radio"} and not HONEYPOT.search(r.get("name") or "")]
    return any(is_password(r) for r in boxes) and all(ACCOUNT_FIELD.search(r.get("name") or "") for r in boxes)


def same_posting(current, job):
    """Whether the browser is on the job's own posting (the row's link): the same site and path, a language part like
    /en-US/ aside (sites add one when they redirect); the query and anchor don't matter."""
    a, b = urlparse(current or ""), urlparse(job or "")
    if not a.hostname or a.hostname.lower() != (b.hostname or "").lower():
        return False
    parts = lambda u: [s for s in u.path.split("/") if s and not LOCALE.match(s)]  # noqa: E731
    return parts(a) == parts(b)


def posting_page(state):
    """The job's own posting, before anything is filled in: its page (the row's link), and no form boxes on it (a job
    search box or a bot trap aside). Its "Apply" opens the application; nothing can go out from there (except on a
    site with one-click apply from a saved profile: the user's call, 2026-10-10)."""
    if state.get("filled") or not same_posting(state.get("current_url"), state.get("job_url")):
        return False
    return not [r for r in state["refs"].values() if r["role"] in FIELD_ROLES | {"checkbox", "radio"}
                and not SEARCH_BOX.search(r.get("name") or "") and not HONEYPOT.search(r.get("name") or "")]


WORKDAY = ("myworkdayjobs.com", "myworkdaysite.com")


def workday(state):
    """Whether the page is a Workday job site. Its application pages don't submit on Enter (they are not HTML forms;
    each step has its own button, and the final Submit is a button on a review page with no boxes), and its
    search-a-list boxes need Enter."""
    h = host(state.get("current_url", ""))
    return any(h == d or h.endswith("." + d) for d in WORKDAY)


def placeholder_of(value):
    """The password placeholder this value is ("" for none); None when it mixes one with other text."""
    v = str(value or "")
    if not any(p in v for p in accounts.PLACEHOLDERS):
        return ""
    return v if v in accounts.PLACEHOLDERS else None


def password_check(state, target, value, submit=False):
    """None (nothing to do with passwords), ("deny", why), or ("fill", placeholder): the guard types the site's
    password in place of the placeholder (hook "pre")."""
    ph = placeholder_of(value)
    if ph is None:
        return deny(f"Type {accounts.NEW} or {accounts.SIGN_IN} alone, with nothing else in the same field.")
    if not ph:
        if is_password(target):
            return deny(f"Password fields take {accounts.NEW} (making an account on this site) or {accounts.SIGN_IN} "
                        "(signing in to the one NUauto made here), never a password itself.")
        return None
    if not is_password(target):
        return deny(f"{ph} goes only into a field your latest snapshot names a password (take a new snapshot).")
    if submit:
        return deny("Type the password without submit, then click the sign-in or create-account button.")
    why = accounts.never(host(state.get("current_url", "")))
    if why:
        return deny(f"Not here: {why}. Tell the user.")
    return ("fill", ph)


def decide(state, tool, inp):
    """("allow" | "deny" | "ask", reason), or ("fill", plan). Pure: the PreToolUse hook's whole logic. "ask" = Claude
    Code asks the user in the terminal before the action runs (that is the review before anything is submitted).
    "fill": a password placeholder, typed by the guard; plan = [(where in the input, placeholder)], where is "text"
    (browser_type) or a fields index (browser_fill_form)."""
    if tool == "Bash":
        why = check_bash(state, inp.get("command", ""))
        return deny(why) if why else ALLOW
    if tool in ("Read", "Glob", "Grep"):
        why = check_read(state, tool, inp)
        return deny(why) if why else ALLOW
    if tool in WEB_TOOLS:
        return ALLOW
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
            if sign_in_page(state["refs"]):
                return ("allow", "Enter on a sign-in page (only account boxes on it): it signs in")
            if posting_page(state):
                return ("allow", "Enter on the posting (no form on it): a job search at most")
            if workday(state) and any(r.get("active") and r["role"] in FIELD_ROLES for r in state["refs"].values()):
                return ("allow", "Enter in a box on Workday (its pages don't submit on Enter; Submit is its own button)")
            return ask("pressing Enter can submit a form (on a one-page application, the whole application). Approve "
                       "only if the user has reviewed everything; to pick from a list, click the option instead.")
        return ALLOW
    if name == "browser_type":
        target = state["refs"].get(ref_of(inp) or "")
        if target and HONEYPOT.search(target.get("name") or ""):
            return deny("That box is a trap for bots (filling it marks you as one): leave it empty.")
        check = password_check(state, target, inp.get("text"), bool(inp.get("submit")))
        if check:
            return check if check[0] == "deny" else ("fill", [("text", check[1])])
        if inp.get("submit"):
            if workday(state) and target and target["role"] in FIELD_ROLES:
                return ("allow", "Enter in a box on Workday (its pages don't submit on Enter; Submit is its own button)")
            return ask("typing with submit=true presses Enter, which can submit the form.")
        return ALLOW
    if name == "browser_fill_form":
        plan = []
        for k, f in enumerate(inp.get("fields", [])):
            target = state["refs"].get(ref_of(f) or "")
            if target and HONEYPOT.search(target.get("name") or "") and str(f.get("value") or "").strip():
                return deny("One of those boxes is a trap for bots (filling it marks you as one): leave it empty.")
            if not target and "password" in str(f.get("name", "")).lower() and not placeholder_of(f.get("value")):
                return deny(f"Password fields take {accounts.NEW} or {accounts.SIGN_IN}, never a password itself.")
            check = password_check(state, target, f.get("value"))
            if check and check[0] == "deny":
                return check
            if check:
                plan.append((k, check[1]))
        return ("fill", plan) if plan else ALLOW
    if name == "browser_click":
        target = state["refs"].get(ref_of(inp) or "")
        if not target:
            return deny(f"Unknown element {ref_of(inp)!r}: use the element's ref (like e52) from your latest "
                        "browser_snapshot; after any click or typing, take a new snapshot first.")
        if SUBMIT_RE.match(target["name"]):
            if target["name"].strip().lower() in SIGN_IN_SUBMIT and sign_in_page(state["refs"]):
                return ("allow", "a sign-in page's own Submit (only account boxes on it): no application goes out")
            if target["name"].strip().lower() in POSTING_APPLY and posting_page(state):
                return ("allow", "the posting's own Apply (its page, nothing filled in, no form on it): opens the application")
            return ask(f"clicking {target['name']!r} may SUBMIT the application. Approve only after the user has "
                       "reviewed every page (it is fine if this only opens the form).")
        return ALLOW
    return ALLOW


def ref_of(inp):
    """The element an action targets: "ref" or (newer Playwright MCP) "target"."""
    return inp.get("ref") or inp.get("target")


def check_bash(state, command):
    if re.search(r"[;&|`$<>\n\\]", command):
        return ("Only the answer-bank command is allowed, one per call: no shell operators, loops or backslashes. A "
                "label with double quotes in it goes in single quotes: save 'If you answered \"Yes\", why?' 'answer'.")
    try:
        argv = shlex.split(command)
    except ValueError:
        return "Could not parse the command."
    if argv[:2] != state["answer_cmd"] or len(argv) < 3 or argv[2] not in BASH_COMMANDS:
        return f"The only command allowed is: {' '.join(state['answer_cmd'])} <{'|'.join(sorted(BASH_COMMANDS))}> ..."
    return None


def lookup(entries, label, options, page):
    """The answer bank's reply for `answer`: dict with status (and value). Voluntary / self-identify pages (gender,
    race, veteran, disability...) work like any other: a saved answer is used (until 2026-10-09 they were asked every
    time). page: the step's name, for the log only."""
    entry = answers.find(entries, label_key(label))
    if entry is None:
        return {"status": "unknown", "saved": saved_answers(entries)}
    if entry.get("always_ask"):
        return {"status": "ask_every_time", "why": "always_ask question"}
    if entry.get("leave_blank"):
        return {"status": "leave_blank"}
    if not entry["answer"]:
        return {"status": "unknown", "saved": saved_answers(entries)}
    if options and entry["answer"] not in options:
        return {"status": "not_an_option", "saved": entry["answer"], "options": options}
    return {"status": "answer", "value": entry["answer"]}


class Bank:
    """The answers one run sees, and where what it saves goes. A NUworks job (main tab): answers.json. An Other jobs
    tab job: answers_other.json first, then answers.json minus its NUworks-only entries (co-op dates, term); new
    answers go to answers_other.json, so NUworks runs never see them. An alias goes on the entry it names."""

    def __init__(self, other=False):
        self.main = answers.load()
        self.path = config.OTHER_ANSWERS_PATH if other else config.ANSWERS_PATH
        self.own = answers.load(self.path) if other else self.main
        self.entries = self.own + [e for e in self.main if not answers.nuworks_only(e)] if other else self.main

    def save(self, entry=None):
        """Write the file that holds entry (no entry: the one new answers go to)."""
        if entry is None or any(entry is e for e in self.own):
            answers.save(self.own, self.path)
        else:
            answers.save(self.main)

    def put(self, key, value, field_type):
        """Save an answer in this run's own bank: fill its entry for the question if it has one (a starter with no
        answer yet, or one marked leave blank), else add one."""
        e = answers.find(self.own, key)
        if e:
            e["answer"], e["field_type"], e["leave_blank"] = value, field_type, False
        else:
            self.own.append(answers.new_entry(key, value, field_type))
        self.save()

    def add(self, entry):
        self.own.append(entry)
        self.save()


def saved_answers(entries):
    """What the agent may work an unknown field out from: every saved answer, minus the always-ask and
    leave-blank ones (those are never reused under another wording)."""
    return [{"question": e["question"], "answer": e["answer"]} for e in entries
            if e["answer"] and not e.get("always_ask") and not e.get("leave_blank")]


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


# ---------- side by side: up to SLOTS sessions, each its own browser profile; one per job site ----------

SLOTS = 3  # local_config.json "assist_slots" (1-3): how many assistant sessions may run at once


def slots():
    n = config.LOCAL.get("assist_slots", SLOTS)
    return n if isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= SLOTS else SLOTS


def slot_profile(n):
    """Slot 1 keeps the profile the assistant always had (your company-site logins); the others get their own."""
    return config.ASSIST_PROFILE_DIR if n == 1 else f"{config.ASSIST_PROFILE_DIR}_{n}"


def _lock(path, info=None):
    """An exclusive lock on path for as long as the returned file stays open (None if another process holds it). With
    info, what holds it is written in (sessions() reads it)."""
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    f = open(path, "a+")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        return None
    if info is not None:
        f.seek(0)
        f.truncate()
        f.write(json.dumps(info))
        f.flush()
    return f


def take_slot(info):
    """(slot number, its lock) for a new session, or (None, None) when every slot is in use."""
    for n in range(1, slots() + 1):
        f = _lock(os.path.join(config.LOCAL_DIR, f"assist_slot{n}.lock"), {**info, "slot": n, "pid": os.getpid()})
        if f:
            return n, f
    return None, None


def take_site(url):
    """One session per job site at a time (two could make the same account at once). The lock, or None if busy."""
    return _lock(os.path.join(config.LOCAL_DIR, "assist_sites", re.sub(r"[^a-z0-9.-]", "_", host(url) or "none") + ".lock"))


def sessions():
    """The assistant sessions running now: [{"slot", "row", "tab", "company", "host", "since", "pid"}] (lock files
    held by a live session; a crashed one's lock is free again)."""
    out = []
    for n in range(1, SLOTS + 1):
        path = os.path.join(config.LOCAL_DIR, f"assist_slot{n}.lock")
        if not os.path.exists(path):
            continue
        with open(path) as f:
            try:
                fcntl.flock(f, fcntl.LOCK_SH | fcntl.LOCK_NB)  # free = nobody runs in this slot
                fcntl.flock(f, fcntl.LOCK_UN)
                continue
            except BlockingIOError:
                pass
            try:
                out.append(json.loads(f.read() or "{}"))
            except ValueError:
                out.append({"slot": n})
    return out


# ---------- answer-bank commands (run by the agent through Bash) ----------

def bank(argv):
    """One answer-bank command. Sessions run side by side: each command holds the bank's lock while it reads and
    saves, so two sessions' answers never overwrite each other."""
    if argv[:1] == ["wait"]:
        return _bank(argv)
    os.makedirs(config.LOCAL_DIR, mode=0o700, exist_ok=True)
    with open(os.path.join(config.LOCAL_DIR, "answers.lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _bank(argv)


def _bank(argv):
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
    out = {}
    with state_file(os.path.join(d, "state.json")) as state:
        b = Bank(other=state.get("tab") == "other")
        entries = b.entries
        if a.cmd == "answer":
            out = lookup(entries, a.label, a.option, a.page or state.get("page", ""))
            if out["status"] == "answer":
                issue(state, a.label, out["value"])
        elif a.cmd in ("save", "once"):
            if a.value is None:
                out = {"error": f"{a.cmd} needs a value"}
            elif why := valid_answer(a.value, a.option):
                out = {"error": why}
            elif a.cmd == "save" and (answers.find(entries, label_key(a.label)) or {}).get("always_ask"):
                out = {"error": "This question is asked every time: use once, not save."}
            else:
                if a.cmd == "save":
                    e = answers.find(entries, label_key(a.label))
                    if e and e["answer"] and not e.get("leave_blank"):
                        out = {"error": f"Already saved as {e['answer']!r}; the user changes it in the answer bank."}
                    else:
                        b.put(label_key(a.label), a.value, "select" if a.option else "text")
                if not out:
                    issue(state, a.label, a.value)
                    out = {"status": "answer", "value": a.value, "saved": a.cmd == "save"}
        elif a.cmd == "alias":
            target = answers.find(entries, a.value or "")
            if not target:
                out = {"error": f"No saved question {a.value!r}."}
            else:
                target.setdefault("aliases", []).append(label_key(a.label))
                b.save(target)
                out = lookup(entries, a.label, a.option, a.page)
                if out["status"] == "answer":
                    issue(state, a.label, out["value"])
        elif a.cmd == "blank":
            if answers.find(entries, label_key(a.label)):
                out = {"error": "That question already has an entry; edit answers.json by hand."}
            else:
                e = answers.new_entry(label_key(a.label), "", "text")
                e["leave_blank"] = True
                b.add(e)
                out = {"status": "leave_blank", "saved": True}
    log(d, f"bank {a.cmd} {a.label!r} -> {out.get('status') or out.get('error')}")
    print(json.dumps(out))


# ---------- hooks (Claude Code runs these for every browser / Bash call) ----------

def fill(state, inp, plan):
    """The tool input with the site's real password in place of each placeholder (accounts.password_for: NEW makes
    one when the site has none). Remembers the site, so the replies can be cleaned of it. NoAccount if SIGN_IN has
    no saved login there."""
    h = host(state.get("current_url", ""))
    job = {"title": state.get("job_title", ""), "url": state.get("job_url", "")}
    new = json.loads(json.dumps(inp))
    for where, ph in plan:
        pw = accounts.password_for(h, ph, company=state.get("company", ""), job=job)
        if where == "text":
            new["text"] = pw
        else:
            new["fields"][where]["value"] = pw
    if h not in state.setdefault("password_sites", []):
        state["password_sites"].append(h)
    return new


def redact(obj, secrets_):
    """obj (a tool's reply: the code it ran, the page's snapshot, which shows what a password box holds) with every
    one of these passwords replaced by HIDDEN; and whether anything was."""
    if isinstance(obj, str):
        out = obj
        for s in secrets_:
            out = out.replace(s, HIDDEN)
        return out, out != obj
    if isinstance(obj, list):
        pairs = [redact(v, secrets_) for v in obj]
        return [p[0] for p in pairs], any(p[1] for p in pairs)
    if isinstance(obj, dict):
        pairs = {k: redact(v, secrets_) for k, v in obj.items()}
        return {k: p[0] for k, p in pairs.items()}, any(p[1] for p in pairs.values())
    return obj, False


def hook(kind):
    d = run_dir()
    data = json.load(sys.stdin)
    tool, inp = data.get("tool_name", ""), data.get("tool_input") or {}
    with state_file(os.path.join(d, "state.json")) as state:
        if kind == "pre":
            verdict, why = decide(state, tool, inp)
            if verdict != "deny" and tool.split("__")[-1] in INPUT_TOOLS:
                state["filled"] = True  # something is filled in now: from here on a posting's "Apply" asks again
            if verdict == "fill":  # a password placeholder: the guard types the real one (the agent never sees it)
                try:
                    new = fill(state, inp, why)
                except accounts.NoAccount as e:
                    verdict, why = deny(f"{e}.")
                else:
                    log(d, f"FILL  {tool} {json.dumps(inp)[:300]}  (the site's password typed by NUauto)")
                    print(json.dumps({"hookSpecificOutput": {
                        "hookEventName": "PreToolUse", "permissionDecision": "allow", "updatedInput": new,
                        "permissionDecisionReason": "NUAUTO: NUauto typed this site's password (the agent never sees it)"}}))
                    return
            log(d, f"{verdict.upper():5} {tool} {json.dumps(inp)[:300]}" + (f"  ({why})" if why else ""))
            if verdict == "deny":
                print(why, file=sys.stderr)
                sys.exit(2)
            if verdict == "ask":  # Claude Code shows the user a yes/no confirmation in the terminal
                print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                                         "permissionDecisionReason": why}}))
            return
        response, changed = data.get("tool_response"), False
        if state.get("password_sites"):  # the browser tool echoes what it typed ("Ran Playwright code"): never the password
            response, changed = redact(response, accounts.passwords(state["password_sites"]))
        with open(os.path.join(d, "last_response.json"), "w") as f:  # for debugging the guard (local log folder)
            json.dump(response, f)
        update_after(state, tool, "\n".join(strings(response)))
        if changed:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": response,
                                                     "updatedMCPToolOutput": response}}))


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
    from nuauto import sheet
    ws = sheet.open_worksheet()
    rows = sheet.read_rows(ws)
    row = next((r for r in rows if r.number == number), None)
    if row is None or row.status != "Needs Human":
        sys.exit(f"Row {number} is not a Needs Human row.")
    url, why = assist_target(row.notes, url_override)
    if why:
        sys.exit(f"Row {number} ({row.company}): {why}.")
    return rows, row, url


def find_other_row(number, url_override):
    from nuauto import sheet
    rows = sheet.read_rows(sheet.open_other())
    row = next((r for r in rows if r.number == number), None)
    if row is None:
        sys.exit(f"The {sheet.OTHER_TAB} tab has no row {number}.")
    url, why = other_target(row, url_override)
    if why:
        sys.exit(f"{sheet.OTHER_TAB} row {number} ({row.company}): {why}.")
    return rows, row, url


OTHER_NOTE = ("This job is not on NUworks (the sheet's Other jobs tab): nothing is submitted on NUworks afterwards. "
              "Its answer bank leaves out the NUworks co-op answers (start / end dates, co-op term): when the form "
              "asks one, ask the user.\n")


def answer_prefix():
    """How the agent and Claude Code's hooks run this file: [python, assist.py] from a source install, [the app,
    "_assist"] when packaged. Always two words (check_bash compares exactly these)."""
    if config.FROZEN:
        return [sys.executable, "_assist"]
    return [sys.executable, os.path.abspath(__file__)]


def claude_bin():
    return config.tool_path("claude") or "claude"


def session_log_dir(cwd):
    """Where Claude Code keeps the session logs of sessions started in cwd."""
    home = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    return os.path.join(home, "projects", re.sub(r"[^A-Za-z0-9-]", "-", os.path.realpath(cwd)))


def scrub(folder, since, secrets_):
    """Replace these passwords with HIDDEN in every file under folder written since `since`. Claude Code keeps every
    hook's output in its session log (the guard's answer holds the password it typed), and Playwright MCP may save
    page snapshots (which show what a password box holds) in the run's folder. Returns how many files were cleaned."""
    if not secrets_:
        return 0
    cleaned = 0
    for root, _, files in os.walk(folder):
        for name in files:
            p = os.path.join(root, name)
            try:
                if os.path.getmtime(p) < since or os.path.getsize(p) > 200_000_000:
                    continue
                with open(p, encoding="utf-8", errors="surrogateescape") as f:
                    text = f.read()
                new = text
                for s in secrets_:
                    new = new.replace(s, HIDDEN)
                if new != text:
                    tmp = p + ".nuauto-tmp"
                    with open(tmp, "w", encoding="utf-8", errors="surrogateescape") as f:
                        f.write(new)
                    os.chmod(tmp, os.stat(p).st_mode & 0o777)
                    os.replace(tmp, p)
                    cleaned += 1
            except OSError:
                continue
    return cleaned


def after_session(d, started, log_):
    """When the agent's session ends: the site passwords it used go out of Claude Code's session log and the run's
    folder (Playwright MCP's saved snapshots)."""
    try:
        with open(os.path.join(d, "state.json")) as f:
            sites = json.load(f).get("password_sites") or []
        secrets_ = accounts.passwords(sites)
        n = scrub(session_log_dir(config.STATE_DIR), started - 60, secrets_) + scrub(d, started - 60, secrets_)
        if n:
            log_.write(f"Took this run's site passwords out of the session logs ({n} file{'s' if n > 1 else ''}).")
    except Exception as e:  # never in the way of the y/n question that follows
        log_.write(f"Could not clean the session logs ({type(e).__name__}).")


def effort():
    """The agent's reasoning effort (local_config.json "assist_effort"; Claude Code's --effort levels)."""
    e = str(config.LOCAL.get("assist_effort") or EFFORT)
    return e if e in ("low", "medium", "high", "xhigh", "max") else EFFORT


def rules_text(answer_cmd):
    with open(os.path.join(config.PROJECT_DIR, "prompts", "ASSIST_PROMPT.md")) as f:
        return f.read().replace("ANSWER ", " ".join(shlex.quote(a) for a in answer_cmd) + " ")


def context_text(where, row, url, other, resume, read_roots, answer_cmd):
    email = accounts.account_email()
    return (f"\n\nTHIS RUN\n{where.capitalize()}: {row.company} | {row.title}\nPosting: {url}\n"
            + (OTHER_NOTE if other else "") +
            f"Resume file (the only file you may upload): {resume or 'not found'}\n"
            f"Account email (for any account a site needs): {email or 'not set: ask the user'}\n"
            "If you have no browser tools yet, they are still connecting: run the answer-bank command with "
            "just `wait` (it pauses 5 s), then look again (up to 6 times) before telling the user.\n"
            f"Resume and notes you may read (Read / Glob / Grep): {', '.join(read_roots) or 'none'}\n"
            "Answer bank command: "
            f"{' '.join(shlex.quote(a) for a in answer_cmd)} <answer|save|once|alias|blank> ...")


def session(d, answer_cmd, read_roots, profile, prompt, where):
    """The `claude` command for one agent session in run folder d (and its mcp.json / settings.json there): Sonnet at
    effort(), the browser (its profile), the answer bank, web lookups, the notes; every browser, Bash, file and web call
    goes through the guard (hook pre), every browser reply through hook post; a guard that fails or times out blocks."""
    mcp = {"mcpServers": {SERVER: {"command": "npx", "args": [MCP_PACKAGE, "--user-data-dir", profile, "--output-dir", d]}}}
    hook_cmd = " ".join(shlex.quote(a) for a in answer_cmd) + " hook"
    guard = lambda kind: [{"type": "command", "command": f"{hook_cmd} {kind}", "timeout": 60, "onFailure": "block"}]  # noqa: E731
    settings = {"hooks": {
        "PreToolUse": [{"matcher": f"mcp__{SERVER}__.*|Bash|Read|Glob|Grep|WebSearch|WebFetch", "hooks": guard("pre")}],
        "PostToolUse": [{"matcher": f"mcp__{SERVER}__.*", "hooks": guard("post")}]}}
    for name, obj in (("mcp.json", mcp), ("settings.json", settings)):
        with open(os.path.join(d, name), "w") as f:
            json.dump(obj, f, indent=1)
    # Claude Code's regular (manual) mode, whatever your own default is: the tools below are approved up front and the
    # guard decides the rest (it asks before anything that may submit). Auto mode's own check asked about every click.
    return [claude_bin(), "--model", "sonnet", "--effort", effort(), "--permission-mode", "default", "--strict-mcp-config",
            "--mcp-config", os.path.join(d, "mcp.json"), "--settings", os.path.join(d, "settings.json"),
            "--tools", "Bash", "Read", "Glob", "Grep", "WebSearch", "WebFetch",
            *[a for r in read_roots for a in ("--add-dir", r if os.path.isdir(r) else os.path.dirname(r))],
            "--allowedTools", f"mcp__{SERVER}", f"Bash({answer_cmd[0]} {answer_cmd[1]}:*)", "Read", "Glob", "Grep",
            "WebSearch", "WebFetch",
            "--append-system-prompt", prompt,
            f"Go: open the posting and fill the application for {where}, following the rules."]


def run(number, url_override, other=False):
    """other: a row of the Other jobs tab (no weekly / total cap, nothing sent to NUworks afterwards)."""
    from nuauto import browser
    from nuauto import sheet
    if not sys.stdin.isatty():
        sys.exit("nuauto assist needs a real terminal (you talk to the agent and confirm Submit).")
    if other:
        rows, row, url = find_other_row(number, url_override)
        where = f"{sheet.OTHER_TAB} row {number}"
    else:
        rows, row, url = find_row(number, url_override)
        sheet.check_limits(rows)
        where = f"row {number}"
    tab = "other" if other else "jobs"
    # side by side (up to slots() at once): this row in one session only, a free slot (its own browser profile), and
    # this job site in one session only. The locks are held until this run ends (after the y/n question).
    row_lock = _lock(os.path.join(config.LOCAL_DIR, "assist_rows", f"{tab}-{number}.lock"))
    if row_lock is None:
        sys.exit(f"An assistant is already working on {where}: finish it in its own window first.")
    slot, slot_lock = take_slot({"row": number, "tab": tab, "company": row.company, "host": host(url),
                                 "since": __import__("datetime").datetime.now().isoformat(timespec="minutes")})
    if slot_lock is None:
        sys.exit(f"{slots()} assistants are running already (the most at once). Finish one (/exit in its window), "
                 "then start this one.")
    site_lock = take_site(url)
    if site_lock is None:
        sys.exit(f"Another assistant is working on {host(url)} right now (two could make the same account there at "
                 "once). Start this one when it is done.")
    log_ = browser.RunLog(f"assist_{'other_' if other else ''}row{number}")
    d = log_.dir
    answer_cmd = answer_prefix()
    read_roots = [os.path.realpath(r) for r in [*config.ASSIST_READ_PATHS, config.LAPTOP_RESUME] if os.path.exists(r)]
    resume = upload_copy(config.LAPTOP_RESUME, d) if os.path.exists(config.LAPTOP_RESUME) else None
    state = {"current_url": url, "refs": {}, "issued": {}, "issued_values": [], "answer_cmd": answer_cmd,
             "read_roots": read_roots, "protected": protected_dirs(read_roots),
             "resume_src": os.path.realpath(config.LAPTOP_RESUME), "resume": resume, "tab": "other" if other else "jobs",
             "company": row.company, "job_title": row.title, "job_url": url, "password_sites": []}
    with open(os.path.join(d, "state.json"), "w") as f:
        json.dump(state, f)
    profile = slot_profile(slot)
    os.makedirs(profile, mode=0o700, exist_ok=True)
    cmd = session(d, answer_cmd, read_roots, profile, rules_text(answer_cmd) + context_text(
        where, row, url, other, resume, read_roots, answer_cmd), where)
    log_.write(f"{where.capitalize()}: {row.company} | {row.title} -> {host(url)} (assistant slot {slot} of {slots()})")
    log_.write("Starting the agent. Captchas, email codes and Submit are yours. Type /exit in the session when done.")
    import time
    started = time.time()
    try:
        subprocess.run(cmd, env={**os.environ, STATE_ENV: d}, cwd=config.STATE_DIR)
    except KeyboardInterrupt:
        pass
    finally:
        after_session(d, started, log_)
    try:
        while True:
            reply = input(f"\nDid you press Submit for {where} ({row.company}) and see the confirmation? "
                          "[y = yes / n = no]: ").strip().lower()
            if reply in ("y", "n"):
                break
    except (KeyboardInterrupt, EOFError):
        reply = "n"
    if reply != "y":
        log_.write(f"Not submitted; {where} stays {row.status}.")
        return
    ws = sheet.open_other() if other else sheet.open_worksheet()
    sheet.mark_applied_by_hand(ws, row.number, row.url, how="on the company site (nuauto assist; you pressed Submit)")
    log_.write(f"{where.capitalize()} marked Applied (dated today).")
    if not other:  # an Other jobs tab job is not on NUworks: nothing more to send
        nuworks_side(ws, row.number)


def nuworks_side_blocked(notes):
    """Why the NUworks side must not be (re)submitted, from the row's Notes; None = go ahead. Once Submit was
    clicked there, only the user can tell whether it went through (the older notes are matched too)."""
    if "NUworks side submitted" in notes:
        return "the NUworks side is already submitted."
    if any(m in notes for m in ("NUworks side: Submit clicked", "after Submit was clicked")):
        return ("Submit was already clicked on NUworks once; check NUworks by hand. If it did not go through, "
                "delete that note in the sheet and run this again.")
    return None


def nuworks_side(ws, number, io=None):
    """Company site done -> submit the same job on NUworks too (apply.submit_nuworks_side: tested NUworks
    code, not the agent). io: answers.JsonIO when the GUI runs it."""
    from nuauto import apply
    from nuauto import sheet
    row = next((r for r in sheet.read_rows(ws) if r.number == number), None)
    if row is None or row.status != "Applied":
        sys.exit(f"Row {number} is not Applied; the NUworks side runs only after the company site is done.")
    blocked = nuworks_side_blocked(row.notes)
    if blocked:
        sys.exit(f"Row {number}: {blocked}")
    print(f"\nNow submitting row {number} on NUworks too (Ctrl+C stops)...")
    print(apply.submit_nuworks_side(row, ws, io))


def list_rows():
    from nuauto import sheet
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


def list_other():
    from nuauto import sheet
    ready = [(r, *other_target(r)) for r in sheet.read_rows(sheet.open_other()) if r.status == "Approved"]
    print(f"{sheet.OTHER_TAB} tab, Approved (nuauto assist other <row>):" if ready else
          f"No Approved rows in the {sheet.OTHER_TAB} tab.")
    for r, u, why in ready:
        print(f"  row {r.number:<3} {r.company[:25]:25} {r.title[:38]:38} {host(u) if u else why}")


def add_other(url, company, title, interactive=True):
    """A job that is not on NUworks -> the Other jobs tab as Approved (you adding it is the go-ahead); the tab is made
    if the sheet has none. Returns its row number. SheetError if the link is not one the agent may open, or is there.
    interactive=False (the GUI): never opens Google's login page. (manage.add: the same for either tab.)"""
    from nuauto import manage
    return manage.add("other", url, company, title, interactive=interactive)


def main_other(args, url):
    """nuauto assist other [<row> | add <url> <company> <title>]"""
    from nuauto import sheet
    try:
        if not args:
            return list_other()
        if len(args) == 1 and args[0].isdigit():
            return run(int(args[0]), url, other=True)
        if len(args) == 4 and args[0] == "add":
            n = add_other(*args[1:])
            return print(f"Added as {sheet.OTHER_TAB} row {n} (Approved). Start the agent: nuauto assist other {n}")
    except sheet.SheetError as e:
        sys.exit(str(e))
    sys.exit(__doc__)


def main(argv):
    args, url, ui = list(argv), None, None
    for flag in ("--url", "--ui"):
        if flag in args:
            i = args.index(flag)
            if i + 1 >= len(args):
                sys.exit(__doc__)
            if flag == "--url":
                url = args[i + 1]
            else:
                ui = args[i + 1]
            del args[i:i + 2]
    if ui not in (None, "json"):
        sys.exit(__doc__)
    if not args:
        return list_rows()
    if args[0] == "other":
        return main_other(args[1:], url)
    if len(args) == 2 and args[0] == "nuworks" and args[1].isdigit():  # retry / catch up the NUworks side
        from nuauto import apply
        from nuauto import sheet
        apply.refuse_unattended(ui)  # a real NUworks submit: you at a terminal or the GUI, never an agent
        return nuworks_side(sheet.open_worksheet(), int(args[1]), answers.JsonIO() if ui == "json" else None)
    if len(args) == 1 and args[0].isdigit():
        from nuauto import sheet
        try:
            return run(int(args[0]), url)
        except sheet.LimitReached as e:
            sys.exit(str(e))
    sys.exit(__doc__)


def cli(argv):
    """This file's command line: `hook pre|post` (Claude Code's hooks), the answer-bank commands, else main()."""
    if argv[:1] == ["hook"] and argv[1:2] in (["pre"], ["post"]):
        hook_main(argv[1])
    elif argv[:1] and argv[0] in BASH_COMMANDS:
        bank(argv)
    else:
        main(argv)


if __name__ == "__main__":
    cli(sys.argv[1:])
