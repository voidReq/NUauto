DEPLOYMENT: laptop, optional homelab, optional public Mark-done page
(Keep this current when you change hosts, units, secrets or sync. NUauto's page and the assistant on the homelab: docs/REMOTE.md.)
File names below are in local/ (gitignored, mode 700) unless a folder is given: local_config.json
means local/local_config.json. The code is the nuauto package (src/nuauto/), installed editable in .venv.

MODES
- Local mode (default): server_hostname "" in local_config.json (config.HAS_SERVER False).
  Everything runs on one machine: `nuauto update` runs the update here, nothing syncs, no Mark links.
  Automatic updates: the GUI's setup (Notifications, automatic updates) installs a user timer at 08:00 and 18:00:
  systemd (~/.config/systemd/user/nuauto-daily.{timer,service}) or launchd (~/Library/LaunchAgents/com.nuauto.daily.plist)
  (onboard.scheduler_enable).
- The packaged app (AppImage / NUauto.app, docs/GUI.md) is always local mode (config.SERVER_HOSTNAME is forced to "");
  homelab mode needs a git checkout.
- Homelab mode: an always-on Linux box runs the update twice a day and sends Discord messages;
  the laptop syncs with it. Set in local_config.json:
    server_hostname  the homelab's hostname (that is how config.IS_SERVER knows it is the homelab)
    server_ssh       how the laptop reaches it with ssh (an ~/.ssh/config alias; default: the hostname)
    server_dir       the project folder under its home (default projects/auto). The units in deploy/systemd/ run
                     from ~/projects/auto; if yours differs, change them with `systemctl --user edit <unit>` (a
                     drop-in), not by editing the file: the deploy installs the repo's copy over it.
    web_listen_host  the homelab's Tailscale IP (never 0.0.0.0 or 127.0.0.1). Everything it serves listens only here:
                     the Mark-done page (:8765), NUauto's page (:8766) and the assistant's browser views (docs/REMOTE.md).
    web_base_url     where the Mark-done page is published (below); "" = no Mark links
    deploy_from_git  true = the homelab deploys GitHub's main by itself (DEPLOYING A CHANGE); the laptop stops pushing
                     code. deploy_repo: the https address of another fork (default: this project's GitHub repo).
    remote_app, remote_assist, remote_auto, remote_hours   NUauto's page and the assistant on the homelab (docs/REMOTE.md)
  The GUI sets all of these up (a source install): Settings > Homelab (Check, Use this homelab, Set it up, Stop using
  it; Advanced: deploy_from_git, deploy_repo, web_base_url; NUauto on your homelab: the remote_* keys), or setup's
  optional Homelab step. A change to the server fields takes a restart of NUauto.
  Below, `ssh homelab` means ssh to server_ssh.

MACHINES (homelab mode)
- Laptop. Where code is edited; the git repo. Runs everything interactive: the GUI (`nuauto gui`, docs/GUI.md),
  approve, rate, apply (a real terminal or the GUI, and a visible browser), `nuauto login` (manual SSO),
  `nuauto login google`. Owns the resume (local_config.json resume_path) and, unless NUauto runs on the homelab
  too, the settings.
- Homelab = PROD. Key-based ssh from the laptop (e.g. over Tailscale). venv made with uv
  (Python 3.12), claude CLI logged in (~/.local/bin/claude, or on PATH), user lingering on
  (user timers run without anyone logged in). Owns data/ (the job pool). NOT a git checkout:
  code is copied there by deploy.py (GitHub's main, deploy_from_git) or by sync.py (the laptop's checkout).
  Never edit code on the homelab; the next deploy or push overwrites it.
- Mark-done page (optional). `nuauto web` (web.py) listens on web_listen_host:8765 (the Tailscale IP, so it is not
  reachable from the LAN). To click Mark links from your phone, publish it at web_base_url with any tunnel or reverse
  proxy that forwards to that address (8765 only; never 8766). Example: cloudflared on another box, an ingress rule in
  its config.yml, then restart cloudflared:
    - hostname: <web_base_url host>
      service: http://<web_listen_host>:8765

WHAT RUNS ON THE HOMELAB
Unit files live in this repo (deploy/systemd/) and are installed in ~/.config/systemd/user/. All run with TZ New York.
- nuauto-daily.timer -> nuauto-daily.service: `nuauto daily` at 08:00 and 18:00 New York time
  (+0-15 min random, Persistent=true so a missed run happens at boot). Every run, in order:
  - the scan (docs/PIPELINE.md): list -> Claude triage -> details -> Claude score -> Claude category -> pool; counts
    and the best new job go to data/scans.json.
  - the applications check: NUworks is asked about every sent row (jobs.check_applications -> data/applications.json), so
    the total limit counts what NUworks has; the Status dropdown is set again (Accepted / Rejected reach an older
    sheet). Rejected rows NUworks still has and Applied rows whose job looks filled (its Hiring Status turned Filled, or
    it was interviewing and stopped: sheet.maybe_filled) are in the log, and in the morning message until you withdraw
    them there.
  - the morning run only (before noon): if the scan worked, jobs you applied to by hand on NUworks become Applied rows
    (jobs.check_applied); then the morning message.
  - the internship update (when "internships" is on).
  DISCORD (since 2026-10-26, my request: fewer, shorter messages, glanced at once a day). Two kinds only:
  - "NUauto morning" (daily.morning), always sent, a line for each thing that applies: New (pool co-ops and
    internships of the last 24h, each with the best one), Closing soon (Approved rows on both tabs closing within a week,
    soonest first with the day, an internship's date from its posting; pool co-ops and internships closing within 3
    days not in the sheet, counted), To do (company-site applications owed, external Needs Human rows, the first three:
    each name links to its job, its ✓ to the Mark-done page), Withdraw on NUworks (Rejected / may be filled), what
    other runs kept for it (daily.later -> data/later.json: applications recorded from NUworks, approved internships
    now closed, deploys), Week (this week's count, the total, Approved waiting), Wrong (this run stopped, the sheet
    can't be read, the Google login runs out), and NUauto's page when there is one.
  - a problem when it happens (daily.alert: a run that stopped, a login to renew, the sheet unreadable, a crash): the
    same title at most once a day (data/told.json, TELL_AGAIN), so the 2-hourly internship update failing the same way
    is told once. The morning run's own problems go in the morning message instead (daily.QUIET).
  Not on Discord any more: new pool jobs per update, new internships per 2-hourly update, the separate deadline and
  Google login messages, recorded applications, closed internships, "Deployed", the Sunday check-in. Unchanged: the
  assistant's messages (it waits for you, wants to Submit, ended: docs/REMOTE.md), a failed deploy, device pairing.
  If the job list fails (NUworks slow), it is read once more 2 minutes later (daily.RETRY_WAIT), and a page load gets one
  retry after 60 s; the morning message still goes out, from the sheet and the last saved pool. Why a run stopped goes
  to data/last_run.json (daily.record_run: nuworks_login, nuworks, claude or failed, in a few words); the laptop pulls
  it with data/, and the GUI's New jobs check shows it on Today with the fix (Log in to NUworks, Run it again, Log in to
  Claude there). Discord says the same (once a day at most): a login only when NUworks really asks for one.
  Claude runs as `claude -p --model sonnet`, one call per batch file, shut in work/ (it runs there and may read and
  write only there: no shell, web or MCP tools; the batches hold web text, job postings, so nothing in one can reach
  local/, the code or your home). Headless browser; never applies to anything.
- nuauto-weekly.timer -> nuauto-weekly.service: `nuauto daily weekly`, Sunday 19:00 New York time. Sends nothing
  since 2026-10-26 (the morning message has the week's count); kept so the installed timer runs cleanly (deploy never
  removes unit files: `systemctl --user disable --now nuauto-weekly.timer` turns it off).
- nuauto-intern.timer -> nuauto-intern.service: `nuauto intern update` every 2 hours (:40 on even hours New York,
  +0-10 min). Needs "internships" in local_config.json (docs/PIPELINE.md, SUMMER INTERNSHIPS); without it the run stops at
  once saying so. Simplify's and vanshb03's lists, postings from the companies' own job sites, Claude triage / score /
  category; the morning message counts new pool internships. `nuauto daily` runs the same update after the NUworks
  one; a lock keeps it to one at a time. The laptop's `nuauto intern update` starts it here.
- nuauto-workday.timer -> nuauto-workday.service: `nuauto workday auto` once a day (13:10 New York, +0-20 min):
  signs in to each Workday site NUauto has a login for (read-only, headless), marks clear rejections tied to one sent
  row Rejected, and messages Discord about them, the NUworks rows to withdraw, and closed / unclear statuses (each once;
  data/workday.json). Needs the logins here (remote_assist merges accounts.json); without them it does nothing.
- nuauto-deploy.timer -> nuauto-deploy.service: `nuauto deploy` 2 min after boot, then every 5 minutes (deploy_from_git
  only). Looks at GitHub's main; a new commit is unpacked aside (work/deploy/), imported as a check (deploy.MODULES),
  then its changed files are copied in (the old ones kept to undo), the package is reinstalled if pyproject.toml
  changed, changed unit files are installed (daemon-reload; changed timers restarted, new ones enabled), and nuauto-web
  restarts when a module it runs changed (deploy.web_code: any .py but deploy.NOT_WEB: cli, daily, deploy, selftest,
  window_gtk, inspect_form, __main__; its page's own files, gui_static/, are read per request). A commit that does not
  import, or a web that does not come back, is undone and Discord says so once. It waits while an update (`nuauto
  daily`, `nuauto intern update`) or a NUworks run started from NUauto's page (`nuauto apply`, `nuauto assist nuworks`:
  nuauto-web's children, which a restart would cut off) runs; assistant sessions run in scopes of their own and are
  not cut off. Record: local/deployed.json; log: logs/deploy.log; the next morning message: "Updated: <commit>".
- nuauto-assist.timer -> nuauto-assist.service: `nuauto assist auto` every 10 minutes: the assistant's queue. Does
  nothing unless "remote_assist" and "remote_auto" are on (docs/REMOTE.md).
- nuauto-web.service: `nuauto web`, always on (Restart=on-failure). Serves the signed Mark-done links on
  web_listen_host:8765 (web.LISTEN): GET = confirm page only, POST (button) changes the sheet; every link is
  HMAC-signed with web_secret.txt for one action (site: the company-site part is done; applied: you applied yourself) on
  one row + job. Log: logs/web.log. With remote_app or remote_assist it also serves NUauto's page for your phone on
  :8766 (a thread of this process: gui.serve_remote; docs/REMOTE.md); the runs started there are its children, and its
  update button starts nuauto-daily.service (a second copy never runs beside the timer's).
- Don't also schedule the update on the laptop while the homelab runs it: two runs = double
  Discord messages and Claude usage (`nuauto doctor` warns when the laptop's timer is on).

SECRETS AND STATE (never print or log any of these; all mode 600)
  file                  laptop  homelab  how it gets to the homelab
  token.json            yes     yes      sync.push (only if newer), after `nuauto login google`
  client_secret.json    yes     yes      copied once (Settings > Homelab > Set it up, or by hand; Google OAuth Desktop client)
  session_cookies.json  yes     yes      `nuauto login` on the laptop (sync.push_session)
  browser_profile/      yes     yes      same as session_cookies.json
  discord_webhook.txt   yes     yes      sync.push (only if newer: one saved on the homelab's page stays)
  web_secret.txt        no      yes      created by `nuauto web` on first start. Replacing it breaks
                                         every Mark link already sent.
  (yes) = only with "remote_assist" or "remote_app" (NUauto on the homelab too, docs/REMOTE.md):
  assist_profile/, _2/, _3/  yes  (yes)  company-site logins of the assistant's 1st, 2nd and 3rd slot; never synced
                                         (the homelab has its own)
  accounts.json         yes     (yes)    the job-site logins the assistant made (email + a password per site), and your
                                         own ones imported from Bitwarden (`nuauto accounts import`); with
                                         remote_assist merged both ways (sync.share), else never synced;
                                         `nuauto accounts export` (or Settings) writes Bitwarden's import file
  transcript.pdf        (yes)   (yes)    your unofficial transcript (transcript_path: the GUI's copy local/transcript.pdf,
                                         or any place on the laptop); with remote_assist sync.push copies it there (via
                                         a 600 copy in local/), at once when given in the GUI (sync.push_documents)
  gmail_token.json      yes     (yes)    the read-only Gmail login for the assistant's email codes (`nuauto login gmail`;
                                         renewed by `nuauto login google`); with remote_assist sync.push (if newer)
  gui.lock              yes     (yes)    the running GUI (pid, port, a secret that only opens a window); never synced
                                         (the homelab's is nuauto-web's)
  review_devices.json   no      (yes)    the devices paired with the homelab's page (key hashes only); never synced
  homelab_link.json     (yes)   no       the laptop app's device key for the homelab's page (docs/REMOTE.md); never synced
  answers.json, answers_other.json, profile.json, local_config.json, data/ratings.json: with NUauto on the homelab
                        merged both ways (sync.share; docs/REMOTE.md, WHAT LIVES WHERE); else laptop only
Not secret, gitignored, pushed by sync.push: local_config.json (personal settings), data/ratings.json, docs/STATUS.md
(your local log), google_login.txt (date of the last Google login), the resume (as local/resume.pdf on the homelab).

GOOGLE SHEETS LOGIN (gspread + OAuth)
- OAuth only: gspread.oauth(...), token in token.json (chmod 600). No service accounts (many
  orgs block service account keys: iam.disableServiceAccountKeyCreation).
- Google Cloud project with the Sheets API enabled. OAuth consent screen External (Testing),
  your Google account as the only test user. OAuth client type "Desktop app".
- Testing mode -> a login lasts 7 days (config.GOOGLE_LOGIN_DAYS). Re-login on the laptop: `nuauto login google`; it
  pushes the new token to the homelab. The morning run warns on Discord once 1 day is left; laptop commands open the
  Google login by themselves when it has expired. The homelab never opens a login (no browser there): an expired or
  missing token is sheet.NotLoggedIn.
- Open the sheet with open_by_key(SHEET_ID) so only the Sheets API is needed (SHEET_ID: sheet_id in local_config.json).
  Each process keeps the opened sheet and its tabs 5 minutes (sheet.OPEN, OPEN_FOR; rows are always read fresh), so a
  read is one request: opened again when the sheet ID or the login files change, when the access token is about to
  expire (an expired login is still NotLoggedIn), and after a request Google refuses (sheet.HTTPClient, gspread's
  BackOffHTTPClient: it also waits and retries on "too many requests", about 60 reads a minute).
- Optional: the assistant's email codes (mail.py) need the Gmail API enabled in the same project
  (console.cloud.google.com/apis/library/gmail.googleapis.com), then `nuauto login gmail` on the laptop: Google's
  read-only Gmail permission, a token of its own (local/gmail_token.json), so the Sheets token never reads mail. It
  lasts 7 days too; `nuauto login google` renews both (two Google pages in a row).

SYNC (sync.py; runs automatically inside `nuauto` on the laptop, homelab mode only)
- Before every laptop command but login / update / deploy / doctor / test: pull homelab data/ -> laptop (except
  ratings.json) (`nuauto approve`, rate, apply, status, assist, intern).
- After approve / rate / status: push to the homelab. Also after `nuauto login google`, and `login gmail` (remote_assist).
  A push sends: local_config.json, the code (files listed in sync.CODE: pyproject.toml, src/nuauto/, prompts/, docs;
  rsync -c; NOT with deploy_from_git: then the homelab deploys main itself), docs/STATUS.md, data/ratings.json, the
  resume, token.json (if newer), google_login.txt, discord_webhook.txt (if newer), and with remote_assist the transcript,
  the Gmail login (if newer) and a copy of your writeups (docs/REMOTE.md).
- With NUauto on the homelab (remote_app / remote_assist; its page can change settings and ratings too): every pull and
  push first merges both machines' answer banks, local_config.json, profile.json and data/ratings.json (with
  remote_assist also accounts.json), per question / setting / job (sync.share; rules: docs/REMOTE.md, WHAT LIVES
  WHERE); push then copies only what that could not carry (an older homelab).
- `nuauto update`: push, start nuauto-daily.service on the homelab and show its lines as they come (journalctl -f;
  sync.watch_unit, also the homelab page's update and `nuauto intern update`), pull. Exit code = the run's result.
- `nuauto login`: copy browser_profile/ + session_cookies.json to the homelab (refuses while
  the homelab update is running, because that uses the profile).
- `nuauto test` and `nuauto doctor` don't sync.
- The GUI: pull at start and every 30 minutes (gui.pull_loop); push after a Review session, after the Google login,
  after a settings change the homelab acts on, and when you give it a resume or transcript; never on a timer (an open
  GUI on a work-in-progress branch does not deploy it by itself).

DEPLOYING A CHANGE
With deploy_from_git (the homelab pulls main; no unit file or dependency steps by hand):
1. Change code on the laptop (on a branch; merge to main when approved).
2. Merge to main. Within 5 minutes the homelab has it (the next morning message: "Updated: <commit>"). Now:
   `nuauto deploy` (starts the run on the homelab and shows the end of its log). A branch on the laptop never reaches
   the homelab.
3. `nuauto doctor` shows the deployed commit; a failed deploy shows as FAIL with the reason (logs/deploy.log).
   Roll back: revert the commit on main (a new commit), merge it; the homelab deploys the revert.
What it copies: src/nuauto/, prompts/, docs/, deploy/systemd/ and the top-level pyproject.toml, README.md, CLAUDE.md,
CONTRIBUTING.md, local_config.example.json (deploy.DIRS / deploy.FILES). Not tests/, packaging/ or .github/.
The old way, without deploy_from_git:
1. Change code on the laptop (on a branch; merge to main when approved).
2. Push it: any `nuauto status` (or approve/rate), or `.venv/bin/python -c "from nuauto import sync; sync.push()"`.
   Whatever is checked out on the laptop is what gets pushed.
3. New module (src/nuauto/) or prompt file (prompts/)? Add it to sync.PACKAGE / sync.CODE (tests/test_sync.py
   fails if you forget; deploy.py copies whole folders and needs nothing). New dependency in pyproject.toml? Reinstall on
   the homelab: ssh homelab 'cd ~/projects/auto && uv pip install --python .venv/bin/python -e .'
4. Changed a module nuauto-web runs (web.py, and with NUauto's page there most of them: deploy.web_code)? Restart it,
   when no NUworks run of its page is going:
   ssh homelab systemctl --user restart nuauto-web
   (daily/weekly runs start fresh each time; no restart needed.)
5. Changed a unit file in deploy/systemd/? Install it by hand:
   scp deploy/systemd/<unit> homelab:.config/systemd/user/
   ssh homelab 'systemctl --user daemon-reload && systemctl --user restart <unit>'
   (for a .timer: restart the timer, not the service)
6. Check: `nuauto doctor`.
Roll back: `git log`, then `git revert <commit>` (or check out the old file), and push again.

HEALTH AND LOGS
  nuauto doctor [--json]                                   # both machines, read-only (doctor.py; the GUI's Homelab check uses it)
  ssh homelab 'systemctl --user list-timers "nuauto*"'     # next runs
  ssh homelab 'journalctl --user -u nuauto-daily -n 50'    # last run's output
  ssh homelab 'ls -t ~/projects/auto/logs/daily-*.txt | head -3'   # per-run logs (homelab)
  ssh homelab 'systemctl --user status nuauto-web'
  ssh homelab 'tail -n 20 ~/projects/auto/logs/deploy.log'   # deploys (deploy_from_git); web.log, review.log: the page
Doctor: the laptop's files, resume, Google, NUworks session and Claude; then (run on the homelab) secret files mode 600,
Google days left, the daily / weekly / web (and deploy) units active, the last update run (WARN after 15 h), nuauto-web
older than the code, deployed commit vs main, lingering, Claude, disk, and with the remote_* keys the assistant's tools
and the page on :8766; then code and unit files against the laptop's, and the public Mark-done address. It does not
check nuauto-intern.timer or nuauto-assist.timer.
In the GUI: Settings > Status > Run all checks now.

TURN ON DEPLOY FROM GITHUB (once, after this is merged to main)
Easiest: Settings > Homelab > Advanced, or `nuauto onboard homelab-deploy on [<fork's https address>]` (pushes the
code once, installs and enables the deploy timer, sets deploy_from_git; `off` turns it back).
By hand:
1. Laptop, on main: push the code once the old way: `nuauto status` (it carries deploy.py and the unit files).
2. scp deploy/systemd/nuauto-deploy.* homelab:.config/systemd/user/
   ssh homelab 'systemctl --user daemon-reload && systemctl --user enable --now nuauto-deploy.timer'
3. Add "deploy_from_git": true to local_config.json; `nuauto status` pushes it. From then on the laptop sends no code.
4. `nuauto deploy`, then `nuauto doctor`.
Turn it off: set it to false (the laptop pushes code again) and `systemctl --user disable --now nuauto-deploy.timer`.

SET UP (OR REBUILD) A HOMELAB
The GUI does all of this (a source install): Settings > Homelab (or setup's optional Homelab step): its ssh name ->
Check (over ssh, read-only: its hostname and Tailscale address, what NUauto has there; `nuauto onboard homelab-check
<ssh name> [<folder>]`) -> Use this homelab (fills the server fields) -> restart NUauto -> Set it up: a terminal window
running `nuauto onboard homelab-setup`, which asks before each of its 8 steps (y / skip / quit; safe to run again; sudo
asks for the homelab's password in that window, NUauto never sees it): 1 push code, settings, resume, tokens; 2 uv, the
venv, Playwright's Firefox; 3 copy data/; 4 client_secret.json; 5 Claude Code installed and started once (log in,
trust the folder, /exit); 6 the NUworks session; 7 the units (daily, weekly, intern, web, plus deploy and assist when
on) and lingering; 8 the assistant's tools (remote_assist only). Then check: Settings > Status, or `nuauto doctor`.
By hand:
1. Linux with systemd; `ssh homelab` works from the laptop with a key (BatchMode).
   Fill in the server fields of local_config.json (see MODES). If the hostname, path or IP
   changes later, update them there (and your tunnel's ingress rule).
2. Laptop: ssh homelab mkdir -p projects/auto/data
           .venv/bin/python -c "from nuauto import sync; sync.push()"   # code, config, resume, token, ratings
           rsync -a data/ homelab:projects/auto/data/            # keep the pool (else Claude re-scores everything)
3. Homelab, in ~/projects/auto:
           uv venv --python 3.12 .venv
           uv pip install --python .venv/bin/python -e .          # installs .venv/bin/nuauto
           .venv/bin/python -m playwright install firefox
           (if Firefox won't start: sudo .venv/bin/python -m playwright install-deps firefox)
   Install the claude CLI (curl -fsSL https://claude.ai/install.sh | bash) and log in once (run `claude`).
4. Copy into local/, mode 600: client_secret.json (discord_webhook.txt comes with sync.push; web_secret.txt from the
   old machine by hand, if you want old Mark links to keep working).
5. Laptop: `nuauto login` (copies the NUworks session).
6. Units: scp deploy/systemd/* homelab:.config/systemd/user/
          ssh homelab 'sudo loginctl enable-linger $USER'   # asks for the sudo password
          ssh homelab 'systemctl --user daemon-reload && systemctl --user enable --now nuauto-daily.timer nuauto-weekly.timer nuauto-intern.timer nuauto-web.service'
          (plus nuauto-deploy.timer with deploy_from_git, nuauto-assist.timer with remote_assist)
7. Laptop: `nuauto doctor`, then `nuauto update` to see one full run.
