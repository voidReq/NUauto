"""Demo mode end to end (demo.py): fake sheet, fake NUworks pages, fake Claude, in a temp folder. Run: python test_demo.py

The real code runs on the fakes: sheet.py on the JSON sheet, apply.py (`--ui json`, the GUI's channel) on the fake
NUworks pages in headless Firefox, the NUworks health check through the real one-click re-login. No network: the
demo browser's proxy does not exist and every non-NUworks request is aborted.
"""
import json
import os
import signal
import subprocess
import sys
import tempfile
from datetime import date

STATE = tempfile.mkdtemp(prefix="nuauto-test-demo-")
os.environ.update(NUAUTO_STATE_DIR=STATE, NUAUTO_DEMO="1", NUAUTO_DEMO_PACE="0.02")

from nuauto import answers  # noqa: E402
from nuauto import config  # noqa: E402
from nuauto import demo  # noqa: E402
from nuauto import jobs  # noqa: E402
from nuauto import sheet  # noqa: E402

ENV = {**os.environ, "PYTHONUNBUFFERED": "1"}
MARK = answers.JsonIO.MARK

# demo mode never uses the real folders, and refuses to start without its own
assert config.DEMO and config.LOCAL_DIR.startswith(STATE) and config.DATA_DIR.startswith(STATE)
r = subprocess.run([sys.executable, "-c", "import nuauto.config"], capture_output=True, text=True,
                   env={k: v for k, v in ENV.items() if k != "NUAUTO_STATE_DIR"})
assert r.returncode != 0 and "own NUAUTO_STATE_DIR" in r.stderr, r.stderr

demo.setup()
cfg = json.load(open(config.LOCAL_CONFIG_PATH))
assert cfg["tools"]["claude"].startswith(STATE)  # the fake claude, never the real one


def reload():
    """A fresh process reads local_config.json; this one keeps import-time values, so set the ones tests need."""
    config.SHEET_ID = cfg["sheet_id"]
    config.LOCAL = cfg


reload()


def rows():
    return {r.company: r for r in sheet.read_rows(sheet.open_worksheet(interactive=False))}


# the fake sheet runs the real sheet.py rules
by = rows()
assert by["Harbor Embedded"].status == "Approved" and by["Iron Valley Medical"].status == "Needs Human"
assert sheet.check_limits_safe(list(by.values())) == (5, 6)  # one applied 10 days ago: total only
ws = sheet.open_worksheet(interactive=False)
sheet.unapprove(ws, by["Quarry Hardware"].number, by["Quarry Hardware"].url)
assert rows()["Quarry Hardware"].status == "Proposed"
try:
    sheet.unapprove(ws, by["Quarry Hardware"].number, by["Quarry Hardware"].url)
    raise AssertionError("unapprove must refuse a row that is not Approved")
except sheet.SheetError:
    pass
ws.update(range_name=f"D{by['Quarry Hardware'].number}", values=[["Approved"]], value_input_option="RAW")
n = sheet.add_proposed(ws, [{"url": jobs.job_url("900106"), "company": "Fern Data Co", "title": "Data Analyst Co-op",
                             "notes": ""}], status="Approved")
assert n == 1 and rows()["Fern Data Co"].status == "Approved"
ws.update(range_name=f"A{rows()['Fern Data Co'].number}:F{rows()['Fern Data Co'].number}", values=[[""] * 6])
assert "Fern Data Co" not in rows()

# Google: an expired login never opens a login page from a check; interactively it logs in again
with open(config.GOOGLE_LOGIN_PATH, "w") as f:
    f.write("2020-01-01\n")
try:
    sheet.open_worksheet(interactive=False)
    raise AssertionError("expected NotLoggedIn")
except sheet.NotLoggedIn:
    pass
sheet.open_worksheet()  # the demo "browser login" writes a fresh token
assert open(config.GOOGLE_LOGIN_PATH).read().strip() == date.today().isoformat()
new_id = sheet.create_sheet("test")
assert new_id.startswith("DEMO-") and sheet.client(False).open_by_key(new_id).sheet1.get_all_values() == []

# review queue + job card: sheet rows never shown again; closing soon first; same text rules as the terminal
details, ratings = jobs.load_details(), jobs.load("ratings.json", {})
todo, urgent = jobs.review_queue("approve", details, ratings, sheet.read_rows(ws))
ids = [r["id"] for r in todo]
assert "900101" not in ids and "900102" not in ids and set(ids) >= {"900103", "900104", "900106"} and not urgent, ids
assert ids[-1] == "900107"  # full-stack/web always last
view = jobs.job_view(todo[0], details[todo[0]["id"]])
assert view["url"] == jobs.job_url(view["id"]) and {"header", "bullet", "text"} <= {b["kind"] for b in view["description"]}
assert jobs.text_blocks("Responsibilities:\n- Build things\nPlain line") == [
    {"kind": "header", "text": "Responsibilities"}, {"kind": "bullet", "text": "Build things"},
    {"kind": "text", "text": "Plain line"}]
rate, _ = jobs.review_queue("rate", details, {"900103": {"label": 1, "date": "2026-10-01"}}, sheet.read_rows(ws))
assert "900103" not in [r["id"] for r in rate] and len(rate) == len(todo) - 1

# the fake update brings in the held-back jobs; one closes in 5 days, so it comes first
demo.update()
todo, urgent = jobs.review_queue("approve", jobs.load_details(), ratings, sheet.read_rows(ws))
assert [r["id"] for r in urgent] == ["900109"] and todo[0]["id"] == "900109", [r["id"] for r in todo]
assert jobs.load("scans.json", [])[-1]["pool"] == 2


# ---------------------------------------------------------------- the real apply.py over the GUI's channel

def apply_run(react):
    """Start `nuauto apply --ui json` like the GUI does (own process group). react(proc, msg) answers or stops.
    Returns (exit code, log lines, protocol messages)."""
    p = subprocess.Popen([sys.executable, "-m", "nuauto", "apply", "--ui", "json"], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=ENV,
                         cwd=config.PROJECT_DIR, start_new_session=True)
    log, msgs = [], []
    for line in p.stdout:
        if line.startswith(MARK):
            msg = json.loads(line[len(MARK):])
            msgs.append(msg)
            if msg["t"] == "ask":
                react(p, msg)
        else:
            log.append(line.rstrip())
    return p.wait(timeout=120), log, msgs


def answer(p, msg):
    reply = {"id": msg["id"], "how": "new", "answer": "Yes"} if msg["kind"] == "unknown" else {"id": msg["id"], "answer": "y"}
    p.stdin.write(json.dumps(reply) + "\n")
    p.stdin.flush()


def ctrl_c(p, msg):  # what the GUI's Stop button sends: SIGINT to the run (Ctrl+C's KeyboardInterrupt path)
    os.kill(p.pid, signal.SIGINT)


def gui_gone(p, msg):  # the GUI died: its end of the pipe closes
    p.stdin.close()


# Ctrl+C while a question is open: nothing submitted for that row, it stays Approved, the run ends
code, log, msgs = apply_run(ctrl_c)
by = rows()
assert by["Harbor Embedded"].status == "Applied", by["Harbor Embedded"]  # closes soonest: went first
assert by["Lumen Security"].status == "Approved" and by["Quarry Hardware"].status == "Approved"
assert any("Ctrl+C: stopping. Nothing was submitted; row stays Approved." in line for line in log), log[-8:]
assert [m["kind"] for m in msgs if m["t"] == "ask"] == ["unknown"]

# the GUI going away mid-question stops the run the same way
code, log, msgs = apply_run(gui_gone)
assert rows()["Lumen Security"].status == "Approved"
assert any("row stays Approved" in line for line in log), log[-8:]

# answered: the new answer is saved to the bank, the row submitted; the external job stops as Needs Human
code, log, msgs = apply_run(answer)
by = rows()
assert code == 0 and by["Lumen Security"].status == "Applied" and by["Lumen Security"].date == date.today().isoformat()
assert by["Quarry Hardware"].status == "Needs Human" and by["Quarry Hardware"].notes.startswith("External application")
assert answers.find(answers.load(), "are you at least 18 years old? *")["answer"] == "Yes"
kinds = [m["kind"] for m in msgs if m["t"] == "event"]
assert "row" in kinds and "screenshot" in kinds and "row_done" in kinds, kinds
shots = [m["path"] for m in msgs if m.get("kind") == "screenshot"]
assert shots and all(p.startswith(STATE) and os.path.exists(p) for p in shots)  # screenshots before/after Submit

# nothing Approved left: the run says so and exits; the weekly cap refuses up front
code, log, msgs = apply_run(answer)
assert any("No Approved rows." in line for line in log), log
demo.set_state("cap-reached", True)
ws = sheet.open_worksheet(interactive=False)
for k in range(sheet.MAX_PER_WEEK):
    sheet.add_proposed(ws, [{"url": jobs.job_url(f"91{k:04d}"), "company": f"Cap {k}", "title": "T", "notes": ""}])
    row = rows()[f"Cap {k}"]
    ws.update(range_name=f"D{row.number}:F{row.number}", values=[["Applied", "", date.today().isoformat()]])
sheet.add_proposed(ws, [{"url": jobs.job_url("900104"), "company": "Cobalt Systems", "title": "Systems Software Co-op",
                         "notes": ""}], status="Approved")
code, log, msgs = apply_run(answer)
assert code != 0 and any("Refusing to run: Weekly limit reached" in line for line in log), log
assert rows()["Cobalt Systems"].status == "Approved"


# ---------------------------------------------------------------- health checks that run in their own process

def health(name):
    r = subprocess.run([sys.executable, "-m", "nuauto.health", "--json", name], capture_output=True, text=True,
                       env=ENV, timeout=120)
    return {c["id"]: c for c in json.loads(r.stdout)}


assert health("nuworks")["nuworks"]["status"] == "ok"
demo.set_state("nuworks-relogin", True)
c = health("nuworks")["nuworks"]
assert c["status"] == "ok" and "re-login" in c["detail"], c
assert "nuworks-relogin" not in demo.states()  # the session was renewed
demo.set_state("nuworks-password", True)
c = health("nuworks")["nuworks"]
assert (c["status"], c["fix"]) == ("fail", "login_nuworks"), c
demo.login_nuworks()
assert health("nuworks")["nuworks"]["status"] == "ok"
demo.set_state("claude-logged-out", True)
assert health("claude")["claude"]["status"] == "fail" and health("claude_live")["claude"]["status"] == "fail"
demo.set_state("claude-logged-out", False)
assert health("claude_live")["claude"]["status"] == "ok"

# doctor --json reads the same checks; one JSON object, nothing else on stdout
r = subprocess.run([sys.executable, "-m", "nuauto.doctor", "--json"], capture_output=True, text=True, env=ENV, timeout=120)
out = json.loads(r.stdout)
assert out["ok"] is True and any(c["msg"].startswith("Google:") for c in out["checks"]), out

# real (non-demo) submits refuse inside a Claude Code shell, and --ui json refuses outside the GUI
real = tempfile.mkdtemp(prefix="nuauto-test-real-")
env = {k: v for k, v in ENV.items() if k not in ("NUAUTO_DEMO", "CLAUDECODE")} | {"NUAUTO_STATE_DIR": real}
r = subprocess.run([sys.executable, "-m", "nuauto", "apply"], capture_output=True, text=True, env=env | {"CLAUDECODE": "1"})
assert r.returncode != 0 and "never runs inside a Claude Code session" in r.stderr, r.stderr
r = subprocess.run([sys.executable, "-m", "nuauto", "apply", "--ui", "json"], capture_output=True, text=True, env=env)
assert r.returncode != 0 and "only for nuauto gui" in r.stderr, r.stderr
r = subprocess.run([sys.executable, "-m", "nuauto", "assist", "nuworks", "3"], capture_output=True, text=True,
                   env=env | {"CLAUDECODE": "1"})
assert r.returncode != 0 and "never runs inside a Claude Code session" in r.stderr, r.stderr

print("All demo checks passed.")
