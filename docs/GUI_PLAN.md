GUI PLAN (branch `gui`; all phases on this one branch, merged when the owner OKs it)
Status per phase at the bottom. Keep this current while the work is in progress; once merged, the
lasting parts move to docs/GUI.md, docs/ARCHITECTURE.md and docs/SAFETY.md and this file goes.

DECISIONS (2026-10-05, owner)
1. The GUI may submit applications. It counts as "a real terminal": a human clicks Start, the Firefox
   window stays visible, Stop does exactly what Ctrl+C does. Agents never run it for real: code refuses
   real submits inside a Claude Code shell (CLAUDECODE is set there); agents use `nuauto gui --demo`.
2. For other students too: the owner's personal settings (term, class year, major, interests,
   thresholds, bonuses) become preferences set in the setup wizard; the owner's current values are the
   defaults and render the prompts unchanged.
3. Google: each user makes their own Google Cloud OAuth client ("Desktop app"), guided step by step.

APPROACH
- `nuauto gui`: a local web app. ThreadingHTTPServer (stdlib, like web.py) on 127.0.0.1, random port;
  one page of plain HTML/CSS/JS from src/nuauto/gui_static/ (no build step, nothing loaded from the
  internet, light/dark from the OS). Opens as an app window when Chrome/Chromium is installed, else a
  browser tab. Laptop only.
- The CLI stays. The GUI calls the same functions; long or browser work (apply, update, logins, the
  NUworks check) runs as the same `nuauto` command in a child process.
- Questions during a run (answer bank, "did it submit?") travel over a small JSON-lines channel
  (answers.JsonIO). Stop = SIGINT to the child's process group, the same signal Ctrl+C sends. If the GUI
  dies, the child sees its stdin close and stops the same way.

SCREENS: Home (health strip, week count, actions, to-do), Review (approve / rate-only, one job card,
y/n/s/u/o keys; approvals written at once, Undo = back to Proposed), Apply (Approved rows in apply order,
Start, live log + screenshot, question dialogs, Stop, past runs), Company sites (start `nuauto assist <row>`
in a terminal window or copy the command; Mark done; retry the NUworks side), Answers (edit the bank),
Settings (+ the setup wizard).

SETUP WIZARD: welcome + NUworks terms-of-use acknowledgement; tools (Playwright Firefox, Claude Code
install + `claude auth login`, Node/Chrome for the assistant); Google Cloud client (linked steps, drop the
JSON; validated, saved mode 600); Google login; sheet (create via the Sheets API, or paste the URL: the ID
is extracted and access verified; never overwrites data); resume PDF; NUworks login + resume label picked
from the real Apply popup (read-only, inspect_form code); preferences; optional Discord webhook, automatic
updates (systemd user timer / launchd), PATH + app launcher.

HEALTH (health.py; doctor prints the same checks; `nuauto doctor --json`)
  check        how                                                      how often
  google       days left (google_login.txt); live: token refresh + read row 1   1 min / 15 min
  sheet        row 1 headers, status values, week/total counts               15 min
  nuworks      headless page load + one-click SSO re-login (skipped if the   on open, before Apply/Update, 6 h
               browser profile is in use)
  claude       installed; `claude auth status --json` loggedIn; live: one     15 min / daily + before Update
               tiny `claude -p` call
  firefox      Playwright Firefox installed                                  on open
  discord      GET the webhook (posts nothing)                               6 h
  homelab      doctor --server --json over ssh                               15 min
  files        local/ 700, secrets 600, config complete, resume + label      1 min
Checks never start a login or open a window. Preflight before Apply/Update/Assist.

AGENT TESTING
- `nuauto gui --demo`: fake sheet (JSON file), fake Claude, fake NUworks pages served inside Playwright
  (every other request aborted), all state in a temp folder. The real apply.py runs against those pages.
- `--demo-state google-expired,claude-logged-out,nuworks-password,cap-reached` forces each warning.
- `--port 0 --no-open` prints one JSON line {"url": ...} when ready. JSON API for everything shown.
- Tests in `nuauto test`; headless Firefox end-to-end runs; screenshot command; CI on Ubuntu + macOS.

SAFETY
- 127.0.0.1 only, random port, per-launch secret (cookie) + Host/Origin checks + a custom header on
  POSTs; secrets never sent to the page ("set / not set" only).
- `apply --ui json` only as a child of the running GUI (parent pid recorded in local/gui.lock) and never
  inside a Claude Code shell; same CLAUDECODE refusal on the terminal path.
- Real lock on local/browser_profile (closes the GAPS item); background checks never collide.
- GUI keeps today's sync rules (pull on open; push after Review and Google login), never on a timer.
- Approved in the sheet stays the only go-ahead; caps unchanged.

PHASES (all on branch `gui`)
1. Foundations: health.py + doctor --json, profile lock, tool paths, JsonIO + apply/assist --ui json +
   agent guard, jobs.review_queue/job_view, sheet open without login popups, create_sheet, unapprove.
2. GUI shell + demo mode: server, security, Home + health strip, API, task runner, e2e harness, CI.
3. Setup wizard + preferences (prompt templating, generalized class-year/term rules).
4. Review, Apply, Company sites, Answers, Settings screens.
5. install.sh, launchers, scheduler toggle, notifications, docs (README, GUI.md, SAFETY, ARCHITECTURE,
   CLAUDE.md rule 7).

NOT PROVABLE BY AGENTS (owner): first real GUI apply while watching; Google + NUworks logins through the
GUI; a run on a real Mac (CI covers demo mode only).

PROGRESS
- Phase 1: in progress
