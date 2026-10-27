# NDHD: congressional trades, day plan for Claude Code

Paste this whole file into Claude Code, started in `F:\Ex_Data\projects\NDHD-Trading`, or tell it: "Read prompts\claude-code-day-plan.md and carry it out."

---

## Your role and how to work

You are working in my personal trading project, NDHD (Windows, PowerShell, Python 3.14 in `.venv`). Act as a senior engineer and a risk-aware investor at the same time.

- Before any change, read `.github/skills/ndhd-trading-investor/SKILL.md` and `.github/skills/ndhd-trading-developer/SKILL.md` and follow them. The investor skill decides **whether** something is allowed; the developer skill says **how** to build it.
- Start in plan mode. Show me your plan for the whole day, then work phase by phase.
- Keep a task list and tick items off as you go.
- **Stop and ask me** at every point marked **CHECKPOINT**. Do not continue past one without my "yes".
- Never touch `.env`, never print or commit credentials, and never set `LIVE_TRADING=true`.
- I already have uncommitted edits in `portfolio_engine.py`, `alpaca_wheel_strategy.py`, `.gitignore` and `portfolio_engine.log.clean`. They are mine. Don't revert them, and don't commit them unless I say so.
- Tell me when my request or premise is wrong. I'd rather be corrected than agreed with.
- At the end of each phase, give me 3–5 lines: what changed, which commands you ran, their results, and what's next.

## Background (already done, do not redo)

I want to track stock trades that US officials disclose under the STOCK Act and use them as research input for NDHD. The rules:

- **Both parties.** Party is a filter, never a scope limit.
- **Rescan the last 60 days of filings on every run.** Filings may arrive up to 45 days after a trade.
- **Personal, non-commercial use only.** The Senate site forbids commercial use. Keep `data/` out of git.
- Filings report **dollar ranges**, not share counts. Any dollar figure we compute is a range-midpoint **estimate** and must be labelled that way.
- **The "why" is never disclosed.** We attach evidence (committee seats, bills, contracts, news) and a labelled "possible explanation" plus an ordinary explanation. We never state motive as fact.
- The dashboard must be easy enough for a 10-year-old to read.

Already in the repo:

| Path | What it is |
| --- | --- |
| `congress_trades/normalize.py` | Parses, classifies, deduplicates, summarises; standard library only |
| `congress_trades/build_dashboard.py` + `dashboard_template.html` | One command writes `data\congress_trades.json` and `data\congress-trade-ledger.html` |
| `congress_trades/sectors.py` | Ticker to sector map, plus tickers known to be wrong at source |
| `congress_trades/test_*.py` | 13 passing tests |
| `congress_trades/sample/raw_congressflow.csv` | Sample input from one aggregator, marked `unverified` |
| `data/.gitignore` | Keeps generated output out of git |
| `requirements.txt`, `requirements-api.txt`, `requirements.lock.txt` | Exact pins; the lock is hashed for Windows and Python 3.14 |
| `skill-updates/*.md` | Updated versions of both skills, not yet copied into `.github/skills` |

Design plan: https://claude.ai/code/artifact/c7c4bb32-f312-42aa-ba97-9b378d91d6e1 (reference only; the skills are the source of truth).

## Phase 0: check the starting state

1. Run `python --version`, which should report 3.14.x. Run `pip check`, then `python -m unittest discover -s congress_trades -t .`, which should pass all 13 tests.
2. Run `python -m py_compile portfolio_engine.py alpaca_wheel_strategy.py`.
3. Run `git status` and `git branch`. Report anything unexpected.

## Phase 1: install the updated skills

1. Diff `skill-updates\ndhd-trading-developer.md` against `.github\skills\ndhd-trading-developer\SKILL.md`, and do the same for the investor skill. Summarise the differences for me in a few lines.
2. **CHECKPOINT:** ask me to approve. Then copy both into place and delete `skill-updates\`.
3. Re-read both skills. From here on, they govern everything.

## Phase 2: replace the sample with official filings (House first)

Goal: rows marked `source_status = "verified"`, taken from the official House source and not from an aggregator.

1. Investigate the House Clerk financial disclosure site (`https://disclosures-clerk.house.gov/FinancialDisclosure`). It offers a yearly bulk download (`{YEAR}FD.zip`, containing an XML or TXT index of filings) and one PDF per Periodic Transaction Report (filing type `P`). Confirm the current URLs and file layout before writing code; don't trust my description.
2. Write `congress_trades/fetch_house.py`, which:
   - downloads the current year's index, plus last year's while the 60-day window crosses New Year;
   - selects PTR filings from the last 60 days and downloads each PTR PDF into a cache folder under `data\`;
   - is polite: a single thread, a pause between requests, and a clear User-Agent;
   - never re-downloads a PDF it already has.
3. Reading the PDFs needs a PDF library. **CHECKPOINT:** propose one library with its exact version and a known-vulnerability check. Don't install anything until I approve. Once approved:
   - add it to `requirements.txt`, pinned with `==`;
   - regenerate `requirements.lock.txt` with hashes, using the command in the developer skill;
   - scan the lock for vulnerabilities;
   - reinstall with `pip install --require-hashes -r requirements.lock.txt`.
4. Write `congress_trades/parse_ptr.py`, which turns each PTR into rows in the same shape `normalize.py` expects, plus `owner`, `filing_id` and `filing_url`.
   - Electronic PDFs must parse reliably.
   - Scanned or hand-written PDFs must be **flagged `needs_review` and skipped**, never guessed.
   - Never guess a ticker.
5. Extend `normalize.py` so it can merge official and aggregator rows:
   - official rows are `verified`;
   - an aggregator row that matches an official row is dropped;
   - an aggregator row with no official match stays `unverified`.
6. Tests: a fixture PTR text, the scanned-PDF path, the 60-day window crossing New Year, amendments (a newer filing that supersedes an older one), and deduplication between official and aggregator rows.
7. Run the full build and show me the new counts, including how many rows are verified and how many need review.

**Senate (investigate only, don't build):** the Senate eFD site (`https://efdsearch.senate.gov`) requires accepting a use agreement before searching. Report whether automated access is technically possible **without** getting around that agreement, a captcha or a login, and stop there. Senate rows stay on the aggregator (`unverified`) until I decide.

## Phase 3: level-2 advisory context in the engine

Build exactly what the developer skill's "Integrating `congress_trades` into an engine" section describes, and nothing more.

- `congress_trades/context.py` provides `context_for(symbol, as_of)`. It uses verified rows only and times everything by `filing_date`.
- Add a `Config` field `CONGRESS_CONTEXT_ENABLED`, **off by default**.
- When the flag is on, `portfolio_engine.py` logs, and alerts through `EmailAlerter.send(..., key=f"congress:{symbol}")`, when it holds or is about to enter a symbol that had a recent cluster of lawmakers trading it or a committee-overlap trade.
- A missing, stale or broken data file does nothing except log one warning per day.
- Changes **no** order, size, stop or `RiskEngine` decision. Nothing goes into `alpaca_wheel_strategy.py`.
- Tests: missing file, stale file, bad JSON, a row usable only by trade date (must be ignored), an unverified row (must be ignored), and the flag off by default.
- Show me the diff to `portfolio_engine.py`, and confirm it stays clear of my uncommitted edits there.
- Fill in both skills' completion checklists in your phase summary.

**CHECKPOINT:** paper-trading check. Confirm `.env` has `ALPACA_PAPER=true` and `LIVE_TRADING=false` without printing any values. Then ask me before starting `python portfolio_engine.py` for a short observation run with `CONGRESS_CONTEXT_ENABLED=true`. Report what the logs show.

## Phase 4: guard rails in GitHub

1. Add `.github/dependabot.yml`: weekly pip updates, plus GitHub Actions updates.
2. Add `.github/workflows/ci.yml`, running on Windows with Python 3.14. It should:
   - install from the hashed lock;
   - run `py_compile` on both engines;
   - run the `congress_trades` tests;
   - run a vulnerability scan of the lock that fails the build on any known vulnerability.

   Pin every action to a full commit SHA, not a tag.
3. Update the root `README.md` with:
   - Python 3.14;
   - the new requirements files;
   - a short `congress_trades` section linking to its README.

   Remove the README's reference to a `requirements.txt` that previously didn't exist only if it's now correct.

## Phase 5: commit (and push only when I approve)

1. Create the branch `feature/congress-trades`.
2. Stage **only** these:
   - `congress_trades/`, including `sample/`;
   - `data/.gitignore`;
   - the three requirements files;
   - `.github/skills/*` and `.github/dependabot.yml`;
   - `.github/workflows/ci.yml`;
   - `README.md`;
   - the Phase 3 changes to `portfolio_engine.py`, staged hunk by hunk so my own uncommitted edits stay out. If the hunks can't be cleanly separated, stop and ask.
3. Show me `git diff --cached --stat` and a scan confirming that no `.env`, keys, `data/` output or log files are staged.
4. Commit in logical pieces (data pipeline; official House source; engine advisory context; CI and dependency hygiene), each with a clear message.
5. **CHECKPOINT:** ask before `git push -u origin feature/congress-trades`. After pushing, offer to open a pull request with a description that lists the investor-skill decisions and what was tested. Don't merge.

## Out of scope today (don't do these)

- Scheduling the daily run, choosing its run time, or setting up any background job. We'll decide that later.
- Any use of the data that sizes, triggers or blocks a trade (levels 3 and up in the investor skill).
- Live trading or any change to live configuration.
- Python 3.15, which is still a release candidate.

## Definition of done

- All tests pass on Python 3.14, and CI is green on the branch if it was pushed.
- `python -m congress_trades.build_dashboard ...` produces a dashboard whose rows are mostly `verified` House data. Senate rows are clearly `unverified`.
- The engine runs in paper mode with the advisory flag on and makes no trading decisions differently.
- Both skills are installed and accurate.
- Your final message lists:
  - what changed;
  - every command you ran with its result;
  - anything skipped and why;
  - open questions for me.
