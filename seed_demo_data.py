"""Populate (or reset) the demo database with a believable student budget.

    venv\\Scripts\\python.exe seed_demo_data.py

Running it more than once is safe: the demo user's transactions, goals, budget
plans and chat actions are cleared first, then re-seeded through the same
`ledger` rules engine the app uses (so tiers and totals stay consistent).
"""
import os
import sqlite3
from datetime import timedelta

import agent
import app as app_module
import ledger

DEMO_PROFILE = {
    "name": "Demo Student",
    "risk_level": "moderate",
    "security_threshold": 500.0,
    "monthly_income": 3000.0,
    "language": "en",
}

# (category, type, amount, note, day offset from this week's Monday)
DEMO_TRANSACTIONS = [
    ("rent", "expense", 450.0, "Dorm rent", 0),
    ("job", "income", 1800.0, "Part-time salary", 1),
    ("books", "expense", 88.0, "Algorithm textbook", 1),
    ("groceries", "expense", 96.5, "Weekly supermarket run", 2),
    ("transport", "expense", 30.0, "Bus card top-up", 3),
    ("scholarship", "income", 600.0, "Semester scholarship", 4),
    ("entertainment", "expense", 45.0, "Cinema with friends", 5),
    ("food", "expense", 22.0, "Canteen lunch", 6),
    ("phone", "expense", 25.0, "Phone bill", -3),
    ("courses", "expense", 60.0, "Online Python course", -5),
]

# (name, target, current, deadline in days or None)
DEMO_GOALS = [
    ("Laptop upgrade", 1200.0, 400.0, 120),
    ("Emergency fund", 600.0, 200.0, None),
    ("Spring trip", 300.0, 300.0, 90),
]


def _clear_demo_rows(conn):
    for table in ("Transaction", "Goal", "BudgetPlan", "ChatAction"):
        conn.execute(f'DELETE FROM "{table}"' if table == "Transaction" else f"DELETE FROM {table}")
    conn.execute("DELETE FROM UserProfile")
    conn.commit()


def _seed_profile(conn):
    now = ledger.utcnow().isoformat()
    conn.execute(
        "INSERT INTO UserProfile (id, name, risk_level, security_threshold, monthly_income, "
        "language, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            ledger.DEFAULT_USER_ID,
            DEMO_PROFILE["name"],
            DEMO_PROFILE["risk_level"],
            DEMO_PROFILE["security_threshold"],
            DEMO_PROFILE["monthly_income"],
            DEMO_PROFILE["language"],
            now,
        ),
    )
    conn.commit()


def _seed_transactions(conn):
    week_start = ledger.period_bounds("weekly")[0]
    for category, txn_type, amount, note, day_offset in DEMO_TRANSACTIONS:
        occurred_at = (week_start + timedelta(days=day_offset)).isoformat()
        ledger.add_transaction(
            conn, amount, txn_type, category, note, occurred_at, "weekly"
        )


def _seed_goals(conn):
    now = ledger.utcnow()
    for name, target, current, deadline_days in DEMO_GOALS:
        deadline = (
            (now + timedelta(days=deadline_days)).date().isoformat()
            if deadline_days is not None
            else None
        )
        conn.execute(
            "INSERT INTO Goal (user_id, name, target_amount, current_amount, deadline, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (ledger.DEFAULT_USER_ID, name, target, current, deadline, now.isoformat()),
        )
    conn.commit()


def main():
    app_module.init_db()
    conn = sqlite3.connect(app_module.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        _clear_demo_rows(conn)
        _seed_profile(conn)
        _seed_transactions(conn)
        _seed_goals(conn)

        status = ledger.compute_budget_status(conn, "weekly")
        insights = agent.build_insights(
            ledger.get_profile(conn), status, ledger.list_goals(conn)
        )
    finally:
        conn.close()

    print(f"Seeded {app_module.DB_PATH}")
    print(f"  transactions : {len(DEMO_TRANSACTIONS)}")
    print(f"  goals        : {len(DEMO_GOALS)}")
    print(f"  net balance  : {status['net_balance']:.2f}")
    print(f"  locked       : {status['locked_amount']:.2f}")
    print(f"  available    : {status['available_balance']:.2f}")
    for tier, data in status["tiers"].items():
        print(
            f"  {tier:<9} limit {data['limit']:.2f} spent {data['spent']:.2f} "
            f"({data['pct']:.0f}%)"
        )
    print("  insights     : " + ", ".join(i["key"] for i in insights))


if __name__ == "__main__":
    main()
