"""Deadline and insight maths — the part of the agent that is easiest to get subtly wrong.

Covers both the pure rules in ``agent.py`` and the JSON the API exposes, because the
frontend renders ``days_left``/``pct``/``weekly_savings_needed`` verbatim.
"""
import os
import sys
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import support  # noqa: E402

import agent  # noqa: E402


def iso_in(days):
    return (date.today() + timedelta(days=days)).isoformat()


def budget_status(percentages=(0, 0, 0), net_balance=1000.0, available_balance=None):
    tiers = {}
    for tier, pct in zip(("survival", "buffer", "learning"), percentages):
        tiers[tier] = {
            "limit": 100.0,
            "spent": float(pct),
            "remaining": 100.0 - float(pct),
            "pct": float(pct),
        }
    if available_balance is None:
        available_balance = net_balance
    return {
        "tiers": tiers,
        "net_balance": net_balance,
        "available_balance": available_balance,
    }


def goal(target, current=0, deadline=None, name="Trip"):
    return {
        "id": 1,
        "user_id": 1,
        "name": name,
        "target_amount": float(target),
        "current_amount": float(current),
        "deadline": deadline,
        "created_at": "2026-01-01T00:00:00",
    }


class ComputeGoalProgressTest(unittest.TestCase):
    def test_progress_percentage_and_remaining(self):
        progress = agent.compute_goal_progress(goal(500, 125, iso_in(30)))
        self.assertEqual(progress["remaining"], 375.0)
        self.assertEqual(progress["pct"], 25.0)
        self.assertEqual(progress["days_left"], 30)

    def test_overfunded_goals_report_full_and_no_negative_remainder(self):
        progress = agent.compute_goal_progress(goal(100, 250))
        self.assertEqual(progress["pct"], 100.0)
        self.assertEqual(progress["remaining"], 0.0)

    def test_zero_target_does_not_divide_by_zero(self):
        progress = agent.compute_goal_progress(goal(0, 10))
        self.assertEqual(progress["pct"], 0.0)
        self.assertEqual(progress["remaining"], 0.0)

    def test_missing_deadline_reports_no_days_left(self):
        self.assertIsNone(agent.compute_goal_progress(goal(100, 10))["days_left"])

    def test_invalid_deadline_is_treated_as_missing(self):
        progress = agent.compute_goal_progress(goal(100, 10, "not-a-date"))
        self.assertIsNone(progress["days_left"])

    def test_past_deadline_reports_negative_days(self):
        progress = agent.compute_goal_progress(goal(100, 10, iso_in(-3)))
        self.assertEqual(progress["days_left"], -3)


class BuildInsightsTest(unittest.TestCase):
    def profile(self, threshold=50.0):
        return {"id": 1, "security_threshold": threshold, "risk_level": "moderate"}

    def test_on_track_is_the_fallback(self):
        insights = agent.build_insights(self.profile(), budget_status(net_balance=500), [])
        self.assertEqual([i["key"] for i in insights], ["on_track"])
        self.assertEqual(insights[0]["level"], "success")

    def test_tier_warning_and_exceeded_thresholds(self):
        insights = agent.build_insights(
            self.profile(), budget_status((85, 100, 10), net_balance=500), []
        )
        by_key = {(i["key"], i["params"]["tier"]): i for i in insights}
        self.assertEqual(by_key[("tier_warning", "survival")]["level"], "warning")
        self.assertEqual(by_key[("tier_warning", "survival")]["params"]["pct"], 85)
        self.assertEqual(by_key[("tier_exceeded", "buffer")]["level"], "danger")
        self.assertNotIn(("tier_warning", "learning"), by_key)

    def test_balance_below_threshold(self):
        insights = agent.build_insights(
            self.profile(500), budget_status(net_balance=120.0), []
        )
        self.assertEqual(insights[0]["key"], "balance_below_threshold")
        self.assertEqual(insights[0]["level"], "danger")
        self.assertEqual(insights[0]["params"]["balance"], 120.0)
        self.assertEqual(insights[0]["params"]["threshold"], 500.0)

    def test_balance_threshold_compares_available_not_net_money(self):
        # Healthy net balance, but goal money is locked: only 120 can be spent.
        insights = agent.build_insights(
            self.profile(500),
            budget_status(net_balance=1000.0, available_balance=120.0),
            [],
        )
        self.assertEqual([i["key"] for i in insights], ["balance_below_threshold"])
        self.assertEqual(insights[0]["params"]["balance"], 120.0)

    def test_healthy_available_balance_is_not_below_threshold(self):
        insights = agent.build_insights(
            self.profile(500),
            budget_status(net_balance=400.0, available_balance=900.0),
            [],
        )
        self.assertEqual([i["key"] for i in insights], ["on_track"])

    def test_reached_goal_is_success_and_has_no_savings_estimate(self):
        insights = agent.build_insights(
            self.profile(), budget_status(), [goal(100, 100, iso_in(10))]
        )
        self.assertEqual([i["key"] for i in insights], ["goal_reached"])
        self.assertEqual(insights[0]["params"]["name"], "Trip")

    def test_weekly_savings_needed_divides_by_weeks_left(self):
        # 14 days left is two weeks, so 200 remaining needs 100 per week.
        insights = agent.build_insights(
            self.profile(), budget_status(), [goal(200, 0, iso_in(14))]
        )
        self.assertEqual(insights[0]["key"], "weekly_savings_needed")
        self.assertEqual(insights[0]["params"]["amount"], 100.0)
        self.assertEqual(insights[0]["params"]["deadline"], iso_in(14))

    def test_savings_estimate_never_divides_by_less_than_one_week(self):
        # 2 days left still counts as one week, so 70 remaining needs 70 per week.
        insights = agent.build_insights(
            self.profile(), budget_status(), [goal(70, 0, iso_in(2))]
        )
        self.assertEqual(insights[0]["params"]["amount"], 70.0)

    def test_deadline_today_is_still_actionable(self):
        # 0 days left means "due today", not "missed": the remaining 50 has to be
        # saved this week, so the goal still gets a savings target.
        insights = agent.build_insights(
            self.profile(), budget_status(), [goal(70, 20, date.today().isoformat())]
        )
        self.assertEqual(insights[0]["key"], "weekly_savings_needed")
        self.assertEqual(insights[0]["params"]["amount"], 50.0)
        self.assertEqual(insights[0]["params"]["deadline"], date.today().isoformat())

    def test_invalid_deadline_is_skipped_instead_of_crashing(self):
        insights = agent.build_insights(
            self.profile(), budget_status(), [goal(70, 0, "soon")]
        )
        self.assertEqual([i["key"] for i in insights], ["on_track"])


class SafetyThresholdRuleTest(unittest.TestCase):
    """`agent.exceeds_safety_threshold` is the one rule both features call."""

    def test_projected_spend_is_compared_to_the_threshold(self):
        profile = {"security_threshold": 500.0}
        status = budget_status(net_balance=520.0, available_balance=520.0)
        # Healthy right now, but the proposed 30 would drop below the threshold.
        self.assertFalse(agent.exceeds_safety_threshold(profile, status))
        self.assertTrue(agent.exceeds_safety_threshold(profile, status, 30.0))

    def test_overdrawn_available_balance_always_warns(self):
        profile = {"security_threshold": 0.0}
        status = budget_status(net_balance=-100.0, available_balance=-100.0)
        self.assertTrue(agent.exceeds_safety_threshold(profile, status))
        self.assertTrue(agent.exceeds_safety_threshold(profile, status, 10.0))


class GoalDeadlineApiTest(support.ApiTestCase):
    def test_goal_progress_endpoint_exposes_days_left(self):
        created = self.client.post(
            "/api/goals",
            json={"name": "Laptop", "target_amount": 1000, "deadline": iso_in(21)},
        ).get_json()
        progress = self.client.get("/api/goal/progress").get_json()[0]
        self.assertEqual(progress["days_left"], 21)
        self.assertEqual(progress["pct"], 0.0)
        self.assertEqual(progress["remaining"], 1000.0)
        self.assertEqual(progress["id"], created["id"])

    def test_insights_endpoint_reports_weekly_target(self):
        self.client.put("/api/profile", json={"security_threshold": 0})
        self.client.post(
            "/api/goals",
            json={"name": "Laptop", "target_amount": 280, "deadline": iso_in(28)},
        )
        insights = self.client.get("/api/insights?period=weekly").get_json()
        weekly = [i for i in insights if i["key"] == "weekly_savings_needed"]
        self.assertEqual(len(weekly), 1)
        self.assertEqual(weekly[0]["params"]["amount"], 70.0)

    def test_past_deadline_surfaces_a_warning(self):
        self.client.put("/api/profile", json={"security_threshold": 0})
        self.client.post(
            "/api/goals",
            json={"name": "Late", "target_amount": 100, "deadline": iso_in(-5)},
        )
        insights = self.client.get("/api/insights").get_json()
        passed = [i for i in insights if i["key"] == "goal_deadline_passed"]
        self.assertEqual(len(passed), 1)
        self.assertEqual(passed[0]["params"]["name"], "Late")

    def test_goal_due_today_is_not_reported_as_passed(self):
        self.client.put("/api/profile", json={"security_threshold": 0})
        self.client.post(
            "/api/goals",
            json={
                "name": "Due today",
                "target_amount": 100,
                "deadline": date.today().isoformat(),
            },
        )
        insights = self.client.get("/api/insights").get_json()
        keys = [i["key"] for i in insights]
        self.assertNotIn("goal_deadline_passed", keys)
        weekly = [i for i in insights if i["key"] == "weekly_savings_needed"]
        self.assertEqual(len(weekly), 1)
        self.assertEqual(weekly[0]["params"]["amount"], 100.0)


class SafetyThresholdApiTest(support.ApiTestCase):
    """The insights feed and the chat gate must apply one and the same safety rule."""

    def test_locked_goal_money_warns_in_both_places(self):
        self.client.put("/api/profile", json={"security_threshold": 500})
        self.client.post(
            "/api/transactions",
            json={"amount": 1200, "type": "income", "category": "job"},
        )
        self.client.post("/api/goals", json={"name": "Laptop", "target_amount": 1000})

        status = self.client.get("/api/budget/status?period=weekly").get_json()
        self.assertEqual(status["net_balance"], 1200.0)
        self.assertEqual(status["available_balance"], 200.0)

        insights = self.client.get("/api/insights?period=weekly").get_json()
        below = [i for i in insights if i["key"] == "balance_below_threshold"]
        self.assertEqual(len(below), 1)
        self.assertEqual(below[0]["params"]["balance"], 200.0)

        reply = self.client.post(
            "/api/chat", json={"message": "spent 10 on food"}
        ).get_json()
        self.assertEqual(reply["action"]["type"], "risk_warning")

    def test_spendable_money_above_the_threshold_does_not_warn(self):
        self.client.put("/api/profile", json={"security_threshold": 100})
        self.client.post(
            "/api/transactions",
            json={"amount": 500, "type": "income", "category": "job"},
        )
        insights = self.client.get("/api/insights?period=weekly").get_json()
        self.assertNotIn("balance_below_threshold", [i["key"] for i in insights])

        reply = self.client.post(
            "/api/chat", json={"message": "spent 10 on food"}
        ).get_json()
        self.assertEqual(reply["action"]["type"], "confirm_credit")


class GoalDeadlineValidationApiTest(support.ApiTestCase):
    """The deadline column is read back with date.fromisoformat(), so the wire format is enforced."""

    INVALID_DEADLINES = (
        {},
        [],
        123,
        True,
        "not-a-date",
        "2030-1-5",
        "20300131",
        "2030-W05-1",
        "2030-02-31",
    )

    def test_create_rejects_invalid_deadlines(self):
        for value in self.INVALID_DEADLINES:
            with self.subTest(deadline=value):
                resp = self.client.post(
                    "/api/goals",
                    json={"name": "Trip", "target_amount": 100, "deadline": value},
                )
                self.assertEqual(resp.status_code, 400)
                self.assertIn("deadline", resp.get_json()["error"])
        self.assertEqual(self.client.get("/api/goals").get_json(), [])

    def test_valid_deadline_is_canonicalised_and_optional(self):
        created = self.client.post(
            "/api/goals",
            json={"name": "Trip", "target_amount": 100, "deadline": "  2030-01-31 "},
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.get_json()["deadline"], "2030-01-31")

        for payload in (
            {"name": "Missing", "target_amount": 100},
            {"name": "Null", "target_amount": 100, "deadline": None},
            {"name": "Blank", "target_amount": 100, "deadline": "   "},
        ):
            with self.subTest(payload=payload):
                resp = self.client.post("/api/goals", json=payload)
                self.assertEqual(resp.status_code, 201)
                self.assertIsNone(resp.get_json()["deadline"])

    def test_past_deadline_is_allowed(self):
        resp = self.client.post(
            "/api/goals",
            json={"name": "Late", "target_amount": 100, "deadline": iso_in(-1)},
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.get_json()["deadline"], iso_in(-1))

    def test_update_rejects_invalid_deadline_and_keeps_the_stored_value(self):
        created = self.client.post(
            "/api/goals",
            json={"name": "Trip", "target_amount": 100, "deadline": "2030-01-31"},
        ).get_json()

        for value in self.INVALID_DEADLINES:
            with self.subTest(deadline=value):
                resp = self.client.put(
                    f"/api/goals/{created['id']}", json={"deadline": value}
                )
                self.assertEqual(resp.status_code, 400)

        stored = self.client.get(
            f"/api/goal/progress?id={created['id']}"
        ).get_json()
        self.assertEqual(stored["deadline"], "2030-01-31")

    def test_update_can_clear_the_deadline(self):
        created = self.client.post(
            "/api/goals",
            json={"name": "Trip", "target_amount": 100, "deadline": "2030-01-31"},
        ).get_json()

        resp = self.client.put(
            f"/api/goals/{created['id']}", json={"deadline": None}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.get_json()["deadline"])


class ChatGoalOverdueApiTest(support.ApiTestCase):
    """A past deadline must never be rendered as ``days_left: -46``."""

    def create_goal(self, deadline=None):
        payload = {"name": "Late", "target_amount": 100}
        if deadline is not None:
            payload["deadline"] = deadline
        return self.client.post("/api/goals", json=payload).get_json()

    def chat_progress(self):
        reply = self.client.post(
            "/api/chat", json={"message": "goal progress"}
        ).get_json()
        self.assertEqual(reply["key"], "chat_goal_progress")
        return reply["params"]

    def test_past_deadline_is_reported_as_overdue(self):
        self.create_goal(iso_in(-5))
        params = self.chat_progress()
        self.assertEqual(params["days_left"], 0)
        self.assertEqual(params["days_overdue"], 5)

    def test_due_today_has_no_overdue_days(self):
        self.create_goal(date.today().isoformat())
        params = self.chat_progress()
        self.assertEqual(params["days_left"], 0)
        self.assertEqual(params["days_overdue"], 0)

    def test_goal_without_deadline_reports_no_day_counts(self):
        self.create_goal()
        params = self.chat_progress()
        self.assertIsNone(params["days_left"])
        self.assertEqual(params["days_overdue"], 0)


if __name__ == "__main__":
    unittest.main()
