"""Offline checks for screen.py (the homelab's virtual screen for the assistant's browser). Run: python tests/test_screen.py

The commands and the address rules are checked everywhere; one real start / stop of a screen runs only where Xvfb,
x11vnc, websockify and noVNC are installed (else it says it was skipped).
"""
import json
import os
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import zlib

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
assert xvfb[xvfb.index("-fbdir") + 1] == run_dir and screen.frame_path(run_dir) == os.path.join(run_dir, "Xvfb_screen0")
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

# the screen as a picture: a TrueColor XWD (what Xvfb -fbdir keeps) to PNG
def make_xwd(pixels, width, height, byte_order, bpp=32, depth=24, masks=(0xff0000, 0x00ff00, 0x0000ff), ncolors=0, pad=0):
    name = b"Xvfb screen\0"
    per_line = width * 4 + pad
    size = 100 + len(name)
    head = struct.pack(">25I", size, 7, 2, depth, width, height, 0, byte_order, 32, byte_order, 32, bpp, per_line, 4, *masks,
                       8, 256, ncolors, width, height, 0, 0, 0)
    body = b""
    for y in range(height):
        for x in range(width):
            r, g, b = pixels[y * width + x]
            body += struct.pack("<I" if byte_order == 0 else ">I", (r << 16) | (g << 8) | b)
        body += b"\xee" * pad
    return head + name + b"\0" * (12 * ncolors) + body


def png_pixels(png):
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    pos, chunks = 8, {}
    while pos < len(png):
        n, kind = struct.unpack(">I4s", png[pos:pos + 8])
        body = png[pos + 8:pos + 8 + n]
        assert struct.unpack(">I", png[pos + 8 + n:pos + 12 + n])[0] == zlib.crc32(kind + body) & 0xffffffff, kind
        chunks.setdefault(kind, b"")
        chunks[kind] += body
        pos += 12 + n
    w, h, depth, ctype, _, _, _ = struct.unpack(">IIBBBBB", chunks[b"IHDR"])
    assert (depth, ctype) == (8, 2) and b"IEND" in chunks
    raw = zlib.decompress(chunks[b"IDAT"])
    assert len(raw) == h * (1 + 3 * w)
    rows = [raw[y * (1 + 3 * w):(y + 1) * (1 + 3 * w)] for y in range(h)]
    assert all(r[0] == 0 for r in rows)                       # filter "none"
    return w, h, [tuple(r[1 + 3 * x:4 + 3 * x]) for r in rows for x in range(w)]


PIX = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (10, 20, 30), (200, 100, 50), (1, 2, 3)]
for order in (0, 1):                                          # LSBFirst, MSBFirst
    for pad, ncolors in ((0, 0), (8, 0), (0, 2)):
        w, h, got = png_pixels(screen.xwd_to_png(make_xwd(PIX, 3, 2, order, pad=pad, ncolors=ncolors)))
        assert (w, h) == (3, 2) and got == PIX, (order, pad, ncolors, got)
w, h, got = png_pixels(screen.xwd_to_png(make_xwd(PIX, 3, 2, 0, masks=(0x0000ff, 0x00ff00, 0xff0000))))   # BGR masks
assert got == [(b, g, r) for r, g, b in PIX]
w, h, got = png_pixels(screen.xwd_to_png(make_xwd(PIX, 3, 2, 0, depth=32)))
assert got == PIX
for bad in (dict(bpp=16, depth=16), dict(bpp=16), dict(depth=8), dict(depth=15, bpp=32)):
    try:
        screen.xwd_to_png(make_xwd(PIX, 3, 2, 0, **bad))
    except ValueError as e:
        assert "unsupported" in str(e)
    else:
        raise AssertionError(f"accepted {bad}")
shots = tempfile.mkdtemp()
assert screen.screenshot(shots) is None                                           # no frame file
frame = screen.frame_path(shots)
with open(frame, "wb") as f:
    f.write(make_xwd(PIX, 3, 2, 0))
assert png_pixels(screen.screenshot(shots))[2] == PIX
with open(frame, "wb") as f:
    f.write(make_xwd(PIX, 3, 2, 0)[:60])                                          # half written: no picture, no error
assert screen.screenshot(shots) is None
with open(frame, "wb") as f:
    f.write(make_xwd(PIX, 3, 2, 0, bpp=16, depth=16))
assert screen.screenshot(shots) is None

# start() writes view.json (mode 600), stop() removes it and the frame file: with stand-ins for the three programs
fake_run = tempfile.mkdtemp()
real = (screen.missing, screen.novnc_dir, screen.Screen._clear_stale, screen.Screen._wait_display, subprocess.Popen, time.sleep)
started = []


def fake_popen(argv, **kw):
    p = real[4](["sleep", "60"], start_new_session=True)
    p.args = argv
    started.append(argv)
    return p


screen.missing, screen.novnc_dir = (lambda: []), (lambda: "/usr/share/novnc")
screen.Screen._clear_stale = lambda self: None
screen.Screen._wait_display = lambda self: None
subprocess.Popen, time.sleep = fake_popen, (lambda s: None)
config.LOCAL = {**config.LOCAL, "web_listen_host": TAILSCALE}
fake = screen.Screen(3, fake_run)
try:
    fake.start()
    view = os.path.join(fake_run, screen.VIEW_FILE)
    assert [a[0] for a in started] == ["Xvfb", "x11vnc", "websockify"] and "-fbdir" in started[0]
    assert stat.S_IMODE(os.stat(view).st_mode) == 0o600
    assert json.load(open(view)) == {"url": f"http://{TAILSCALE}:6083/vnc.html?autoconnect=1&resize=scale&reconnect=1", "password": fake.password}
    assert fake.url == json.load(open(view))["url"]
    open(screen.frame_path(fake_run), "wb").write(b"x")
finally:
    fake.stop()
    screen.missing, screen.novnc_dir, screen.Screen._clear_stale, screen.Screen._wait_display, subprocess.Popen, time.sleep = real
assert not os.path.exists(view) and not os.path.exists(screen.frame_path(fake_run)) and not os.path.exists(os.path.join(fake_run, "vnc.pass"))
assert fake.procs == []

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
        assert os.path.exists(os.path.join(real_run, screen.VIEW_FILE)) and screen.screenshot(real_run) is not None
        with socket.create_connection(("127.0.0.1", 5943), timeout=2) as c:  # x11vnc is up (localhost only)
            assert c.recv(3) == b"RFB"
        pids = [p.pid for p in scr.procs]
        assert len(pids) == 3 and all(os.path.exists(f"/proc/{p}") for p in pids)
    finally:
        scr.stop()
    time.sleep(0.5)
    assert scr.procs == [] and not any(os.path.exists(f"/proc/{p}") for p in pids)  # stop() reaps them
    assert not os.path.exists(os.path.join(config.LOCAL_DIR, "screen3.json"))
    assert not os.path.exists(os.path.join(real_run, screen.VIEW_FILE)) and not os.path.exists(screen.frame_path(real_run))
    try:
        socket.create_connection(("127.0.0.1", 6083), timeout=1).close()
    except OSError:
        pass
    else:
        raise AssertionError("the view still answers after stop")

print("All screen checks passed.")
