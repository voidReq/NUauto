ASSIST: the company-site agent (`nuauto assist`), every rule
(Moved from CLAUDE.md on 2026-10-16 to keep what every session loads small; CLAUDE.md keeps the summary. Keep this
current when you change assist.py, accounts.py, mail.py, letter.py or prompts/ASSIST_PROMPT.md. Where each rule is
enforced and tested: docs/SAFETY.md. The homelab side: docs/REMOTE.md. What is proven for real: docs/STATUS.md.)

WHICH ROWS (assist.py + prompts/ASSIST_PROMPT.md, `nuauto assist <row>`; added 2026-10-05)
- Needs Human rows stopped at an external application (Notes start "External application"; Workday, Oracle, iCIMS,
  SuccessFactors...): assist.assist_target. Never a NUworks link (check_link refuses it, even with --url).
- (2026-10-16, my call: "by hand can still have an assistant, since users can do their stuff through the remote desktop")
  Needs Human rows stopped on NUworks: assist.by_hand_target. The row's own NUworks job page only (no --url); never one
  whose Submit click is unresolved, or past its deadline. Such a "by-hand" session is told what stopped NUauto and the
  exact resume label (assist.hand_note; the rules' NUWORKS ROWS): it clicks the job's Apply, fills the popup, uploads
  the transcript or the cover letter, and its Submit asks me like any other. After y: Applied ("on NUworks"), no
  NUworks side after. The weekly cap is checked at its start and the Submit question says it counts.
- Other jobs (added 2026-10-07): `nuauto assist other <row>`: an Approved row of the Other jobs tab (https, never
  NUworks: assist.other_target), same agent, rules and hooks, with the layered answer bank (CLAUDE.md, ANSWER BANK).
  After y: Applied (dated today, no cap), nothing sent to NUworks; n = stays Approved. `nuauto assist other add <url>
  <company> <title>` adds one. GUI: Apply's internships tab.
- NUworks sign-in for by-hand rows (2026-10-23, my call: "i don't wanna sign in each time"): before the agent starts,
  assist.seed_nuworks copies NUauto's own NUworks sign-in from local/session_cookies.json into that slot's browser
  profile: only the cookies a browser sends to NUworks and its two sign-in hosts (assist.nuworks_cookies; session
  cookies given 12 hours), written through the browser tool's own package so the same browser build opens the
  profile. If that is stale: the one click on "Current Students And Alumni"; if that asks for a username, password or
  Duo, it asks me to sign in in its browser (never typed by it).
- One application at a time on NUworks: a by-hand session holds NUworks' site lock (assist.nuworks_session; one by-hand
  session per machine), takes the NUworks turn (remote.nuworks_turn: not beside the other machine's Apply), won't start
  while an Apply run holds the NUworks browser, and `nuauto apply` / `nuauto assist nuworks` refuse while it runs.
- After a company-site row (NUworks tab) is Applied, the same job is submitted on NUworks by apply.submit_nuworks_side
  (the tested NUworks code; every popup check except the off-site-link stop; outcome appended to Notes). Retry:
  `nuauto assist nuworks <row>` (laptop, or the GUI; never from an agent's shell; assist.nuworks_side_blocked refuses a
  second submit once Submit was clicked there). On the homelab it never goes by itself: the row's Notes say "NUworks
  side NOT submitted yet" and Apply's Still owed offers it.

WHAT IT IS
- Laptop, real terminal, me watching (`nuauto assist` needs a tty). Or the homelab (my call 2026-10-11, "remote_assist"):
  in a tmux session, its browser on a virtual screen I open over Tailscale (screen.py), Discord links me to the
  session's screen in NUauto's page there (#/assistant/<slot>; paired devices only). Everything about the homelab (the
  queue, "remote_auto" per tab, hours, claims, "do it on the laptop"): docs/REMOTE.md. Set up in the GUI too.
- Starts `claude --model sonnet --effort low --permission-mode default --strict-mcp-config` (never my auto mode: its own
  check asked about every click; the guard decides; local_config.json "assist_effort" low / medium / high, Settings; low is my call, 2026-10-10). Tools: the Playwright MCP browser (assist.TOOLS, pinned
  assist.MCP_PACKAGE; assist_browser: Chrome on the laptop, Firefox on the homelab), Bash, Read / Glob / Grep, WebSearch
  / WebFetch (company context, my call 2026-10-10). Its prompt: prompts/ASSIST_PROMPT.md plus a "THIS RUN" block
  (assist.context_text: resume, transcript, account email, own login for the site, notes folders, the bank command).
- Browser profile: slot 1 = local/assist_profile/, the others assist_profile_<N>/ (assist.slot_profile). They hold my
  company-site logins: secret. The browser tool's own output (snapshots, downloads) goes in the run folder's browser/,
  never beside the guard's files.
- Log per run: logs/<stamp>_assist_row<N>/ (assist_other_row<N> for Other jobs; actions.log = every guard decision).

THE ONE HARD RULE: nothing is submitted without my review (relaxed 2026-10-05, my call: the agent may visit any site,
fill anything, tick boxes, upload files below, draft longer answers)
Enforced by code: Claude Code hooks -> `assist.py hook pre|post` (matcher: every browser tool, Bash, Read, Glob, Grep,
WebSearch, WebFetch), logic in assist.decide / update_after, tested in tests/test_assist.py. A guard that fails or
times out (60 s; the homelab waits longer for my tap) blocks (hooks' "onFailure": "block"; hook_main blocks on a crash).
- Ask first ("ask": the terminal asks me): every Submit-type click (SUBMIT_RE: Submit, Submit application, Send
  application, Apply, Apply now, Finish, Complete application, Confirm...), Enter (browser_press_key), and type with
  submit=true. Element names come from the latest full snapshot only (assist.parse_refs; refs cleared by anything that
  changes the page), never from the agent's description; a nameless button is checked by its visible text (`button:
  Submit`, Workday). An unknown ref is refused: snapshot again. Every such question also lists, from code
  (assist.review_lines): the guesses I haven't answered, the ones I changed, how many I confirmed, values typed with no
  saved answer or guess behind them (the guard's own record, assist.record_typed), the cover letter's name and size, and
  "counts toward your weekly limit" on a NUworks row.
- Doesn't ask (my calls, 2026-10-10): a sign-in / create-account page's own "Submit" / "Confirm" and Enter there
  (assist.sign_in_page: the latest snapshot has a password box and only account boxes; any other question on the page
  and it asks again); the job posting's own "Apply" / "Apply now" and Enter there (assist.posting_page: the row's link,
  nothing filled in yet, no form boxes; the catch I accepted: a site with one-click apply from a saved profile could
  submit there); Enter in a box on a Workday site (its pages don't submit on Enter; the final Submit is a button).
  Enter anywhere else asks (one-page forms submit on Enter).
- Never: page scripts and any browser tool outside assist.TOOLS (they could submit behind the review); non-web links;
  a box meant for robots ("for robots only", assist.HONEYPOT: filling it marks me as a bot); anything but the files
  below in an upload; Bash other than the answer-bank command (one per call, no shell operators: check_bash).
- Captchas (2026-10-11): any click, typing, hover, select or key inside one is refused (assist.CAPTCHA, captcha_target;
  also a whole frame that holds one). No solving, no disguised browser: it tells me and waits.
- Uploads: only the resume, the transcript (local_config.json "transcript_path"; GUI Settings > Applying keeps it as
  local/transcript.pdf; the homelab gets a copy) and this application's approved cover letter. The launcher copies the
  first two into the run's browser/upload/ (the browser tool uploads only from there); the letter's PDF is made there.
  The guard allows exactly those paths. "Only where a page asks" is in the prompt; code doesn't check that.
- Files: Read / Glob / Grep only inside my resume, the transcript (Read), local_config.json "assist_read_paths" (my
  projects and notes) and "assist_writeups" (the first place it looks for essay material). Never NUauto's local/ folders
  or logs, nor keys, tokens, .env, .git, cookie, password or answer files in them (assist.secret_path; Grep may not
  search a folder that holds a local/).
- Homelab only (config.IS_SERVER): navigating, a tab, WebFetch, or touching a page on my own machines or network
  (localhost, LAN, Tailscale, one-word names; assist.own_network) is refused: the homelab's page and screen live there.

GUESS, THEN ASK ONCE (2026-10-11, my call: "fill the questions with their best guess and then navigate to the end,
asking all their questions at once")
- Answer bank commands (the agent's only Bash; assist.BASH_COMMANDS): answer, guess, guesses, save, once, confirm,
  alias, blank, letter, email, wait, link-login. Answers are one line (300 characters); with options, exactly one of
  them. Each command holds local/answers.lock (no answer lost between sessions).
- A field the bank can't answer gets the agent's best guess (from my resume, notes, writeups, the posting, my saved
  answers), recorded with `guess` and a reason (200 characters). Never over a saved answer, one I gave this run, or a
  leave-blank one. Always-ask questions are guessed too and confirmed with `once`. It asks right away only for
  captchas, email codes, sign-in trouble and a required field it can't guess.
- At the last page `guesses` lists every open guess; it sends me one message, essays in full. My answers: `save` (kept
  for next time; refuses an always-ask question, an already saved one, and today's date, within a week:
  assist.todays_date: use `once`), `once` (this application only), `confirm` (an essay: approved, never saved), `blank`
  (leave this one blank from now on). It fixes the fields I changed, runs `guesses` again, then waits for my review.
- Saved answers first (my call 2026-10-06: "I'd rather agents infer based on answers"). An unknown field's reply lists
  the saved answers that fit best, at most 25, each cut to 140 characters (assist.saved_answers; never always-ask or
  leave-blank ones): first those that are exactly one of its options, then by shared words. If one answers the field
  (other wording, a direct part like city from address, a dropdown option that means the saved answer), the agent uses
  it, says so, and `alias` links the label (answers.add_alias) so the next run matches exactly. Demographic /
  self-identify questions work the same way and are never declined unless that is the saved answer (2026-10-09).
- Longer answers (essays, "why this company"): typed into the field as a draft, recorded as `guess --essay`; I
  approve or edit the full text in the end list.
- Other jobs rows: answers.json minus its NUworks-only entries, after local/answers_other.json; everything saved goes to
  answers_other.json (assist.Bank).

COVER LETTERS (2026-10-16, my call: "approved letters shouldn't be saved, there's a new cover letter per application")
- `letter --para "<paragraph>" ...` records the agent's letter for THIS application (at most 600 words; a new one
  replaces it) as an essay guess, shown in full in the end list. After I approve it, `confirm "cover letter"` makes the
  PDF (letter.py: plain text, Helvetica, letter size) in the run's browser/upload/: the one cover letter the guard lets
  be uploaded. The session's end removes the PDF and its text (assist.after_session). Never in the answer bank. Works
  on company sites and NUworks.

ACCOUNTS (added 2026-10-10, my call; accounts.py)
- The agent makes the accounts job sites ask for, with local_config.json "accounts_email" (else the answer bank's email)
  and a random 16-character password per site that NUauto makes (16: 20 broke sites capped at 16 or 18). Kept in
  local/accounts.json (600, under a lock, never synced; with remote_assist merged with the homelab's, sync.share).
- It types {{NEW_PASSWORD}} / {{PASSWORD}} alone into a field the latest snapshot names a password
  (assist.password_check); nothing else goes into a password box. The guard types the real one (updatedInput) and
  takes it out of the browser tool's replies and snapshots ("<password hidden by NUauto>", updatedToolOutput) and the
  run's logs; after the session assist.after_session scrubs it from Claude Code's session log and the run folder.
- Never on Google / Microsoft / Apple / LinkedIn / GitHub / Northeastern sign-ins, nor the other providers in
  accounts.NEVER (accounts.never). "Sign in with Google" buttons are mine.
- A site is its host; on a host companies share (Workday's myworkdaysite.com, Greenhouse / Lever / Ashby / Workable)
  host/<company> (accounts.site; assist.site_key). An Other jobs row with an internship list's short link
  (go.vansh.nyc/...) is its posting's own link once the internship update knows it (assist.other_target ->
  intern.real_url), so its site is the job site, not the shortener (2026-10-26).
- `nuauto accounts export` (or Settings) writes Bitwarden's import file (one login per site, matched on its host; the
  jobs in its notes); `--all` every login again. `nuauto accounts export --bitwarden` (2026-10-11) puts the logins
  NUauto made and Bitwarden doesn't have yet straight into my vault with `bw` (I unlock it: BW_SESSION; NUauto never
  sees my master password; passwords only on bw's stdin; folder "NUauto job sites"; with a homelab both machines'
  logins).
- My own logins (2026-10-11, my request): `nuauto accounts import <file>|- --only <text>` takes only the Bitwarden
  logins whose address has that text (best: `bw list items --search <site> | nuauto accounts import - --only <site>`).
  Saved "own" (they replace one NUauto made there), used with {{PASSWORD}} and the email they hold, never a new account
  on that site, never exported back. An own login on a non-shared domain counts for the whole domain.
- A sign-in page on another address than the company's usual one: the guard names my own login(s) sharing a company word
  with it and refuses both a sign-in and a new account; the agent runs `link-login <site>`, which asks me first
  (terminal / its screen on the homelab page); then that login works there too ("also" in accounts.json). Never a login
  chosen by the agent alone (2026-10-11).

EMAIL CODES AND LINKS (2026-10-11, my call; mail.py)
- The agent runs `email --want code|link`. mail.py (not the agent) reads my Gmail with its own read-only login
  (`nuauto login gmail`, local/gmail_token.json, 600, pushed to the homelab with remote_assist; `nuauto login google`
  renews it too; it lasts 7 days; it refuses any Gmail but accounts_email's, checked on every lookup). Only mail of the
  last 15 minutes that names the site the agent is on (its company, or a link to that very host), polled up to 75 s.
  It hands back one code, or one link to that same site; nothing else from any message reaches the agent or the logs
  (the log says from whom, never the code). A message is used once per run.
- Replies: code, link, or none / ambiguous / refused (an identity provider or Northeastern) / no_email_access (no login,
  expired, Gmail down): for those it asks me to read the email.

NEEDS YOU (2026-10-11, my request)
- When a session waits for me (its turn ended: a question, the end list) or shows a permission question (a Submit to
  approve), a desktop notification says so, with the agent's first line (Discord on the homelab); the GUI's chip says
  "waiting for you" / "approve in its window". Claude Code's own Stop / Notification / UserPromptSubmit hooks
  (`assist.py attention`; the agent can't call it) write the run folder's attention.json, cleared when I answer or a
  browser call finishes; it never blocks or changes a turn. Idle reminders are left out.

SIDE BY SIDE (2026-10-10, my call)
- Up to 3 sessions at once (local_config.json "assist_slots" 1-3; assist.slots). Each holds a slot (local/assist_slot<N>.lock,
  with what runs there), a row lock (local/assist_rows/) and a job-site lock (local/assist_sites/; one session per
  site: two could make the same account at once; the site is assist.site_key, each company on a shared board its own
  site, 2026-10-12), until its run ends. From the laptop with remote_assist, the homelab's claims are asked first
  (remote.claim_on_homelab): never a row or site the other machine holds. The GUI's Apply shows which rows have a
  session (assist.sessions); its Assistants tab lists them all.

THE END, AND THE HOMELAB'S APPROVALS
- After I /exit, the launcher asks "did you press Submit?": y = Applied (dated today; a NUworks-tab row counts toward
  the weekly limit), n = the row stays. The agent never touches the sheet.
- Homelab: a Submit-type action has no terminal to ask in. The guard puts it on NUauto's page (a screenshot, and
  Discord), and waits up to 9 minutes for my tap. Decline or no answer blocks it. A late Approve is a pass for that
  one action, on that page, with the same typed values, for 10 minutes. Anything typed meanwhile, or another page, voids
  the question or pass. Captchas I solve on its screen. After the session its page asks "did it go through?" (waits
  24 h, then leaves the row as it was); the queue never restarts a row whose approved Submit has no outcome.
- A site that refuses the homelab's browser (a fake sign-in page): "End: do it on the laptop" marks the row's Notes
  (assist.LAPTOP_MARK, sheet.mark_on_laptop); the homelab skips it and the GUI shows "do it on the laptop" (2026-10-11;
  no disguising the browser).
