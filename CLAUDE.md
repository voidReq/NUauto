NUAUTO (NUworks application helper)
Tracks co-op jobs in a Google Sheet and fills out NUworks applications in the browser.
I (the human) decide which jobs to apply to by marking them Approved in the sheet; that is
the go-ahead (since 2026-10-03 there is no per-job y/n at submit time). This is NOT a
mass-apply bot.

This file has the rules. Also read (imported here; update them when you change things):
- @docs/DEPLOY.md (modes, machines, the homelab = prod, Mark-done page, sync, secrets, Google login, health checks, rebuild steps)
- @docs/PIPELINE.md (how the job pool is built, my bounds, ranking, ratings)
- @docs/GUI.md (the window `nuauto gui`: screens, setup wizard, health checks, safety model, demo mode, API)
- @docs/STATUS.md (what is done, what is not yet proven for real, dated decisions; my local log,
  gitignored: keep job/company names and other personal history there, never in tracked files)

WHERE AM I
- Laptop (Fedora, ~/projects/auto): code is edited here. Interactive commands run here.
- Homelab (`ssh homelab`; hostname and path in local_config.json) = PROD. Runs the
  scheduled update, Discord messages and the Mark-done page. config.IS_SERVER is True there.
  Code is copied there by sync.py: never edit it on the homelab. Details: docs/DEPLOY.md.

GIT
- This folder is a git repo (since 2026-10-03). Remote: github.com/voidReq/NUauto, PUBLIC (since
  2026-10-04). main only receives merges: work on a branch, merge when I OK it, push when I OK it.
  Before any push: no personal info in tracked files or commit messages (names, emails, hosts,
  IPs, IDs, companies I applied to); those go in local_config.json or docs/STATUS.md.
- Gitignored: local/ (all personal files and secrets), docs/STATUS.md, data/, work/, logs/, .venv/.
- Whatever is checked out on the laptop is what the next `nuauto` command pushes to the homelab.

HARD LIMITS
- Under 100 applications total.
- Weekly cap, enforced in code (count "Applied" rows with a date in the current week; refuse to
  run past the limit). sheet.py: MAX_PER_WEEK = 11, MAX_TOTAL = 99 (decided 2026-10-03). A week =
  fixed 7-day periods from local_config.json "week_start" (sheet.week_window); without it, the last
  7 days. Mine: week_start 2026-10-06 (reset because I started applying late), then every 7 days.
  Approved rows beyond the cap just wait for the next week.
- One application at a time.
- I should check NUworks' terms of use before running against the real site.

SAFETY RULES (most important)
1. No guessing, ever. If unsure about anything, stop and ask me.
2. Only act on Approved rows. (Exception: `nuauto assist <row>` works on a Needs Human row I
   approved earlier; the row only changes after I press Submit myself and answer y.)
3. Field filling uses two local files:
   - profile.json: basic info (name, email, phone, address, school, etc.)
   - answers.json: answer bank (see below)
4. Any field not covered by those files = stop, ask me in the terminal.
5. Never write free text (essays, cover letters, "why do you want to work here", etc.).
   Those always stop and wait for me.
6. Uploads: only one fixed resume file path that I choose. Nothing else.
   DECISION: NUworks has no upload; its Apply popup picks from resumes already in my
   profile. The script selects only the exact "resume_label" saved in profile.json and
   never uploads anything.
7. (Changed 2026-10-03; GUI added 2026-10-05.) `nuauto apply` submits every Approved row, one at a
   time, with no per-job prompt: Approved in the sheet IS my approval. It runs only with me there:
   in a real terminal, or from the GUI (`nuauto gui`: I click Start, the browser is visible, Stop =
   Ctrl+C). Never from an agent: code refuses real submits inside a Claude Code shell (CLAUDECODE),
   and `--ui json` works only as a child of the running GUI. A dry run (fill, screenshot, stop)
   exists only as the hidden --dry-run dev flag.
8. (Changed 2026-10-03.) No y/n before submit. Every filled form is still screenshotted to
   logs/ before Submit, and after-submit confirmation is checked.
9. Log every action and save every screenshot to a logs/ folder, per application.
10. Ctrl+C must stop everything cleanly and leave the sheet in a correct state.

ANSWER BANK (answers.json)
- Each entry: question text, my answer, field type, date added, optional always_ask flag.
- Matching: lowercase + trim the field label, then EXACT match only. No fuzzy matching.
- No match: stop, ask me in the terminal, save my answer, reuse it next time.
- If wording is slightly different, ask me again; I can then mark it as an alias of an
  existing question.
- Dropdowns/radios: only select an option whose text exactly matches my saved answer.
  If not present, ask me.
- always_ask entries (e.g., salary, work authorization, demographic questions) are never
  auto-filled.
- Starter questions to prefill: name, preferred name, email, phone, address, school, major,
  expected graduation date, GPA, work authorization, sponsorship needed, available start/end
  dates, hours per week, co-op term, relocation, commute, LinkedIn, GitHub, portfolio, how did
  you hear about us, over 18, OK with background check.

SHEET
Columns (row 1): URL, Company, Title, Status, Notes, Date. setup_sheet.py made them (one-time,
never overwrites existing data), with the Status dropdown and row 1 frozen.
Status values: Proposed, Approved, Applied, Failed, Needs Human
- Proposed: job added by me (or suggested), not approved yet.
- Approved: I approved it. The script only ever acts on these rows.
- Applied: submitted. Fill in Date.
- Failed / Needs Human: script stopped. Put the reason in Notes.
- Notes starting "ALSO APPLY ON COMPANY SITE" (SITE_MARK): submitted on NUworks, but the
  company also wants its own site; I do that part.
Google access is OAuth only, never service accounts (docs/DEPLOY.md, GOOGLE SHEETS LOGIN).

BROWSER (Playwright, Firefox)
- Deterministic script, not an AI clicking around freely. Slow, human-like pacing.
  (Exception: company sites, with me watching: `nuauto assist`, below; it may also upload the resume
  and type longer answers I approved, never submit without my review.)
- Persistent browser profile in local/browser_profile/; I log in by hand (`nuauto login`) and the
  session is reused. Never handle my password in code or prompts.
- Domain lock: only northeastern-csm.symplicity.com (config.ALLOWED_HOSTS). Jobs whose Apply
  popup links to an external site (Workday, iCIMS, Greenhouse...) are marked Needs Human; I
  apply there. Exception: during the one-click re-login only, SSO_HOSTS are allowed (never
  typing credentials).
- Firefox drops session cookies on close, so browser.py saves cookies to
  local/session_cookies.json (chmod 600, never print it) and reloads them.
- Only ONE Playwright session may use local/browser_profile/ at a time (Firefox locks it, and since
  2026-10-05 browser.lock_profile refuses a second one): not while apply.py, jobs.py or browser.py are
  running. Use a separate profile dir if in doubt.
  The homelab has its own copy and uses it during its update runs (08:00/18:00 New York).

GUI (`nuauto gui`, gui.py + gui_static/ + onboard.py + health.py + demo.py; added 2026-10-05; docs/GUI.md)
- A local web app on the laptop: 127.0.0.1 only, a secret per start (cookie), Host/Origin/X-NUauto checks;
  secrets never reach the page. Long or browser work runs as the same `nuauto ...` commands in child
  processes, one at a time; their questions come over answers.JsonIO as dialogs (same rules as typing).
- Agents: only `nuauto gui --demo` (fake sheet, fake NUworks pages, fake Claude, temp folder; nothing real
  can be read or sent). Never start the real GUI or click Start in one; the real GUI refuses to start
  inside a Claude Code shell anyway. Screens for review: `nuauto gui --demo --screenshots DIR`.
- Setup wizard (onboard.py): each step checks itself; preferences go in local_config.json "preferences"
  (jobs.DEFAULTS = my original settings). Health checks (health.py) never open a login page.
- Its own window (window.py): macOS pywebview; Linux the system python3's GTK + WebKitGTK (window_gtk.py); else a
  Chrome app window or a tab. Closing it quits NUauto. `nuauto _window` says which.
- Packaged app (packaging/, added 2026-10-05 on my request: "for the users local, preferably executable"): AppImage
  (Linux) and DMG (macOS) from PyInstaller, built + smoke-tested by .github/workflows/release.yml on v* tags. Same code
  (config.FROZEN): files in the app-data folder, local mode only, runs itself via config.self_cmd / self_exe (never
  `python -m` or file paths in code that may run packaged). Build locally: sh packaging/build.sh; check: packaging/smoke.py.
- Not yet proven for real: a GUI apply with me watching, the logins through the GUI, the NUworks reads
  (resume labels, term list), macOS (CI runs the tests there in demo mode).

COMPANY-SITE AGENT (assist.py + prompts/ASSIST_PROMPT.md, `nuauto assist <row>`; added 2026-10-05)
- Only for Needs Human rows stopped at an external application (Notes start "External application";
  Workday, Oracle, iCIMS, SuccessFactors...). Never NUworks itself (refused even with --url), never rows
  stopped for something on NUworks (cover letter, transcript...): those are mine (assist.assist_target).
- Laptop, real terminal, me watching. Starts `claude --model sonnet` with the Playwright MCP browser
  (profile assist_profile/: it holds my company-site logins, treat it as secret), Bash and Read/Glob/Grep.
- Relaxed 2026-10-05 (my call): the agent may visit any site, fill anything, tick boxes, upload the
  resume, and draft longer answers that I approve before it types them. The one hard rule: NOTHING is
  submitted without my review. Enforced by code (Claude Code hooks -> `assist.py hook pre|post`, logic
  in assist.decide / update_after, tested in tests/test_assist.py): every Submit-type click (SUBMIT_RE, incl.
  "Apply"), Enter and type(submit) make the terminal ask me first ("ask"); element names come from the
  latest full snapshot only (refs cleared by anything that changes the page), never from the agent's
  description. Never: password fields, page scripts (could submit behind the review), uploads other than
  the resume, non-web links; Bash only the answer-bank command (answer|save|once|alias|blank|wait);
  Read/Glob/Grep only inside my resume and local_config.json "assist_read_paths" (my writeups).
- Mine: sign-in, captchas, approving Submit. After I /exit, the launcher asks "did you submit?"; y =
  Applied (dated today, counts toward the weekly limit), then the same job is submitted on NUworks too by
  apply.submit_nuworks_side (the tested NUworks code; every popup check except the off-site-link stop;
  outcome appended to Notes; retry: `nuauto assist nuworks <row>`). The agent never touches the sheet.
  Log per run: logs/<stamp>_assist_row<N>/ (actions.log = every guard decision).
- Proven: a first application end to end (2026-10-05, stricter version). The "ask before Submit" hook
  prompt is proven in an interactive session; a full run with the relaxed rules is not yet.

NUWORKS LOGIN / AUTH FLOW (read this before touching the browser)
- NUworks sessions expire after a few hours. Expired = any page shows the "Sign In / Please
  select a role" screen (page title "Students: Sign-in method").
- Northeastern's SSO session lives much longer. So usually re-login is ONE click: click
  "Current Students And Alumni". It hops through shibboleth-northeastern-csm.symplicity.com and
  neuidmsso.neu.edu and lands back on NUworks logged in, with NO password or Duo prompt. Do
  that first; don't stop and ask me just because the sign-in screen appeared.
- Only if that click ends on a page asking for a username/password (or Duo), stop and ask me
  to log in: `nuauto login` (I log in, then close the window; it also copies the session to
  the homelab). Never type, store or ask for my password.
- Our scripts do the one click automatically (browser.relogin / goto_logged_in, used by
  apply.py, jobs.py, inspect_form.py, daily.py) and allow the two SSO hosts only during that
  click (SSO_HOSTS in config.py).
- "Local Login - Not Supported" on that screen is not for us; never use it.
- A separate browser profile (another agent's) starts with no SSO session, so the first
  login there needs me once; after that the one click works there too.

NUWORKS PROFILE (e.g. when another session edits it with Playwright)
- If the NUworks resume is replaced or renamed, update profile.json "resume_label" to the
  EXACT new text in the Apply popup's Resume dropdown. Until then apply.py stops safely.
- The resume uploaded to NUworks should be the one at local_config.json "resume_path".
- Profile class level says Junior; I'm a sophomore. Fixing it there is my call. jobs.py
  ignores the profile value either way. Profile changes can change NUworks' own "Jobs I
  qualify for" screening; the job pool doesn't depend on it.

FILES (layout since 2026-10-05: an installable package, `pip install -e .`)
pyproject.toml (package + dependencies; defines the `nuauto` command: .venv/bin/nuauto, ~/.local/bin/nuauto links there)
src/nuauto/: cli.py (the nuauto command)   config.py (all paths, hosts; reads local/local_config.json)
  sheet.py (rows, limits, status updates, Google login)   apply.py (the NUworks runner)
  browser.py (login/open, domain lock, cookies)   answers.py (answer bank)   jobs.py (pool + viewers)
  daily.py (homelab update + Discord)   web.py (Mark-done page, homelab)   sync.py (laptop<->homelab)
  doctor.py (health check)   inspect_form.py (read-only form lister)   assist.py (company-site agent)
  setup_sheet.py (sheet setup / `format` restyle)   gui.py (the window's server)   gui_static/ (its page)
  health.py (the checks behind doctor and the GUI)   onboard.py (setup wizard)   demo.py (demo mode fakes)
  window.py (which window) + window_gtk.py (the Linux window, run by the system python3)
  Imports are always absolute: `from nuauto import sheet` (test_sync enforces it).
prompts/ (TRIAGE_, SCORE_, CATEGORY_PROMPT.md: Claude batch prompts; ASSIST_PROMPT.md: agent rules)
tests/ (test_*.py offline checks)   docs/ (DEPLOY, PIPELINE, GUI, ARCHITECTURE, SAFETY, STATUS)
deploy/systemd/ (homelab units)   install.sh (installer from source)   packaging/ (the packaged app: spec, build.sh, smoke.py)
.github/workflows/ (tests.yml: tests on Ubuntu + macOS; release.yml: AppImages + DMGs on v* tags)
README.md (public, new-user setup; keep it short)   LICENSE   local_config.example.json (template)
local/ (gitignored, mode 700): everything personal or secret, on both machines:
  local_config.json (sheet ID, resume path, homelab hostname/dir, Mark-done URL, Tailscale IP),
  profile.json (resume_label), answers.json, google_login.txt, browser_profile/, assist_profile/,
  resume.pdf (homelab copy), gui.lock, browser_profile.lock, onboard.json, and the SECRETS below.
  Keep personal values (names, emails, hosts, IPs, IDs, companies) out of every committed file: the
  repo is public.
Generated (gitignored, repo root): data/, logs/, work/
SECRETS (in local/), never print, log or copy their contents: token.json, session_cookies.json,
client_secret.json, discord_webhook.txt, web_secret.txt

COMMANDS (installed in .venv; no activation needed)
nuauto gui             # the window (setup, review, apply, health); --demo: everything fake (agents: only this)
nuauto approve         # viewer on best unrated jobs: y = Approved in the sheet, n = no, s skip
nuauto apply [-n 3]    # submit every Approved row (30-60s between); real terminal (or the GUI) only
nuauto rate            # taste training viewer (keys in docs/PIPELINE.md)
nuauto update          # check NUworks for new jobs now (runs on the homelab, then syncs)
nuauto status          # Approved rows + weekly count (also pushes code to the homelab)
nuauto login           # manual SSO login; close the window when done
nuauto login google    # Google Sheets login, every 7 days (Discord warns the day before)
nuauto test            # all offline tests
nuauto doctor [--json] # health check: laptop, homelab, Mark-done page (read-only)
nuauto assist [<row>]  # company-site agent for a Needs Human row; asks me before any Submit
nuauto answers list    # answer bank; edit with: nvim local/answers.json
nuauto setup-sheet format          # restyle the sheet (formatting only, safe to re-run)
nuauto jobs stats | pool | suggest 5
nuauto daily [weekly] / nuauto web # what the homelab's timers / Mark-done service run
python -m nuauto.<module> ...      # any module's own command line (same as the nuauto tools)

TESTS
`nuauto test` runs every tests/test_*.py (4 at a time): offline, no sheet, no real NUworks, no network
(browser tests use headless Firefox on local pages or demo mode). Run it before pushing; CI runs it on
Ubuntu and macOS. tests/test_sync.py fails if a new module or prompt isn't in sync.CODE (so it would never
reach the homelab). test_web.py covers the public Mark-done links (signatures; GET never
changes the sheet). test_browser.py covers the domain lock and the profile lock. test_assist.py covers the
company-site agent's guard. test_demo.py runs the real apply.py on demo mode's fake pages (stops, cap, re-login).
test_gui.py covers the GUI server's security and flows; test_setup.py the wizard; test_health.py the checks;
test_window.py the packaged-app plumbing (self_cmd, the assistant's command, the window choice, clean env).

ENVIRONMENT / STYLE
- Python 3.12 venv in .venv with the package installed editable (`.venv/bin/pip install -e .`); dependencies
  in pyproject.toml: playwright (Firefox), gspread, scikit-learn (ranking model), pypdf (resume text).
  Fedora laptop, editor nvim.
- Use Sonnet by default; Opus only for hard bugs.
- Keep explanations short and simple. Give me commands I can run.
- Use nvim in any command that opens a file for editing.
