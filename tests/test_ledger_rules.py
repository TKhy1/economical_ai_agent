"""Rules-engine unit tests: period windows, tier allocation, and the budget maths."""
import os
import sqlite3
import sys
import unittest
from datetime import date, datetime, timezone

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

    def test_expense_with_an_income_category_lands_in_a_spending_pool(self):
        # An expense must never be tiered as "income": it would count in
        # expense_total but vanish from all three fund pools.
        for category in sorted(agent.INCOME_CATEGORIES):
            with self.subTest(category=category):
                self.assertEqual(agent.category_tier(category, "expense"), "buffer")

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

    def test_boolean_amounts_are_rejected(self):
        # float(True) == 1.0 would book bools as one unit of currency.
        for amount in (True, False):
            with self.subTest(amount=amount):
                with self.assertRaises(ValueError) as ctx:
                    self.add(amount)
                self.assertEqual(str(ctx.exception), "amount must be positive")

    def test_every_expense_lands_in_a_fund_pool(self):
        for category in ("income", "allowance", "scholarship", "job", "parttime"):
            with self.subTest(category=category):
                self.add(10.0, "expense", category)

        conn = self.connect()
        try:
            status = ledger.compute_budget_status(conn, "weekly")
        finally:
            conn.close()

        pools = sum(
            status["tiers"][tier]["spent"] for tier in ("survival", "buffer", "learning")
        )
        self.assertEqual(pools, status["expense_total"])
        self.assertEqual(status["expense_total"], 50.0)
        self.assertEqual(status["income_total"], 0.0)

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


class TimestampClockTest(support.ApiTestCase):
    """Transaction timestamps and period windows must share one clock (local time)."""

    def connect(self):
        conn = sqlite3.connect(app_module.DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    def add(self, amount, when=None):
        conn = self.connect()
        try:
            return ledger.add_transaction(conn, amount, "expense", "food", "", when)
        finally:
            conn.close()

    def test_default_timestamp_uses_the_local_period_clock(self):
        # 00:30 local on a Monday is exactly where a UTC stamp lands in the previous
        # week. Freezing the local clock pins both the stamp and the week window.
        fixed = datetime(2026, 1, 5, 0, 30)
        original = ledger.now
        ledger.now = lambda: fixed
        try:
            result = self.add(10.0)
        finally:
            ledger.now = original

        self.assertEqual(result["transaction"]["occurred_at"], "2026-01-05T00:30:00")
        self.assertEqual(result["budget_status"]["period_start"], "2026-01-05")
        self.assertEqual(result["budget_status"]["expense_total"], 10.0)
        self.assertEqual(result["budget_status"]["tiers"]["survival"]["spent"], 10.0)

    def test_default_timestamp_is_naive_local_time(self):
        result = self.add(5.0)
        stored = datetime.fromisoformat(result["transaction"]["occurred_at"])
        self.assertIsNone(stored.tzinfo)
        self.assertLess(abs((stored - datetime.now()).total_seconds()), 120)

    def test_offset_timestamp_is_converted_to_local_time(self):
        aware = datetime(2026, 1, 5, 0, 30, tzinfo=timezone.utc)
        expected = aware.astimezone().replace(tzinfo=None).isoformat()
        result = self.add(5.0, when=aware.isoformat())
        self.assertEqual(result["transaction"]["occurred_at"], expected)

    def test_date_only_timestamp_is_normalised(self):
        result = self.add(5.0, when="2026-01-05")
        self.assertEqual(result["transaction"]["occurred_at"], "2026-01-05T00:00:00")


class ChatParserTest(unittest.TestCase):
    """The rule-based chat parser must not turn incidental numbers into expenses."""

    def parse(self, message):
        return agent.parse_chat_message(message)

    def test_numbers_without_a_transaction_cue_are_not_expenses(self):
        for message in (
            "3",
            "我住在3号楼",
            "I have 100 yuan",
            "我想存1000元",
            "我今年20岁",
        ):
            with self.subTest(message=message):
                self.assertEqual(self.parse(message)["intent"], "help")

    def test_amount_comes_from_the_spending_clause(self):
        cases = (
            ("我买了2杯咖啡花了30", 30.0, "food"),
            ("I spent 30 on 2 coffees", 30.0, "food"),
            ("2 coffees cost 30", 30.0, "food"),
            ("I bought 2 coffees for 30", 30.0, "food"),
            ("我花了30还剩20", 30.0, "other"),
        )
        for message, amount, category in cases:
            with self.subTest(message=message):
                parsed = self.parse(message)
                self.assertEqual(parsed["intent"], "add_transaction")
                self.assertEqual(parsed["amount"], amount)
                self.assertEqual(parsed["txn_type"], "expense")
                self.assertEqual(parsed["category"], category)

    def test_currency_marked_number_wins_over_a_quantity(self):
        parsed = self.parse("我花了2元买了3个苹果")
        self.assertEqual(parsed["intent"], "add_transaction")
        self.assertEqual(parsed["amount"], 2.0)

    def test_bare_quantity_is_not_a_price(self):
        parsed = self.parse("我买了2杯咖啡")
        self.assertEqual(parsed["intent"], "add_transaction")
        self.assertIsNone(parsed["amount"])

    def test_income_nouns_state_income_without_a_verb(self):
        cases = (
            ("我的生活费是800", 800.0, "allowance"),
            ("工资3000", 3000.0, "job"),
            ("my allowance is 800", 800.0, "allowance"),
            ("我领到800生活费", 800.0, "allowance"),
            ("I got paid 200", 200.0, "income"),
        )
        for message, amount, category in cases:
            with self.subTest(message=message):
                parsed = self.parse(message)
                self.assertEqual(parsed["intent"], "add_transaction")
                self.assertEqual(parsed["txn_type"], "income")
                self.assertEqual(parsed["amount"], amount)
                self.assertEqual(parsed["category"], category)

    def test_amount_before_the_keyword_and_verb_priority(self):
        cases = (
            ("30 spent", 30.0, "expense", "other"),
            ("800到账", 800.0, "income", "income"),
            ("800生活费", 800.0, "income", "allowance"),
            (
                "I earned 200 from a part-time job 30 hours",
                200.0,
                "income",
                "job",
            ),
        )
        for message, amount, txn_type, category in cases:
            with self.subTest(message=message):
                parsed = self.parse(message)
                self.assertEqual(parsed["intent"], "add_transaction")
                self.assertEqual(parsed["amount"], amount)
                self.assertEqual(parsed["txn_type"], txn_type)
                self.assertEqual(parsed["category"], category)

    def test_goal_words_outrank_remaining_budget_words(self):
        self.assertEqual(self.parse("目标还剩多少")["intent"], "goal_progress")
        self.assertEqual(self.parse("我的目标进度")["intent"], "goal_progress")
        self.assertEqual(self.parse("预算还剩多少")["intent"], "budget_status")
        self.assertEqual(self.parse("还剩多少")["intent"], "budget_status")

    def test_missing_amount_still_asks_for_one(self):
        parsed = self.parse("I bought a coffee")
        self.assertEqual(parsed["intent"], "add_transaction")
        self.assertIsNone(parsed["amount"])
        # An income noun without an amount is a statement, not a missing transaction.
        self.assertEqual(self.parse("我的生活费是多少")["intent"], "help")


if __name__ == "__main__":
    unittest.main()
