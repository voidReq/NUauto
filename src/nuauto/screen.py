"""A screen for the assistant's browser on the homelab, and a view of it you open on your phone (`nuauto assist` there).

The homelab has no monitor. Each assistant slot gets a virtual screen (Xvfb, display :40 + slot) that its browser
opens on, and a view of that screen in a web page: x11vnc shares the screen on 127.0.0.1 only, websockify + noVNC
serve it at http://<web_listen_host>:<6080 + slot>/vnc.html. web_listen_host is the homelab's Tailscale IP, so only
your own devices on Tailscale reach it (never the LAN, never the public Mark-done tunnel). The view asks for a password
made for this run (8 characters, the most VNC takes), shown in the session's terminal and never written to a log.
It is how you solve a captcha (you click in the real browser; the agent never touches one) and how you watch.
The browser is a plain Playwright browser on that screen: nothing about it is disguised.

Needs (once): sudo apt install xvfb x11vnc novnc   (novnc brings websockify)
"""
import json
import os
import secrets
import shutil
import signal
import string
import struct
import subprocess
import time
import zlib

from nuauto import config

DISPLAY_BASE = 40   # slot n draws on display :40+n (well clear of a desktop's :0 / :1)
WEB_BASE = 6080     # slot n's view: http://<web_listen_host>:6080+n/vnc.html
SIZE = "1366x900x24"
NOVNC_DIRS = ("/usr/share/novnc", "/usr/share/webapps/novnc")
TOOLS = ("Xvfb", "x11vnc", "websockify")
VIEW_FILE = "view.json"  # in the run folder while the screen is up: the view's link and password (mode 600)


class ScreenError(Exception):
    """The virtual screen or its view could not start (the message says what to install or set)."""


def display(slot):
    return f":{DISPLAY_BASE + slot}"


def vnc_port(slot):
    return 5900 + DISPLAY_BASE + slot


def web_port(slot):
    return WEB_BASE + slot


def listen_host():
    """The address the view listens on: the homelab's Tailscale IP (web_listen_host), never every address."""
    h = (config.LOCAL.get("web_listen_host") or "").strip()
    if not h or h in ("0.0.0.0", "::", "*"):
        raise ScreenError("Set web_listen_host in local_config.json to the homelab's Tailscale IP: the browser view "
                          "listens there only (Tailscale devices, never the LAN).")
    return h


def view_url(slot, host=None):
    return f"http://{host or listen_host()}:{web_port(slot)}/vnc.html?autoconnect=1&resize=scale&reconnect=1"


def novnc_dir():
    return next((d for d in NOVNC_DIRS if os.path.exists(os.path.join(d, "vnc.html"))), None)


def missing():
    """What has to be installed first (empty: ready)."""
    out = [t for t in TOOLS if not shutil.which(t)]
    if not novnc_dir():
        out.append("novnc")
    return out


def make_password():
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(8))


def commands(slot, run_dir, host, web_dir):
    """The three programs behind one slot's screen, in start order: [argv]. Pure (the tests check them)."""
    d, vnc = display(slot), vnc_port(slot)
    return [
        # -fbdir: the screen is also a file in the run folder (XWD), always current: screenshot() reads it.
        ["Xvfb", d, "-screen", "0", SIZE, "-nolisten", "tcp", "-fbdir", run_dir],
        # -localhost: the shared screen itself is never on the network; only websockify (below) reaches it.
        # "rm:": x11vnc reads the password file, then deletes it.
        ["x11vnc", "-display", d, "-localhost", "-rfbport", str(vnc), "-passwdfile",
         "rm:" + os.path.join(run_dir, "vnc.pass"), "-forever", "-shared", "-noxdamage", "-quiet"],
        ["websockify", "--web", web_dir, f"{host}:{web_port(slot)}", f"127.0.0.1:{vnc}"],
    ]


def _pids_path(slot):
    return os.path.join(config.LOCAL_DIR, f"screen{slot}.json")


def _alive_as(pid, name):
    """True if pid is running and is that program (not a reused pid)."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            argv = [a.decode(errors="replace") for a in f.read().split(b"\0") if a]
    except OSError:
        return False
    return any(os.path.basename(a) == name for a in argv[:2])  # websockify runs as "python3 /usr/bin/websockify"


class Screen:
    """One slot's virtual screen and its view: start(), then stop() (also when start fails part way)."""

    def __init__(self, slot, run_dir):
        self.slot, self.run_dir, self.procs = slot, run_dir, []
        self.display, self.password, self.url = display(slot), None, None

    def start(self):
        gone = missing()
        if gone:
            raise ScreenError("The browser view needs these on the homelab first: " + ", ".join(gone) +
                              ". Install them once: sudo apt install xvfb x11vnc novnc")
        host = listen_host()
        self._clear_stale()
        self.password = make_password()
        fd = os.open(os.path.join(self.run_dir, "vnc.pass"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(self.password + "\n")
        out = open(os.path.join(self.run_dir, "screen.log"), "a")
        try:
            for argv in commands(self.slot, self.run_dir, host, novnc_dir()):
                self.procs.append(subprocess.Popen(argv, stdout=out, stderr=out, stdin=subprocess.DEVNULL,
                                                   start_new_session=True))
                self._save_pids()
                if argv[0] == "Xvfb":
                    self._wait_display()
            time.sleep(1)
            dead = [p.args[0] for p in self.procs if p.poll() is not None]
            if dead:
                raise ScreenError(f"{', '.join(dead)} stopped right away (see {out.name}).")
        except BaseException:
            self.stop()
            raise
        finally:
            out.close()
        self.url = view_url(self.slot, host)
        # for the review page (it shows the link and the password to your paired devices only); gone at stop()
        fd = os.open(os.path.join(self.run_dir, VIEW_FILE), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"url": self.url, "password": self.password}, f)
        return self

    def _wait_display(self):
        sock = f"/tmp/.X11-unix/X{DISPLAY_BASE + self.slot}"
        for _ in range(50):
            if os.path.exists(sock):
                return
            if self.procs[0].poll() is not None:
                break
            time.sleep(0.1)
        raise ScreenError(f"The virtual screen {self.display} did not start (see screen.log in the run folder).")

    def _save_pids(self):
        with open(_pids_path(self.slot), "w") as f:
            json.dump([[p.args[0], p.pid] for p in self.procs], f)

    def _clear_stale(self):
        """A crashed run may have left this slot's screen running. Its slot lock is ours now, so it is nobody's:
        stop exactly the programs it recorded (by pid, and only if each is still that program)."""
        try:
            with open(_pids_path(self.slot)) as f:
                old = json.load(f)
        except (OSError, ValueError):
            old = []
        for name, pid in old:
            if _alive_as(pid, name):
                try:
                    os.killpg(pid, signal.SIGTERM)
                except OSError:
                    pass
        for name, pid in old:
            for _ in range(30):
                if not _alive_as(pid, name):
                    break
                time.sleep(0.1)
        n = DISPLAY_BASE + self.slot
        try:
            with open(f"/tmp/.X{n}-lock") as f:
                holder = int(f.read().strip())
        except (OSError, ValueError):
            holder = None
        if holder and not _alive_as(holder, "Xvfb"):  # a lock left by a screen that is gone
            for p in (f"/tmp/.X{n}-lock", f"/tmp/.X11-unix/X{n}"):
                try:
                    os.remove(p)
                except OSError:
                    pass

    def stop(self):
        for p in reversed(self.procs):
            if p.poll() is None:
                try:
                    os.killpg(p.pid, signal.SIGTERM)
                except OSError:
                    pass
        for p in self.procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except OSError:
                    pass
                try:
                    p.wait(timeout=5)  # reaped, so nothing is left behind
                except subprocess.TimeoutExpired:
                    pass
        self.procs = []
        for path in (os.path.join(self.run_dir, "vnc.pass"), os.path.join(self.run_dir, VIEW_FILE), _pids_path(self.slot),
                     frame_path(self.run_dir)):
            try:
                os.remove(path)
            except OSError:
                pass


# ---------- screenshots (the review page shows what you approve): the screen's own file, as a PNG ----------

def frame_path(run_dir):
    return os.path.join(run_dir, "Xvfb_screen0")


def xwd_to_png(data):
    """A TrueColor XWD image (what Xvfb -fbdir keeps) as PNG bytes. Pure stdlib (struct + zlib)."""
    head = struct.unpack(">25I", data[:100])
    size, depth, width, height, byte_order, bpp, per_line = head[0], head[3], head[4], head[5], head[7], head[11], head[12]
    masks, ncolors = head[14:17], head[19]
    if bpp != 32 or depth not in (24, 32):
        raise ValueError(f"unsupported XWD image ({depth}-bit, {bpp} bits per pixel)")
    start = size + 12 * ncolors
    pixels = memoryview(data)[start:start + per_line * height]

    def offset(mask):  # which byte of a pixel holds this color
        shift = (mask & -mask).bit_length() - 1
        return shift // 8 if byte_order == 0 else 3 - shift // 8
    r, g, b = (offset(m) for m in masks)
    rows = []
    for y in range(height):
        row = bytes(pixels[y * per_line:y * per_line + width * 4])
        rgb = bytearray(width * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = row[r::4], row[g::4], row[b::4]
        rows.append(b"\x00" + bytes(rgb))

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 6)) + chunk(b"IEND", b""))


def screenshot(run_dir):
    """What the session's browser shows right now (PNG bytes), or None (no screen: not on the homelab, or it stopped)."""
    try:
        with open(frame_path(run_dir), "rb") as f:
            return xwd_to_png(f.read())
    except (OSError, ValueError, struct.error):
        return None
