ARCHITECTURE: a map of the code (src/nuauto/)
(Keep this current when you add, rename or re-wire a module. Setup and machines: docs/DEPLOY.md.
Pool rules and ranking: docs/PIPELINE.md. Safety rules: docs/SAFETY.md.)

MODULES (src/nuauto/: 36 .py files, and gui_static/ for the window's page)
Command line and plumbing
  cli.py          the `nuauto` command. TOOLS (jobs, answers, sheet, setup-sheet, inspect, daily, web, health, demo,
                  onboard, selftest, insights, intern, accounts, review, link) run a module's own __main__ via run_tool();
                  COMMANDS: approve, rate, apply, status, update, login [google|gmail], test, doctor, assist, gui, deploy.
                  A laptop with a homelab pulls data/ first and pushes after approve / rate / status. `nuauto test` runs
                  tests/test_*.py 4 at a time. Internal (packaged app): _assist, _playwright, _window.
  __main__.py     python -m nuauto -> cli.main.   __init__.py  the version.
  config.py       every path and host; reads local/local_config.json. ALLOWED_HOSTS, SSO_HOSTS, HAS_SERVER / IS_SERVER,
                  DEPLOY_FROM_GIT, REMOTE_ASSIST / REMOTE_APP, DEMO / DEMO_LINK / DEMO_PACE, STATE_DIR (NUAUTO_STATE_DIR),
                  FROZEN (packaged app: bundle paths, app-data STATE_DIR, never a homelab), set_files(), settings_lock,
                  find_client_json(), lock_token(), tool_path() / tools_path(), self_cmd() / self_exe().
  sync.py         laptop<->homelab (ssh + rsync). pull(), push(), push_session(), push_documents(), deploy_now(),
                  watch_unit(); with the homelab's page on, share() (merge_bank, merge_dict, merge_accounts: both
                  machines' banks, settings, ratings, logins merged, nothing deleted by a missing copy), push_notes().
                  PACKAGE / CODE: the files that reach the homelab (not sent when deploy_from_git is on).
  deploy.py       homelab: Deploy.run() deploys GitHub's main (unpack aside, import check, copy changed files, reinstall,
                  units, restart web only if needed, undo on failure); nuauto-deploy.timer.
  doctor.py       health check. main() -> laptop() (health.py), server() (piped to the homelab), check_*; --json.
  health.py       the checks behind doctor and the GUI's status strip: Check, QUICK / LIGHT / HEAVY groups (nuworks runs
                  in a hidden browser), fix_permissions(), app_update(), desktop_notify().
  selftest.py     `nuauto selftest`: demo mode end to end in a hidden browser.
Sheet and NUworks
  sheet.py        the Google Sheet. open_worksheet() / open_other() (kept OPEN_FOR s: OPEN, forget(), HTTPClient retries),
                  read_rows(); limits: max_per_week(), week_window(), applied_dates(), total_count() (not_on_nuworks,
                  rejected_not_withdrawn, maybe_filled; reapplying() rows count as sent), check_limits(); writers: set_status,
                  mark_submit_started / resolve_submit, mark_applied_by_hand, mark_site_done, set_outcome, drop,
                  mark_reapply / undo_reapply, approve_proposed, unapprove,
                  mark_on_laptop, append_note, add_proposed; create_sheet(), google_login().
  manage.py       rows by hand: add(), move(), remove() (cells cleared, never deleted), lookup(), check_url(), movable(),
                  reapply() (`nuauto sheet reapply <row>... [--undo]`).
  setup_sheet.py  one-time setup (refuses a sheet with data: HasData); format_sheet(); ensure_dropdown().
  browser.py      Playwright Firefox. launch() (lock_profile(): one process on the profile), install_domain_lock(),
                  goto_logged_in() / relogin() (one click, never credentials), RunLog (logs/ dir + screenshots),
                  cmd_login(), cmd_open().
  apply.py        the NUworks runner. main() (refuse_unattended, nuworks_turn), run_rows() (limits before each row,
                  queue), apply_one(), fill_popup(), check_popup(), fill_extra_fields(), submit_flow(),
                  submit_nuworks_side(), apply_order().
  answers.py      answer bank. load() / save(), find() (exact match after norm()), resolve_answer(), forget() (deleted
                  questions, for the homelab's copy), TerminalIO / NoTerminalIO / JsonIO (the GUI's channel),
                  json_ui_allowed(), Stop.
  inspect_form.py read-only lister of an Apply form; FIELDS_JS is reused by apply.py.
Job pool
  jobs.py         the NUworks pool. PREFS / set_prefs(), cmd_list, cmd_triage_*, cmd_details (hard_rules), cmd_score_*,
                  cmd_cat_*, build_pool(), ranked_pool() (taste model), review_queue(), job_view(), rate_viewer(),
                  cmd_rate / cmd_approve / cmd_suggest / cmd_stats, set_rating(), check_applied(), check_applications()
                  (what NUworks says about sent rows -> data/applications.json; application_answer(): applied, and the
                  job's hiring stage tracked across checks).
  fields.py       per-field starting preferences (the wizard's "What do you study?").
  intern.py       Summer internships (Simplify's and vanshb03's lists, one job on both kept once): the second pool, built
                  like jobs.py (refresh, triage, fetch, redate, score, category, build_pool); approve() ->
                  manage.add("other"); update(), review_queue(), closing_dates(), stats(). Short links -> the posting's
                  own (real_links, real_url; used by assist.other_target). Data in data/intern/.
  postings.py     reads one posting from a company's own job site, no login; public addresses only (allowed_host).
  daily.py        what the homelab timers run. main() -> scan() (via step()), applications(), reminders() (the morning
                  message: morning(), its lines; record_hand_applications()), internships(); run_claude(); messages:
                  notify() / discord(), alert() (a problem, once a day each), later() (a line for the next morning).
  insights.py     summarize(): pay, places, kinds of work, sheet statuses; text() for `nuauto insights`.
  web.py          Mark-done page (homelab, port 8765). link() signs URLs, Handler.do_GET (confirm) / do_POST; main() also
                  starts gui.serve_remote() when the homelab's page is on.
Company-site agent
  assist.py       the agent. run() / run_locked() start `claude`; hook_main() -> hook() -> pre_hook() / decide() /
                  update_after() is the guard; bank() the answer-bank command; assist_target(), by_hand_target(),
                  other_target(), nuworks_side(); slot / site / row locks; Bank (which answer file a run uses).
  accounts.py     the job-site logins the agent makes: site(), password_for() (placeholders), never(), candidates_for(),
                  export() / to_bitwarden(), import_own().
  mail.py         read-only Gmail: lookup() (pick, find_code, find_link), login(), ready() / days_left().
  letter.py       a cover letter as a PDF, for the one application it was written for.
Homelab
  remote.py       the assistant's queue (candidates, plan, auto, start), claims (claim, claimed, claim_on_homelab), the
                  NUworks turn (nuworks_turn, take_turn, give_turn), record_outcome().
  review.py       pairing devices (make_pairing, pair, device, unpair) and the assistant sessions' screens (session, act,
                  end_session, answer_approval, queue_view, start_now).
  screen.py       a virtual screen per assistant slot (Xvfb, x11vnc, websockify + noVNC), a view of it, screenshot().
  link.py         the laptop app's link to the homelab's page, as a paired device: pair(), call(), forget().
Window and setup
  gui.py          `nuauto gui`: Handler (the local server and its checks), App (tasks, health monitor, cached rows), Task
                  (one child process + its JSON-lines questions), act() (the buttons), the screens' data, open_terminal(),
                  screenshots(); serve_remote() = the same app on the homelab (REMOTE). Page: gui_static/ (index.html,
                  app.js, setup.js, app.css, icon.svg).
  onboard.py      the setup wizard: steps(), check_client, open_and_prepare, check_prefs, save_webhook, scheduler_*,
                  launcher_create, homelab_probe / homelab_setup, save_document / remove_document (resume, transcript),
                  read_resume_labels, read_terms (read-only NUworks reads).
  demo.py         demo mode: FakeClient / FakeWorksheet, serve_nuworks (fake NUworks pages), the fake claude, sample data,
                  forced states; setup() / update().
  window.py       which window (kind(): mac / gtk / app / tab), run_mac(), open_gtk(), open_browser(), system_env().
  window_gtk.py   the Linux window: run by the system's python3 (GTK + WebKitGTK), imports nothing from nuauto.

WHO CALLS WHAT (module-level imports; "lazy" = inside functions; imports are absolute, test_sync enforces it)
  config      imported by almost everything; imports nothing from nuauto (as do fields, letter, postings, window_gtk).
  sheet       -> config; lazy: jobs, manage, setup_sheet, assist (LAPTOP_MARK), demo.
  browser     -> config; lazy: demo.        answers -> config.        screen -> config.
  apply       -> answers, browser, config, jobs, sheet, inspect_form; lazy: assist, remote, insights.
  assist      -> accounts, answers, config; lazy: apply, browser, sheet, remote, review, screen, mail, letter, manage,
                 daily, health, sync.
  accounts    -> config; lazy: answers, sync.        mail -> accounts, config.
  remote      -> config; lazy: assist, apply, sheet, jobs, review, daily, sync.
  review      -> config; lazy: assist, accounts, remote, screen, sheet, daily, gui, sync.
  link        -> config; lazy: review, assist, gui.
  jobs        -> config, fields, sheet; lazy: browser.        manage -> config, jobs, sheet; lazy: assist (check_link).
  intern      -> config, jobs; lazy: postings, manage, sheet, daily, insights, sync.
  daily       -> config, jobs; lazy: sheet, setup_sheet, health, intern, web.
  web         -> config; lazy: jobs, sheet, gui.        insights -> jobs; lazy: intern, sheet.
  inspect_form-> browser, config.        setup_sheet -> config; lazy: sheet.
  sync        -> config; lazy: accounts, answers, assist, jobs, mail.        deploy -> config; lazy: daily.
  doctor      -> config, sync; lazy: health, web.        health -> config; lazy: accounts, browser, doctor, jobs, sheet, sync.
  selftest    -> config, health.        window -> config (pywebview inside functions, macOS only).
  gui         -> answers, config, health, jobs, sheet, window; lazy: apply, assist, accounts, browser, insights, intern,
                 link, mail, manage, onboard, remote, review, screen, setup_sheet, sync.
  onboard     -> config, fields, health, jobs; lazy: apply, assist, accounts, browser, demo, inspect_form, intern, remote,
                 review, setup_sheet, sheet, sync.
  demo        -> config; lazy: answers, assist, intern, jobs, sheet.
  cli         -> config; every other module lazily, per command.

MAIN FLOWS
  nuauto update / daily (homelab; laptop-only in local mode)
    cli.main: laptop pushes (sync.push), starts nuauto-daily.service over ssh, pulls data/. daily.main: scan():
    jobs.cmd_list -> cmd_triage_export -> run_claude(TRIAGE_PROMPT.md) per batch -> cmd_triage_import -> cmd_details
    (hard_rules) -> cmd_score_export -> run_claude(SCORE_) -> cmd_score_import -> cmd_cat_export -> run_claude(CATEGORY_)
    -> cmd_cat_import -> build_pool() -> pool.json -> notify() (Discord). Morning: reminders() (record_hand_applications
    first). Every run ends with applications() (jobs.check_applications) and internships() (intern.update, when on).
  nuauto approve / the GUI's Review (gui.decide)
    jobs.review_queue minus rows in the sheet and rated-no -> y -> sheet.approve_proposed (a Proposed row) or
    sheet.add_proposed(status="Approved"); an internship -> intern.approve (Other jobs tab).
  nuauto apply
    apply.main: refuse_unattended (terminal / --ui json from the GUI / never CLAUDECODE), assist.nuworks_session, resume
    label, remote.nuworks_turn -> run_rows: sheet.check_limits -> row (queued rows first, then apply_order) -> deadline ->
    apply_one: browser.launch + install_domain_lock -> fill_popup (check_popup, resume dropdown, fill_extra_fields via
    answers.resolve_answer) -> submit_flow: sheet.mark_submit_started, click, confirm text -> sheet.resolve_submit.
    Any stop -> sheet.set_status("Needs Human"). 10-20 s between rows.
  nuauto gui
    gui.main: single instance (local/gui.lock), ThreadingHTTPServer on 127.0.0.1, App.monitor runs the health groups. A
    button -> /api/action -> act() -> App.start -> Task runs `nuauto <cmd> --ui json`; its plain lines = log, "::nuauto:: "
    lines = JsonIO questions/events; /api/answer writes to its stdin, /api/stop sends SIGINT. The page (app.js) polls
    /api/state, /api/health, /api/task. --view: read-only. --demo: demo.py's fakes (--remote: the homelab's page).
  nuauto assist <row> / assist other <row>
    assist.run: find_row, check_limits (NUworks rows), row + slot + job-site locks, the homelab's claim; run_locked: a
    logs/<stamp>_assist_row<N>/ dir with mcp.json + settings.json (hooks), `claude` with the Playwright MCP browser. Every
    tool call -> `assist.py hook pre|post` -> decide() / update_after(); answer-bank Bash -> bank(). After /exit, "did you
    submit?"; y -> sheet.mark_applied_by_hand -> (company-site NUworks rows) nuworks_side -> apply.submit_nuworks_side.
    `assist nuworks <row>` retries only that last step.
  The homelab's page and queue
    nuauto-web -> web.main -> gui.serve_remote (paired devices, port 8766; review.py serves the Assistants screens).
    nuauto-assist.timer -> `assist auto` -> remote.auto starts tmux sessions. Both machines take remote.nuworks_turn
    around a NUworks submit; the laptop's sessions claim rows there first (remote.claim_on_homelab).
  Mark-done page: daily builds web.link(action, row, job_id) (HMAC-signed). GET /m shows a confirm page only; the button
    POSTs -> sheet.mark_site_done / mark_applied_by_hand. Bad signature = 404; row/job mismatch = 409.
  nuauto deploy (homelab timer, every 5 min): deploy.Deploy.run. Laptop: sync.deploy_now starts it there.

STATE ON DISK (STATE_DIR = repo root; app-data folder when packaged; a temp folder in demo mode and tests)
  data/   the pool, owned by the homelab (the laptop pulls a copy): list.json, triage.json, scores.json, categories.json,
          category_sets.json, pool.json, details/<id>.json (jobs.py); scans.json, last_run.json, later.json, told.json
          (daily.py);
          applications.json (jobs.check_applications); ratings.json (jobs.set_rating, merged between machines);
          intern/ (intern.py: listings, source, failed, unread, triage, scores, categories, pool, details/, update.lock).
  work/   Claude's batches: <kind>_in_NNN.json / <kind>_out_NNN.json, resume.txt (jobs.py writes the inputs, the claude
          subprocess the outputs); apply_queue_<id>.json (gui); deploy/ (deploy.py).
  logs/   browser.RunLog: <stamp>_<label>/ per run (actions.log + screenshots; labels row<N>, row<N>_nuworks_side, login,
          open, check_applied, check_applications, assist_row<N>, assist_other_row<N>, <kind>_screen; an assistant run adds
          state.json, mcp.json, settings.json, browser/, approval / decision / grant / attention files). Also
          daily-<stamp>.txt, web.log, review.log, deploy.log.
  local/  personal and secret, mode 700, never committed; both machines unless noted.
          Settings: local_config.json, profile.json, onboard.json, settings.lock. Answers: answers.json,
          answers_other.json, answers_gone.json, answers.lock, accounts.json, accounts.lock, share_base.json (laptop).
          Google: token.json, google_login.txt, client_secret.json, gmail_token.json, gmail_login.txt, discord_webhook.txt.
          NUworks: session_cookies.json, browser_profile/, browser_profile.lock. Assistant: assist_profile/ (+ _2, _3),
          assist_slot<N>.lock, assist_sites/, assist_rows/, resume.pdf, transcript.pdf (the homelab's copies).
          Homelab only: web_secret.txt, review_devices.json, review_pair.json, screen<N>.json, assist_auto.json,
          assist_claims/, nuworks_turn.json, deployed.json; ~/nuauto-notes (copy of your writeups). Laptop only:
          homelab_link.json. gui.lock: whichever machine serves the page. Demo: demo_state.json, demo_sheet_<id>.json.
          Which of these sync: docs/DEPLOY.md, sync.py.

SHEET (columns in sheet.HEADERS, made by setup_sheet.py)
  Columns: URL, Company, Title, Status, Notes, Date. First tab NUworks only; second tab "Other jobs" (sheet.OTHER_TAB).
  Status values (sheet.STATUSES): Proposed, Approved, Applied, Failed, Needs Human, Accepted, Rejected.
  Who sets what:
    Proposed      jobs.cmd_suggest, manage.add (--proposed), Undo (sheet.unapprove).
    Approved      sheet.approve_proposed / add_proposed(status="Approved") from Review; intern.approve; manage.add; or you.
    Applied       sheet.resolve_submit (apply.py after a confirmed submit); mark_applied_by_hand (assist.run,
                  daily.record_hand_applications, web do_POST, gui.mark); set_outcome back to Applied.
    Accepted / Rejected   sheet.set_outcome (gui.mark); the Status dropdown.
    Failed        sheet.resolve_submit (you said the submit failed); sheet.drop (Notes "Dropped <date>: ...").
    Needs Human   sheet.set_status (apply.py stops, deadline passed); mark_submit_started (the row reads Needs Human
                  between the Submit click and the result, so it can't be sent twice).
  set_status only changes rows still Approved. Notes starting sheet.SITE_MARK are written by apply.submit_flow and cleared
  by sheet.mark_site_done. The weekly cap counts sent rows (sheet.SENT) dated in the current week (sheet.week_window); the
  total counts those NUworks has (sheet.total_count). The Other jobs tab has neither.

REPO LAYOUT (what is not a module)
  pyproject.toml (package, dependencies, the `nuauto` command)   install.sh   README.md   CONTRIBUTING.md   LICENSE
  local_config.example.json (template; its placeholders run the offline tests)
  prompts/ (TRIAGE_, SCORE_, CATEGORY_PROMPT.md; INTERN_TRIAGE_, INTERN_SCORE_PROMPT.md; ASSIST_PROMPT.md)
  docs/ (DEPLOY, PIPELINE, GUI, REMOTE, ASSIST, ARCHITECTURE, SAFETY; STATUS is gitignored)
  deploy/systemd/ (homelab units: daily, weekly, intern, deploy, assist timers + services; web service)
  packaging/ (the packaged app: nuauto.spec, build.sh, distros.sh, entry.py, AppRun, desktop file, icons, smoke.py)
  .github/workflows/ (tests.yml: `nuauto test` on Ubuntu + macOS, on PRs and main; release.yml: AppImages + DMGs, built on
  PRs (Intel Mac: tags only), released on v* tags. Firefox's system libraries are installed on Linux only when Firefox
  won't start without them: GitHub's runners already have them, and apt can take 20+ minutes on a slow Ubuntu mirror)

TESTS (`nuauto test` runs every tests/test_*.py, 4 at a time: offline, no sheet, no real NUworks, no network; browser tests
use headless Firefox on local pages or demo mode. Run it before pushing; CI runs it on Ubuntu and macOS.)
  test_sync      sync.CODE is complete (a new module or prompt must be in it), absolute imports, rsync options, units
  test_sheet     sheet.py: parsing, week and total limits, max_per_week, not_on_nuworks, maybe_filled, submit safety,
                 drop, set_outcome, mark_on_laptop, retries, the kept sheet (OPEN)
  test_jobs      hard rules, pool, model; sheet.add_proposed / approve_proposed; application_answer (the hiring stage)
  test_apply     apply.check_popup, run_rows and its queue, BETWEEN
  test_answers   answers.py, apply.fill_extra_fields (a local form), JsonIO, json_ui_allowed
  test_browser   domain lock, goto retry, profile lock
  test_demo      the real apply.py on demo mode's fake pages (stops, cap, Ctrl+C, GUI gone, CLAUDECODE refusals)
  test_daily     daily.py's morning message, alerts once a day, Claude errors, the applications check
  test_web       Mark-done links (signatures; GET never changes the sheet)
  test_deploy    deploy.py on a throwaway git repo
  test_doctor_remote   doctor.check_remote_assist          test_health   health.py's checks
  test_insights  Insights numbers          test_selftest  `nuauto selftest`          test_setup  the wizard (onboard.py)
  test_intern    internship pool, postings.py (links, parsing, close dates, the network guard, fetch tries, redate),
                 short links, Review queue
  test_assist    the agent's guard, answer bank, slots, passwords, uploads, by-hand rows
  test_accounts  accounts.py: sites, Bitwarden export / import of your own logins
  test_mail      mail.py: Gmail's format, which mail counts, the `email` command
  test_remote    remote.py: queue, claims, nuworks_turn, assist.run's claim step
  test_review    review.py and the homelab app's handler rules, sessions' backend
  test_screen    screen.py commands, address rules; one real start / stop where Xvfb etc. are installed
  test_share     answer banks, settings, logins both machines share (sync.share)
  test_link      the laptop's link to the homelab's page (pairing, /api/homelab/ routes, an Apply run there)
  test_homelab_app   the page as the homelab serves it (demo --remote)       test_homelab_settings   its Settings keys
  test_gui       the GUI server's security and flows (demo); tests/screens.py (not a test) opens every screen for test_gui,
                 test_homelab_app and test_link
  test_view      `--view`: read-only            test_window   packaged-app plumbing (self_cmd, window choice, clean env)
  Packaged app: packaging/smoke.py (release.yml).
