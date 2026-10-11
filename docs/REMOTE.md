REMOTE: the company-site assistant on the homelab (Phase 3)
(Keep this current when you change it. Code: screen.py, the IS_SERVER parts of assist.py, sync.share / push_notes.)

WHAT IT IS
- `nuauto assist [other] <row>` runs on the homelab too, inside a tmux session, so an application can go on with the
  laptop closed. Its browser draws on a virtual screen there; you see and use that browser in a web page (noVNC) on
  your phone or laptop, over Tailscale only.
- Phase 3a (this part): you start a session by hand over ssh, and you answer it and approve its Submit in its Claude
  Code terminal (attach to the tmux session from the laptop, or from a phone ssh app). Discord tells you when it waits.
- Next: 3b a review page (answer, approve Submit, captcha hand-off from the phone, through Discord links); 3c sessions
  that start by themselves from the Approved rows, and a claim so the laptop and the homelab never work the same row;
  3d tests, demo mode, docs.

WHAT STAYS THE SAME
- Nothing is submitted without your OK: the same guard (assist.decide) asks before every Submit-type click and Enter,
  in the session's terminal.
- Captchas are yours, on both machines: the guard refuses any click, typing, hover or key inside a captcha (its box,
  its pictures, its buttons; assist.CAPTCHA, captcha_target). The agent tells you and waits; you solve it in the
  browser view. No solver, no disguised browser, no human-like mouse: a plain Playwright Firefox, on a virtual screen.
- Approved rows only (Needs Human external rows, Other jobs rows), the same caps.
- NUworks submits never happen on the homelab. After a NUworks job's company site is done there, its row gets
  "NUworks side NOT submitted yet" and shows in the laptop's Apply > Company sites (retry), or run
  `nuauto assist nuworks <row>` on the laptop.

TURN IT ON (once)
1. Homelab: sudo apt install xvfb x11vnc novnc nodejs npm      (novnc brings websockify)
2. Homelab, the browser tool's own Firefox (its version, next to the update's):
     npx -y -p @playwright/mcp@0.0.82 playwright install firefox
   (assist.MCP_PACKAGE: change the version here too when that pin changes.)
3. local_config.json (laptop): "remote_assist": true. web_listen_host must be the homelab's Tailscale IP (it already
   is for the Mark-done page): the browser view listens there only.
4. Laptop: `nuauto status`. It pushes the setting, the Gmail login, a copy of your writeups, and merges the answer
   banks and job-site logins both ways (below). Merge the PR first: the homelab runs main (deploy_from_git).
Off again: "remote_assist": false (the homelab then refuses `nuauto assist`; its copies stay until you delete them).

RUN ONE
  ssh -t homelab 'tmux new -s nuauto1 "cd ~/projects/auto && .venv/bin/nuauto assist other 12"'
  Leave it running: Ctrl+b then d. Back to it: ssh -t homelab tmux attach -t nuauto1 (a phone ssh app works too).
- At the start the terminal shows the browser view's link, http://<web_listen_host>:6081/vnc.html... (slot 1; 6082,
  6083 for slots 2, 3), and its password for this run only (shown nowhere else: not in a log, not on Discord).
- Discord says when the session waits for you (a question, the end list, a captcha) or wants to Submit: the company,
  the agent's first line, the tmux attach command and the browser link.
- Ends like on the laptop: /exit, then "Did you press Submit...?" y = Applied. The virtual screen stops with it.

WHAT LIVES WHERE (with remote_assist)
  answers.json, answers_other.json   both machines. The laptop merges them both ways (sync.share): at the start of every
       nuauto command (its pull), every 30 minutes while the GUI is open, after a laptop assistant session, on every push.
       Each question takes the side that changed it since the last merge (local/share_base.json); changed on both: the
       laptop's answer stays (it says which); aliases added on both: all kept. The homelab merges the laptop's result
       under its own lock, so an answer its assistant saved in the meantime stays.
  accounts.json (job-site logins)    both machines, merged the same way: a login is never dropped. If each machine made
       an account on the same site before they merged, the earlier stays the login and the other is kept with it
       ("other_passwords"); the merge names the site. Sent as JSON over ssh, never printed or logged.
  gmail_token.json                   pushed by push() when the laptop's is newer (`nuauto login google` / `login gmail`).
  your writeups (assist_writeups)    copied by push() to ~/nuauto-notes/<folder name> on the homelab: only the files the
       agent may read anyway (sync.notes_files = assist.secret_path: no keys, tokens, .env, .git, NUauto's local/),
       a folder over 50 MB is skipped. The agent there reads only these and the resume (local/resume.pdf), never the
       homelab's ~/projects (the live NUauto is there).
  assist_profile*/ (site cookies)    each machine its own, never copied; the agent signs in again with the saved login.
  slots, row and site locks          each machine its own. Until 3c's claim: don't start the same row, or two sessions on
       the same job site, on both machines at once.

PORTS (homelab; slot n = 1..3)
  virtual screen :40+n (Xvfb, -nolisten tcp)   VNC 127.0.0.1:5940+n (x11vnc -localhost: never on the network)
  browser view http://<web_listen_host>:6080+n/vnc.html (websockify + noVNC; Tailscale only; a password per run)
  The run folder's screen.log has the three programs' output; local/screen<n>.json their pids (a crashed run's are
  stopped by the next run in that slot).

NOT YET PROVEN FOR REAL
- Everything here: the virtual screen and view on the homelab, a session there, the merge against real files.
