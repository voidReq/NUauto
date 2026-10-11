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
  Code gets there by itself: with "deploy_from_git" (mine, once turned on) the homelab deploys GitHub's main every
  5 minutes (deploy.py, nuauto-deploy.timer); otherwise sync.py copies the laptop's checkout. Never edit it on the
  homelab. Details: docs/DEPLOY.md.

GIT
- This folder is a git repo (since 2026-10-03). Remote: github.com/voidReq/NUauto, PUBLIC (since
  2026-10-04). main only receives merges: work on a branch, merge when I OK it, push when I OK it.
  Before any push: no personal info in tracked files or commit messages (names, emails, hosts,
  IPs, IDs, companies I applied to); those go in local_config.json or docs/STATUS.md.
- After every merge (and at the start of a session): bring the laptop checkout up to date (`git fetch --prune`,
  then on main `git pull --ff-only`; never over uncommitted changes: ask me), then clear stale branches: delete
  local and remote branches already merged into main (`git branch -d`, `git push origin --delete`) and remove
  their worktrees. Never delete a branch that is not merged, or main.
- Gitignored: local/ (all personal files and secrets), docs/STATUS.md, data/, work/, logs/, .venv/.
- Prod = whatever is on main: merging to main deploys it to the homelab within 5 minutes (deploy_from_git; web is
  restarted when needed). Without that setting, whatever is checked out on the laptop is what the next `nuauto`
  command pushes to the homelab.

HARD LIMITS
- Under 100 applications total.
- Weekly cap, enforced in code (count "Applied" rows with a date in the current week; refuse to
  run past the limit). sheet.py: MAX_PER_WEEK = 11 is the default, changeable in the GUI (Settings > Applications per week, local_config.json "max_per_week", 1-30; sheet.max_per_week()), MAX_TOTAL = 99 (decided 2026-10-03). A week =
  fixed 7-day periods from local_config.json "week_start" (sheet.week_window); without it, the last
  7 days. Mine: week_start 2026-10-06 (reset because I started applying late), then every 7 days.
  Approved rows beyond the cap just wait for the next week.
  Both caps count the main (NUworks) tab only: the Other jobs tab has no cap (my call, 2026-10-07). Summer
  internships (Simplify's list, intern.py) go in the Other jobs tab: no weekly or total cap either; Approved ones
  can be applied to as much as I want (my call, 2026-10-10).
- One application at a time on NUworks (apply.py: one browser, one row after another). Company sites: up to 3 assistant
  sessions side by side (my call, 2026-10-10; local_config.json "assist_slots" 1-3), each in its own terminal window and
  browser profile, one per row and one per job site at a time (COMPANY-SITE AGENT).
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
- always_ask entries (e.g., salary, work authorization) are never auto-filled.
  (Changed 2026-10-09, my call.) Demographic / voluntary self-identify questions (gender, race, Hispanic/Latino,
  veteran, disability) are saved and reused like any answer; the agent no longer asks on every such page.
- (Changed 2026-10-06, my call: "I'd rather agents infer based on answers".) The rules above are for the
  NUworks runner (apply.py). The company-site agent (`nuauto assist`) may work an unmatched field out from
  my saved answers (other wording, or a direct part like city from address; a dropdown option that means
  the saved answer), says what it used, and aliases the label so the next run matches exactly. Never from
  always_ask or leave-blank entries; anything my answers don't cover, it asks. I review before Submit.
- (Added 2026-10-07.) Jobs in the sheet's Other jobs tab use a second bank, local/answers_other.json, checked
  first; then answers.json minus its NUworks-only entries (answers.NUWORKS_ONLY: available start/end date,
  co-op term; an entry's own "nuworks_only" true/false wins, GUI Answers checkbox). Everything the agent saves
  in such a run goes to answers_other.json, never answers.json; an alias goes on the entry it names
  (assist.Bank). always_ask entries in answers.json stay always-ask there.
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
- The first tab is NUworks only (sheet.open_worksheet refuses if "Other jobs" is first). A second tab,
  "Other jobs" (sheet.OTHER_TAB, added 2026-10-07), holds jobs that are not on NUworks: same columns and
  statuses, made by sheet.open_other(create=True) the first time I add one (GUI Sheet screen, or
  `nuauto sheet add other` / `nuauto assist other add`; added rows are Approved), styled by `nuauto setup-sheet format` / `other`.
  Only `nuauto assist other` acts on it (Approved rows only); no weekly or total cap. Approving a Summer internship
  in Review (or `nuauto intern approve`) adds an Approved row there (intern.approve; Undo = back to Proposed).
- By hand (manage.py, added 2026-10-09; GUI Sheet screen, `nuauto sheet add|move|remove`): add a job to either tab
  (Approved, or Proposed; the NUworks tab takes NUworks job links only and fills company, title, match from data/),
  move a row to the other tab (status, notes, date go with it; a new link if needed), remove one. Rows are never
  deleted, only cleared, so no row number shifts under a run or a Mark link. Applied rows are never removed and never
  leave the NUworks tab (they count toward the caps); an Applied Other job may move to NUworks (then it counts
  there; my call 2026-10-09). Unresolved Submit clicks are never moved or removed.
Google access is OAuth only, never service accounts (docs/DEPLOY.md, GOOGLE SHEETS LOGIN). Google allows about 60 reads a
minute: every client waits and retries on "too many requests" (gspread.BackOffHTTPClient), and the internship Review reuses
the Other jobs rows for 30 s (each change NUauto makes forgets them).

BROWSER (Playwright, Firefox)
- Deterministic script, not an AI clicking around freely. Slow, human-like pacing.
  (Exception: company sites, with me watching: `nuauto assist`, below; it may also upload the resume
  and type longer answers I approved, never submit without my review.)
- Persistent browser profile in local/browser_profile/; I log in by hand (`nuauto login`) and the
  session is reused. Never handle my password in code or prompts. (The one exception: the random per-site
  passwords NUauto makes for job-site accounts, COMPANY-SITE AGENT below; never my own accounts' passwords.)
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
  can be read or sent) or `nuauto gui --view` (added 2026-10-09 on my request, so an agent can see my real screens
  to test: my real sheet and job data, READ-ONLY. gui.VIEW: the server refuses every POST but Quit, no child
  process can start, only the sheet and file checks run, no window, no lock file, no homelab sync; my saved
  answers and run logs / screenshots are hidden. Start it with --no-open --port 0 and open the printed URL in
  Playwright). Never start the real GUI (no --view) or click Start in one; it refuses to start inside a Claude Code
  shell anyway. Screens for review: `nuauto gui --demo --screenshots DIR`.
- Setup wizard (onboard.py): each step checks itself; preferences go in local_config.json "preferences"
  (jobs.DEFAULTS = my original settings). Health checks (health.py) never open a login page.
- Its own window (window.py): macOS pywebview; Linux the system python3's GTK + WebKitGTK (window_gtk.py); else a
  Chrome app window or a tab. Closing it quits NUauto. `nuauto _window` says which.
- Packaged app (packaging/, added 2026-10-05 on my request: "for the users local, preferably executable"): AppImage
  (Linux) and DMG (macOS) from PyInstaller, built + smoke-tested by .github/workflows/release.yml on every PR (the Intel Mac one only on
  v* tags and Run workflow), published on v* tags. Same code
  (config.FROZEN): files in the app-data folder, local mode only, runs itself via config.self_cmd / self_exe (never
  `python -m` or file paths in code that may run packaged). Build locally: sh packaging/build.sh; check: packaging/smoke.py;
  other distros: sh packaging/distros.sh <AppImage> (podman).
- Not yet proven for real: a GUI apply with me watching, the logins through the GUI, the NUworks reads
  (resume labels, term list), a Mac app opened by a person (CI builds both Mac apps and runs the tests and their
  self-test there, in demo mode).

COMPANY-SITE AGENT (assist.py + prompts/ASSIST_PROMPT.md, `nuauto assist <row>`; added 2026-10-05)
- Only for Needs Human rows stopped at an external application (Notes start "External application";
  Workday, Oracle, iCIMS, SuccessFactors...). Never NUworks itself (refused even with --url), never rows
  stopped for something on NUworks (cover letter, transcript...): those are mine (assist.assist_target).
- Laptop, real terminal, me watching. Starts `claude --model sonnet --effort low --permission-mode default` (never my auto
  mode: its own check asked about every click; the guard decides; local_config.json "assist_effort":
  low / medium / high; Settings; low is my call, 2026-10-10) with the Playwright MCP browser (profile assist_profile/:
  it holds my company-site logins, treat it as secret), Bash, Read/Glob/Grep and WebSearch/WebFetch (company context,
  my call 2026-10-10).
- Relaxed 2026-10-05 (my call): the agent may visit any site, fill anything, tick boxes, upload the
  resume, and draft longer answers that I approve before it types them. The one hard rule: NOTHING is
  submitted without my review. Enforced by code (Claude Code hooks -> `assist.py hook pre|post`, logic
  in assist.decide / update_after, tested in tests/test_assist.py): every Submit-type click (SUBMIT_RE, incl.
  "Apply"), Enter and type(submit) make the terminal ask me first ("ask"); element names come from the
  latest full snapshot only (refs cleared by anything that changes the page), never from the agent's
  description. Never: page scripts (could submit behind the review), uploads other than
  the resume (the launcher copies it into the run's log folder, logs/<run>/upload/, because the browser tool
  only uploads from there; only that copy is allowed), non-web links; Bash only the answer-bank command (answer|save|once|alias|blank|wait);
  Read/Glob/Grep only inside my resume and local_config.json "assist_read_paths" (my projects and writeups: mine is
  ~/projects, 2026-10-10), and never NUauto's local/ folders or logs, keys, tokens, .env or .git files in them
  (assist.secret_path; Grep may not search a folder holding a local/). A guard that fails or times out (60 s) blocks
  the action (hooks' "onFailure": "block").
- Accounts (added 2026-10-10, my call): the agent makes the accounts job sites ask for (Workday...), with
  local_config.json "accounts_email" (mine: my Gmail, in local_config only) and a random password per site that NUauto
  makes (accounts.py; local/accounts.json, 600, under a lock, never synced). It types {{NEW_PASSWORD}} / {{PASSWORD}}
  alone into a field the latest snapshot names a password; the guard types the real one (updatedInput) and takes it
  out of the browser tool's replies and snapshots (updatedToolOutput: "<password hidden by NUauto>"), NUauto's run
  logs and, after the session, Claude Code's session log. Never on Google / Microsoft / Apple / LinkedIn / GitHub or
  Northeastern sign-ins (accounts.never). `nuauto accounts export` (or Settings) writes Bitwarden's import file (one
  login per site, matched on its host; the jobs in its notes). Proven end to end with a real session on a local page.
- Sign-in pages (added 2026-10-10, my call): a sign-in / create-account page's own "Submit" doesn't ask (the latest
  snapshot has a password box and only account boxes: assist.sign_in_page); any question on the page, or "Apply" /
  "Submit Application", still asks. The agent never types into a bot trap ("for robots only"). Also my call
  (2026-10-10): the job's own posting's "Apply" / "Apply now", and Enter there or on a sign-in page, don't ask
  (assist.posting_page: the row's link, nothing filled in yet, no form boxes); the catch I accepted: a site with
  one-click apply from a saved profile could submit there. On a Workday site, Enter in a box doesn't ask either (its
  pages don't submit on Enter; the final Submit is a button), my call 2026-10-10. Enter anywhere else asks (one-page
  forms submit on Enter).
  Buttons with no name but their text (`button: Submit`, Workday) are checked by that text.
- Mine: email codes and verification links, captchas, approving Submit. After I /exit, the launcher asks "did you submit?"; y =
  Applied (dated today, counts toward the weekly limit), then the same job is submitted on NUworks too by
  apply.submit_nuworks_side (the tested NUworks code; every popup check except the off-site-link stop;
  outcome appended to Notes; retry: `nuauto assist nuworks <row>`). The agent never touches the sheet.
  Log per run: logs/<stamp>_assist_row<N>/ (actions.log = every guard decision).
- Side by side (added 2026-10-10, my call): up to 3 sessions at once (assist.slots). Each takes a slot (lock
  local/assist_slot<N>.lock with what runs there; slot 1 = assist_profile/, the others assist_profile_<N>/), a row lock
  and a job-site lock (one session per site: two could make the same account at once); all held until its run ends.
  Answer-bank commands and the Answers screen's save hold local/answers.lock (no answer lost between sessions). The
  GUI's Company sites shows which rows have one running (assist.sessions).
- Other jobs (added 2026-10-07): `nuauto assist other <row>` runs the same agent, rules and hooks on an Approved
  row of the Other jobs tab (its URL column; https, never NUworks: assist.other_target), with the layered answer
  bank (ANSWER BANK). After /exit, y = Applied (dated today, no cap), and nothing is sent to NUworks; n = stays
  Approved. Log: logs/<stamp>_assist_other_row<N>/. GUI: the Other jobs card in Apply > Company sites (Start
  assistant, I applied myself; Add a job opens the Sheet screen; since 2026-10-09 no screen of its own). Not yet run for real.
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
  browser.py (login/open, domain lock, cookies)   answers.py (answer bank)   jobs.py (pool + viewers)   fields.py (what you study: per-field starting preferences, kinds of work)
  daily.py (homelab update + Discord)   web.py (Mark-done page, homelab)   sync.py (laptop<->homelab)   deploy.py (homelab: deploys main)
  doctor.py (health check)   inspect_form.py (read-only form lister)   assist.py (company-site agent)   accounts.py (its job-site logins)
  setup_sheet.py (sheet setup / `format` restyle)   gui.py (the window's server)   gui_static/ (its page)
  health.py (the checks behind doctor and the GUI)   onboard.py (setup wizard)   demo.py (demo mode fakes)
  window.py (which window) + window_gtk.py (the Linux window, run by the system python3)
  insights.py (pay, places, kinds of work, sheet statuses: the GUI's Insights screen and `nuauto insights`)
  manage.py (add / move / remove sheet rows by hand: the GUI's Sheet screen and `nuauto sheet add|move|remove`)
  intern.py (Summer internships from Simplify's list: the second pool, docs/PIPELINE.md)   postings.py (reads a posting
  from a company's own job site, no login: job-site JSON, page data, hidden browser; never a non-public address)
  Imports are always absolute: `from nuauto import sheet` (test_sync enforces it).
prompts/ (TRIAGE_, SCORE_, CATEGORY_PROMPT.md: Claude batch prompts; INTERN_TRIAGE_, INTERN_SCORE_PROMPT.md: the
  internships'; ASSIST_PROMPT.md: agent rules)
tests/ (test_*.py offline checks)   docs/ (DEPLOY, PIPELINE, GUI, ARCHITECTURE, SAFETY, STATUS)
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
SECRETS (in local/), never print, log or copy their contents: token.json, session_cookies.json,
client_secret.json, discord_webhook.txt, web_secret.txt, accounts.json (job-site logins; only `nuauto accounts export`
writes them out, for my password manager)

COMMANDS (installed in .venv; no activation needed)
nuauto gui             # the window (setup, review, apply, health); --demo: everything fake; --view: my real data, read-only (agents: only these two)
nuauto approve         # viewer: my Proposed rows first, then best unrated jobs: y = Approved in the sheet, n = no, s skip
nuauto apply [-n 3] [--order match]  # submit every Approved row (30-60s between); real terminal (or the GUI) only
                       # --order: default (closing within a week, then score) | score | match | closes | pay
nuauto rate            # taste training viewer (keys in docs/PIPELINE.md)
nuauto update          # check NUworks for new jobs now (runs on the homelab, then syncs)
nuauto status          # Approved rows + weekly count (also pushes code to the homelab)
nuauto login           # manual SSO login; close the window when done
nuauto login google    # Google Sheets login, every 7 days (Discord warns the day before)
nuauto test            # all offline tests
nuauto doctor [--json] # health check: laptop, homelab, Mark-done page (read-only)
nuauto deploy          # homelab: deploy GitHub's main now (laptop: starts it there); the timer does it every 5 min
nuauto insights        # pay, places, kinds of work in my pool and applications; sheet statuses (read-only)
nuauto selftest        # is this install OK? demo mode end to end in a hidden browser (nothing real touched)
nuauto assist [<row>]  # company-site agent for a Needs Human row; asks me before any Submit
nuauto accounts [export [<file>] [--all]]   # the job-site logins the agent made (no passwords shown); Bitwarden's import file
nuauto assist other [<row>]                      # same agent for the sheet's Other jobs tab (no NUworks step after)
nuauto assist other add <url> <company> <title>  # add a job there as Approved
nuauto sheet add <nuworks|other> <url> [<company> <title>] [--proposed]   # add a job by hand
nuauto sheet move <nuworks|other> <row> [<new url>]  # to the other tab;  nuauto sheet remove <nuworks|other> <row>
nuauto answers list    # answer bank; edit with: nvim local/answers.json
nuauto setup-sheet format          # restyle the sheet (formatting only, safe to re-run)
nuauto jobs stats | pool | suggest 5
nuauto intern update [--here] | approve | rate | stats | pool   # Summer internships (Simplify's list); approve -> Other jobs tab
nuauto intern fetch <url> [--browser]                 # read one posting from its job site now (nothing saved)
nuauto daily [weekly] / nuauto web # what the homelab's timers / Mark-done service run
python -m nuauto.<module> ...      # any module's own command line (same as the nuauto tools)

TESTS
`nuauto test` runs every tests/test_*.py (4 at a time): offline, no sheet, no real NUworks, no network
(browser tests use headless Firefox on local pages or demo mode). Run it before pushing; CI runs it on
Ubuntu and macOS. tests/test_sync.py fails if a new module or prompt isn't in sync.CODE (so it would never
reach the homelab). test_web.py covers the public Mark-done links (signatures; GET never
changes the sheet). test_browser.py covers the domain lock and the profile lock. test_assist.py covers the
company-site agent's guard. test_insights.py covers the Insights numbers (pay parsing, folding, no guessing). test_intern.py covers the
internship pool and postings.py (job-site links, parsing, the network guard on a local server, fetch tries, rules,
Review queue, a whole update with Claude stood in for). test_deploy.py runs deploy.py on a throwaway git repo (copy, web restart only when needed, undo on failure). test_demo.py runs the real apply.py on demo mode's fake pages (stops, cap, re-login).
test_gui.py covers the GUI server's security and flows; test_view.py the read-only `--view` mode; test_setup.py the wizard; test_health.py the checks;
test_window.py the packaged-app plumbing (self_cmd, the assistant's command, the window choice, clean env).

ENVIRONMENT / STYLE
- Python 3.12 venv in .venv with the package installed editable (`.venv/bin/pip install -e .`); dependencies
  in pyproject.toml: playwright (Firefox), gspread, scikit-learn (ranking model), pypdf (resume text).
  Fedora laptop, editor nvim.
- Use Sonnet by default; Opus only for hard bugs.
- Keep explanations short and simple. Give me commands I can run.
- Use nvim in any command that opens a file for editing.
