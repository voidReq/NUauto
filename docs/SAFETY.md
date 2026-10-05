SAFETY: every guardrail, where the code enforces it, which test covers it
(Keep this current when you change a rule. Rules in prose: CLAUDE.md. Tests: `nuauto test`, tests/.)
Format: rule | enforced in (src/nuauto/) | test. "no test" = nothing in tests/ exercises it.
Paths below are in src/nuauto/ unless a folder is given.

APPLYING (apply.py, sheet.py)
- Only Approved rows are acted on | apply.pick_row, sheet.approved; set_status refuses a row that is not Approved | test_sheet (approved filter, refusals)
- Weekly cap (fixed weeks from local_config week_start, else the last 7 days) | sheet.MAX_PER_WEEK = 11, sheet.week_window, sheet.check_limits; apply.main checks before every row | test_sheet (7-day edge, limit refuses)
- Total cap | sheet.MAX_TOTAL = 99, same check_limits | test_sheet (counts only; the total refusal itself is not tested)
- Applied row with no/odd date is an error, never guessed | sheet.applied_dates | test_sheet
- One application at a time, 30-60 s apart | apply.main loop, time.sleep(random.uniform(30, 60)) | no test
- apply must run in a real terminal | apply.main (isatty check, exits otherwise); assist.run likewise | no test
- Row can never be sent twice | sheet.mark_submit_started (row becomes Needs Human BEFORE the Submit click); sheet.resolve_submit only accepts a marked row | test_sheet (submit-safety checks)
- Screenshot before Submit, and after | browser.RunLog.screenshot ("filled_popup", "after_submit") in apply.fill_popup / submit_flow | no test
- Every action logged per application under logs/ | browser.RunLog | no test
- After-submit confirmation checked | apply.submit_flow waits for "Your application has been submitted"; no text = asks you (y/n/u) | no test
- Popup links off-site, "How to Apply", missing Cover Letter/Transcript = stop as Needs Human | apply.check_popup | test_apply
- Unexpected error never carries on to the next job | apply.apply_one (outcome "stop") | no test
- Ctrl+C stops cleanly; row stays Approved, or Needs Human if Submit was already clicked | apply.apply_one (KeyboardInterrupt branch) | no test
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
- Only one Playwright session on browser_profile/ | Firefox's own profile lock only; nothing in our code | no test (see GAPS)

COMPANY-SITE AGENT (assist.py, prompts/ASSIST_PROMPT.md)
- Submit-type click asks the user in the terminal first | assist.decide (SUBMIT_RE on the element name) -> hook "ask" | test_assist
- Enter key and type(submit=true) ask first | assist.decide (ENTER_KEYS, browser_type) | test_assist
- Element names come only from the latest full snapshot; page-changing actions clear refs; unknown ref = deny | assist.update_after, assist.parse_refs, decide (browser_click) | test_assist
- Unnamed fields take the label text above them, from the page | assist.parse_refs | test_assist
- Password fields: no typing or filling | assist.decide (browser_type, browser_fill_form) | test_assist
- No page scripts; only tools in assist.TOOLS | assist.decide | test_assist
- Navigation only to http(s) | assist.decide | test_assist
- Uploads: only the resume file (resolved path) | assist.decide (browser_file_upload) | test_assist
- Bash: only the answer-bank command (answer|save|once|alias|blank|wait), no shell operators | assist.check_bash | test_assist
- Read/Glob/Grep only inside the resume and local_config.json assist_read_paths; symlinks resolved | assist.check_read, assist.under | test_assist
- Saved/once answers: one line, max 300 chars, must equal an option; already-saved answers are not overwritten; always_ask / voluntary pages are never saved | assist.valid_answer, assist.bank | test_assist
- Voluntary / EEO pages always ask the user | assist.page_always_asks, assist.lookup | test_assist
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

GAPS (policy or prompt only, or weaker than the rules read)
- Assist: typed text is not checked against the answer bank. assist.issue records what the bank handed out (state issued / issued_values) but decide never compares it with browser_type / browser_fill_form text. "Never guess" and "essays only after I approve" are prompt-only.
- Assist: Submit detection is by exact button name (SUBMIT_RE). An icon-only or differently worded final button ("Place order", "Done") is not caught. Pressing Space on a focused button is not asked about (only Enter is).
- Assist: no domain lock. The agent may visit any http(s) site and tick any box; only the Submit review stands between it and a submission.
- Assist: the cap is checked once at start (sheet.check_limits); marking a row Applied by hand has no cap check (the application has already gone out).
- Single use of browser_profile/ is not enforced by our code, only by Firefox locking the profile.
- local/ being mode 700 is not enforced in code (only the profile dir and the two secret files get chmod).
- "Check NUworks' terms of use before running against the real site" is a human to-do (docs/STATUS.md OPEN), not enforced.
- Reading answers.json / profile.json / token files is protected only by file permissions and by the assist read-limits; nothing scrubs logs/ (screenshots can show personal data; logs/ is gitignored).
