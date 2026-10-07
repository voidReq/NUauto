"""nuauto: the one command for this project (installed by `pip install -e .`; see pyproject.toml).

  nuauto gui             the NUauto window: setup, review, apply, health (`--demo`: everything fake, for trying it)
  nuauto approve         browse the best jobs; y approves (goes in the sheet as Approved), n rejects
  nuauto apply           apply to every Approved row, up to the weekly limit  (-n 3 = at most 3)
  nuauto rate            teach the ranking your taste (y/n only, nothing goes in the sheet)
  nuauto update          check NUworks for new jobs now (on the homelab if you have one, then syncs)
  nuauto status          sheet counts, weekly limit, Approved rows
  nuauto login           log in to NUworks by hand (and copies the session to the homelab, if any)
  nuauto login google    fresh Google Sheets login (needed every 7 days; Discord reminds you)
  nuauto assist          company-site applications: list rows / `<row>` starts the agent (asks before Submit)
  nuauto test            run all offline tests (no network, no sheet, no browser)
  nuauto doctor          health check of this machine (and the homelab + Mark-done page, if any; read-only)
  nuauto deploy          homelab: deploy GitHub's main now (the timer does it every 5 min; docs/DEPLOY.md)

Tools (each module's own command line; `nuauto <tool>` with no arguments shows its help):
  nuauto jobs ...        job pool steps (list, triage-export, details, pool, suggest N, stats...)
  nuauto answers ...     answer bank (init, list)
  nuauto sheet ...       sheet commands (status...)
  nuauto setup-sheet     one-time sheet setup; `format` restyles it
  nuauto inspect <url>   read-only look at a NUworks Apply form
  nuauto daily [weekly]  the update itself / the Sunday check-in (what the homelab timers run)
  nuauto web             the Mark-done page (what the homelab's nuauto-web service runs)
  nuauto insights        pay, places and kinds of work in your pool and applications; where your sheet stands
  nuauto selftest        is this install working? demo mode end to end (nothing real is touched)
  nuauto health | demo | onboard ...   the GUI's helpers (health checks, demo mode, setup reads)

The packaged app (AppImage / NUauto.app) is this same command: started with nothing (double-clicked) it opens the
window; `NUauto-x86_64.AppImage apply` etc. work from a terminal too.
"""
import os
import runpy
import sys

from nuauto import config

TOOLS = {"jobs": "jobs", "answers": "answers", "sheet": "sheet", "setup-sheet": "setup_sheet",
         "inspect": "inspect_form", "daily": "daily", "web": "web", "health": "health", "demo": "demo",
         "onboard": "onboard", "selftest": "selftest", "insights": "insights"}
COMMANDS = ("approve", "rate", "apply", "status", "update", "login", "test", "doctor", "assist", "gui", "deploy")


def run_tool(name, rest):
    """Run a module's own `if __name__ == "__main__":` block, as `python -m nuauto.<module> ...` would."""
    sys.argv = [f"nuauto {name}"] + rest
    runpy.run_module(f"nuauto.{TOOLS[name]}", run_name="__main__", alter_sys=True)


def playwright_cli(args):
    """Playwright's own command line (`install firefox`) through the driver inside the package: the packaged app
    has no `python -m playwright`."""
    import subprocess
    from playwright._impl._driver import compute_driver_executable, get_driver_env
    return subprocess.run([*compute_driver_executable(), *args], env=get_driver_env()).returncode


def main():
    argv = sys.argv[1:]
    if config.FROZEN:
        os.makedirs(config.STATE_DIR, exist_ok=True)
        os.chdir(config.STATE_DIR)  # the bundle is read-only (and an AppImage's is a temporary mount)
        if not argv or argv[0].startswith("-psn_"):  # double-clicked: the window (old macOS adds a -psn_ argument)
            argv = ["gui"]
    else:
        os.chdir(config.PROJECT_DIR)  # relative paths (data/, logs/, rsync sources) are from the repo root
    cmd, rest = (argv[0], argv[1:]) if argv else ("", [])
    if cmd == "_assist":  # assist.py's own command line (the agent's hook and answer bank) when packaged
        from nuauto import assist
        return assist.cli(rest)
    if cmd == "_playwright":
        sys.exit(playwright_cli(rest))
    if cmd == "_window":  # which window this system gets (mac / gtk / app / tab): for support and the build checks
        from nuauto import window
        return print(window.kind())
    if cmd in TOOLS:
        return run_tool(cmd, rest)
    laptop = config.HAS_SERVER and not config.IS_SERVER  # a laptop that syncs with a homelab
    if cmd not in COMMANDS or \
            (rest and cmd not in ("apply", "assist", "gui", "doctor") and not (cmd == "login" and rest == ["google"])):
        sys.exit(__doc__)
    if cmd == "test":  # every tests/test_*.py, 4 at a time (each uses its own temp folder); output in order
        import glob
        import subprocess
        from concurrent.futures import ThreadPoolExecutor

        def run(t):
            return t, subprocess.run([sys.executable, t], capture_output=True, text=True)
        failed = []
        with ThreadPoolExecutor(max_workers=4) as pool:
            for t, r in pool.map(run, sorted(glob.glob("tests/test_*.py"))):
                sys.stdout.write(r.stdout)
                sys.stderr.write(r.stderr)
                if r.returncode:
                    failed.append(t)
        sys.exit(f"FAILED: {', '.join(failed)}" if failed else 0)
    if cmd == "doctor":
        from nuauto import doctor
        sys.exit(doctor.main())
    if cmd == "gui":  # syncs by itself, in the background (the window opens at once)
        from nuauto import gui
        return gui.main(rest)
    if cmd == "deploy" and not laptop:  # on the homelab itself (what nuauto-deploy.service runs)
        from nuauto import deploy
        sys.exit(deploy.main())
    if laptop:
        from nuauto import sync
        if cmd == "deploy":
            sys.exit(sync.deploy_now())
        if cmd == "update":
            sync.push()
            print("Running the update on the homelab (a few minutes; Ctrl+C stops watching, not the run)...")
            sync.ssh("systemctl --user start nuauto-daily.service; "
                     f"tail -n 4 \"$(ls -t {config.SERVER_DIR}/logs/daily-*.txt | head -1)\"")
            sync.pull()
            return
        if cmd != "login":
            sync.pull()

    try:
        if cmd == "approve":
            from nuauto import jobs
            jobs.cmd_approve()
        elif cmd == "rate":
            from nuauto import jobs
            jobs.cmd_rate()
        elif cmd == "apply":
            from nuauto import apply
            apply.main(rest)
        elif cmd == "status":
            from nuauto import sheet
            sys.argv = ["nuauto status", "status"]
            sheet.main()
        elif cmd == "update":  # on the homelab itself, or no homelab at all
            from nuauto import daily
            sys.exit(daily.main())
        elif cmd == "assist":
            if config.IS_SERVER:
                sys.exit("nuauto assist runs on the laptop (visible browser, you at the terminal).")
            from nuauto import assist
            assist.main(rest)
        elif cmd == "login" and rest == ["google"]:
            from nuauto import sheet
            sheet.google_login()
            if laptop:
                sync.push()
        elif cmd == "login":
            from nuauto import browser
            browser.cmd_login()
            if laptop:
                sync.push_session()
    finally:
        if laptop and cmd in ("approve", "rate", "status"):
            sync.push()  # ratings (and a fresh Google token after a re-login) go to the homelab


if __name__ == "__main__":
    main()
