REMOTE: the company-site assistant on the homelab (Phase 3)
(Keep this current when you change it. Code: screen.py, review.py, remote.py, the IS_SERVER parts of assist.py,
sync.share / push_notes.)

WHAT IT IS
- `nuauto assist [other] <row>` runs on the homelab too, inside a tmux session, so an application can go on with the
  laptop closed. Its browser draws on a virtual screen there; you see and use that browser in a web page (noVNC) on
  your phone or laptop, over Tailscale only.
- 3a: you start a session by hand over ssh, in tmux. 3b: you answer it, approve or decline its Submit, and get the
  captcha hand-off on the review page, from your phone; Discord links you there. The terminal (tmux attach) still
  works for anything else.
- 3c: sessions start by themselves from your approved rows (the queue), and a claim keeps the laptop and the homelab
  off each other's rows and job sites. Next: 3d demo mode for the review page.

WHAT STAYS THE SAME
- Nothing is submitted without your OK: the same guard (assist.decide) catches every Submit-type click and Enter. On
  the laptop it asks in the terminal; on the homelab it puts the question on the review page and waits for your tap
  (below). No answer, an error, or anything else = not submitted.
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
   And once, so Claude Code trusts the folder the sessions start in (the review page can't answer that question):
     ssh -t homelab 'cd ~/projects/auto && claude'     -> "Yes, I trust this folder", then /exit
3. Turn it on: the GUI's Settings > Assistant on your homelab (or setup's optional step), or "remote_assist": true in
   local_config.json (laptop). The GUI pushes it to the homelab and restarts its review page; the Homelab chip then
   checks what the homelab still needs (the tools above, its Firefox, Claude's folder trust, the review page). web_listen_host must be the homelab's Tailscale IP (it already
   is for the Mark-done page): the browser view listens there only.
4. Laptop: `nuauto status`. It pushes the setting, the Gmail login, a copy of your writeups, and merges the answer
   banks and job-site logins both ways (below). Merge the PR first: the homelab runs main (deploy_from_git).
Off again: "remote_assist": false (the homelab then refuses `nuauto assist`; its copies stay until you delete them).

PAIR YOUR PHONE (once per device)
  nuauto review pair phone          (laptop or homelab; --discord also posts the link to your Discord)
  Open the printed link on the phone, Tailscale on, and press Pair (within 10 minutes; the link works once). Pair the
  browser you will use: a link tapped in Discord opens in Discord's own browser, which keeps its own cookies (copy it
  into Safari / Chrome instead). Each browser is paired once; then bookmark http://<web_listen_host>:8766/ there.
  nuauto review devices / nuauto review unpair <name>|--all

THE QUEUE (remote.py; nuauto-assist.timer every 10 minutes runs `nuauto assist auto`)
- On with "remote_auto": true (and remote_assist). Hours: "remote_hours": [9, 22] (the default: 09:00-22:00 New York
  time), so no pings at night.
- What it starts: Approved rows of the Other jobs tab and Needs Human rows stopped at a company site, best first:
  closing within a week, then match % (the "match NN%" in Notes), then sheet order. Into free slots (assist_slots),
  one session per job site, never a row or site the laptop holds (claims). NUworks rows only while the weekly cap and
  the total leave room, counting the NUworks sessions already running on either machine (each may submit one).
- Each row starts by itself once (local/assist_auto.json). A session that ended without "Applied" leaves its row for
  you: Start now on the review page (it lists the queue and why each row waits), or `nuauto assist start other 12`.
- A session where you approved a Submit but nobody answered "Did it go through?" (24 h, or Ctrl+C) is never started
  again, not even by Start now: check the site and mark the row, or `nuauto assist start other 12 --again`.
- The record file can't be read: the queue starts nothing until you fix or remove it (it could start a row twice).
- Each session runs in tmux, named nuauto-<nuworks|other>-<row>, in its own systemd scope (it outlives the timer):
  ssh -t homelab tmux attach -t nuauto-other-12      (Ctrl+b then d to leave it running)
- `nuauto assist queue` (homelab) shows what it would start and why the others wait.

CLAIMS (the laptop and the homelab)
- A laptop session first asks the homelab over ssh for a claim on its row and job site (`nuauto assist claim` there):
  refused if the homelab runs that row or site. Held until the laptop session ends (or 12 h), in local/assist_claims/
  on the homelab; the queue and the homelab's sessions keep off it. Homelab unreachable: the laptop goes ahead and says so.
- The laptop's GUI reads the homelab's sessions every minute (`nuauto assist sessions` over ssh): a row the homelab
  works on shows "assistant running on the homelab" (or "waiting for you") instead of Start.

RUN ONE BY HAND
  ssh -t homelab 'cd ~/projects/auto && .venv/bin/nuauto assist start other 12'     (in tmux, as the queue would)
  or in a tmux session of your own: ssh -t homelab 'tmux new -s mine "cd ~/projects/auto && .venv/bin/nuauto assist other 12"'
  Leave it running: Ctrl+b then d. Back to it: ssh -t homelab tmux attach -t <its name> (a phone ssh app works too).
- At the start the terminal shows the browser view's link, http://<web_listen_host>:6081/vnc.html... (slot 1; 6082,
  6083 for slots 2, 3), and its password for this run only (shown nowhere else: not in a log, not on Discord).
- Discord says when the session waits for you (a question, the end list, a captcha), wants to Submit, or ended: the
  company, the agent's first line, the review page's link (and the tmux attach command).
- Ends like on the laptop: /exit (or End session on the page), then "Did you press Submit...?" (the page's Yes / No
  types the answer there) y = Applied. The virtual screen stops with it. No answer in 24 h: the row stays as it was
  (check it yourself) and the slot is free again.

THE REVIEW PAGE (review.py; http://<web_listen_host>:8766/, inside nuauto-web, only with remote_assist)
- One page per running session (/s/<slot>):
  Ready to submit   what it will click, on which page; the guesses you haven't confirmed, what you changed, anything
                    typed with no answer behind it (the same list as the laptop's Submit question); the screen when it
                    asked. Approve / Decline (a note for the agent). The guard waits 9 minutes (assist.WAIT); after
                    that the agent is told to wait for you, the question stays, and a later Approve is a pass for
                    that one action on that page with those values, once, for 10 minutes (assist.take_grant: only a
                    question the guard itself asked this run); the page tells the agent to do it. Anything typed or
                    another page after the question withdraws it (and any pass); an Approve while something was typed
                    meanwhile doesn't count (the agent is told to ask again).
  Its last message  the whole message (a question, the end list, a captcha), and a reply box: what you send is typed
                    into its terminal (tmux send-keys; a newline = Ctrl+J, so several lines go as one message: checked
                    with a real Claude Code session, as is /exit), as if you typed it there. Only into the pane the
                    session started in, and never when that pane shows a shell (the assistant ended).
  The browser       the browser view's link and password, and the screen now. A captcha: solve it there, reply "done".
  Its terminal      the last lines (this run's site passwords taken out); Interrupt (Esc), End session (/exit).
  Ended             Did it go through? Yes / No (typed into the launcher's question).
- Paired devices only: a cookie with a random key (HttpOnly, SameSite=Strict; the homelab keeps its hash in
  local/review_devices.json); this server's own Host; on every POST a token made from the key and a matching Origin
  (Referrer-Policy same-origin: with no-referrer browsers send "Origin: null" and every button would be refused).
  Discord links carry no power by themselves. Each answer goes in the run's actions.log with the device name.
- `nuauto review demo` (any computer): the page with three made-up sessions on 127.0.0.1 (prints a pairing link);
  nothing real is read or sent. Agents look at the page with it (phone width, light / dark), like `nuauto gui --demo`.
- The agent on the homelab never opens your own network: localhost, the LAN, Tailscale addresses (so not this page,
  not the browser view) are refused in its browser and WebFetch (assist.own_network).

WHAT LIVES WHERE (with remote_assist)
  answers.json, answers_other.json   both machines. The laptop merges them both ways (sync.share): at the start of every
       nuauto command (its pull), every 30 minutes while the GUI is open, after a laptop assistant session, on every push.
       Each question takes the side that changed it since the last merge (local/share_base.json); changed on both: the
       laptop's answer stays (it says which); aliases added on both: all kept. The homelab merges the laptop's result
       under its own lock, so an answer its assistant saved in the meantime stays. The merge never deletes by itself (a
       missing file or a copy saved from an old read wipes nothing): delete answers in the GUI's Answers screen, which
       records the deletion (local/answers_gone.json) for both machines; one deleted by editing the file comes back
       from the other copy. A file that can't be read (bad JSON, e.g. while you edit it) is left alone.
  accounts.json (job-site logins)    both machines, merged the same way: a login is never dropped. If each machine made
       an account on the same site before they merged, the earlier stays the login and the other is kept with it
       ("other_passwords"); the merge names the site. Sent as JSON over ssh, never printed or logged.
  gmail_token.json                   pushed by push() when the laptop's is newer (`nuauto login google` / `login gmail`).
  your writeups (assist_writeups)    copied by push() to ~/nuauto-notes/<folder name> on the homelab: only the files the
       agent may read anyway (sync.notes_files = assist.secret_path: no keys, tokens, .env, .git, NUauto's local/),
       a folder over 50 MB is skipped. The agent there reads only these and the resume (local/resume.pdf), never the
       homelab's ~/projects (the live NUauto is there).
  assist_profile*/ (site cookies)    each machine its own, never copied; the agent signs in again with the saved login.
  slots, row and site locks          each machine its own; across the two, the claims above.

PORTS (homelab; slot n = 1..3)
  review page http://<web_listen_host>:8766 (in nuauto-web; the Mark-done tunnel forwards 8765 only); its index lists
  the running sessions and the queue (Start now; a row a session works on links to it), and reloads by itself when a
  session starts, ends or wants something (/v)
  virtual screen :40+n (Xvfb, -nolisten tcp)   VNC 127.0.0.1:5940+n (x11vnc -localhost: never on the network)
  browser view http://<web_listen_host>:6080+n/vnc.html (websockify + noVNC; Tailscale only; a password per run)
  The run folder's screen.log has the three programs' output; local/screen<n>.json their pids (a crashed run's are
  stopped by the next run in that slot). The browser tool's own files (downloads, snapshots, the resume copy) are in
  the run folder's browser/, apart from the guard's (a page can't plant a Submit answer there).

FROM THE LAPTOP'S GUI (Apply > Company sites, only with remote_assist)
- A Homelab assistants card: the homelab's sessions and what each waits for, Open its page (the review page in your
  browser), Open the review page, Pair this computer (asks the homelab for a one-time link over ssh and opens it in
  your browser: press Pair). The page itself can't live inside NUauto's window (it refuses frames, and its cookie
  belongs to the browser that paired), so it opens in your browser.
- Without a homelab (local mode, the packaged app) none of this exists: NUauto works on the laptop alone as before.

PROVEN FOR REAL (2026-10-11, a practice run on the homelab: a side install of the branch, demo mode's fake identity
and sheet, Selenium's public test form)
- The virtual screen and the browser view (Xvfb + x11vnc + noVNC over Tailscale), its real start/stop test there.
- A whole session: Sonnet filled the form in Playwright's Firefox on the virtual screen, its end list answered from a
  phone on the review page (replies typed into its tmux pane), the Submit asked on the page and approved from the
  phone 18 s later, then End session and "Did it go through?" Yes: the fake sheet row Applied, the queue's record
  "approved, y", the screen stopped by itself.
- Pairing a phone (a link opened from Discord pairs Discord's in-app or incognito browser only: copy it into the
  phone's own browser).
NOT YET PROVEN FOR REAL
- The queue starting sessions by itself (nuauto-assist.timer), a laptop claim over ssh, the merge on your real answer
  banks and logins, a real application on the homelab, the GUI's Homelab assistants card.
