# candly — working notes for Claude

candly forecasts candlesticks for Indian markets (NSE, BSE, MCX), using patterns plus market context. Before changing anything, read **PLAN.md** (what was decided and why) and **docs/CONTRACTS.md** (the data shapes every module must follow).

## Machine quirks (read first)

- Windows 10 with PowerShell 5.1: `&&` and `||` don't work. Chain commands with `;`, and use `if ($?) { ... }` for conditional steps.
- **Seqrite antivirus** kills any shell process that downloads a program: installers, a `.zip` containing `.exe`/`.dll` files, or a binary Python wheel. It also flags powershell.exe.
  - Never download binaries from the shell.
  - Never run `pip install` or `npm install` until the user has paused Seqrite Behavior Detection. Ask them first.
  - Text, JSON and RSS downloads are fine.
- If `python`, `node` or `git` is "not found", VS Code hasn't picked up the new PATH. Ask the user to restart VS Code.

## Layout

```
backend/                 Python package `candly` (src layout); tests in backend/tests
  src/candly/core        settings, timeframes, instruments, calendar, schema, log     (lead)
  src/candly/data        broker adapters, candle store, cleaning, ingest CLI          (backend-engineer)
  src/candly/news        RSS fetch, instrument mapping, sentiment, news store          (backend-engineer)
  src/candly/jobs        scheduler jobs                                                (backend-engineer; lead wires quant jobs)
  src/candly/api         app.py (lead), routes/platform.py (backend), routes/analytics.py (quant)
  src/candly/{indicators,patterns,features,research,forecast,ledger}                   (quant-engineer)
frontend/                Vite + React + TypeScript dashboard                           (frontend-engineer)
config/                  YAML: watchlist, markets, costs, research protocol, news feeds, patterns
data/                    gitignored: candles/ (Parquet), db/ (SQLite), derived/, logs/, secrets/
docs/CONTRACTS.md        API and module interfaces. Change it first, and say so in your report.
```

## Commands (run from the repo root)

Backend (venv at `backend/.venv`):
- Tests: `backend\.venv\Scripts\python -m pytest backend -q`
- Lint: `backend\.venv\Scripts\python -m ruff check backend`
- API server: `backend\.venv\Scripts\python -m uvicorn candly.api.app:app --reload --port 8000`
- Ingest: `backend\.venv\Scripts\python -m candly.data.ingest --tf 1D`

Frontend (run inside `frontend/`):
- Dev server: `npm run dev`. Serves http://localhost:5173 and proxies `/api` to port 8000.
- Build and type-check: `npm run build`
- Lint and tests: `npm run lint ; npm run test`

## Rules that protect the results (non-negotiable)

- **Time:** UTC everywhere in code, storage and the API; JSON uses UNIX seconds. IST is only for display. A candle's `ts` is the bar's **open** time. For a daily bar, `ts` is that day's session open.
- **Features are causal:** the value at bar t may use only bars up to and including t. Every new feature gets a truncation test.
- **Labels and outcomes** use future bars, so they live only in `research/` and `ledger/`.
- **The holdout is locked:** data on or after `holdout.start` in `config/research.yaml` must not be used for tuning. Only an explicitly requested go/no-go run may read it.
- **Costs** come from `config/costs.yaml`, and results are reported after costs.
- **Multiple testing:** report how many hypotheses were tested, and apply the FDR control set in `research.yaml`.
- **The Claude API explains; it never outputs model probabilities or prices.**
- **The ledger is write-once:** forecasts are written at bar close, before the outcome exists. Ledger rows are never edited except to grade them.

## Secrets

- Keys live only in `.env`, which is gitignored; `.env.example` is the template. Load them through `candly.core.settings.get_settings()`, where secret fields are `SecretStr`.
- Never print, log or commit keys or broker tokens. Tokens are stored under `data/secrets/`.

## Code conventions

- Python 3.12 with type hints. pandas for data frames, pydantic models for API input and output. Keep modules small and prefer pure functions.
- Don't write comments that restate the code. Use one short line only when the reason behind something isn't obvious.
- Unit tests never hit the network; use fixtures or respx. Tests that need the network get `@pytest.mark.network` and are skipped by default.
- TypeScript runs in strict mode. API types live in `frontend/src/api/types.ts` and mirror docs/CONTRACTS.md.

## Claude API code

- Load the `claude-api` skill before writing any code that calls Claude.
- Models: `claude-haiku-4-5` for bulk news tagging, `claude-opus-5` for explanations and briefs.
- Use structured outputs, prompt caching, and the Batch API for backfills. Log every prompt and response along with its cost.

## Git

- Only the lead (the main session) commits. Agents never commit.
- Commit and push to `origin main` after each verified milestone (tests green).
- Write short, human-style commit messages with an imperative subject, e.g. "Add Fyers history client". Never add AI or Claude attribution trailers.

## Team workflow

- Agents in `.claude/agents/`: backend-engineer, quant-engineer, frontend-engineer, quant-auditor (read-only), reviewer (read-only), trader-tester.
- trader-tester is a 20+ year Indian-markets trader who is also a backend engineer. It writes only acceptance tests (`backend/tests/acceptance/`) and reports (`docs/test-reports/`).
- Each agent stays inside its own folders. Anything that crosses into another agent's area goes in the agent's report, and the lead (the main session) integrates it.
- Any change to indicators, patterns, features, labels, the scorecard, forecasts or backtests needs a quant-auditor pass before its results are trusted.
