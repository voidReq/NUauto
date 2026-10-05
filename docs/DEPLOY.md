DEPLOYMENT: laptop, optional homelab, optional public Mark-done page
(Keep this current when you change hosts, units, secrets or sync.)

MODES
- Local mode (default): server_hostname "" in local_config.json (config.HAS_SERVER False).
  Everything runs on one machine: `nuauto update` runs daily.py here, nothing syncs, no Mark links.
- Homelab mode: an always-on Linux box runs the update twice a day and sends Discord messages;
  the laptop syncs with it. Set in local_config.json:
    server_hostname  the homelab's hostname (that is how config.IS_SERVER knows it is the homelab)
    server_ssh       how the laptop reaches it with ssh (an ~/.ssh/config alias; default: the hostname)
    server_dir       the project dir there. The units in deploy/systemd/ assume ~/projects/auto;
                     edit their WorkingDirectory/ExecStart if yours differs.
    web_base_url, web_listen_host   the Mark-done page (below); "" = no Mark links.
  Below, `ssh homelab` means ssh to server_ssh.

MACHINES (homelab mode)
- Laptop. Where code is edited; the git repo. Runs everything interactive: approve, rate,
  apply (needs a real terminal and a visible browser), `nuauto login` (manual SSO),
  `nuauto login google`. Owns data/ratings.json and the resume (local_config.json resume_path).
- Homelab = PROD. Key-based ssh from the laptop (e.g. over Tailscale). venv made with uv
  (Python 3.12), claude CLI logged in (~/.local/bin/claude, or on PATH), user lingering on
  (user timers run without anyone logged in). Owns data/ (the job pool). NOT a git checkout:
  code is copied there by sync.py. Never edit code on the homelab; the next push overwrites it.
- Mark-done page (optional). web.py listens on web_listen_host:8765 (use the homelab's
  Tailscale IP so it is not reachable from the LAN). To click Mark links from your phone,
  publish it at web_base_url with any tunnel or reverse proxy. Example: cloudflared on another
  box, ingress rule in /etc/cloudflared/config.yml, then sudo systemctl restart cloudflared:
    - hostname: <web_base_url host>
      service: http://<web_listen_host>:8765

WHAT RUNS ON THE HOMELAB
Unit files live in this repo (deploy/systemd/) and are installed in ~/.config/systemd/user/.
- nuauto-daily.timer -> nuauto-daily.service: `daily.py` at 08:00 and 18:00 New York time
  (+0-15 min random, Persistent=true so a missed run happens at boot). Steps: list -> Claude
  triage -> details -> Claude score -> Claude category -> pool -> notifications (see
  docs/PIPELINE.md). Also: marks jobs applied to by hand as Applied, warns the day before
  the Google login expires. Every run records its counts in data/scans.json. The morning run
  adds deadline reminders and the "NUauto morning" message (always sent): new postings and
  new pool jobs in the last 24h, then the to-do list (company-site applications owed,
  external Needs Human rows) with signed Mark links.
  Claude runs as `claude -p --model sonnet` with Read/Write only, one call per batch file in work/.
  Headless browser; never applies to anything.
- nuauto-weekly.timer -> nuauto-weekly.service: `daily.py weekly`, Sunday 19:00 New York time
  (Discord check-in).
- nuauto-web.service: `web.py`, always on (Restart=on-failure). Listens on web_listen_host:8765
  only (web.LISTEN). Serves the signed Discord links: GET = confirm page only, POST (button)
  changes the sheet. Every link is HMAC-signed with web_secret.txt for one action on one
  row + job. Log: logs/web.log.
- Don't also schedule the update on the laptop while the homelab runs it: two runs = double
  Discord messages and Claude usage.

SECRETS AND STATE (never print or log any of these; all mode 600)
  file                  laptop  homelab  how it gets to the homelab
  token.json            yes     yes      sync.push (only if newer), after `nuauto login google`
  client_secret.json    yes     yes      copied by hand once (Google OAuth Desktop client)
  session_cookies.json  yes     yes      `nuauto login` on the laptop (sync.push_session)
  browser_profile/      yes     yes      same as session_cookies.json
  discord_webhook.txt   yes     yes      copied by hand
  web_secret.txt        no      yes      created by web.py on first start. Replacing it breaks
                                         every Mark link already sent.
  assist_profile/       yes     no       company-site logins of `nuauto assist`; never synced
  answers.json, profile.json  laptop only in practice (apply runs on the laptop); not synced
Not secret, gitignored, pushed by sync.push: local_config.json (personal settings),
docs/STATUS.md (your local log), google_login.txt (date of the last Google login), the resume
(as resume.pdf on the homelab).

GOOGLE SHEETS LOGIN (gspread + OAuth)
- OAuth only: gspread.oauth(...), token in token.json (chmod 600). No service accounts (many
  orgs block service account keys: iam.disableServiceAccountKeyCreation).
- Google Cloud project with the Sheets API enabled. OAuth consent screen External (Testing),
  your Google account as the only test user. OAuth client type "Desktop app".
- Testing mode -> a login lasts 7 days. Re-login on the laptop: `nuauto login google`; it
  pushes the new token to the homelab. Discord warns the day before; laptop commands re-open
  the Google login by themselves when it has expired.
- Open the sheet with open_by_key(SHEET_ID) so only the Sheets API is needed.
  SHEET_ID: sheet_id in local_config.json.

SYNC (sync.py; runs automatically inside `nuauto` on the laptop, homelab mode only)
- Before approve / rate / apply / status: pull homelab data/ -> laptop (except ratings.json).
- After approve / rate / status / login google: push to the homelab: local_config.json, the
  code (files listed in sync.CODE, rsync -c), docs/STATUS.md, data/ratings.json, the resume,
  token.json (if newer), google_login.txt.
- `nuauto update`: push, start nuauto-daily.service on the homelab, show its log tail, pull.
- `nuauto login`: copy browser_profile/ + session_cookies.json to the homelab (refuses while
  the homelab update is running, because that uses the profile).
- `nuauto test` and `nuauto doctor` don't sync.

DEPLOYING A CHANGE
1. Change code on the laptop (on a branch; merge to main when approved).
2. Push it: any `nuauto status` (or approve/rate), or `.venv/bin/python -c "import sync; sync.push()"`.
   Whatever is checked out on the laptop is what gets pushed.
3. New .py file or prompt file? Add it to sync.CODE (test_sync.py fails if you forget).
4. Changed web.py or a module it uses (config, sheet, jobs)? Restart it:
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

SET UP (OR REBUILD) A HOMELAB
1. Linux with systemd; `ssh homelab` works from the laptop with a key (BatchMode).
   Fill in the server fields of local_config.json (see MODES). If the hostname, path or IP
   changes later, update them there (and your tunnel's ingress rule).
2. Laptop: ssh homelab mkdir -p projects/auto/data
           .venv/bin/python -c "import sync; sync.push()"     # code, config, resume, token, ratings
           rsync -a data/ homelab:projects/auto/data/            # keep the pool (else Claude re-scores everything)
3. Homelab, in ~/projects/auto:
           uv venv --python 3.12 .venv
           uv pip install --python .venv/bin/python -r requirements.txt
           .venv/bin/python -m playwright install firefox
           (if Firefox won't start: sudo .venv/bin/python -m playwright install-deps firefox)
   Install the claude CLI (curl -fsSL https://claude.ai/install.sh | bash) and log in once (run `claude`).
4. Copy by hand, mode 600: client_secret.json, discord_webhook.txt (and web_secret.txt from the
   old machine if you want old Mark links to keep working).
5. Laptop: `nuauto login` (copies the NUworks session).
6. Units: scp deploy/systemd/* homelab:.config/systemd/user/
          ssh homelab 'sudo loginctl enable-linger $USER'   # asks for the sudo password
          ssh homelab 'systemctl --user daemon-reload && systemctl --user enable --now nuauto-daily.timer nuauto-weekly.timer nuauto-web.service'
7. Laptop: `nuauto doctor`, then `nuauto update` to see one full run.
