"""Deterministic financial rules engine —the only code path allowed to mutate ledger data.

Neither the LLM chat assistant nor the rule-based chat parser touches the database
directly. Both describe *what* the user wants in plain terms and call `add_transaction`
here, exactly like a bank's core ledger sits behind a teller: whoever's asking, the same
deterministic rules run every time.
"""
import math
from datetime import datetime, timedelta

import agent
import limits

DEFAULT_USER_ID = 1


def now():
    """Current *local* wall-clock time as a naive datetime.

    Rows store naive ISO strings without an offset so that they sort and compare
    lexicographically, which only works while every reader and writer agrees on the
    clock. The period windows (`period_bounds`) are local calendar windows and the
    frontend renders these strings as local time, so transactions are stamped in
    local time too. Stamping UTC here used to drop a local Monday 00:00-07:59 entry
    into the previous week and show it as the previous day.
    """
    return datetime.now()


def utcnow():
    """Deprecated alias of `now()`; it returns local time, not UTC.

    Kept only for callers that imported the old name, so every timestamp in the
    database still comes from the same clock.
    """
    return now()


def today():
    """Local calendar date - the reference date for every period window."""
    return now().date()


def parse_number(value):
    """Coerce `value` to a finite float, rejecting booleans and non-numbers.

    JSON booleans are not amounts: Python's ``float(True) == 1.0`` would silently
    book ``{"amount": true}`` as CNY 1, so bools are rejected explicitly. NaN and
    Infinity are rejected too (they can arrive as strings such as ``"NaN"``), and
    so is a JSON integer too large for a C double (``float(10**400)`` raises
    OverflowError, which used to escape as a 500). Values above
    ``limits.MAX_AMOUNT`` are rejected too, so summing ledger rows can never produce
    ``inf`` (and therefore never emits invalid JSON ``Infinity``).
    """
    if isinstance(value, bool):
        raise ValueError("not a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("not a finite number") from None
    if not math.isfinite(number):
        raise ValueError("not a finite number")
    if abs(number) > limits.MAX_AMOUNT:
        raise ValueError("number is too large")
    return number


def storable_text(value):
    """Return `value` if it is a str SQLite can store in a TEXT column, else None.

    Python's JSON decoder accepts a lone surrogate escape (`"\\ud800"` — not valid JSON, but decoded
    anyway), and binding that to SQLite tries to encode it as UTF-8 and raises UnicodeEncodeError.
    Callers check here before touching the database so that is a 400, not a 500.
    """
    if not isinstance(value, str):
        return None
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return None
    return value


def _row_to_dict(row):
    return dict(row)


def period_bounds(period_type, ref_date=None):
    """Return the half-open [start, end) window that `period_type` covers today."""
    ref_date = ref_date or today()
    if period_type == "monthly":
        start = ref_date.replace(day=1)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
    else:
        start = ref_date - timedelta(days=ref_date.weekday())
        end = start + timedelta(days=7)
    return start, end


def get_profile(db):
    row = db.execute(
        "SELECT * FROM UserProfile WHERE id = ?", (DEFAULT_USER_ID,)
    ).fetchone()
    return _row_to_dict(row) if row else None


def list_goals(db):
    rows = db.execute(
        "SELECT * FROM Goal WHERE user_id = ? ORDER BY created_at DESC",
        (DEFAULT_USER_ID,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_or_create_budget_plan(db, period_type):
    """Return this period's fund-pool limits, seeding them from the profile when absent."""
    start, _ = period_bounds(period_type)
    row = db.execute(
        "SELECT * FROM BudgetPlan WHERE user_id = ? AND period_type = ? AND period_start = ?",
        (DEFAULT_USER_ID, period_type, start.isoformat()),
    ).fetchone()
    if row:
        return _row_to_dict(row)

    profile = get_profile(db) or {}
    plan = agent.generate_budget_plan(
        profile.get("monthly_income"), profile.get("risk_level"), period_type
    )
    db.execute(
        "INSERT INTO BudgetPlan (user_id, period_type, period_start, survival_limit, "
        "buffer_limit, learning_limit, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            DEFAULT_USER_ID,
            period_type,
            start.isoformat(),
            plan["survival_limit"],
            plan["buffer_limit"],
            plan["learning_limit"],
            now().isoformat(),
        ),
    )
    db.commit()
    row = db.execute(
        "SELECT * FROM BudgetPlan WHERE user_id = ? AND period_type = ? AND period_start = ?",
        (DEFAULT_USER_ID, period_type, start.isoformat()),
    ).fetchone()
    return _row_to_dict(row)


def update_budget_plan(db, period_type, survival_limit, buffer_limit, learning_limit):
    plan = get_or_create_budget_plan(db, period_type)
    db.execute(
        "UPDATE BudgetPlan SET survival_limit = ?, buffer_limit = ?, learning_limit = ? "
        "WHERE id = ?",
        (survival_limit, buffer_limit, learning_limit, plan["id"]),
    )
    db.commit()
    row = db.execute(
        "SELECT * FROM BudgetPlan WHERE id = ?", (plan["id"],)
    ).fetchone()
    return _row_to_dict(row)


def compute_locked_amount(db):
    """Step 2 of the rules engine: money already committed to unmet savings goals is 'locked' —    it's still in the account, but it isn't really free to spend."""
    locked = 0.0
    for g in list_goals(db):
        remaining = g["target_amount"] - g["current_amount"]
        if remaining > 0:
            locked += remaining
    return round(locked, 2)


def compute_budget_status(db, period_type):
    """Spending per fund-pool tier for the current period, plus spendable balance."""
    plan = get_or_create_budget_plan(db, period_type)
    start, end = period_bounds(period_type)
    rows = db.execute(
        'SELECT tier, type, SUM(amount) as total FROM "Transaction" '
        "WHERE user_id = ? AND occurred_at >= ? AND occurred_at < ? "
        "GROUP BY tier, type",
        (DEFAULT_USER_ID, start.isoformat(), end.isoformat()),
    ).fetchall()

    spent = {tier: 0.0 for tier in ("survival", "buffer", "learning")}
    income_total = 0.0
    expense_total = 0.0
    for r in rows:
        total = r["total"] or 0.0
        if r["type"] == "expense":
            expense_total += total
            # Every expense has to land in one of the three pools, otherwise it
            # would count in expense_total but be invisible per tier. Rows written
            # before category_tier was direction-strict can still say "income";
            # they fall back to the catch-all buffer pool.
            spent[r["tier"] if r["tier"] in spent else "buffer"] += total
        else:
            income_total += total

    tiers = {}
    for tier in ("survival", "buffer", "learning"):
        limit = plan[tier + "_limit"]
        used = spent[tier]
        pct = round(used / limit * 100.0, 1) if limit > 0 else 0.0
        tiers[tier] = {
            "limit": round(limit, 2),
            "spent": round(used, 2),
            "remaining": round(limit - used, 2),
            "pct": pct,
        }

    net_balance = round(income_total - expense_total, 2)
    locked_amount = compute_locked_amount(db)
    return {
        "period_type": period_type,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "tiers": tiers,
        "income_total": round(income_total, 2),
        "expense_total": round(expense_total, 2),
        "net_balance": net_balance,
        "locked_amount": locked_amount,
        "available_balance": round(net_balance - locked_amount, 2),
    }


def _local_occurred_at(value):
    """Normalise a caller-supplied timestamp to a naive local ISO string.

    A string without an offset is local wall-clock time (that is what the frontend
    and the seed script send); one with an offset is converted to local time first.
    Either way the stored value stays comparable to the local period bounds and to
    every other row.
    """
    if value is None or value == "":
        return now().isoformat()
    if not isinstance(value, str):
        raise ValueError("occurred_at must be a valid ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip())
    except (TypeError, ValueError):
        raise ValueError("occurred_at must be a valid ISO timestamp") from None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed.isoformat()


def add_transaction(
    db, amount, txn_type, category, note="", occurred_at=None, period_type="weekly"
):
    """Record a transaction and return it with the refreshed budget status."""
    if txn_type not in ("income", "expense"):
        raise ValueError("type must be income or expense")
    if period_type not in ("weekly", "monthly"):
        raise ValueError("period must be weekly or monthly")

    try:
        amount = parse_number(amount)
    except ValueError:
        raise ValueError("amount must be positive") from None
    if amount <= 0:
        raise ValueError("amount must be positive")

    if category is None:
        raise ValueError("category is required")
    category = storable_text(category)
    if category is None:
        raise ValueError("category must be a string")
    category = category.strip().lower()
    if not category:
        raise ValueError("category is required")
    if len(category) > limits.MAX_CATEGORY_LENGTH:
        raise ValueError("category is too long")

    if note is None:
        note = ""
    note = storable_text(note)
    if note is None:
        raise ValueError("note must be a string")
    if len(note) > limits.MAX_NOTE_LENGTH:
        raise ValueError("note is too long")

    occurred_at = _local_occurred_at(occurred_at)

    tier = agent.category_tier(category, txn_type)
    cur = db.execute(
        'INSERT INTO "Transaction" (user_id, amount, type, category, tier, note, '
        "occurred_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (DEFAULT_USER_ID, amount, txn_type, category, tier, note, occurred_at),
    )
    db.commit()
    txn = _row_to_dict(
        db.execute(
            'SELECT * FROM "Transaction" WHERE id = ?', (cur.lastrowid,)
        ).fetchone()
    )
    return {"transaction": txn, "budget_status": compute_budget_status(db, period_type)}
