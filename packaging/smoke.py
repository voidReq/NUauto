"""Smoke test for a built NUauto package. CI runs it after packaging/build.sh; you can too:

  packaging/build/venv/bin/python packaging/smoke.py dist/NUauto-<version>-linux-x86_64.AppImage
  packaging/build/venv/bin/python packaging/smoke.py packaging/build/dist/NUauto.app/Contents/MacOS/NUauto

1. the app's own commands answer (`doctor --json`, `_playwright --version`);
2. Playwright's Firefox gets installed through the app (`_playwright install firefox`, as the setup screen does);
3. demo mode end to end in headless Firefox: approve a job, start applying, answer the question that comes up,
   and the fake sheet ends up with those rows Applied (the app runs its own `apply --ui json` child).
Uses a temp HOME, so a real NUauto setup on this machine is never touched. Exits non-zero on any failure.
"""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time

from playwright.sync_api import sync_playwright

APP = os.path.abspath(sys.argv[1])
HOME = tempfile.mkdtemp(prefix="nuauto-smoke-home-")
REAL_CACHE = os.path.expanduser("~/Library/Caches/ms-playwright" if sys.platform == "darwin" else "~/.cache/ms-playwright")
ENV = {**os.environ, "HOME": HOME, "APPIMAGE_EXTRACT_AND_RUN": "1", "NUAUTO_DEMO_PACE": "0.02",
       "PLAYWRIGHT_BROWSERS_PATH": os.environ.get("PLAYWRIGHT_BROWSERS_PATH", REAL_CACHE)}
for k in ("NUAUTO_STATE_DIR", "NUAUTO_DEMO", "CLAUDECODE"):
    ENV.pop(k, None)


def run(*args, timeout=600):
    r = subprocess.run([APP, *args], env=ENV, capture_output=True, text=True, timeout=timeout, cwd=HOME)
    return r.returncode, r.stdout, r.stderr


code, out, err = run("doctor", "--json")
report = json.loads(out)  # nothing set up in this HOME: problems expected, but a real answer
assert "checks" in report and any(c["msg"].startswith("Settings:") for c in report["checks"]), out
print("doctor --json: ok")
code, out, err = run("_playwright", "--version")
assert code == 0 and "Version" in out, (code, out, err)
print("bundled Playwright:", out.strip())
code, out, err = run("_window")
print("window on this system:", out.strip())
if sys.platform == "darwin":
    assert out.strip() == "mac", (out, err)  # the app's own WebKit window (pywebview is in the bundle)
code, out, err = run("_playwright", "install", "firefox", timeout=1200)
assert code == 0, (out[-800:], err[-800:])
print("Firefox installed through the app")

gui = subprocess.Popen([APP, "gui", "--demo", "--no-open"], env=ENV, cwd=HOME, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, text=True, start_new_session=True)
try:
    ready = json.loads(gui.stdout.readline())
    url, state = ready["url"], ready["state_dir"]
    assert ready["demo"] is True
    with sync_playwright() as p:
        b = p.firefox.launch(headless=True)
        page = b.new_page(viewport={"width": 1200, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url)
        page.wait_for_selector("[data-testid=week]", timeout=30000)
        page.click("[data-testid=nav-review]")
        page.wait_for_selector("[data-testid=job-card]")
        company = page.inner_text("[data-testid=job-card] .meta").split(" · ")[0]
        page.click("[data-testid=btn-approve]")
        page.wait_for_selector("[data-testid=toast]")
        page.click("[data-testid=nav-apply]")
        page.wait_for_selector("[data-testid=btn-start-apply]:not([disabled])")
        page.click("[data-testid=btn-start-apply]")
        page.wait_for_selector("[data-testid=question]", timeout=120000)
        page.get_by_role("button", name="Yes", exact=True).click()
        page.wait_for_function("() => document.body.innerText.includes('Last run: done') || "
                               "document.querySelector('[data-testid=q-menu-u]')", timeout=180000)
        b.close()
    assert not errors, errors
    rows = {r[1]: r[3] for r in json.load(open(os.path.join(state, "local", "demo_sheet_DEMO-SHEET.json")))["rows"][1:]}
    assert rows["Harbor Embedded"] == rows["Lumen Security"] == rows[company] == "Applied", rows
    assert rows["Quarry Hardware"] == "Needs Human", rows
    print("demo mode end to end: ok (approved", company, "and applied)")
finally:  # the whole group: an AppImage's runtime does not pass the signal on to the app it started
    try:
        os.killpg(gui.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    gui.wait(timeout=60)
    for _ in range(120):  # until every process of the group has quit (the app stops a run first, like Ctrl+C)
        try:
            os.killpg(gui.pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.5)
    else:
        raise AssertionError("the app did not quit within 60 s of SIGTERM")
print("Smoke test passed.")
