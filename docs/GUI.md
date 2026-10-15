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
  ~/.local/bin; an app icon). `nuauto gui` never runs on the homelab (it refuses there).
- The same app on the homelab (2026-10-14; "remote_app" or "remote_assist"; docs/REMOTE.md): nuauto-web serves this
  page and this API there too (gui.serve_remote, REMOTE mode), on the homelab's Tailscale address :8766, to paired
  devices (your phone). Every screen is the same; the page knows where it runs (state.where) and shows what only the
  laptop can do (state.laptop_only: logins, terminal windows, the laptop's files, Bitwarden, setup's file steps, Quit) as
  "On your laptop" with the reason; the server refuses those there (the Bitwarden card is left out). What it adds there:
  each assistant session's screen (#/assistant/<slot>), the queue in Company sites, Settings > This homelab (paired
  devices). Without a homelab none of it exists, and the laptop's app is unchanged.
- Each thing in one place (2026-10-14), with or without a homelab: Today has one list of what only you can do (the
  assistants waiting for you first, each item opening where you do it: no second Mark done or Open job there); Company
  sites has one assistants line (here, on the homelab, waiting for you; the homelab's queue), each session reached from
  its own row; Settings has one Homelab card (and setup one Homelab step); Pair this computer and Open NUauto on the
  homelab are there only.

PACKAGED APP (packaging/; .github/workflows/release.yml builds and smoke-tests it on every pull request (all but the
Intel Mac app, the slowest build), and builds all four and publishes them as a GitHub release on every v* tag)
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
                 internships are on and some wait: it opens Review on that list), what only you can do: the assistants
                 that wait for you first (approve, answer, did it go through: its screen, or "in its terminal window"),
                 then each item with its due date, soonest first; each item opens where you do it (Company sites,
                 Review), nothing done twice in two places
  Review         one pool job at a time. Approve mode: Approve (y) adds it to the sheet as Approved at once
                 (or, for one of your Proposed rows, which Review shows first, turns that row Approved);
                 Each card shows its Score (match + each bonus) and Order (why it sits there: closing soon,
                 your Proposed row, half score / half taste, or front-end last); the terminal viewer shows the Score.
                 Keys as in the CLI viewer: y n s u o, plus j/k scroll, space/b page, g/G top/bottom. Decisions are
                 sent behind the screen (the next job shows at once; a failed write brings you back to that job).
                 Undo puts the row back to Proposed; Not for me (n) rates it no; Skip (s); Back (u); Open (o).
                 Rate only mode: yes/no ratings for the taste model, nothing in the sheet.
                 Source switch, only when internships are on ("internships" in local_config.json, intern.py): "NUworks
                 co-ops" / "<term> internships" (Simplify's and vanshb03's lists; the postings are on the companies' own sites). Remembered
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
                 running shows "assistant running (slot N)" instead of Start, or "assistant waiting for you" / "approve in its window"
                 while it waits for you (assist.sessions attention; a desktop notification says so too); one the homelab runs
                 (remote_assist) shows "assistant running on the homelab: open" / "...: waiting for you: open" (its screen
                 in NUauto on the homelab, in your browser), from `nuauto assist sessions` over ssh every minute (never in
                 view or demo mode); it follows /api/state assistants). First one line for the whole tab
                 (assistants-bar): "Assistants: N of 3 running here · M on the homelab · K wait for you (Today lists
                 them)". On the homelab's own page the same line (no "here") plus the queue's (what starts by itself, its
                 hours, free slots, NUworks applications left); each row's place in the queue as a chip
                 (review.queue_view: starts next by itself / ready / what it waits for), Start now (remote start in tmux;
                 refused with the reason), a running session's Open (its screen) and "Sort the list by: the queue's
                 order". Then: Needs Human rows for the assistant (opens
                 `nuauto assist <row>` in a terminal window, or shows the command), "I applied myself", Drop (not applying: closed, a
                 duplicate, a bad location; sheet.drop: Failed, "Dropped <date>: <reason>" in Notes; never while an
                 assistant is on the row), company sites still
                 owed (Mark done), the NUworks side to retry, then the Other jobs card: the sheet's Other jobs tab's
                 Approved rows (read fresh; counted in the tab, not the menu), Start assistant (opens `nuauto assist other
                 <row>`), Open site, I applied myself, Drop, Add a job (opens Sheet on the Other jobs tab). No caps there; their
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
  Settings       every health check (run them now), logins (Google, NUworks, Claude Code, and Gmail: the read-only login
                 for the assistant's email codes, with a link to turn the Gmail API on), resume label, applications per
                 week (the weekly cap), week start;
                 the company-site assistant: account email, effort (low / medium / high), assistants at once (1-3, on each
                 machine; no homelab needed), the folders it may read, your writeups (assist_writeups: the first place it
                 looks for essays), its job-site logins (how many; Export for Bitwarden: the import file in ~/Downloads,
                 mode 600, and how to import it); Summer internships (optional; local_config "internships",
                 docs/PIPELINE.md): on / off, the term as the lists name it (a new year's term moves the lists' addresses
                 with it), the match % to enter the pool (empty: the co-ops' bar), each list's address (empty: not read);
                 Password manager (optional, for anyone; local_config "password_manager": "bitwarden"): Use Bitwarden / Stop
                 using it, Install it (npm @bitwarden/cli, then bw login, in a terminal window), Add N to Bitwarden (a terminal
                 window: bw unlock asks your master password itself, then `nuauto accounts export --bitwarden`), Your own logins
                 (words their addresses contain, "bitwarden_import") + Import my logins (a terminal: unlock, then `bw list items
                 --search <word> | nuauto accounts import - --only <word>` per word), Check now; Homelab (a source install,
                 never the packaged app; onboard.homelab_link): its ssh name and folder, Check (over ssh, read-only:
                 onboard.homelab_probe: its hostname, Tailscale address, NUauto / Claude / timers / lingering / the
                 assistant's tools there), Use this homelab (local_config's server fields; NUauto then needs a restart: the
                 card offers Quit), Set it up (a terminal window: `nuauto onboard homelab-setup`, docs/DEPLOY.md's steps,
                 each asked first; sudo asks for the homelab's password there), Log in to Claude there (ssh -t ... claude),
                 Stop using it (local mode again); with one in use, its code (From GitHub's main / From this computer
                 instead: deploy_from_git, a run of `nuauto onboard homelab-deploy on|off`: the code pushed once, the
                 deploy timer turned on or off; your fork's address, deploy_repo) and the Mark-done links' public address
                 (web_base_url; the homelab's web page restarts with it); in the same card, NUauto on your homelab (homelab mode only, docs/REMOTE.md): its page on /
                 off (remote_app), its assistant on / off (remote_assist, which needs the page: both pushed to the homelab,
                 nuauto-web restarted), Start sessions by itself, one switch for NUworks rows and one for Other jobs rows
                 (remote_auto {"nuworks", "other"}), its hours (remote_hours), assistants at once (assist_slots), Pair this
                 computer, Open it; setup, logs, quit. On the homelab's page one This homelab card: Paired devices (each
                 one, "this one", Unpair, Make a pairing link for another device) and its assistant's queue settings (no
                 on / off: the laptop's); the email and effort only (the transcript, folders, export: on your laptop); no
                 Password manager card (Bitwarden unlocks on the laptop), no Quit
  Setup          the wizard (below); opens by itself until setup is done (never on the homelab's page: setup is the
                 laptop's; there it says so, and the steps it can do are preferences, Discord and the resume label)
  Past runs      logs/ folders: actions.log and the screenshots before and after Submit
  Assistant      (the homelab's page; #/assistant/<slot>) one assistant session: its browser (the view's link and
                 password, the screen now), its terminal (Interrupt), its latest message, Ready to submit (what, on which
                 page, the list of what you haven't confirmed, the screen when it asked; Approve / Decline with a note), the
                 end question (Yes / No / No: do it on the laptop), Reply, End session, End: do it on the laptop. It asks
                 again every 4 s; what you type is kept (docs/REMOTE.md, AN ASSISTANT'S SCREEN)

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
  internships  optional: the same Summer internships part as Settings (done when on)
  extras       Discord webhook (checked with a GET, posts nothing until you press Test), automatic updates
               (local mode: systemd user timer / launchd agent at 08:00 and 18:00), app icon
  password_manager   optional: the same Password manager part as Settings (done when Bitwarden is on and its check is OK)
  homelab            optional, a source install only: the same Homelab card as Settings (done when NUauto runs with one;
                     then its NUauto on your homelab part shows there too: one step for all of it)

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
  homelab   doctor's homelab checks over ssh (homelab mode only; with remote_assist  15 min
            also the assistant's tools, its browser, Claude's folder trust, the review page)
  bitwarden only with password_manager bitwarden: bw there and logged in (`bw  15 min
            status`; locked is fine), the logins NUauto made not in the vault yet
            (Add N to Bitwarden); no chip while off; demo mode never uses a real bw
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
  timer, so an open GUI on a work-in-progress branch does not deploy it. With NUauto on the homelab the pull also
  merges the settings, ratings and answers changed on its page (sync.share), and this process takes the settings again.
- On the homelab (REMOTE: gui.serve_remote inside nuauto-web): web_listen_host:8766 only; a paired device's cookie
  (review.py) instead of the start secret; that server's own Host; the same X-NUauto / JSON / Origin rules on every POST
  (the pairing form: its Origin); Referrer-Policy same-origin. The page's own files are served without the cookie
  (public code; a link tapped in Discord arrives without it), every /api needs it (else 403 {"pair": true}).
  LAPTOP_ONLY actions, setup steps outside REMOTE_SETUP, the settings in REMOTE_SETTINGS_OFF, /api/quit and
  /api/logins/export are refused there with the reason. Its runs are nuauto-web's children (gui.lock holds its pid, for
  `--ui json`); a run you watch (Apply, the NUworks side) gets a virtual screen and its browser view (run_screen, slot 0;
  Task.browser_view: the link and password, shown in the live run). No idle quit, no desktop notifications, no pull
  (the homelab owns the data); it takes a changed local_config.json at once (reload_config). The update starts the
  homelab's own nuauto-daily.service (never a second copy beside the timer's).

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
- `nuauto gui --demo --remote [--no-open] [--port N] [--screenshots DIR]` (also `nuauto review demo`): the app as the
  homelab serves it (REMOTE), on 127.0.0.1, with demo state "homelab" (more company-site rows, the assistant on) and
  three made-up assistant sessions (review.demo_setup: waiting to submit, waiting for an answer, ended). Prints
  {"url": a pairing link, "port", "remote": true, ...}: open it, press Pair. --screenshots: homelab-<screen>-*.png
  (Today, Apply, Company sites, the three assistants, Settings, Setup).
- `nuauto gui --demo --linked [--no-open]`: the laptop's demo app linked to a demo homelab page of its own (a child
  `--demo --remote`; config.DEMO_LINK, gui.demo_link): Today's homelab items open their screens in the app, Company
  sites' Start on the homelab with each row's place in the homelab's queue, Apply's Run on: the homelab, Settings >
  Homelab > This app (Unlink / Link). Both on 127.0.0.1, made-up data, no ssh. Test: tests/test_link.py.
- One page for both machines: tests/screens.py opens every screen on the laptop's demo (test_gui), the linked demo
  (test_link) and the homelab's (test_homelab_app): the same menu, none empty, none showing an error. A change that
  should differ between the two goes behind state.where / REMOTE (or linked) with a check on each side.
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
  on the fake pages: Ctrl+C and GUI-gone stops, cap refusal, NUworks re-login states), test_health.py,
  test_homelab_app.py (the homelab's app in demo mode: pairing, refusals, sessions, queue, devices, a phone-width
  browser approving a Submit), test_review.py (its handler's rules and the sessions' backend).
- CI: .github/workflows/tests.yml runs `nuauto test` on Ubuntu and macOS for every pull request and every push to main
  (a newer push to a pull request stops its older run).

API (JSON; all need the cookie; POSTs need X-NUauto: 1)
  GET  /api/state            counts, week, to-do, last scan, setup_needed, the current task; internships {on, term,
                             review: how many wait for Review}; where (laptop | homelab), laptop_only {action: why} (the
                             homelab's page), remote_app (the laptop: the homelab's page address, when it has one); todo:
                             the assistants that wait for you first ({"kind": "assistant", "status", "text", "screen": its
                             screen, or null for a laptop session in its terminal window}), then site / company / urgent;
                             commit (the code this machine runs, short; both pages show it in the side bar); homelab_link
                             (the laptop, homelab page on: {on, error, commit: the homelab's, task: its running task
                             {id, kind, label, state}}, else null)
  GET  /api/health           the checks; POST /api/health/run {"groups": [...]}
  GET  /api/review?mode=&q=&category=&source=   the review queue (q / category narrow it; total, categories = the whole
                             queue; source=intern: the internships, else the co-ops; the reply says "source"; an internship
                             that could not be read has match null and comes last); GET /api/job/<id> one card (an
                             internship's adds source "simplify" (any list), url = the posting, posted_text (with its list), year_text, unread)
  POST /api/decide           {"id", "mode", "decision": approve|yes|no} -> {"row", "previous"}; an internship's approve adds
                             "tab": "other" (its row is in the Other jobs tab)
  POST /api/undo             {"id", "row", "previous"} (an internship also needs "tab": "other": its row goes back to
                             Proposed in the Other jobs tab); POST /api/review/done (push ratings, homelab mode)
  GET  /api/apply            Approved rows in apply order, week, why_not, recent runs
  POST /api/action           {"kind": apply ({"n", "order"} or {"row"})|update|login_google|login_nuworks|check_nuworks|login_claude|
                             install_firefox|install_claude|nuworks_side|assist|fix_permissions|review_pair|bitwarden_add|
                             bitwarden_import ({"only": [words]})|bitwarden_login|bitwarden_install, "args"} (the bitwarden_ ones
                             open a terminal window: Bitwarden asks for your master password there, never this page)
                             (assist: {"row", "tab": "other"} for the Other jobs tab; on the homelab it starts the row's
                             session in tmux: {"started", "message"}); the homelab's own: pair_device ({"name"} ->
                             {"link", "minutes"}), unpair ({"id"}: never this device -> {"gone", "devices"})
  GET  /api/task?after=N     the current task: state, new log lines, question, row, screenshot, countdown; browser_view
                             {url, password} (the homelab: its virtual screen's view, while it runs)
  GET  /api/assistant/<slot>  (homelab) one session's screen: review.view (status, message, approval {id, what, url, why,
                             waiting, shot, answered, by}, browser {url, password}, terminal, version); .../shot.png[?live=1]
  POST /api/assistant        (homelab) {"slot", "do": approve|decline|reply|interrupt|exit|laptop|finish, "id", "note",
                             "text", "answer": y|n|laptop} -> {"message", "closing"}
  GET  /api/devices          (homelab) the paired devices {id, name, paired} and this one's id
  POST /api/homelab          (laptop) Settings > Homelab: {"action": check ({"ssh", "folder"} -> what it has)|save ({"ssh",
                             "hostname", "folder", "listen"})|off|setup (a terminal window)|claude (a terminal window)}
  POST /api/answer           {"task", "id", "reply"}; POST /api/stop {"task", "force"}
  GET  /api/homelab/assistant/<slot>[/shot.png], /api/homelab/task?after=N, /api/homelab/file?path=   (laptop, linked: the
                             homelab's page's answers through the link: a session's screen, its run, their pictures;
                             a task comes with "where": "homelab" and its screenshot through this route)
  POST /api/homelab/assistant, /api/homelab/answer, /api/homelab/stop   (laptop, linked) a tap on a homelab session's
                             screen, its run's question, its Stop; refusals carry the homelab page's own words. POST
                             /api/action apply / nuworks_side / assist with "on": "homelab" runs it there (Start on the
                             homelab, Run on: the homelab); homelab_link / homelab_unlink: Settings > Homelab > This app
  GET  /api/company          agent / other / site / retry rows, sessions (the assistants running now: slot, row, tab,
                             company, host, since, attention: {kind waiting|approve, since, said} while it waits for you)
                             and slots (how many may); queue (the homelab: review.queue_view, or {"error"}); POST /api/mark {"action": applied|site|drop, "row", "url",
                             "why" (drop: closed|duplicate|location|other), "tab": "other" (Other jobs tab: applied, drop)}
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
                             "week_start", "max_per_week", "accounts_email", "assist_effort", "assist_read_paths",
                             "assist_writeups", "assist_slots" (1-3), "internships" ({"on", "term", "threshold", "lists"}),
                             "web_base_url", "password_manager" ("" | "bitwarden"), "bitwarden_import" [words], and with a homelab "remote_app",
                             "remote_assist", "remote_auto" (true / false, or {"nuworks": bool, "other": bool}: a tab left
                             out stays as it was), "remote_hours" [from, to], "assist_slots"}; GET adds password_manager
                             {on, check, waiting, own, only, bw}, homelab_assistant {available, on, app, auto (any tab),
                             auto_tabs {nuworks, other}, hours, slots, review_url}, where, homelab (the laptop:
                             onboard.homelab_link) and (the homelab) devices, this
  POST /api/logins/export    Bitwarden's import file of the logins not exported yet ({"every": true}: all) -> {"path", "count"}
  GET  /api/setup            wizard steps; POST /api/setup {"action": ack|client_upload|client_downloads|
                             sheet_create|sheet_link|resume_pick|resume_set|labels_read|label_set|terms_read|
                             prefs_save|discord_save|discord_test|discord_remove|schedule_on|schedule_off|launcher}
  GET  /api/logs, /api/file?path=   past runs (the assistant's own screenshots are in a run's browser/); a screenshot or
                             log under logs/ only
  POST /api/quit

FILES IT WRITES (all under local/, mode 700, except as noted)
  gui.lock          pid, port and a secret that only lets a second start ask for a window (removed on exit); on the
                    homelab nuauto-web's (its runs check it)
  settings.lock     local_config.json and profile.json are changed under it (Settings, setup, sync's merge)
  review_devices.json, review_pair.json   (homelab) the paired devices' key hashes, the open pairing links' hashes
  browser_profile.lock   who uses the NUworks browser (pid, what, since)
  onboard.json      what the read-only NUworks reads found (resume labels, terms)
  local_config.json gains tos_ack, preferences, tools (saved tool paths) through the wizard (mode 644, as before)
  scheduler: ~/.config/systemd/user/nuauto-daily.{timer,service} or ~/Library/LaunchAgents/com.nuauto.daily.plist
  launcher: ~/.local/share/applications/nuauto.desktop (+ icon) or ~/Applications/NUauto.app

NOT YET PROVEN FOR REAL (only in demo mode so far)
- A real GUI apply with you watching; the Google and NUworks logins through the GUI; the resume-label and term
  reads against the real NUworks; the systemd/launchd toggle.
- The app on the real homelab (2026-10-14): served by nuauto-web, used from a paired phone, an Apply run there on its
  virtual screen; Settings > Homelab's Check and Set it up against a real homelab.
- The packaged app on a real desktop other than Fedora 44 (containers ran it on Ubuntu 22.04/24.04, Debian 12, Fedora
  and Arch; its own window was rendered on GTK 4 and on GTK 3 / Ubuntu 22.04 with a virtual X server). The macOS app
  on a real Mac: CI builds both (Apple Silicon on every pull request, Intel on v* tags) and runs their self-test, but nobody has opened one yet. No
  release has been published (that takes a v* tag).
