SAFETY: every guardrail, where the code enforces it, which test covers it
(Keep this current when you change a rule. Rules in prose: CLAUDE.md. Tests: `nuauto test`, tests/.)
Format: rule | enforced in (src/nuauto/) | test. "no test" = nothing in tests/ exercises it.
Paths below are in src/nuauto/ unless a folder is given.

BATCH CLAUDE (daily.run_claude: triage, score, category for NUworks and internships)
- Runs in work/, reads and edits only there (Read(./**), Edit(./**); never a bare Write, which allows every path);
  no shell, web or MCP tools; -p refuses anything else. The batches hold web text (postings, Simplify's list) | daily.run_claude |
  test_daily (the command); a real call: writes in work/ work, /tmp and /etc refused (2026-10-10, by hand)
- Internship imports keep the good rows (a mis-copied id or a failed batch is retried next run; nothing usable stops
  the step) | jobs.read_outputs(lenient), intern.take | test_intern

APPLYING (apply.py, sheet.py)
- Only Approved rows are acted on | apply.pick_row, sheet.approved; set_status refuses a row that is not Approved | test_sheet (approved filter, refusals)
- Weekly cap (fixed weeks from local_config week_start, else the last 7 days) | sheet.max_per_week() (default MAX_PER_WEEK = 11; local_config "max_per_week" 1-30, anything else = 11; Settings in the GUI), sheet.week_window, sheet.check_limits; apply.main checks before every row | test_sheet (7-day edge, limit refuses, the setting's bounds), test_gui (settings refuse bad values), test_demo (a run refuses up front)
- Total cap | sheet.MAX_TOTAL = 99, same check_limits | test_sheet (counts only; the total refusal itself is not tested)
- Applied row with no/odd date is an error, never guessed | sheet.applied_dates | test_sheet
- One application at a time, 30-60 s apart | apply.main loop, time.sleep(random.uniform(30, 60)) | no test
- apply submits only with you there: a real terminal, or the GUI (`--ui json` only as a child of the running GUI, its pid in local/gui.lock); never inside a Claude Code shell (CLAUDECODE); the same for `assist nuworks <row>`; assist.run needs a terminal | apply.refuse_unattended (isatty / answers.json_ui_allowed / CLAUDECODE), apply.main, assist.main | test_demo (CLAUDECODE and --ui json refusals), test_answers (json_ui_allowed)
- Row can never be sent twice | sheet.mark_submit_started (row becomes Needs Human BEFORE the Submit click); sheet.resolve_submit only accepts a marked row | test_sheet (submit-safety checks)
- Screenshot before Submit, and after | browser.RunLog.screenshot ("filled_popup", "after_submit") in apply.fill_popup / submit_flow | no test
- Every action logged per application under logs/ | browser.RunLog | no test
- After-submit confirmation checked | apply.submit_flow waits for "Your application has been submitted"; no text = asks you (y/n/u; anything else = u, the row stays Needs Human) | test_demo / test_gui (the confirmed path on the fake pages); the unconfirmed path: no test
- Popup links off-site, "How to Apply", missing Cover Letter/Transcript = stop as Needs Human | apply.check_popup | test_apply
- Unexpected error never carries on to the next job | apply.apply_one (outcome "stop") | no test
- Ctrl+C (or the GUI's Stop: SIGINT to the run) stops cleanly; row stays Approved, or Needs Human if Submit was already clicked. The GUI going away (the run's stdin closes) stops it the same way | apply.apply_one (KeyboardInterrupt branch), answers.JsonIO, gui.Task.stop | test_demo (stop mid-question, GUI gone), test_answers (exactly one interrupt)
- Deadline passed = Needs Human, not attempted | apply.main | no test
- Dry run exists only as hidden --dry-run | apply.main | no test

FORM FILLING (apply.py, answers.py)
- Exact match only: lowercase + trim, no fuzzy | answers.norm, answers.find | test_answers
- No match: stop and ask, save the answer, reuse it; alias option | answers.resolve_answer | test_answers
- always_ask entries never auto-filled, never saved | answers.resolve_answer | test_answers
- Dropdowns: only an option whose text exactly equals the saved answer, else ask | answers.resolve_answer, apply.fill_extra_fields (select_option by label) | test_answers
- Value read back after filling must equal what was typed | apply.fill_extra_fields | test_answers (real local form)
- Free text (textarea), unlabeled fields, unseen field types stop | apply.fill_extra_fields (NeedsHuman) | test_answers
- No terminal = no asking: unknown field stops the run | answers.NoTerminalIO | test_answers (unknown question, nobody to ask)
- GUI answers get the same checks as typed ones: an option must be one of the form's choices, an alias index must exist, blank = stop, always-ask never saved | answers.JsonIO, resolve_answer | test_answers (JsonIO section)
- Resume: only the exact resume_label from profile.json; missing label stops | apply.check_popup, apply.load_resume_label, shown_resume re-checked right before Submit | test_apply (missing option, no dropdown)
- Never uploads in apply.py (no file-input call exists in it) | absence in apply.py | no test

BROWSER (browser.py, config.py)
- Domain lock: only config.ALLOWED_HOSTS (northeastern-csm.symplicity.com), https only; navigations elsewhere aborted | browser.host_allowed, browser.install_domain_lock | test_browser
- Backslash URL tricks refused | browser.host_allowed | test_browser
- Embedded frames from other hosts are blocked without leaving the page | browser.install_domain_lock | test_browser
- SSO hosts (config.SSO_HOSTS) allowed only inside the one-click re-login, then cleared (also on error / Ctrl+C) | browser.sso_hosts_allowed, browser.relogin | test_browser
- Never types credentials: re-login is one click; a password field means stop and ask for `nuauto login` | browser.relogin | no test
- Page-load timeout gets one retry, then gives up | browser.goto | test_browser
- Cookie file mode 600, profile dir mode 700 | browser.save_cookies, browser.launch | no test
- Refuses to open a URL off the allowed host | browser.cmd_open | no test
- Only one process on browser_profile/ (apply, the update, login, the GUI's NUworks check) | browser.lock_profile (flock on local/browser_profile.lock, from launch to close; a failed launch releases it); the GUI's background check gives way to your runs | test_browser (profile lock)

COMPANY-SITE AGENT (assist.py, prompts/ASSIST_PROMPT.md)
- Side by side: at most assist.slots() (3) sessions; one per row and one per job site at a time (locks held by the
  launcher for the whole run, freed if it dies); each slot its own browser profile | assist.take_slot, take_site,
  run (row lock), slot_profile | test_assist (slots, sites, a slot held by another process), test_gui (the rows say so)
- No answer lost between sessions: every answer-bank command and the Answers screen's save hold local/answers.lock
  (the screen's version check and save are one step) | assist.bank, gui.answers_put | test_assist (8 writers at once;
  without the lock they lose answers)
- Submit-type click asks the user in the terminal first | assist.decide (SUBMIT_RE on the element name) -> hook "ask" | test_assist
- Except a sign-in or create-account page's own "Submit" / "Confirm": the latest snapshot has a password box and only
  account boxes (email, user name, password; tick boxes and bot traps aside): no application can go out there. Any
  other box on the page (a question), or another name ("Apply", "Submit Application"), still asks | assist.sign_in_page |
  test_assist (Workday's real sign-in markup)
- Never types into a bot trap ("for robots only", "leave blank": filling it marks you as a bot) | assist.decide | test_assist
- Enter key and type(submit=true) ask first | assist.decide (ENTER_KEYS, browser_type) | test_assist
- Except on a sign-in page (assist.sign_in_page) or the job's own posting before anything is filled in (assist.posting_page:
  the row's link, a language part like /en-US/ aside, no form boxes on it but a job search box): there Enter, and the
  posting's own "Apply" / "Apply now", don't ask (they open the application). The catch, accepted 2026-10-10: a site with
  one-click apply from a saved profile could submit there | assist.posting_page, same_posting | test_assist (Workday's real
  posting markup)
- On a Workday site, Enter in a box doesn't ask: the box has the focus in the latest snapshot ([active]), or it is the
  box typed into with submit=true. Workday's pages aren't HTML forms (no submit on Enter) and its final Submit is a
  button on a review page with no boxes; Enter with the focus on a button, or on any other site, asks | assist.workday,
  decide (browser_press_key, browser_type) | test_assist
- A button or link with no name but its text (`button [ref=e9]: Submit`, as Workday writes some) is checked by that text
  (before 2026-10-10 such a button had no name, so a final Submit written that way would not have asked) | assist.parse_refs | test_assist
- Element names come only from the latest full snapshot; page-changing actions clear refs; unknown ref = deny | assist.update_after, assist.parse_refs, decide (browser_click) | test_assist
- Unnamed fields take the label text above them, from the page | assist.parse_refs | test_assist
- Password fields: only {{NEW_PASSWORD}} / {{PASSWORD}}, alone, into a field the latest snapshot names a password (never
  the agent's description), not with submit=true, never on an identity provider or Northeastern (accounts.never); the
  guard types the site's own password (updatedInput, "allow") and the agent never sees it | assist.password_check,
  decide, fill, hook | test_assist
- One random password per site (Workday tenant, iCIMS site...), made by NUauto, kept in local/accounts.json (600, under a
  lock, never synced); {{PASSWORD}} where NUauto has no login = deny | accounts.password_for, make_password | test_assist
- The browser tool's reply ("Ran Playwright code" echoes typed text) is cleaned of those passwords before the agent sees
  it (updatedToolOutput), and in logs/<run>/last_response.json; actions.log records the placeholder | assist.redact, hook
  (post) | test_assist (the hook's output and the files); a real session: see GAPS
- Bitwarden export: mode 600, logins matched on their own host only | accounts.export, bitwarden | test_assist
- No page scripts; only tools in assist.TOOLS | assist.decide | test_assist
- Navigation only to http(s) | assist.decide | test_assist
- Uploads: only the run's copy of the resume (logs/<run>/upload/, resolved path) | assist.decide (browser_file_upload) | test_assist
- Bash: only the answer-bank command (answer|save|once|alias|blank|wait), no shell operators | assist.check_bash | test_assist
- Read/Glob/Grep only inside the resume and local_config.json assist_read_paths; symlinks resolved | assist.check_read, assist.under | test_assist
- Never read inside them: NUauto's local/ folders (this one and any checkout's, found at start), .ssh/.gnupg/.git/...,
  key / token / cookie / password / .env files; Grep may not search a folder holding a local/ | assist.secret_path,
  protected_dirs, check_read | test_assist
- WebSearch / WebFetch allowed (read-only lookups), logged | assist.decide (WEB_TOOLS) | test_assist
- A guard that fails or times out (60 s) blocks the action: the hooks' "onFailure": "block" (Claude Code 2.1.295+) |
  assist.session | test_assist (the settings it writes)
- Saved/once answers: one line, max 300 chars, must equal an option; already-saved answers are not overwritten; always_ask / voluntary pages are never saved | assist.valid_answer, assist.bank | test_assist
- Voluntary / EEO pages always ask the user | assist.page_always_asks, assist.lookup | test_assist
- Unknown label: the agent gets the saved answers to infer from (not always_ask, not leave-blank), then aliases the label; anything else it asks | assist.lookup, assist.saved_answers | test_assist (what is handed out); the inference itself is prompt-only
- Guard crash blocks the action | assist.hook_main (exit 2) | no test
- Rows the agent may take: Needs Human + "External application"; never NUworks (even with --url) | assist.assist_target | test_assist
- Sheet touched only by the launcher, after you answer y; agent has no sheet access | assist.run (mark_applied_by_hand), tools limited to the list above | test_assist (target rules only; the y/n flow is not tested)
- NUworks side after a company-site submit reuses apply code, outcome appended to Notes | apply.submit_nuworks_side, sheet.append_note | no test
- NUworks side never sent twice: apply.NUWORKS_CLICK_MARK goes in Notes before the click; a retry refuses once it (or "submitted") is there | assist.nuworks_side_blocked | test_assist
- Every guard decision logged | assist.hook -> actions.log | no test
- Playwright MCP pinned to one version (guard tested against its tool inputs) | assist.MCP_PACKAGE | no test

MARK-DONE PAGE (web.py)
- Every link HMAC-SHA256 signed for one action + row + job | web._sig, web.link, web.Handler._params (hmac.compare_digest) | test_web
- Tampered row/job/action/signature, odd characters, unknown action: 404 | web.Handler._params | test_web
- GET shows a confirm page only; only POST changes the sheet | web.Handler.do_GET / do_POST | test_web
- Row must still hold the same job URL, else 409 and nothing changes | web.Handler._row | test_web
- Secret file created mode 600 | web._secret | test_web
- No access log (query strings hold signatures) | web.Handler.log_message | no test
- Listens only on web_listen_host (homelab Tailscale IP) | web.LISTEN | no test

THE WINDOW (gui.py, gui_static/)
- Only this machine, only you: 127.0.0.1, random port; a secret per start, swapped for an HttpOnly SameSite=Strict cookie; Host must name this server (DNS rebinding); every POST needs X-NUauto: 1, a JSON body and a matching Origin | gui.Handler (_host_ok, _authed, do_POST) | test_gui (security section)
- Only the page's own files run (CSP), no framing; static files by name only; /api/file serves only .png/.log/.txt under logs/ (real paths) | gui.Handler._send/_static, gui.log_file | test_gui (path tricks)
- Secrets never reach the page (token, cookies, webhook, client secret: "set / not set" only) | gui.settings_get, health checks | test_gui (token not in settings)
- The real window never starts inside a Claude Code shell; agents use --demo (fake) or --view (real data, read-only) | gui.main | test_gui, test_view
- `--view` can't change or start anything: every POST but Quit refused, no Task, no lock, no sync, answers / logs hidden | gui.VIEW (Handler.do_POST, App.start, run_group) | test_view
- One window server at a time (a second start opens a window on the first, with a secret that can do nothing else) | gui.running_instance, /api/window | test_gui (second start)
- Runs you watch get a visible browser (AUTO_HEADLESS cleared for apply, logins, the NUworks side) | gui.VISIBLE | no test
- Undo of an approval only moves a still-Approved row with the same job back to Proposed; nothing is deleted | sheet.unapprove | test_demo, test_gui
- Answer-bank edits: refused while a run uses the bank, refused if a run saved an answer since you opened it, duplicate questions/aliases refused | gui.answers_put | test_gui
- Checks never open a login page or show a secret | sheet.open_worksheet(interactive=False), health.* | test_health (interactive False; webhook not shown)
- Demo mode never touches real files or the network: it needs its own NUAUTO_STATE_DIR, its browser has a proxy that does not exist and aborts every non-NUworks request, the sheet and claude are fakes | config.py (DEMO check), browser.launch, demo.serve_nuworks | test_demo (refuses without its own folder)

THE PACKAGED APP (packaging/, config.FROZEN)
- The same rules and code as a source install; your files never go in the bundle (app-data folder, mode 700 local/) | config.py (FROZEN paths) | packaging/smoke.py (demo end to end in the built app)
- Always local mode: no homelab sync from a packaged app | config.SERVER_HOSTNAME = "" when frozen | no test
- The assistant's hook / answer-bank command stays exactly two words (`<app> _assist`) and check_bash still refuses anything else | assist.answer_prefix, check_bash | test_window
- Programs it starts that are not its own children (system python3, browsers, a terminal) get none of its PyInstaller / AppImage / Python variables | window.system_env | test_window
- Closing NUauto's own window quits it; a run in progress stops the way Ctrl+C stops it | window.open_gtk / run_mac -> App.quit | packaging/smoke.py (process group gone after SIGTERM); the window close itself: no test
- The update check sends nothing about you (GET of the latest public release) | health.app_update | test_health (source install: no call)
- A hidden browser only in demo mode (NUAUTO_DEMO_HEADLESS or no screen); a real apply always opens a visible one | browser.launch (config.DEMO check), gui.VISIBLE | test_selftest / test_demo (no screen); the real side: no test

SETUP WIZARD (onboard.py)
- Only a Desktop-app Google client file is accepted; saved mode 600 | onboard.check_client, save_client | test_setup
- A sheet with other data is never written to (an empty one is set up; NUauto's own columns = fine) | onboard.open_and_prepare, setup_sheet.setup (HasData) | test_setup
- Discord webhook checked with a GET (posts nothing until you press Test), saved mode 600, never shown again | onboard.save_webhook | test_setup
- The NUworks reads (resume labels, terms) are read-only: hidden browser, domain lock on, nothing filled, never Submit (the resume read presses Cancel) | onboard.read_resume_labels, read_terms | test_setup (on the fake pages)
- Preferences are validated before they shape the pool or Claude's prompt (no "{{" in text, known categories, ranges) | onboard.check_prefs | test_setup

GAPS (policy or prompt only, or weaker than the rules read)
- Assist: typed text is not checked against the answer bank. assist.issue records what the bank handed out (state issued / issued_values) but decide never compares it with browser_type / browser_fill_form text. "Only infer from saved answers, else ask" and "essays only after I approve" are prompt-only.
- Assist: Submit detection is by exact button name (SUBMIT_RE). An icon-only or differently worded final button ("Place order", "Done") is not caught. Pressing Space on a focused button is not asked about (only Enter is).
- Assist: no domain lock. The agent may visit any http(s) site and tick any box; only the Submit review stands between it and a submission.
- Assist: the cap is checked once at start (sheet.check_limits); marking a row Applied by hand has no cap check (the application has already gone out).
- local/ being mode 700 is not enforced in code (only the profile dir and the two secret files get chmod).
- "Check NUworks' terms of use before running against the real site" is a human to-do (docs/STATUS.md OPEN), not enforced.
- Reading answers.json / profile.json / token files is protected only by file permissions and by the assist read-limits; nothing scrubs logs/ (screenshots can show personal data; logs/ is gitignored).
- Assist: Grep's own file choice is ripgrep's (hidden and gitignored files skipped by default): a secret in an ordinary
  file inside the notes folders could reach the agent through Grep. Read refuses secret-looking names; Grep only
  refuses folders that hold a local/. The agent can reach any web page (WebFetch, the browser), so what it reads can
  leave: keep notes folders free of secrets.
- Assist passwords: Claude Code's own session log (~/.claude/projects/) and telemetry may keep the browser tool's
  original reply; the guard cleans what the agent sees and NUauto's logs. Playwright MCP's page dumps (logs/<run>/
  page-*.yml) hold snapshots, which do not show password values. A site that shows the password in a visible box
  after typing would show it to the agent.
