"""Input-validation contract for every mutating endpoint.

These tests pin the JSON error shape the frontend relies on: a 4xx status plus
``{"error": "..."}``, never a Flask HTML error page, never a 500.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import support


class ProfileValidationTest(support.ApiTestCase):
    def test_missing_body_is_rejected(self):
        resp = self.client.put("/api/profile")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.json_error(resp), "a JSON object is required")

    def test_non_object_body_is_rejected(self):
        for payload in ([1, 2, 3], "just text", 42):
            with self.subTest(payload=payload):
                resp = self.client.put("/api/profile", json=payload)
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(self.json_error(resp), "a JSON object is required")

    def test_non_string_names_are_rejected(self):
        for name in (42, 3.5, True, [1], {"a": 1}):
            with self.subTest(name=name):
                resp = self.client.put("/api/profile", json={"name": name})
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(self.json_error(resp), "name must be a string")

    def test_unencodable_names_are_rejected(self):
        resp = self.client.put("/api/profile", json={"name": "\ud800"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.json_error(resp), "name must be valid UTF-8 text")

    def test_blank_name_is_rejected(self):
        resp = self.client.put("/api/profile", json={"name": "   "})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.json_error(resp), "name must not be empty")

    def test_valid_name_is_trimmed_and_stored(self):
        resp = self.client.put("/api/profile", json={"name": "  Ada Lovelace  "})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["name"], "Ada Lovelace")
        self.assertEqual(self.client.get("/api/profile").get_json()["name"], "Ada Lovelace")

    def test_partial_update_keeps_the_stored_name(self):
        self.client.put("/api/profile", json={"name": "Ada"})
        resp = self.client.put("/api/profile", json={"risk_level": "aggressive"})
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["name"], "Ada")
        self.assertEqual(body["risk_level"], "aggressive")

    def test_invalid_risk_level_is_rejected(self):
        resp = self.client.put("/api/profile", json={"risk_level": "yolo"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            self.json_error(resp),
            "risk_level must be conservative, moderate, or aggressive",
        )

    def test_invalid_language_is_rejected(self):
        resp = self.client.put("/api/profile", json={"language": "fr"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.json_error(resp), "language must be en or zh")

    def test_non_numeric_numbers_are_rejected(self):
        resp = self.client.put("/api/profile", json={"monthly_income": "lots"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            self.json_error(resp),
            "security_threshold and monthly_income must be numbers",
        )

    def test_valid_numeric_strings_are_coerced(self):
        resp = self.client.put(
            "/api/profile", json={"monthly_income": "3000", "security_threshold": "150.5"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["monthly_income"], 3000.0)
        self.assertEqual(resp.get_json()["security_threshold"], 150.5)

    def json_error(self, resp):
        body = resp.get_json()
        self.assertIsInstance(body, dict)
        return body.get("error")


class TransactionValidationTest(support.ApiTestCase):
    def test_missing_fields_are_rejected(self):
        cases = [
            {},
            {"amount": 10},
            {"amount": 10, "type": "expense"},
            {"amount": 10, "category": "food"},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                resp = self.client.post("/api/transactions", json=payload)
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(
                    resp.get_json()["error"],
                    "amount, type, and category are required",
                )

    def test_non_object_body_is_rejected(self):
        resp = self.client.post("/api/transactions", json=[1, 2, 3])
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "a JSON object is required")

    def test_amount_must_be_positive(self):
        for amount in (0, -5, "abc", None):
            with self.subTest(amount=amount):
                resp = self.client.post(
                    "/api/transactions",
                    json={"amount": amount, "type": "expense", "category": "food"},
                )
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(resp.get_json()["error"], "amount must be positive")

    def test_non_finite_amounts_are_rejected(self):
        for token in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(token=token):
                resp = self.client.post(
                    "/api/transactions",
                    data='{"amount": %s, "type": "expense", "category": "food"}' % token,
                    content_type="application/json",
                )
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(resp.get_json()["error"], "amount must be positive")

    def test_invalid_type_is_rejected(self):
        resp = self.client.post(
            "/api/transactions", json={"amount": 1, "type": "transfer", "category": "food"}
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "type must be income or expense")

    def test_blank_category_is_rejected(self):
        resp = self.client.post(
            "/api/transactions", json={"amount": 1, "type": "expense", "category": "   "}
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "category is required")

    def test_invalid_occurred_at_is_rejected(self):
        resp = self.client.post(
            "/api/transactions",
            json={
                "amount": 1,
                "type": "expense",
                "category": "food",
                "occurred_at": "last tuesday",
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.get_json()["error"], "occurred_at must be a valid ISO timestamp"
        )

    def test_valid_transaction_returns_the_created_row(self):
        resp = self.client.post(
            "/api/transactions",
            json={"amount": 12.5, "type": "expense", "category": "Food", "note": "lunch"},
        )
        self.assertEqual(resp.status_code, 201)
        body = resp.get_json()
        self.assertIsInstance(body["id"], int)
        self.assertEqual(body["amount"], 12.5)
        self.assertEqual(body["category"], "food")
        self.assertEqual(body["tier"], "survival")
        self.assertEqual(body["note"], "lunch")

    def test_tier_assignment_covers_all_pools(self):
        cases = [
            ("food", "expense", "survival"),
            ("books", "expense", "learning"),
            ("entertainment", "expense", "buffer"),
            ("job", "income", "income"),
        ]
        for category, txn_type, tier in cases:
            with self.subTest(category=category):
                resp = self.client.post(
                    "/api/transactions",
                    json={"amount": 5, "type": txn_type, "category": category},
                )
                self.assertEqual(resp.status_code, 201)
                self.assertEqual(resp.get_json()["tier"], tier)

    def test_ledger_add_returns_budget_status(self):
        resp = self.client.post(
            "/api/ledger/add",
            json={"amount": 40, "type": "expense", "category": "groceries"},
        )
        self.assertEqual(resp.status_code, 201)
        body = resp.get_json()
        self.assertIn("transaction", body)
        self.assertIn("budget_status", body)
        status = body["budget_status"]
        for key in (
            "tiers",
            "income_total",
            "expense_total",
            "net_balance",
            "locked_amount",
            "available_balance",
        ):
            self.assertIn(key, status)
        self.assertEqual(status["expense_total"], 40.0)
        self.assertEqual(status["net_balance"], -40.0)

    def test_transactions_can_be_listed_and_deleted(self):
        created = self.client.post(
            "/api/transactions",
            json={"amount": 9, "type": "expense", "category": "food"},
        ).get_json()
        listed = self.client.get("/api/transactions?limit=5").get_json()
        self.assertEqual([t["id"] for t in listed], [created["id"]])
        resp = self.client.delete(f"/api/transactions/{created['id']}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {"deleted": created["id"]})
        self.assertEqual(self.client.get("/api/transactions").get_json(), [])

    def test_list_limit_is_validated_and_clamped(self):
        resp = self.client.get("/api/transactions?limit=abc")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "limit must be an integer")

        self.client.post(
            "/api/transactions",
            json={"amount": 1, "type": "expense", "category": "food"},
        )
        clamped = self.client.get("/api/transactions?limit=999999999999999999999")
        self.assertEqual(clamped.status_code, 200)
        self.assertEqual(len(clamped.get_json()), 1)

    def test_out_of_range_ids_do_not_500(self):
        resp = self.client.delete("/api/transactions/9223372036854775808")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.get_json()["error"], "transaction not found")


class GoalValidationTest(support.ApiTestCase):
    def test_create_requires_name_and_target(self):
        for payload in ({}, {"name": "Trip"}, {"target_amount": 100}, {"name": "  ", "target_amount": 1}):
            with self.subTest(payload=payload):
                resp = self.client.post("/api/goals", json=payload)
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(
                    resp.get_json()["error"], "name and target_amount are required"
                )

    def test_non_numeric_target_is_rejected(self):
        resp = self.client.post("/api/goals", json={"name": "Trip", "target_amount": "lots"})
        self.assertEqual(resp.status_code, 400)

    def test_non_numeric_current_amount_is_rejected(self):
        resp = self.client.post(
            "/api/goals",
            json={"name": "Trip", "target_amount": 100, "current_amount": "x"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "current_amount must be a number")

    def test_create_then_update_goal(self):
        created = self.client.post(
            "/api/goals", json={"name": "Trip", "target_amount": 500, "current_amount": 50}
        )
        self.assertEqual(created.status_code, 201)
        goal_id = created.get_json()["id"]

        updated = self.client.put(
            f"/api/goals/{goal_id}", json={"current_amount": 200, "name": "Trip 2026"}
        )
        self.assertEqual(updated.status_code, 200)
        body = updated.get_json()
        self.assertEqual(body["current_amount"], 200.0)
        self.assertEqual(body["name"], "Trip 2026")

    def test_update_missing_goal_is_404(self):
        resp = self.client.put("/api/goals/12345", json={"name": "Nope"})
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.get_json()["error"], "goal not found")

    def test_out_of_range_goal_ids_are_404(self):
        for resp in (
            self.client.put("/api/goals/9223372036854775808", json={"name": "x"}),
            self.client.delete("/api/goals/9223372036854775808"),
            self.client.get("/api/goal/progress?id=9223372036854775808"),
        ):
            self.assertEqual(resp.status_code, 404)
            self.assertEqual(resp.get_json()["error"], "goal not found")

    def test_goal_progress_unknown_id_is_404(self):
        resp = self.client.get("/api/goal/progress?id=999")
        self.assertEqual(resp.status_code, 404)


class BudgetValidationTest(support.ApiTestCase):
    def test_invalid_periods_are_rejected(self):
        for path in ("/api/budget", "/api/budget/status", "/api/insights"):
            with self.subTest(path=path):
                resp = self.client.get(f"{path}?period=daily")
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(
                    resp.get_json()["error"], "period must be weekly or monthly"
                )

    def test_budget_update_validates_period(self):
        resp = self.client.put("/api/budget", json={"period_type": "daily"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.get_json()["error"], "period_type must be weekly or monthly"
        )

    def test_budget_update_validates_limits(self):
        resp = self.client.put("/api/budget", json={"survival_limit": "lots"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "limits must be numbers")

    def test_budget_update_persists_limits(self):
        resp = self.client.put(
            "/api/budget",
            json={
                "period_type": "weekly",
                "survival_limit": 100,
                "buffer_limit": 40,
                "learning_limit": 60,
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["survival_limit"], 100.0)
        self.assertEqual(body["buffer_limit"], 40.0)
        self.assertEqual(body["learning_limit"], 60.0)

        fetched = self.client.get("/api/budget?period=weekly").get_json()
        self.assertEqual(fetched["survival_limit"], 100.0)


class ChatValidationTest(support.ApiTestCase):
    def test_message_is_required(self):
        for payload in ({}, {"message": ""}, {"message": "   "}):
            with self.subTest(payload=payload):
                resp = self.client.post("/api/chat", json=payload)
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(resp.get_json()["error"], "message is required")

    def test_non_object_body_is_rejected(self):
        resp = self.client.post("/api/chat", json="hello")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "a JSON object is required")

    def test_decision_must_be_confirm_or_cancel(self):
        resp = self.client.post("/api/chat/action", json={"id": "x", "decision": "maybe"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "decision must be confirm or cancel")

    def test_action_id_is_required(self):
        resp = self.client.post("/api/chat/action", json={"decision": "confirm"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "action id is required")

    def test_unknown_action_is_404(self):
        resp = self.client.post(
            "/api/chat/action", json={"id": "not-a-real-id", "decision": "confirm"}
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.get_json()["error"], "action not found")

    def test_expense_needs_confirmation_before_it_is_written(self):
        reply = self.client.post("/api/chat", json={"message": "spent 30 on food"}).get_json()
        self.assertIn("action", reply)
        self.assertIsNotNone(reply["action"])
        self.assertEqual(reply["key"], "chat_action_preview")
        self.assertEqual(self.count_transactions(), 0)

        confirmed = self.client.post(
            "/api/chat/action", json={"id": reply["action"]["id"], "decision": "confirm"}
        )
        self.assertEqual(confirmed.status_code, 201)
        self.assertEqual(confirmed.get_json()["status"], "confirmed")
        self.assertEqual(self.count_transactions(), 1)

    def test_cancel_does_not_write(self):
        reply = self.client.post("/api/chat", json={"message": "spent 30 on food"}).get_json()
        cancelled = self.client.post(
            "/api/chat/action", json={"id": reply["action"]["id"], "decision": "cancel"}
        )
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.get_json()["status"], "cancelled")
        self.assertEqual(self.count_transactions(), 0)

        again = self.client.post(
            "/api/chat/action", json={"id": reply["action"]["id"], "decision": "confirm"}
        )
        self.assertEqual(again.status_code, 409)
        self.assertEqual(again.get_json()["error"], "action already cancelled")

    def test_double_confirm_is_rejected(self):
        reply = self.client.post("/api/chat", json={"message": "spent 30 on food"}).get_json()
        action_id = reply["action"]["id"]
        self.client.post("/api/chat/action", json={"id": action_id, "decision": "confirm"})
        again = self.client.post(
            "/api/chat/action", json={"id": action_id, "decision": "confirm"}
        )
        self.assertEqual(again.status_code, 409)
        self.assertEqual(again.get_json()["error"], "action already consumed")

    def test_expired_action_is_rejected(self):
        action_id = self.seed_expired_action()
        resp = self.client.post(
            "/api/chat/action", json={"id": action_id, "decision": "confirm"}
        )
        self.assertEqual(resp.status_code, 410)
        self.assertEqual(resp.get_json()["error"], "action expired")
        self.assertEqual(self.count_transactions(), 0)

    def test_budget_question_answers_without_an_action(self):
        reply = self.client.post("/api/chat", json={"message": "what is my budget"}).get_json()
        self.assertIsNone(reply["action"])
        self.assertEqual(reply["key"], "chat_budget_status")
        for key in (
            "net_balance",
            "available_balance",
            "survival_remaining",
            "buffer_remaining",
            "learning_remaining",
        ):
            self.assertIn(key, reply["params"])

    def test_message_without_amount_asks_for_one(self):
        reply = self.client.post("/api/chat", json={"message": "I bought a coffee"}).get_json()
        self.assertEqual(reply["key"], "chat_missing_amount")


if __name__ == "__main__":
    unittest.main()
