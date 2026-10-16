ARCHITECTURE: a map of the code (src/nuauto/)
(Keep this current when you add, rename or re-wire a module. Setup and machines: docs/DEPLOY.md.
Pool rules and ranking: docs/PIPELINE.md. Safety rules: docs/SAFETY.md.)

MODULES (src/nuauto/)
  cli.py          the `nuauto` command. main() syncs, then dispatches. TOOLS run a module's own
                  __main__ via run_tool(); COMMANDS are approve/rate/apply/status/update/login/test/doctor/assist/gui.
                  `nuauto test` runs the test files 4 at a time. Internal commands for the packaged app: _assist,
                  _playwright, _window. Frozen and started with nothing: gui.
  config.py       every path and host; reads local/local_config.json. ALLOWED_HOSTS, SSO_HOSTS,
                  HAS_SERVER, IS_SERVER, STATE_DIR / DEMO (NUAUTO_STATE_DIR, NUAUTO_DEMO), DEMO_PACE,
                  find_client_json(), lock_token(), tool_path() (claude/npx, saved paths first), FROZEN (the packaged
                  app: bundle paths, app-data STATE_DIR, local mode, Playwright's browser cache), self_cmd() / self_exe()
                  (how NUauto runs itself). No logic beyond that.
  sheet.py        the Google Sheet. client() / open_worksheet(interactive) (interactive=False never opens a
                  login page: NotLoggedIn), read_rows(), check_limits() (MAX_PER_WEEK/MAX_TOTAL), set_status(),
                  mark_submit_started()/resolve_submit(), mark_applied_by_hand(), mark_site_done(), append_note(),
                  add_proposed(), unapprove() (GUI Undo), create_sheet() (Sheets API), google_login().
  jobs.py         the job pool + viewers. PREFS (local_config "preferences" over DEFAULTS), RULES, set_prefs(),
                  render_prompt(); cmd_list, cmd_triage_export/import, cmd_details (hard_rules),
                  cmd_score_export/import, cmd_cat_export/import, build_pool(), ranked_pool(), review_queue()
                  (shared with the GUI), job_view()/text_blocks() (the GUI's job card), rate_viewer(), cmd_rate(),
                  cmd_approve(), cmd_suggest(), check_applied().
  daily.py        what the homelab timers run. main() -> scan() (steps above via step()), reminders(),
                  run_claude(), notify()/discord(), record_hand_applications(), weekly().
  web.py          Mark-done page. link() makes signed URLs, Handler.do_GET (confirm page) / do_POST (change sheet).
  browser.py      Playwright Firefox. launch() (takes lock_profile(): one process on the profile), goto_logged_in(),
                  relogin(), install_domain_lock(), RunLog (per-run logs/ dir + screenshots), cmd_login(), cmd_open().
  apply.py        the NUworks runner. main(), apply_one(), fill_popup(), submit_flow(), check_popup(),
                  submit_nuworks_side(), apply_order().
  answers.py      answer bank. load()/save(), find() (exact match after norm()), resolve_answer(),
                  TerminalIO (the question methods, terminal wording) / NoTerminalIO / JsonIO (the GUI's channel),
                  json_ui_allowed(), Stop.
  assist.py       company-site agent. run() launches claude; hook_main() -> hook() -> decide() /
                  update_after() is the guard; bank() is the answer-bank command; assist_target(),
                  nuworks_side().
  inspect_form.py read-only lister of an Apply form. main(); FIELDS_JS is reused by apply.py.
  sync.py         laptop<->homelab rsync. pull(), push(), push_session(), server_busy(), deploy_now(); PACKAGE/CODE
                  list the files that reach the homelab (not sent when deploy_from_git is on).
  insights.py     summarize(pool, details, rows) -> pay (hourly(), buckets, median, middle half), places (place(),
                  by state), categories, sheet statuses; text() for `nuauto insights`; gui.py's /api/insights.
  deploy.py       homelab: Deploy.run() deploys GitHub's main (unpack aside, import check, copy changed files,
                  reinstall, unit files, restart web only if needed, undo on failure); run by nuauto-deploy.timer.
  doctor.py       health check. main() -> laptop() (health.py's checks), server() (piped to the homelab),
                  server_results(), check_public_page(); --json.
  health.py       the checks behind doctor and the GUI's status strip: Check, quick (files), light (sheet, claude,
                  firefox, discord, homelab), heavy (nuworks in a hidden browser, claude_live); desktop_notify().
  gui.py          `nuauto gui`: Handler (the local server and its checks), App (tasks, health monitor, cached rows),
                  Task (one child process + its JSON-lines questions), the pages' data (state, review, apply,
                  company, answers, settings, setup), open_terminal(), screenshots(). Static files: gui_static/.
  onboard.py      the setup wizard's steps (steps(), check_client, open_and_prepare, resume_preview, check_prefs,
                  save_webhook, scheduler_*, launcher_create) and the read-only NUworks reads (resume labels, terms).
  demo.py         demo mode: FakeClient/FakeWorksheet (sheet), serve_nuworks (fake NUworks pages in Playwright), the
                  fake claude (fake_claude, run through a two-line script), sample data, forced states; setup() / update().
  window.py       which window (kind(): mac / gtk / app / tab), run_mac() (pywebview, main thread), open_gtk() (the GTK
                  helper, one process per window), open_browser(), system_env() (no app variables for outside programs).
  window_gtk.py   the Linux window: run by the system's python3 (GTK + WebKitGTK), imports nothing from nuauto.
  setup_sheet.py  one-time sheet setup: setup() (refuses a sheet with data: HasData); format_sheet() restyles.
  __main__.py     python -m nuauto -> cli.main.

WHO CALLS WHAT (from the imports)
  config      imported by almost everything; imports nothing from nuauto.
  sheet       -> config.
  browser     -> config.
  answers     -> config.
  jobs        -> config, sheet; browser (inside open_browser only).
  daily       -> config, jobs; sheet and web (inside functions).
  web         -> config; jobs and sheet (inside the handler).
  apply       -> answers, browser, config, jobs, sheet, inspect_form.FIELDS_JS.
  assist      -> answers, config; sheet, browser, apply (inside functions).
  inspect_form-> browser, config.
  sync        -> config.
  doctor      -> config, sync; health, web (inside functions).
  health      -> config; sheet, jobs, browser, doctor (inside functions).
  gui         -> answers, config, health, jobs, sheet; apply, assist, onboard, sync, browser (inside functions).
  onboard     -> config, health, jobs; sheet, setup_sheet, apply, browser, demo (inside functions).
  demo        -> config; sheet, jobs, answers (inside functions). browser and sheet import it only in demo mode.
  window      -> config (pywebview inside functions, macOS only). gui imports it.
  setup_sheet -> config; sheet (SITE_MARK).
  cli         -> config; everything else is imported lazily per command (sync, doctor, jobs, apply,
                 sheet, daily, assist, browser). Tools via runpy: jobs, answers, sheet, setup_sheet,
                 inspect_form, daily, web.
  Imports are absolute (`from nuauto import sheet`); tests/test_sync.py enforces it.

MAIN FLOWS
  nuauto update / daily (homelab; laptop-only in local mode)
    cli.main: laptop pushes code (sync.push), starts nuauto-daily.service, pulls data/.
    daily.main -> scan(): jobs.cmd_list -> cmd_triage_export -> run_claude(TRIAGE_PROMPT.md) per
    batch -> cmd_triage_import -> cmd_details (hard_rules) -> cmd_score_export -> run_claude(SCORE_)
    -> cmd_score_import -> cmd_cat_export -> run_claude(CATEGORY_) -> cmd_cat_import ->
    build_pool() -> save pool.json -> notify() (Discord). Morning run then calls reminders().
  nuauto approve
    cli.main (sync.pull) -> jobs.cmd_approve: load_details + ranked_pool (data/) minus rows already
    in the sheet and rated-no -> rate_viewer; y collects jobs -> sheet.add_proposed(status="Approved")
    on exit. Then sync.push (ratings).
  nuauto apply
    apply.main: sheet.check_limits -> pick_row/apply_order (closing soon first) -> apply_one:
    browser.launch + install_domain_lock -> fill_popup (check_popup, resume dropdown,
    fill_extra_fields via answers.resolve_answer) -> submit_flow: sheet.mark_submit_started, click,
    confirm text -> sheet.resolve_submit (Applied or Failed). Any stop -> sheet.set_status("Needs Human").
  nuauto gui
    gui.main: refuses in a Claude Code shell (real mode); single instance (local/gui.lock); ThreadingHTTPServer on
    127.0.0.1; App.monitor runs the health groups on timers. A button -> /api/action -> App.start -> Task runs
    `python -m nuauto <cmd>` (apply / assist nuworks with --ui json). Task._read splits the child's output: plain
    lines = log, "::nuauto:: " lines = JsonIO questions/events. /api/answer writes the reply to the child's stdin;
    /api/stop sends SIGINT. The page (gui_static/app.js) polls /api/state, /api/health, /api/task.
  nuauto assist <row>
    assist.run: find_row, sheet.check_limits, write mcp.json + settings.json (hooks) into a
    logs/<stamp>_assist_row<N>/ dir, start `claude` with the Playwright MCP browser. Every tool call goes
    through `assist.py hook pre|post` -> decide()/update_after(); answer-bank Bash goes to bank().
    After /exit, asks "did you submit?"; y -> sheet.mark_applied_by_hand -> nuworks_side ->
    apply.submit_nuworks_side -> sheet.append_note. `assist nuworks <row>` retries only that last step.
  Mark-done page (homelab)
    daily builds links with web.link(action, row, job_id) (HMAC-signed). Browser GET /m ->
    Handler.do_GET shows a confirm page only. Button POST -> do_POST -> sheet.mark_site_done or
    sheet.mark_applied_by_hand. Bad signature = 404; row/job mismatch = 409.

STATE ON DISK (repo root; all gitignored except as noted)
  data/   job pool, owned by the homelab; laptop pulls a copy. list.json, triage.json, scores.json,
          categories.json, pool.json, scans.json, details/ (one file per job): written by jobs.py
          (scans.json and last_run.json, why the last update stopped, by daily.py). ratings.json: written by jobs.cmd_rate on the laptop, pushed.
  work/   batch files for Claude: <kind>_in_NNN.json, <kind>_out_NNN.json, resume.txt. jobs.py
          writes the inputs and resume.txt; the claude subprocess (daily.run_claude) writes the outputs.
  logs/   browser.RunLog: <stamp>_<label>/ per application (actions log + screenshots; assist adds
          actions.log, state.json, mcp.json, settings.json). daily.py: daily-<stamp>.txt. web.py: web.log.
  local/  personal and secret, mode 700, never committed. config + profile (local_config.json,
          profile.json), answers.json (answers.py), Google files (token.json, google_login.txt;
          sheet.py), NUworks session (session_cookies.json, browser_profile/, browser_profile.lock; browser.py),
          assist_profile/ (assist.py), web_secret.txt (web.py), discord_webhook.txt (daily.py),
          gui.lock (gui.py), onboard.json (onboard.py). Demo mode: all of these under a temp NUAUTO_STATE_DIR.
          Which of these sync to the homelab: docs/DEPLOY.md.
  Also in the repo, tracked: prompts/ (Claude prompts), tests/ (offline checks), deploy/systemd/.

SHEET (columns in sheet.HEADERS, created by setup_sheet.py)
  Columns: URL, Company, Title, Status, Notes, Date.
  Status values (sheet.STATUSES): Proposed, Approved, Applied, Failed, Needs Human.
  Who sets what:
    Proposed      jobs.cmd_suggest (add_proposed).
    Approved      jobs.cmd_approve (add_proposed with status Approved); or you, by hand in the sheet.
    Applied       sheet.resolve_submit (apply.py after a confirmed submit); sheet.mark_applied_by_hand
                  (assist.run, daily.record_hand_applications, web do_POST).
    Failed        sheet.resolve_submit (you reported the submit failed).
    Needs Human   sheet.set_status (apply.py stops, deadline passed); mark_submit_started (the row reads
                  Needs Human between the Submit click and the result, so it can't be sent twice).
  set_status only changes rows still Approved. Notes starting sheet.SITE_MARK are written by
  apply.submit_flow and cleared by sheet.mark_site_done. The weekly cap counts Applied rows with a
  Date in the current week (sheet.week_window, check_limits).

FILES (moved from CLAUDE.md 2026-10-16; layout since 2026-10-05: an installable package, `pip install -e .`)
pyproject.toml (package + dependencies; defines the `nuauto` command: .venv/bin/nuauto, ~/.local/bin/nuauto links there)
src/nuauto/: cli.py (the nuauto command)   config.py (all paths, hosts; reads local/local_config.json)
  sheet.py (rows, limits, status updates, Google login)   apply.py (the NUworks runner)
  browser.py (login/open, domain lock, cookies)   answers.py (answer bank)   jobs.py (pool + viewers)   fields.py (what you study: per-field starting preferences, kinds of work)
  daily.py (homelab update + Discord)   web.py (Mark-done page, homelab)   sync.py (laptop<->homelab)   deploy.py (homelab: deploys main)
  doctor.py (health check)   inspect_form.py (read-only form lister)   assist.py (company-site agent)   accounts.py (its job-site logins)
  mail.py (the agent's email codes and links: read-only Gmail)   screen.py (the homelab assistant's virtual screen + browser view)
  review.py (pairing devices with the homelab's page; its assistant sessions' screens and queue, served by gui.py)   remote.py (its queue, and claims between the machines)
  link.py (the laptop app's link to the homelab's page: a paired device; gui.homelab and /api/homelab/ use it)
  setup_sheet.py (sheet setup / `format` restyle)   gui.py (the window's server)   gui_static/ (its page)
  health.py (the checks behind doctor and the GUI)   onboard.py (setup wizard)   demo.py (demo mode fakes)
  window.py (which window) + window_gtk.py (the Linux window, run by the system python3)
  insights.py (pay, places, kinds of work, sheet statuses: the GUI's Insights screen and `nuauto insights`)
  manage.py (add / move / remove sheet rows by hand: the GUI's Apply (Add a job, a row's More) and `nuauto sheet add|move|remove`)
  intern.py (Summer internships from Simplify's and vanshb03's lists, a job on both kept once: the second pool, docs/PIPELINE.md)   postings.py (reads a posting
  from a company's own job site, no login: job-site JSON, page data, hidden browser; never a non-public address)
  Imports are always absolute: `from nuauto import sheet` (test_sync enforces it).
prompts/ (TRIAGE_, SCORE_, CATEGORY_PROMPT.md: Claude batch prompts; INTERN_TRIAGE_, INTERN_SCORE_PROMPT.md: the
  internships'; ASSIST_PROMPT.md: agent rules)
tests/ (test_*.py offline checks)   docs/ (DEPLOY, PIPELINE, GUI, REMOTE, ASSIST, ARCHITECTURE, SAFETY, STATUS)
deploy/systemd/ (homelab units)   install.sh (installer from source)   packaging/ (the packaged app: spec, build.sh, smoke.py)
.github/workflows/ (tests.yml: tests on Ubuntu + macOS, on PRs and main; release.yml: AppImages + DMGs, built on PRs (Intel Mac: tags only), released on v* tags)
README.md (public, new-user setup; keep it short)   LICENSE   local_config.example.json (template)
local/ (gitignored, mode 700): everything personal or secret, on both machines:
  local_config.json (sheet ID, resume path, homelab hostname/dir, Mark-done URL, Tailscale IP),
  profile.json (resume_label), answers.json, answers_other.json (Other jobs tab), google_login.txt, browser_profile/, assist_profile/,
  resume.pdf (homelab copy), gui.lock, browser_profile.lock, onboard.json, and the SECRETS below.
  Keep personal values (names, emails, hosts, IPs, IDs, companies) out of every committed file: the
  repo is public.
Generated (gitignored, repo root): data/, logs/, work/

TESTS (moved from CLAUDE.md 2026-10-16)
`nuauto test` runs every tests/test_*.py (4 at a time): offline, no sheet, no real NUworks, no network
(browser tests use headless Firefox on local pages or demo mode). Run it before pushing; CI runs it on
Ubuntu and macOS. tests/test_sync.py fails if a new module or prompt isn't in sync.CODE (so it would never
reach the homelab). test_web.py covers the public Mark-done links (signatures; GET never
changes the sheet). test_browser.py covers the domain lock and the profile lock. test_assist.py covers the
company-site agent's guard. test_mail.py covers the email codes and links (Gmail's message format, which mail counts, the
agent's `email` command). test_insights.py covers the Insights numbers (pay parsing, folding, no guessing). test_intern.py covers the
internship pool and postings.py (job-site links, parsing, the network guard on a local server, fetch tries, rules,
Review queue, a whole update with Claude stood in for). test_deploy.py runs deploy.py on a throwaway git repo (copy, web restart only when needed, undo on failure). test_demo.py runs the real apply.py on demo mode's fake pages (stops, cap, re-login).
test_gui.py covers the GUI server's security and flows; test_homelab_app.py the same app as the homelab serves it (demo
`--remote`: pairing, refusals, sessions, queue, devices, a phone-width browser); test_link.py the laptop's link to it (pairing,
calls, the /api/homelab/ routes, an Apply run there, unpairing; then `--demo --linked` in a browser); tests/screens.py (not a test:
every screen, used by test_gui, test_homelab_app and test_link); test_review.py its handler's rules and the sessions' backend; test_view.py the read-only `--view` mode; test_setup.py the wizard; test_health.py the checks;
test_window.py the packaged-app plumbing (self_cmd, the assistant's command, the window choice, clean env).
