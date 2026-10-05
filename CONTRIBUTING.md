# Contributing to NUauto

NUauto helps one student apply to NUworks co-op jobs. The human approves every job. Changes
that make it apply without that approval will not be merged.

## Setup

Follow the Setup section of `README.md`. The key step is the editable install, so code changes
take effect without reinstalling:

```sh
.venv/bin/pip install -e .
nuauto test        # runs every tests/test_*.py
```

## Workflow

1. Branch off `main`. Never commit to `main`: it only receives merges.
2. Run `nuauto test` before you push. It must pass.
3. Open a pull request. Keep it small and say what you checked.

## Code rules

- **Absolute imports only:** `from nuauto import sheet`. `tests/test_sync.py` fails on relative imports.
- **New module or prompt:** add it to `PACKAGE` (modules) or `CODE` (prompts, docs) in
  `src/nuauto/sync.py`. Otherwise it never reaches the optional server, and `test_sync.py` fails.
- **New `nuauto` command:** a user-facing command goes in `COMMANDS` in `src/nuauto/cli.py`, with a branch
  in `main()` and a line in the docstring. A tool that is just a module's own command line goes in
  `TOOLS` instead (it runs that module's `if __name__ == "__main__":` block).
- **Tests are offline only:** no Google Sheet, no browser, no network. Each `tests/test_*.py` is a
  plain script that asserts and exits non-zero on failure. Run one with
  `.venv/bin/python tests/test_sheet.py`.
- **Config and paths** live in `src/nuauto/config.py`. Personal settings come from
  `local/local_config.json`. When you add a setting, also add it to `local_config.example.json`.

## Safety rules you must not weaken

Details and the reasons are in `docs/SAFETY.md`.

- Only rows marked Approved in the sheet are ever acted on.
- The weekly and total caps in `src/nuauto/sheet.py` (`MAX_PER_WEEK`, `MAX_TOTAL`) stay enforced in code.
- The browser stays locked to NUworks (`config.ALLOWED_HOSTS`). Single sign-on hosts are allowed only
  during the one-click re-login.
- The answer bank matches question text exactly. No fuzzy matching. Unknown fields stop and ask.
- `nuauto apply` never writes free text (essays, cover letters).
- No code reads, stores or asks for a password.
- `nuauto assist` submits nothing without the human's review. The guard is the hook logic in
  `src/nuauto/assist.py`, tested by `tests/test_assist.py`. Change both together.
- Every action is logged and every filled form is screenshotted to `logs/` before Submit.

## Privacy

This repo is public. Tracked files and commit messages must not contain names, emails, hostnames,
IPs, IDs, or the companies you applied to. Put those in `local/` (gitignored) or `docs/STATUS.md`
(gitignored). Never print or log anything in `local/` that is a secret (tokens, cookies, webhooks).

## Docs

Update the doc that matches your change:

- `README.md`: setup and commands for a new user. Keep it short.
- `docs/DEPLOY.md`: the optional server, units, secrets, sync.
- `docs/PIPELINE.md`: how the job pool is built, bounds, ranking.
- `docs/ARCHITECTURE.md`: how the modules fit together.
- `docs/SAFETY.md`: the safety rules and how code enforces them.
