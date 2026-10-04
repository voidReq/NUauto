# nuauto

Finds Northeastern co-op postings on NUworks that fit your resume, lets you approve them, and
submits the approved ones for you. Not affiliated with Northeastern, NUworks or Symplicity. Not a mass-apply bot: you approve every job, and it applies
to at most 11 per week (99 total).

## How it works

1. `nuauto update` pulls new postings, has Claude score them against your resume, and ranks them.
2. `nuauto approve` shows the best ones. `y` adds a job to your Google Sheet as Approved.
3. `nuauto apply` opens NUworks in Firefox and submits each Approved job, one at a time.

It stops and asks you when a form has a question with no saved answer, and never writes free
text (essays, cover letters). Jobs that send you to an outside site are marked Needs Human.

## Setup

Needs Linux or macOS, Python 3.12, a NUworks account, a Google account, and
[Claude Code](https://claude.com/claude-code) (a Claude subscription or API key; scoring uses Sonnet).

```sh
git clone <this repo> nuauto && cd nuauto
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install firefox
ln -s "$PWD/nuauto" ~/.local/bin/nuauto   # ~/.local/bin must be on your PATH
cp local_config.example.json local_config.json
curl -fsSL https://claude.ai/install.sh | bash   # Claude Code, if you don't have it
claude                                           # log in once, then /exit
```

1. **Google:** in Google Cloud, enable the Sheets API and create an OAuth client ("Desktop app").
   Save its JSON here as `client_secret.json`.
2. **Sheet:** make a blank Google Sheet, put its ID (from the URL) in `local_config.json` as
   `sheet_id`, then run `.venv/bin/python setup_sheet.py`.
3. **Resume:** set `resume_path` in `local_config.json` to your resume PDF, and create
   `profile.json` with `{"resume_label": "..."}`: the exact name of that resume in NUworks'
   Apply popup.
4. **NUworks:** `nuauto login`, log in in the window that opens, then close it.
5. `nuauto doctor` to check everything, then `nuauto update`.

Optional: a Discord webhook URL in `discord_webhook.txt` for notifications.

## Commands

```
nuauto update          find and score new jobs
nuauto approve         review the best jobs (y approve, n no, s skip, q quit)
nuauto rate            y/n on jobs to teach the ranking your taste
nuauto apply [-n 3]    submit Approved jobs (run in a real terminal)
nuauto status          sheet counts and this week's limit
nuauto login [google]  NUworks login / Google login (Google's lasts 7 days)
nuauto doctor          health check
nuauto test            offline tests
```

## Make it yours

Your preferences live in `RULES` and the term ID in `jobs.py` (term, class level, thresholds,
bonuses) and in the Claude prompts `*_PROMPT.md`. See `docs/PIPELINE.md`.

To run updates on a server twice a day instead, fill in the server fields in
`local_config.json` and follow `docs/DEPLOY.md`. Leave them empty and everything runs locally.

Check NUworks' terms of use before using this. Secret files (`token.json`, `client_secret.json`,
`session_cookies.json`, ...) are gitignored; keep it that way.
