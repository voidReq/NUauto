ASSIST: the company-site agent (`nuauto assist`), every rule
(Moved from CLAUDE.md on 2026-10-16 to keep what every session loads small; CLAUDE.md keeps the summary. Keep this
current when you change assist.py, accounts.py, mail.py or prompts/ASSIST_PROMPT.md. Where each rule is enforced and
tested: docs/SAFETY.md. The homelab side: docs/REMOTE.md.)

COMPANY-SITE AGENT (assist.py + prompts/ASSIST_PROMPT.md, `nuauto assist <row>`; added 2026-10-05)
- Only for Needs Human rows stopped at an external application (Notes start "External application";
  Workday, Oracle, iCIMS, SuccessFactors...). Never NUworks itself (refused even with --url), never rows
  stopped for something on NUworks (cover letter, transcript...): those are mine (assist.assist_target).
- Laptop, real terminal, me watching; or (Phase 3, my call 2026-10-11) the homelab, in a tmux session I attach to, with
  "remote_assist" on: its browser on a virtual screen I open over Tailscale (screen.py), Discord links me to its screen
  in NUauto's page on the homelab (#/assistant/<slot>; the old review page until 2026-10-14; Tailscale only, paired
  devices only): I answer it, approve or decline each Submit (the guard waits
  for my tap; a late Approve = a 10-minute pass for that one action) and get captchas there. With "remote_auto" (each tab
  its own switch since 2026-10-13: {"nuworks": .., "other": ..}; the queue shows in that page's Apply) the
  homelab starts sessions by itself from my approved rows (remote.py: 09:00-22:00, free slots, never a row that had a
  session before, never a row or site the laptop holds: claims over ssh). A site that refuses the homelab's browser
  (a fake sign-in page): "End: do it on the laptop" marks the row's Notes (assist.LAPTOP_MARK); the homelab skips it
  and the GUI shows "do it on the laptop" (my request, 2026-10-11; no disguising the browser). The GUI's Apply shows them (each row
  opens its session's screen; its Assistants tab lists them all and counts them; Today lists the ones waiting for me). Everything optional and set in
  the GUI too (my request, for other users: setup's optional steps and Settings: one Homelab card (which homelab, NUauto
  on it, pairing), Password manager (Bitwarden) with its status chip). Each thing in one place, with or without a
  homelab (my request, 2026-10-14: "not redundancy between any of the pages"). Proven with a
  practice run on 2026-10-11 (docs/REMOTE.md). Starts `claude --model sonnet --effort low --permission-mode default` (never my auto
  mode: its own check asked about every click; the guard decides; local_config.json "assist_effort":
  low / medium / high; Settings; low is my call, 2026-10-10) with the Playwright MCP browser (profile assist_profile/:
  it holds my company-site logins, treat it as secret), Bash, Read/Glob/Grep and WebSearch/WebFetch (company context,
  my call 2026-10-10).
- Relaxed 2026-10-05 (my call): the agent may visit any site, fill anything, tick boxes, upload the
  resume, and draft longer answers (since 2026-10-11 typed in as drafts that I approve in the end list). The one hard rule: NOTHING is
  submitted without my review. Enforced by code (Claude Code hooks -> `assist.py hook pre|post`, logic
  in assist.decide / update_after, tested in tests/test_assist.py): every Submit-type click (SUBMIT_RE, incl.
  "Apply"), Enter and type(submit) make the terminal ask me first ("ask"); element names come from the
  latest full snapshot only (refs cleared by anything that changes the page), never from the agent's
  description. Never: page scripts (could submit behind the review), uploads other than
  the resume and the transcript (the launcher copies them into the run's log folder, logs/<run>/browser/upload/, because
  the browser tool only uploads from there; only those copies are allowed), non-web links; Bash only the answer-bank command (answer|guess|guesses|save|once|confirm|alias|blank|wait);
  Read/Glob/Grep only inside my resume, local_config.json "assist_read_paths" (my projects and writeups: mine is
  ~/projects, 2026-10-10) and "assist_writeups" (the first place it looks for essay material: mine is my site's
  project and security writeups, 2026-10-11), and never NUauto's local/ folders or logs, keys, tokens, .env or .git files in them
  (assist.secret_path; Grep may not search a folder holding a local/). A guard that fails or times out (60 s) blocks
  the action (hooks' "onFailure": "block").
- Guess, then ask once (added 2026-10-11, my call: "fill the questions with their best guess and then navigate to the
  end, asking all their questions at once"): a field the answer bank can't answer gets the agent's best guess (from my
  resume, notes, writeups, the posting, my saved answers), recorded with `guess` (a reason; never over a saved answer,
  one I gave this run, or a leave-blank one; always-ask questions are guessed too and confirmed with `once`). It asks
  right away only for captchas, email codes, sign-in trouble and a required field it can't guess. At the last page it
  lists every open guess in one message (`guesses`), essays in full; my answers are saved (save; an essay is only
  confirmed, never saved), it fixes the fields I changed, then waits for my review. Every Submit-type question also lists,
  from code (assist.review_lines): the guesses I haven't answered, the ones I changed, how many I confirmed, and values
  typed with no saved answer or guess behind them (the guard's own record, assist.record_typed). Proven with a real
  Sonnet session on a local practice form (guesses, one end list, a correction saved and fixed, the Submit question's
  list); not yet on a real application.
- Accounts (added 2026-10-10, my call): the agent makes the accounts job sites ask for (Workday...), with
  local_config.json "accounts_email" (mine: my Gmail, in local_config only) and a random password per site that NUauto
  makes (accounts.py; local/accounts.json, 600, under a lock, never synced; with
  remote_assist merged with the homelab's over ssh, sync.share). It types {{NEW_PASSWORD}} / {{PASSWORD}}
  alone into a field the latest snapshot names a password; the guard types the real one (updatedInput) and takes it
  out of the browser tool's replies and snapshots (updatedToolOutput: "<password hidden by NUauto>"), NUauto's run
  logs and, after the session, Claude Code's session log. Never on Google / Microsoft / Apple / LinkedIn / GitHub or
  Northeastern sign-ins (accounts.never). `nuauto accounts export` (or Settings) writes Bitwarden's import file (one
  login per site, matched on its host; the jobs in its notes). Proven end to end with a real session on a local page.
  My own logins (added 2026-10-11, my request: my Workday and Amazon jobs accounts are in Bitwarden): `nuauto accounts
  import <file>|- --only <text>` takes only the Bitwarden logins whose address has that text (best: `bw list items
  --search myworkday | nuauto accounts import - --only myworkday`, no whole-vault export); saved "own" in accounts.json
  (they replace one NUauto made there), used with {{PASSWORD}} and the email they hold, never a new account on that
  site, never exported back. `nuauto accounts export --bitwarden` (my request, 2026-10-11) puts the logins NUauto made
  and Bitwarden doesn't have yet straight into my vault with `bw` (I unlock it: BW_SESSION; NUauto never sees my master
  password; passwords only on bw's stdin; folder "NUauto job sites"; one already there not added twice; with a homelab
  both machines' logins). Sites: the host; on Workday's shared hosts (wd1.myworkdaysite.com/.../recruiting/<company>)
  host/<company>; my own login on another domain counts for the whole domain (account.amazon.jobs = amazon.jobs). A
  sign-in page on yet another address (Amazon jobs' passport.amazon.com): the guard names my own login(s) sharing a
  company word with it and refuses both a sign-in and a new account; the agent runs `link-login <site>`, which the guard
  asks me first (terminal / its screen on the homelab page), then my login works there too ("also" in accounts.json). Never a login chosen
  by the agent alone (my call, 2026-10-11).
- Sign-in pages (added 2026-10-10, my call): a sign-in / create-account page's own "Submit" doesn't ask (the latest
  snapshot has a password box and only account boxes: assist.sign_in_page); any question on the page, or "Apply" /
  "Submit Application", still asks. The agent never types into a bot trap ("for robots only"). Also my call
  (2026-10-10): the job's own posting's "Apply" / "Apply now", and Enter there or on a sign-in page, don't ask
  (assist.posting_page: the row's link, nothing filled in yet, no form boxes); the catch I accepted: a site with
  one-click apply from a saved profile could submit there. On a Workday site, Enter in a box doesn't ask either (its
  pages don't submit on Enter; the final Submit is a button), my call 2026-10-10. Enter anywhere else asks (one-page
  forms submit on Enter).
  Buttons with no name but their text (`button: Submit`, Workday) are checked by that text.
- Email codes and verification links (added 2026-10-11, my call): the agent runs `email --want code|link`; code (mail.py, not
  the agent) reads my Gmail with its own read-only login (`nuauto login gmail`, local/gmail_token.json, 600, never synced (with remote_assist
  pushed to the homelab);
  `nuauto login google` renews it too), only mail of the last 15 minutes that names the site the agent is on (its company,
  or a link to that very host), and hands back one code, or one link to that same site; nothing else from any message
  reaches the agent or the logs. Never for Google / Microsoft / Apple / LinkedIn / GitHub / Northeastern sign-ins
  (accounts.never). None found, two candidates or no login: it asks me. Setup once: enable the Gmail API in my Google
  Cloud project, then `nuauto login gmail` (it refuses any Gmail but accounts_email's, and every lookup checks it).
  Checked on my real inbox (2026-10-11): Workday's verification links found, each to its own site; "thanks for
  applying" emails skipped. Not yet seen: an emailed code, and the agent using it in a live application.
- Mine: email codes it can't find, captchas, approving Submit. Captchas are enforced (2026-10-11): the guard refuses any
  click, typing, hover or key inside one (assist.CAPTCHA, captcha_target); no solving, no disguised browser. After I /exit, the launcher asks "did you submit?"; y =
  Applied (dated today, counts toward the weekly limit), then the same job is submitted on NUworks too by
  apply.submit_nuworks_side (the tested NUworks code; every popup check except the off-site-link stop;
  outcome appended to Notes; retry: `nuauto assist nuworks <row>`). On the homelab never by itself: the row's Notes say
  "NUworks side NOT submitted yet" and Apply's Still owed (the laptop's page or the homelab's, since 2026-10-14) offers
  Submit on NUworks too. The agent never touches the sheet.
  Log per run: logs/<stamp>_assist_row<N>/ (actions.log = every guard decision).
- Side by side (added 2026-10-10, my call): up to 3 sessions at once (assist.slots). Each takes a slot (lock
  local/assist_slot<N>.lock with what runs there; slot 1 = assist_profile/, the others assist_profile_<N>/), a row lock
  and a job-site lock (one session per site: two could make the same account at once; the site is assist.site_key:
  each company on a shared board, Greenhouse / Lever / Ashby / Workable / myworkdaysite.com, is its own site, 2026-10-12); all held until its run ends.
  Answer-bank commands and the Answers screen's save hold local/answers.lock (no answer lost between sessions). The
  GUI's Apply shows which rows have one running (assist.sessions), and its Assistants tab every session.
- Needs you (added 2026-10-11, my request): when a session waits for me (its turn ended: a question, the end list) or
  shows a permission question (a Submit to approve), a desktop notification says so, with the agent's first line;
  the GUI's chip says "waiting for you" / "approve in its window". Claude Code's own Stop / Notification /
  UserPromptSubmit hooks (`assist.py attention`; the agent can't call it) write the run folder's attention.json,
  cleared when I answer or a browser call finishes; it never blocks or changes a turn. Idle reminders are left out.
  The same record is what a phone or Discord view would read later.
- Other jobs (added 2026-10-07): `nuauto assist other <row>` runs the same agent, rules and hooks on an Approved
  row of the Other jobs tab (its URL column; https, never NUworks: assist.other_target), with the layered answer
  bank (ANSWER BANK). After /exit, y = Applied (dated today, no cap), and nothing is sent to NUworks; n = stays
  Approved. Log: logs/<stamp>_assist_other_row<N>/. GUI: Apply's internships tab (the sheet's Other jobs tab, since
  2026-10-16; Start assistant, Open site, More: I applied myself, Drop, Move, Remove; Add a job). Not yet run for real.
- Proven: a first application end to end (2026-10-05, stricter version). The "ask before Submit" hook
  prompt is proven in an interactive session; a full run with the relaxed rules is not yet.
