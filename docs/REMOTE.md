REMOTE: NUauto on the homelab, for your phone; and the company-site assistant there (Phase 3)
(Keep this current when you change it. Code: gui.serve_remote and the REMOTE parts of gui.py / gui_static/, review.py,
screen.py, remote.py, the IS_SERVER parts of assist.py, sync.share / push_notes.)

WHAT IT IS
- NUauto's page on the homelab (2026-10-14, my request: "the webui the homelab hosts 1:1 with the app"): the same app as
  `nuauto gui` (the same server code, gui.py, and the same page, gui_static/), served by nuauto-web on the homelab's
  Tailscale address, port 8766, for your paired phone or other computers. Review, Apply, Sheet, Insights, Answers,
  Settings work there as on the laptop, on the homelab's data, with the laptop closed. It replaced the old review page
  (the same address; its /s/<slot> links now open #/assistant/<slot>).
- "remote_app": true in local_config.json turns the page on (Settings > Homelab > NUauto on your homelab > Its page, on the
  laptop). "remote_assist" (its assistant) turns it on too: you answer the assistant there.
- `nuauto assist [other] <row>` runs on the homelab too, inside a tmux session, so an application can go on with the
  laptop closed. Its browser draws on a virtual screen there; you see and use that browser in a web page (noVNC) on
  your phone or laptop, over Tailscale only. Its screen in the app (its row in Apply, Apply > Assistants, or its item in Today's "Only you can do
  these"): approve or decline its Submit, reply, watch its browser and terminal, end it.
- Sessions start by themselves from your approved rows (the queue), and a claim keeps the laptop and the homelab off
  each other's rows and job sites.

WHAT NEEDS THE LAPTOP (the homelab's page shows these as "On your laptop", with why; the server refuses them there)
- The Google and NUworks logins (each opens a browser window on the computer that asks) and Claude Code's login (a
  terminal): from the laptop. The laptop copies the Google login and the NUworks session to the homelab (sync.push,
  push_session); the homelab's Claude login: the laptop's Settings > Homelab > Log in to Claude there.
- Anything with a terminal window (Bitwarden: it asks your master password there), the files and folders that live on
  the laptop (resume, transcript, the folders the assistant may read), setup's file steps (the Google client, the
  sheet, the resume), Pair this computer, Quit (the page is part of nuauto-web: always on).
- Settings it can change itself: the weekly limit, week start, resume label, the assistant's email and effort, its queue
  (starts by itself, hours, assistants at once), its paired devices; setup's preferences and Discord. These merge back
  to the laptop (WHAT LIVES WHERE).

APPLY ON THE HOMELAB (2026-10-14, my call: "Allow it on the homelab")
- Start (or a row's Apply, or Submit on NUworks too) on the homelab's page runs the same `nuauto apply --ui json` as the
  laptop's GUI, as a child of nuauto-web. Its browser is visible, on a virtual screen of its own (screen.py slot 0:
  display :40, browser view on <web_listen_host>:6080, a password for that run): the run's card shows the link and the
  password; you watch it, and its questions come up on the page, like on the laptop. Same caps, same checks, same logs.
- One machine submits on NUworks at a time (remote.nuworks_turn): both count the weekly cap from the sheet before each
  submit, so two at once could each see room for the last one. A run on the homelab takes the turn there (until it
  ends); a run on the laptop asks the homelab for it over ssh first (homelab unreachable: it goes ahead and says so).
  The other one is refused: "Not now: the homelab is applying on NUworks now".
- The NUworks side of a company-site application the homelab's assistant did is still never sent by itself (nobody
  watches the session's end there): the row gets "NUworks side NOT submitted yet" and Apply's Still owed (either
  machine's page) offers Submit on NUworks too.
- The homelab needs the virtual-screen tools for this too (TURN IT ON, step 1); without them Start says what to install.
  The NUworks session is the one `nuauto login` on the laptop copies there (the one-click re-login renews it); one that
  needs your password stops the run (Needs Human), as on the laptop: log in on the laptop.

WHAT STAYS THE SAME
- Nothing is submitted without your OK: the same guard (assist.decide) catches every Submit-type click and Enter. On
  the laptop it asks in the terminal; on the homelab it puts the question on the assistant's screen and waits for your
  tap (below). No answer, an error, or anything else = not submitted.
- Captchas are yours, on both machines: the guard refuses any click, typing, hover or key inside a captcha (its box,
  its pictures, its buttons; assist.CAPTCHA, captcha_target). The agent tells you and waits; you solve it in the
  browser view. No solver, no disguised browser, no human-like mouse: a plain Playwright Firefox, on a virtual screen.
- Approved rows only (Needs Human external rows, Other jobs rows), the same caps.
- Without a homelab (local mode, the packaged app) none of this exists: NUauto works on the laptop alone, the same way.

TURN IT ON (once; new setups: the laptop's Settings > Homelab > Set it up does steps 1-2 and the rest of a homelab)
1. Homelab: sudo apt install xvfb x11vnc novnc nodejs npm tmux      (novnc brings websockify)
2. Homelab, the browser tool's own Firefox (its version, next to the update's):
     npx -y -p @playwright/mcp@0.0.82 playwright install firefox
   (assist.MCP_PACKAGE: change the version here too when that pin changes.)
   And once, so Claude Code trusts the folder the sessions start in (the page can't answer that question):
     ssh -t homelab 'cd ~/projects/auto && claude'     -> "Yes, I trust this folder", then /exit
     (or the laptop's Settings > Homelab > Log in to Claude there)
3. Turn it on: the laptop's Settings > Homelab > NUauto on your homelab (Its page; Its assistant), or setup's Homelab step, or
   "remote_app" / "remote_assist": true in local_config.json (laptop). The GUI pushes it to the homelab and restarts
   nuauto-web; the Homelab chip then checks what the homelab still needs (the tools above, its Firefox, Claude's folder
   trust, the page). web_listen_host must be the homelab's Tailscale IP (Settings > Homelab > Check finds it): the page
   and the browser view listen there only.
4. Laptop: `nuauto status`. It pushes the settings, the Gmail login, a copy of your writeups, and merges the answer
   banks, settings, ratings and job-site logins both ways (below). Merge the PR first: the homelab runs main
   (deploy_from_git).
Off again: Turn off in the same place (the homelab's page stops; its copies stay until you delete them).

PAIR YOUR PHONE (once per device)
  nuauto review pair phone          (laptop or homelab; --discord also posts the link to your Discord)
  or, from a device already paired: the homelab page's Settings > Paired devices > Make a pairing link.
  Open the link on the phone, Tailscale on, and press Pair (within 10 minutes; the link works once). Pair the browser
  you will use: a link tapped in Discord opens in Discord's own browser, which keeps its own cookies (copy it into
  Safari / Chrome instead). Each browser is paired once; then bookmark http://<web_listen_host>:8766/ there (or Add to
  Home Screen). The laptop's browser: Settings > Homelab > Pair this computer.
  nuauto review devices / nuauto review unpair <name>|--all     (or Settings > Paired devices > Unpair)

THE QUEUE (remote.py; nuauto-assist.timer every 10 minutes runs `nuauto assist auto`)
- On with "remote_auto" (and remote_assist), per sheet tab: true = both; {"nuworks": true, "other": false} = each on
  its own (2026-10-13, my request; Settings > Homelab has a switch for each). A tab that is off starts
  nothing by itself; its rows still show in Apply with Start now. Hours: "remote_hours": [9, 22] (the default:
  09:00-22:00 New York time, whatever clock the homelab runs on), so no pings at night: the only hours it starts
  sessions by itself (one already running goes on; you can start one yourself any time). It takes effect at the timer's
  next run (every 10 minutes).
- By-hand NUworks rows (stopped on NUworks for a cover letter, a transcript...: docs/ASSIST.md) are listed too but never
  start by themselves (they need you there: NUworks' sign-in in its browser view, your letter): Start now.
- What it starts: Approved rows of the Other jobs tab and Needs Human rows stopped at a company site, best first:
  closing within a week, then match % (the "match NN%" in Notes), then sheet order. Into free slots (assist_slots),
  one session per job site, never a row or site the laptop holds (claims). NUworks rows only while the weekly cap and
  the total leave room, counting the NUworks sessions already running on either machine (each may submit one).
- On the homelab's page, Apply shows each company-site row's place in it (review.queue_view, read again every 20 s
  at most): "starts next by itself", "ready to start" (Start now), or what it waits for (a slot, its job site, the
  weekly cap; check first: a Submit approved, outcome not given; marked: do it on the laptop); the internships tab's
  "Sort: the queue's order" (the default there); and Apply > Assistants' line with what starts by itself, its hours,
  the free slots and the NUworks applications left.
- A row that has had a session (started by the queue or by you) never starts again by itself (local/assist_auto.json):
  if it ended without "Applied", it waits for you: Start now on its row in Apply, or `nuauto assist start other 12`.
- Not applying (closed, a duplicate, a bad location): Drop (pick the reason). The row becomes Failed with "Dropped
  <date>: <reason>" in Notes (sheet.drop), so it leaves the queue; never while a session or a laptop claim is on it.
- One session per job site (assist.site_key): the host, but each company on a board companies share by path is its own
  site (job-boards.greenhouse.io/<company>, jobs.lever.co/<company>, jobs.ashbyhq.com/<company>,
  apply.workable.com/<company>, wd1.myworkdaysite.com/<company>), so two companies' Greenhouse jobs run side by side.
- Some sites refuse an automated browser (they show it a sign-in page with no way to register). End such a session
  with "End: do it on the laptop" (or "No: do it on the laptop" at "Did it go through?"): no question after, and the
  row's Notes get "DO ON THE LAPTOP..." (assist.LAPTOP_MARK, at the end): the homelab never starts it again, and the
  laptop's GUI shows "do it on the laptop" on it (start the assistant there, or apply yourself). NUauto never disguises
  its browser to get past such a site.
- A session where you approved a Submit but nobody answered "Did it go through?" (24 h, or Ctrl+C) is never started
  again, not even by Start now: check the site and mark the row, or `nuauto assist start other 12 --again`.
- The record file can't be read: the queue starts nothing until you fix or remove it (it could start a row twice).
- Each session runs in tmux, named nuauto-<nuworks|other>-<row>, in its own systemd scope (it outlives the timer and
  a restart of nuauto-web): ssh -t homelab tmux attach -t nuauto-other-12      (Ctrl+b then d to leave it running)
- `nuauto assist queue` (homelab) shows what it would start and why the others wait.

CLAIMS (the laptop and the homelab)
- A laptop session first asks the homelab over ssh for a claim on its row and job site (`nuauto assist claim` there):
  refused if the homelab runs that row or site. Held until the laptop session ends (or 12 h), in local/assist_claims/
  on the homelab; the queue and the homelab's sessions keep off it. Homelab unreachable: the laptop goes ahead and says so.
- The laptop's GUI reads the homelab's sessions every minute (`nuauto assist sessions` over ssh): a row the homelab
  works on shows "assistant running on the homelab" (or "waiting for you") instead of Start.
- NUworks runs (Apply, the NUworks side) take the NUworks turn the same way (APPLY ON THE HOMELAB).

RUN ONE BY HAND
  ssh -t homelab 'cd ~/projects/auto && .venv/bin/nuauto assist start other 12'     (in tmux, as the queue would)
  or in a tmux session of your own: ssh -t homelab 'tmux new -s mine "cd ~/projects/auto && .venv/bin/nuauto assist other 12"'
  Leave it running: Ctrl+b then d. Back to it: ssh -t homelab tmux attach -t <its name> (a phone ssh app works too).
- At the start the terminal shows the browser view's link, http://<web_listen_host>:6081/vnc.html... (slot 1; 6082,
  6083 for slots 2, 3), and its password for this run only (shown nowhere else: not in a log, not on Discord; its
  screen in the app shows it to your paired devices).
- Discord says when the session waits for you (a question, the end list, a captcha), wants to Submit, or ended: the
  company, the agent's first line, the link to its screen in the app (and the tmux attach command).
- Ends like on the laptop: /exit (or End session on its screen), then "Did you press Submit...?" (the screen's Yes / No
  types the answer there) y = Applied. The virtual screen stops with it. No answer in 24 h: the row stays as it was
  (check it yourself) and the slot is free again.

AN ASSISTANT'S SCREEN (#/assistant/<slot> in the homelab's app; review.view / act, served by gui.py)
- Read like a chat, newest at the bottom; it opens at what it wants from you, asks again every 4 s (what you type is
  kept), and refreshes the browser picture and the terminal in place:
  The browser       the browser view's link and password, and the screen now. A captcha: solve it there, reply "done".
  Its terminal      the last lines (this run's site passwords taken out); Interrupt (Esc).
  Its last message  the whole message (a question, the end list, a captcha).
  Ready to submit   what it will click, on which page; the guesses you haven't confirmed, what you changed, anything
                    typed with no answer behind it (the same list as the laptop's Submit question); the screen when it
                    asked. Approve / Decline (a note for the agent). The guard waits 9 minutes (assist.WAIT); after
                    that the agent is told to wait for you, the question stays, and a later Approve is a pass for
                    that one action on that page with those values, once, for 10 minutes (assist.take_grant: only a
                    question the guard itself asked this run); the page tells the agent to do it. Anything typed or
                    another page after the question withdraws it (and any pass); an Approve while something was typed
                    meanwhile doesn't count (the agent is told to ask again).
  Reply             typed into its terminal (tmux send-keys; a newline = Ctrl+J, so several lines go as one message:
                    checked with a real Claude Code session, as is /exit), as if you typed it there. Only into the pane
                    the session started in, and never when that pane shows a shell (the assistant ended). End session
                    (/exit) and End: do it on the laptop.
  Ended             Did it go through? Yes / No / No: do it on the laptop (typed into the launcher's question). One tap:
                    back to Apply > Assistants, and the screen says your answer until the run closes.
- Each answer goes in the run's actions.log with the paired device's name.
- The agent on the homelab never opens your own network: localhost, the LAN, Tailscale addresses (so not this page,
  not the browser view) are refused in its browser and WebFetch (assist.own_network).

HOW THE PAGE IS KEPT YOURS (gui.Handler in REMOTE mode; docs/SAFETY.md)
- Listens on web_listen_host:8766 only (refused when it is not set, or every address); this server's own Host on every
  request (a rebinding DNS name never matches).
- Paired devices only: a cookie with a random key (HttpOnly, SameSite=Strict; the homelab keeps its hash in
  local/review_devices.json). The page's own code ("/", /static/) is served to anyone, because a link tapped in another
  app arrives without the SameSite=Strict cookie; every /api call needs the cookie (else 403 "not paired").
- Every POST: X-NUauto: 1 (other sites can't send it without asking first, and the server never says yes), JSON, and a
  matching Origin (Referrer-Policy same-origin: with no-referrer a phone's browser sends "Origin: null" on the pairing
  form). Discord links carry no power by themselves.
- Secrets never reach it, as on the laptop (tokens, cookies, the webhook, passwords). It does show what the laptop's
  page shows: your sheet, job data, saved answers, run logs and screenshots, settings: to your paired devices only.

WHAT LIVES WHERE (with NUauto on the homelab)
  answers.json, answers_other.json   both machines. The laptop merges them both ways (sync.share): at the start of every
       nuauto command (its pull), every 30 minutes while the GUI is open, after a laptop assistant session, on every push.
       Each question takes the side that changed it since the last merge (local/share_base.json); changed on both: the
       laptop's answer stays (it says which); aliases added on both: all kept. The homelab merges the laptop's result
       under its own lock, so an answer saved there in the meantime stays. The merge never deletes by itself (a
       missing file or a copy saved from an old read wipes nothing): delete answers in the GUI's Answers screen, which
       records the deletion (local/answers_gone.json) for both machines; one deleted by editing the file comes back
       from the other copy. A file that can't be read (bad JSON, e.g. while you edit it) is left alone.
  local_config.json, profile.json, data/ratings.json   both machines (2026-10-14: either page can change them), merged
       the same way per setting / per job (sync.merge_dict): the side that changed it wins; both: the laptop's (it
       says which); a key one side removed is removed. The laptop's tool paths ("tools") are always the laptop's. An
       older homelab that sends none of them: push() copies the laptop's over, as before. The homelab's page takes a
       changed local_config.json at once (gui.reload_config); the laptop's GUI after its next pull.
  accounts.json (job-site logins)    both machines, with remote_assist, merged the same way: a login is never dropped. If
       each machine made an account on the same site before they merged, the earlier stays the login and the other is
       kept with it ("other_passwords"); the merge names the site. Sent as JSON over ssh, never printed or logged.
  discord_webhook.txt                pushed by push() when the laptop's is newer (the homelab sends the messages).
  transcript (transcript_path)       pushed by push() to local/transcript.pdf (mode 600); the agent there uploads it.
  gmail_token.json                   pushed by push() when the laptop's is newer (`nuauto login google` / `login gmail`).
  your writeups (assist_writeups)    copied by push() to ~/nuauto-notes/<folder name> on the homelab: only the files the
       agent may read anyway (sync.notes_files = assist.secret_path: no keys, tokens, .env, .git, NUauto's local/),
       a folder over 50 MB is skipped. The agent there reads only these and the resume (local/resume.pdf), never the
       homelab's ~/projects (the live NUauto is there).
  assist_profile*/ (site cookies)    each machine its own, never copied; the agent signs in again with the saved login.
  slots, row and site locks          each machine its own; across the two, the claims and the NUworks turn above.
  logs/ (runs, screenshots)          each machine its own: each page's Past runs shows its own.

PORTS (homelab; slot n = 1..3)
  NUauto's page http://<web_listen_host>:8766 (in nuauto-web; the Mark-done tunnel forwards 8765 only)
  virtual screen :40+n (Xvfb, -nolisten tcp)   VNC 127.0.0.1:5940+n (x11vnc -localhost: never on the network)
  browser view http://<web_listen_host>:6080+n/vnc.html (websockify + noVNC; Tailscale only; a password per run)
  slot 0 (:40, VNC 5940, view 6080): a NUworks run started from the page (APPLY ON THE HOMELAB)
  The run folder's screen.log has the three programs' output (a page's run: logs/<stamp>_<kind>_screen/);
  local/screen<n>.json their pids (a crashed run's are stopped by the next run in that slot). The browser tool's own
  files (downloads, snapshots, the resume copy) are in the run folder's browser/, apart from the guard's (a page can't
  plant a Submit answer there).

FROM THE LAPTOP'S GUI (each thing in one place, 2026-10-14)
- Settings > Homelab (a source install), one card: which homelab, Check (over ssh, read-only), Use this homelab, Set it
  up (a terminal window: docs/DEPLOY.md's steps, each asked first), Log in to Claude there, Stop using it; then NUauto on
  your homelab: its page and its assistant, on / off; Pair this computer (asks the homelab for a one-time link over ssh
  and opens it in your browser: press Pair); Open it; its queue's settings. Setup's one optional Homelab step is the
  same.
- Today's "Only you can do these": a homelab session that waits for you is an item there (Open its screen).
- Apply > Assistants: one line, how many assistants run here and on the homelab and how many wait for you, then every
  session with where you answer it; a row the homelab works on has "assistant running on the homelab: open" (its screen). Linked (below), that screen opens in
  this window; else in your browser (the page refuses frames, and its cookie belongs to the browser that paired).
- The homelab's own page has the same three places: Today (its sessions that wait for you), Apply (each row's queue
  chip, Start now, a running session's Open; the Assistants tab: the same line, plus what its queue starts by itself),
  Settings >
  This homelab (its paired devices, its assistant's queue settings).

THE LAPTOP'S LINK (link.py, 2026-10-15, my request: "as much support and smooth interaction between the two as possible")
- Settings > Homelab > This app: Link this app makes the laptop's NUauto one of the page's paired devices: a one-time
  pairing code from the homelab over ssh (`nuauto review pair` there), used up at once by the laptop's server. Its key:
  local/homelab_link.json (600), never shown, never synced; the device is named "NUauto on <this computer>" in the
  page's Paired devices. Unlink forgets it here and unpairs it there; unpaired there (Settings > This homelab), the app
  forgets it at its next request and shows "not linked" until you press Link again.
- The laptop's server makes every request itself, to the page's own address (Tailscale), as that device; the laptop's
  page only ever talks to the laptop's server (/api/homelab/...). What the homelab's page refuses, it refuses here too,
  with the same words: the same rules, checked in one place.
- Linked, the laptop's app also: reads the homelab's sessions, queue and running task through it every 20 s (ssh every
  60 s without it); opens a homelab session's screen inside this window (#/assistant/h<slot>: approve its Submit, reply,
  end it); shows each company-site row's place in the homelab's queue, with "Start on the homelab" next to "Start here"
  (greyed out with the queue's reason when it can't start there now); Apply's "Run on: This computer / The homelab"
  (the homelab's page runs Apply on its virtual screen, one NUworks machine at a time as always; its log, screenshots,
  questions and Stop come here); a run going on the homelab shows "Running on the homelab: Follow it here".
- Not linked (or the page unreachable a moment): everything works as before; the homelab's screens open in your browser.
- Same code on both: both pages show the commit they run (the side bar); with deploy_from_git the Homelab check warns
  when the homelab runs another commit than this checkout (a branch here, or main not pulled yet).

FOR AGENTS
- `nuauto gui --demo --remote --no-open` (or `nuauto review demo`): the homelab's app on 127.0.0.1, on demo data with
  three made-up assistant sessions (review.demo_setup; demo.py's "homelab" rows); prints a pairing link (open it, press
  Pair). Nothing real is read or sent; Reply / End say there is no tmux. `--screenshots DIR`: its screens as
  homelab-*.png (phone and desktop width, light and dark). Tests: tests/test_homelab_app.py, tests/test_review.py.
- `nuauto gui --demo --linked --no-open`: the laptop's demo app linked to a demo homelab page of its own (a child
  `--demo --remote`, both on 127.0.0.1; the pairing codes come from that page, no ssh): Start on the homelab, its
  sessions' screens in the app, Run on: the homelab. Test: tests/test_link.py.
- One page, two places: every screen is opened on the laptop's demo, the linked demo and the homelab's demo
  (tests/screens.py, from test_gui.py, test_link.py, test_homelab_app.py): the same menu, none empty, none failing.

PROVEN FOR REAL (2026-10-11, a practice run on the homelab: a side install of the branch, demo mode's fake identity
and sheet, Selenium's public test form; with the old review page)
- The virtual screen and the browser view (Xvfb + x11vnc + noVNC over Tailscale), its real start/stop test there.
- A whole session: Sonnet filled the form in Playwright's Firefox on the virtual screen, its end list answered from a
  phone on the review page (replies typed into its tmux pane), the Submit asked on the page and approved from the
  phone 18 s later, then End session and "Did it go through?" Yes: the fake sheet row Applied, the queue's record
  "approved, y", the screen stopped by itself.
- Pairing a phone (a link opened from Discord pairs Discord's in-app or incognito browser only: copy it into the
  phone's own browser).
NOT YET PROVEN FOR REAL
- NUauto's page on the homelab at all (2026-10-14: demo mode only, desktop and phone width): a paired phone using it,
  Apply from it (its virtual screen), the NUworks turn between the machines, the settings / ratings merge on real files,
  Settings > Homelab's Check and Set it up on a real homelab.
- The queue starting sessions by itself (nuauto-assist.timer), a laptop claim over ssh, the merge on your real answer
  banks and logins, a real application on the homelab, the laptop GUI's links to the homelab's sessions.
- The laptop's link (demo mode only so far): pairing over ssh, Start on the homelab, an Apply run there followed here.
