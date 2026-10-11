GUI: `nuauto gui` (the NUauto window)
(Keep this current when you change the GUI, its API, demo mode or the setup wizard. Code: src/nuauto/gui.py,
gui_static/, onboard.py, health.py, demo.py. Safety rules and their tests: docs/SAFETY.md.)

WHAT IT IS
- A local app on your own computer; nothing is hosted anywhere. `nuauto gui` (or the packaged app) starts a server on
  127.0.0.1 (stdlib ThreadingHTTPServer, like web.py) that only this computer can reach, and shows its one page: plain
  HTML/CSS/JS in src/nuauto/gui_static/ (no build step, nothing loaded from the internet, light/dark from the system).
- The window (window.py; `nuauto _window` says which one this system gets): its own window on macOS (pywebview: the
  system's WebKit) and on Linux when the system's python3 has GTK + WebKitGTK (window_gtk.py, most GNOME desktops);
  else Chrome/Chromium/Brave/Edge in app mode (--app), else a browser tab (--browser forces a tab). Closing its own
  window quits NUauto (a run in progress stops like Ctrl+C). Starting it again opens another window on the running one.
- The CLI stays. The GUI calls the same functions; long or browser work runs as the same `nuauto ...` commands in
  child processes, one at a time (gui.Task): apply, update, logins, the NUworks check, the NUworks side of a
  company-site application, the wizard's read-only NUworks reads.
- It quits after 30 minutes with no window open and nothing running, or with Settings > Quit.
- Linux and macOS. Install: install.sh (uv brings Python 3.12; Playwright's Firefox; the `nuauto` command in
  ~/.local/bin; an app icon). The homelab never runs it (it refuses there).

PACKAGED APP (packaging/; .github/workflows/release.yml builds and smoke-tests it on every pull request, and publishes
it as a GitHub release on every v* tag)
- Linux: an AppImage per architecture (built on Ubuntu 22.04: runs on distributions from 2022 on). macOS: a DMG with
  NUauto.app per architecture (wheels for macOS 12+). PyInstaller, one folder: its own Python 3.12 and libraries, the
  code, prompts/, the page and window_gtk.py. Not signed by Apple: the first open needs Privacy & Security > Open
  Anyway. About 115 MB (AppImage).
- Same code as a source install, with config.FROZEN: code and prompts come from the bundle (read-only); your files go in
  the app-data folder (~/.local/share/NUauto, ~/Library/Application Support/NUauto); always local mode (no homelab:
  sync copies a checkout). Started with nothing it opens the window; from a terminal it is the whole `nuauto` command
  (`NUauto-x86_64.AppImage apply`). It runs itself for its children (config.self_cmd) and, for a terminal window or a
  timer, the AppImage file or the app binary (config.self_exe). Internal commands: `_assist` (the assistant's hook and
  answer bank, two words as check_bash requires), `_playwright` (Playwright's CLI from the bundled driver: install
  firefox), `_window`.
- Playwright's Firefox is downloaded on first run into the usual cache (~/.cache/ms-playwright, ~/Library/Caches/
  ms-playwright): PLAYWRIGHT_BROWSERS_PATH is set to it, because frozen Playwright would look inside the bundle.
  Programs started from the app (the system's python3, browsers, a terminal) get an environment without the app's
  PyInstaller / AppImage variables (window.system_env); LD_LIBRARY_PATH is restored at start.
- Updates: once a day the app asks GitHub's public API for the latest release (Version in Settings; Download opens the
  release page). Your files stay when you replace the app.
- Build and check one yourself: `sh packaging/build.sh` (needs uv; Linux: curl and binutils), then
  `packaging/build/venv/bin/python packaging/smoke.py <the AppImage, or NUauto.app/Contents/MacOS/NUauto>`: the app's
  commands, `_window`, and `selftest` run by the app itself. Other distributions: `sh packaging/distros.sh <AppImage>`
  (podman or docker: Ubuntu 22.04 and 24.04, Debian 12, Fedora, Arch; logs in packaging/build/distros/).

SCREENS
  Today          health problems with fix buttons, this week's count, next steps (with "Review N internships" when
                 internships are on and some wait: it opens Review on that list), what only you can do (each item with
                 its due date, soonest first; same in Apply's Company sites part)
  Review         one pool job at a time. Approve mode: Approve (y) adds it to the sheet as Approved at once
                 (or, for one of your Proposed rows, which Review shows first, turns that row Approved);
                 Each card shows its Score (match + each bonus) and Order (why it sits there: closing soon,
                 your Proposed row, half score / half taste, or full-stack last); the terminal viewer shows the Score.
                 Keys as in the CLI viewer: y n s u o, plus j/k scroll, space/b page, g/G top/bottom. Decisions are
                 sent behind the screen (the next job shows at once; a failed write brings you back to that job).
                 Undo puts the row back to Proposed; Not for me (n) rates it no; Skip (s); Back (u); Open (o).
                 Rate only mode: yes/no ratings for the taste model, nothing in the sheet.
                 Source switch, only when internships are on ("internships" in local_config.json, intern.py): "NUworks
                 co-ops" / "<term> internships" (Simplify's list; the postings are on the companies' own sites). Remembered
                 for the session (default co-ops; Today's "Review N internships" picks the internships, its co-op button the
                 co-ops); changing it reloads the list. Everything above works the same on the internships, except: Approve
                 adds an Approved row to the sheet's Other jobs tab (apply there from Apply > Company sites; no weekly cap),
                 and Undo puts it back to Proposed there; a job whose Other jobs row is Proposed shows first, flagged
                 "already Proposed (Other jobs row N)", and Approve turns that row Approved; the button says "Open posting";
                 cards add Posted and, when the posting says so, Who can apply; a posting that could not be read comes last
                 with no match ("not scored", Why says what failed: judge it from its link). When the list is done it points
                 to Apply > Company sites.
                 Search box (words or "quoted phrases", all must appear, any case, in title, company, place, kind of
                 work, tag, skills, description, qualifications) and a kind-of-work menu narrow the list; the order
                 inside stays the same. Esc clears the search. A text search shows every match as its own card (8 at a
                 time, "Show more"; the posting cut short, "Show the whole posting"), each with its own Approve / Not for
                 me buttons (y / n keys are off there; a decided card turns into one line with Change). The job you were on
                 is left undecided; clearing the search comes back to it. A kind-of-work choice alone stays one job at a time.
  Apply          the weekly count and Start, the live run (below), then three tabs, each with its row count: NUworks, Company
                 sites, By hand on NUworks (the tab you picked is remembered; #/company, #/other and Today's buttons open
                 Company sites). Every NUworks row in the three tabs shows its match % (or its score, "score 105 = 75% match + 10 Boston + 20
                 security", when bonuses add to it: Start goes by score) and pay. NUworks tab: "Apply in this order" (remembered for the
                 session) IS the order Start and At most go: closing within a week first, then score (default), score, match %,
                 closing soonest, pay per hour (insights.hourly; unclear pay last). The server sorts (apply.apply_order, GET
                 /api/apply?order=) and Start runs `nuauto apply --order <it>`. Company sites / By hand: "Sort the list by" (the view
                 only): closing soonest (default), sheet order, score, match %, pay. And a "Show" kind-of-work
                 menu (the categories, with counts; shared by the tabs; NUworks rows only, the Other jobs card is not
                 filtered; the view only); filtered, NUworks says "Start still goes through all N". A NUworks row's number is
                 where Start takes it, also while filtered. NUworks: Approved rows in apply order (numbered 1, 2, 3: the order Start goes), each with when it closes
                 (due chip: amber within a week, red once past), its match % and pay, a "may also want the company's
                 site" chip when the posting says so, and its own Apply button (after a confirm: just that row, `nuauto apply --row N`); Start (optionally
                 "at most N"), the live run: current row, latest screenshot, log, next-job countdown, Stop / Force
                 stop; question dialogs; recent runs. Start tries every row on its own; one that sends you to a
                 company site or needs a cover letter / transcript becomes Needs Human (nothing submitted) and shows
                 in the other tabs (since 2026-10-09 Company sites is a part of Apply; the menu count is Approved + the
                 assistant's rows + retries). Company sites tab (assistants side by side, up to 3: a row with one
                 running shows "assistant running (slot N)" instead of Start; "N of 3 assistants running"; it follows
                 /api/state assistants): Needs Human rows for the assistant (opens
                 `nuauto assist <row>` in a terminal window, or shows the command), "I applied myself", company sites still
                 owed (Mark done), the NUworks side to retry, then the Other jobs card: the sheet's Other jobs tab's
                 Approved rows (read fresh; counted in the tab, not the menu), Start assistant (opens `nuauto assist other
                 <row>`), Open site, I applied myself, Add a job (opens Sheet on the Other jobs tab). No caps there; their
                 Applied and other rows are on the Sheet screen. (A screen of its own until 2026-10-09.) By hand on NUworks tab: rows only you can finish there.
                 The lists follow the sheet by themselves: /api/state carries rows_at (when the sheet was last read), the
                 server reads it again after every finished row and run and when it is 30 s old, and the open screen
                 reloads its lists when rows_at changes (no closing and reopening). "At most" keeps what you typed.
  Sheet          add a job by hand (manage.py): NUworks co-op or Other job, Approved or Proposed, link; a NUworks
                 link switches the tab and fills company / title / match from your job data. Below it the sheet's two tabs as
                 tabs (NUworks, Other jobs, each with its row count; remembered; adding a job shows the tab it went to; Apply's
                 Add a job opens Other jobs). Each tab's rows with
                 Move to the other tab (asks for the link there) and Remove (clears the row's cells: no row is
                 renumbered). Applied rows are never removed and never leave the NUworks tab (an Applied Other job may move
                 to NUworks: there it counts toward the limits); unresolved Submit clicks are locked; nothing moves while a run is on
  Insights       where your sheet stands (a status ring: arcs in an order checked for color-blind separation, with a
                 labeled legend), then for Your pool / Applied: median and usual pay, share in Massachusetts, closing this
                 week; an hourly-pay histogram; by state (top 5 + Other, top cities); kind of work. insights.py, also
                 `nuauto insights` in a terminal. Read-only; pay that doesn't read as $10-150/h counts as not listed
  Answers        the answer bank as a table (exact-match rules unchanged); locked while a run uses it. NUworks /
                 Other jobs switch: the Other jobs bank (answers_other.json) is its own table; the NUworks one has a
                 "NUworks only" column (never used for Other jobs; default on for start/end date and co-op term)
  Settings       every health check (run them now), logins, resume label, applications per week (the weekly cap), week start;
                 the company-site assistant: account email, effort (low / medium / high), the folders it may read, its job-site
                 logins (how many; Export for Bitwarden: the import file in ~/Downloads, mode 600, and how to import it); setup,
                 logs, quit
  Setup          the wizard (below); opens by itself until setup is done
  Past runs      logs/ folders: actions.log and the screenshots before and after Submit

SETUP WIZARD (onboard.py + gui_static/setup.js; each step checks itself)
  welcome      NUworks terms of use read (local_config tos_ack) and the limits
  tools        Playwright's Firefox starts; Claude Code installed and logged in (install / log in buttons)
  google       your own OAuth client: linked Google Cloud steps, drop the JSON (only a Desktop-app client is
               accepted; saved local/client_secret.json, mode 600); then the Google login
  sheet        create one (Sheets API spreadsheets.create; gspread's create needs the Drive API) or paste the
               address (ID extracted, access tested). Empty = set up; other data = refused, never overwritten.
  resume       native file dialog (osascript / zenity / kdialog) or a path; the PDF must have text
  nuworks      login (Firefox window; you type nothing into NUauto), then the resume label read from one job's
               real Apply popup (read-only: opens it, reads the Resume dropdown, Cancel)
  preferences  what you study first (fields.py: Engineering or CS, Business, Health sciences or nursing, Natural
               sciences or math, Social sciences or humanities, Arts, media or design): picking one fills in that
               field's starting values (after a confirm; term, year, bars, home state and tags stay): Claude's
               description of you, the roles to keep / drop, major words, and the kinds of work (name, what counts,
               ranking bonus, lower bar, always last; add / remove; Other always there, last). Then term (NUworks' own
               list, read-only; onboard.find_terms looks for terms anywhere in that JSON), year, graduation year,
               thresholds, home state, tags -> local_config.json "preferences" (docs/PIPELINE.md). onboard.check_prefs
               checks it all (check_categories: keys from the names, unique, Other added). A first setup has no
               field picked and Save asks for one. Saving rebuilds the pool (homelab mode: pushes). The resume-label
               read (nuworks step) tries up to 4 jobs (onboard.a_job_urls: pool, then jobs with details, then any
               listed job), so it works before the pool has anything for you.
  extras       Discord webhook (checked with a GET, posts nothing until you press Test), automatic updates
               (local mode: systemd user timer / launchd agent at 08:00 and 18:00), app icon

HEALTH (health.py; the same checks `nuauto doctor` prints, `nuauto doctor --json`)
  check     how                                                               GUI runs it
  files     settings saved, local/ 700 and secrets 600 (Fix permissions)       every minute
  resume    PDF there, NUworks resume label set                                every minute
  google    days left from google_login.txt; a sheet read that fails on login  1 min / 15 min
  sheet     row 1 headers and statuses; limits = this week / total             15 min
  nuworks   hidden browser, domain lock on, one-click SSO re-login if needed   at start, every 6 h, Check now
            (own process; it gives the browser up to anything you start, and runs again when that ends)
  claude    `claude auth status --json` loggedIn; once a day one tiny real      15 min / daily
            `claude -p` call (no tools, no MCP, not saved)
  firefox   Playwright's Firefox starts (a separate empty profile)             daily
  discord   GET the webhook (posts nothing)                                    6 h
  homelab   doctor's homelab checks over ssh (homelab mode only)               15 min
  updates   age of the last scan (data/scans.json)                             every minute
- The status chips at the top (Google, Sheet, NUworks, Claude Code, Homelab): click one for its detail, its fix button
  and Check now (runs that check's groups again, gui.App.GROUPS: google quick + sheet, nuworks quick + nuworks, the
  others their own; "Checking…" until done, the result shows in place). Not in view mode.
- Checks never open a login page, show a secret or change the sheet. A check turning bad (after the first
  round) sends a desktop notification (notify-send / osascript; none in demo mode).
- When the GUI is closed, the scheduled update (homelab, or the laptop's timer) covers it: daily.py warns about the
  Google login and now also about Claude Code being logged out (claude_ready) before it scores anything.

SAFETY MODEL (details and tests: docs/SAFETY.md)
- Network: 127.0.0.1 only, random port. A secret made at each start is in the URL opened once; the page swaps it
  for an HttpOnly SameSite=Strict cookie (nuauto_<port>). Every request needs that cookie and a Host of
  127.0.0.1:<port> or localhost:<port> (DNS rebinding). Every POST also needs X-NUauto: 1 and a JSON body (other
  websites can't send that), and a matching Origin if one is sent. CSP: only this server's own files.
- The page never receives a secret (token, cookies, webhook, client secret): only "set / not set".
- Runs: `nuauto apply --ui json` / `nuauto assist nuworks <row> --ui json` work only as a child of the running GUI
  (its pid in local/gui.lock) and never inside a Claude Code shell (CLAUDECODE). The real GUI refuses to start
  there too: agents use --demo (fake) or --view (real data, read-only, below). Apply, logins and the NUworks side always get a visible browser (AUTO_HEADLESS
  cleared). Approved in the sheet stays the only go-ahead; caps unchanged.
- Questions (answers.JsonIO): one JSON line per question on the child's stdout ("::nuauto:: " + JSON), the
  answer as one JSON line on its stdin. The same rules as typing: an option must be one of the choices, blank
  = stop (Skip this job = the row becomes Needs Human), always-ask answers are never saved.
- Stop: SIGINT to the run (the KeyboardInterrupt path Ctrl+C takes: nothing submitted = row stays Approved,
  Submit already clicked = Needs Human). Force stop: SIGKILL to the whole process group. If the GUI dies, the
  child's stdin closes and it stops the same way.
- One browser at a time: browser.lock_profile (flock on local/browser_profile.lock), held from launch to close.
- Sync (homelab mode): pull at start and every 30 minutes; push after Review and the Google login; never on a
  timer, so an open GUI on a work-in-progress branch does not deploy it.

DEMO MODE (demo.py) AND TESTING WITH AGENTS
- `nuauto gui --demo` makes a temp folder (NUAUTO_STATE_DIR) and runs everything on fakes: the sheet is a JSON file
  (the real sheet.py code runs on it), NUworks is a few fake pages served inside Playwright (the real apply.py fills
  and submits them; the browser's proxy does not exist, so nothing reaches the network), claude is a fake script,
  the update adds held-back jobs. Nothing real can be read or sent.
- `--demo-state a,b`: fresh (the wizard from the start), google-expired, claude-logged-out, nuworks-relogin,
  nuworks-password, cap-reached. `python -m nuauto.demo state <name> on|off` flips one while it runs.
- `--no-open --port 0`: prints one JSON line {"url", "port", "demo", "state_dir"} when ready; open the url (it
  carries the secret) in Playwright. Everything on screen has a JSON API (below); key controls have data-testid.
- `nuauto gui --demo --screenshots DIR`: every screen, light/dark, wide/narrow, as PNGs (plus Review's internship list:
  review-intern, review-intern-unread, review-intern-list); then it quits.
- `nuauto selftest` (also Settings > Run a self-test): the whole demo flow in a hidden browser (Firefox installed if
  missing; approve, apply with a question, the fake sheet), in its own temp folder; exit 0 = this install works.
- Demo runs nobody watches use a hidden browser: with NUAUTO_DEMO_HEADLESS=1 (tests and the self-test set it) or no
  screen (CI, containers). Real runs always open a visible browser.
- Agents: only ever --demo or --view. Never start the real GUI (no --view), never click Start in a real one.

VIEW MODE (`nuauto gui --view`; gui.VIEW; added 2026-10-09 so an agent can look at your real screens)
- Your real sheet and job data, read-only. Allowed inside a Claude Code shell (the real GUI is not). Prints its URL like
  --no-open (no window); open it in Playwright. Quit: POST /api/quit or Ctrl+C.
- Enforced by the server, not the page: every POST except /api/quit is refused (403 "View-only mode"), so no button
  and no route added later can act; Task start is refused too (no child process of any kind); only the quick and
  sheet checks run (no hidden NUworks browser, no Claude call, no Discord, no ssh, no desktop notification); no
  lock file (a running GUI's is never touched or asked for a window); no homelab pull. Banner: "View only".
- Hidden: saved answer values (/api/answers shows the questions only) and run logs / screenshots (/api/file 403).
  Job data, the sheet rows, the settings (host names, sheet id) and past-run names are shown.
- Test: tests/test_view.py.
- Tests (in `nuauto test`): test_gui.py (security rules, API, single instance, the main flows in headless
  Firefox, Review's internship list: approve / undo / the unreadable one / internships off), test_setup.py (each wizard step's checks, then the wizard end to end), test_demo.py (the real apply.py
  on the fake pages: Ctrl+C and GUI-gone stops, cap refusal, NUworks re-login states), test_health.py.
- CI: .github/workflows/tests.yml runs `nuauto test` on Ubuntu and macOS.

API (JSON; all need the cookie; POSTs need X-NUauto: 1)
  GET  /api/state            counts, week, to-do, last scan, setup_needed, the current task; internships {on, term,
                             review: how many wait for Review}
  GET  /api/health           the checks; POST /api/health/run {"groups": [...]}
  GET  /api/review?mode=&q=&category=&source=   the review queue (q / category narrow it; total, categories = the whole
                             queue; source=intern: the internships, else the co-ops; the reply says "source"; an internship
                             that could not be read has match null and comes last); GET /api/job/<id> one card (an
                             internship's adds source "simplify", url = the posting, posted_text, year_text, unread)
  POST /api/decide           {"id", "mode", "decision": approve|yes|no} -> {"row", "previous"}; an internship's approve adds
                             "tab": "other" (its row is in the Other jobs tab)
  POST /api/undo             {"id", "row", "previous"} (an internship also needs "tab": "other": its row goes back to
                             Proposed in the Other jobs tab); POST /api/review/done (push ratings, homelab mode)
  GET  /api/apply            Approved rows in apply order, week, why_not, recent runs
  POST /api/action           {"kind": apply ({"n", "order"} or {"row"})|update|login_google|login_nuworks|check_nuworks|login_claude|
                             install_firefox|install_claude|nuworks_side|assist|fix_permissions, "args"}
                             (assist: {"row", "tab": "other"} for the Other jobs tab)
  GET  /api/task?after=N     the current task: state, new log lines, question, row, screenshot, countdown
  POST /api/answer           {"task", "id", "reply"}; POST /api/stop {"task", "force"}
  GET  /api/company          agent / other / site / retry rows, sessions (the assistants running now: slot, row, tab,
                             company, host, since) and slots (how many may); POST /api/mark {"action": applied|site, "row", "url",
                             "tab": "other" (Other jobs tab, applied only)}
  GET  /api/other            the Other jobs tab: exists, ready (Approved; an internship with its match, score, pay, posted),
                             applied, rest, sessions, slots
  POST /api/other/add        {"url", "company", "title"} -> {"row"}: an Approved row there (the tab made if missing)
  GET  /api/sheet            both tabs: exists, rows (each with locked: why it can't move / be removed, or null), busy
  GET  /api/sheet/lookup?url=  the tab a link belongs in; for a NUworks job: known, company, title, match, in_pool, notes
  POST /api/sheet            {"action": add, "tab": nuworks|other, "url", "company", "title", "status"} -> {"tab", "row"};
                             {"action": move, "tab", "row", "url", "new_url"} -> {"tab", "row"}; {"action": remove, "tab", "row", "url"}
  GET  /api/insights         pool / applied numbers (pay, places, categories) and the sheet's statuses
  GET  /api/answers?bank=    entries + version (bank other: answers_other.json); POST /api/answers {"bank", "entries",
                             "version"}. answers.json entries carry nuworks_only; leave_blank is kept while empty
  GET  /api/settings         settings (no secrets; logins: {sites, new}, never a password); POST /api/settings {"resume_label",
                             "week_start", "max_per_week", "accounts_email", "assist_effort", "assist_read_paths"}
  POST /api/logins/export    Bitwarden's import file of the logins not exported yet ({"every": true}: all) -> {"path", "count"}
  GET  /api/setup            wizard steps; POST /api/setup {"action": ack|client_upload|client_downloads|
                             sheet_create|sheet_link|resume_pick|resume_set|labels_read|label_set|terms_read|
                             prefs_save|discord_save|discord_test|discord_remove|schedule_on|schedule_off|launcher}
  GET  /api/logs, /api/file?path=   past runs; a screenshot or log under logs/ only
  POST /api/quit

FILES IT WRITES (all under local/, mode 700, except as noted)
  gui.lock          pid, port and a secret that only lets a second start ask for a window (removed on exit)
  browser_profile.lock   who uses the NUworks browser (pid, what, since)
  onboard.json      what the read-only NUworks reads found (resume labels, terms)
  local_config.json gains tos_ack, preferences, tools (saved tool paths) through the wizard (mode 644, as before)
  scheduler: ~/.config/systemd/user/nuauto-daily.{timer,service} or ~/Library/LaunchAgents/com.nuauto.daily.plist
  launcher: ~/.local/share/applications/nuauto.desktop (+ icon) or ~/Applications/NUauto.app

NOT YET PROVEN FOR REAL (only in demo mode so far)
- A real GUI apply with you watching; the Google and NUworks logins through the GUI; the resume-label and term
  reads against the real NUworks; the systemd/launchd toggle.
- The packaged app on a real desktop other than Fedora 44 (containers ran it on Ubuntu 22.04/24.04, Debian 12, Fedora
  and Arch; its own window was rendered on GTK 4 and on GTK 3 / Ubuntu 22.04 with a virtual X server). The macOS app
  on a real Mac: CI builds both (Apple Silicon, Intel) and runs their self-test, but nobody has opened one yet. No
  release has been published (that takes a v* tag).
