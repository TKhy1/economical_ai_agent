"""Rules-engine unit tests: period windows, tier allocation, and the budget maths."""
import os
import sqlite3
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import support  # noqa: E402

import agent  # noqa: E402
import ledger  # noqa: E402

app_module = support.app_module


class CategoryTierTest(unittest.TestCase):
    def test_expense_categories_map_to_pools(self):
        self.assertEqual(agent.category_tier("food", "expense"), "survival")
        self.assertEqual(agent.category_tier("textbook", "expense"), "buffer")
        self.assertEqual(agent.category_tier("books", "expense"), "learning")
        self.assertEqual(agent.category_tier("mystery", "expense"), "buffer")

    def test_income_always_lands_in_the_income_tier(self):
        self.assertEqual(agent.category_tier("food", "income"), "income")
        self.assertEqual(agent.category_tier("job", "income"), "income")

    def test_category_is_normalised(self):
        self.assertEqual(agent.category_tier("  FOOD ", "expense"), "survival")


class BudgetPlanMathTest(unittest.TestCase):
    def test_moderate_split_of_monthly_income(self):
        plan = agent.generate_budget_plan(3000, "moderate", "monthly")
        self.assertEqual(plan["survival_limit"], 1500.0)
        self.assertEqual(plan["buffer_limit"], 750.0)
        self.assertEqual(plan["learning_limit"], 750.0)

    def test_weekly_plan_is_a_quarter_of_the_month(self):
        plan = agent.generate_budget_plan(3000, "moderate", "weekly")
        self.assertEqual(plan["survival_limit"], 375.0)
        self.assertEqual(round(plan["learning_limit"] + plan["buffer_limit"], 2), 375.0)

    def test_unknown_risk_level_falls_back_to_moderate(self):
        self.assertEqual(
            agent.generate_budget_plan(1000, "yolo", "monthly"),
            agent.generate_budget_plan(1000, "moderate", "monthly"),
        )

    def test_missing_income_is_treated_as_zero(self):
        plan = agent.generate_budget_plan(None, "moderate", "weekly")
        self.assertEqual(plan, {"survival_limit": 0.0, "buffer_limit": 0.0, "learning_limit": 0.0})


class PeriodBoundsTest(unittest.TestCase):
    def test_week_starts_on_monday(self):
        start, end = ledger.period_bounds("weekly", date(2026, 10, 14))  # a Wednesday
        self.assertEqual(start, date(2026, 10, 12))
        self.assertEqual(end, date(2026, 10, 19))

    def test_month_is_calendar_month(self):
        start, end = ledger.period_bounds("monthly", date(2026, 10, 17))
        self.assertEqual(start, date(2026, 10, 1))
        self.assertEqual(end, date(2026, 11, 1))

    def test_december_wraps_into_january(self):
        start, end = ledger.period_bounds("monthly", date(2026, 12, 20))
        self.assertEqual(start, date(2026, 12, 1))
        self.assertEqual(end, date(2027, 1, 1))

    def test_unknown_period_type_defaults_to_weekly(self):
        start, end = ledger.period_bounds("daily", date(2026, 10, 14))
        self.assertEqual((end - start).days, 7)


class RulesEngineDbTest(support.ApiTestCase):
    def connect(self):
        conn = sqlite3.connect(app_module.DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    def add(self, amount, txn_type="expense", category="food", note="", when=None):
        conn = self.connect()
        try:
            result = ledger.add_transaction(conn, amount, txn_type, category, note, when)
        finally:
            conn.close()
        return result

    def add_goal(self, name, target, current=0.0, deadline=None):
        conn = self.connect()
        conn.execute(
            "INSERT INTO Goal (user_id, name, target_amount, current_amount, deadline, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (ledger.DEFAULT_USER_ID, name, target, current, deadline, "2026-01-01T00:00:00"),
        )
        conn.commit()
        conn.close()

    def test_add_transaction_returns_row_and_status(self):
        result = self.add(25.0, category="groceries")
        self.assertEqual(result["transaction"]["tier"], "survival")
        self.assertEqual(result["transaction"]["amount"], 25.0)
        self.assertEqual(result["budget_status"]["expense_total"], 25.0)

    def test_transaction_rejects_bad_input(self):
        cases = [
            (0, "expense", "food", "amount must be positive"),
            (-1, "expense", "food", "amount must be positive"),
            (1, "transfer", "food", "type must be income or expense"),
            (1, "expense", "   ", "category is required"),
        ]
        for amount, txn_type, category, message in cases:
            with self.subTest(message=message):
                with self.assertRaises(ValueError) as ctx:
                    self.add(amount, txn_type, category)
                self.assertEqual(str(ctx.exception), message)

    def test_locked_amount_sums_unmet_goal_remainders(self):
        conn = self.connect()
        try:
            self.assertEqual(ledger.compute_locked_amount(conn), 0.0)
        finally:
            conn.close()

        self.add_goal("Trip", 500, 100)      # 400 locked
        self.add_goal("Laptop", 300, 300)    # fully funded, nothing locked

        conn = self.connect()
        try:
            self.assertEqual(ledger.compute_locked_amount(conn), 400.0)
        finally:
            conn.close()

    def test_budget_status_splits_spending_by_tier(self):
        self.add(100.0, category="groceries")       # survival
        self.add(50.0, category="books")            # learning
        self.add(30.0, category="entertainment")    # buffer
        self.add(500.0, "income", "job")            # income

        conn = self.connect()
        try:
            status = ledger.compute_budget_status(conn, "weekly")
        finally:
            conn.close()

        self.assertEqual(status["expense_total"], 180.0)
        self.assertEqual(status["income_total"], 500.0)
        self.assertEqual(status["net_balance"], 320.0)
        self.assertEqual(status["tiers"]["survival"]["spent"], 100.0)
        self.assertEqual(status["tiers"]["learning"]["spent"], 50.0)
        self.assertEqual(status["tiers"]["buffer"]["spent"], 30.0)

    def test_available_balance_subtracts_locked_goal_money(self):
        self.add(300.0, "income", "job")
        self.add_goal("Trip", 200)
        conn = self.connect()
        try:
            status = ledger.compute_budget_status(conn, "weekly")
        finally:
            conn.close()
        self.assertEqual(status["net_balance"], 300.0)
        self.assertEqual(status["locked_amount"], 200.0)
        self.assertEqual(status["available_balance"], 100.0)

    def test_pct_is_relative_to_the_plan_limit(self):
        conn = self.connect()
        try:
            ledger.update_budget_plan(conn, "weekly", 100.0, 100.0, 100.0)
        finally:
            conn.close()
        self.add(50.0, category="groceries")
        conn = self.connect()
        try:
            status = ledger.compute_budget_status(conn, "weekly")
        finally:
            conn.close()
        self.assertEqual(status["tiers"]["survival"]["pct"], 50.0)
        self.assertEqual(status["tiers"]["survival"]["remaining"], 50.0)


if __name__ == "__main__":
    unittest.main()
