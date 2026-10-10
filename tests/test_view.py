"""`nuauto gui --view`: your real data, read-only (gui.py VIEW). Run: python test_view.py

Runs in a temp state folder (never your real files; no sheet there, so only the offline parts show). Checks the
rules that make it safe for an agent: it may start inside a Claude Code shell, every POST but Quit is refused (each
real route, and one that doesn't exist), no child process can start, answers and run logs are hidden, no lock file
(the running GUI's is never touched), no window; and the real GUI still refuses inside a Claude Code shell.
"""
import http.client
import json
import os
import subprocess
import sys
import tempfile
import time

STATE = tempfile.mkdtemp(prefix="nuauto-test-view-")
os.makedirs(os.path.join(STATE, "local", "x"), exist_ok=True)
os.environ["NUAUTO_STATE_DIR"] = STATE
ENV = {**os.environ, "NUAUTO_STATE_DIR": STATE, "CLAUDECODE": "1", "PYTHONUNBUFFERED": "1"}
ENV.pop("NUAUTO_DEMO", None)
PY = sys.executable
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL = os.path.join(STATE, "local")

from nuauto import answers, config, gui  # noqa: E402  (after the state folder is set)

assert config.STATE_DIR == STATE and not config.DEMO
answers.save([{"question": "address", "answer": "1 Secret Street", "field_type": "text", "date_added": "2026-01-01",
               "always_ask": False, "aliases": ["street"]}])
os.makedirs(os.path.join(config.LOGS_DIR, "run1"), exist_ok=True)
open(os.path.join(config.LOGS_DIR, "run1", "actions.log"), "w").write("private log line\n")

# the real GUI (no --view) still refuses inside a Claude Code shell; --view with --demo is refused too
r = subprocess.run([PY, "-m", "nuauto", "gui", "--no-open"], env=ENV, cwd=ROOT, capture_output=True, text=True, timeout=60)
assert r.returncode != 0 and "--view" in (r.stdout + r.stderr), (r.stdout, r.stderr)
r = subprocess.run([PY, "-m", "nuauto", "gui", "--view"], env={**ENV, "NUAUTO_DEMO": "1"}, cwd=ROOT, capture_output=True, text=True, timeout=60)
assert r.returncode != 0, (r.stdout, r.stderr)

lock_path = config.GUI_LOCK_PATH
open(lock_path, "w").write(json.dumps({"pid": 999999, "port": 1, "open": "x", "demo": False}))  # "another GUI": left alone
lock_before = open(lock_path).read()
p = subprocess.Popen([PY, "-m", "nuauto", "gui", "--view"], env=ENV, cwd=ROOT, stdout=subprocess.PIPE,
                     stderr=open(os.path.join(STATE, "stderr.log"), "w"), text=True)
try:
    ready = json.loads(p.stdout.readline())
    URL, PORT = ready["url"], ready["port"]
    assert ready["view"] is True and ready["demo"] is False
    TOKEN = URL.split("?t=")[1]
    HOST = f"127.0.0.1:{PORT}"

    def request(method, path, body=None):
        h = {"Host": HOST, "Cookie": f"nuauto_{PORT}={TOKEN}"}
        if method == "POST":
            h.update({"Content-Type": "application/json", "X-NUauto": "1"})
        c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=30)
        c.request(method, path, body=json.dumps(body if body is not None else {}) if method == "POST" else None, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    status, data = request("GET", "/api/state")
    assert status == 200 and json.loads(data)["view"] is True
    assert open(lock_path).read() == lock_before, "a view must not touch the running GUI's lock"

    # every POST is refused, except Quit: each real route and one that does not exist
    for path in sorted(set(gui.POST_ROUTES) - gui.VIEW_POSTS) + ["/api/nothing", "/api/window"]:
        status, data = request("POST", path, {"kind": "apply", "args": {}, "action": "add", "id": "1", "decision": "approve"})
        assert status == 403 and b"View-only" in data, (path, status, data[:200])
    # no task, so nothing ran
    assert json.loads(request("GET", "/api/task")[1])["task"] is None

    # what is personal stays hidden: saved answers (the table still shows), run logs and screenshots
    entries = json.loads(request("GET", "/api/answers")[1])["entries"]
    assert [e["question"] for e in entries] == ["address"] and "Secret" not in json.dumps(entries), entries
    assert request("GET", "/api/logs")[0] == 200
    status, data = request("GET", "/api/file?path=" + os.path.join(config.LOGS_DIR, "run1", "actions.log"))
    assert status == 403 and b"private" not in data, (status, data)
    assert open(config.ANSWERS_PATH).read().count("1 Secret Street") == 1  # the file itself is untouched

    status, _ = request("POST", "/api/quit")
    assert status == 200
    p.wait(timeout=30)
    assert open(lock_path).read() == lock_before
finally:
    if p.poll() is None:
        p.kill()

# Start (the Task class's gate) is closed in view mode whatever calls it
assert gui.VIEW is False  # this process did not start a view
gui.VIEW = True
try:
    try:
        gui.APP.start("apply", "x", [PY, "-c", "print(1)"], browser=False)
        raise AssertionError("a task started in view mode")
    except gui.Refused:
        pass
finally:
    gui.VIEW = False
print("All view-only checks passed.")
