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


def budget_status(percentages=(0, 0, 0), net_balance=1000.0):
    tiers = {}
    for tier, pct in zip(("survival", "buffer", "learning"), percentages):
        tiers[tier] = {
            "limit": 100.0,
            "spent": float(pct),
            "remaining": 100.0 - float(pct),
            "pct": float(pct),
        }
    return {"tiers": tiers, "net_balance": net_balance}


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

    def test_deadline_today_is_reported_as_passed(self):
        insights = agent.build_insights(
            self.profile(), budget_status(), [goal(70, 20, date.today().isoformat())]
        )
        self.assertEqual(insights[0]["key"], "goal_deadline_passed")
        self.assertEqual(insights[0]["level"], "warning")
        self.assertEqual(insights[0]["params"]["remaining"], 50.0)

    def test_invalid_deadline_is_skipped_instead_of_crashing(self):
        insights = agent.build_insights(
            self.profile(), budget_status(), [goal(70, 0, "soon")]
        )
        self.assertEqual([i["key"] for i in insights], ["on_track"])


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


if __name__ == "__main__":
    unittest.main()
