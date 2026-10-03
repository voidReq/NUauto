PROJECT HANDOFF: NUWorks application helper
Project dir: ~/projects/auto (plain directory, not a git repo)

GOAL
A controlled helper that tracks jobs in a Google Sheet and fills out NUWorks
applications in the browser. The human (me) decides which jobs to apply to and
approves jobs by marking them Approved in the sheet; that is the go-ahead (changed
2026-10-03: no per-job y/n at submit time). This is NOT a mass-apply bot.

HARD LIMITS
- Under 100 applications total.
- Weekly cap, enforced in code (count "Applied" rows with a date in the last 7
  days; refuse to run past the limit).
  DECISION (2026-10-03): sheet.py uses 11/week and 99 total (MAX_PER_WEEK, MAX_TOTAL).
  Approved rows beyond the cap just wait for the next week.
- One application at a time.
- I should check NUWorks' terms of use before running against the real site.

ENVIRONMENT
- Fedora Linux. Editor: nvim.
- Python with a venv in the project dir (.venv).
- Libraries: playwright (Firefox), gspread, scikit-learn (ranking model), pypdf (resume text).
- Use Sonnet by default; Opus only for hard bugs.

GOOGLE SHEETS ACCESS (gspread + OAuth)
- Service account keys are blocked by my Google Cloud org policy
  (iam.disableServiceAccountKeyCreation). Do NOT use service accounts.
- Use OAuth instead: gspread.oauth(...).
- Google Cloud project: "My First Project" under org you-org.
  Sheets API enabled, OAuth consent screen set to External (Testing) with
  you@example.com as the only test user (Internal rejected it: that
  Gmail account is not in the org directory). Testing-mode tokens expire
  after 7 days, so re-run the login weekly. OAuth client is type "Desktop app".
- The OAuth client JSON is client_secret.json in the project dir (moved from ~/Downloads on
  2026-10-03; mode 600). The homelab has its own copy in its project dir.
- Store the login token at ~/projects/auto/token.json, chmod 600.
- Never print, log, or copy the contents of the client JSON or token.json.
- Open the sheet with open_by_key(SHEET_ID) so only the Sheets API is needed.
  SHEET_ID: YOUR_GOOGLE_SHEET_ID

SHEET SETUP
- The sheet starts blank. Write a one-time setup script that adds the
  headers, the Status dropdown (data validation), and freezes row 1.
- It must not overwrite anything if the sheet already has data.

SHEET LAYOUT
Columns (row 1): URL, Company, Title, Status, Notes, Date
Status values (dropdown): Proposed, Approved, Applied, Failed, Needs Human
- Proposed: job added by me (or suggested), not approved yet.
- Approved: I approved it. The script only ever acts on these rows.
- Applied: submitted. Fill in Date.
- Failed / Needs Human: script stopped. Put the reason in Notes.

BROWSER (Playwright)
- Deterministic script, not an AI clicking around freely.
- I log in to NUWorks manually once. Use a separate persistent Playwright
  browser profile stored in the project dir and reuse the session.
- Never handle my password in code or prompts.
- Domain lock: only visit NUWorks and the application pages it links to.
  Anything else = stop and mark Needs Human.
  DECISION: the lock allows only northeastern-csm.symplicity.com. Jobs whose
  Apply popup links to an external site (Workday, iCIMS, Greenhouse...) are
  marked Needs Human; the human applies there. Exception: during the one-click
  re-login only, SSO_HOSTS in config.py are allowed (never typing credentials).
- Firefox drops session cookies on close, so browser.py saves cookies to
  session_cookies.json (chmod 600, never print it) and reloads them.
  NUworks sessions last only a few hours; apply.py re-logs in with one click
  when the SSO session is still alive, else says to run `browser.py login`.
- Slow, human-like pacing with delays between actions.

SAFETY RULES (most important)
1. No guessing, ever. If unsure about anything, stop and ask me.
2. Only act on Approved rows.
3. Field filling uses two local files:
   - profile.json: basic info (name, email, phone, address, school, etc.)
   - answers.json: answer bank (see below)
4. Any field not covered by those files = stop, ask me in the terminal.
5. Never write free text (essays, cover letters, "why do you want to work
   here", etc.). Those always stop and wait for me.
6. Uploads: only one fixed resume file path that I choose. Nothing else.
   DECISION: NUworks has no upload; its Apply popup picks from resumes already
   in my profile. The script selects only the exact "resume_label" saved in
   profile.json and never uploads anything.
7. (Changed 2026-10-03.) `nuworks apply` submits every Approved row, one at a
   time, with no per-job prompt: Approved in the sheet IS my approval. It must
   run in a real terminal (never from a background agent). A dry run (fill,
   screenshot, stop) exists only as the hidden --dry-run dev flag.
8. (Changed 2026-10-03.) No y/n before submit. Every filled form is still
   screenshotted to logs/ before Submit, and after-submit confirmation is checked.
9. Log every action and save every screenshot to a logs/ folder, per
   application.
10. Ctrl+C must stop everything cleanly and leave the sheet in a correct
    state.

ANSWER BANK (answers.json)
- Each entry: question text, my answer, field type, date added, and an
  optional always_ask flag.
- Matching: lowercase + trim the field label, then EXACT match only.
  No fuzzy matching.
- No match: stop, ask me in the terminal, save my answer, reuse it next time.
- If wording is slightly different, ask me again; I can then mark it as an
  alias of an existing question.
- Dropdowns/radios: only select an option whose text exactly matches my saved
  answer. If not present, ask me.
- always_ask entries (e.g., salary, work authorization, demographic
  questions) are never auto-filled.
- Starter questions to prefill: name, preferred name, email, phone, address,
  school, major, expected graduation date, GPA, work authorization,
  sponsorship needed, available start/end dates, hours per week, co-op term,
  relocation, commute, LinkedIn, GitHub, portfolio, how did you hear about
  us, over 18, OK with background check.

BUILD ORDER
1. OAuth test: connect to the sheet and read it.
2. One-time sheet setup script (headers, dropdown, frozen row 1).
3. Sheet module: read Approved rows, update Status/Notes/Date, enforce the
   weekly limit.
4. Playwright: persistent profile, manual login, open one job page.
5. Dry-run form filling on one job using profile.json + answers.json.
   Screenshot and stop.
6. Stop-on-unknown-field logic and the terminal "ask me and save" flow.
7. Confirm-then-submit with the --submit flag.
8. A few real runs with me watching.

STATUS (updated 2026-10-03)
Done: steps 1-8. Real automated runs: several applied (NUworks confirmed);
Some external (Needs Human), one applied by hand.
Tested: approve/apply, deadline + rank ordering, homelab update + sync, Discord reminders, signed
Mark links (GET confirm page), NUworks "applied" detection.
NOT yet seen for real: homelab Claude scoring a batch of new jobs (first chance: next 18:00 run),
pressing a Mark link (POST), a SITE_MARK row (NUworks-submitted job that also wants the company site),
an Apply popup with questions beyond Resume (answer bank still empty).
Google OAuth app stays in Testing mode (my choice) -> re-login weekly with `nuworks login google`.

JOB POOL (jobs.py, rebuilt 2026-10-02; rules = RULES in jobs.py)
Data comes from NUworks' own search/job data, not page text. Pipeline:
list (server filters: Co-op, Spring 2027, Available, 4/6 month, not applied; plus
co-ops with no term) -> Claude triage (Sonnet subagents, TRIAGE_PROMPT.md; drops
only clearly unrelated) -> details + hard rules -> Claude match % vs my resume
(Sonnet subagents, SCORE_PROMPT.md, fixed formula) -> category (CATEGORY_PROMPT.md)
-> pool. Pool on 2026-10-02: 153 jobs, all with real match >= 60%.
My bounds (decided 2026-10-02):
- Co-op, or Internship only if explicitly Spring 2027. Term unclear = keep + flag.
- 4 or 6 month. Undergrad must be allowed.
- Class level: I'm a SOPHOMORE (NUworks profile says Junior; ignore that).
  Lists Sophomore or no level -> need 65%. Junior and up -> need 90%.
  Senior/grad only -> drop.
- Anywhere in the US; Massachusetts jobs get +10 for ranking (not for entry).
- Targeted majors without ECE/College of Engineering -> flag, not drop.
- Citizenship/clearance: no filter. External applications: keep.
- Priorities (2026-10-02): cybersecurity is top. Each scored job gets a category
  (CATEGORY_PROMPT.md). Security roles need 60% (junior-only still 90%).
  Pool entry uses the RAW resume match (Boston +10 is ranking only).
  Ranking = match + Boston + category bonus: security +20, low-level (embedded,
  hardware, systems, robotics/controls/test) +10. Full-stack/web/front-end roles
  stay in the pool but always rank last.
  Extra ranking boosts (past job experience): AR/XR/smart glasses +5, wearables
  (incl. medical) +3 (not stacked with AR), embedded +5 more (so +15 total).
  (Scaled down 2026-10-02 so real fit matters more: need to pass technical interviews.)
- Scorer also reads class-year text (class_req): junior+ -> 90%, senior/grad -> drop.
  Flags shown, never auto-dropped: not your major, prior experience required,
  mentions graduation 2027/2028, title says other term, no first-time co-ops?,
  vague posting. Description sent to scorer is capped at 15000 chars.
- Resume: ~/Documents/resume.pdf (only file there).
  Updated 2026-10-02 16:44 (minor: past job title/description). Jobs were scored
  against the 10:03 version; not re-scored since the change is minor. New jobs use
  the new one (work/resume.txt is regenerated on every export).
- My y/n ratings (jobs.py rate, data/ratings.json) train a TF-IDF model that
  reorders the pool (half rank, half my taste) once I have 5 yes + 5 no.
  Rating order rotates categories (security, embedded, hardware, systems,
  robotics/test, software, data/ML, IT, other, full-stack) so every category gets
  rated; once the model trains, it also mixes in the jobs it is least sure about.
  Viewer keys: j/k scroll, space/b page, g/G top/bottom, y/n rate (overwrites an
  earlier answer), s skip, u back one job, o open in browser, q quit.

FILES
config.py (paths, hosts)  sheet.py (sheet rows, limits, status updates)
setup_sheet.py (one-time)  oauth_test.py  browser.py (login/open, lock, cookies)
apply.py (the runner)  inspect_form.py (read-only form lister)
profile.json (resume_label)  answers.py / answers.json (answer bank, chmod 600)
jobs.py (pool pipeline + viewers)  daily.py (update + reminders)  nuworks (the CLI)
sync.py (laptop<->homelab)  web.py (Mark-done links, homelab)
TRIAGE_PROMPT.md  SCORE_PROMPT.md  CATEGORY_PROMPT.md
data/ (list, triage, details/, scores, categories, ratings, pool)  work/ (subagent batches, resume.txt)
~/.config/systemd/user/nuworks-daily.{service,timer}
test_sheet.py / test_apply.py / test_answers.py / test_jobs.py (offline checks)
Secrets, never print: token.json, session_cookies.json, client_secret.json, discord_webhook.txt, web_secret.txt

COMMANDS (from ~/projects/auto, after `source .venv/bin/activate`)
nuworks approve                  # viewer on best unrated jobs: y = Approved in the sheet, n = no, s skip
nuworks apply [-n 3]             # submit every Approved row: closing within 7 days first, then best match (30-60s between), real terminal only
                                 # (approve shows jobs closing within 7 days first; past-deadline rows -> Needs Human;
                                 #  daily run notifies about pool jobs closing within 3 days that aren't in the sheet)
nuworks rate                     # taste training viewer (keys above)
nuworks update                   # check NUworks for new jobs now (same as the daily timer run)
nuworks status                   # Approved rows + weekly count
nuworks login                    # manual SSO login; close the window when done
                                 # (nuworks = ~/.local/bin/nuworks -> project dir; no venv activation needed)
python answers.py list           # answer bank; edit with: nvim answers.json
python jobs.py stats              # pipeline counts; see jobs.py docstring for all steps
python jobs.py pool               # show the pool
python jobs.py suggest 5          # top 5, asks before adding as Proposed
python test_sheet.py && python test_apply.py && python test_answers.py && python test_jobs.py
Google login lasts 7 days (Testing mode, staying that way): `nuworks login google` on the laptop. Discord warns the
day before (google_login.txt holds the login date); laptop commands also re-open the Google login by themselves when expired.
HOMELAB (since 2026-10-03): `ssh homelab` (Ubuntu, Tailscale), project at /home/you/projects/auto,
venv via uv (Python 3.12), claude CLI logged in. It runs the update at 08:00 + 18:00 New York time
(nuworks-daily.timer) and a Sunday 19:00 Discord check-in (nuworks-weekly.timer); the morning run also posts
deadline reminders. Discord webhook URL: discord_webhook.txt (secret, 600). The laptop timer is DISABLED.
Sync (sync.py, automatic in `nuworks`): laptop pulls data/ from the homelab; pushes ratings.json, code,
resume (-> resume.pdf there), token.json (if newer). `nuworks login` on the laptop also copies the NUworks
session to the homelab. Code changes reach the homelab on the next nuworks command (or: python -c "import sync; sync.push()").
Each homelab run also asks NUworks about jobs that left the list: ones you applied to by hand get marked
Applied in the sheet (or added). Morning Discord message lists rows only you can finish (company-site
applications owed = Notes starting "ALSO APPLY ON COMPANY SITE"; external Needs Human rows) with signed
"Mark done"/"Mark applied" links -> web.py on the homelab (nuworks-web.service, Tailscale IP :8765, secret
web_secret.txt), published as https://nuworks.example.org by the PI's cloudflared (ingress rule added to
/etc/cloudflared/config.yml on the pi; backup config.yml.bak-*). GET = confirm page only; POST changes the sheet.
OLD laptop daily timer notes: systemd user timer nuworks-daily.timer, 14:00 (+0-20 min), runs daily.py
(hidden browser; claude -p Sonnet with Read/Write only; desktop notification of new pool jobs).
  systemctl --user list-timers nuworks-daily.timer   # next run
  systemctl --user disable --now nuworks-daily.timer # turn off
  logs/daily-*.txt                                   # what each run did

NUWORKS LOGIN / AUTH FLOW (read this before touching the browser)
- NUworks (northeastern-csm.symplicity.com) sessions expire after a few hours.
  Expired = any page shows the "Sign In / Please select a role" screen
  (page title "Students: Sign-in method").
- Northeastern's SSO session lives much longer. So usually re-login is ONE click:
  click "Current Students And Alumni". It hops through
  shibboleth-northeastern-csm.symplicity.com and neuidmsso.neu.edu and lands back
  on NUworks logged in, with NO password or Duo prompt. Do that first; don't
  stop and ask me just because the sign-in screen appeared.
- Only if that click ends on a page asking for a username/password (or Duo),
  stop and ask me to log in. Never type, store or ask for my password.
  In this project: python browser.py login (I log in, then close the window).
- Our scripts do the one click automatically (browser.relogin / goto_logged_in,
  used by apply.py, jobs.py, inspect_form.py, daily.py) and allow the two SSO
  hosts only during that click (SSO_HOSTS in config.py).
- "Local Login - Not Supported" on that screen is not for us; never use it.
- A separate browser profile (another agent's) starts with no SSO session, so the
  first login there needs me once; after that the one click works there too.

OTHER SESSIONS (e.g. setting up my NUworks profile with Playwright)
- Only ONE Playwright session may use browser_profile/ at a time (Firefox locks it).
  Don't use it while the daily timer runs (~14:00-14:45), or while apply.py,
  jobs.py or browser.py are running. Use a separate profile dir if in doubt.
- Never type or store my password; I log in myself.
- If the NUworks resume is replaced or renamed, update profile.json "resume_label"
  to the EXACT new text in the Apply popup's Resume dropdown (currently
  "Doe, Jane Resume | S27 v5" since 2026-10-03). Until then apply.py stops safely.
- The resume uploaded to NUworks should be the one in ~/Documents/.
- NUworks profile class level says Junior; I'm a sophomore. Fixing it there is my
  call. jobs.py ignores the profile value either way.
- Profile changes can change NUworks' own "Jobs I qualify for" screening; the job
  pool doesn't depend on it.

STYLE
- Keep explanations short and simple. Give me commands I can run.
- Use nvim in any command that opens a file for editing.
