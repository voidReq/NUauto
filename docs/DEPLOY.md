DEPLOYMENT: laptop, homelab (prod), Pi tunnel
(Written 2026-10-03. Keep this current when you change hosts, units, secrets or sync.)
The homelab is optional: server_hostname "" in local_config.json = local mode (config.HAS_SERVER
False): `nuauto update` runs daily.py on this machine, nothing syncs, no Mark links.

MACHINES
- Laptop (Fedora, ~/projects/auto). Where code is edited; the git repo.
  Runs everything interactive: approve, rate, apply (needs a real terminal and a visible
  browser), `nuauto login` (manual SSO), `nuauto login google`. Owns data/ratings.json and
  the resume (local_config.json resume_path).
- Homelab = PROD. `ssh homelab` (alias in ~/.ssh/config, key auth, over Tailscale).
  Ubuntu. Its hostname (server_hostname; that is how config.IS_SERVER knows) and project dir
  (server_dir) are in local_config.json; below, ~/projects/auto means that dir. venv made with uv
  (Python 3.12), claude CLI at ~/.local/bin/claude (logged in; daily.CLAUDE also checks PATH), user lingering on (user
  timers run without anyone logged in). Owns data/ (the job pool). NOT a git checkout:
  code is copied there by sync.py. Never edit code on the homelab; the next push
  overwrites it.
- Pi. `ssh pi`. Runs cloudflared (system service "cloudflared"), which publishes the
  homelab's Mark-done page at web_base_url (local_config.json). Ingress rule in
  /etc/cloudflared/config.yml (added 2026-10-03; backup config.yml.bak-20261003-132957):
    - hostname: <web_base_url host>
      service: http://<web_listen_host>:8765
  After editing: sudo systemctl restart cloudflared.

WHAT RUNS ON THE HOMELAB
Unit files live in this repo (deploy/systemd/) and are installed in ~/.config/systemd/user/.
- nuauto-daily.timer -> nuauto-daily.service: `daily.py` at 08:00 and 18:00 New York time
  (+0-15 min random, Persistent=true so a missed run happens at boot). Steps: list -> Claude
  triage -> details -> Claude score -> Claude category -> pool -> notifications (see
  docs/PIPELINE.md). Also: marks jobs I applied to by hand as Applied, warns the day before
  the Google login expires. The morning run adds deadline reminders and the to-do list
  (company-site applications owed, external Needs Human rows) with signed Mark links.
  Claude runs as `claude -p --model sonnet` with Read/Write only, one call per batch file in work/.
  Headless browser; never applies to anything.
- nuauto-weekly.timer -> nuauto-weekly.service: `daily.py weekly`, Sunday 19:00 New York time
  (Discord check-in).
- nuauto-web.service: `web.py`, always on (Restart=on-failure). Listens on the homelab's
  Tailscale IP (web_listen_host):8765 only (web.LISTEN). Serves the signed Discord links:
  GET = confirm page only, POST (button) changes the sheet. Every link is HMAC-signed with
  web_secret.txt for one action on one row + job. Log: logs/web.log.
- The laptop used to run nuauto-daily at 14:00. Retired 2026-10-03 (disabled). Don't
  re-enable it while the homelab runs: two runs = double Discord messages and Claude usage.

SECRETS AND STATE (never print or log any of these; all mode 600)
  file                  laptop  homelab  how it gets to the homelab
  token.json            yes     yes      sync.push (only if newer), after `nuauto login google`
  client_secret.json    yes     yes      copied by hand once (Google OAuth Desktop client)
  session_cookies.json  yes     yes      `nuauto login` on the laptop (sync.push_session)
  browser_profile/      yes     yes      same as session_cookies.json
  discord_webhook.txt   yes     yes      copied by hand
  web_secret.txt        no      yes      created by web.py on first start. Replacing it breaks
                                         every Mark link already sent.
  answers.json, profile.json  laptop only in practice (apply runs on the laptop); not synced
Not secret: google_login.txt (date of the last Google login; synced), resume.pdf on the
homelab (pushed from resume_path), local_config.json (personal settings, gitignored; sync.push
copies it to the homelab).

GOOGLE SHEETS LOGIN (gspread + OAuth)
- Service account keys are blocked by the Google Cloud org policy
  (iam.disableServiceAccountKeyCreation). Do NOT use service accounts. OAuth only:
  gspread.oauth(...), token in token.json (chmod 600).
- Google Cloud project "My First Project" under my own org. Sheets API enabled.
  OAuth consent screen External (Testing), my Gmail account the only test user
  (Internal was rejected: that Gmail account is not in the org directory). OAuth client
  type "Desktop app".
- Testing mode (staying that way, my choice) -> a login lasts 7 days. Re-login on the laptop:
  `nuauto login google`; it pushes the new token to the homelab. Discord warns the day
  before; laptop commands re-open the Google login by themselves when it has expired.
- Open the sheet with open_by_key(SHEET_ID) so only the Sheets API is needed.
  SHEET_ID: sheet_id in local_config.json.

SYNC (sync.py; runs automatically inside `nuauto` on the laptop)
- Before approve / rate / apply / status: pull homelab data/ -> laptop (except ratings.json).
- After approve / rate / status / login google: push to the homelab: the code (files listed
  in sync.CODE, rsync -c), data/ratings.json, the resume, token.json (if newer),
  google_login.txt.
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
  ssh pi 'systemctl status cloudflared'

REBUILD THE HOMELAB FROM SCRATCH
1. Ubuntu + Tailscale; `ssh homelab` works from the laptop with a key (BatchMode).
   If the hostname, user, path or Tailscale IP changed: update server_hostname / server_dir /
   web_listen_host in local_config.json, and the Pi ingress rule.
2. Laptop: ssh homelab mkdir -p projects/auto/data
           .venv/bin/python -c "import sync; sync.push()"     # code, resume, token, ratings
           rsync -a data/ homelab:projects/auto/data/            # keep the pool (else Claude re-scores everything)
3. Homelab, in ~/projects/auto:
           uv venv --python 3.12 .venv
           uv pip install --python .venv/bin/python -r requirements.txt
           .venv/bin/python -m playwright install firefox
           (if Firefox won't start: sudo .venv/bin/python -m playwright install-deps firefox)
   Install the claude CLI to ~/.local/bin/claude and log in once (run `claude`).
4. Copy by hand, mode 600: client_secret.json, discord_webhook.txt (and web_secret.txt from the
   old machine if you want old Mark links to keep working).
5. Laptop: `nuauto login` (copies the NUworks session).
6. Units: scp deploy/systemd/* homelab:.config/systemd/user/
          ssh homelab 'sudo loginctl enable-linger $USER'   # asks for the sudo password
          ssh homelab 'systemctl --user daemon-reload && systemctl --user enable --now nuauto-daily.timer nuauto-weekly.timer nuauto-web.service'
7. Laptop: `nuauto doctor`, then `nuauto update` to see one full run.
