"""Tiny web endpoint for the "Mark done" links in Discord. Runs on the homelab (nuworks-web.service),
listening on its Tailscale address only; the Pi's Cloudflare tunnel publishes it as nuworks.example.org.

Every link is signed (HMAC with web_secret.txt) for one action on one sheet row + job, so a link can
only ever do the thing it was made for. Opening a link (GET) only shows a confirm page; nothing changes
until you press the button (POST). Discord's link previews therefore can't mark anything.

  site     Applied row: the company-site application is done (clears the SITE_MARK note)
  applied  you applied yourself (e.g. an external job that went to Needs Human): mark Applied, today
"""
import hashlib
import hmac
import html
import os
import secrets
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import config

BASE_URL = "https://nuworks.example.org"
LISTEN = ("100.64.0.1", 8765)  # homelab's Tailscale IP: not reachable from the LAN or the internet directly
SECRET_PATH = os.path.join(config.PROJECT_DIR, "web_secret.txt")
ACTIONS = {"site": "Mark the company-site application as done", "applied": "Mark as Applied (you applied yourself)"}


def _secret():
    if not os.path.exists(SECRET_PATH):
        fd = os.open(SECRET_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(32))
    with open(SECRET_PATH) as f:
        return f.read().strip().encode()


def _sig(action, row, job):
    return hmac.new(_secret(), f"{action}:{row}:{job}".encode(), hashlib.sha256).hexdigest()[:32]


def link(action, row_number, job_id):
    return f"{BASE_URL}/m?" + urlencode({"a": action, "r": row_number, "j": job_id, "s": _sig(action, row_number, job_id)})


def _log(msg):
    os.makedirs(config.LOGS_DIR, exist_ok=True)
    with open(os.path.join(config.LOGS_DIR, "web.log"), "a") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}\n")


PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>NUworks helper</title><meta name=robots content=noindex>
<style>body{{font:17px system-ui;max-width:32rem;margin:2rem auto;padding:0 1rem;color:#222;background:#fafafa}}
button{{font:inherit;padding:.7rem 1.2rem;border:0;border-radius:.5rem;background:#c8102e;color:#fff}}
.dim{{color:#777}}</style>{body}"""


class Handler(BaseHTTPRequestHandler):
    server_version = "nuworks"
    sys_version = ""

    def log_message(self, *args):
        pass  # no access log with query strings (they contain signatures)

    def _send(self, code, body):
        data = PAGE.format(body=body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Robots-Tag", "noindex")
        self.end_headers()
        self.wfile.write(data)

    def _params(self, query):
        q = {k: v[0] for k, v in parse_qs(query).items()}
        a, r, j, s = q.get("a", ""), q.get("r", ""), q.get("j", ""), q.get("s", "")
        # isascii first: "²".isdigit() is True, and compare_digest raises on non-ASCII strings
        if not (r + j + s).isascii() or a not in ACTIONS or not r.isdigit() or not j.isalnum() or len(j) > 64:
            return None
        if not hmac.compare_digest(s, _sig(a, int(r), j)):
            return None
        return a, int(r), j

    def _row(self, r, j):
        import jobs
        import sheet
        ws = sheet.open_worksheet()
        row = next((x for x in sheet.read_rows(ws) if x.number == r), None)
        if row is None or row.url != jobs.job_url(j):
            return ws, None
        return ws, row

    def do_GET(self):
        u = urlparse(self.path)
        p = self._params(u.query) if u.path == "/m" else None
        if p is None:
            return self._send(404, "<p>Not found.</p>")
        a, r, j = p
        try:
            _, row = self._row(r, j)
        except Exception as e:
            return self._send(500, f"<p>Could not read the sheet ({html.escape(type(e).__name__)}).</p>")
        if row is None:
            return self._send(409, "<p>That sheet row no longer holds this job. Nothing changed.</p>")
        self._send(200, f"""<h2>{html.escape(row.company)}</h2><p>{html.escape(row.title)}</p>
<p class=dim>Row {r} · now: {html.escape(row.status)}</p>
<form method=post action="/m?{html.escape(u.query)}"><button>{ACTIONS[a]}</button></form>""")

    def do_POST(self):
        u = urlparse(self.path)
        p = self._params(u.query) if u.path == "/m" else None
        if p is None:
            return self._send(404, "<p>Not found.</p>")
        a, r, j = p
        import sheet
        try:
            ws, row = self._row(r, j)
            if row is None:
                return self._send(409, "<p>That sheet row no longer holds this job. Nothing changed.</p>")
            if a == "site":
                sheet.mark_site_done(ws, r, row.url)
            else:
                sheet.mark_applied_by_hand(ws, r, row.url, how="by hand (marked from Discord)")
        except sheet.SheetError as e:
            return self._send(409, f"<p>{html.escape(str(e))}</p><p class=dim>Nothing changed.</p>")
        except Exception as e:
            _log(f"error {a} row {r}: {type(e).__name__}")
            return self._send(500, f"<p>Error ({html.escape(type(e).__name__)}). Nothing changed.</p>")
        _log(f"{a} row {r} ({row.company})")
        self._send(200, f"<h2>Done</h2><p>{html.escape(row.company)}: {ACTIONS[a].lower()}.</p>")


def main():
    if not config.IS_SERVER:
        sys.exit("web.py runs on the homelab.")
    _secret()
    ThreadingHTTPServer(LISTEN, Handler).serve_forever()


if __name__ == "__main__":
    main()
