"""Offline checks for web.py (the signed "Mark done" links; public via the tunnel). Run: python test_web.py

Starts web.Handler on 127.0.0.1 (random port), with a temp secret file, temp logs dir and a fake sheet.
"""
import http.client
import os
import stat
import tempfile
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

from nuauto import config
from nuauto import jobs
from nuauto import sheet
from nuauto import web

TMP = tempfile.mkdtemp()
web.SECRET_PATH = os.path.join(TMP, "web_secret.txt")  # never touch the real one
config.LOGS_DIR = os.path.join(TMP, "logs")

JOB = "1234567"
OTHER_JOB = "7654321"
ROWS = {
    5: sheet.Row(5, jobs.job_url(JOB), "Acme", "Embedded Co-op", "Applied", sheet.SITE_MARK + " acme.com", "2026-10-01"),
    6: sheet.Row(6, jobs.job_url(OTHER_JOB), "Beta", "Firmware Co-op", "Needs Human", "external", ""),
}
WS = object()  # stands in for the worksheet
calls = []  # every mark call, in order


def fake_mark_site_done(ws, row_number, url):
    assert ws is WS
    calls.append(("site", row_number, url))


def fake_mark_applied_by_hand(ws, row_number, url, how="by hand"):
    assert ws is WS
    calls.append(("applied", row_number, url, how))


sheet.open_worksheet = lambda: WS
sheet.read_rows = lambda ws: list(ROWS.values())
sheet.mark_site_done = fake_mark_site_done
sheet.mark_applied_by_hand = fake_mark_applied_by_hand

server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
PORT = server.server_address[1]


def request(method, target):
    """Returns (status, body); status is None if the server dropped the connection without answering."""
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=5)
    try:
        conn.request(method, target, body=b"" if method == "POST" else None)
        r = conn.getresponse()
        return r.status, r.read().decode()
    except (http.client.RemoteDisconnected, ConnectionError):
        return None, ""
    finally:
        conn.close()


def target_of(link_url):
    u = urlparse(link_url)
    return u.path + "?" + u.query


def params_of(link_url):
    return {k: v[0] for k, v in parse_qs(urlparse(link_url).query).items()}


def target(**over):
    p = {**params_of(web.link("site", 5, JOB)), **over}
    return "/m?" + urlencode(p)


def expect_404(label, tgt):
    """GET and POST must both answer 404 and mark nothing."""
    for method in ("GET", "POST"):
        before = len(calls)
        status, _ = request(method, tgt)
        assert status == 404, f"{label}: {method} gave {status}, expected 404"
        assert len(calls) == before, f"{label}: {method} marked something: {calls[before:]}"


def main():
    # secret file is created on first use, mode 600, in the temp dir
    assert not os.path.exists(web.SECRET_PATH)
    good = web.link("site", 5, JOB)
    assert stat.S_IMODE(os.stat(web.SECRET_PATH).st_mode) == 0o600

    # a link parses back correctly
    u = urlparse(good)
    assert good.startswith(web.BASE_URL + "/m?") and u.path == "/m"
    p = params_of(good)
    assert (p["a"], p["r"], p["j"]) == ("site", "5", JOB) and len(p["s"]) == 32
    assert web.Handler._params(None, u.query) == ("site", 5, JOB)

    # signatures: stable, and different per action / row / job
    assert web.link("site", 5, JOB) == good
    sigs = {params_of(web.link(a, r, j))["s"] for a in web.ACTIONS for r in (5, 6) for j in (JOB, OTHER_JOB)}
    assert len(sigs) == len(web.ACTIONS) * 4
    assert params_of(web.link("site", 5, JOB))["s"] != params_of(web.link("applied", 5, JOB))["s"]

    # GET with a valid signature: confirm page only, nothing marked
    status, body = request("GET", target_of(good))
    assert status == 200 and "Acme" in body and "<form method=post" in body, (status, body)
    status, _ = request("GET", target_of(web.link("applied", 6, OTHER_JOB)))
    assert status == 200 and calls == []

    # POST with a valid signature: exactly the right mark function, once, with the right row
    status, body = request("POST", target_of(good))
    assert status == 200 and "Done" in body, (status, body)
    assert calls == [("site", 5, ROWS[5].url)], calls
    del calls[:]
    status, _ = request("POST", target_of(web.link("applied", 6, OTHER_JOB)))
    assert status == 200
    assert calls == [("applied", 6, ROWS[6].url, "by hand (marked from Discord)")], calls
    del calls[:]

    # bad / missing / tampered signature
    expect_404("bad signature", target(s="0" * 32))
    expect_404("empty signature", target(s=""))
    expect_404("truncated signature", target(s=params_of(good)["s"][:-1]))
    p = params_of(good)
    del p["s"]
    expect_404("no signature", "/m?" + urlencode(p))
    # tampered row, job, action (signature kept from the good link)
    expect_404("tampered row", target(r="6"))
    expect_404("tampered job", target(j=OTHER_JOB))
    expect_404("tampered action", target(a="applied"))
    # unknown action / non-digit row / odd job id, even with a correct signature for those values
    for label, a, r, j in [("unknown action", "delete", 5, JOB), ("non-digit row", "site", "5x", JOB),
                           ("negative row", "site", "-5", JOB), ("empty row", "site", "", JOB),
                           ("job with slash", "site", 5, "12/34"), ("job with dots", "site", 5, "..%2f"),
                           ("empty job", "site", 5, ""), ("overlong job", "site", 5, "1" * 65)]:
        expect_404(label, "/m?" + urlencode({"a": a, "r": r, "j": j, "s": web._sig(a, r, j)}))
    # wrong path (valid query)
    q = urlparse(good).query
    for path in ("/", "/x", "/m/", "/M", "/m/extra"):
        expect_404(f"path {path}", f"{path}?{q}")
    expect_404("no query", "/m")
    assert calls == []

    # valid signature, but the sheet row now holds a different job: 409, nothing marked
    ROWS[5] = sheet.Row(5, jobs.job_url(OTHER_JOB), "Acme", "Embedded Co-op", "Applied", sheet.SITE_MARK, "2026-10-01")
    for method in ("GET", "POST"):
        status, body = request(method, target_of(good))
        assert status == 409 and "no longer holds" in body, (method, status)
    assert calls == []
    # row number that is no longer in the sheet at all
    gone = web.link("site", 99, JOB)
    for method in ("GET", "POST"):
        assert request(method, target_of(gone))[0] == 409
    assert calls == []
    ROWS[5] = sheet.Row(5, jobs.job_url(JOB), "Acme", "Embedded Co-op", "Applied", sheet.SITE_MARK, "2026-10-01")

    # a SheetError from the mark function -> 409 with the message, nothing else changes
    def refusing(*args, **kwargs):
        raise sheet.SheetError("Row 5 changed under us.")
    real = sheet.mark_site_done
    sheet.mark_site_done = refusing
    status, body = request("POST", target_of(good))
    sheet.mark_site_done = real
    assert status == 409 and "Row 5 changed under us." in body and "Nothing changed" in body, (status, body)
    assert calls == []

    # an unexpected error -> 500, logged by type only, nothing marked
    def boom(*args, **kwargs):
        raise RuntimeError("secret detail")
    sheet.mark_site_done = boom
    status, body = request("POST", target_of(good))
    sheet.mark_site_done = real
    assert status == 500 and "RuntimeError" in body and "secret detail" not in body, (status, body)
    with open(os.path.join(config.LOGS_DIR, "web.log")) as f:
        log = f.read()
    assert "RuntimeError" in log and "secret detail" not in log and params_of(good)["s"] not in log
    assert calls == []

    # a sheet that cannot be read -> GET 500, nothing marked
    sheet.read_rows = lambda ws: (_ for _ in ()).throw(sheet.SheetError("down"))
    assert request("GET", target_of(good))[0] == 500
    assert request("POST", target_of(good))[0] in (409, 500)  # SheetError -> 409
    assert calls == []

    # non-ASCII digits / signature characters must be a clean 404, not a crashed handler
    # (last on purpose: web._params currently raises TypeError / ValueError for these, see report)
    expect_404("unicode signature", "/m?" + urlencode({**params_of(good), "s": "é" * 32}))
    expect_404("unicode superscript row", "/m?" + urlencode({"a": "site", "r": "²", "j": JOB, "s": "0" * 32}))
    assert calls == []

    server.shutdown()
    print("All web checks passed.")


if __name__ == "__main__":
    main()
