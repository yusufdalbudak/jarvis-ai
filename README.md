# Yusuf Revenue Agent

A bounded revenue agent for finding and applying to suitable paid gigs on uGig.

## What it does

- Reads current **hiring** gigs from the public uGig API.
- Filters out low-value, stale, risky, on-site, or clearly mismatched work.
- Scores opportunities against a focused delivery profile: Python, TypeScript/JavaScript, research, technical writing, documentation, data/CSV/JSON, API integration, automation, code review, cybersecurity, and spreadsheet work.
- Checks the authenticated account's existing applications to avoid duplicate applications.
- Applies to at most **one** best-fit gig per run.
- Uses a factual, non-inflated application template and links to `https://github.com/yusufdalbudak` as the portfolio.
- Never prints the uGig API key.

## Safety / quality guardrails

The agent skips work involving credential theft, anti-bot/CAPTCHA bypass, deceptive activity, spam, prohibited scraping, gambling, weapons, malware, fake reviews, account farming, or requests for secrets/private data outside a clearly authorized scope.

It also skips jobs that are obviously outside the current delivery profile rather than applying indiscriminately.

## Required secret

The workflow needs one repository Actions secret:

- `UGIG_API_KEY`

Create the API key in uGig and store it only as a GitHub Actions secret. Do **not** commit it to this repository.

## Current defaults

- Scan cadence: every 6 hours
- Minimum advertised budget: USD 25
- Maximum applications per run: 1
- Remote work only
- Auto-apply: enabled once `UGIG_API_KEY` exists

The workflow can also be run manually from GitHub Actions.
