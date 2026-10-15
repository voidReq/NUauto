"""Offline checks for doctor.check_remote_assist (what the homelab's assistant needs: tools, Playwright's Firefox, Claude
Code's folder trust, the review page's port) and for the line in server() that runs it only with remote_assist.
Run: python tests/test_doctor_remote.py

Nothing real is looked at: HOME is a temp folder, shutil.which and the novnc paths are stand-ins, the review page is
a socket in this process on 127.0.0.1:8766 (that part is skipped, with a note, when something else holds the port).
"""
import json
import os
import socket
import subprocess
import tempfile

STATE = tempfile.mkdtemp(prefix="nuauto-test-doctor-")
HOME = os.path.join(STATE, "home")
os.makedirs(HOME)
os.environ["NUAUTO_STATE_DIR"] = STATE
os.environ["HOME"] = HOME

from nuauto import config  # noqa: E402

assert config.LOCAL_DIR == os.path.join(STATE, "local") and not config.DEMO
from nuauto import doctor  # noqa: E402

assert os.path.expanduser("~") == HOME
doctor.JSON = True  # no printing: results only

TOOLS = ("Xvfb", "x11vnc", "websockify", "npx", "tmux")
installed = set(TOOLS)
novnc_at = {"/usr/share/novnc/vnc.html"}
real_which, real_exists = doctor.shutil.which, os.path.exists
doctor.shutil.which = lambda name, *a, **kw: f"/fake/bin/{name}" if name in installed else None
os.path.exists = lambda p: (p in novnc_at) if str(p).endswith("/vnc.html") else real_exists(p)
config.LOCAL = {"web_listen_host": "127.0.0.1"}
PORT_FREE = True
try:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 8766))
    probe.close()
except OSError:
    PORT_FREE = False


def run():
    """What check_remote_assist and check_remote_app (NUauto's page) say now: [(level, msg)]."""
    doctor.results.clear()
    doctor.fails.clear()
    doctor.check_remote_assist()
    doctor.check_remote_app()
    return [(r["level"], r["msg"]) for r in doctor.results]


def levels(out):
    return [lv for lv, _ in out]


def only(out, level):
    return [m for lv, m in out if lv == level]


def firefox(there):
    path = os.path.join(HOME, ".cache", "ms-playwright", "firefox-1490")
    if there:
        os.makedirs(path, exist_ok=True)
    elif os.path.isdir(path):
        os.rmdir(path)


def claude_json(content):
    path = os.path.join(HOME, ".claude.json")
    if content is None:
        if os.path.exists(path):
            os.remove(path)
    else:
        with open(path, "w") as f:
            f.write(content if isinstance(content, str) else json.dumps(content))


def trusted(value, key=None):
    return {"projects": {key or os.path.realpath(config.STATE_DIR): {"hasTrustDialogAccepted": value}}}


# ---- the tools: everything missing is one FAIL that names each; one missing names just it; all there is "ok"
installed.clear()
novnc_at.clear()
out = run()
msg = only(out, "FAIL")[0]
assert all(t in msg.split(":")[0] for t in TOOLS) and "novnc" in msg.split(":")[0] and "apt install" in msg, msg
assert doctor.fails and msg in doctor.fails  # a FAIL makes the doctor's exit status bad
installed.update(TOOLS)
novnc_at.add("/usr/share/novnc/vnc.html")
firefox(True)
claude_json(trusted(True))
out = run()
assert not any(m.startswith("the assistant needs") for m in only(out, "FAIL")), out
assert "the assistant's virtual screen and browser view tools are installed" in only(out, "ok")
installed.discard("tmux")
assert only(run(), "FAIL")[0].startswith("the assistant needs tmux:"), run()
named = only(run(), "FAIL")[0].split(":")[0]  # (the apt hint after it names every package)
assert "novnc" not in named and "Xvfb" not in named, named
installed.add("tmux")
for tool in TOOLS:  # each one alone
    installed.discard(tool)
    msg = only(run(), "FAIL")[0]
    assert f"needs {tool}:" in msg, (tool, msg)
    installed.add(tool)
novnc_at.clear()  # noVNC: either distribution's folder counts
assert "needs novnc:" in only(run(), "FAIL")[0]
novnc_at.add("/usr/share/webapps/novnc/vnc.html")
assert not any("needs" in m for m in only(run(), "FAIL"))
novnc_at.add("/usr/share/novnc/vnc.html")

# ---- Playwright's Firefox (the assistant's browser): the cache folder under HOME
firefox(False)
msg = only(run(), "FAIL")[0]
assert "no Playwright Firefox" in msg and "playwright install firefox" in msg, msg
os.makedirs(os.path.join(HOME, ".cache", "ms-playwright", "chromium-1"), exist_ok=True)  # another browser is not it
assert any("no Playwright Firefox" in m for m in only(run(), "FAIL"))
firefox(True)
assert not any("Playwright" in m for m in only(run(), "FAIL"))

# ---- Claude Code's folder trust: a WARN (never a FAIL) unless the folder is recorded as trusted
for content, why in ((None, "no ~/.claude.json"), ("{not json", "unreadable"), ({}, "no projects"),
                     ({"projects": {}}, "folder not listed"), ({"projects": {config.STATE_DIR + "-other": {"hasTrustDialogAccepted": True}}},
                      "another folder"), (trusted(False), "refused"), ({"projects": {os.path.realpath(config.STATE_DIR): {}}}, "no flag"),
                     ({"projects": {os.path.realpath(config.STATE_DIR): None}}, "null entry")):
    claude_json(content)
    out = run()
    warns = only(out, "WARN")
    assert len(warns) == 1 and "trust" in warns[0] and config.STATE_DIR in warns[0], (why, out)
    assert not any("trust" in m for m in only(out, "FAIL")), why
claude_json(trusted(True))
assert only(run(), "WARN") == []
# the key is the folder's real path (a symlinked state folder is recorded under its target)
link = os.path.join(STATE, "link")
os.symlink(STATE, link)
real_state = config.STATE_DIR
config.STATE_DIR = link
try:
    claude_json(trusted(True, os.path.realpath(link)))
    assert only(run(), "WARN") == []
    claude_json(trusted(True, link))
    assert len(only(run(), "WARN")) == 1
finally:
    config.STATE_DIR = real_state
claude_json(trusted(True))

# ---- the review page: port 8766 on web_listen_host
if PORT_FREE:
    assert any(":8766" in m and "does not answer" in m for m in only(run(), "FAIL"))   # nothing listens
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 8766))
    srv.listen(5)
    try:
        out = run()
        assert only(out, "FAIL") == [], out
        assert "NUauto's page answers on 127.0.0.1:8766" in only(out, "ok"), out
        assert only(out, "WARN") == []
    finally:
        srv.close()
    assert any("does not answer" in m for m in only(run(), "FAIL"))  # and closed again
    config.LOCAL = {}                                              # no web_listen_host: an error is a FAIL, not a crash
    assert any("does not answer" in m for m in only(run(), "FAIL"))
    config.LOCAL = {"web_listen_host": "127.0.0.1"}
else:
    print("note: something holds 127.0.0.1:8766 here; the review-page sub-check was skipped")

# ---- server() runs the assistant's checks only with remote_assist; the page's with remote_app or remote_assist
calls = []
real = {n: getattr(doctor, n) for n in ("sh", "check_files", "check_google_login", "check_web_fresh", "check_claude",
                                        "check_remote_assist", "check_remote_app")}
real_is_server = config.IS_SERVER
try:
    doctor.sh = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "yes" if "show-user" in cmd else "success"
                                                              if "show" in cmd else "enabled" if "is-enabled" in cmd else "active", "")
    for name in ("check_files", "check_google_login", "check_web_fresh", "check_claude"):
        setattr(doctor, name, lambda *a, **kw: None)
    doctor.check_remote_assist = lambda: calls.append("assist")
    doctor.check_remote_app = lambda: calls.append("app")
    config.IS_SERVER = True
    for local, expected in (({}, []), ({"remote_assist": False}, []), ({"remote_assist": True}, ["assist", "app"]),
                            ({"remote_app": True}, ["app"])):
        calls.clear()
        config.LOCAL = local
        doctor.server()
        assert calls == expected, (local, calls)
finally:
    config.IS_SERVER = real_is_server
    for n, f in real.items():
        setattr(doctor, n, f)
    doctor.shutil.which = real_which
    os.path.exists = real_exists

print("All doctor remote-assist checks passed.")
