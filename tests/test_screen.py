"""Offline checks for screen.py (the homelab's virtual screen for the assistant's browser). Run: python tests/test_screen.py

The commands and the address rules are checked everywhere; one real start / stop of a screen runs only where Xvfb,
x11vnc, websockify and noVNC are installed (else it says it was skipped).
"""
import os
import socket
import sys
import tempfile
import time
import urllib.request

from nuauto import config

tmp = tempfile.mkdtemp()
config.LOCAL_DIR = os.path.join(tmp, "local")  # the screen's pid file: never the real local/
os.makedirs(config.LOCAL_DIR, mode=0o700)
TAILSCALE = "100.64.0.7"
config.LOCAL = {**config.LOCAL, "web_listen_host": TAILSCALE}

from nuauto import screen  # noqa: E402

# the numbers: slot n draws on :40+n, shares on a VNC port that is never on the network, and is viewed on 6080+n
assert [screen.display(n) for n in (1, 2, 3)] == [":41", ":42", ":43"]
assert [screen.vnc_port(n) for n in (1, 3)] == [5941, 5943] and [screen.web_port(n) for n in (1, 3)] == [6081, 6083]

# the three programs, in start order
run_dir = os.path.join(tmp, "run")
xvfb, vnc, web = screen.commands(1, run_dir, TAILSCALE, "/usr/share/novnc")
assert xvfb[:2] == ["Xvfb", ":41"] and "-nolisten" in xvfb and xvfb[xvfb.index("-nolisten") + 1] == "tcp"
assert vnc[0] == "x11vnc" and vnc[vnc.index("-display") + 1] == ":41" and "-localhost" in vnc
assert vnc[vnc.index("-rfbport") + 1] == "5941"
assert vnc[vnc.index("-passwdfile") + 1] == "rm:" + os.path.join(run_dir, "vnc.pass")  # read, then deleted by x11vnc
assert web[0] == "websockify" and web[web.index("--web") + 1] == "/usr/share/novnc"
assert web[-2:] == [f"{TAILSCALE}:6081", "127.0.0.1:5941"]                              # listens on Tailscale, forwards locally
assert all("0.0.0.0" not in a and a != "::" for argv in (xvfb, vnc, web) for a in argv)
assert screen.commands(3, run_dir, TAILSCALE, "/n")[2][-2:] == [f"{TAILSCALE}:6083", "127.0.0.1:5943"]

# the address: the homelab's Tailscale IP only, never every address
assert screen.listen_host() == TAILSCALE
for bad in ("", "  ", "0.0.0.0", "::", "*", None):
    config.LOCAL = {**config.LOCAL, "web_listen_host": bad}
    try:
        screen.listen_host()
    except screen.ScreenError as e:
        assert "web_listen_host" in str(e)
    else:
        raise AssertionError(f"listen_host accepted {bad!r}")
config.LOCAL = {**config.LOCAL, "web_listen_host": f" {TAILSCALE} "}
assert screen.listen_host() == TAILSCALE
config.LOCAL = {**config.LOCAL, "web_listen_host": TAILSCALE}
url = screen.view_url(1)
assert url.startswith(f"http://{TAILSCALE}:6081/vnc.html?") and "autoconnect=1" in url
assert screen.view_url(2, "127.0.0.1").startswith("http://127.0.0.1:6082/vnc.html")
pw = screen.make_password()
assert len(pw) == 8 and pw.isalnum() and screen.make_password() != pw
assert isinstance(screen.missing(), list) and set(screen.missing()) <= {"Xvfb", "x11vnc", "websockify", "novnc"}

# start() without the tools names what to install (and starts nothing)
real_missing = screen.missing
screen.missing = lambda: ["x11vnc", "novnc"]
try:
    screen.Screen(1, run_dir).start()
except screen.ScreenError as e:
    assert "x11vnc, novnc" in str(e) and "apt install" in str(e)
else:
    raise AssertionError("start did not complain about missing tools")
finally:
    screen.missing = real_missing

# one real start / stop (slot 3, on 127.0.0.1), only where the tools are installed
if screen.missing():
    print(f"(skipped the real start / stop: not installed here: {', '.join(screen.missing())})")
else:
    config.LOCAL = {**config.LOCAL, "web_listen_host": "127.0.0.1"}
    real_run = os.path.join(tmp, "realrun")
    os.makedirs(real_run)
    scr = screen.Screen(3, real_run)
    try:
        scr.start()
        assert os.path.exists("/tmp/.X11-unix/X43"), "the X socket"
        assert scr.url.startswith("http://127.0.0.1:6083/vnc.html") and scr.display == ":43" and len(scr.password) == 8
        for _ in range(30):
            try:
                with urllib.request.urlopen("http://127.0.0.1:6083/vnc.html", timeout=2) as r:
                    assert r.status == 200
                break
            except OSError:
                time.sleep(0.3)
        else:
            raise AssertionError("the view does not answer")
        assert not os.path.exists(os.path.join(real_run, "vnc.pass")), "x11vnc deletes the password file"
        with socket.create_connection(("127.0.0.1", 5943), timeout=2) as c:  # x11vnc is up (localhost only)
            assert c.recv(3) == b"RFB"
        pids = [p.pid for p in scr.procs]
        assert len(pids) == 3 and all(os.path.exists(f"/proc/{p}") for p in pids)
    finally:
        scr.stop()
    time.sleep(0.5)
    assert scr.procs == [] and not any(os.path.exists(f"/proc/{p}") for p in pids)  # stop() reaps them
    assert not os.path.exists(os.path.join(config.LOCAL_DIR, "screen3.json"))
    try:
        socket.create_connection(("127.0.0.1", 6083), timeout=1).close()
    except OSError:
        pass
    else:
        raise AssertionError("the view still answers after stop")

print("All screen checks passed.")
