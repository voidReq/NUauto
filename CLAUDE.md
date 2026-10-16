NUAUTO (NUworks application helper)
Tracks co-op jobs in a Google Sheet and fills out NUworks applications in the browser.
I (the human) decide which jobs to apply to by marking them Approved in the sheet; that is
the go-ahead (since 2026-10-03 there is no per-job y/n at submit time). This is NOT a
mass-apply bot.

This file has the rules. The details live in docs/ and are NOT loaded by themselves (they were ~140k
characters): before working on an area, read its doc; when you change that area, update it.
- docs/DEPLOY.md (modes, machines, the homelab = prod, Mark-done page, sync, secrets, Google login, health checks, rebuild steps)
- docs/PIPELINE.md (how the job pool is built, my bounds, ranking, ratings, Summer internships)
- docs/GUI.md (the window `nuauto gui`: screens, setup wizard, health checks, safety model, demo mode, API)
- docs/REMOTE.md (NUauto on the homelab: its page, the assistant there, queue, claims, what both machines share)
- docs/ASSIST.md (the company-site agent: every rule; read it before touching assist.py, accounts.py, mail.py or
  prompts/ASSIST_PROMPT.md)
- docs/SAFETY.md (every guardrail, where the code enforces it, which test covers it)
- docs/ARCHITECTURE.md (modules, who calls what, files on disk, which test checks what)
- docs/STATUS.md (what is done, what is not yet proven for real, dated decisions; my local log,
  gitignored: keep job/company names and other personal history there, never in tracked files)

WHERE AM I
- Laptop (Fedora, ~/projects/auto): code is edited here. Interactive commands run here.
- Homelab (`ssh homelab`; hostname and path in local_config.json) = PROD. Runs the
  scheduled update, Discord messages and the Mark-done page. config.IS_SERVER is True there. With "remote_assist"
  (Phase 3, 2026-10-11) also company-site assistant sessions, in tmux (docs/REMOTE.md). With "remote_app" (or
  remote_assist; 2026-10-14, my request: "1:1 with the app") nuauto-web also serves NUauto's page for my phone: the
  same app as `nuauto gui` (gui.serve_remote), Tailscale IP :8766, paired devices only; what needs the laptop shows as
  "On your laptop" there (docs/REMOTE.md, docs/GUI.md).
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
  internships (Simplify's and vanshb03's lists, intern.py) go in the Other jobs tab: no weekly or total cap either; Approved ones
  can be applied to as much as I want (my call, 2026-10-10).
- One application at a time on NUworks (apply.py: one browser, one row after another; and one machine at a time,
  since both can run Apply: remote.nuworks_turn, 2026-10-14). Company sites (the assistant): up to 3 assistant
  sessions side by side (my call, 2026-10-10; local_config.json "assist_slots" 1-3), each in its own terminal window and
  browser profile, one per row and one per job site at a time (COMPANY-SITE AGENT).
- I should check NUworks' terms of use before running against the real site.

SAFETY RULES (most important)
1. No guessing, ever. If unsure about anything, stop and ask me. (The one exception: the company-site agent's
   recorded guesses, which I answer all at once before Submit: COMPANY-SITE AGENT, 2026-10-11.)
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
   (Added 2026-10-12, my request.) The company-site agent may also upload my unofficial transcript (local_config.json
   "transcript_path", GUI Settings; the homelab gets a copy as local/transcript.pdf), only where a page asks for one.
7. (Changed 2026-10-03; GUI added 2026-10-05.) `nuauto apply` submits every Approved row, one at a
   time, with no per-job prompt: Approved in the sheet IS my approval. It runs only with me there:
   in a real terminal, or from the GUI (`nuauto gui`: I click Start, the browser is visible, Stop =
   Ctrl+C). (Added 2026-10-14, my call: "Allow it on the homelab".) Or from NUauto's page on the homelab: I tap Start
   on a paired device, the browser runs on a virtual screen I watch there (its link and password on the run's card),
   Stop = Ctrl+C. Never from an agent: code refuses real submits an agent's shell starts (CLAUDECODE: `nuauto apply`,
   `assist nuworks`), and `--ui json` works only as a child of the running GUI (on the homelab: nuauto-web).
   (Changed 2026-10-16, my call: "i don't really care if claude launches it".) An agent may open the real GUI for me
   (`nuauto gui`: its own window; --no-open from Claude Code is refused: it would print the agent the secret);
   I click Start there, never the agent. A dry run (fill, screenshot,
   stop) exists only as the hidden --dry-run dev flag.
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
  always_ask or leave-blank entries. Anything my answers don't cover, it guesses and records (since 2026-10-11:
  COMPANY-SITE AGENT), then asks me everything at once at the end. I review before Submit.
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
- Notes starting "Dropped <date>: <reason>" (sheet.drop, added 2026-10-12, my request): a Failed row I chose not to apply
  to (closed, a duplicate, a bad location): Drop in a row's More menu on the GUI's Apply screen (both machines' pages,
  every tab). Only Approved / Needs Human rows; the row stays as a record, so Review never re-offers it.
- Notes starting "ALSO APPLY ON COMPANY SITE" (SITE_MARK): submitted on NUworks, but the
  company also wants its own site; I do that part.
- The first tab is NUworks only (sheet.open_worksheet refuses if "Other jobs" is first). A second tab,
  "Other jobs" (sheet.OTHER_TAB, added 2026-10-07), holds jobs that are not on NUworks: same columns and
  statuses, made by sheet.open_other(create=True) the first time I add one (GUI Apply > Add a job, or
  `nuauto sheet add other` / `nuauto assist other add`; added rows are Approved), styled by `nuauto setup-sheet format` / `other`.
  Only `nuauto assist other` acts on it (Approved rows only); no weekly or total cap. Approving a Summer internship
  in Review (or `nuauto intern approve`) adds an Approved row there (intern.approve; Undo = back to Proposed).
- By hand (manage.py, added 2026-10-09; GUI Apply: Add a job and each row's More menu, since 2026-10-16 (the Sheet screen
  merged into Apply, my call); `nuauto sheet add|move|remove`): add a job to either tab
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

GUI (`nuauto gui`; details: docs/GUI.md)
- Agents test with `nuauto gui --demo` (everything fake: sheet, NUworks pages, Claude, temp folder) or `nuauto gui
  --view` (my real data, READ-ONLY, enforced by the server; --no-open --port 0, open the printed URL in Playwright). The
  real GUI: an agent may open its window for me (SAFETY RULES 7), never click Start or Apply in one. The homelab's page:
  `nuauto gui --demo --remote` (= `nuauto review demo`); the linked laptop: `nuauto gui --demo --linked`. Never use the
  real homelab page. Screens for review: `--screenshots DIR`.
- ONE APP, TWO PLACES (my request, 2026-10-15: "as 1:1 as possible; a change to one adds to the other"): the laptop's
  window and the homelab's page are the same gui.py + gui_static/; every change to them applies to both. Something that
  must differ goes behind state.where / gui.REMOTE (or "linked") and gets a check on each side: test_gui.py (laptop),
  test_homelab_app.py (homelab), test_link.py (the linked laptop). tests/screens.py opens every screen in all three.
- Packaged app (config.FROZEN): code that may run packaged runs NUauto via config.self_cmd / self_exe, never
  `python -m` or file paths.

COMPANY-SITE AGENT (`nuauto assist`; every rule: docs/ASSIST.md, read it before changing the agent)
- Only Needs Human rows stopped at an external application, and Approved Other jobs rows; never NUworks itself.
- The one hard rule: NOTHING is submitted without my review, enforced in code (Claude Code hooks -> assist.decide,
  tests/test_assist.py); a guard that fails or times out blocks. Captchas are mine (the guard refuses them). Passwords:
  only NUauto's {{NEW_PASSWORD}} / {{PASSWORD}} placeholders, never on Google / Microsoft / Apple / LinkedIn / GitHub /
  Northeastern sign-ins. Bash only the answer-bank commands; reads only inside allowed paths, never secrets.
- It guesses what my answers don't cover, records each guess, and asks me everything at once before Submit.

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

FILES: an installable package (`pip install -e .`), code in src/nuauto/; the map is docs/ARCHITECTURE.md.
- Imports are always absolute: `from nuauto import sheet` (test_sync enforces it). A new module or prompt goes in
  sync.PACKAGE / sync.CODE (test_sync fails otherwise).
- local/ (gitignored, mode 700): everything personal or secret, on both machines. Keep personal values (names,
  emails, hosts, IPs, IDs, companies) out of every committed file: the repo is public.
- Generated (gitignored, repo root): data/, logs/, work/

SECRETS (in local/), never print, log or copy their contents: token.json, session_cookies.json,
client_secret.json, discord_webhook.txt, web_secret.txt, accounts.json (job-site logins; only `nuauto accounts export`
writes them out, for my password manager), gmail_token.json (read-only Gmail login), homelab_link.json (the laptop
app's device key for the homelab's page)

COMMANDS (installed in .venv; no activation needed)
nuauto gui             # the window (setup, review, apply, health); --demo: everything fake; --view: my real data, read-only (agents: only these two)
nuauto approve         # viewer: my Proposed rows first, then best unrated jobs: y = Approved in the sheet, n = no, s skip
nuauto apply [-n 3] [--order match]  # submit every Approved row (30-60s between); real terminal (or the GUI) only
                       # --order: default (closing within a week, then score) | score | match | closes | pay
nuauto rate            # taste training viewer (keys in docs/PIPELINE.md)
nuauto update          # check NUworks for new jobs now (runs on the homelab, then syncs)
nuauto status          # Approved rows + weekly count (also pushes code to the homelab)
nuauto login           # manual SSO login; close the window when done
nuauto login google    # Google Sheets login, every 7 days (Discord warns the day before); renews the Gmail one too
nuauto login gmail     # read-only Gmail login for the agent's email codes (once; then renewed with login google)
nuauto test            # all offline tests
nuauto doctor [--json] # health check: laptop, homelab, Mark-done page (read-only)
nuauto deploy          # homelab: deploy GitHub's main now (laptop: starts it there); the timer does it every 5 min
nuauto insights        # pay, places, kinds of work in my pool and applications; sheet statuses (read-only)
nuauto selftest        # is this install OK? demo mode end to end in a hidden browser (nothing real touched)
nuauto assist [<row>]  # company-site agent for a Needs Human row; asks me before any Submit
nuauto accounts [export [<file>] [--all]]   # the job-site logins the agent made (no passwords shown); Bitwarden's import file
nuauto accounts import <file>|- --only <text>   # my own logins from Bitwarden for those sites (bw list items --search ...)
nuauto accounts export --bitwarden              # NUauto's new logins straight into my vault (bw unlocked by me; no file)
nuauto review pair [<name>] | devices | unpair <name>   # NUauto's page on the homelab: pair my phone (docs/REMOTE.md)
nuauto review demo                                      # that page with made-up sessions, locally (= nuauto gui --demo --remote; agents)
nuauto onboard homelab-setup                            # set a homelab up step by step (Settings > Homelab > Set it up opens it)
nuauto onboard homelab-check <ssh name> [<folder>]      # what that homelab has, read-only over ssh
nuauto assist queue | start <nuworks|other> <row>      # (homelab) the assistant's queue / start one row's session in tmux
nuauto assist other [<row>]                      # same agent for the sheet's Other jobs tab (no NUworks step after)
nuauto assist other add <url> <company> <title>  # add a job there as Approved
nuauto sheet add <nuworks|other> <url> [<company> <title>] [--proposed]   # add a job by hand
nuauto sheet move <nuworks|other> <row> [<new url>]  # to the other tab;  nuauto sheet remove <nuworks|other> <row>
nuauto answers list    # answer bank; edit with: nvim local/answers.json
nuauto setup-sheet format          # restyle the sheet (formatting only, safe to re-run)
nuauto jobs stats | pool | suggest 5
nuauto intern update [--here] | approve | rate | stats | pool   # Summer internships (Simplify's + vanshb03's lists); approve -> Other jobs tab
nuauto intern fetch <url> [--browser]                 # read one posting from its job site now (nothing saved)
nuauto daily [weekly] / nuauto web # what the homelab's timers / Mark-done service run
python -m nuauto.<module> ...      # any module's own command line (same as the nuauto tools)

TESTS
`nuauto test` runs every tests/test_*.py (4 at a time): offline, no sheet, no real NUworks, no network. Run it before
pushing; CI runs it on Ubuntu and macOS. Which test covers what: docs/ARCHITECTURE.md (TESTS), docs/SAFETY.md.

ENVIRONMENT / STYLE
- Python 3.12 venv in .venv with the package installed editable (`.venv/bin/pip install -e .`); dependencies
  in pyproject.toml: playwright (Firefox), gspread, scikit-learn (ranking model), pypdf (resume text).
  Fedora laptop, editor nvim.
- Use Sonnet by default; Opus only for hard bugs.
- Keep explanations short and simple. Give me commands I can run.
- Use nvim in any command that opens a file for editing.
