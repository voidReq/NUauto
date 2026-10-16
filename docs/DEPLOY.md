DEPLOYMENT: laptop, optional homelab, optional public Mark-done page
(Keep this current when you change hosts, units, secrets or sync.)
File names below are in local/ (gitignored, mode 700) unless a folder is given: local_config.json
means local/local_config.json. The code is the nuauto package (src/nuauto/), installed editable in .venv.

MODES
- Local mode (default): server_hostname "" in local_config.json (config.HAS_SERVER False).
  Everything runs on one machine: `nuauto update` runs the update here, nothing syncs, no Mark links.
  Automatic updates: the GUI's setup (Notifications, automatic updates) installs a user timer at 08:00 and 18:00:
  systemd (~/.config/systemd/user/nuauto-daily.{timer,service}) or launchd (~/Library/LaunchAgents/com.nuauto.daily.plist).
- The packaged app (AppImage / NUauto.app, docs/GUI.md) is always local mode; homelab mode needs a git checkout.
- Homelab mode: an always-on Linux box runs the update twice a day and sends Discord messages;
  the laptop syncs with it. Set in local_config.json:
    server_hostname  the homelab's hostname (that is how config.IS_SERVER knows it is the homelab)
    server_ssh       how the laptop reaches it with ssh (an ~/.ssh/config alias; default: the hostname)
    server_dir       the project dir there. The units in deploy/systemd/ assume ~/projects/auto;
                     if yours differs, change them with `systemctl --user edit <unit>` (a drop-in), not by editing
                     the file: with deploy_from_git the repo's copy is installed over it.
    deploy_from_git  true = the homelab deploys GitHub's main by itself (DEPLOYING A CHANGE); the laptop stops
                     pushing code. deploy_repo: the URL of another fork (default: this project's GitHub repo).
    web_base_url, web_listen_host   the Mark-done page (below); "" = no Mark links. web_listen_host (the Tailscale IP)
                     is also where NUauto's page for your phone and the assistant's browser views listen.
    remote_app       true = NUauto's page on the homelab too (the same app, for your phone; docs/REMOTE.md);
                     remote_assist (its assistant) turns it on as well.
  The GUI sets all of these up (a source install): Settings > Homelab (Check, Use this homelab, Set it up; its code:
  deploy_from_git and your fork's deploy_repo; the Mark-done links' web_base_url), or setup's optional Homelab step;
  the same card's NUauto on your homelab part for remote_app / remote_assist.
  Below, `ssh homelab` means ssh to server_ssh.

MACHINES (homelab mode)
- Laptop. Where code is edited; the git repo. Runs everything interactive: the GUI (`nuauto gui`, docs/GUI.md),
  approve, rate, apply (a real terminal or the GUI, and a visible browser), `nuauto login` (manual SSO),
  `nuauto login google`. Owns the resume (local_config.json resume_path) and the settings (with NUauto's page on the
  homelab, settings and data/ratings.json changed there are merged back: SYNC).
- Homelab = PROD. Key-based ssh from the laptop (e.g. over Tailscale). venv made with uv
  (Python 3.12), claude CLI logged in (~/.local/bin/claude, or on PATH), user lingering on
  (user timers run without anyone logged in). Owns data/ (the job pool). NOT a git checkout:
  code is copied there by deploy.py (GitHub's main, deploy_from_git) or by sync.py (the laptop's checkout).
  Never edit code on the homelab; the next deploy or push overwrites it.
- Mark-done page (optional). `nuauto web` (web.py) listens on web_listen_host:8765 (use the homelab's
  Tailscale IP so it is not reachable from the LAN). To click Mark links from your phone,
  publish it at web_base_url with any tunnel or reverse proxy. Example: cloudflared on another
  box, ingress rule in /etc/cloudflared/config.yml, then sudo systemctl restart cloudflared:
    - hostname: <web_base_url host>
      service: http://<web_listen_host>:8765

WHAT RUNS ON THE HOMELAB
Unit files live in this repo (deploy/systemd/) and are installed in ~/.config/systemd/user/.
- With "remote_assist": company-site assistant sessions in tmux, each with a virtual screen and a browser view on the
  Tailscale IP; nuauto-assist.timer (every 10 min, `nuauto assist auto`) starts them from the queue when "remote_auto"
  is on for that row's tab (NUworks, Other jobs: each its own switch; else it does nothing). Setup, ports, the queue and what is shared: docs/REMOTE.md.
- nuauto-daily.timer -> nuauto-daily.service: `nuauto daily` at 08:00 and 18:00 New York time
  (+0-15 min random, Persistent=true so a missed run happens at boot). Steps: list -> Claude
  triage -> details -> Claude score -> Claude category -> pool -> notifications (see
  docs/PIPELINE.md). Also: marks jobs applied to by hand as Applied, warns the day before
  the Google login expires. Every run records its counts in data/scans.json. The morning run
  adds deadline reminders and the "NUauto morning" message (always sent): new postings and
  new pool jobs in the last 24h, then the to-do list (company-site applications owed,
  external Needs Human rows) with signed Mark links.
  If the scan fails (e.g. NUworks too slow; a page load gets one retry after 60s), the morning
  reminders still go out, from the sheet and the last saved pool. No job list from NUworks: the list is read once more
  2 minutes later (daily.RETRY_WAIT). Why a run stopped goes to data/last_run.json (daily.record_run: nuworks_login,
  nuworks, claude or failed, in a few words); the laptop pulls it with data/, and the GUI's New jobs check shows it
  on Today with the fix (Log in to NUworks, Run it again, Log in to Claude there). Discord says the same: a login only
  when NUworks really asks for one.
  Claude runs as `claude -p --model sonnet`, one call per batch file, shut in work/ (it runs there and may read and
  write only there: no shell, web or MCP tools; the batches hold web text, job postings, so nothing in one can reach
  local/, the code or your home).
  Headless browser; never applies to anything.
- nuauto-weekly.timer -> nuauto-weekly.service: `nuauto daily weekly`, Sunday 19:00 New York time
  (Discord check-in).
- nuauto-intern.timer -> nuauto-intern.service: `nuauto intern update` every 2 hours (:40 on even hours New York,
  +0-10 min), when local_config.json has "internships" (docs/PIPELINE.md, SUMMER INTERNSHIPS): Simplify's and vanshb03's lists
  (the same job on both kept once),
  postings from the companies' own job sites (plain requests and a hidden browser, no login), Claude triage / score /
  category, Discord for new pool internships. `nuauto daily` runs the same update after the NUworks one; a lock keeps
  it to one at a time. Off: it does nothing (the update says so). The laptop's `nuauto intern update` starts it here.
- nuauto-deploy.timer -> nuauto-deploy.service: `nuauto deploy` every 5 minutes (deploy_from_git only): looks at
  GitHub's main; a new commit is unpacked aside, imported as a check, then its changed files are copied in, the
  package is reinstalled if pyproject.toml changed, changed unit files are installed (daemon-reload, timers
  restarted, new ones enabled) and nuauto-web restarts when a module it runs changed (deploy.web_code: any module
  but cli, daily, deploy, selftest, window_gtk, inspect_form; its page's own files are read per request). A commit
  that does not import, or a web that does not come back, is undone and Discord says so once. It waits while an
  update (`nuauto daily`, `nuauto intern update`) or a NUworks run started from NUauto's page (`nuauto apply`, `nuauto
  assist nuworks`: nuauto-web's children, which a restart would cut off) runs. Record: local/deployed.json; log:
  logs/deploy.log.
- nuauto-web.service: `nuauto web`, always on (Restart=on-failure). Listens on web_listen_host:8765
  only (web.LISTEN). With remote_app or remote_assist it also serves NUauto's page for your phone on
  web_listen_host:8766 (gui.serve_remote: the same app as `nuauto gui`, paired devices only; never add 8766 to the
  tunnel: Tailscale only, docs/REMOTE.md); the runs started there (Apply on its virtual screen, the NUworks side) are its
  children; its update button starts nuauto-daily.service. Serves the signed Discord links: GET = confirm page only, POST (button)
  changes the sheet. Every link is HMAC-signed with web_secret.txt for one action on one
  row + job. Log: logs/web.log.
- Don't also schedule the update on the laptop while the homelab runs it: two runs = double
  Discord messages and Claude usage.

SECRETS AND STATE (never print or log any of these; all mode 600)
  file                  laptop  homelab  how it gets to the homelab
  token.json            yes     yes      sync.push (only if newer), after `nuauto login google`
  client_secret.json    yes     yes      copied once (Settings > Homelab > Set it up, or by hand; Google OAuth Desktop client)
  session_cookies.json  yes     yes      `nuauto login` on the laptop (sync.push_session)
  browser_profile/      yes     yes      same as session_cookies.json
  discord_webhook.txt   yes     yes      sync.push (only if newer: one saved on the homelab's page stays)
  web_secret.txt        no      yes      created by `nuauto web` on first start. Replacing it breaks
                                         every Mark link already sent.
  assist_profile/       yes     (yes)    company-site logins of `nuauto assist`; never synced (with remote_assist the
                                         homelab has its own)
  assist_profile_2/, _3/ yes    (yes)    the same, for the 2nd and 3rd assistant running side by side
  accounts.json         yes     (yes)    the job-site logins the assistant made (email + a password per site), and your
                                         own ones imported from Bitwarden (`nuauto accounts import`); with
                                         remote_assist merged both ways by sync.share (docs/REMOTE.md), else never
                                         synced; `nuauto accounts export` (or Settings) writes Bitwarden's import file
  transcript.pdf        (yes)   (yes)    your unofficial transcript (transcript_path, any place on the laptop), for the assistant to
                                         upload; with remote_assist sync.push copies it there (via a 600 copy in local/)
  gmail_token.json      yes     (yes)    the read-only Gmail login for the assistant's email codes (`nuauto login gmail`;
                                         renewed by `nuauto login google`); with remote_assist sync.push (if newer)
  gui.lock              yes     (yes)    the running GUI (pid, port, a secret that only opens a window); not synced (the
                                         homelab's: nuauto-web's, with its page on)
  review_devices.json   no      (yes)    the devices paired with the homelab's page (key hashes only); never synced
  homelab_link.json     (yes)   no       the laptop app's device key for the homelab's page (Settings > Homelab > This
                                         app: Link; link.py); never synced; Unlink removes it (and unpairs it there)
  answers.json, answers_other.json  with NUauto on the homelab (remote_app / remote_assist) merged both ways by
                                     sync.share (either page, and the assistant, can change them); else laptop only
  profile.json          the NUworks resume label: with NUauto on the homelab merged both ways (Apply runs there too);
                        else laptop only, not synced
  (yes) = only with "remote_assist" or "remote_app" (NUauto on the homelab too, docs/REMOTE.md).
Not secret, gitignored, pushed by sync.push: local_config.json (personal settings; with NUauto on the homelab merged
both ways per setting by sync.share instead), data/ratings.json (the same), docs/STATUS.md (your local log),
google_login.txt (date of the last Google login), the resume (as local/resume.pdf on the homelab).

GOOGLE SHEETS LOGIN (gspread + OAuth)
- OAuth only: gspread.oauth(...), token in token.json (chmod 600). No service accounts (many
  orgs block service account keys: iam.disableServiceAccountKeyCreation).
- Google Cloud project with the Sheets API enabled. OAuth consent screen External (Testing),
  your Google account as the only test user. OAuth client type "Desktop app".
- Testing mode -> a login lasts 7 days. Re-login on the laptop: `nuauto login google`; it
  pushes the new token to the homelab. Discord warns the day before; laptop commands re-open
  the Google login by themselves when it has expired.
- Open the sheet with open_by_key(SHEET_ID) so only the Sheets API is needed.
- Optional: the assistant's email codes (mail.py) need the Gmail API enabled in the same project
  (console.cloud.google.com/apis/library/gmail.googleapis.com), then `nuauto login gmail` on the laptop: Google's
  read-only Gmail permission, a token of its own (local/gmail_token.json), so the Sheets token never reads mail. It
  lasts 7 days too; `nuauto login google` renews both (two Google pages in a row).
  SHEET_ID: sheet_id in local_config.json.

SYNC (sync.py; runs automatically inside `nuauto` on the laptop, homelab mode only)
- Before approve / rate / apply / status: pull homelab data/ -> laptop (except ratings.json).
- After approve / rate / status / login google: push to the homelab: local_config.json, the
  code (files listed in sync.CODE: pyproject.toml, src/nuauto/, prompts/, docs; rsync -c; NOT with deploy_from_git:
  then the homelab deploys main itself), docs/STATUS.md, data/ratings.json, the resume,
  token.json (if newer), google_login.txt, discord_webhook.txt (if newer).
- With NUauto on the homelab (remote_app / remote_assist; its page can change settings and ratings too): every pull
  and push first merges both machines' answer banks, local_config.json, profile.json and data/ratings.json, per
  question / setting / job (sync.share; docs/REMOTE.md, WHAT LIVES WHERE); push then copies only what that could not
  carry (an older homelab).
- `nuauto update`: push, start nuauto-daily.service on the homelab and show its lines as they come (journalctl -f;
  sync.watch_unit, also the homelab page's update and `nuauto intern update`), pull. Exit code = the run's result.
- `nuauto login`: copy browser_profile/ + session_cookies.json to the homelab (refuses while
  the homelab update is running, because that uses the profile).
- `nuauto test` and `nuauto doctor` don't sync.
- The GUI: pull at start and every 30 minutes; push after a Review session and after the Google login, never on a
  timer (an open GUI on a work-in-progress branch does not deploy it by itself).

DEPLOYING A CHANGE
With deploy_from_git (the homelab pulls main; no unit file or dependency steps by hand):
1. Change code on the laptop (on a branch; merge to main when approved).
2. Merge to main. Within 5 minutes the homelab has it (Discord: "Deployed <commit>"). Now: `nuauto deploy`
   (starts the run on the homelab and shows its log). A branch on the laptop never reaches the homelab.
3. `nuauto doctor` shows the deployed commit; a failed deploy shows as FAIL with the reason (logs/deploy.log).
   Roll back: revert the commit on main (a new commit), merge it; the homelab deploys the revert.
What it copies: src/nuauto/, prompts/, docs/, deploy/systemd/ and the top-level pyproject.toml, README.md, CLAUDE.md,
CONTRIBUTING.md, local_config.example.json (deploy.DIRS / deploy.FILES). Not tests/, packaging/ or .github/.
The old way, without deploy_from_git:
1. Change code on the laptop (on a branch; merge to main when approved).
2. Push it: any `nuauto status` (or approve/rate), or `.venv/bin/python -c "from nuauto import sync; sync.push()"`.
   Whatever is checked out on the laptop is what gets pushed.
3. New module (src/nuauto/) or prompt file (prompts/)? Add it to sync.PACKAGE / sync.CODE (tests/test_sync.py
   fails if you forget). New dependency in pyproject.toml? Reinstall on the homelab:
   ssh homelab 'cd ~/projects/auto && uv pip install --python .venv/bin/python -e .'
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
  nuauto doctor                                            # both machines, read-only
  ssh homelab 'systemctl --user list-timers "nuauto*"'     # next runs
  ssh homelab 'journalctl --user -u nuauto-daily -n 50'    # last run's output
  ssh homelab 'ls -t ~/projects/auto/logs/daily-*.txt | head -3'   # per-run logs (homelab)
  ssh homelab 'systemctl --user status nuauto-web'
  ssh homelab 'tail -n 20 ~/projects/auto/logs/deploy.log'   # deploys (deploy_from_git)

TURN ON DEPLOY FROM GITHUB (once, after this is merged to main)
1. Laptop, on main: push the code once the old way: `nuauto status` (it carries deploy.py and the unit files).
2. scp deploy/systemd/nuauto-deploy.* homelab:.config/systemd/user/
   ssh homelab 'systemctl --user daemon-reload && systemctl --user enable --now nuauto-deploy.timer'
3. Add "deploy_from_git": true to local_config.json; `nuauto status` pushes it. From then on the laptop sends no code.
4. `nuauto deploy`, then `nuauto doctor`.
Turn it off: set it to false (the laptop pushes code again) and `systemctl --user disable --now nuauto-deploy.timer`.

SET UP (OR REBUILD) A HOMELAB
The GUI does all of this (a source install): Settings > Homelab (or setup's optional Homelab step): its ssh name ->
Check (over ssh, read-only: its hostname and Tailscale address, what NUauto has there) -> Use this homelab (fills the
server fields) -> restart NUauto -> Set it up: a terminal window running `nuauto onboard homelab-setup`, which does
steps 2-6 below one at a time, each asked first (sudo asks for the homelab's password in that window; NUauto never sees
it), and 7's checks are the Homelab chip. By hand:
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
7. Laptop: `nuauto doctor`, then `nuauto update` to see one full run.
