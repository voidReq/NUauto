GUI: `nuauto gui` (the NUauto window)
(Keep this current when you change the GUI, its API, demo mode or the setup wizard. Code: src/nuauto/gui.py,
gui_static/, onboard.py, health.py, demo.py, window.py, link.py. Safety rules and their tests: docs/SAFETY.md.)

WHAT IT IS
- A local app on your own computer; nothing is hosted anywhere. `nuauto gui` (or the packaged app) starts a server on
  127.0.0.1 (stdlib ThreadingHTTPServer, like web.py) that only this computer can reach, and shows its one page: plain
  HTML/CSS/JS in src/nuauto/gui_static/ (index.html, app.js, setup.js, app.css; no build step, nothing loaded from the
  internet, light/dark from the system).
- The window (window.py; `nuauto _window` says which one this system gets): its own window on macOS (pywebview: the
  system's WebKit) and on Linux when the system's python3 has GTK + WebKitGTK (window_gtk.py, GTK 4 or 3; most GNOME
  desktops); else Chrome/Chromium/Brave/Edge in app mode (--app), else a browser tab (--browser forces a tab). Closing
  its own window quits NUauto (a run in progress stops like Ctrl+C). Starting it again opens another window on the
  running one (POST /api/window; with --no-open it refuses instead, with --port it starts a server of its own).
- The CLI stays. The GUI calls the same functions; long or browser work runs as the same `nuauto ...` commands in
  child processes, one at a time (gui.Task): apply, update, logins, the NUworks check and side of a company-site
  application, installs, the self-test, the wizard's NUworks reads, the homelab deploy switch.
- It quits after 30 minutes with no request and nothing running (the open page asks every 3 s, so: no window open),
  or with Settings > App > Quit. A browser-tab or app-mode window keeps running when closed; its own window quits it.
- Linux and macOS. Install: install.sh (uv brings Python 3.12; Playwright's Firefox; the `nuauto` command in
  ~/.local/bin; an app icon). `nuauto gui` refuses to run on the homelab.
- The same app on the homelab (2026-10-14; "remote_app" or "remote_assist"; docs/REMOTE.md): nuauto-web serves this
  page and API there too (gui.serve_remote, REMOTE mode), on the homelab's Tailscale address :8766, to paired devices
  (your phone). Every screen is the same; the page knows where it runs (state.where) and shows what only the laptop can
  do (state.laptop_only: logins, terminal windows, the laptop's files, Bitwarden, setup's file steps, Quit) as "On your
  laptop" with the reason; the server refuses those there. It adds each assistant session's screen (#/assistant/<slot>),
  the queue on Apply's rows, and Settings > Homelab > This homelab (paired devices). Without a homelab none of it exists.
- Each thing in one place (2026-10-14), with or without a homelab: one Today list, one Assistants tab, one Homelab tab
  in Settings, one Homelab step in setup; Pair this computer and Open NUauto on the homelab are the laptop's only.

PACKAGED APP (packaging/; .github/workflows/release.yml builds and smoke-tests it on every pull request (all but the
Intel Mac app, the slowest build), and builds all four and publishes them as a GitHub release on every v* tag)
- Linux: an AppImage per architecture (built on Ubuntu 22.04: runs on distributions from 2022 on). macOS: a DMG with
  NUauto.app per architecture (wheels for macOS 12+). PyInstaller, one folder: its own Python 3.12 and libraries, the
  code, prompts/, the page and window_gtk.py. Not signed by Apple: the first open needs Privacy & Security > Open
  Anyway. About 115 MB (AppImage).
- Same code as a source install, with config.FROZEN: code and prompts come from the bundle (read-only); your files go in
  the app-data folder (~/.local/share/NUauto, ~/Library/Application Support/NUauto); always local mode (no homelab).
  Started with nothing it opens the window; from a terminal it is the whole `nuauto` command (`NUauto-x86_64.AppImage
  apply`). It runs itself for its children (config.self_cmd) and, for a terminal window or a timer, the AppImage file or
  the app binary (config.self_exe). Internal commands: `_assist` (the assistant's hook and answer bank, two words as
  check_bash requires), `_playwright` (Playwright's CLI: install firefox), `_window`.
- Playwright's Firefox is downloaded on first run into the usual cache (~/.cache/ms-playwright, ~/Library/Caches/
  ms-playwright; PLAYWRIGHT_BROWSERS_PATH is set to it, because frozen Playwright would look inside the bundle).
  Programs started from the app get an environment without its PyInstaller / AppImage variables (window.system_env).
- Updates: once a day the app asks GitHub's public API for the latest release (health check "Version" in Settings >
  Status; Download opens the release page). Your files stay when you replace the app.
- Build one yourself: `sh packaging/build.sh` (needs uv), check it with `packaging/build/venv/bin/python
  packaging/smoke.py <the AppImage, or NUauto.app/Contents/MacOS/NUauto>`; other distributions: `sh
  packaging/distros.sh <AppImage>` (podman or docker; logs in packaging/build/distros/).

SCREENS
The menu (side bar; counts on Review and Apply; its foot shows the version and the code's commit): Today, Review,
Apply, Insights, Answers, Settings. Not in the menu: Setup (#/setup), Past runs (#/logs), an assistant's screen
(#/assistant/<slot>; it counts as Apply). The old #/company, #/other and #/sheet open Apply. At the top: the status
chips (HEALTH). Banners: Demo mode, View only, and on the homelab's page "Can't reach NUauto on your homelab" (it tries
again by itself). A task that is running (not an Apply run on Apply itself) shows in a bar at the bottom with Log and Stop.
A question from a run (an unknown form field, an option to pick, "did it submit?") is a dialog: answers.JsonIO below.
  Today          "Finish setting up" (until setup is done), Needs attention (health problems with fix buttons), This
                 week (applied of the limit, room left, the total), Next steps (Review N new jobs; "Review N internships"
                 when internships are on and some wait; Apply to N approved; Check for new jobs now), Waiting on you
                 (until 2026-10-27 "Only you can do these"; the assistant does the company sites now, so it is just what
                 waits on you): the assistants that wait for you first (approve, answer, did it go through: its screen, or "in
                 its terminal window"), then each item with its due date, soonest first (a company site to finish, a
                 company-site application, a job closing soon); each opens where you do it (Apply, Review)
  Review         one pool job at a time. Approve mode: Approve (y) adds it to the sheet as Approved at once
                 (or, for one of your Proposed rows, which Review shows first, turns that row Approved).
                 Each card shows Match (and the bar it needed), Score (+ your taste once the model has 5 yes and 5 no),
                 Order (why it sits there: closing within a week, your Proposed row, half score / half taste, a kind
                 of work set to come last, or "rating order" in Rate only), Closes, Pay, Flags, Skills, and "Also on
                 the company site?" when the posting says so; the terminal viewer shows the Score.
                 Keys as in the CLI viewer: y n s u o, plus j/k scroll, space/b page, g/G top/bottom. Decisions are
                 sent behind the screen (the next job shows at once; a failed write brings you back to that job).
                 Undo (in the toast) puts the row back to Proposed; Not for me (n) rates it no; Skip (s); Back (u);
                 Open (o). Rate only mode: yes/no ratings for the taste model, nothing in the sheet. Leaving the
                 screen pushes the ratings to the homelab (not in view mode).
                 Source switch, only when internships are on ("internships" in local_config.json, intern.py): "NUworks
                 co-ops" / "<term> internships" (the two public internship lists; the postings are on the companies'
                 own sites), remembered for the session (Today's "Review N internships" picks the internships); changing
                 it reloads the list. Everything above works the same on the internships, except: Approve adds an
                 Approved row to the sheet's Other jobs tab (no weekly cap), and Undo puts it back to Proposed there; a
                 job whose Other jobs row is Proposed shows first, flagged "already Proposed (Other jobs row N)"; the
                 button says "Open posting"; cards add Posted and, when the posting says so, Who can apply; a posting
                 that could not be read comes last, "not scored" (Why says what failed: judge it from its link).
                 Search box (words or "quoted phrases", all must appear, any case, in title, company, place, kind of
                 work, tag, skills, description, qualifications) and a kind-of-work menu narrow the list; the order
                 stays. Esc clears the search. A text search shows every match as its own card (8 at a time, "Show
                 more"; the posting cut short), each with its own Approve / Not for me buttons (y / n keys are off there;
                 a decided card turns into one line with Change). The job you were on is left undecided; clearing the
                 search comes back to it. A kind-of-work choice alone stays one job at a time.
  Apply          (2026-10-16, my request: one place per sheet tab; the Sheet screen merged in) three tabs, each with its
                 count, the one you picked remembered for the session: NUworks co-ops (the sheet's NUworks tab: the weekly
                 limit), "<term> internships" ("Other jobs" when internships are off: the sheet's Other jobs tab, no
                 limits), Assistants. Above them the live Apply run (a finished run's box has × to close it, per run;
                 2026-10-22, my request).
                 A sheet tab's head: (NUworks) the week's count, Run on (this computer / the homelab, when linked), At most
                 and Start, How Start works folded; (the other tab, laid out the same, 2026-10-18, my request: "same
                 buttons, same subtext") no limit, how many are ready and applied, Run on, How Apply works folded. Then one
                 toolbar: Search this tab (words, all must appear, any case, in company, title, notes, site, kind of work,
                 status, pay; Ctrl+F; Esc clears), a kind-of-work menu (with counts), Sort, Add a job. Then the rows in
                 sections (<details>: what is left to do open, the rest folded, each with its count; your choice kept for
                 the session; a search opens every section with a match).
                 NUworks sections: Ready to apply (Approved, numbered in the order Start goes: the Sort menu IS that
                 order: closing within a week first, then score (default), score, match %, closing soonest, pay per hour
                 (insights.hourly; unclear pay last); the server sorts, apply.apply_order, GET /api/jobs?tab=nuworks&order=,
                 and Start runs `nuauto apply --order <it>`; filtered, "Start still goes through all N"), Company site
                 needed (Needs Human rows stopped at an external application: the assistant), By hand on NUworks (Needs
                 Human rows only you can finish there: a cover letter, a transcript; the assistant can work them on NUworks
                 with you, or a chip says why not), Still owed (submitted on NUworks, the company site still wants you:
                 Mark the company site done; the NUworks side to send again: Submit on NUworks too), Applied ("not on
                 NUworks" chip when NUworks has no application for it: it doesn't count toward the total; the job's own
                 hiring stage at the last check (jobs.application_answer): "job interviewing", then amber "job stopped
                 interviewing" or "job filled" (sheet.maybe_filled: it may be filled; withdrawing on NUworks frees its
                 place in the total). Until 2026-10-26 an "interviewing (NUworks)" chip showed the job's is_interviewing
                 flag as if it were yours: it is about the job, set also on jobs you never applied to, and NUworks never
                 shows a student whom an employer picked), Accepted, Rejected, Proposed, Dropped or failed, Other rows;
                 Recent runs here at the end. The
                 other tab: Ready to apply (Approved: the assistant; an internship whose posting has closed leaves it for
                 Closed postings, folded: sections.expired, not changed in the sheet, Drop clears it; the homelab's queue
                 skips them too, intern.closes_func), Applied, Accepted, Rejected, Proposed, Dropped or
                 failed, Other rows (Sort: score, match %, pay, sheet order, and on the homelab's page the queue's order
                 first; it changes the view only).
                 Every row looks alike: its company (click it: a dropdown, 2026-10-27) with the role under it, score / match % and pay ("score 105 = 75% match + 10 Boston + 20
                 security" when bonuses add to it: Start goes by score), a line about it (site, notes), chips (when it
                 closes, amber within a week, red once past; an internship's from its posting (gui.intern_facts), else
                 "no close date · posted N days ago"; "asks for juniors+" (jobs.year_asked: the class year a posting asks for,
                 when above yours; from NUworks' class levels, the scorer's class_req, an internship's year_req); the queue's
                 place; "do it on the laptop" when the homelab's browser was refused), what to do next, Open, and More (⋯;
                 on a phone in the row's top right corner). The dropdown (GET /api/job?tab=&url=, read when it opens, open
                 rows stay open across redraws): where, pay, close date; kind of work and your tags (AR/XR...), the class
                 year asked, job functions, skills and the like (NUworks' own labels); the posting, cut short with
                 "Show the whole posting". Nothing is shown for a job NUauto holds no posting for. (The "may also want the
                 company's site" chip on Ready to apply rows was removed 2026-10-27: such a job lands in Company site
                 needed by itself.)
                 Next: Apply on an Approved NUworks row, after a confirm, just that row (`nuauto apply --row N`); while an
                 Apply run goes it is Add to queue: the row joins the run's queue (`--queue FILE`, gui.queue_row; done next
                 in the order clicked; the button shows Queued #k and takes it out again (apply_unqueue); Stop empties it;
                 rows queued just as the run ended get a run of their own, gui.apply_done). On an assistant's row Apply
                 starts the assistant, where Run on says (up to "Assistants at once", 1-3, side by side; a row with one
                 running shows "assistant running (slot N)", "assistant waiting for you" or "approve in its window" (assist.
                 sessions attention; a desktop notification says so too); every slot in use greys it out; one the homelab
                 runs (remote_assist) shows "assistant running on the homelab: open" / "...: waiting for you: open", its
                 screen in the app when linked, else in the browser). Mark the company site done; Submit on NUworks too.
                 More: on a sent row Mark accepted / Mark rejected (or Back to Applied: sheet.set_outcome), I applied
                 myself, Drop (closed, a duplicate, a bad location, other; sheet.drop: Failed, "Dropped <date>: <reason>"
                 in Notes; only Approved / Needs Human rows, never while an assistant is on the row), Move to the other tab
                 (asks for the link there), Remove from the sheet (clears the row's cells: no row is renumbered). What a row
                 can't do is greyed out with why: Applied rows are never removed and never leave the NUworks tab (an
                 Applied Other job may move to NUworks: there it counts toward the limits); unresolved Submit clicks are
                 locked; nothing moves while a run is on.
                 Add a job (manage.py; a dialog): NUworks co-op or Other job, Approved or Proposed, link; a NUworks link
                 fills company / title / match from your job data (/api/sheet/lookup also says which tab a link belongs
                 in); the screen then shows the tab it went to.
                 Homelab sessions are read through the link every 20 s, else `nuauto assist sessions` over ssh every
                 minute (3 s for 45 s after a start; never in view or demo mode). On the homelab's own page and on the
                 linked laptop each assistant row shows its place in the queue (review.queue_view: starts next by itself /
                 ready / what it waits for) and Apply starts it there (the button stays "Starting…" until its session
                 shows, up to 45 s; refused with the reason otherwise).
                 Assistants tab: the one line ("Assistants: N of 3 running here · M on the homelab · K wait for you (Today
                 lists them)"; on the homelab's page also the queue's: what starts by itself, its hours, free slots, NUworks
                 applications left), then every session running now, here and on the homelab: the job, its site, where
                 (this computer / the homelab, slot), since when, working / waiting for you / wants to Submit, and where you
                 answer it (its screen; a laptop session: its terminal window).
                 The lists follow the sheet by themselves: /api/state carries sheet_version (+1 each time a read finds
                 either tab changed); the server reads both tabs again after every finished row and run, when an assistant
                 session starts or ends, and when they are 30 s old; the open screen reloads when sheet_version changes
                 (each /api/jobs answer says which version its rows are as new as, so its own reload is not followed by a
                 second one that would close an open menu). Each tab is drawn as soon as its own answer comes (SPEED).
                 "At most" keeps what you typed. The menu's count is Approved + the assistant's rows + retries.
  Insights       NUworks co-ops / "<term> internships" switch when internships are on (2026-10-16; remembered for the
                 session): each its own pool, applications and sheet tab (insights.collect / collect_intern). Where that
                 sheet tab stands (a status ring: arcs in an order checked for color-blind separation, with a labeled
                 legend), then for Your pool / Applied: median and usual pay, share in Massachusetts, closing this week
                 (pool) or pay listed (applied); an hourly-pay histogram; by state (top 5 + Other, top cities); kind of
                 work; skills and tags (NUworks' skill labels, top 20; your own tags from the preferences found in the text; an
                 internship list gives no skills), job function, class year asked (jobs.year_asked).
                 insights.py, also `nuauto insights` in a terminal. Read-only; pay that doesn't read as $10-150/h
                 counts as not listed
  Answers        the answer bank as a table (exact-match rules unchanged); locked while a run uses it. NUworks / Other
                 jobs switch: the Other jobs bank (answers_other.json) is its own table; the NUworks one has a "NUworks
                 only" column (never used for Other jobs; default on for start/end date and co-op term). Columns: question,
                 answer, type (any / text / dropdown), always ask, other wordings. Search (Ctrl+F) narrows it to questions,
                 answers and aliases with that text. Other wordings (aliases): a dialog, one per line (a wording may hold
                 commas). Save leaves out a wording that does nothing (its own question, a repeat, one an entry above
                 already has) and refuses a question that is another entry's wording, naming both, or a table a run
                 changed since you opened it (reload, edit again)
  Ctrl+F         (Cmd+F on a Mac) goes to the screen's own search box: Apply, Review, Answers ([data-find]); elsewhere, and in
                 a dialog, the browser's own find
  Settings       in tabs (#/settings/<tab>; plain #/settings: the last tab this session, else Status), the same on the
                 homelab's page (2026-10-22, my request: it was one long page). A health poll redraws only Status's list
                 of checks, never a form being typed in.
                 Status: every health check (Run all checks now); Logins (Google, NUworks with Check now, Claude Code, and
                 Gmail: the read-only login for the assistant's email codes, with a link to turn the Gmail API on).
                 Jobs: What you're looking for (Setup's preferences step, the same form and save: term, year, match bars,
                 kinds of work with their bonuses and lower bars, tags; saving rebuilds the pool); Summer internships
                 (optional; local_config "internships", docs/PIPELINE.md): on / off, the term as the lists name it (a new
                 year's term moves the lists' addresses with it), the match % to enter the pool (empty: the co-ops' bar),
                 each list's address (empty: not read).
                 Applying: "Applying on NUworks": applications per week (the weekly cap, 1 to sheet.MAX_PER_WEEK_CEILING =
                 30), week start, the resume NUauto picks (resume label), a link to your sheet (another sheet: Setup); Your
                 files (the resume and the transcript: YOUR FILES).
                 Assistant: the company-site assistant: email for job-site accounts, effort (low / medium / high),
                 assistants at once (1-3, on each computer; no homelab needed), the folders it may read, writeups
                 (assist_writeups: the first place it looks for essays), its job-site logins (how many; with Bitwarden off,
                 Export for Bitwarden: the import file in ~/Downloads, mode 600). Password manager (optional; local_config
                 "password_manager": "bitwarden"): Use Bitwarden / Stop using it, Install it (npm @bitwarden/cli, then bw
                 login, in a terminal window), Add N to Bitwarden (a terminal window: bw unlock asks your master password
                 itself, then `nuauto accounts export --bitwarden`), Your own logins (words their addresses contain,
                 "bitwarden_import") + Import my logins (a terminal: unlock, then `bw list items --search <word> | nuauto
                 accounts import - --only <word>` per word), Check now.
                 Homelab (a source install, never the packaged app; onboard.homelab_link): not in use: its ssh name and
                 folder, Check (over ssh, read-only: onboard.homelab_probe: hostname, Tailscale address, NUauto / Claude /
                 timers / lingering / the assistant's tools there), Use this homelab (local_config's server fields; NUauto
                 then needs a restart: the card offers Quit). In use: Set it up (a terminal window: `nuauto onboard
                 homelab-setup`, docs/DEPLOY.md's steps, each asked first), Log in to Claude there (ssh -t ... claude),
                 Stop using it (local mode again); folded away: another homelab's ssh name and folder, and Advanced: its
                 code (From GitHub's main / From this computer instead: deploy_from_git, a run of `nuauto onboard
                 homelab-deploy on|off`; your fork's address, deploy_repo) and the Mark-done links' public address
                 (web_base_url; the homelab's web page restarts with it). Its own card, NUauto on your homelab (homelab
                 mode only, docs/REMOTE.md): its page on / off (remote_app), its assistant on / off (remote_assist, which
                 needs the page: both pushed to the homelab, nuauto-web restarted), Start sessions by itself (a switch for
                 NUworks rows and one for Other jobs rows: remote_auto {"nuworks", "other"}), its hours (remote_hours),
                 Pair this computer, Open it, This app (link.py: Link this app / Unlink).
                 App: Discord, automatic updates, app icon (Setup's extras step, the same body); Setup (open it, Run a
                 self-test, Logs and screenshots); About (version, folders, Quit).
                 On the homelab's page: Homelab is This homelab (Paired devices: Unpair, Make a pairing link for another
                 device) and Its assistant (its queue settings; on / off is the laptop's); Assistant: the email, effort
                 and assistants at once only, no Password manager card; Applying: Your files read-only; App: Discord
                 only, no Quit
  Setup          the wizard (below); opens by itself until setup is done (gui.setup_needed: terms of use acknowledged, a sheet, preferences,
                 a resume file and label, a Google login; never on the homelab's page: setup is the laptop's; there it
                 says so, and the steps it can do are preferences, Discord and the resume label)
  Past runs      (#/logs; linked from Settings > App and Apply's recent runs) the newest 60 logs/ folders: actions.log
                 and the screenshots before and after Submit (an assistant's own are in browser/)
  Assistant      (#/assistant/<slot>; the homelab's page; on the linked laptop #/assistant/h<slot> shows the homelab's)
                 one assistant session: its browser (the view's link and password, the screen now; captchas are yours),
                 its terminal (Interrupt), its latest message, Ready to submit (what, on which page, what you haven't
                 confirmed, the screen when it asked; Approve / Decline with a note), the end question (Yes / No / No: do
                 it on the laptop), a question Claude Code asks in its own terminal (answer it there; the reply box stays
                 off), Reply, End session, End: do it on the laptop. It asks again every 4 s; what you type is kept
                 (docs/REMOTE.md, AN ASSISTANT'S SCREEN)

SETUP WIZARD (onboard.py + gui_static/setup.js; each step checks itself; a step opens when you click it, the first one
not done is open at the start; "You're set up" + Go to Today at the end; Settings reuses some steps' bodies)
  welcome            NUworks terms of use read (local_config tos_ack) and the limits (it shows the default 11 a week and 99
                     in total, whatever your setting says)
  tools              Playwright's Firefox starts; Claude Code installed and logged in (install / log in buttons); Node.js and
                     Chrome, shown for the assistant (optional)
  google_client      your own OAuth client: linked Google Cloud steps, drop the JSON (or use the one found in Downloads; only
                     a Desktop-app client is accepted; saved local/client_secret.json, mode 600)
  google_login       the Google login (a browser opens Google's own page)
  sheet              create one (Sheets API spreadsheets.create; gspread's create needs the Drive API) or paste the
                     address (ID extracted, access tested). Empty = set up; other data = refused, never overwritten.
  resume             drop the PDF or choose it (the page's own file chooser), or one it found (PDFs called resume / CV in
                     Documents, Downloads, Desktop); the PDF must have text. NUauto keeps its own copy (YOUR FILES)
  nuworks            login (Firefox window; you type nothing into NUauto), then the resume label read from one job's
                     real Apply popup (read-only: opens it, reads the Resume dropdown, Cancel)
  preferences        what you study first (fields.py: Engineering or CS, Business, Health sciences or nursing, Natural
                     sciences or math, Social sciences or humanities, Arts, media or design): picking one fills in that
                     field's starting values (after a confirm; term, year, bars, home state and tags stay): Claude's
                     description of you, the roles to keep / drop, major words, and the kinds of work (name, what counts,
                     ranking bonus, lower bar, always last; add / remove; Other always there, last). Then term (NUworks' own
                     list: "Read the list from NUworks"; onboard.find_terms), year, graduation year, match needed (and for
                     the year above yours), home state and its bonus, tags -> local_config.json "preferences"
                     (docs/PIPELINE.md). onboard.check_prefs checks it all. A first setup has no field picked and Save asks
                     for one. Saving rebuilds the pool (homelab mode: pushes). The resume-label read (nuworks step) tries
                     up to 4 jobs (onboard.a_job_urls), so it works before the pool has anything for you.
  extras             Discord webhook (checked with a GET, posts nothing until you press Test), automatic updates
                     (local mode: systemd user timer / launchd agent at 08:00 and 18:00; with a homelab it says the
                     homelab checks), app icon
  internships        optional: the same Summer internships part as Settings (done when on)
  password_manager   optional: the same Password manager part as Settings (done when Bitwarden is on and its check is OK)
  homelab            optional, a source install only: the same Homelab card as Settings (done when NUauto runs with one;
                     then its NUauto on your homelab part shows there too: one step for all of it)

SPEED (2026-10-22, my request: "the apply page takes quite a while to load")
- Google Sheets: the opened spreadsheet and its tabs are kept 5 minutes (sheet.OPEN; a read is one trip to Google,
  about 0.25 s); rows are always read fresh. Opened again on a new login, an expiring token or a refused request.
- Apply: answered from the rows read last (the NUworks tab's: up to 60 s, gui.ROWS_FOR; the Other jobs tab's: up to 30 s,
  gui.other_rows); opening the screen (recheck=1) reads both again in the background, and a change reloads the lists.
- Review: the list (pool, taste model, order) is kept until data/, your settings or the sheet's rows change
  (gui.review_lists, keyed by every data/ file's size and time: a few ms); searches and filters only narrow it. The
  taste model trains again only when your ratings or a job's text change (jobs.taste_for). The lists are made ahead at
  start and a few seconds after your last decision (warm_review). (2026-10-22, my request: Review still took 0.5-3 s)
  Review asks with quick=1: the list made last comes at once (about 10 ms), never waiting on Google or a rebuild; when
  something changed since (stale) the page asks again without quick and swaps the new list in: once you moved (decided, skipped, went back) it keeps
  your place; before that it starts at the new list's first job (a quick list's first may no longer come first). A job
  decided here after a list was made is left out of it (gui._decided). The monitor makes every kept list again when its
  rows, data/ or settings changed (warm_changed, every 15 s), so stale is rare. Each source (co-ops / internships) has
  its own kept list.
- Buttons: one whose click starts something slower shows it at once (aria-busy, busyWhile) and takes no second click
  until it is done; sheet changes show "Updating the sheet…". Switching screens shows Loading… at once; a screen you
  already left never takes over.

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
  updates   age of the last scan (data/scans.json); if the last update stopped    every minute
            (data/last_run.json), why, with its fix: Log in to NUworks, Run it again,
            Log in to Claude (Code / there). The homelab check then leaves out doctor's
            own "last update run stopped" line (one place)
  app       "Version": the packaged app asks GitHub for a newer release (Download);   daily
            a source install says to update with git
- The groups the monitor runs (gui.App.GROUPS): quick (files, resume, google, nuworks_saved, updates), sheet, claude,
  firefox, discord, homelab, bitwarden, nuworks, claude_live, app. The homelab's page leaves out homelab, bitwarden and app
  (the laptop's); view mode runs only quick and sheet.
- The status chips at the top (Google, Sheet, NUworks, Claude Code, Homelab, Bitwarden; the last two only once used): click
  one for its detail, its fix button and Check now (runs that check's groups again, RECHECK in app.js: google quick +
  sheet, nuworks quick + nuworks, the others their own; "Checking…" until done, the result shows in place). Not in view mode.
- Checks never open a login page, show a secret or change the sheet. A check turning bad (after the first
  round) sends a desktop notification (notify-send / osascript; none in demo mode or on the homelab).
- With the GUI closed, the scheduled update (homelab, or the laptop's timer) covers it: daily.py warns about the Google
  login and Claude Code being logged out before it scores anything.

SAFETY MODEL (details and tests: docs/SAFETY.md)
- Network: 127.0.0.1 only, random port. A secret made at each start is in the URL opened once; the page swaps it
  for an HttpOnly SameSite=Strict cookie (nuauto_<port>). Every request needs that cookie and a Host of
  127.0.0.1:<port> or localhost:<port> (DNS rebinding). Every POST also needs X-NUauto: 1 and a JSON body (other
  websites can't send that), and a matching Origin if one is sent. CSP: only this server's own files.
- The page never receives a secret (token, cookies, webhook, client secret): only "set / not set".
- Runs: `nuauto apply --ui json` / `nuauto assist nuworks <row> --ui json` work only as a child of the running GUI
  (its pid in local/gui.lock); `nuauto apply` itself never runs from a Claude Code shell (CLAUDECODE). The real GUI
  may be opened from one (2026-10-16): its own window, for you to click Start; with --no-open it refuses there (it
  would print the agent its secret; with its window, the line it prints there has its address only, gui.printed_url).
  Agents test with --demo (fake) or --view (real data, read-only, below). Apply, logins and the NUworks side always get
  a visible browser (gui.VISIBLE: AUTO_HEADLESS cleared). Approved in the sheet stays the only go-ahead; caps unchanged.
- Questions (answers.JsonIO): one JSON line per question on the child's stdout ("::nuauto:: " + JSON), the answer as
  one JSON line on its stdin. The same rules as typing: an option must be one of the choices, blank = stop (Skip this
  job = Needs Human), always-ask answers are never saved.
- Stop: SIGINT to the run (the KeyboardInterrupt path Ctrl+C takes: nothing submitted = row stays Approved,
  Submit already clicked = Needs Human). Force stop: SIGKILL to the whole process group. If the GUI dies, the
  child's stdin closes and it stops the same way.
- One browser at a time: browser.lock_profile (flock on local/browser_profile.lock), held from launch to close. A run
  you start first stops the background NUworks check.
- Sync (homelab mode): pull at start and every 30 minutes; push after Review and the Google login; never on a timer,
  so an open GUI on a work-in-progress branch does not deploy it. With NUauto on the homelab the pull also merges the
  settings, ratings and answers changed on its page (sync.share).
- On the homelab (REMOTE: gui.serve_remote inside nuauto-web): web_listen_host:8766 only; a paired device's cookie
  (review.py; /pair makes it from a one-time link) instead of the start secret; that server's own Host; the same
  X-NUauto / JSON / Origin rules on every POST (the pairing form: its Origin); Referrer-Policy same-origin. The page's own
  files are served without the cookie (public code; a link tapped in Discord arrives without it), every /api needs it
  (else 403 {"pair": true}). LAPTOP_ONLY actions, setup steps outside REMOTE_SETUP, the settings in REMOTE_SETTINGS_OFF,
  /api/quit, /api/files and /api/logins/export are refused there with the reason. Its runs are nuauto-web's children
  (gui.lock holds its pid, for `--ui json`); a run you watch gets a virtual screen and its browser view (run_screen,
  slot 0; Task.browser_view: the link and password, shown in the live run). No idle quit, no desktop notifications, no
  pull (the homelab owns the data); reload_config takes a changed local_config.json at once. The update starts the
  homelab's own nuauto-daily.service (never a second copy beside the timer's).

DEMO MODE (demo.py) AND TESTING WITH AGENTS
- `nuauto gui --demo` makes a temp folder (NUAUTO_STATE_DIR) and runs everything on fakes: the sheet is a JSON file
  (the real sheet.py code runs on it), NUworks is a few fake pages served inside Playwright (the real apply.py fills
  and submits them; the browser's proxy does not exist, so nothing reaches the network), claude is a fake script,
  the update adds held-back jobs. Nothing real can be read or sent.
- `--demo-state a,b`: fresh (the wizard from the start), google-expired, claude-logged-out, nuworks-relogin,
  nuworks-password, cap-reached, homelab (more company-site rows; `--remote` and `--linked` add it themselves).
  `python -m nuauto.demo state <name> on|off` flips one while it runs.
- `--no-open --port 0`: prints one JSON line {"url", "port", "demo", "view", "state_dir"} when ready; open the url (it
  carries the secret) in Playwright. Everything on screen has a JSON API (below); key controls have data-testid.
- `nuauto gui --demo --screenshots DIR`: every screen (each Settings tab too), light/dark, wide/narrow, as PNGs (plus
  Review's internship list: review-intern*); then it quits.
- `nuauto gui --demo --remote [--no-open] [--port N] [--screenshots DIR]` (also `nuauto review demo`, which never opens a
  window): the app as the homelab serves it (REMOTE), on 127.0.0.1, with demo state "homelab" and three made-up
  assistant sessions (review.demo_setup: waiting to submit, waiting for an answer, ended). Prints {"url": a pairing
  link, "port", "remote": true, ...}: open it, press Pair. --screenshots: homelab-<screen>-*.png. `--remote` without
  `--demo` is refused.
- `nuauto gui --demo --linked [--no-open]`: the laptop's demo app linked to a demo homelab page of its own (a child
  `--demo --remote`; config.DEMO_LINK, gui.demo_link): the homelab's sessions open in the app, the assistant's Apply on
  the homelab with each row's queue place, Apply's Run on: the homelab, Settings > Homelab > This app (Unlink / Link).
  Both on 127.0.0.1, made-up data, no ssh. Test: tests/test_link.py.
- One page for both machines: tests/screens.py opens every screen on the laptop's demo (test_gui), the linked demo
  (test_link) and the homelab's (test_homelab_app): the same menu, none empty, none showing an error. A difference
  goes behind state.where / REMOTE (or linked) with a check on each side.
- `nuauto selftest` (also Settings > App > Run a self-test): the whole demo flow in a hidden browser (Firefox installed if
  missing; approve, apply with a question, the fake sheet), in its own temp folder; exit 0 = this install works.
- Demo runs nobody watches use a hidden browser: with NUAUTO_DEMO_HEADLESS=1 (tests and the self-test set it) or no
  screen (CI, containers). Real runs always open a visible browser.
- Agents: test with --demo or --view. They may open the real GUI's window for you (never --no-open), never click
  Start in a real one.

VIEW MODE (`nuauto gui --view`; gui.VIEW; added 2026-10-09 so an agent can look at your real screens)
- Your real sheet and job data, read-only. Allowed inside a Claude Code shell (the real GUI is not). Prints its URL like
  --no-open (no window); open it in Playwright. Quit: POST /api/quit or Ctrl+C. Not with --demo.
- Enforced by the server, not the page: every POST except /api/quit is refused (403 "View-only mode"), so no button
  and no route added later can act; Task start is refused too (no child process of any kind); only the quick and
  sheet checks run (gui.VIEW_GROUPS: no hidden NUworks browser, no Claude call, no Discord, no ssh, no desktop
  notification); no lock file (a running GUI's is never touched or asked for a window); no homelab pull. Banner: "View only".
- Hidden: saved answer values (/api/answers shows the questions only) and run logs / screenshots (/api/file 403).
  Job data, the sheet rows, the settings (host names, sheet id) and past-run names are shown.

TESTS (in `nuauto test`; CI: .github/workflows/tests.yml runs it on Ubuntu and macOS for every pull request and every push
to main; a newer push to a pull request stops its older run)
- test_gui.py (security rules, API, single instance, the main flows in headless Firefox), test_view.py, test_setup.py
  (each wizard step, then end to end), test_demo.py (the real apply.py on the fake pages: Ctrl+C and GUI-gone stops, cap
  refusal, re-login states), test_health.py, test_homelab_settings.py, test_homelab_app.py (pairing, refusals, sessions,
  queue, devices, a phone-width browser approving a Submit), test_link.py, test_review.py, test_window.py,
  test_selftest.py.

API (JSON; all need the cookie; POSTs need X-NUauto: 1)
  GET  /api/state            counts, week, to-do, last scan, setup_needed, the current task, sheet_version, demo, view,
                             version, mode (local | homelab); internships {on, term, review}; where (laptop | homelab),
                             laptop_only {action: why} (the homelab's page), remote_app (the laptop: the homelab page's
                             address, when it has one); todo: the assistants that wait for you first ({"kind": "assistant",
                             "status", "text", "screen": its screen, or null for a laptop session in its terminal window}),
                             then site / company / urgent; commit (the code this machine runs; the side bar shows it);
                             homelab_link (the laptop, homelab page on: {on, error, commit, task {id, kind, label, state}})
  GET  /api/health           the checks; POST /api/health/run {"groups": [...]}
  GET  /api/review?mode=&q=&category=&source=&quick=1   the review queue (q / category narrow it; total, categories = the
                             whole queue; source=intern: the internships, else the co-ops; quick=1: the list made last at
                             once, "stale" true when it will change; an internship that could not be read has match null and
                             comes last); GET /api/job/<id> one card (an internship's adds source "simplify", url = the
                             posting, posted_text, year_text, unread)
  POST /api/decide           {"id", "mode", "decision": approve|yes|no} -> {"row", "previous"}; an internship's approve adds
                             "tab": "other" (its row is in the Other jobs tab)
  POST /api/undo             {"id", "row", "previous"} (an internship also needs "tab": "other": its row goes back to
                             Proposed in the Other jobs tab); POST /api/review/done (push ratings, homelab mode)
  GET  /api/jobs?tab=&order=&recheck=1   one Apply tab (nuworks | other): its rows in sections (nuworks: approved in apply
                             order, agent, manual, owed (site | retry), applied, accepted, rejected, proposed, closed, rest;
                             other: ready, applied, accepted, rejected, proposed, closed, rest), each row with locked /
                             remove_locked (why Move / Remove can't, or null) and, on a sent NUworks row, what NUworks said;
                             sessions, slots, homelab, linked, queue, sheet_version; nuworks also week, why_not, history,
                             order; other also exists
  GET  /api/apply            Approved rows in apply order, week, why_not, recent runs (/api/jobs has them too)
  POST /api/action           {"kind": apply ({"n", "order"} or {"row"}; a row during a run: queued, reply {"queue"}; "on":
                             "homelab")|apply_unqueue ({"row"})|update|login_google|login_gmail|login_nuworks|check_nuworks|
                             login_claude|install_firefox|install_claude|nuworks_side|assist|selftest|fix_permissions|
                             review_pair|homelab_link|homelab_unlink|bitwarden_add|bitwarden_import ({"only": [words]})|
                             bitwarden_login|bitwarden_install, "args"} (the bitwarden_ ones open a terminal window: Bitwarden
                             asks for your master password there, never this page)
                             (assist: {"row", "tab": "other"} for the Other jobs tab; on the homelab it starts the row's
                             session in tmux; "on": "homelab" from the linked laptop does that through the link); the
                             homelab's own: pair_device ({"name"} -> {"link", "minutes"}), unpair ({"id"}: never this device)
  GET  /api/task?after=N     the current task: state, new log lines, question, row, queue, screenshot, countdown; browser_view
                             {url, password} (the homelab: its virtual screen's view, while it runs)
  GET  /api/assistant/<slot>  (homelab) one session's screen: review.view (status, message, approval {id, what, url, why,
                             waiting, shot, answered, by}, browser {url, password}, terminal, version); .../shot.png[?live=1]
  POST /api/assistant        (homelab) {"slot", "do": approve|decline|reply|interrupt|exit|laptop|finish, "id", "note",
                             "text", "answer": y|n|laptop} -> {"message", "closing"}
  GET  /api/devices          (homelab) the paired devices {id, name, paired} and this one's id
  GET  /pair?c=, POST /pair  (homelab) the pairing page (no script) and its form: a one-time code -> the device's cookie; the
                             old /s/<n> review links redirect to #/assistant/<n>
  POST /api/window           (laptop) a second `nuauto gui` asks for another window (needs the lock file's open secret)
  POST /api/homelab          (laptop) Settings > Homelab: {"action": check ({"ssh", "folder"} -> what it has)|save ({"ssh",
                             "hostname", "folder", "listen"})|off|setup (a terminal window)|claude (a terminal window)|deploy
                             ({"on", "repo"}: a run)}
  POST /api/answer           {"task", "id", "reply"}; POST /api/stop {"task", "force"}
  GET  /api/homelab/assistant/<slot>[/shot.png], /api/homelab/task?after=N, /api/homelab/file?path=   and POST
                             /api/homelab/assistant, /api/homelab/answer, /api/homelab/stop   (laptop, linked) the homelab
                             page's answers through the link: a session's screen, its run (with "where": "homelab"), their
                             pictures; a tap on a session's screen, its run's question, its Stop (refusals carry the
                             homelab page's own words)
  GET  /api/company          agent / other / site / retry rows, sessions (the assistants running now: slot, row, tab,
                             company, host, since, attention: {kind waiting|approve, since, said} while it waits for you)
                             and slots (how many may); queue (the homelab: review.queue_view, or {"error"}); the laptop's link
                             reads the homelab's. POST /api/mark {"action": applied|site|drop|outcome, "row", "url", "why"
                             (drop: closed|duplicate|location|other), "outcome" (Applied|Accepted|Rejected), "tab": "other"
                             (Other jobs tab: applied, drop, outcome)}
  GET  /api/other            the Other jobs tab: exists, ready, applied, rest, sessions, slots (/api/jobs has them too)
  POST /api/other/add        {"url", "company", "title"} -> {"row"}: an Approved row there (the tab made if missing)
  GET  /api/sheet            both tabs: exists, rows (each with locked), busy
  GET  /api/sheet/lookup?url=  the tab a link belongs in; for a NUworks job: known, company, title, match, in_pool, notes
  POST /api/sheet            {"action": add, "tab": nuworks|other, "url", "company", "title", "status"} -> {"tab", "row"};
                             {"action": move, "tab", "row", "url", "new_url"} -> {"tab", "row"}; {"action": remove, "tab", "row", "url"}
  GET  /api/insights?source= pool / applied numbers (pay, places, categories) and the sheet tab's statuses; source=intern:
                             the Summer internships and the Other jobs tab (when internships are on; the reply says source)
  GET  /api/answers?bank=    entries + version (bank other: answers_other.json); POST /api/answers {"bank", "entries",
                             "version"}. answers.json entries carry nuworks_only; leave_blank is kept while empty
  GET  /api/settings         settings (no secrets; logins: {sites, new}, never a password); POST /api/settings {"resume_label", "week_start", "max_per_week", "accounts_email",
                             "assist_effort", "assist_read_paths", "assist_writeups", "assist_slots" (1-3), "assist_discord" (bool), "transcript_path",
                             "internships" ({"on", "term", "threshold", "lists"}), "web_base_url", "password_manager" ("" |
                             "bitwarden"), "bitwarden_import" [words], and with a homelab "remote_app", "remote_assist",
                             "remote_auto" (true / false, or {"nuworks": bool, "other": bool}: a tab left out stays as it
                             was), "remote_hours" [from, to]}; GET adds password_manager, homelab_assistant {available, on, app,
                             auto, auto_tabs, hours, slots, review_url}, where, homelab (the laptop: onboard.homelab_link)
                             and (the homelab) devices, this
  POST /api/files            {"kind": "resume" | "transcript", "data": the PDF in base64} or {"kind", "remove": true}
                             (transcript only) -> settings; up to 10 MB (the only request over 2 MB); the homelab: 409
  POST /api/logins/export    Bitwarden's import file of the logins not exported yet ({"every": true}: all) -> {"path", "count"}
  GET  /api/setup            wizard steps; POST /api/setup {"action": ack|client_upload|client_downloads|
                             sheet_create|sheet_link|resume_set (a path it found: copied in)|labels_read|label_set|terms_read|
                             prefs_save|discord_save|discord_test|discord_remove|schedule_on|schedule_off|launcher}
  GET  /api/logs, /api/file?path=   past runs (the assistant's own screenshots are in a run's browser/); a screenshot or
                             log under logs/ only
  POST /api/quit

YOUR FILES (2026-10-20, my request: "a way to upload a resume and a transcript into the gui")
- The resume (Setup's resume step, or Settings > Applying > Your files) and the transcript (Settings > Applying > Your files; optional, Remove
  takes it out) are given to NUauto by dropping or choosing the PDF in the page. NUauto keeps its own copy,
  local/resume.pdf / local/transcript.pdf (mode 600; onboard.save_document: a PDF, up to 10 MB, the resume with text
  Claude can read), and points resume_path / transcript_path at it. So changing the PDF you had elsewhere changes
  nothing: give NUauto the new one. An older resume_path / transcript_path anywhere on the laptop still works.
- With a homelab the laptop pushes the copies there at once (sync.push_documents; a removed transcript is deleted
  there too), not at the next push. The homelab's page shows the copies it has (name, size, when) and can't change them.

FILES IT WRITES (all under local/, mode 700, except as noted)
  gui.lock          pid, port and a secret that only lets a second start ask for a window (removed on exit); on the
                    homelab nuauto-web's (its runs check it)
  settings.lock     local_config.json and profile.json are changed under it (Settings, setup, sync's merge)
  answers.lock      the answer bank is saved under it (the assistant's answer-bank commands take it too)
  review_devices.json, review_pair.json   (homelab) the paired devices' key hashes, the open pairing links' hashes
  homelab_link.json (laptop) this app's device key for the homelab's page (link.py; mode 600, never synced)
  browser_profile.lock   who uses the NUworks browser (pid, what, since)
  onboard.json      what the read-only NUworks reads found (resume labels, terms)
  resume.pdf, transcript.pdf   YOUR FILES (mode 600)
  local_config.json gains tos_ack, preferences, tools (saved tool paths) through the wizard (mode 644, as before)
  work/apply_queue_<id>.json   (outside local/) an Apply run's queue of rows, removed when it ends
  logs/<time>_<kind>_screen/   (homelab) a watched run's virtual screen
  outside the folder: the scheduler (~/.config/systemd/user/nuauto-daily.{timer,service} or
  ~/Library/LaunchAgents/com.nuauto.daily.plist) and the launcher (~/.local/share/applications/nuauto.desktop or
  ~/Applications/NUauto.app)

NOT YET PROVEN FOR REAL (only in demo mode so far)
- A real GUI apply with you watching; the Google and NUworks logins through the GUI; the resume-label and term
  reads against the real NUworks; the systemd/launchd toggle.
- The app on the real homelab (2026-10-14): served by nuauto-web, used from a paired phone, an Apply run there on its
  virtual screen; Settings > Homelab's Check and Set it up against a real homelab; the laptop's link (Link this app,
  Start on the homelab, Run on: the homelab; as of 2026-10-15).
- The packaged app on a real desktop other than Fedora 44 (containers ran it on Ubuntu 22.04/24.04, Debian 12, Fedora
  and Arch; its window was rendered on GTK 4 and GTK 3 with a virtual X server). The macOS app on a real Mac: CI builds
  and self-tests both, but nobody has opened one yet. No release has been published (that takes a v* tag).
