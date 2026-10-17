# CampusWallet — AI Financial Planner for College Students

A mobile-first web demo that helps a student split a tight budget into three fund pools
(**survival / buffer / learning**), track spending against each pool, save toward goals,
and talk to an AI assistant that can only act through deterministic rules.

Flask + SQLite backend, vanilla HTML/CSS/JS frontend, optional Claude-powered chat that
degrades gracefully to a built-in bilingual rule engine when no API key is configured.

---

## Features

- **Three-tier fund pools** — every expense is classified into `survival`, `buffer`, or
  `learning`; limits are derived from monthly income and risk appetite
  (conservative / moderate / aggressive).
- **Locked vs. spendable money** — money promised to unmet savings goals is *locked*;
  the dashboard shows the balance you can actually spend.
- **Goals with deadline maths** — progress %, remaining amount, days left, and a weekly
  savings target that never divides by less than one week.
- **Insight feed** — tier overrun / warning, balance below the security threshold, goal
  reached, deadline missed — returned as `{level, key, params}` so the UI can render any
  language.
- **Chat assistant with a safety gate** — the LLM (or the rule parser) can only *propose*
  a transaction. Writing to the ledger requires an explicit confirmation of a
  `ChatAction` that expires after 10 minutes.
- **Bilingual UI** (EN/ZH) persisted per profile, no build step, no JS framework.

## Architecture

```
app.py              Flask app: routing, JSON validation, chat action state machine
ledger.py           Deterministic rules engine — the ONLY writer of financial data
agent.py            Risk allocation, tier classification, insights, NL parsing
llm_agent.py        Optional Claude client (tool use); never touches SQLite itself
config.py           Env/.env reader (AI_API_KEY, AI_MODEL)
seed_demo_data.py   Reset + reseed a believable demo dataset
templates/index.html        Single-page shell (three tabs)
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

Three suites cover the API contract (`tests/test_input_validation.py`), the deadline and
insight maths (`tests/test_goal_deadline.py`) and the rules engine
(`tests/test_ledger_rules.py`). Each test runs against a throwaway SQLite file, so your
real database is never touched. Set `ECONOMICAL_AGENT_TEST_TMP` if the system temp
directory is not writable in your environment.

## Configuration

Copy `.env.example` to `.env`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `AI_API_KEY` | _(empty)_ | Enables the Claude chat backend. Without it, `/api/chat` uses the rule engine. |
| `AI_MODEL` | `claude-sonnet-5` | Model name passed to the Messages API. |

Real environment variables win over `.env`. `ANTHROPIC_API_KEY` is accepted as a fallback
name for `AI_API_KEY`. If the `anthropic` package is missing or the API call fails, the
request silently falls back to the rule-based reply — the demo always works.

## Budget model

1. `agent.generate_budget_plan(monthly_income, risk_level, period_type)` splits income into
   three limits. Weekly periods use a quarter of the monthly figure.
2. Each expense is classified by `agent.category_tier(category, type)`.
3. `ledger.compute_locked_amount` sums the *unmet* part of every savings goal.
4. `net_balance = income − expense`; `available_balance = net_balance − locked_amount`.

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
| POST | `/api/chat` | Natural-language turn (LLM when configured, else rules) |
| POST | `/api/chat/action` | `{"id": ..., "decision": "confirm" \| "cancel"}` |

All JSON errors use `{"error": "..."}` with a 4xx status — never an HTML error page and
never a 500 for out-of-range ids or non-finite amounts.

## Data model

`UserProfile`, `Transaction`, `BudgetPlan`, `Goal` and `ChatAction` — see `app.init_db()`.
The database file is created on first import and is **not** committed (see `.gitignore`);
regenerate demo rows with `seed_demo_data.py`.
