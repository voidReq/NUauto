"""The laptop app's link to NUauto's page on the homelab (gui.serve_remote; docs/REMOTE.md, THE LAPTOP'S LINK).

The laptop app is one of the homelab page's paired devices, so its homelab parts work from the laptop's own window:
start an assistant session there, its sessions' screens (approve a Submit, reply, end it), an Apply run there (its
log, questions and Stop), the homelab's queue on each row. The laptop's server makes these requests itself (over
Tailscale, to the page's own address); the laptop's page only ever talks to the laptop's server.

The device key: made once over ssh (a one-time pairing code from `nuauto review pair` there, used up here at once),
kept in local/homelab_link.json (mode 600), never shown, never synced. Unpaired on the homelab (its Settings > This
homelab > Unpair): the key is forgotten here, and the link comes back only when you press Link in Settings.

  python -m nuauto.link pair | status | forget
"""
import http.client
import json
import os
import socket
import sys
import time
from urllib.parse import parse_qs, urlencode, urlparse

from nuauto import config

PATH = os.path.join(config.LOCAL_DIR, "homelab_link.json")
TIMEOUT = 20  # seconds for one request to the homelab's page


class NoLink(Exception):
    """No link (not paired, unpaired there, the page off or unreachable): the message says which."""


class Refused(Exception):
    """The homelab's page turned the request down (HTTP 409): its message, for the page here."""


def address():
    """(host, port) of the homelab's page: web_listen_host (its Tailscale IP) and the page's port."""
    from nuauto import review
    try:
        return review.listen_host(), review.PORT
    except Exception:
        raise NoLink("The homelab's address is not set (Settings > Homelab > Check).") from None


def key():
    try:
        with open(PATH) as f:
            k = json.load(f).get("key")
    except (OSError, ValueError):
        return None
    return k if isinstance(k, str) and k else None


def linked():
    return config.REMOTE_APP and not config.IS_SERVER and key() is not None


def forget():
    try:
        os.remove(PATH)
    except OSError:
        pass


def _request(method, path, body=None, headers=None, timeout=TIMEOUT):
    """(status, {header: value}, bytes). NoLink when the page can't be reached."""
    host, port = address()
    h = {"Host": f"{host}:{port}", **(headers or {})}
    try:
        c = http.client.HTTPConnection(host, port, timeout=timeout)
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        out = r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
        c.close()
        return out
    except (OSError, http.client.HTTPException):
        raise NoLink("The homelab's page can't be reached now (Tailscale up? nuauto-web on there?).") from None


def pair(name=None):
    """Link this computer's app: a one-time pairing code from the homelab over ssh, used up here at once. Returns
    the device name."""
    from nuauto import assist
    from nuauto import review
    if config.IS_SERVER or not config.REMOTE_APP:
        raise NoLink("NUauto's page on the homelab is off (Settings > Homelab > NUauto on your homelab).")
    name = (name or f"NUauto on {socket.gethostname().split('.')[0]}")[:40]
    url = review.pair_link_from_homelab(name)
    if not url:
        raise NoLink("The homelab could not be asked for a pairing code (is it reachable over ssh?).")
    code = (parse_qs(urlparse(url).query).get("c") or [""])[0]
    host, port = address()
    status, headers, _ = _request("POST", "/pair", urlencode({"c": code, "name": name}),
                                  {"Content-Type": "application/x-www-form-urlencoded", "Origin": f"http://{host}:{port}"})
    from nuauto import gui  # (the cookie's name)
    cookie = headers.get("set-cookie", "")
    prefix = gui.review_cookie() + "="
    got = cookie.split(";")[0][len(prefix):] if status == 200 and cookie.startswith(prefix) else ""
    if not got:
        raise NoLink(f"The homelab's page did not pair this computer (HTTP {status}).")
    os.makedirs(config.LOCAL_DIR, mode=0o700, exist_ok=True)
    assist.write_private(PATH, {"key": got, "name": name, "paired": time.strftime("%Y-%m-%dT%H:%M")})
    return name


def call(method, path, body=None, raw=False):
    """One request to the homelab's page as this computer's device: its JSON answer (raw: (bytes, its type)). NoLink
    without a working link; Refused with the page's own message when it says no."""
    from nuauto import gui
    k = key()
    if not k:
        raise NoLink("This app is not linked to the homelab's page (Settings > Homelab > Link this app).")
    host, port = address()
    headers = {"Cookie": f"{gui.review_cookie()}={k}"}
    data = None
    if method == "POST":
        data = json.dumps(body or {})
        headers.update({"Content-Type": "application/json", "X-NUauto": "1", "Origin": f"http://{host}:{port}"})
    status, h, out = _request(method, path, data, headers)
    if status == 403:
        try:
            unpaired = bool(json.loads(out).get("pair"))
        except ValueError:
            unpaired = False
        if unpaired:  # unpaired there: forgotten here (it links again only when you ask)
            forget()
            raise NoLink("This app was unpaired from the homelab's page: Settings > Homelab > Link this app.")
        raise NoLink("The homelab's page refused this computer (HTTP 403).")
    if status == 409:
        try:
            raise Refused(json.loads(out).get("error") or "The homelab's page said no.")
        except ValueError:
            raise Refused("The homelab's page said no.") from None
    if status == 404:
        raise NoLink("not found on the homelab")
    if status != 200:
        raise NoLink(f"The homelab's page answered HTTP {status}.")
    if raw:
        return out, h.get("content-type", "application/octet-stream")
    try:
        return json.loads(out)
    except ValueError:
        raise NoLink("The homelab's page gave an answer that could not be read.") from None


def main(argv):
    if argv == ["pair"]:
        print(f"Linked as {pair()}.")
    elif argv == ["status"]:
        print("linked" if key() else "not linked")
    elif argv == ["forget"]:
        forget()
        print("Forgotten here (unpair it on the homelab's page too: Settings > This homelab).")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
