# CampusWallet — AI Financial Planner for College Students

A mobile-first web demo that helps a student split a tight budget into three fund pools
(**survival / buffer / learning**), track spending against each pool, save toward goals,
and talk to an AI assistant that can only act through deterministic rules.

Flask + SQLite backend, vanilla HTML/CSS/JS frontend, optional **DeepSeek**-powered chat
(OpenAI-compatible function calling) that degrades gracefully to a built-in bilingual rule
engine when no API key is configured or the API call fails. Claude (Anthropic Messages API)
is kept as an optional second backend.

---

## Features

- **Three-tier fund pools** — every expense is classified into `survival`, `buffer`, or
  `learning`; limits are derived from monthly income and risk appetite
  (conservative / moderate / aggressive).
- **Locked vs. spendable money** — money promised to unmet savings goals is *locked*;
  the dashboard shows the balance you can actually spend.
- **Goals with deadline maths** — progress %, remaining amount, days left, and a weekly
  savings target that never divides by less than one week.
- **Insight feed** — tier overrun / warning, available balance below the safety
  threshold, goal reached, deadline missed — returned as `{level, key, params}` so the UI
  can render any language.
- **Chat assistant with a safety gate** — the LLM (or the rule parser) can only *propose*
  a transaction. Writing to the ledger requires an explicit confirmation of a
  `ChatAction` that expires after 10 minutes. Recent turns are sent as text-only history,
  so follow-up questions reach the model with context.
- **DeepSeek tool calling** — the model calls three tools (transaction proposal, budget
  status, goal progress) over the OpenAI-compatible protocol; the app executes them
  locally. The system prompt also carries a **read-only budget snapshot** (current tier
  limits, totals, locked money, spendable balance, goals), so routine questions are
  answered in one request instead of a tool round trip. Timeouts, rate limits and auth
  errors fall back to the rule engine automatically, and the chat header labels the active
  backend.
- **Bilingual UI** (EN/ZH) persisted per profile, no build step, no JS framework.

## Architecture

```
app.py              Flask app: routing, JSON validation, chat action state machine
ledger.py           Deterministic rules engine — the ONLY writer of financial data
agent.py            Risk allocation, tier classification, insights, NL parsing
llm_agent.py        LLM chat layer: DeepSeek (OpenAI-compatible tool calling) / Anthropic
                    (optional); never touches SQLite, only proposes
limits.py           Shared money/text caps that keep aggregates finite
config.py           Env/.env reader (AI_PROVIDER, DEEPSEEK_API_KEY, AI_MODEL, …)
seed_demo_data.py   Reset + reseed a believable demo dataset
check_llm.py        Backend self-check: one live call, prints provider/model/reply/latency
templates/index.html        Single-page shell (six tabs)
static/css/style.css        Mobile-first responsive layout
static/js/app.js            View logic, API calls, chat rendering
static/js/i18n.js           EN/ZH dictionary + interpolation
tests/                      unittest suites (API contract + rules engine)
requirements.txt            Direct dependencies, pinned
requirements.lock.txt       Full transitive lock
```

Every mutating path funnels through `ledger.add_transaction`, so the rule engine can never
be bypassed by a different caller — the app, the rule parser and the LLM tools all land in
the same place.

## Quick start

Requires **Python 3.12+**.

```powershell
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt   # pinned direct deps
venv\Scripts\python.exe app.py                               # http://127.0.0.1:5000
```

To reproduce the exact environment the tests were verified against, install the lock
instead of the direct dependencies:

```powershell
venv\Scripts\python.exe -m pip install -r requirements.lock.txt
venv\Scripts\python.exe -m pip check
```

Optional demo data (safe to re-run — it clears and reseeds the demo user):

```powershell
venv\Scripts\python.exe seed_demo_data.py
```

## Tests

```powershell
venv\Scripts\python.exe -m unittest discover -s tests
```

Six suites cover the API contract (`tests/test_input_validation.py`), the deadline and
insight maths (`tests/test_goal_deadline.py`), the rules engine and chat parser
(`tests/test_ledger_rules.py`), the LLM config/history layer plus the DeepSeek tool-call
protocol (`tests/test_llm_agent.py`), the demo seeder (`tests/test_seed_demo.py`) and
frontend i18n interpolation (`tests/test_frontend_i18n.py`, which uses Node and is skipped
when Node is unavailable).
Each test runs against a throwaway SQLite file, so your real database is never touched.
Set `ECONOMICAL_AGENT_TEST_TMP` if the system temp directory is not writable in your
environment.

## Hook setup after cloning

The repo ships `.githooks/pre-commit`, which blocks `.env`, databases and private keys,
plus secret-shaped content such as `sk-…`, `ghp_…` or `AKIA…`. The hook file travels with
the repo, but `core.hooksPath` is local config and is not versioned — **enable it once
after cloning**:

```powershell
git config core.hooksPath .githooks
```

Verify it (no output and exit code 0 means the check ran and passed):

```powershell
git hook run pre-commit
```

Skipping this step raises no error — the hook just stays silently inactive. Use
`git commit --no-verify` to bypass it for a false positive.

## Configuration

Copy `.env.example` to `.env` and paste a DeepSeek key
([create one here](https://platform.deepseek.com/api_keys)):

```powershell
Copy-Item .env.example .env
# edit .env: DEEPSEEK_API_KEY=sk-your-real-key
venv\Scripts\python.exe check_llm.py   # live self-check: key, endpoint and model
venv\Scripts\python.exe app.py
```

| Variable | Default | Purpose |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | _(empty)_ | Enables the DeepSeek chat backend. Without it, `/api/chat` uses the rule engine. |
| `AI_PROVIDER` | inferred from the keys | `deepseek` or `anthropic`; `openai` is an alias for the OpenAI-compatible protocol. |
| `AI_MODEL` | per backend | DeepSeek: `deepseek-flash`; Claude: `claude-sonnet-5`. |
| `AI_BASE_URL` | per backend | DeepSeek: `https://api.deepseek.com`; Claude: the SDK default (forwarded only when set). |
| `AI_TIMEOUT` | per backend | DeepSeek: `30` seconds; Claude: the SDK default (not passed). |
| `AI_MAX_TOKENS` | per backend | DeepSeek: `600`; Claude: `400` (its original cap). |
| `AI_TEMPERATURE` | `0.7` | Sampling temperature (ignored — and not sent — in thinking mode). |
| `AI_THINKING` | `auto` | `auto`: off on DeepSeek, field omitted on third-party gateways; `enabled` echoes `reasoning_content` back with tools. |
| `AI_REASONING_EFFORT` | _(empty)_ | `low` / `high` / `max`, thinking mode only. |
| `AI_CONTEXT` | `true` | Inject the read-only budget snapshot into the system prompt (`false` disables it for Claude too). |
| `AI_MAX_RETRIES` | `1` | Retries on 429/5xx/network errors. |
| `ANTHROPIC_API_KEY` | _(empty)_ | Claude key (the original integration — see below). |

**Model names (V4.1 Flash)** — DeepSeek's own API serves the model under the stable id
`deepseek-flash`, while other catalogs (for example Alibaba Cloud) call it
`deepseek-v4.1-flash`. On DeepSeek's host, `AI_MODEL` is canonicalised:

| Configured `AI_MODEL` | Sent to `api.deepseek.com` |
| --- | --- |
| `deepseek-flash` / `deepseek-v4.1-flash` / `deepseek-v41-flash` / `DeepSeek-V4.1-Flash` | `deepseek-flash` (V4.1-Flash-1017) |
| `deepseek-v4-flash` / `deepseek-v4.1-flash-1017` | `deepseek-flash` (legacy / dated spellings) |
| `deepseek-v4-pro` | `deepseek-v4-pro` |
| anything else | sent verbatim |

Any other endpoint receives the string untouched, because a gateway defines its own model
ids. For example, running the same model through Alibaba Cloud Bailian:

```dotenv
AI_PROVIDER=openai
AI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
AI_MODEL=deepseek-v4.1-flash
AI_API_KEY=<your DashScope key>
```

`GET /api/chat/status` reports `model` (the id actually sent), `model_requested` (your
spelling) and `thinking` (the effective behaviour: `enabled` / `disabled` / `omitted`), so
the mapping is always visible.

### Claude compatibility (unchanged)

Adding DeepSeek did not touch the Claude path; an existing Claude deployment keeps working
as-is:

- **Same key names**: only `ANTHROPIC_API_KEY`, or only the generic `AI_API_KEY`, still
  selects Claude, and `AI_API_KEY` still takes precedence over `ANTHROPIC_API_KEY`.
  Inference order: `AI_PROVIDER` → `DEEPSEEK_API_KEY` → `ANTHROPIC_API_KEY` → a lone
  `AI_API_KEY` (= Claude) → DeepSeek when nothing is set. Placeholders never count as a
  configured key, so copying `.env.example` cannot flip a Claude setup to DeepSeek.
- **Same defaults**: model `claude-sonnet-5`, `AI_MAX_TOKENS` 400, no timeout passed to
  the SDK unless `AI_TIMEOUT` is set, and `AI_BASE_URL` only forwarded when it differs
  from the SDK default.
- **Same model ids**: alias mapping only applies to `api.deepseek.com`; `claude-*` names
  are passed through untouched.
- **Same protocol**: Messages API + tool use, identical tool results / `is_error`
  handling, and `add_transaction` still only proposes.
- One addition: a read-only budget snapshot is appended to the system prompt
  (`AI_CONTEXT=true`). Set `AI_CONTEXT=false` to get the byte-for-byte original prompt.

Real environment variables win over `.env`. `AI_API_KEY` is a generic name shared by both
backends: DeepSeek prefers `DEEPSEEK_API_KEY`, while Claude keeps its historical order
(`AI_API_KEY` before `ANTHROPIC_API_KEY`). The placeholder in
`.env.example` (and other obvious dummies such as `your-...-key` or `xxxx`) counts as
unconfigured, so no doomed API call is attempted. Any failure — missing `anthropic`
package, network error, 401/402/429, empty reply — is logged as a warning and falls back to
the rule-based reply, so the demo always works. The chat header badge shows the active
backend, and `GET /api/chat/status` exposes the same information without the key.

## Input limits

Names are capped at 120 characters, categories at 60, notes at 1,000 and chat messages
at 4,000. Any monetary value must be at most ¥1,000,000,000,000. Larger values are
rejected with 400; the cap also keeps income/expense/locked aggregates finite, so API
responses never contain the invalid JSON token `Infinity`.

## Budget model

1. `agent.generate_budget_plan(monthly_income, risk_level, period_type)` splits income into
   three limits. Weekly periods use a quarter of the monthly figure.
2. Each expense is classified by `agent.category_tier(category, type)`. Income always
   lands in the `income` tier; every expense lands in one of the three pools (income-style
   categories on an expense fall back to `buffer`, so the pools always add up to
   `expense_total`).
3. `ledger.compute_locked_amount` sums the *unmet* part of every savings goal.
4. `net_balance = income − expense`; `available_balance = net_balance − locked_amount`.
5. The safety threshold is compared against `available_balance` by
   `agent.exceeds_safety_threshold`, which both the insight feed and the chat
   confirmation gate call — the dashboard and the warning card cannot disagree.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/` | Single-page app |
| GET/PUT | `/api/profile` | Read / update name, risk level, threshold, income, language |
| GET | `/api/transactions?limit=` | Recent transactions (limit clamped to 1…1000) |
| POST | `/api/transactions` | Add a transaction, returns the stored row |
| DELETE | `/api/transactions/<id>` | Delete a transaction |
| POST | `/api/ledger/add` | Add a transaction, returns row **and** fresh budget status |
| GET/PUT | `/api/budget?period=` | Current period limits / override them |
| GET | `/api/budget/status?period=` | Spending per tier, totals, locked and available balance |
| GET | `/api/goals` | List savings goals |
| POST | `/api/goals` | Create a goal |
| PUT/DELETE | `/api/goals/<id>` | Update / delete a goal |
| GET | `/api/goal/progress?id=` | Goals enriched with `pct`, `remaining`, `days_left` |
| GET | `/api/insights?period=` | Insight list for the dashboard |
| POST | `/api/chat` | Natural-language turn (DeepSeek/Claude when configured, else rules); returns `source: llm \| rules` |
| GET | `/api/chat/status` | Active chat backend (provider / model / configured, never the key) |
| POST | `/api/chat/action` | `{"id": ..., "decision": "confirm" \| "cancel"}` |

All JSON errors use `{"error": "..."}` with a 4xx status — never an HTML error page.
Malformed or non-object bodies, wrong field types, non-finite or negative numbers, and
malformed deadlines are `400`; out-of-range ids are `404`. Deleting a row that does not
exist is a `404`, and a non-integer `id` on `/api/goal/progress` is a `400`.

## Data model

`UserProfile`, `Transaction`, `BudgetPlan`, `Goal` and `ChatAction` — see `app.init_db()`.
The database file is created on first import and is **not** committed (see `.gitignore`);
regenerate demo rows with `seed_demo_data.py`.

Timestamps are stored as **naive local-time** ISO strings (`ledger.now()`), the same clock
the local Monday-based week/month windows use, so an entry always counts in the period it
happened in locally. A caller may send `occurred_at` with a UTC offset; `ledger` converts
it to local time before storing it. `/api/goal/progress` reports `days_left = 0` for a goal
due today, and such a goal is still actionable rather than "past its deadline".

## Troubleshooting (Windows)

### `/api/chat` returns 500 with `attempt to write a readonly database`

The database file is not writable by the running process: SQLite can read it but cannot
create its journal, so every write (a transaction, a chat confirmation, a budget edit)
fails — the chat looks broken even though the model is fine. It usually means the `.db`
file was created before the workspace sandbox granted write access; the sandbox only
labels files created afterwards.

Fix: create a fresh copy inside the same folder (new files inherit the correct
permissions, data is preserved) and swap it in:

```powershell
venv\Scripts\python.exe -c "import sqlite3; s=sqlite3.connect('economical_agent.db'); d=sqlite3.connect('economical_agent.db.fix'); s.backup(d); d.close(); s.close()"
Move-Item economical_agent.db economical_agent.db.old -Force
Move-Item economical_agent.db.fix economical_agent.db -Force
venv\Scripts\python.exe app.py
```

If the second `Move-Item` is denied, the terminal itself is sandboxed: run these two lines
from Explorer or a plain `Win+R` terminal. `app.py` now logs this guidance at startup when
it detects a read-only database, instead of failing halfway through a chat request.

### Changed `.env` but the backend did not change

`.env` is read once per process, so restart `app.py`; real environment variables win over
`.env`. `venv\Scripts\python.exe check_llm.py` prints the provider, model and a live call
result at any time.
