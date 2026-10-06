"""`nuauto selftest`: is this install working? NUauto end to end in demo mode, where nothing is real (a fake sheet, fake
NUworks pages and a fake Claude, in a temp folder): it never touches your files or your accounts.

  1. Playwright's Firefox is there and starts (installed if missing: about 90 MB, once)
  2. a demo window server starts (`nuauto gui --demo`)
  3. a hidden browser uses it the way you would: approve a job, start applying, answer the question that comes up
  4. the fake sheet shows those jobs Applied, and the external one Needs Human
Exit 0 = all good. Also in Settings (Run a self-test); the release builds run it on every platform (packaging/smoke.py).
"""
import json
import os
import signal
import subprocess
import sys
import time

from nuauto import config
from nuauto import health


def say(msg):
    print(msg, flush=True)


def firefox():
    c = health.firefox()[0]
    if c.status == health.OK:
        return
    say("Installing Playwright's Firefox (once, about 90 MB)...")
    if subprocess.run(config.self_cmd("_playwright", "install", "firefox")).returncode != 0:
        sys.exit("Self-test failed: Playwright's Firefox could not be installed (offline?).")
    c = health.firefox()[0]
    if c.status != health.OK:
        sys.exit(f"Self-test failed: {c.detail}")


def stop(proc):
    """The demo server and everything it started (a packaged app's runtime does not pass signals on)."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    proc.wait(timeout=60)
    for _ in range(120):
        try:
            os.killpg(proc.pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.5)
    os.killpg(proc.pid, signal.SIGKILL)


def run_demo():
    from playwright.sync_api import sync_playwright
    env = {k: v for k, v in os.environ.items() if k not in ("NUAUTO_STATE_DIR", "NUAUTO_DEMO", "CLAUDECODE")}
    env.update(NUAUTO_DEMO_PACE="0.02", PYTHONUNBUFFERED="1")
    gui = subprocess.Popen(config.self_cmd("gui", "--demo", "--no-open"), env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        line = gui.stdout.readline()
        if not line:
            sys.exit(f"Self-test failed: the demo server did not start. {gui.stderr.read()[-500:]}")
        ready = json.loads(line)
        say("Demo server: started")
        errors = []
        with sync_playwright() as p:
            b = p.firefox.launch(headless=True)
            page = b.new_page(viewport={"width": 1200, "height": 900})
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(ready["url"])
            page.wait_for_selector("[data-testid=week]", timeout=30000)
            page.click("[data-testid=nav-review]")
            page.wait_for_selector("[data-testid=job-card]")
            company = page.inner_text("[data-testid=job-card] .meta").split(" · ")[0]
            page.click("[data-testid=btn-approve]")
            page.wait_for_selector("[data-testid=toast]")
            say(f"Review: approved {company}")
            page.click("[data-testid=nav-apply]")
            page.wait_for_selector("[data-testid=btn-start-apply]:not([disabled])")
            page.click("[data-testid=btn-start-apply]")
            page.wait_for_selector("[data-testid=question]", timeout=120000)
            page.get_by_role("button", name="Yes", exact=True).click()
            say("Apply: a question came up and was answered")
            page.wait_for_function("() => document.body.innerText.includes('Last run: done') || "
                                   "document.querySelector('[data-testid=q-menu-u]')", timeout=180000)
            b.close()
        if errors:
            sys.exit(f"Self-test failed: the page had errors: {errors[:3]}")
        with open(os.path.join(ready["state_dir"], "local", "demo_sheet_DEMO-SHEET.json")) as f:
            rows = {r[1]: r[3] for r in json.load(f)["rows"][1:]}
        want = {"Harbor Embedded": "Applied", "Lumen Security": "Applied", company: "Applied",
                "Quarry Hardware": "Needs Human"}
        wrong = {k: rows.get(k) for k, v in want.items() if rows.get(k) != v}
        if wrong:
            sys.exit(f"Self-test failed: the fake sheet does not show what it should: {wrong}")
        say("Sheet: the jobs are Applied, the external one Needs Human")
    finally:
        stop(gui)


def main(argv):
    if argv not in ([], ["--quiet"]):
        sys.exit(__doc__)
    say(f"NUauto {__import__('nuauto').__version__} self-test (demo mode: nothing real is touched)")
    firefox()
    say("Firefox: installed and starts")
    run_demo()
    say("Self-test passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
